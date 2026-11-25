"""Offline install transaction for tools/rc_install.py against an in-memory fake camera.
No ADB, camera, card, reboot or service. Covers read-only preflight, explicit authority
retirement, mixed and malformed policy, a crash before and after every camera write with
resume and rollback, and failure injection before the final select."""
import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from tools import rc_install as I  # noqa: E402
from tools import rc_wifi_policy as W  # noqa: E402

OLD = '440191e11935ce0f116f0e18'
NEW = '17ae76337dcd67403642288f'
OTHER = 'aaaaaaaaaaaaaaaaaaaaaaaa'
STOCK = b'factory init fixture\n'
BOOT = '11111111-2222-4333-8444-555555555555'
ATT = 'b' * 32
S01, S99, ALLOW, SEL = '/etc/init.d/S01app', '/etc/init.d/S99cammy-tf', '/etc/cammy/native-select.allow', I.SELECTOR
RESIDENT, RETRY, LEDGER, ADVANCE = (I.CFG + '/native-select.' + n for n in ('resident', 'retry', 'retry-used', 'advance'))
CREDS = {I.CFG + '/home.conf': b'ssid=home\npsk=secret\n', I.CFG + '/portal.token': b'token\n',
         I.CFG + '/ap.conf': b'ap\n'}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def s99(release):
    return ('#!/bin/sh\nexec /mnt/extsd/CammyOS-releases/%s/runtime/startup.sh\n' % release).encode()


def resident(release):
    return ('format=cammy-resident/1\nrelease_id=%s\n' % release).encode()


def retry_optin(release):
    return ('format=cammy-power-retry/1\nrelease_id=%s\ncandidate_md5=%s\n' % (release, 'c' * 32)).encode()


LEDGER_BYTES = ('format=cammy-power-retry-used/1\nattempt=%s\nfailed_boot=%s\nretry_boot=%s\n'
                % (ATT, BOOT, BOOT)).encode()
OLD_FILES = {S01: b'#!/bin/sh\n# 0.7.6 selector\n', S99: s99(OLD), ALLOW: b'format=allow\nold\n', SEL: I.SELECT}


class Crash(Exception):
    """Power or link loss at a camera write."""


class FakeCamera:
    def __init__(self, files, dirs, links=()):
        self.files, self.dirs, self.links = dict(files), set(dirs), set(links)
        self.boot = BOOT
        self.mutations = []
        self.fail_at, self.fail_after = None, False
        self.corrupt_write = None
        self.free_bytes = 512 * 1024

    # reads
    def boot_id(self):
        return self.boot

    def kinds(self, paths):
        return {p: 'link' if p in self.links else 'file' if p in self.files else 'dir' if p in self.dirs
                else 'absent' for p in paths}

    def read(self, path):
        if path not in self.files:
            raise RuntimeError('adb pull failed: %s' % path)
        return self.files[path]

    def listdir(self, path):
        names = {p[len(path) + 1:] for p in (*self.files, *self.dirs, *self.links) if p.startswith(path + '/')}
        return sorted(n for n in names if '/' not in n)

    def hashes(self, base, names, busybox):
        assert busybox == base + '/runtime/busybox'
        return {n: sha(self.files[base + '/' + n]) for n in names if base + '/' + n in self.files}

    def overlay_free_bytes(self, candidate):
        return self.free_bytes

    def info(self):
        return {'mem_available_kb': 340000, 'sdvcam_pid': '1097'}

    def archive_md5(self, path):
        return hashlib.md5(self.read(path)).hexdigest()

    def archive_remove(self, path, md5, boot):
        assert self.boot == boot
        if md5 is None:
            assert path in self.dirs and not self.listdir(path)
        else:
            assert self.archive_md5(path) == md5
        crash = self._step('prune', path)
        if crash and not self.fail_after:
            raise Crash(path)
        if md5 is None:
            self.dirs.remove(path)
        else:
            del self.files[path]
        if crash:
            raise Crash(path)

    # writes
    def _step(self, op, *paths):
        for p in paths:
            assert Path(p).name not in W.PRIVATE_NAMES, p
        self.mutations.append((op, *paths))
        return self.fail_at == len(self.mutations) - 1

    def _parent(self, path):
        assert path.rsplit('/', 1)[0] in self.dirs, path

    def write(self, path, data, mode):
        self._parent(path)
        crash = self._step('write', path)
        if crash and not self.fail_after:
            self.files[path] = data[:len(data) // 2]  # torn copy
            raise Crash(path)
        self.files[path] = self.corrupt_write(path, data) if self.corrupt_write else data
        if crash:
            raise Crash(path)

    def rename(self, src, dst):
        assert dst not in self.dirs and src in self.files
        crash = self._step('rename', src, dst)
        if crash and not self.fail_after:
            raise Crash(src)
        self._parent(dst)
        self.files[dst] = self.files.pop(src)
        if crash:
            raise Crash(src)

    def mkdir(self, path):
        assert path not in self.dirs and path not in self.files
        self._parent(path)
        crash = self._step('mkdir', path)
        if crash and not self.fail_after:
            raise Crash(path)
        self.dirs.add(path)
        if crash:
            raise Crash(path)

    def remove(self, path):
        assert path.endswith('.rc-new') or path in (S99, ALLOW), path
        crash = self._step('remove', path)
        if crash and not self.fail_after:
            raise Crash(path)
        self.files.pop(path, None)
        if crash:
            raise Crash(path)


def build_candidate(base):
    slot = W.release_slot(NEW)
    tf = {m: ('payload %s\n' % m).encode() for m in W.TF_MEMBERS}
    for m, data in tf.items():
        (base / 'tf' / slot / m).parent.mkdir(parents=True, exist_ok=True)
        (base / 'tf' / slot / m).write_bytes(data)
    new = {S01: b'#!/bin/sh\n# 0.7.9 selector\n', S99: s99(NEW), ALLOW: b'format=allow\nnew\n', SEL: I.DISABLED}
    for target, (source, _) in W.INTERNAL_FILES.items():
        (base / source).parent.mkdir(parents=True, exist_ok=True)
        (base / source).write_bytes(new[target])
    (base / 'templates').mkdir()
    (base / 'templates/native-select.enabled.select').write_bytes(I.SELECT)
    plan = {'release_id': NEW, 'tf_destination': '/mnt/extsd/' + slot,
            'internal_files': [dict(source=src, target=t, mode=mode,
                                    **({'before_sha256': sha(OLD_FILES[t]), 'after_sha256': sha(new[t])}
                                       if t in (S01, S99) else {}))
                               for t, (src, mode) in W.INTERNAL_FILES.items()]}
    (base / 'installation-plan.json').write_text(json.dumps(plan))
    files = {'tf/%s/%s' % (slot, m): sha(d) for m, d in tf.items()}
    (base / 'candidate.json').write_text(json.dumps({'release_id': NEW, 'version': '0.7.9-test', 'files': files}))
    return tf, new


class InstallTransactionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.enterContext(patch.object(I, 'STOCK_S01APP_SHA256', sha(STOCK)))
        self.tf, self.new = build_candidate(self.base / 'candidate')
        self.replacing = {'release': OLD, 'after_sha256': {t: sha(d) for t, d in OLD_FILES.items()}}
        self.slot = '/mnt/extsd/' + W.release_slot(NEW)

    def camera(self, **extra):
        files = {**OLD_FILES, **CREDS, RESIDENT: resident(OLD), RETRY: retry_optin(OLD), LEDGER: LEDGER_BYTES,
                 I.STATE + '/claimed-' + ATT: ('format=cammy-native-claim/1\nattempt=%s\n' % ATT).encode(),
                 I.CFG + '/rollback-20261117/S01app': STOCK,
                 I.RUNTIME_CONF: ('version=0.7.6\nrelease_id=%s\n' % OLD).encode(),
                 **{self.slot + '/' + m: d for m, d in self.tf.items()}}
        files.update({k: v for k, v in extra.items() if v is not None})
        for k in [k for k, v in extra.items() if v is None]:
            files.pop(k, None)
        dirs = {'/etc', '/etc/init.d', I.CFG, I.STATE, I.CFG + '/rollback-20261117', '/tmp/cammy-runtime',
                '/mnt/extsd', '/mnt/extsd/CammyOS-releases', self.slot, self.slot + '/native', self.slot + '/runtime'}
        cam = FakeCamera(files, dirs)
        self.initial = dict(cam.files)
        return cam

    def tx(self, cam, retire=OLD, **kw):
        c = I.Candidate(self.base / 'candidate', work=self.base / 'work')
        return I.Transaction(c, cam, kw.pop('replacing', self.replacing), retire_authority=retire,
                             out=lambda msg: None, **kw)

    def run_install(self, cam, **kw):
        return self.tx(cam, **kw).install()

    def assert_aborts(self, cam, pattern, **kw):
        with self.assertRaises(SystemExit) as ctx:
            self.run_install(cam, **kw)
        self.assertRegex(str(ctx.exception), pattern)
        self.assertEqual(cam.mutations, [], 'preflight wrote to the camera')

    # ---- invariants ------------------------------------------------------
    def archive_files(self, cam):
        return {p: d for p, d in cam.files.items() if '/native-select.archived-' in p}

    def assert_safe(self, cam):
        f = cam.files
        evidence = [p for p in self.initial if p.startswith(I.CFG + '/native-select.')
                    and p not in (ALLOW, SEL)] + [p for p in self.initial if p.startswith(I.STATE + '/')]
        if f.get(SEL) == I.SELECT:
            untouched = all(f.get(p) == self.initial[p] for p in (*OLD_FILES, *evidence))
            complete = (all(f[t] == self.new[t] for t in (S01, S99, ALLOW)) and not any(
                p in f for p in (RESIDENT, RETRY, LEDGER, ADVANCE, I.PENDING)) and not cam.listdir(I.STATE))
            self.assertTrue(untouched or complete, 'select with a mixed install')
        # The S99 runtime's health check rejects a resident record naming another release.
        running = re.search(rb'CammyOS-releases/([0-9a-f]{24})', f[S99]).group(1).decode()
        if RESIDENT in f:
            self.assertEqual(f[RESIDENT], resident(running), 'stale resident record under a new runtime')
        archived = set(self.archive_files(cam).values())
        for p in evidence:
            self.assertTrue(f.get(p) == self.initial[p] or self.initial[p] in archived, 'lost %s' % p)
        for p, d in CREDS.items():
            self.assertEqual(f[p], d)
        self.assertEqual(f[I.CFG + '/rollback-20261117/S01app'], STOCK)

    def assert_installed(self, cam):
        f = cam.files
        for t in (S01, S99, ALLOW):
            self.assertEqual(f[t], self.new[t])
        self.assertEqual(f[SEL], I.SELECT)
        for p in (RESIDENT, RETRY, LEDGER, I.PENDING):
            self.assertNotIn(p, f)
        self.assertEqual(cam.listdir(I.STATE), [])
        self.assertFalse([p for p in f if p.endswith('.rc-new') and '/rollback-' not in p])
        archived = self.archive_files(cam)
        for p in (RESIDENT, RETRY, LEDGER, I.STATE + '/claimed-' + ATT):
            self.assertIn(self.initial[p], archived.values())
        journal = json.loads((self.base / 'work/transaction.json').read_text())
        rollback = journal['rollback_dir']
        for t, d in OLD_FILES.items():
            self.assertEqual(f[rollback + '/' + Path(t).name], d)
        self.assertEqual(journal['status'], 'selected')
        result = json.loads((self.base / 'work/installed.json').read_text())
        self.assertEqual((result['release'], result['previous_release']), (NEW, OLD))
        self.assertIn('none', result['authority_after'])
        self.assert_safe(cam)

    def assert_rolled_back(self, cam):
        for t in (S01, S99, ALLOW):
            self.assertEqual(cam.files[t], OLD_FILES[t])
        self.assertEqual(cam.files[SEL], I.DISABLED)
        self.assertEqual(json.loads((self.base / 'work/transaction.json').read_text())['status'], 'rolled-back')
        self.assert_safe(cam)

    # ---- tests -------------------------------------------------------------
    def test_check_is_read_only_and_reports_authority(self):
        cam = self.camera()
        c = I.Candidate(self.base / 'candidate', work=self.base / 'work')
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, '--retire-authority %s' % OLD):
                I.check(c, cam, self.replacing)
            point = I.check(c, cam, self.replacing, retire_authority=OLD)
        self.assertEqual(cam.mutations, [])
        self.assertEqual(point['authority'], ['native-select.resident', 'native-select.retry',
                                              'native-select.retry-used'])
        self.assertEqual(point['mode'], 'fresh')
        self.assertFalse((self.base / 'work/transaction.json').exists())

    def test_preflight_reports_unstaged_slot_before_any_camera_write(self):
        cam = self.camera()
        cam.dirs.remove(self.slot)
        c = I.Candidate(self.base / 'candidate', work=self.base / 'work')
        with contextlib.redirect_stdout(io.StringIO()):
            point = I.check(c, cam, self.replacing, retire_authority=OLD)
        self.assertFalse(point['slot_on_card'])
        self.assert_aborts(cam, 'run stage-tf first')

    def test_install_retires_stale_authority_and_selects_last(self):
        cam = self.camera()
        self.run_install(cam)
        self.assert_installed(cam)
        writes = [m for m in cam.mutations if m[0] in ('write', 'rename')]
        self.assertEqual(writes[0], ('write', SEL + '.rc-new'))
        self.assertEqual(writes[-1], ('rename', SEL + '.rc-new', SEL))
        self.assertEqual(cam.files[SEL + '.rc-new'] if SEL + '.rc-new' in cam.files else None, None)
        order = [m[1] for m in cam.mutations if m[0] == 'rename']
        self.assertLess(order.index(RESIDENT), order.index(S99 + '.rc-new'))
        self.assertEqual(sum(1 for m in cam.mutations if m[-1] == SEL), 2)  # disabled, then select
        # No authority was written for the new release.
        self.assertFalse([p for p, d in cam.files.items() if NEW.encode() in d and 'native-select' in p])
        before = list(cam.mutations)
        self.run_install(cam)  # done: verified, no writes
        self.assertEqual(cam.mutations, before)

    def prune_fixture(self, cam):
        obsolete = I.CFG + '/rollback-retired'
        archive = I.CFG + '/native-select.archived-retired'
        archived = I.CFG + '/startup.pending.archived-retired'
        items = {obsolete + '/S01app': b'old init', obsolete + '/S99cammy-tf': b'old hook',
                 archive + '/native-select.retry-used': b'old ledger', archived: b'old guard'}
        cam.dirs.update({obsolete, archive})
        cam.files.update(items)
        return items

    def test_prune_protects_factory_replacing_and_every_unfinished_journal_reference(self):
        cam = self.camera()
        retired = self.prune_fixture(cam)
        active = I.CFG + '/rollback-20261122-230956'
        in_progress = I.CFG + '/rollback-20261123-010203'
        moving = I.CFG + '/native-select.archived-20261123-010203'
        referenced = I.CFG + '/startup.pending.archived-cut'
        prior = I.CFG + '/rollback-20261121-160804'
        cam.dirs.update({active, in_progress, moving, prior})
        cam.files.update({path + '/S01app': b'non-factory' for path in (active, in_progress, prior)})
        cam.files[referenced] = b'journaled source'
        replacement = {**self.replacing, 'transaction': '20261122-230956', 'on_camera_rollback': active}
        journal = {'format': I.JOURNAL_FORMAT, 'status': 'rolling-back', 'rollback_dir': in_progress,
                   'archive_dir': moving, 'before': self.replacing['after_sha256'], 'previous_release': OLD,
                   'archived': {moving + '/startup.pending': {'from': referenced}}}
        work = self.base / 'lifecycle/jobs/job-another-install'
        work.mkdir(parents=True)
        (work / 'transaction.json').write_text(json.dumps(journal))
        (work / 'installed.json').write_text(json.dumps({**self.replacing, 'transaction': '20261121-160804'}))
        dry_run = I.prune(cam, self.base, replacement, out=lambda _: None)
        self.assertEqual(dry_run['protected'], {
            I.CFG + '/rollback-20261117': 'factory S01app',
            active: 'installed receipt',
            in_progress: 'unfinished journal',
            moving: 'unfinished journal',
            referenced: 'unfinished journal',
            prior: 'prior receipt of unfinished journal',
        })
        self.assertEqual(cam.mutations, [])
        self.assertFalse(list(self.base.glob('flash-archive-*')))
        I.prune(cam, self.base, replacement, apply=True, out=lambda _: None)
        self.assertTrue(set(dry_run['protected']).issubset(cam.files.keys() | cam.dirs))
        self.assertTrue(set(retired).isdisjoint(cam.files))
        self.assertTrue(set(dry_run['obsolete']).isdisjoint(cam.files.keys() | cam.dirs))
        self.assertEqual(I.prune(cam, self.base, replacement, out=lambda _: None)['obsolete'], {})
        for path, data in CREDS.items():
            self.assertEqual(cam.files[path], data)

    def test_install_prunes_the_replaced_rollback_once_its_own_receipt_is_durable(self):
        cam = self.camera()
        replaced = I.CFG + '/rollback-20261122-230956'
        cam.dirs.add(replaced)
        cam.files[replaced + '/S01app'] = b'0.8.3 selector'
        result = self.run_install(cam, replacing={**self.replacing, 'transaction': '20261122-230956',
                                                  'on_camera_rollback': replaced})
        self.assert_installed(cam)
        self.assertNotIn(replaced, cam.dirs)
        self.assertNotIn(replaced + '/S01app', cam.files)
        own = result['on_camera_rollback']
        self.assertTrue(any(p.startswith(own + '/') for p in cam.files))
        self.assertTrue(any(p.startswith(I.CFG + '/rollback-20261117/') for p in cam.files))   # factory
        for path, data in CREDS.items():
            self.assertEqual(cam.files[path], data)

    def test_prune_runs_before_headroom_and_before_live_boot_files_change(self):
        cam = self.camera()
        retired = self.prune_fixture(cam)

        def measure(candidate):
            self.assertTrue(set(retired).isdisjoint(cam.files))
            self.assertTrue(all(cam.files[target] == before for target, before in OLD_FILES.items()))
            return cam.free_bytes

        cam.overlay_free_bytes = measure
        self.run_install(cam)
        self.assertEqual(cam.mutations[0][0], 'prune')
        self.assert_installed(cam)

    def test_prune_aborts_on_hash_or_file_type_mismatch(self):
        cam = self.camera()
        wrong = I.CFG + '/native-select.archived-retired/record'
        cam.dirs.add(str(Path(wrong).parent))
        cam.files[wrong] = b'copy'
        cam.archive_md5 = lambda path: '0' * 32
        with self.assertRaisesRegex(SystemExit, 'MD5 did not match'):
            self.run_install(cam)
        cam.links.add(wrong)
        with self.assertRaisesRegex(SystemExit, 'regular archive member'):
            self.run_install(cam)
        self.assertEqual(cam.mutations, [])
        self.assertFalse(list(self.base.glob('flash-archive-*')))

    def test_stock_shell_prune_verifies_digest_boot_and_empty_directory(self):
        cfg = self.base / 'cammy'
        folder = cfg / 'native-select.archived-legacy'
        folder.mkdir(parents=True)
        boot_file = self.base / 'boot'
        boot_file.write_text(BOOT)
        synced = self.base / 'sync'
        file = folder / 'startup.pending'
        file.write_bytes(b'test guard')
        hidden = folder / '.unexpected'
        hidden.touch()
        remote_dir = I.CFG + '/' + folder.name
        remote_file = remote_dir + '/' + file.name

        def shell(command):
            command = command.replace(I.CFG, str(cfg)).replace('/proc/sys/kernel/random/boot_id', str(boot_file))
            result = subprocess.run(['dash', '-c', f'sync() {{ touch "{synced}"; }}; ' + command],
                                    capture_output=True, text=True, check=True)
            return result.stdout.replace(str(cfg), I.CFG)

        digest = I.archive_md5(shell, remote_file)
        self.assertEqual(digest, hashlib.md5(b'test guard').hexdigest())
        for wrong_md5, wrong_boot in (('0' * 32, BOOT), (digest, '0' * 36)):
            with self.assertRaises(subprocess.CalledProcessError):
                I.remove_archive_entry(shell, remote_file, wrong_md5, wrong_boot)
        with self.assertRaises(subprocess.CalledProcessError):
            I.remove_archive_entry(shell, remote_dir, None, BOOT)
        self.assertTrue(file.exists())
        self.assertFalse(synced.exists())
        I.remove_archive_entry(shell, remote_file, digest, BOOT)
        self.assertTrue(synced.exists())
        with self.assertRaises(subprocess.CalledProcessError):
            I.remove_archive_entry(shell, remote_dir, None, BOOT)
        hidden.unlink()
        I.remove_archive_entry(shell, remote_dir, None, BOOT)
        self.assertFalse(folder.exists())

    def test_prune_recovers_before_and_after_every_pc_and_camera_write(self):
        txid = '20261123-141500'

        def run(cam, root):
            I.prune(cam, root, inventory=I.prune_inventory(cam, root), apply=True,
                    txid=txid, out=lambda _: None)

        def verify(cam, root, retired):
            archived = {}
            for path in root.rglob('receipt.json'):
                receipt = json.loads(path.read_text())
                for member, entry in receipt['files'].items():
                    data = (path.parent / 'files' / Path(member).name).read_bytes()
                    self.assertEqual(entry['sha256'], sha(data))
                    self.assertEqual(entry['md5'], hashlib.md5(data).hexdigest())
                    archived[member] = data
            for member, data in retired.items():
                self.assertTrue(cam.files.get(member) == data or archived.get(member) == data, member)

        write = I.durable_write
        for surface in ('pc', 'camera'):
            for after in (False, True):
                for cut in range(1, 8 if surface == 'pc' else 7):
                    with self.subTest(surface=surface, after=after, cut=cut):
                        with tempfile.TemporaryDirectory(dir=self.base) as folder:
                            root = Path(folder)
                            cam = self.camera()
                            retired = self.prune_fixture(cam)
                            calls = 0

                            def save(path, data):
                                nonlocal calls
                                calls += 1
                                if calls == cut and not after:
                                    raise Crash(path)
                                write(path, data)
                                if calls == cut:
                                    raise Crash(path)

                            if surface == 'pc':
                                with patch.object(I, 'durable_write', save), self.assertRaises(Crash):
                                    run(cam, root)
                            else:
                                cam.fail_at, cam.fail_after = cut - 1, after
                                with self.assertRaises(Crash):
                                    run(cam, root)
                                cam.fail_at = None
                            verify(cam, root, retired)
                            cam.boot = '99999999-2222-4333-8444-555555555555'
                            run(cam, root)
                            verify(cam, root, retired)
                            self.assertTrue(set(retired).isdisjoint(cam.files))

    def test_low_nor_space_refuses_before_any_mutation(self):
        cam = self.camera()
        cam.free_bytes = 1024
        self.assert_aborts(cam, '/overlay has 1024 bytes free')
        self.assertEqual(cam.mutations, [])
        self.assertEqual(cam.files[SEL], I.SELECT)

    def test_spent_crash_authority_and_fault_archived_by_reviewed_install(self):
        path = I.CFG + '/native-select.crash-used'
        ledger = (f'format=cammy-crash-retry-used/1\nrelease_id={OLD}\ncandidate_md5={"c"*32}\n'
                  f'failed_boot=00000001-0000-4000-8000-000000000001\nretry_boot={BOOT}\n').encode()
        fault = b'format=cammy-runtime-health-fault/1\n'
        cam = self.camera(**{path: ledger, I.FAULT: fault})
        self.assert_aborts(cam, 'runtime-health-fault')
        self.assert_aborts(cam, 'retire-authority', retire=None, clear_latch=True)
        journal = self.run_install(cam, clear_latch=True)
        for src, data in ((path, ledger), (I.FAULT, fault)):
            self.assertNotIn(src, cam.files)
            self.assertEqual(cam.files[journal['archive_dir'] + '/' + Path(src).name], data)

    def test_authorization_required(self):
        self.assert_aborts(self.camera(), 'retire-authority %s' % OLD, retire=None)
        self.assert_aborts(self.camera(), 'retire-authority %s' % OLD, retire=OTHER)
        cam = self.camera(**{I.PENDING: ('phase=idle_ready\nboot_id=%s\n' % BOOT).encode()})
        self.assert_aborts(cam, 'startup.pending')
        self.tx(cam, clear_latch=True).install()
        self.assertIn(self.initial[I.PENDING], self.archive_files(cam).values())
        self.assert_installed(cam)

    def test_ledger_only_needs_attribution(self):
        cam = self.camera(**{RESIDENT: None, RETRY: None, I.RUNTIME_CONF: None})
        self.assert_aborts(cam, 'cannot attribute', replacing={**self.replacing, 'release': None})
        self.assert_aborts(cam, 'retire-authority %s' % OLD, retire=None)
        self.run_install(cam)
        self.assertIn(LEDGER_BYTES, self.archive_files(cam).values())

    def test_mixed_policy_rejected_without_writes(self):
        cases = [({RETRY: retry_optin(OTHER)}, {}, 'mixed policy'),
                 ({RESIDENT: resident(NEW)}, {}, 'predates its install'),
                 ({I.RUNTIME_CONF: ('release_id=%s\n' % OTHER).encode()}, {}, 'mixed policy'),
                 ({}, {'replacing': {**self.replacing, 'release': OTHER}}, 'mixed policy'),
                 ({ADVANCE: ('format=cammy-advance/1\nrelease_id=%s\nboot=%s\n' % (OTHER, BOOT)).encode()}, {},
                  'mixed policy')]
        for extra, kw, pattern in cases:
            with self.subTest(pattern=pattern, extra=list(extra)):
                self.assert_aborts(self.camera(**extra), pattern, **kw)

    def test_malformed_or_unsafe_state_rejected_without_writes(self):
        cases = [({RESIDENT: b'format=cammy-resident/1\r\nrelease_id=%s\n' % OLD.encode()}, 'malformed'),
                 ({RESIDENT: resident(OLD) + b'extra=1\n'}, 'malformed'),
                 ({RESIDENT: b'format=cammy-resident/1\nrelease_id=local-test\n'}, 'malformed'),
                 ({LEDGER: LEDGER_BYTES.replace(ATT.encode(), b'zz')}, 'malformed'),
                 ({LEDGER: b'format=cammy-power-retry-used/1\n'}, 'malformed'),
                 ({SEL: b'mode=yes\n'}, 'is '),
                 ({S99 + '.rc-new': b'half'}, 'staging residue')]
        for extra, pattern in cases:
            with self.subTest(pattern=pattern, extra=list(extra)):
                self.assert_aborts(self.camera(**extra), pattern)
        cam = self.camera(**{RESIDENT: None})
        cam.links.add(RESIDENT)
        self.assert_aborts(cam, 'link')
        cam = self.camera()
        cam.dirs.add(I.STATE + '/inflight-x')
        self.assert_aborts(cam, 'unexpected state entry')

    def test_crash_at_every_write_then_resume_or_rollback(self):
        spent = I.CFG + '/native-select.crash-used'
        recovery = {
            spent: (f'format=cammy-crash-retry-used/1\nrelease_id={OLD}\ncandidate_md5={"c"*32}\n'
                    f'failed_boot=00000001-0000-4000-8000-000000000001\nretry_boot={BOOT}\n').encode(),
            I.FAULT: b'fixture fault\n',
            I.PENDING: b'fixture guard\n',
        }
        clean = self.camera(**recovery)
        self.run_install(clean, clear_latch=True)
        total = len(clean.mutations)
        self.assertGreater(total, 20)
        for after in (False, True):
            for k in range(total):
                for finish in ('resume', 'rollback'):
                    with self.subTest(step=k, after=after, finish=finish, op=clean.mutations[k]):
                        (self.base / 'work/transaction.json').unlink(missing_ok=True)
                        cam = self.camera(**recovery)
                        cam.fail_at, cam.fail_after = k, after
                        with self.assertRaises(Crash):
                            self.run_install(cam, clear_latch=True)
                        self.assert_safe(cam)
                        if cam.files.get(SEL) == I.SELECT:  # only before the first write or after the last
                            self.assertTrue(k <= 1 or (after and k == total - 1), cam.mutations[-1])
                        for path, data in recovery.items():
                            self.assertTrue(cam.files.get(path) == data or data in self.archive_files(cam).values())
                        cam.fail_at = None
                        cam.boot = '99999999-2222-4333-8444-555555555555'  # power cycle at stock
                        if finish == 'resume':
                            self.run_install(cam, clear_latch=True)
                            self.assert_installed(cam)
                        else:
                            self.tx(cam).rollback()
                            self.assert_rolled_back(cam)

    def test_stock_only_resume_and_factory_recovery(self):
        clean = self.camera()
        result = self.run_install(clean, stock_only=True)
        self.assertEqual(result['recovery'], 'factory-stock')
        self.assertIsNone(I.receipt_rollback(result))
        self.assertNotIn(self.tx(clean).journal['rollback_dir'], clean.dirs)
        selected = max(i for i, op in enumerate(clean.mutations) if op == ('rename', SEL + '.rc-new', SEL))
        for step in range(selected + 1):
            for recovery in (False, True):
                with self.subTest(step=step, recovery=recovery):
                    (self.base / 'work/transaction.json').unlink()
                    cam = self.camera()
                    cam.fail_at = step
                    with self.assertRaises(Crash):
                        self.run_install(cam, stock_only=True)
                    cam.fail_at = None
                    tx = self.tx(cam)
                    self.assertTrue(tx.stock_only)
                    if recovery:
                        tx.rollback()
                        self.assertEqual(cam.files[S01], STOCK)
                        self.assertEqual(cam.files[SEL], I.DISABLED)
                        self.assertNotIn(S99, cam.files)
                        self.assertNotIn(ALLOW, cam.files)
                    else:
                        tx.install()
                        self.assertEqual(cam.files[SEL], I.SELECT)
                    self.assertNotIn(tx.journal['rollback_dir'], cam.dirs)

    def test_stock_only_requires_verified_factory_backup(self):
        cam = self.camera(**{I.CFG + '/rollback-20261117/S01app': b'wrong fixture'})
        self.assert_aborts(cam, 'verified factory S01app', stock_only=True)

    def test_failures_before_select_leave_stock(self):
        cam = self.camera(**{self.slot + '/native/face.so': b'truncated'})
        with self.assertRaisesRegex(SystemExit, 'TF slot differs'):
            self.run_install(cam)
        self.assertEqual(cam.files[SEL], I.DISABLED)
        self.assert_safe(cam)
        cam.files[self.slot + '/native/face.so'] = self.tf['native/face.so']
        self.run_install(cam)
        self.assert_installed(cam)

        (self.base / 'work/transaction.json').unlink()
        cam = self.camera()
        cam.corrupt_write = lambda p, d: d + b'x' if p == S99 + '.rc-new' else d
        with self.assertRaisesRegex(SystemExit, 'staged /etc/init.d/S99cammy-tf hash mismatch'):
            self.run_install(cam)
        self.assertEqual(cam.files[SEL], I.DISABLED)
        self.assert_safe(cam)
        cam.corrupt_write = None
        self.tx(cam).rollback()
        self.assert_rolled_back(cam)
        with self.assertRaisesRegex(SystemExit, 'rolled back'):
            self.run_install(cam)

    def test_rollback_after_complete_install(self):
        cam = self.camera()
        self.run_install(cam)
        self.tx(cam).rollback()
        self.assert_rolled_back(cam)
        for p in (RESIDENT, RETRY, LEDGER):
            self.assertNotIn(p, cam.files)  # authority stays archived; re-granting is explicit
            self.assertIn(self.initial[p], self.archive_files(cam).values())

    def test_writes_stay_inside_the_allowlist(self):
        cam = self.camera()
        self.run_install(cam)
        journal = json.loads((self.base / 'work/transaction.json').read_text())
        allowed = {*OLD_FILES, *(t + '.rc-new' for t in OLD_FILES), RESIDENT, RETRY, LEDGER,
                   I.STATE + '/claimed-' + ATT}
        for op, *paths in cam.mutations:
            for p in paths:
                self.assertTrue(p in allowed or p.startswith((journal['archive_dir'], journal['rollback_dir'])),
                                (op, p))


if __name__ == '__main__':
    unittest.main()
