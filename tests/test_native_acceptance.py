"""WP5 stage-5 acceptance validator: FIXTURE reports only (no camera, card or device).

Each test starts from a consistent fixture report and changes one thing, then
checks the per-stage status and the overall verdict. The "hardware-labelled"
cases relabel fixtures only to exercise the validator's top verdict; they are
fabricated data, and the CLI caps any report outside private/ at LOCAL_ONLY.
"""
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from native_acceptance import cli, fixtures, spec, validate  # noqa: E402
from native_trial import policy  # noqa: E402

NOW = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cammy-wp5-acc-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def hardware(self, profile='status-only'):
        """Fixture relabelled as hardware with real artifact files under self.root."""
        raw = self.root / 'raw'
        raw.mkdir(exist_ok=True)
        report = fixtures.report(profile, fixture=False, hardware_class='hardware')
        for item in report['evidence']:
            for art in item['artifacts']:
                path = self.root / art['path']
                path.write_text(f'raw capture for {item["stage"]}\n')
                art['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        return report

    def run_validate(self, report, root=True, **kwargs):
        return validate.validate(report, now=NOW, evidence_root=self.root if root else None, **kwargs)

    def item(self, report, stage):
        return next(i for i in report['evidence'] if i['stage'] == stage)

    def assertVerdict(self, report, verdict, stage=None, status=None, **kwargs):
        result = self.run_validate(report, **kwargs)
        self.assertEqual(result['verdict'], verdict, (result['errors'], result['stages']))
        if stage:
            self.assertEqual(result['stages'][stage]['status'], status, result['stages'][stage])
        self.assertEqual(result['hardware_pass'], verdict == 'HARDWARE_EVIDENCE_COMPLETE')
        return result


class VerdictTests(Base):
    def test_fixture_reports_are_local_only(self):
        for profile in spec.PROFILES:
            with self.subTest(profile):
                result = self.run_validate(fixtures.report(profile), root=False,
                                           prerequisite={'hardware_pass': True, 'profile': 'status-only',
                                                         'report_sha256': '9' * 64})
                self.assertEqual(result['verdict'], 'LOCAL_ONLY')
                self.assertFalse(result['hardware_pass'])
                self.assertIn('none', result['approval'])
                self.assertTrue(all(s['status'] == 'PASS_LOCAL' for s in result['stages'].values()))

    def test_consistent_hardware_labels_reach_the_top_verdict_but_never_approval(self):
        result = self.assertVerdict(self.hardware(), 'HARDWARE_EVIDENCE_COMPLETE')
        self.assertTrue(result['approval'].startswith('none'))

    def test_simulated_or_relabelled_evidence_caps_at_local_only(self):
        cases = {
            'report fixture flag': lambda r: r.update(fixture=True),
            'simulated hardware stage': lambda r: self.item(r, 'selected-boot').update(evidence_class='simulated'),
            'host class on hardware stage': lambda r: self.item(r, 'stock-restore').update(evidence_class='host'),
            'fixture review': lambda r: self.item(r, 'abi-proved')['observations'].update(
                review_evidence_class='fixture'),
            'local-only bundle': lambda r: self.item(r, 'staged')['observations'].update(local_only=True),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                report = self.hardware()
                mutate(report)
                self.assertVerdict(report, 'LOCAL_ONLY')

    def test_artifacts_not_rehashed_are_local_only(self):
        self.assertVerdict(self.hardware(), 'LOCAL_ONLY', root=False)

    def test_local_duplicate_cannot_be_hidden_by_hardware_pass(self):
        for stage in ('selected-boot', 'abi-proved'):
            for local_first in (False, True):
                with self.subTest(stage=stage, local_first=local_first):
                    report = self.hardware()
                    local = copy.deepcopy(self.item(report, stage))
                    local['evidence_class'] = 'fixture'
                    report['evidence'].insert(0 if local_first else len(report['evidence']), local)
                    self.assertVerdict(report, 'LOCAL_ONLY', stage, 'PASS_LOCAL')

    def test_tampered_artifact_rejects(self):
        report = self.hardware()
        (self.root / 'raw/selected-boot.txt').write_text('edited later\n')
        self.assertVerdict(report, 'REJECTED', 'selected-boot', 'INVALID')

    def test_missing_stage_is_incomplete(self):
        for stage in fixtures.report()['evidence']:
            with self.subTest(stage['stage']):
                report = self.hardware()
                report['evidence'] = [i for i in report['evidence'] if i['stage'] != stage['stage']]
                self.assertVerdict(report, 'INCOMPLETE', stage['stage'], 'MISSING')

    def test_failed_or_unknown_evidence(self):
        report = self.hardware()
        failed = copy.deepcopy(self.item(report, 'selected-boot'))
        failed['result'] = 'fail'
        report['evidence'].append(failed)          # fail dominates an earlier pass
        self.assertVerdict(report, 'REJECTED', 'selected-boot', 'FAIL')
        report = self.hardware()
        self.item(report, 'cleanup')['result'] = 'unknown'
        self.assertVerdict(report, 'INCOMPLETE', 'cleanup', 'UNKNOWN')

    def test_stale_evidence(self):
        cases = {
            'before window': '2026-11-14T17:00:00Z',
            'in the future': '2026-11-15T09:00:00Z',
        }
        for label, when in cases.items():
            with self.subTest(label):
                report = self.hardware()
                self.item(report, 'abi-proved')['captured_utc'] = when
                self.assertVerdict(report, 'REJECTED', 'abi-proved', 'STALE')
        report = self.hardware()
        result = validate.validate(report, now=datetime(2026, 12, 20, tzinfo=timezone.utc), evidence_root=self.root)
        self.assertEqual(result['verdict'], 'REJECTED')
        report = self.hardware()
        report['window']['closed_utc'] = '2026-11-14T18:30:00Z'   # restore captured after close
        self.assertVerdict(report, 'REJECTED', 'stock-restore', 'STALE')

    def test_out_of_order_hardware_evidence(self):
        report = self.hardware()
        self.item(report, 'stock-restore')['captured_utc'] = '2026-11-14T18:10:00Z'
        result = self.assertVerdict(report, 'REJECTED')
        self.assertIn('stock-restore evidence predates selected-boot evidence', result['errors'])

    def test_unknown_keys_are_refused(self):
        report = self.hardware()
        report['approved'] = True
        self.assertVerdict(report, 'REJECTED')
        report = self.hardware()
        self.item(report, 'cleanup')['operator_says'] = 'fine'
        self.assertVerdict(report, 'REJECTED', 'cleanup', 'INVALID')


class IdentityAndBootTests(Base):
    def test_report_identities_must_be_stock_and_manager_path(self):
        cases = {
            'source not stock': lambda r: r['identities']['source'].update(sha256='0' * 64),
            's01app not stock': lambda r: r['identities']['s01app'].update(md5='0' * 32),
            'candidate equals stock': lambda r: r['identities']['candidate'].update(
                sha256=policy.STOCK_SDVCAM_SHA256),
            'old md5 hyphen library path': lambda r: r['identities']['library'].update(
                ram_path=f"/tmp/cammy-native-status/{fixtures.LIBRARY_MD5}/libcammy-native-status.so"),
            'boots not distinct': lambda r: r['boots'].update(card_removed=r['boots']['selected']),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                report = self.hardware()
                mutate(report)
                self.assertVerdict(report, 'REJECTED')

    def test_evidence_bound_to_other_bytes(self):
        report = self.hardware()
        self.item(report, 'cleanup')['binding']['library_sha256'] = 'c' * 64
        self.assertVerdict(report, 'REJECTED', 'cleanup', 'MISMATCH')

    def test_selected_app_identity_is_by_bytes_not_name(self):
        def obs(r):
            return self.item(r, 'selected-boot')['observations']
        cases = {
            'stock bytes under the same name': (lambda r: obs(r)['native_apps'][0].update(
                exe_sha256=policy.STOCK_SDVCAM_SHA256), 'MISMATCH'),
            'name/path only': (lambda r: obs(r)['native_apps'][0].pop('exe_sha256'), 'INVALID'),
            'two native apps': (lambda r: obs(r)['native_apps'].append(dict(obs(r)['native_apps'][0], pid=413)),
                                'INVALID'),
            'library not mapped in the app': (lambda r: obs(r)['library_mapping'].update(pid=999), 'INVALID'),
            'library bytes differ': (lambda r: obs(r)['library_mapping'].update(mapped_sha256='0' * 64), 'MISMATCH'),
            'watchdog held elsewhere': (lambda r: obs(r)['watchdog'].update(holder_pid=1), 'INVALID'),
            'watchdog window short': (lambda r: obs(r)['watchdog'].update(window_s=60), 'INVALID'),
            'watchdog reset': (lambda r: obs(r)['watchdog'].update(resets=1), 'INVALID'),
            'wifi not confirmed': (lambda r: obs(r)['network'].update(home_wifi_unchanged=None), 'INVALID'),
            'lcd not confirmed': (lambda r: obs(r)['lcd'].update(normal_view=False), 'INVALID'),
            'fake bridge backend': (lambda r: obs(r)['bridge'].update(backend_kind=1), 'INVALID'),
            'hardware actions enabled': (lambda r: obs(r)['bridge'].update(hardware_actions_enabled=True), 'INVALID'),
            'media without lease': (lambda r: obs(r)['media'].update(sessions_started=1), 'INVALID'),
            'attempt differs': (lambda r: obs(r)['selector_status'].update(attempt='b' * 32), 'MISMATCH'),
            'selector did not select': (lambda r: obs(r)['selector_status'].update(decision='stock'), 'INVALID'),
        }
        for label, (mutate, status) in cases.items():
            with self.subTest(label):
                report = self.hardware()
                mutate(report)
                self.assertVerdict(report, 'REJECTED', 'selected-boot', status)

    def test_wrong_boot(self):
        cases = {
            'selected item from restore boot': lambda r: self.item(r, 'selected-boot').update(
                boot_id=r['boots']['post_attempt']),
            'selector status from another boot': lambda r: self.item(r, 'selected-boot')['observations'][
                'selector_status'].update(boot=r['boots']['card_removed']),
            'bridge from another boot': lambda r: self.item(r, 'selected-boot')['observations']['bridge'].update(
                boot_id=r['boots']['post_attempt']),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                report = self.hardware()
                mutate(report)
                self.assertVerdict(report, 'REJECTED', 'selected-boot', 'WRONG_BOOT')

    def test_stock_restore_requirements(self):
        def obs(r):
            return self.item(r, 'stock-restore')['observations']
        cases = {
            'candidate still running after restore': (lambda r: obs(r)['post_attempt'].update(
                native_apps_exe_sha256=[fixtures.CANDIDATE]), 'MISMATCH'),
            'post-attempt boot selected': (lambda r: obs(r)['post_attempt']['selector_status'].update(
                decision='candidate'), 'INVALID'),
            'observed record of another boot': (lambda r: obs(r)['post_attempt'].update(
                observed_record_boot=r['boots']['selected']), 'WRONG_BOOT'),
            'card still present': (lambda r: obs(r)['card_removed'].update(card_present=True), 'INVALID'),
            'ack for another boot': (lambda r: obs(r)['ack'].update(observed_boot=r['boots']['selected']),
                                     'WRONG_BOOT'),
            'internal core dump left': (lambda r: obs(r).update(durable_side_effects=['/mnt/extsd/core.sdvcam.412']),
                                        'INVALID'),
            'unproven side effects': (lambda r: obs(r).pop('durable_side_effects'), 'INVALID'),
        }
        for label, (mutate, status) in cases.items():
            with self.subTest(label):
                report = self.hardware()
                mutate(report)
                self.assertVerdict(report, 'REJECTED', 'stock-restore', status)

    def test_structural_and_abi_stages_bind_the_build(self):
        report = self.hardware()
        self.item(report, 'structurally-classified')['observations']['connect_trigger_sites_modified'] = [
            'client connect -> stock media init']
        self.assertVerdict(report, 'REJECTED', 'structurally-classified', 'INVALID')
        report = self.hardware()
        self.item(report, 'abi-proved')['observations']['proofs']['device_abi'] = 'fail'
        self.assertVerdict(report, 'REJECTED', 'abi-proved', 'INVALID')
        report = self.hardware()
        self.item(report, 'staged')['observations']['installed'] = True
        self.assertVerdict(report, 'REJECTED', 'staged', 'INVALID')


class AuthorizationTests(Base):
    def test_selection_never_carries_media_authority(self):
        for key in ('lease', 'media_token', 'generation'):
            with self.subTest(key):
                report = self.hardware()
                report['selection_authorization'][key] = 'x'
                result = self.assertVerdict(report, 'REJECTED')
                self.assertTrue(any('never carries media authority' in e for e in result['errors']))

    def test_status_only_trial_has_no_lease_and_no_media(self):
        report = self.hardware()
        report['media_lease'] = fixtures.report('video')['media_lease']
        self.assertVerdict(report, 'REJECTED')
        report = self.hardware()
        video_item = self.item(self.hardware('video'), 'working-frame')
        report['evidence'].append(video_item)
        result = self.assertVerdict(report, 'REJECTED')
        self.assertTrue(any('status-only must have no media' in e for e in result['errors']))
        report = self.hardware()
        self.item(report, 'cleanup')['observations']['media_sessions'] = 1
        self.assertVerdict(report, 'REJECTED', 'cleanup', 'INVALID')


class VideoTests(Base):
    PRE = {'hardware_pass': True, 'profile': 'status-only', 'report_sha256': '9' * 64}

    def frame(self, report):
        return self.item(report, 'working-frame')['observations']

    def test_video_needs_verified_status_only_prerequisite(self):
        self.assertVerdict(self.hardware('video'), 'HARDWARE_EVIDENCE_COMPLETE', prerequisite=self.PRE)
        self.assertVerdict(self.hardware('video'), 'INCOMPLETE')
        self.assertVerdict(self.hardware('video'), 'INCOMPLETE', prerequisite=dict(self.PRE, report_sha256='8' * 64))
        self.assertVerdict(self.hardware('video'), 'INCOMPLETE', prerequisite=dict(self.PRE, hardware_pass=False))

    def test_current_boot_lease_is_required_for_frames(self):
        report = self.hardware('video')
        report['media_lease'] = None
        self.assertVerdict(report, 'REJECTED', 'working-frame', 'INVALID', prerequisite=self.PRE)
        report = self.hardware('video')
        report['media_lease']['boot_id'] = report['boots']['post_attempt']
        self.assertVerdict(report, 'REJECTED', 'working-frame', 'WRONG_BOOT', prerequisite=self.PRE)
        report = self.hardware('video')
        report['media_lease']['lease_ref'] = report['selection_authorization']['approval_ref']
        self.assertVerdict(report, 'REJECTED', prerequisite=self.PRE)
        report = self.hardware('video')
        self.frame(report)['lease_generation'] = 2              # stale generation
        self.assertVerdict(report, 'REJECTED', 'working-frame', 'MISMATCH', prerequisite=self.PRE)

    def test_frames_and_milestones(self):
        cases = {
            'frame readiness equals listener readiness': lambda o: o['milestones'].update(
                first_decoded_frame_ms=o['milestones']['sdp_ready_ms']),
            'unchanging target': lambda o: o['frames'][1].update(sha256=o['frames'][0]['sha256']),
            'one frame': lambda o: o.update(frames=o['frames'][:1]),
            'timestamps not increasing': lambda o: o['frames'][1].update(mono_ms=o['frames'][0]['mono_ms']),
            'frame after lease expiry': lambda o: o['frames'][1].update(mono_ms=170000),
            'stale latest frame': lambda o: o.update(latest_frame_age_ms=60000),
            'no dimensions': lambda o: o['frames'][0].update(width=0),
            'stock hash not retained': lambda o: o.update(source_sha256_unchanged=None),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                report = self.hardware('video')
                mutate(self.frame(report))
                self.assertVerdict(report, 'REJECTED', 'working-frame', 'INVALID', prerequisite=self.PRE)

    def test_stop_expiry_disconnect_cleanup(self):
        def cleanup(r):
            return self.item(r, 'cleanup')['observations']
        cases = {
            'stop ack only': lambda o: o['stop'].pop('confirmed_cleanup_ms'),
            'stop ack missing': lambda o: o['stop'].pop('ack_ms'),
            'expiry cleanup too slow': lambda o: o['expiry'].update(confirmed_cleanup_ms=o['expiry']['trigger_ms'] + 9000),
            'disconnect late command': lambda o: o['disconnect'].update(no_late_command=False),
            'encoder kept': lambda o: o['stop'].update(encoder_released=False),
            'orphan listener': lambda o: o.update(unexpected_listeners=1),
            'disarm unverified': lambda o: o['disarm'].update(verified=False),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                report = self.hardware('video')
                mutate(cleanup(report))
                self.assertVerdict(report, 'REJECTED', 'cleanup', 'INVALID', prerequisite=self.PRE)

    def test_video_build_must_be_lease_gated_with_lease_proof(self):
        report = self.hardware('video')
        self.item(report, 'structurally-classified')['observations']['structural_class'] = 'status-only'
        self.assertVerdict(report, 'REJECTED', 'structurally-classified', 'INVALID', prerequisite=self.PRE)
        report = self.hardware('video')
        del self.item(report, 'abi-proved')['observations']['proofs']['lease_enforcement']
        self.assertVerdict(report, 'REJECTED', 'abi-proved', 'INVALID', prerequisite=self.PRE)


class CancellationTests(Base):
    def test_cancel_before_hardware_is_clean(self):
        report = self.hardware()
        report['evidence'] = [i for i in report['evidence'] if spec.STAGE_KIND[i['stage']] == 'host']
        report['cancelled'] = {'at_utc': '2026-11-14T18:10:00Z', 'reason': 'gate EA open', 'after_stage': 'abi-proved'}
        self.assertVerdict(report, 'CANCELLED_CLEAN')

    def test_cancel_after_selected_boot_needs_cleanup_and_restore(self):
        report = self.hardware()
        report['evidence'] = [i for i in report['evidence'] if i['stage'] not in ('cleanup', 'stock-restore')]
        report['cancelled'] = {'at_utc': '2026-11-14T18:25:00Z', 'reason': 'LCD blank', 'after_stage': 'selected-boot'}
        self.assertVerdict(report, 'CANCELLED_CLEANUP_PENDING')
        report = self.hardware()
        report['cancelled'] = {'at_utc': '2026-11-14T18:25:00Z', 'reason': 'LCD blank', 'after_stage': 'selected-boot'}
        result = self.assertVerdict(report, 'CANCELLED_CLEAN')
        self.assertFalse(result['hardware_pass'])

    def test_evidence_after_cancellation_rejects(self):
        report = self.hardware()
        report['cancelled'] = {'at_utc': '2026-11-14T18:10:00Z', 'reason': 'abort', 'after_stage': 'abi-proved'}
        result = self.assertVerdict(report, 'REJECTED')
        self.assertIn('selected-boot evidence captured after cancellation', result['errors'])

    def test_malformed_cancellation(self):
        report = self.hardware()
        report['cancelled'] = {'at_utc': 'yesterday', 'reason': 'x', 'after_stage': 'abi-proved'}
        self.assertVerdict(report, 'REJECTED')


class CliTests(Base):
    def cli(self, *argv):
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_spec_is_machine_readable(self):
        code, out = self.cli('spec')
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([s['stage'] for s in data['stages']], spec.STAGE_ORDER)
        self.assertIn('none', data['approval'])
        self.assertIn('libcammy_native_status.so', data['identities']['library'])

    def test_no_hardware_or_mutating_commands(self):
        for command in ('install', 'arm', 'launch', 'select', 'capture', 'approve'):
            with self.subTest(command):
                with mock.patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as raised:
                    cli.main([command])
                self.assertEqual(raised.exception.code, 2)
        source = ''.join(p.read_text() for p in (ROOT / 'tools/native_acceptance').glob('*.py'))
        for forbidden in ('subprocess', 'socket', 'adb', 'ioctl', 'os.kill', 'os.system', 'urllib'):
            self.assertNotIn(forbidden, source)

    def test_template_validates_local_only_and_write_guard(self):
        out = self.root / 'tmpl.json'
        code, printed = self.cli('template', '--out', str(out))
        self.assertEqual(code, 0, printed)
        self.assertTrue(json.loads(out.read_text())['fixture'])
        code, printed = self.cli('validate', str(out), '--now', '2026-11-14T20:00:00Z')
        self.assertEqual((code, json.loads(printed)['verdict']), (4, 'LOCAL_ONLY'))
        code, _ = self.cli('template', '--out', str(ROOT / 'docs' / 'tmpl.json'))
        self.assertEqual(code, 3)
        self.assertFalse((ROOT / 'docs' / 'tmpl.json').exists())
        code, _ = self.cli('template', '--out', str(out))      # never overwrites
        self.assertEqual(code, 3)

    def test_cli_caps_reports_outside_private_evidence(self):
        report = self.hardware()
        path = self.root / 'looks-real.json'
        path.write_text(json.dumps(report))
        self.assertEqual(self.run_validate(report)['verdict'], 'HARDWARE_EVIDENCE_COMPLETE')
        code, printed = self.cli('validate', str(path), '--evidence-root', str(self.root),
                                 '--now', '2026-11-14T20:00:00Z')
        result = json.loads(printed)
        self.assertEqual((code, result['verdict'], result['hardware_pass']), (4, 'LOCAL_ONLY', False))
        self.assertIn('report is not in the retained private evidence tree', result['local_reasons'])


if __name__ == '__main__':
    unittest.main()
