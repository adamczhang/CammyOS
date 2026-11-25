"""Checked lifecycle and Brain routing with a simulated authorized USB device."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))

from c3 import os_router
from tools import cammy_lifecycle as L
from tools import lifecycle_usb
from tests.test_rc_install import FakeCamera, build_candidate, BOOT, CREDS, NEW, OLD, OLD_FILES

ARTIFACT = 'candidate-0.7.9-test'
FIRST = L.Request('first-device', ARTIFACT)
KEY = 'a separate test passphrase'
STOCK_APP = b'stock application fixture'
STOCK_INIT = b'stock init fixture'


class UsbFixture(FakeCamera):
    def __init__(self):
        super().__init__({L.APP: STOCK_APP, L.TARGETS[0]: STOCK_INIT},
                         {'/etc', '/etc/init.d', '/mnt/extsd'})
        self.card = '1' * 32
        self.approved_tf = None

    def identity(self):
        return {'model': 'AT-M105 / V853', 'serial': '20080411', 'usb_port': '1-3',
                'transport': '11', 'boot_id': self.boot}

    def assert_boot(self):
        pass

    def tf_mount(self):
        return '/dev/mmcblk0'

    def tf_identity(self):
        return self.tf_mount(), self.card

    def read(self, path, *, limit=None):
        return super().read(path)

    def hashes(self, base, names, _):
        return {name: L.rc.sha(self.files[base + '/' + name])
                for name in names if base + '/' + name in self.files}

    def push_file(self, local, path):
        self.write(path, local.read_bytes(), '0600')

    def rename_new(self, source, dest):
        self._parent(dest)
        self._step('rename-new', source, dest)
        for old in [path for path in self.dirs if path == source or path.startswith(source + '/')]:
            self.dirs.remove(old)
            self.dirs.add(dest + old[len(source):])
        for old in [path for path in self.files if path.startswith(source + '/')]:
            self.files[dest + old[len(source):]] = self.files.pop(old)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(folder) / 'rc06'
        self.root.mkdir()
        self.artifact = self.root / ARTIFACT
        self.artifact.mkdir(mode=0o700)
        self.tf, self.new = build_candidate(self.artifact)
        manifest = self.artifact / 'candidate.json'
        files = {p.relative_to(self.artifact).as_posix(): {'sha256': L.rc.sha(p.read_bytes()), 'bytes': p.stat().st_size}
                 for p in self.artifact.rglob('*') if p.is_file() and p != manifest}
        manifest.write_text(json.dumps({'release_id': NEW, 'version': '0.7.9-test', 'files': files}))
        self.enterContext(patch.dict(L.REVIEWED, {NEW: L.rc.sha(manifest.read_bytes())}))
        self.enterContext(patch.multiple(L, STOCK_S01APP_SHA256=L.rc.sha(STOCK_INIT),
                                         STOCK_SDVCAM_SHA256=L.rc.sha(STOCK_APP)))
        self.enterContext(patch.object(L.rc, 'STOCK_S01APP_SHA256', L.rc.sha(STOCK_INIT)))
        self.cam = UsbFixture()
        self.lifecycle = L.Lifecycle(self.root, mount_check=lambda: None, camera=lambda: self.cam)
        self.lifecycle.enroll(KEY)

    def test_stock_install_exact_receipt_status_and_checked_rollback(self):
        proposal = self.lifecycle.prepare(FIRST)
        self.assertEqual(self.cam.mutations, [])
        self.assertEqual(proposal.identity['boot_id'], BOOT)
        result = self.lifecycle.apply(proposal, KEY)
        job = self.lifecycle.state / 'jobs' / result['job']
        self.assertEqual(self.cam.files[L.rc.SELECTOR], L.rc.SELECT)
        self.assertEqual(L._json(job / 'installed.json')['release'], NEW)
        self.assertEqual(L._json(job / 'after.json')['sha256'][L.rc.SELECTOR], L.rc.sha(L.rc.SELECT))
        self.assertEqual(L._json(job / 'checkpoint.json')['before_sha256'][L.TARGETS[0]], L.rc.sha(STOCK_INIT))
        self.assertTrue(all('/dev/' not in str(paths) for _, *paths in self.cam.mutations))
        self.assertTrue(all(path not in self.cam.files for path in CREDS))
        # The local receipt cannot stand in for a running report, or an on-card directory for a verified slot.
        report = self.lifecycle.status(usb=True, artifact=ARTIFACT)
        self.assertIsNone(report['running'])
        self.assertEqual(report['installed']['release'], NEW)
        self.assertEqual(report['on_tf']['state'], 'present, hashes unchecked')
        self.cam.files[L.rc.RUNTIME_CONF] = f'format=cammy-native-release/1\nrelease_id={NEW}\nversion=0.7.9\n'.encode()
        usb_running = self.lifecycle.status(usb=True)
        self.assertEqual(usb_running['running']['version'], '0.7.9')
        running = self.lifecycle.status(portal={'version': '0.7.9', 'boot_id': BOOT}, usb=True)
        self.assertEqual(running['running']['release'], NEW)
        self.assertEqual(running['running']['version'], '0.7.9')
        with self.assertRaisesRegex(L.Blocked, 'already been used'):
            self.lifecycle.apply(proposal, KEY)
        rollback = L.Request('rollback', ARTIFACT, result['job'])
        checked = self.lifecycle.prepare(rollback)
        self.lifecycle.apply(checked, KEY)
        self.assertEqual(self.cam.files[L.TARGETS[0]], STOCK_INIT)
        self.assertNotIn(L.TARGETS[1], self.cam.files)
        self.assertEqual(self.cam.files[L.rc.SELECTOR], L.rc.DISABLED)

    def test_update_requires_receipt_and_preserves_credentials(self):
        self.cam.files.update({**OLD_FILES, **CREDS})
        self.cam.dirs.add(L.rc.CFG)
        receipt = self.root / 'install-previous'
        receipt.mkdir(mode=0o700)
        L._publish(receipt / 'checkpoint.json', {'boot_id': BOOT, 'release': OLD})
        L._publish(receipt / 'installed.json', {'boot_id': BOOT, 'release': OLD,
                                               'transaction': '20261117-211816',
                                               'after_sha256': L._hashes(OLD_FILES)})
        request = L.Request('install', ARTIFACT, receipt.name)
        checked = self.lifecycle.prepare(request)
        self.lifecycle.apply(checked, KEY)
        for name, data in CREDS.items():
            self.assertEqual(self.cam.files[name], data)

    def test_survey_approves_pruning_and_rollback_uses_pc_copies_after_archive_is_pruned(self):
        legacy = L.rc.CFG + '/native-select.archived-retired'
        self.cam.dirs.update({L.rc.CFG, legacy})
        self.cam.files[legacy + '/startup.pending'] = b'old guard'
        original = self.lifecycle.prepare(FIRST)
        self.assertEqual(original.summary['prune']['obsolete'], {legacy: 'dir'})
        self.assertEqual(self.cam.mutations, [])
        self.cam.files[legacy + '/observed-old'] = b'archive member'
        # Approval covers the directory scheduled for archival. Each member
        # pulled at apply time must verify independently before deletion.
        installed = self.lifecycle.apply(original, KEY)
        self.assertNotIn(legacy, self.cam.dirs)
        self.assertTrue(list(self.root.glob('flash-archive-*/native-select.archived-retired/*/receipt.json')))
        job = self.lifecycle.state / 'jobs' / installed['job']
        journal = json.loads((job / 'transaction.json').read_text())
        self.cam.dirs.remove(journal['archive_dir'])
        report = self.lifecycle.status(usb=True)
        self.assertEqual(report['installed']['nor_rollback'], 'dir')
        rollback = L.Request('rollback', ARTIFACT, installed['job'])
        self.lifecycle.apply(self.lifecycle.prepare(rollback), KEY)
        self.assertEqual(self.cam.files[L.TARGETS[0]], STOCK_INIT)

    def test_repair_disallows_unknown_stock_changes_and_reuses_exact_release(self):
        installed = self.lifecycle.apply(self.lifecycle.prepare(FIRST), KEY)
        for target in L.TARGETS:
            self.cam.files.pop(target, None)
        self.cam.files[L.TARGETS[0]] = b'altered stock'
        request = L.Request('repair', ARTIFACT, installed['job'])
        with self.assertRaisesRegex(L.Blocked, 'do not match'):
            self.lifecycle.prepare(request)
        self.cam.files[L.TARGETS[0]] = STOCK_INIT
        # Distinct legacy archive names are generated with second resolution.
        with patch.object(L.rc.time, 'strftime', side_effect=lambda pattern: '20261118-030542'
                   if pattern == '%Y%m%d-%H%M%S' else '2026-11-18T03:05:42+0000'):
            result = self.lifecycle.apply(self.lifecycle.prepare(request), KEY)
        self.assertEqual(result['action'], 'repair')

    def test_guard_authority_transport_card_and_local_approval(self):
        self.cam.files[L.rc.PENDING] = b'private marker fixture'
        with self.assertRaisesRegex(L.Blocked, 'guard'):
            self.lifecycle.prepare(FIRST)
        del self.cam.files[L.rc.PENDING]
        for name in ('native-select.resident', 'native-select.crash-used'):
            auth = L.rc.CFG + '/' + name
            self.cam.files[auth] = b'authority marker'
            with self.assertRaisesRegex(L.Blocked, 'authority'):
                self.lifecycle.prepare(FIRST)
            del self.cam.files[auth]
        self.assertEqual(self.cam.mutations, [])
        proposal = self.lifecycle.prepare(FIRST)
        with self.assertRaisesRegex(L.Blocked, 'expired'):
            self.lifecycle.apply(replace(proposal, expires=0), KEY)
        with self.assertRaisesRegex(L.Blocked, 'approval failed'):
            self.lifecycle.apply(proposal, 'wrong')
        self.cam.card = '2' * 32
        with self.assertRaisesRegex(L.Blocked, 'changed after approval'):
            self.lifecycle.apply(proposal, KEY)
        self.assertEqual(self.cam.mutations, [])
        proposal = self.lifecycle.prepare(FIRST)
        self.cam.boot = '01111111-2222-4333-8444-555555555555'
        with self.assertRaisesRegex(L.Blocked, 'changed after approval'):
            self.lifecycle.apply(proposal, KEY)
        self.assertEqual(self.cam.mutations, [])

    def test_fault_during_staging_stops_before_boot_writes(self):
        proposal = self.lifecycle.prepare(FIRST)
        rename = self.cam.rename_new

        def inject(source, dest):
            rename(source, dest)
            self.cam.files[L.rc.CFG + '/runtime-health-fault'] = b'test fault'

        with patch.object(self.cam, 'rename_new', side_effect=inject):
            with self.assertRaises(L.Blocked):
                self.lifecycle.apply(proposal, KEY)
        self.assertTrue(all(not path.startswith('/etc/') for _, *paths in self.cam.mutations for path in paths))
        self.assertEqual(self.cam.files[L.TARGETS[0]], STOCK_INIT)

    def test_interrupted_boot_write_can_rollback_from_checked_journal(self):
        self.cam.corrupt_write = lambda path, raw: (b'partial' if path == L.TARGETS[0] + '.rc-new' else raw)
        proposal = self.lifecycle.prepare(FIRST)
        with self.assertRaisesRegex(L.Blocked, 'Inspect job-'):
            self.lifecycle.apply(proposal, KEY)
        self.assertEqual(self.cam.files[L.rc.SELECTOR], L.rc.DISABLED)
        self.cam.corrupt_write = None
        request = L.Request('rollback', ARTIFACT, 'job-' + proposal.nonce)
        self.lifecycle.apply(self.lifecycle.prepare(request), KEY)
        self.assertEqual(self.cam.files[L.TARGETS[0]], STOCK_INIT)
        self.assertNotIn(L.TARGETS[0] + '.rc-new', self.cam.files)

    def test_artifact_drift_and_symlink_rejected_before_camera_io(self):
        proposal = self.lifecycle.prepare(FIRST)
        file = self.artifact / 'bootstrap/S01app'
        raw = file.read_bytes()
        file.write_bytes(raw + b'# new\n')
        with self.assertRaisesRegex(L.Blocked, 'bounded manifest'):
            self.lifecycle.apply(proposal, KEY)
        self.assertEqual(self.cam.mutations, [])
        other = self.root / 'other'
        other.write_bytes(raw)
        file.unlink()
        file.symlink_to(other)
        with self.assertRaises(OSError):
            self.lifecycle.review(ARTIFACT)

    def test_status_and_brain_request_without_usb_and_serialized_apply(self):
        with patch.object(self.lifecycle, 'camera', side_effect=L.Blocked('USB unavailable')) as camera:
            self.assertIsNone(self.lifecycle.status()['usb'])
            queued = self.lifecycle.queue(FIRST)
            self.assertEqual(queued['release'], NEW)
            self.assertEqual(self.lifecycle.queued(), FIRST)
            camera.assert_not_called()
            observed = self.lifecycle.status(usb=True)
            self.assertEqual(observed['usb']['detail'], 'USB unavailable')
            self.assertIsNone(observed['installed'])
        proposal = self.lifecycle.prepare(FIRST)
        with self.lifecycle._lock(), self.assertRaisesRegex(L.Blocked, 'already running'):
            self.lifecycle.apply(proposal, KEY)
        self.assertEqual(self.cam.mutations, [])


class UsbTransportTests(unittest.TestCase):
    def test_only_one_authorized_exact_usb_transport(self):
        with patch.object(lifecycle_usb, '_inventory', return_value=[
                '20080411 device usb:1-3.1 transport_id:9']):
            self.assertEqual(lifecycle_usb.UsbCamera._transport(), ('20080411', '1-3.1', '9'))
        for inventory in [
            ['20080411 unauthorized usb:1-3.1 transport_id:9'],
            ['20080411 device transport_id:9'],
            ['20080411 device usb:1-3.1 transport_id:9', 'other device usb:1-2 transport_id:10'],
            ['20080411'],
        ]:
            with patch.object(lifecycle_usb, '_inventory', return_value=inventory):
                with self.assertRaises(L.Blocked):
                    lifecycle_usb.UsbCamera._transport()


    def test_tf_verification_rejects_extra_enable_file_without_reading_it(self):
        cam = lifecycle_usb.UsbCamera.__new__(lifecycle_usb.UsbCamera)
        base = '/mnt/extsd/CammyOS-releases/' + NEW
        members = L.rc.rc_wifi_policy.TF_MEMBERS
        folders = {folder: [name.split('/', 1)[1] for name in members if name.startswith(folder + '/')]
                   for folder in ('native', 'runtime')}
        folders['runtime'].append('enable-diag')
        with patch.object(cam, 'listdir', side_effect=lambda path: ['native', 'runtime'] if path == base
                          else folders[path.rsplit('/', 1)[1]]), patch.object(cam, 'read') as read:
            with self.assertRaisesRegex(L.Blocked, 'additional files'):
                cam.hashes(base, sorted(members), None)
            read.assert_not_called()


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_local_only_and_calendar_composition(self):
        mode = SimpleNamespace(adult=False, generation=1, allows=lambda _: mode.adult)
        profile = SimpleNamespace(key=('brain', 1))
        c3 = SimpleNamespace(family=mode, selected_profile=lambda: profile.key,
                             _calendar_profile_key=lambda: profile.key,
                             read_device_state=AsyncMock(return_value={'version': '0.7.11', 'boot_id': BOOT}))
        lifecycle = SimpleNamespace(status=lambda *, portal: {'running': L.portal_report(portal)},
                                    review_request=lambda _: {'version': '0.7.9', 'release': NEW},
                                    queue=lambda _, allowed: {'release': NEW} if allowed() else None)
        router = os_router.OsRouter(c3, lifecycle)
        calendar = SimpleNamespace(route=AsyncMock(side_effect=[None, {'capability': 'calendar'}]))
        compose = os_router.compose
        self.assertIsNone(await compose(router, calendar, 'Please explain firmware updates'))
        self.assertEqual(await compose(router, calendar, 'Upcoming schedule?'), {'capability': 'calendar'})
        blocked = await compose(router, calendar, '/os status')
        self.assertEqual(blocked['status'], 'blocked')
        c3.read_device_state.assert_not_awaited()
        mode.adult = True
        self.assertIn('0.7.11', (await router.route('/os status'))['message'])
        self.assertEqual((await router.route('/os status', speak=True))['status'], 'blocked')
        self.assertEqual((await router.route('/os apply first-device ' + ARTIFACT))['status'], 'blocked')
        self.assertEqual((await router.route('/os request first-device ../../secret'))['status'], 'blocked')
        self.assertEqual((await router.route('/os prepare first-device ' + ARTIFACT))['status'], 'local')
        self.assertEqual((await router.route('/os request first-device ' + ARTIFACT))['status'], 'local')
        self.assertEqual(calendar.route.await_count, 2)

        async def switch_profile():
            await asyncio.sleep(0)
            profile.key = ('other', 2)
            return {'version': '0.7.11', 'boot_id': BOOT}

        c3.read_device_state.side_effect = switch_profile
        self.assertEqual((await router.route('/os status'))['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
