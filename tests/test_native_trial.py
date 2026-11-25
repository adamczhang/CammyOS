"""WP4 activation tooling: FAKE-only tests (no camera, card, device or vendor exec).

* Selector: the inert shell proposal is relocated into a temporary fake root and
  run under dash with PATH stubs for mount/umount/sync/cp; the Python reference
  model runs against an identical tree; decisions and resulting state must match.
* Power loss is injected at every primitive of the Python model across multi-boot
  sequences and checked against one-attempt invariants.
* Late handoff and selected-boot verification use fake process tables only.
* Bundle tooling uses synthetic bytes; the real private candidate, if present, is
  only hashed/classified read-only.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from native_trial import BackendUnavailable  # noqa: E402
from native_trial import records as R  # noqa: E402
from native_trial import bundle, handoff, policy, review  # noqa: E402
from native_trial.faketree import FakeTree, PowerLoss, TreeEnv  # noqa: E402
from native_trial.selector import REASONS, run_selector  # noqa: E402

PROPOSAL = ROOT / 'native/activation/S00cammy-select.proposal'
DASH = shutil.which('dash')
HEADER_NAMES = ('CFG', 'STATE', 'S01', 'APP', 'RUNROOT', 'RUNDIR', 'PROCFS', 'DEVDIR', 'TMPROOT', 'LIBROOT')

SOURCE = b'\x7fELF stock sdvcam fixture\n' * 64
CANDIDATE = b'\x7fELF status-only candidate fixture\n' * 64
LIBRARY = b'\x7fELF status bridge library fixture\n' * 16
LIBNAME = R.LIBRARY_NAME
S01APP = b'#!/bin/sh\n# stock S01app fixture\n/usr/bin/sdvcam &\n'
BOOTS = [f'{i:08x}-0000-4000-8000-00000000000{i % 10}' for i in range(1, 40)]
ATT1, ATT2, ATT3, ATT4, ATT5 = (f'{i:x}' * 32 for i in (1, 2, 3, 4, 5))


def md5(data):
    return hashlib.md5(data).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def allow(klass=R.CLASS_STATUS_ONLY, candidate=CANDIDATE, library=LIBRARY):
    return R.allow_record(md5(S01APP), md5(SOURCE), len(SOURCE), md5(candidate), len(candidate), klass,
                          sha(library), md5(library), len(library))


def arm(attempt=ATT1, klass=R.CLASS_STATUS_ONLY, candidate=CANDIDATE, library=LIBRARY):
    return R.arm_record(attempt, md5(candidate), len(candidate), klass, sha(library), md5(library), len(library))


def card(attempt=ATT1, extra=None, **kwargs):
    files = {'arm': arm(attempt, **kwargs), 'candidate.bin': CANDIDATE, LIBNAME: LIBRARY}
    files.update(extra or {})
    return files


def relocate(text, root, gate='open'):
    """Rewrite only the path header and (to exercise the blocked design) the review gate."""
    out, in_header = [], False
    for line in text.splitlines(keepends=True):
        if line.startswith('# --- relocatable paths'):
            in_header = True
        elif line.startswith('# --- end relocatable paths'):
            in_header = False
        elif line == 'SELECT_GATE=blocked\n':
            line = f'SELECT_GATE={gate}\n'
        elif in_header:
            name, _, value = line.rstrip('\n').partition('=')
            if name == 'BLOCKTEST':
                line = 'BLOCKTEST=-e\n'
            else:
                assert name in HEADER_NAMES and value.startswith('/'), line
                line = f'{name}={root}{value}\n'
        out.append(line)
    return ''.join(out)


def body_system_paths(text):
    """Absolute camera paths outside the relocatable header and comments."""
    found, in_header = [], False
    for line in text.splitlines():
        if line.startswith('# --- relocatable paths'):
            in_header = True
        elif line.startswith('# --- end relocatable paths'):
            in_header = False
        elif not in_header and not line.lstrip().startswith('#'):
            found += re.findall(r'(?<![\w$}])/(?:etc|usr|run|proc|dev(?!/null)|mnt|data|overlay|tmp)\b', line)
    return found


class Fixture:
    """Builds identical fake roots for the shell proposal and the Python model."""

    def __init__(self, case, **kwargs):
        self.temporary = tempfile.TemporaryDirectory(prefix='cammy-wp4-')
        case.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        defaults = dict(boot_id=BOOTS[0], source=SOURCE, s01app=S01APP, allow=allow(), card=card())
        defaults.update(kwargs)
        self.shell = FakeTree.build(base / 'shell', **defaults)
        self.model = FakeTree.build(base / 'model', **defaults)
        self.trees = (self.shell, self.model)

    def each(self, method, *args, **kwargs):
        for tree in self.trees:
            getattr(tree, method)(*args, **kwargs)

    def edit(self, func):
        for tree in self.trees:
            func(tree)

    def run_shell(self, gate='open'):
        tree = self.shell
        hook = tree.ctl / 'hook.sh'
        hook.write_text(relocate(PROPOSAL.read_text(), tree.root, gate))
        env = {'PATH': f'{tree.ctl / "bin"}:/usr/bin:/bin', 'LC_ALL': 'C'}
        done = subprocess.run([DASH, str(hook), 'start'], capture_output=True, text=True,
                              env=env, timeout=60, cwd=tree.root)
        return done.returncode, done.stdout.strip(), done.stderr.strip()

    def run_model(self, powerloss_at_op=None, gate='open'):
        env = TreeEnv(self.model, powerloss_at_op)
        return run_selector(env, select_gate=gate), env


@unittest.skipUnless(DASH, 'dash is required to run the relocated selector proposal')
class SelectorEquivalenceTests(unittest.TestCase):
    """Shell proposal and Python model agree on every scenario."""

    def check(self, fixture, decision, reason, attempt=None, gate='open'):
        code, out, err = fixture.run_shell(gate)
        result, _ = fixture.run_model(gate=gate)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, result.line(), err)
        self.assertEqual((result.decision, result.reason), (decision, reason))
        if attempt is not None:
            self.assertEqual(result.attempt, attempt)
        shell, model = fixture.shell.snapshot(), fixture.model.snapshot()
        self.assertEqual(shell, model)
        for line in shell['calls']:
            self.assertNotIn('VIOLATION', line)
        if result.decision in ('stock', 'silent') and result.reason not in ('source-mismatch', 'already-bound'):
            self.assertEqual(shell['app_sha256'], hashlib.sha256(SOURCE).hexdigest())
            self.assertFalse(any('cammy-select/sdvcam' in m for m in shell['mounts']))
        self.assertFalse(any('cammy-select/tf' in m for m in shell['mounts']), 'TF left mounted before S01app')
        return result, shell

    def test_relocation_covers_every_system_path(self):
        self.assertEqual(body_system_paths(PROPOSAL.read_text()), [])
        self.assertEqual(PROPOSAL.stat().st_mode & 0o111, 0, 'proposal must not be executable')

    def test_reason_codes_documented(self):
        printed = set(re.findall(r'finish (?:stock|candidate|recovery-required) ([a-z0-9-]+)',
                                 PROPOSAL.read_text()))
        printed |= set(re.findall(r"reason=([a-z0-9-]+)'", PROPOSAL.read_text()))
        self.assertLessEqual(printed, set(REASONS))

    def test_not_enabled_is_silent_and_writes_nothing(self):
        fixture = Fixture(self, mode=None)
        result, snap = self.check(fixture, 'silent', 'not-enabled')
        self.assertEqual(snap['run'], [])
        self.assertEqual(snap['calls'], [])

    def test_unknown_mode_is_disabled(self):
        self.check(Fixture(self, mode='mode=SELECT'), 'stock', 'disabled')

    def test_run_must_be_tmpfs(self):
        self.check(Fixture(self, run_tmpfs=False), 'stock', 'run-not-tmpfs')

    def test_invalid_boot_id(self):
        self.check(Fixture(self, boot_id='not-a-boot-id'), 'stock', 'boot-id-invalid')

    def test_junk_state_file_is_invalid(self):
        self.check(Fixture(self, state={'notes.txt': b'x\n'}), 'stock', 'state-invalid')

    def test_bad_state_name_is_invalid(self):
        self.check(Fixture(self, state={'claimed-XYZ': b'x\n'}), 'stock', 'state-invalid')

    def test_happy_selection_then_post_attempt_boot(self):
        fixture = Fixture(self)
        result, snap = self.check(fixture, 'candidate', 'selected', ATT1)
        self.assertEqual(snap['app_sha256'], hashlib.sha256(CANDIDATE).hexdigest())
        self.assertIn(f'claimed-{ATT1}', snap['state'])
        self.assertIn('decision=candidate', snap['status'])
        lib = fixture.shell.path(R.library_path(sha(LIBRARY)))
        self.assertEqual(lib.read_bytes(), LIBRARY)
        self.assertEqual(lib.stat().st_mode & 0o777, 0o444)
        # Same boot again: no-op, nothing changes.
        before = fixture.shell.snapshot()['state']
        self.check(fixture, 'noop', 'already-ran-this-boot', ATT1)
        self.assertEqual(fixture.shell.snapshot()['state'], before)
        # Next boot: always stock, records the observed boot.
        fixture.each('reboot', BOOTS[1])
        result, snap = self.check(fixture, 'stock', 'post-attempt-boot')
        self.assertIn(f'boot={BOOTS[1]}', snap['state'][f'observed-{ATT1}'])
        # Same arm again: replay refused.
        fixture.each('reboot', BOOTS[2])
        self.check(fixture, 'stock', 'attempt-replayed', ATT1)

    def test_second_attempt_needs_ack_of_observed_boot(self):
        fixture = Fixture(self)
        self.check(fixture, 'candidate', 'selected', ATT1)
        fixture.each('reboot', BOOTS[1])
        self.check(fixture, 'stock', 'post-attempt-boot')

        def rearm(ack):
            def apply(tree):
                (tree.card / 'CammyOS-native/arm').write_bytes(arm(ATT2))
                if ack is not None:
                    (tree.card / f'CammyOS-native/ack-{ATT1}').write_bytes(ack)
            return apply
        fixture.edit(rearm(None))
        fixture.each('reboot', BOOTS[2])
        self.check(fixture, 'stock', 'unacknowledged-attempt', ATT2)
        fixture.edit(rearm(R.ack_record(ATT1, BOOTS[2], 'stock-restored')))  # wrong observed boot
        fixture.each('reboot', BOOTS[3])
        self.check(fixture, 'stock', 'unacknowledged-attempt', ATT2)
        fixture.edit(rearm(R.ack_record(ATT1, BOOTS[1], 'stock-restored')))
        fixture.each('reboot', BOOTS[4])
        self.check(fixture, 'candidate', 'selected', ATT2)

    def test_bad_ack_outcome(self):
        fixture = Fixture(self)
        self.check(fixture, 'candidate', 'selected', ATT1)
        fixture.each('reboot', BOOTS[1])
        self.check(fixture, 'stock', 'post-attempt-boot')
        bad = R.ack_record(ATT1, BOOTS[1], 'stock-restored').replace(b'stock-restored', b'looked-fine')

        def apply(tree):
            (tree.card / 'CammyOS-native/arm').write_bytes(arm(ATT2))
            (tree.card / f'CammyOS-native/ack-{ATT1}').write_bytes(bad)
        fixture.edit(apply)
        fixture.each('reboot', BOOTS[2])
        self.check(fixture, 'stock', 'unacknowledged-attempt', ATT2)

    def test_attempt_limit(self):
        state, extra = {}, {}
        for i, att in enumerate((ATT1, ATT2, ATT3, ATT4)):
            boot, seen = BOOTS[10 + 2 * i], BOOTS[11 + 2 * i]
            state[f'claimed-{att}'] = R.claim_record(att, boot, md5(CANDIDATE))
            state[f'observed-{att}'] = R.observed_record(att, seen)
            extra[f'ack-{att}'] = R.ack_record(att, seen, 'recovered')
        fixture = Fixture(self, state=state, card=card(ATT5, extra=extra))
        self.check(fixture, 'stock', 'attempt-limit')

    def test_observe_mode_runs_preflight_without_claim(self):
        fixture = Fixture(self, mode='observe')
        result, snap = self.check(fixture, 'stock', 'observe-only', ATT1)
        self.assertEqual(snap['state'], {})
        self.assertNotIn('run/cammy-select/sdvcam', snap['run'])
        self.assertFalse([p for p in snap['run'] if p.startswith('tmp/cammy-native-status/')])
        self.assertIn('mode=observe', snap['status'])

    def test_allowlist_rejects_connect_triggered_class(self):
        raw = allow().replace(b':status-only', b':media-connect-triggered')
        self.check(Fixture(self, allow=raw), 'stock', 'allowlist-invalid')

    def test_allowlist_missing(self):
        self.check(Fixture(self, allow=None), 'stock', 'allowlist-invalid')

    def test_allowlist_candidate_equal_to_source(self):
        raw = allow().replace(md5(CANDIDATE).encode(), md5(SOURCE).encode())
        self.check(Fixture(self, allow=raw), 'stock', 'allowlist-invalid')

    def test_media_lease_class_is_known_but_not_selectable(self):
        fixture = Fixture(self, allow=allow(R.CLASS_MEDIA_LEASE), card=card(klass=R.CLASS_MEDIA_LEASE))
        self.check(fixture, 'stock', 'class-ineligible', ATT1)

    def test_s01app_changed(self):
        self.check(Fixture(self, s01app=S01APP + b'# changed\n'), 'stock', 's01app-mismatch')

    def test_source_changed(self):
        self.check(Fixture(self, source=SOURCE + b'x'), 'stock', 'source-mismatch')

    def test_already_bound_without_claim(self):
        self.check(Fixture(self, extra_mounts=('tmpfs <R>/usr/bin/sdvcam tmpfs rw 0 0',)),
                   'stock', 'already-bound')

    def test_vfat_premounted(self):
        self.check(Fixture(self, extra_mounts=('/dev/mmcblk0p1 <R>/mnt/extsd vfat rw 0 0',)),
                   'stock', 'vfat-premounted')

    def test_no_card_device(self):
        self.check(Fixture(self, devices=()), 'stock', 'tf-unavailable')

    def test_whole_disk_card_fallback(self):
        self.check(Fixture(self, devices=('mmcblk0',)), 'candidate', 'selected', ATT1)

    def test_tf_mount_failure(self):
        fixture = Fixture(self)
        fixture.each('set_faults', 'tf-mount-fail')
        self.check(fixture, 'stock', 'tf-unavailable')

    def test_arm_missing(self):
        self.check(Fixture(self, card={'candidate.bin': CANDIDATE}), 'stock', 'arm-missing')

    def test_arm_shapes(self):
        good = arm()
        for bad in (good[:-1], good + b'extra=1\n', good.replace(b'attempt=', b'attempt=X'),
                    good.replace(b'candidate.bin', b'../sdvcam'), good.replace(R.ARM_FORMAT.encode(), b'cammy-native-arm/1'),
                    good.replace(b'lib_sha256=', b'lib_sha256=0'),
                    good.replace(b'size=', b'size=0'), b'lease=granted\n' + good[good.index(b'\n') + 1:]):
            with self.subTest(bad=bad[:40]):
                self.check(Fixture(self, card={'arm': bad, 'candidate.bin': CANDIDATE, LIBNAME: LIBRARY}),
                           'stock', 'arm-invalid')

    def test_arm_not_allowlisted(self):
        other = CANDIDATE + b'other'
        self.check(Fixture(self, card={'arm': arm(candidate=other), 'candidate.bin': other, LIBNAME: LIBRARY}),
                   'stock', 'candidate-not-allowlisted', ATT1)

    def test_candidate_bytes_mismatch(self):
        self.check(Fixture(self, card={'arm': arm(), 'candidate.bin': CANDIDATE[:-1] + b'?', LIBNAME: LIBRARY}),
                   'stock', 'candidate-mismatch', ATT1)

    def test_partial_candidate_on_card(self):
        self.check(Fixture(self, card={'arm': arm(), 'candidate.bin': CANDIDATE[:100], LIBNAME: LIBRARY}),
                   'stock', 'candidate-mismatch', ATT1)

    def test_tmp_must_be_tmpfs(self):
        self.check(Fixture(self, tmp_tmpfs=False), 'stock', 'lib-dir-not-tmpfs')

    def test_preexisting_library_dir_refused(self):
        fixture = Fixture(self)
        fixture.edit(lambda tree: tree.path(R.library_path(sha(LIBRARY))).parent.mkdir(parents=True))
        self.check(fixture, 'stock', 'lib-dir-exists')

    def test_library_root_symlink_refused(self):
        fixture = Fixture(self)
        fixture.edit(lambda tree: tree.path(R.LIBRARY_ROOT).symlink_to(tree.path('/run')))
        self.check(fixture, 'stock', 'lib-dir-exists')

    def test_missing_library_is_stock(self):
        self.check(Fixture(self, card={'arm': arm(), 'candidate.bin': CANDIDATE}),
                   'stock', 'library-mismatch', ATT1)

    def test_library_bytes_mismatch(self):
        self.check(Fixture(self, card={'arm': arm(), 'candidate.bin': CANDIDATE, LIBNAME: LIBRARY + b'x'}),
                   'stock', 'library-mismatch', ATT1)

    def test_library_not_allowlisted(self):
        other = LIBRARY + b'other'
        self.check(Fixture(self, card={'arm': arm(library=other), 'candidate.bin': CANDIDATE, LIBNAME: other}),
                   'stock', 'candidate-not-allowlisted', ATT1)

    def test_library_copy_failures_clean_up(self):
        for fault in ('copy-fail-2', 'copy-corrupt-2'):
            with self.subTest(fault=fault):
                fixture = Fixture(self)
                fixture.each('set_faults', fault)
                result, snap = self.check(fixture, 'stock', 'library-copy-failed', ATT1)
                self.assertEqual(snap['state'], {})
                self.assertNotIn(f'tmp/cammy-native-status/{sha(LIBRARY)}', snap['run'])

    def test_memory_low(self):
        self.check(Fixture(self, mem_available_kb=1024), 'stock', 'memory-low', ATT1)

    def test_copy_failures(self):
        for fault, reason in (('copy-fail', 'copy-failed'), ('copy-corrupt', 'copy-mismatch')):
            with self.subTest(fault=fault):
                fixture = Fixture(self)
                fixture.each('set_faults', fault)
                result, snap = self.check(fixture, 'stock', reason, ATT1)
                self.assertEqual(snap['state'], {})

    def test_tf_umount_falls_back_to_lazy_detach(self):
        fixture = Fixture(self)
        fixture.each('set_faults', 'tf-umount-fail')
        result, snap = self.check(fixture, 'candidate', 'selected', ATT1)
        self.assertIn('umount -l <R>/run/cammy-select/tf', snap['calls'])

    def test_tf_detach_failure_never_selects(self):
        fixture = Fixture(self)
        fixture.each('set_faults', 'tf-umount-fail', 'tf-umount-lazy-fail')
        code, out, err = fixture.run_shell()
        result, _ = fixture.run_model()
        self.assertEqual(out, result.line())
        self.assertEqual((result.decision, result.reason), ('recovery-required', 'tf-detach-failed'))
        self.assertEqual(fixture.shell.snapshot(), fixture.model.snapshot())
        snap = fixture.shell.snapshot()
        self.assertEqual(snap['state'], {})
        self.assertEqual(snap['app_sha256'], hashlib.sha256(SOURCE).hexdigest())

    def test_claim_write_refused(self):
        fixture = Fixture(self, state={})
        fixture.edit(lambda tree: tree.state_dir.chmod(0o500))
        self.addCleanup(lambda: [tree.state_dir.chmod(0o700) for tree in fixture.trees])
        if os.geteuid() == 0:
            self.skipTest('permission fixture needs a non-root user')
        result, snap = self.check(fixture, 'stock', 'claim-failed', ATT1)
        self.assertEqual(snap['state'], {})

    def test_claim_partial_write_consumes_attempt(self):
        fixture = Fixture(self)
        fixture.each('set_faults', 'corrupt-on-sync')
        result, snap = self.check(fixture, 'stock', 'claim-failed', ATT1)
        self.assertIn(f'claimed-{ATT1}', snap['state'])
        fixture.each('reboot', BOOTS[1])
        self.check(fixture, 'stock', 'post-attempt-boot')
        fixture.each('reboot', BOOTS[2])
        self.check(fixture, 'stock', 'attempt-replayed', ATT1)

    def test_bind_failures(self):
        cases = ((('bind-fail',), 'stock', 'bind-failed'),
                 (('bind-wrong-content',), 'stock', 'bind-verify-failed'),
                 (('bind-wrong-content', 'app-umount-fail'), 'recovery-required', 'bind-unrestorable'))
        for faults, decision, reason in cases:
            with self.subTest(faults=faults):
                fixture = Fixture(self)
                fixture.each('set_faults', *faults)
                code, out, err = fixture.run_shell()
                result, _ = fixture.run_model()
                self.assertEqual(out, result.line(), err)
                self.assertEqual((result.decision, result.reason), (decision, reason))
                self.assertEqual(fixture.shell.snapshot(), fixture.model.snapshot())
                # The attempt is consumed either way; the next boot is stock.
                fixture.each('reboot', BOOTS[1], faults=())
                self.check(fixture, 'stock', 'post-attempt-boot')

    def test_shell_power_loss_at_claim_sync(self):
        for mode, next_decision in (('lose', ('candidate', 'selected')),
                                    ('keep', ('stock', 'post-attempt-boot')),
                                    ('truncate', ('stock', 'post-attempt-boot'))):
            with self.subTest(unsynced=mode):
                fixture = Fixture(self)
                fixture.shell.set_faults('powerloss-at-sync-1')
                code, out, err = fixture.run_shell()
                self.assertEqual(code, -9, (out, err))
                self.assertNotIn('decision=candidate', out)
                self.assertEqual(fixture.shell.snapshot()['app_sha256'], hashlib.sha256(SOURCE).hexdigest())
                fixture.shell.reboot(BOOTS[1], unsynced=mode)
                fixture.model.reboot(BOOTS[1])
                # Rebuild the model tree's state to match the shell's surviving state.
                shutil.rmtree(fixture.model.state_dir, ignore_errors=True)
                if fixture.shell.state_dir.exists():
                    shutil.copytree(fixture.shell.state_dir, fixture.model.state_dir)
                fixture.model._record_synced()
                for tree in fixture.trees:
                    (tree.ctl / 'calls.log').unlink(missing_ok=True)
                self.check(fixture, *next_decision)


class SelectorPowerLossSweep(unittest.TestCase):
    """Python model: power loss at every primitive of every boot of a 5-boot script.

    Script: boot0 arms A1; boot1 is the post-attempt boot; before boot2 the operator
    adds ack-A1 (quoting the observed boot, if one was recorded) and arms A2; boot3
    and boot4 follow. Unsynced internal writes are lost, kept or truncated.
    """

    BOOTS_PER_RUN = 5

    def script(self, fail_boot, fail_op, unsynced):
        temporary = tempfile.TemporaryDirectory(prefix='cammy-wp4-pl-')
        self.addCleanup(temporary.cleanup)
        tree = FakeTree.build(Path(temporary.name) / 't', boot_id=BOOTS[0], source=SOURCE, s01app=S01APP,
                              allow=allow(), card=card(ATT1))
        outcomes, ops = [], 0
        for boot in range(self.BOOTS_PER_RUN):
            if boot == 2:
                observed = tree.state_dir / f'observed-{ATT1}'
                if observed.exists() and R.record_ok(observed.read_bytes(), R.OBSERVED_KEYS):
                    seen = R.get(observed.read_bytes(), 'boot')
                    (tree.card / f'{"CammyOS-native"}/ack-{ATT1}').write_bytes(
                        R.ack_record(ATT1, seen, 'stock-restored'))
                (tree.card / 'CammyOS-native/arm').write_bytes(arm(ATT2))
            env = TreeEnv(tree, fail_op if boot == fail_boot else None)
            try:
                result = run_selector(env, select_gate="open")
            except PowerLoss:
                result = None
            ops += len(env.ops)
            outcomes.append(result)
            app = tree.path('/usr/bin/sdvcam').read_bytes()
            if result is not None and result.decision == 'candidate':
                claim = tree.state_dir / f'claimed-{result.attempt}'
                self.assertTrue(claim.exists(), 'selected without claim')
                self.assertEqual(tree._synced().get(claim.name), hashlib.sha256(claim.read_bytes()).hexdigest(),
                                 'selected before the claim was synced')
                self.assertEqual(app, CANDIDATE)
            elif result is not None:
                self.assertEqual(app, SOURCE)
            if result is not None:  # a power-lost boot runs nothing further
                self.assertFalse(any('cammy-select/tf' in m for m in tree.mounts()), 'TF left mounted')
            tree.reboot(BOOTS[boot + 1], unsynced=unsynced)
        return outcomes

    def clean_ops(self):
        counts, temporary = [], tempfile.TemporaryDirectory(prefix='cammy-wp4-ops-')
        self.addCleanup(temporary.cleanup)
        tree = FakeTree.build(Path(temporary.name) / 't', boot_id=BOOTS[0], source=SOURCE, s01app=S01APP,
                              allow=allow(), card=card(ATT1))
        for boot in range(self.BOOTS_PER_RUN):
            if boot == 2:
                seen = R.get((tree.state_dir / f'observed-{ATT1}').read_bytes(), 'boot')
                (tree.card / f'CammyOS-native/ack-{ATT1}').write_bytes(R.ack_record(ATT1, seen, 'recovered'))
                (tree.card / 'CammyOS-native/arm').write_bytes(arm(ATT2))
            env = TreeEnv(tree)
            counts.append((run_selector(env, select_gate="open").reason, len(env.ops)))
            tree.reboot(BOOTS[boot + 1])
        return counts

    def test_clean_script(self):
        reasons = [reason for reason, _ in self.clean_ops()]
        self.assertEqual(reasons, ['selected', 'post-attempt-boot', 'selected', 'post-attempt-boot',
                                   'attempt-replayed'])

    def test_one_attempt_under_power_loss_anywhere(self):
        runs = 0
        for boot, (_, count) in enumerate(self.clean_ops()):
            for op in range(1, count + 1):
                for unsynced in ('lose', 'keep', 'truncate'):
                    with self.subTest(boot=boot, op=op, unsynced=unsynced):
                        outcomes = self.script(boot, op, unsynced)
                        runs += 1
                        picked = [(i, r.attempt) for i, r in enumerate(outcomes) if r and r.decision == 'candidate']
                        attempts = [a for _, a in picked]
                        self.assertEqual(len(attempts), len(set(attempts)), outcomes)
                        for i, _ in picked:
                            if i + 1 < len(outcomes) and outcomes[i + 1] is not None:
                                self.assertNotEqual(outcomes[i + 1].decision, 'candidate', outcomes)
        self.assertGreater(runs, 100)


# ---------------------------------------------------------------------------
# Bundle classification/staging (synthetic bytes; stock constants patched).
# ---------------------------------------------------------------------------
import struct  # noqa: E402
from unittest import mock  # noqa: E402

from native_trial import cli  # noqa: E402
from native_trial.fakeproc import FakeAttemptStore, FakeClock, FakeControl, FakeWorld  # noqa: E402

SYN_SIZE = 0x8D3100
STOCK_PRIVATE = ROOT / 'build/fork/stock-rootfs/usr/bin/sdvcam'
MEDIA_CANDIDATE_PRIVATE = ROOT / 'private/native-start/sdvcam-media-candidate'


def synthetic_library(machine=40, flags=0x05000400, e_type=3):
    header = b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8)
    header += struct.pack('<HHIIIIIHHHHHH', e_type, machine, 1, 0, 52, 0, flags, 52, 32, 1, 40, 0, 0)
    return header + b'\0' * 200 + b'status bridge synthetic body'


STUB_FILE_OFFSET = 0x8D2E40  # synthetic status stub at the reviewed WP2A stub start


def bl_word(site, target):
    return 0xEB000000 | (((target - (site + 8)) >> 2) & 0xFFFFFF)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cammy-wp4-bundle-')
        self.addCleanup(self.temporary.cleanup)
        self.dir = Path(self.temporary.name)
        source = bytearray(SYN_SIZE)
        for i in range(0, SYN_SIZE, 4096):
            source[i:i + 8] = struct.pack('<Q', i * 2654435761 & 0xFFFFFFFFFFFFFFFF)
        source[0x8D2E1C:0x8D3030] = bytes(0x8D3030 - 0x8D2E1C)
        source[0xA4:0xAC] = struct.pack('<II', policy.STOCK_TEXT_LOAD_SIZE, policy.STOCK_TEXT_LOAD_SIZE)
        self.source_bytes = bytes(source)
        self.source = self.write('source', self.source_bytes)
        self.library_bytes = synthetic_library()
        self.library = self.write('lib.so', self.library_bytes)
        self.candidate = self.write('candidate', self.status_candidate())
        self.evidence = self.dir / 'evidence'
        source_sha = hashlib.sha256(self.source_bytes).hexdigest()
        for name, value in (('STOCK_SDVCAM_SHA256', source_sha), ('STOCK_SDVCAM_MD5', md5(self.source_bytes)),
                            ('STOCK_SDVCAM_SIZE', SYN_SIZE), ('STOCK_S01APP_MD5', md5(S01APP))):
            patcher = mock.patch.object(policy, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def status_candidate(self, library=None, extra=(), bl=True, path=None, load=0x8D2F00):
        data = bytearray(self.source_bytes)
        if bl:
            data[0x21DB0:0x21DB4] = struct.pack('<I', bl_word(0x21DB0, STUB_FILE_OFFSET))
            data[STUB_FILE_OFFSET:STUB_FILE_OFFSET + 8] = bytes.fromhex('04e02de500f020e3')
        embedded = path if path is not None else R.library_path(sha(library or self.library_bytes)).encode()
        data[0x8D2E60:0x8D2E60 + len(embedded) + 1] = embedded + b'\0'
        data[0xA4:0xAC] = struct.pack('<II', load, load)
        for offset, blob in extra:
            data[offset:offset + len(blob)] = blob
        return bytes(data)

    def review(self, candidate=None, library=None, klass='fixture', name='review.json', mutate=None):
        """A SHA256-bound review record whose proof artifacts exist under self.evidence."""
        candidate = candidate or self.candidate
        library = library or self.library
        self.evidence.mkdir(exist_ok=True)
        record = review.template(source_sha256=sha(self.source_bytes), candidate_sha256=sha(candidate.read_bytes()),
                                 library_sha256=sha(library.read_bytes()), evidence_class=klass)
        record['provenance'] = {'source_commit': 'c81d295', 'build_recipe_sha256': 'b' * 64,
                                'builder': 'fixture-builder', 'reviewer': 'fixture-reviewer',
                                'reviewed_utc': '2026-11-14T18:00:00Z'}
        for proof, entry in record['proofs'].items():
            artifact = self.evidence / 'proofs' / f'{proof}.txt'
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text(f'FIXTURE proof transcript for {proof}\n')
            entry.update(result='pass', sha256=sha(artifact.read_bytes()))
        if mutate:
            mutate(record)
        path = self.evidence / name
        path.write_text(json.dumps(record))
        return path

    def classify(self, candidate=None, library=None, review_path=None, klass=R.CLASS_STATUS_ONLY):
        return bundle.classify(candidate or self.candidate, self.source, klass, library or self.library,
                               review_path, self.evidence)

    def stage(self, name, klass='fixture'):
        out = self.dir / 'stage' / name
        manifest = bundle.make_bundle(self.candidate, self.source, self.library, R.CLASS_STATUS_ONLY, out,
                                      review=self.review(klass=klass), evidence_root=self.evidence)
        return out, manifest

    def test_structure_alone_is_never_eligible(self):
        report = self.classify()
        self.assertTrue(report['structural_ok'], report['reasons'])
        self.assertEqual(report['structural_class'], R.CLASS_STATUS_ONLY)
        self.assertEqual(report['trust'], 'structural-only')
        self.assertFalse(report['eligible'])
        self.assertFalse(report['eligible_for_staging'])
        self.assertFalse(report['activation_eligible'])
        self.assertIn('structural classification only', ' '.join(report['reasons']))
        with self.assertRaises(bundle.BundleError):
            bundle.make_bundle(self.candidate, self.source, self.library, R.CLASS_STATUS_ONLY,
                               self.dir / 'stage' / 'none', review=None)

    def test_arbitrary_cave_code_needs_bound_review(self):
        # Any bytes in the allowed cave pass the structural scope; trust must not follow.
        hostile = self.write('hostile', self.status_candidate(
            extra=((STUB_FILE_OFFSET + 8, bytes(range(1, 0x18))),)))
        report = self.classify(hostile)
        self.assertTrue(report['structural_ok'], report['reasons'])
        self.assertFalse(report['eligible'])
        # A review written for the benign candidate does not transfer to other bytes.
        report = self.classify(hostile, review_path=self.review())
        self.assertFalse(report['eligible_for_staging'])
        self.assertIn('review candidate_sha256 does not match the supplied bytes', report['reasons'])

    def test_fixture_review_is_local_only_and_reviewed_is_still_not_activation(self):
        report = self.classify(review_path=self.review(klass='fixture'))
        self.assertTrue(report['eligible_for_staging'], report['reasons'])
        self.assertTrue(report['local_only'])
        self.assertFalse(report['eligible'])
        report = self.classify(review_path=self.review(klass='reviewed', name='r2.json'))
        self.assertTrue(report['eligible'], report['reasons'])
        self.assertEqual(report['e1'], 'evidence complete and bound; manager closure pending')
        self.assertFalse(report['activation_eligible'])
        self.assertIn('activation ineligible', report['activation'])

    def test_forged_or_incomplete_reviews_refused(self):
        def drop_proof(r):
            del r['proofs']['device_abi']
        def failed_proof(r):
            r['proofs']['lifecycle_fixture']['result'] = 'fail'
        def bad_hash(r):
            r['proofs']['stub_slice']['sha256'] = '0' * 64
        def escaping(r):
            r['proofs']['identity_scope']['artifact'] = '../../etc/passwd'
        def wrong_path(r):
            r['library_ram_path'] = '/tmp/cammy-native-status/x/libcammy-native-status.so'
        def capability(r):
            r['behavior']['media_paths'] = 'connect-triggered'
        def lease_key(r):
            r['lease'] = 'granted'
        def wrong_library(r):
            r['library_sha256'] = 'c' * 64
        def class_string_only(r):
            r['proofs'] = 'status-only'
        for mutate in (drop_proof, failed_proof, bad_hash, escaping, wrong_path, capability, lease_key,
                       wrong_library, class_string_only):
            with self.subTest(mutate.__name__):
                report = self.classify(review_path=self.review(name=f'{mutate.__name__}.json', mutate=mutate))
                self.assertFalse(report['eligible_for_staging'])
                self.assertFalse(report['eligible'])
        missing = self.review(name='missing.json')
        (self.evidence / 'proofs' / 'status_semantics.txt').unlink()
        self.assertFalse(self.classify(review_path=missing)['eligible_for_staging'])

    def test_status_only_candidate_is_staged_local_only(self):
        out, manifest = self.stage('b1')
        self.assertFalse(manifest['installed'])
        self.assertTrue(manifest['local_only'])
        self.assertFalse(manifest['activation_eligible'])
        self.assertEqual(manifest['library_ram_path'], R.library_path(sha(self.library_bytes)))
        self.assertEqual(manifest['library_ram_path'],
                         f'/tmp/cammy-native-status/{sha(self.library_bytes)}/libcammy_native_status.so')
        self.assertEqual(manifest['identities']['library']['sha256'], sha(self.library_bytes))
        self.assertIn('weak mismatch check', manifest['on_camera_check'])
        self.assertEqual(sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()),
                         sorted(bundle.BUNDLE_FILES))
        arm_rec = R.parse((out / 'tf/CammyOS-native/arm').read_bytes(), R.ARM_KEYS, R.ARM_FORMAT)
        self.assertEqual(arm_rec['lib_sha256'], sha(self.library_bytes))
        self.assertTrue(bundle.validate_bundle(out, self.source, self.evidence)['ok'])
        # Self-consistent files alone never validate.
        self.assertFalse(bundle.validate_bundle(out)['ok'])
        self.assertFalse(bundle.validate_bundle(out, self.source)['ok'])

    def test_bundle_drives_fake_selector_design_and_shipped_gate_blocks(self):
        out, manifest = self.stage('b2')
        tf = out / 'tf/CammyOS-native'
        def tree_for(name):
            return FakeTree.build(self.dir / name, boot_id=BOOTS[0], source=self.source_bytes, s01app=S01APP,
                                  allow=(out / 'internal-proposal/native-select.allow').read_bytes(),
                                  card={p.name: p.read_bytes() for p in tf.iterdir()}, mem_available_kb=80000)
        shipped = tree_for('shipped')
        self.assertEqual(run_selector(TreeEnv(shipped)).reason, 'select-blocked')
        self.assertFalse(shipped.state_dir.exists())
        self.assertEqual(shipped.calls(), [])
        tree = tree_for('design')
        result = run_selector(TreeEnv(tree), select_gate="open")
        self.assertEqual((result.decision, result.reason, result.attempt),
                         ('candidate', 'selected', manifest['attempt']))
        self.assertEqual(tree.path(manifest['library_ram_path']).read_bytes(), self.library_bytes)
        tree.reboot(BOOTS[1])
        self.assertEqual(run_selector(TreeEnv(tree), select_gate="open").reason, 'post-attempt-boot')
        seen = R.get((tree.state_dir / f"claimed-{manifest['attempt']}").read_bytes(), 'boot')
        self.assertEqual(seen, BOOTS[0])
        ack = bundle.make_ack(manifest['attempt'], BOOTS[1], 'stock-restored', self.dir / 'stage' / 'ack')
        self.assertTrue(R.record_ok(ack.read_bytes(), R.ACK_KEYS))

    def test_connect_trigger_patch_is_never_status_only(self):
        candidate = self.write('c', self.status_candidate(extra=((0x9C5D0, b'\x99\x99\x99\xea'),)))
        report = self.classify(candidate, review_path=self.review(candidate=candidate, klass='reviewed'))
        self.assertEqual(report['structural_class'], R.CLASS_MEDIA_CONNECT)
        self.assertFalse(report['eligible'])
        self.assertIsNone(report['review'])

    def test_scope_bl_path_and_library_checks(self):
        other = R.library_path('0' * 64).encode()
        cases = {
            'outside scope': self.status_candidate(extra=((0x30000, b'\xff'),)),
            'no bl change': self.status_candidate(bl=False),
            'bl outside cave': self.status_candidate(extra=((0x21DB0, struct.pack('<I', bl_word(0x21DB0, 0x40000))),)),
            'bl not to stub start': self.status_candidate(
                extra=((0x21DB0, struct.pack('<I', bl_word(0x21DB0, STUB_FILE_OFFSET + 4))),)),
            'not a bl': self.status_candidate(extra=((0x21DB0, b'\x00\x00\xa0\xe1'),)),
            'load past last text page': self.status_candidate(load=0x8D3010),
            'load not grown': self.status_candidate(load=policy.STOCK_TEXT_LOAD_SIZE),
            'stub beyond load': self.status_candidate(load=0x8D2E50),
            'media cave range touched': self.status_candidate(extra=((0x8D2E20, b'\x01\x02\x03\xea'),)),
            'stray cave path': self.status_candidate(extra=((0x8D2EE0, b'/run/evil.so\0'),)),
            'wrong path': self.status_candidate(path=other),
            'two paths': self.status_candidate(extra=((0x8D2EE0, other + b'\0'),)),
            'old md5 hyphen path': self.status_candidate(
                path=f'/tmp/cammy-native-status/{md5(self.library_bytes)}/libcammy-native-status.so'.encode()),
        }
        for label, data in cases.items():
            with self.subTest(label):
                report = self.classify(self.write('x', data))
                self.assertFalse(report['structural_ok'], report['reasons'])
                self.assertFalse(report['eligible'])
        for label, lib in {'x86': synthetic_library(machine=3), 'soft-float': synthetic_library(flags=0x05000200),
                           'exec': synthetic_library(e_type=2), 'not elf': b'MZ' + bytes(100)}.items():
            with self.subTest(label):
                path = self.write('l', lib)
                cand = self.write('c2', self.status_candidate(library=lib))
                self.assertFalse(self.classify(cand, path)['structural_ok'])
        self.assertFalse(bundle.classify(self.candidate, self.source, R.CLASS_STATUS_ONLY, None)['structural_ok'])
        self.assertFalse(bundle.classify(self.candidate, None, R.CLASS_STATUS_ONLY, self.library)['structural_ok'])
        self.assertFalse(bundle.classify(self.candidate, self.source, None, self.library)['structural_ok'])
        self.assertFalse(bundle.classify(self.candidate, self.source, R.CLASS_MEDIA_LEASE, self.library)['eligible'])

    def test_source_must_be_stock(self):
        with mock.patch.object(policy, 'STOCK_SDVCAM_SHA256', '0' * 64):
            report = self.classify()
        self.assertIn('source is not the verified stock sdvcam', report['reasons'])

    def test_staging_destination_guard(self):
        review_path = self.review()
        for out in (Path('/etc/cammy-native-test'), ROOT / 'docs' / 'nope', Path('/run/media/x/CammyOS-native')):
            with self.subTest(out=str(out)):
                with self.assertRaises(bundle.BundleError):
                    bundle.make_bundle(self.candidate, self.source, self.library, R.CLASS_STATUS_ONLY, out,
                                       review=review_path, evidence_root=self.evidence)
                self.assertFalse(out.exists())
        busy = self.dir / 'stage' / 'busy'
        busy.mkdir(parents=True)
        (busy / 'x').write_text('x')
        with self.assertRaises(bundle.BundleError):
            bundle.make_bundle(self.candidate, self.source, self.library, R.CLASS_STATUS_ONLY, busy,
                               review=review_path, evidence_root=self.evidence)

    def test_validate_detects_tampering(self):
        tampers = {
            'candidate': lambda o: (o / 'tf/CammyOS-native/candidate.bin').write_bytes(b'x'),
            'library': lambda o: (o / f'tf/CammyOS-native/{R.LIBRARY_NAME}').write_bytes(synthetic_library() + b'x'),
            'extra': lambda o: (o / 'tf/CammyOS-native/run-me.sh').write_text('x'),
            'missing': lambda o: (o / 'tf/CammyOS-native/arm').unlink(),
            'allow': lambda o: (o / 'internal-proposal/native-select.allow').write_bytes(
                (o / 'internal-proposal/native-select.allow').read_bytes().replace(b'status-only', b'media-lease-gated')),
            'manifest': lambda o: (o / 'bundle-manifest.json').write_text(
                (o / 'bundle-manifest.json').read_text().replace('"installed": false', '"installed": true')),
            'activation claim': lambda o: (o / 'bundle-manifest.json').write_text(
                (o / 'bundle-manifest.json').read_text().replace('"activation_eligible": false',
                                                                 '"activation_eligible": true')),
            'local-only flip': lambda o: (o / 'bundle-manifest.json').write_text(
                (o / 'bundle-manifest.json').read_text().replace('"local_only": true', '"local_only": false')),
            'review': lambda o: (o / 'review.json').write_text(
                (o / 'review.json').read_text().replace('"fixture"', '"reviewed"')),
        }
        for label, tamper in tampers.items():
            with self.subTest(label):
                out, _ = self.stage(label.replace(' ', '-'))
                tamper(out)
                self.assertFalse(bundle.validate_bundle(out, self.source, self.evidence)['ok'])
        out, _ = self.stage('symlink')
        target = out / 'tf/CammyOS-native/candidate.bin'
        target.rename(out / 'elsewhere.bin')
        target.symlink_to(out / 'elsewhere.bin')
        self.assertFalse(bundle.validate_bundle(out, self.source, self.evidence)['ok'])

    def test_proposal_patch_refuses_every_bundle(self):
        out, _ = self.stage('pp')
        from native_trial import cli
        target = self.dir / 'pp.patch'
        with mock.patch('sys.stdout', new_callable=io.StringIO) as printed:
            self.assertEqual(cli.main(['proposal-patch', '--out', str(target), '--bundle', str(out)]), 3)
        self.assertIn('activation blocked', printed.getvalue())
        self.assertFalse(target.exists())

    @unittest.skipUnless(STOCK_PRIVATE.is_file() and MEDIA_CANDIDATE_PRIVATE.is_file(),
                         'private stock/candidate bytes not present')
    def test_real_private_media_candidate_is_ineligible(self):
        mock.patch.stopall()  # use the real stock identities for this read-only check
        report = bundle.classify(MEDIA_CANDIDATE_PRIVATE, STOCK_PRIVATE, R.CLASS_STATUS_ONLY, None)
        self.assertEqual(report['source']['sha256'], policy.STOCK_SDVCAM_SHA256)
        self.assertEqual(report['structural_class'], R.CLASS_MEDIA_CONNECT)
        self.assertEqual(len(report['connect_trigger_sites_modified']), 2)
        self.assertFalse(report['eligible'])
        # A forged "reviewed" record bound to its exact bytes changes nothing.
        forged = self.dir / 'forged.json'
        forged.write_text(json.dumps(review.template(
            source_sha256=policy.STOCK_SDVCAM_SHA256, candidate_sha256=report['candidate_sha256'],
            library_sha256=sha(self.library_bytes), evidence_class='reviewed')))
        again = bundle.classify(MEDIA_CANDIDATE_PRIVATE, STOCK_PRIVATE, R.CLASS_STATUS_ONLY, self.library, forged)
        self.assertFalse(again['eligible'])
        self.assertFalse(again['eligible_for_staging'])
        self.assertEqual(again['structural_class'], R.CLASS_MEDIA_CONNECT)


# ---------------------------------------------------------------------------
# Late handoff (gated) and selected-boot verification: fake processes only.
# ---------------------------------------------------------------------------
SRC_MD5, CAND_MD5 = 'a' * 32, 'c' * 32
OPEN_CONTRACT = handoff.Contract(True, True, True, True, True)
BUDGETS = handoff.Budgets(stop=5, ready=10, candidate_stop=5, restore=20, overall=60, poll=0.5)


def stock_proc(pid=300, starttime=1000):
    return handoff.Proc(pid, starttime, SRC_MD5, 'sdvcam', frozenset({'/dev/watchdog', '/dev/video0'}))


class HandoffTests(unittest.TestCase):
    def make(self, world=None, store=None, contract=OPEN_CONTRACT, budgets=BUDGETS, abort_at=None,
             abort_reason='preview-stop', lease=True, control=True, **behaviour):
        self.clock = FakeClock()
        self.world = world or FakeWorld([stock_proc(), handoff.Proc(1, 1, 'b' * 32, 'init')])
        self.store = store or FakeAttemptStore({'attempt': ATT1, 'candidate_md5': CAND_MD5})
        self.control = FakeControl(self.world, self.clock, SRC_MD5, CAND_MD5, **behaviour) if control else None

        def abort():
            return abort_reason if abort_at is not None and self.clock.now() >= abort_at else None
        return handoff.Handoff(store=self.store, view=self.world, clock=self.clock, boot_id=BOOTS[0],
                               source_md5=SRC_MD5, candidate_md5=CAND_MD5,
                               lease_valid=lambda owner, gen: lease and owner == 'agent-1' and gen == 7,
                               abort_requested=abort, control=self.control, contract=contract, budgets=budgets)

    def request(self, **changes):
        values = dict(boot_id=BOOTS[0], nonce='n-1', attempt=ATT1, candidate_md5=CAND_MD5,
                      lease_owner='agent-1', lease_generation=7)
        values.update(changes)
        return handoff.Request(**values)

    def natives(self):
        return [p for p in self.world.processes() if p.comm == 'sdvcam']

    def test_current_contract_refuses_before_anything(self):
        machine = self.make(contract=handoff.CURRENT_CONTRACT, control=False, budgets=handoff.Budgets())
        outcome = machine.run(self.request())
        self.assertEqual(outcome.state, handoff.State.REFUSED)
        self.assertIn('native-backend-unavailable', outcome.reasons)
        self.assertTrue(any(r.startswith('gate:') for r in outcome.reasons))
        self.assertEqual(self.store.claims, {})

    def test_unavailable_control_always_raises(self):
        control = handoff.UnavailableNativeControl()
        ident = handoff.Identity(1, 1, SRC_MD5)
        for call in (lambda: control.request_stock_stop(ident), control.launch_candidate, control.launch_stock,
                     lambda: control.stop_candidate(ident)):
            with self.assertRaises(BackendUnavailable):
                call()

    def test_missing_budgets_refuse(self):
        outcome = self.make(budgets=handoff.Budgets()).run(self.request())
        self.assertEqual(outcome.state, handoff.State.REFUSED)
        bad = handoff.Budgets(stop=50, ready=50, candidate_stop=5, restore=20, overall=60)
        self.assertIn('budget:sum-exceeds-overall', self.make(budgets=bad).run(self.request()).reasons)

    def test_happy_path_restores_once(self):
        outcome = self.make(abort_at=8).run(self.request())
        self.assertEqual(outcome.state, handoff.State.FINISHED, outcome.trace)
        self.assertEqual(outcome.reasons, ['preview-stop', 'stock-restored'])
        self.assertEqual(self.control.calls, ['request_stock_stop', 'launch_candidate', 'stop_candidate',
                                              'launch_stock'])
        self.assertEqual([p.exe_md5 for p in self.natives()], [SRC_MD5])
        self.assertTrue(self.store.claimed(ATT1))

    def test_request_binding_refusals(self):
        cases = {'stale-boot': self.request(boot_id=BOOTS[1]),
                 'candidate-mismatch': self.request(candidate_md5='d' * 32),
                 'arm-missing-or-different': self.request(attempt=ATT2)}
        for reason, request in cases.items():
            with self.subTest(reason):
                outcome = self.make().run(request)
                self.assertEqual(outcome.state, handoff.State.REFUSED)
                self.assertIn(reason, outcome.reasons)
                self.assertEqual(self.control.calls, [])
        outcome = self.make(lease=False).run(self.request())
        self.assertIn('lease-invalid', outcome.reasons)

    def test_nonce_and_attempt_replay(self):
        machine = self.make(abort_at=8)
        machine.run(self.request())
        again = handoff.Handoff(store=self.store, view=self.world, clock=self.clock, boot_id=BOOTS[0],
                                source_md5=SRC_MD5, candidate_md5=CAND_MD5, lease_valid=lambda o, g: True,
                                abort_requested=lambda: None, control=self.control, contract=OPEN_CONTRACT,
                                budgets=BUDGETS)
        outcome = again.run(self.request())
        self.assertEqual(outcome.state, handoff.State.REFUSED)
        self.assertIn('nonce-replayed', outcome.reasons)
        self.assertIn('attempt-replayed', outcome.reasons)

    def test_claim_durability_failures_never_stop_stock(self):
        for fault in ('write_fails', 'partial_write', 'readback_mismatch'):
            with self.subTest(fault):
                store = FakeAttemptStore({'attempt': ATT1, 'candidate_md5': CAND_MD5}, **{fault: True})
                outcome = self.make(store=store).run(self.request())
                self.assertEqual(outcome.reasons, ['claim-not-durable'])
                self.assertEqual(self.control.calls, [])
                self.assertEqual(len(self.natives()), 1)
                if fault != 'write_fails':
                    self.assertTrue(store.claimed(ATT1), 'ambiguous claim must consume the attempt')

    def test_power_loss_after_claim_means_disarmed_next_boot(self):
        machine = self.make(stock_never_exits=True)
        machine.run(self.request())
        self.store.reboot()
        outcome = self.make(store=self.store).run(self.request(nonce='n-2'))
        self.assertEqual(outcome.state, handoff.State.REFUSED)
        self.assertIn('arm-missing-or-different', outcome.reasons)

    def test_ownership_ambiguity_refuses(self):
        cases = {
            'competing-native-apps': [stock_proc(), stock_proc(pid=301, starttime=1001)],
            'competing-resource-owner': [stock_proc(), handoff.Proc(77, 5, 'e' * 32, 'probe',
                                                                    frozenset({'/dev/video0'}))],
            'unexpected-executable': [handoff.Proc(300, 1000, 'd' * 32, 'sdvcam')],
            'no-native-app': [],
        }
        for reason, procs in cases.items():
            with self.subTest(reason):
                outcome = self.make(world=FakeWorld(list(procs))).run(self.request())
                self.assertEqual(outcome.state, handoff.State.REFUSED)
                self.assertIn(reason, outcome.reasons)
                self.assertEqual(self.store.claims, {})

    def test_stock_stop_failures_never_launch_candidate(self):
        for behaviour, reason in (('stock_never_exits', 'stock-stop-timeout'),
                                  ('stock_respawns', 'native-app-present-after-stop'),
                                  ('resource_leak', 'stock-stop-timeout'),
                                  ('no_release_ack', 'stock-stop-timeout')):
            with self.subTest(behaviour):
                outcome = self.make(**{behaviour: True}).run(self.request())
                self.assertEqual(outcome.state, handoff.State.RECOVERY_REQUIRED)
                self.assertIn(reason, outcome.reasons)
                self.assertNotIn('launch_candidate', self.control.calls)
                self.assertLessEqual(len(self.natives()), 1)

    def test_pid_reuse_is_not_mistaken_for_stock(self):
        self.assertFalse(handoff.alive(FakeWorld([handoff.Proc(300, 1777, SRC_MD5, 'sdvcam')]),
                                       handoff.Identity(300, 1000, SRC_MD5)))
        outcome = self.make(pid_reused=True, abort_at=8).run(self.request())
        self.assertEqual(outcome.state, handoff.State.FINISHED, outcome.trace)

    def test_candidate_failures_restore_exactly_once(self):
        for behaviour, reason in (('candidate_crashes', 'candidate-exited'),
                                  ('candidate_never_ready', 'candidate-ready-timeout'),
                                  ('wrong_candidate', 'candidate-identity-mismatch')):
            with self.subTest(behaviour):
                outcome = self.make(**{behaviour: True}).run(self.request())
                self.assertEqual(outcome.state, handoff.State.FINISHED, outcome.trace)
                self.assertEqual(outcome.reasons[0], reason)
                self.assertEqual(self.control.calls.count('launch_stock'), 1)

    def test_stuck_candidate_never_starts_another_app(self):
        outcome = self.make(candidate_stuck=True, abort_at=8).run(self.request())
        self.assertEqual(outcome.state, handoff.State.RECOVERY_REQUIRED)
        self.assertIn('candidate-release-unconfirmed', outcome.reasons)
        self.assertNotIn('launch_stock', self.control.calls)
        self.assertFalse(outcome.restore_attempted)

    def test_restore_failure_is_recovery_required_without_retry(self):
        outcome = self.make(restore_never_ready=True, abort_at=8).run(self.request())
        self.assertEqual(outcome.state, handoff.State.RECOVERY_REQUIRED)
        self.assertIn('restore-timeout', outcome.reasons)
        self.assertEqual(self.control.calls.count('launch_stock'), 1)

    def test_abort_reasons_and_overall_deadline(self):
        for reason in ('lease-expired', 'controller-lost'):
            with self.subTest(reason):
                outcome = self.make(abort_at=4, abort_reason=reason).run(self.request())
                self.assertEqual(outcome.reasons[0], reason)
        outcome = self.make().run(self.request())
        self.assertEqual(outcome.reasons[0], 'overall-deadline')
        self.assertLessEqual(self.clock.now(), BUDGETS.overall + BUDGETS.candidate_stop + BUDGETS.restore + 1)

    def test_verify_selected_boot(self):
        cand = handoff.Proc(500, 2000, CAND_MD5, 'sdvcam', frozenset({'/dev/watchdog', '/dev/video0'}))
        status = {'decision': 'candidate', 'reason': 'selected', 'boot': BOOTS[0]}
        ok = handoff.verify_selected_boot(FakeWorld([cand]), status, BOOTS[0], CAND_MD5, SRC_MD5, True)
        self.assertTrue(ok['ok'], ok['reasons'])
        self.assertEqual(ok['evidence_class'], 'fake')
        bad_cases = {
            'status-from-another-boot': (FakeWorld([cand]), dict(status, boot=BOOTS[1]), True),
            'selector-did-not-select': (FakeWorld([cand]), dict(status, decision='stock'), True),
            'competing-native-apps': (FakeWorld([cand, stock_proc()]), status, True),
            'watchdog-not-held-by-selected-app': (FakeWorld([handoff.Proc(500, 2000, CAND_MD5, 'sdvcam')]), status, True),
            'bridge-library-not-mapped': (FakeWorld([cand]), status, False),
        }
        for reason, (world, st, lib) in bad_cases.items():
            with self.subTest(reason):
                report = handoff.verify_selected_boot(world, st, BOOTS[0], CAND_MD5, SRC_MD5, lib)
                self.assertFalse(report['ok'])
                self.assertIn(reason, report['reasons'])


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        done = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/native_trial'), *args],
                              capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout, done.stderr

    def test_status_reports_unavailable_backends(self):
        code, out, _ = self.run_cli()
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertFalse(data['hardware_performed'])
        self.assertIn('unavailable', data['native_control_backend'])

    def test_no_mutating_commands_exist(self):
        for command in ('install', 'select', 'stop', 'launch', 'arm-card', 'deploy'):
            with self.subTest(command):
                code, _, _ = self.run_cli(command)
                self.assertEqual(code, 2)

    def test_simulate_is_fake_and_one_shot(self):
        code, out, _ = self.run_cli('simulate')
        data = json.loads(out)
        self.assertEqual([b['reason'] for b in data['runs']['shipped_gate_blocked']], ['select-blocked'])
        self.assertEqual([b['reason'] for b in data['runs']['design_review_gate_open']],
                         ['selected', 'post-attempt-boot', 'attempt-replayed'])
        self.assertIn('FAKE', data['evidence_class'])
        self.assertIn('activation ineligible', data['activation'])

    def test_gates_all_open(self):
        data = json.loads(self.run_cli('gates')[1])
        self.assertFalse(data['hardware_performed'])
        # No gate is closed by local work; E3 holds partial late-boot facts only.
        self.assertTrue(all(g['status'] in ('open', 'blocked', 'partial') for g in data['early_selector_gates']))
        gates = {g['id']: g['status'] for g in data['early_selector_gates']}
        self.assertEqual((gates['EA'], gates['E3']), ('blocked', 'partial'))

    def test_placeholder_patch_is_inert_if_installed(self):
        with tempfile.TemporaryDirectory(prefix='cammy-wp4-patch-') as temporary:
            out = Path(temporary) / 'p' / 'bootstrap.patch'
            code, stdout, _ = self.run_cli('proposal-patch', '--out', str(out))
            self.assertEqual(code, 0, stdout)
            text = out.read_text()
            self.assertIn('new file mode 100755', text)
            self.assertIn('+mode=observe', text)
            self.assertIn('PENDING-GATE-E1', text)
            allow_lines = text.split('+++ b/etc/cammy/native-select.allow\n', 1)[1].split('\n', 1)[1]
            placeholder = ''.join(line[1:] + '\n' for line in allow_lines.splitlines() if line.startswith('+'))
            self.assertIn('SELECT_GATE=blocked', text)
            self.assertIn('BLOCKED', text.split('diff --git', 1)[0])
            tree = FakeTree.build(Path(temporary) / 'root', boot_id=BOOTS[0], source=SOURCE, s01app=S01APP,
                                  allow=placeholder.encode(), card=card(), mode='observe')
            self.assertEqual(run_selector(TreeEnv(tree)).reason, 'allowlist-invalid')
            tree.path('/etc/cammy/native-select.enabled').write_text('mode=select\n')
            self.assertEqual(run_selector(TreeEnv(tree)).reason, 'select-blocked')
            self.assertFalse(tree.state_dir.exists())
            code, _, _ = self.run_cli('proposal-patch', '--out', str(ROOT / 'docs' / 'x.patch'))
            self.assertEqual(code, 3)
            self.assertFalse((ROOT / 'docs' / 'x.patch').exists())


# ---------------------------------------------------------------------------
# WP5 review regressions: blocked gate, fail-closed primitives, hidden mount
# flags, late invocation, exec-bit commit and what stock S01app does afterwards.
# ---------------------------------------------------------------------------
from native_trial import s01app_model  # noqa: E402

LIVE_INIT_PRIVATE = ROOT / 'private/native-start/live-init-scripts.txt'


def s01_effects(tree, candidate_md5=None):
    app = tree.path('/usr/bin/sdvcam')
    return s01app_model.effects(tree.mounts(), app_executable=os.access(app, os.X_OK),
                                app_md5=md5(app.read_bytes()), stock_md5=md5(SOURCE),
                                verified_candidate_md5=candidate_md5, root=str(tree.root))


@unittest.skipUnless(DASH, 'dash is required to run the relocated selector proposal')
class ReviewFixTests(unittest.TestCase):
    """Shell and model still agree; each case pins one WP5 finding."""

    check = SelectorEquivalenceTests.check

    def test_shipped_gate_blocks_select_before_any_action(self):
        fixture = Fixture(self)
        result, snap = self.check(fixture, 'stock', 'select-blocked', gate='blocked')
        self.assertEqual(snap['calls'], [])            # no mount, umount, sync or cp
        self.assertEqual(snap['state'], {})            # no durable write
        self.assertEqual(snap['run'], ['run/cammy-select', 'run/cammy-select/status'])
        self.assertIn('reason=select-blocked', snap['status'])
        text = PROPOSAL.read_text()
        self.assertEqual(text.count('\nSELECT_GATE=blocked\n'), 1)
        self.assertNotIn('SELECT_GATE=open', text)

    def test_observe_mode_still_runs_under_shipped_gate(self):
        result, snap = self.check(Fixture(self, mode='observe'), 'stock', 'observe-only', ATT1, gate='blocked')
        self.assertEqual(snap['state'], {})
        self.assertEqual(s01_effects(Fixture(self, mode='observe').shell)['branch'], 'launch-only')

    def test_missing_or_variant_primitive_is_stock(self):
        # Busybox applets/options are unverified on the camera (E3). A tail without -c
        # used to make record_ok's newline test pass on empty output; md5sum may be absent.
        for name, body, fault, reason in (('tail', 'exit 1', 'tools-broken', 'tools-unverified'),
                                          ('md5sum', 'exit 127', 'tools-broken', 'tools-unverified'),
                                          ('grep', 'exit 0', 'tools-broken', 'tools-unverified'),
                                          ('awk', 'exit 2', 'awk-broken', 'run-not-tmpfs')):
            with self.subTest(primitive=name):
                fixture = Fixture(self)
                stub = fixture.shell.ctl / 'bin' / name
                stub.write_text(f'#!/bin/sh\n{body}\n')
                stub.chmod(0o700)
                fixture.model.set_faults(fault)
                result, snap = self.check(fixture, 'stock', reason)
                self.assertEqual(snap['calls'], [])
                self.assertEqual(snap['state'], {})

    def test_hidden_noexec_and_stacked_mounts(self):
        cases = (
            (dict(extra_mounts=('tmpfs <R>/run tmpfs rw,nosuid,noexec 0 0',)), 'run-noexec'),
            (dict(extra_mounts=('tmpfs <R>/tmp tmpfs rw,noexec 0 0',)), 'lib-dir-noexec'),
            (dict(extra_mounts=('/dev/root <R>/tmp ext4 rw 0 0',)), 'lib-dir-not-tmpfs'),
            (dict(extra_mounts=('/dev/root <R>/run ext4 rw 0 0',)), 'run-not-tmpfs'),
        )
        for kwargs, reason in cases:
            with self.subTest(reason):
                result, snap = self.check(Fixture(self, **kwargs), 'stock', reason)
                self.assertEqual(snap['state'], {})

    def test_late_invocation_changes_nothing(self):
        fixture = Fixture(self)
        def running(tree):
            tree.path('/proc/412').mkdir()
            tree.path('/proc/412/comm').write_text('sdvcam\n')
            tree.path('/run/cammy-select').mkdir()
            tree.path('/run/cammy-select/status').write_text('decision=candidate\n')
        fixture.edit(running)
        result, snap = self.check(fixture, 'stock', 'app-already-running')
        self.assertEqual(snap['status'], 'decision=candidate\n')   # earlier evidence kept
        self.assertEqual(snap['calls'], [])

    def test_exec_bit_commit_happy_path(self):
        fixture = Fixture(self)
        result, snap = self.check(fixture, 'candidate', 'selected', ATT1)
        app = fixture.shell.path('/usr/bin/sdvcam')
        self.assertEqual(app.stat().st_mode & 0o777, 0o555)
        _, env = Fixture(self).run_model()
        ops = env.ops
        self.assertEqual(ops[ops.index('mount', ops.index('create')) - 2:][:4], ['chmod', 'chmod', 'mount', 'chmod'])
        effects = s01_effects(fixture.shell, md5(CANDIDATE))
        self.assertEqual((effects['branch'], effects['runs']), ('launch-only', 'verified-candidate'))

    def test_unrestorable_bind_is_left_non_executable(self):
        cases = (
            (('bind-noexec',), 'stock', 'bind-verify-failed', 'stock'),
            (('bind-wrong-content', 'app-umount-fail'), 'recovery-required', 'bind-unrestorable',
             'none (exec fails; no app until power cycle)'),
            (('bind-foreign-exec', 'app-umount-fail'), 'recovery-required', 'bind-unrestorable-executable',
             'UNVERIFIED'),
        )
        for faults, decision, reason, runs in cases:
            with self.subTest(faults=faults):
                fixture = Fixture(self)
                fixture.each('set_faults', *faults)
                code, out, err = fixture.run_shell()
                result, _ = fixture.run_model()
                self.assertEqual(out, result.line(), err)
                self.assertEqual((result.decision, result.reason), (decision, reason))
                self.assertEqual(fixture.shell.snapshot(), fixture.model.snapshot())
                self.assertEqual(s01_effects(fixture.shell, md5(CANDIDATE))['runs'], runs)

    def test_tf_detach_failure_triggers_s01app_backup_branch(self):
        # Finding F2: recovery-required exits 0, S01app still runs, and the private
        # vfat mount sends it down the backup branch (durable internal writes).
        fixture = Fixture(self)
        fixture.each('set_faults', 'tf-umount-fail', 'tf-umount-lazy-fail')
        code, out, err = fixture.run_shell()
        model, _ = fixture.run_model()
        self.assertEqual(out, model.line(), err)
        self.assertEqual((model.decision, model.reason), ('recovery-required', 'tf-detach-failed'))
        effects = s01_effects(fixture.shell, md5(CANDIDATE))
        self.assertEqual(effects['branch'], 'backup')
        self.assertEqual(effects['runs'], 'stock')
        self.assertFalse(effects['no_durable_side_effect'])
        self.assertIn('/mnt/extsd/.sdvcam -> internal-overlay (if du <= df free)', effects['durable_writes'])

    def test_every_selector_outcome_against_s01app(self):
        """Sweep: only two outcomes break fail-closed; both are why activation is blocked."""
        scenarios = [((), {}), (('bind-fail',), {}), (('bind-wrong-content',), {}), (('bind-noexec',), {}),
                     (('bind-wrong-content', 'app-umount-fail'), {}), (('bind-foreign-exec', 'app-umount-fail'), {}),
                     (('tf-umount-fail',), {}), (('tf-umount-fail', 'tf-umount-lazy-fail'), {}),
                     (('tf-mount-fail',), {}), (('copy-fail',), {}), ((), {'card': None}),
                     ((), {'mode': 'observe'})]
        unsafe = {}
        for faults, kwargs in scenarios:
            tree = FakeTree.build(Path(tempfile.mkdtemp(prefix='cammy-wp5-sweep-')), boot_id=BOOTS[0],
                                  source=SOURCE, s01app=S01APP, allow=allow(),
                                  **{'card': card(), **kwargs})
            self.addCleanup(shutil.rmtree, tree.root, True)
            tree.set_faults(*faults)
            result = run_selector(TreeEnv(tree), select_gate='open')
            effects = s01_effects(tree, md5(CANDIDATE) if result.decision == 'candidate' else None)
            if not (effects['fail_closed'] and effects['no_durable_side_effect']):
                unsafe[result.reason] = effects['runs']
        self.assertEqual(unsafe, {'tf-detach-failed': 'stock', 'bind-unrestorable-executable': 'UNVERIFIED'})

    @unittest.skipUnless(LIVE_INIT_PRIVATE.is_file(), 'private live init text not present')
    def test_s01app_model_anchors_in_private_text(self):
        text = LIVE_INIT_PRIVATE.read_text(errors='replace')
        start = text[text.index('ulimit -c unlimited'):]
        self.assertEqual(text.count('/usr/bin/sdvcam &'), 5)
        self.assertEqual(text.count('> /data/app_md5'), 2)
        self.assertEqual(text.count('cp /usr/bin/sdvcam /mnt/extsd/.sdvcam'), 2)
        live = [line for line in start.splitlines() if "grep '\\<vfat\\>' /proc/mounts" in line]
        self.assertTrue(any(not line.lstrip().startswith('#') for line in live))
        self.assertIn('/mnt/extsd/core.%e.%p', text)


# ---------------------------------------------------------------------------
# WP2A integration: reviewed structural constants and the real offline artifacts
# (read-only; skipped when the private copies are absent).
# ---------------------------------------------------------------------------
WP2A_TOOL = ROOT / 'tools/build_native_status_candidate.py'
WP2A_DIR = ROOT / 'private/ownership-program/wp2a'
WP2A_CANDIDATE = WP2A_DIR / 'candidate/sdvcam-status-db34fa035a26'
WP2A_LIBRARY = WP2A_DIR / 'library/libcammy_native_status.so'
WP5_REVIEW = ROOT / 'private/ownership-program/wp5/real-review/review.json'


class Wp2aIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(WP2A_TOOL.is_file(), 'WP2A generator not present')
    def test_policy_matches_reviewed_wp2a_constants(self):
        text = WP2A_TOOL.read_text()
        def const(name):
            return re.search(rf'^{name} = (.+?)(?:\s+#.*)?$', text, re.M).group(1).strip()
        base = int(const('TEXT_BASE'), 16)
        self.assertEqual(base, policy.TEXT_VA_BIAS)
        self.assertEqual(int(const('STUB_ADDRESS'), 16) - base, policy.STATUS_STUB_START)
        self.assertEqual(int(const('LAST_TEXT_PAGE_END'), 16), policy.LAST_TEXT_PAGE_END)
        self.assertEqual(int(const('ORIGINAL_LOAD_SIZE'), 16), policy.STOCK_TEXT_LOAD_SIZE)
        self.assertEqual(int(const('CALL_SITE'), 16) - base, policy.STATUS_BL_SITE[0])
        lo, hi = (int(x, 16) - base for x in re.findall(r'0x[0-9A-Fa-f]+', const('MEDIA_CANDIDATE_RANGE')))
        self.assertEqual((lo, hi), policy.MEDIA_CAVE_RANGE)
        self.assertEqual(eval(const('LIB_DIR')), R.LIBRARY_ROOT)
        self.assertEqual(eval(const('LIB_NAME')), R.LIBRARY_NAME)

    def test_generator_refuses_non_stock_source(self):
        with tempfile.TemporaryDirectory(prefix='cammy-wp5-gen-') as temporary:
            source = Path(temporary) / 'source'
            source.write_bytes(b'not stock' * 100)
            result = bundle.reproduce_with_generator(source, 'a' * 64)
        if result['available']:
            self.assertIsNone(result['expected_sha256'])
            self.assertTrue(result['error'])

    @unittest.skipUnless(STOCK_PRIVATE.is_file() and WP2A_CANDIDATE.is_file() and WP2A_LIBRARY.is_file(),
                         'private WP2A artifacts not present')
    def test_real_wp2a_candidate_is_structural_only_without_review(self):
        report = bundle.classify(WP2A_CANDIDATE, STOCK_PRIVATE, R.CLASS_STATUS_ONLY, WP2A_LIBRARY, reproduce=True)
        self.assertEqual(report['candidate_sha256'],
                         'a9dedc985cc79d933601390df36b4458784e8dd4807b867cd53cfe0a8a9caba0')
        self.assertEqual(report['library']['sha256'],
                         'db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065')
        self.assertTrue(report['structural_ok'], report['reasons'])
        self.assertTrue(report['reproduction']['identical'])
        self.assertEqual(report['embedded_library_paths'], [report['library']['ram_path']])
        self.assertEqual(report['connect_trigger_sites_modified'], [])
        self.assertEqual((report['trust'], report['eligible'], report['activation_eligible']),
                         ('structural-only', False, False))

    @unittest.skipUnless(STOCK_PRIVATE.is_file() and WP2A_CANDIDATE.is_file() and WP5_REVIEW.is_file(),
                         'private WP2A artifacts or WP5 review record not present')
    def test_real_wp2a_candidate_with_offline_review_is_local_only(self):
        report = bundle.classify(WP2A_CANDIDATE, STOCK_PRIVATE, R.CLASS_STATUS_ONLY, WP2A_LIBRARY,
                                 WP5_REVIEW, WP2A_DIR, reproduce=True)
        self.assertTrue(report['eligible_for_staging'], report['reasons'])
        self.assertEqual(report['review']['evidence_class'], 'offline')
        self.assertTrue(report['local_only'])
        self.assertFalse(report['eligible'])
        self.assertEqual(report['e1'], 'open')
        self.assertFalse(report['activation_eligible'])
