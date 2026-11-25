"""Focused unit tests for Chunk 6 disarmed offline native-trial review package."""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.native_trial import policy as trial_policy
from tools.native_trial import records as R
from tools.native_trial import review as trial_review
from tools.trial_package import (
    CHECKLIST_FORMAT,
    DEFAULT_TEMPLATE_ATTEMPT,
    DEFAULT_TEMPLATE_OBSERVED_BOOT,
    DISABLED_MODE_TEMPLATE,
    EXPECTED_HELPER_PROPOSAL_SHA256,
    EXPECTED_REPLACEMENT_S01APP_LINES,
    EXPECTED_REPLACEMENT_S01APP_MD5,
    EXPECTED_REPLACEMENT_S01APP_SHA256,
    EXPECTED_REPLACEMENT_S01APP_SIZE,
    PACKAGE_FORMAT,
    PACKAGE_MEMBERS,
    RUNBOOK_DOC_PATH,
    SAVED_STATUS_CANDIDATE,
    SAVED_STATUS_LIBRARY,
    SAVED_STOCK_S01APP,
    SAVED_STOCK_SDVCAM,
    SAVED_WP2A_EVIDENCE_ROOT,
    SAVED_WP5_REVIEW,
    STATUS_CANDIDATE_SHA256,
    STATUS_LIBRARY_SHA256,
    STOCK_S01APP_LINES,
    STOCK_S01APP_MD5,
    STOCK_S01APP_SHA256,
    STOCK_S01APP_SIZE,
    STOCK_SDVCAM_MD5,
    STOCK_SDVCAM_SHA256,
    STOCK_SDVCAM_SIZE,
    TrialPackageError,
    VERDICT_LOCAL_BLOCKED,
    VERDICT_REJECTED,
    build_review_package,
    compact_public_summary,
    validate_review_package,
)
from tools.trial_package import cli as pkg_cli
from tools.trial_package import package as pkg_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
MEDIA_CANDIDATE_PRIVATE = REPO_ROOT / 'private/native-start/sdvcam-media-candidate'


def _real_inputs_present() -> bool:
    return all(
        p.exists()
        for p in (
            SAVED_STOCK_S01APP,
            SAVED_STOCK_SDVCAM,
            SAVED_STATUS_CANDIDATE,
            SAVED_STATUS_LIBRARY,
            SAVED_WP5_REVIEW,
            SAVED_WP2A_EVIDENCE_ROOT,
            RUNBOOK_DOC_PATH,
        )
    )


def _refresh_manifest_member_digest(pkg_dir: Path, rel_path: str) -> None:
    """Test helper: update manifest hashes for rel_path to test semantic validator checks beyond raw hash mismatch."""
    manifest_path = pkg_dir / 'package-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    blob = (pkg_dir / rel_path).read_bytes()
    entry = manifest['members'][rel_path]
    entry['sha256'] = hashlib.sha256(blob).hexdigest()
    entry['md5'] = hashlib.md5(blob).hexdigest()
    entry['size'] = len(blob)
    if 'lines' in entry:
        entry['lines'] = len(blob.splitlines())
    blobs = {m: (pkg_dir / m).read_bytes() for m in PACKAGE_MEMBERS if m != 'package-manifest.json'}
    manifest['package_tree_sha256'] = pkg_mod._tree_sha256(blobs)
    os.chmod(manifest_path, 0o644)
    manifest_path.write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode('ascii'))
    os.chmod(manifest_path, 0o644)


def _rewrite_manifest(pkg_dir: Path, mutator) -> None:
    manifest_path = pkg_dir / 'package-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    mutator(manifest)
    manifest_path.write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode('ascii'))
    os.chmod(manifest_path, 0o644)


@unittest.skipUnless(_real_inputs_present(), 'saved real inputs not present')
class TrialPackageRealInputsTests(unittest.TestCase):
    """Focused builder/validator tests against pinned read-only vendor/WP2A artifacts."""

    @classmethod
    def setUpClass(cls):
        cls._shared_tmp = tempfile.TemporaryDirectory(prefix='cammy-chunk6-shared-')
        cls.shared_pkg = Path(cls._shared_tmp.name) / 'base-pkg'
        cls.shared_build = build_review_package(cls.shared_pkg)

    @classmethod
    def tearDownClass(cls):
        cls._shared_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cammy-chunk6-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def copy_package(self, name: str = 'pkg') -> Path:
        dest = self.base / name
        shutil.copytree(self.shared_pkg, dest, symlinks=True)
        for root, _, files in os.walk(dest):
            for f in files:
                os.chmod(Path(root) / f, 0o644)
        return dest

    def test_real_byte_package_build_validate_and_determinism(self):
        pkg_b = self.base / 'pkg-b'
        res_b = build_review_package(pkg_b)
        self.assertTrue(self.shared_build['ok'])
        self.assertTrue(res_b['ok'])
        self.assertEqual(self.shared_build['verdict'], VERDICT_LOCAL_BLOCKED)
        self.assertEqual(self.shared_build['manifest_sha256'], res_b['manifest_sha256'])
        self.assertEqual(self.shared_build['package_tree_sha256'], res_b['package_tree_sha256'])
        self.assertEqual(self.shared_build['contents_count'], 14)

        for rel in PACKAGE_MEMBERS:
            self.assertEqual(
                (self.shared_pkg / rel).read_bytes(),
                (pkg_b / rel).read_bytes(),
                f'non-deterministic member bytes: {rel}',
            )

        val = validate_review_package(self.shared_pkg)
        self.assertTrue(val['ok'], val['reasons'])
        self.assertEqual(val['verdict'], VERDICT_LOCAL_BLOCKED)
        self.assertFalse(val['activation_eligible'])
        self.assertFalse(val['installation_authorized'])
        self.assertFalse(val['deployed'])
        self.assertFalse(val['hardware_validated'])
        self.assertTrue(val['local_only'])
        self.assertTrue(val['review_only'])
        self.assertEqual(val['select_gate'], 'blocked')

        summary = compact_public_summary(self.shared_pkg)
        self.assertTrue(summary['ok'])
        self.assertEqual(summary['verdict'], VERDICT_LOCAL_BLOCKED)
        ids = summary['identities']
        self.assertEqual(ids['stock_s01app']['sha256'], STOCK_S01APP_SHA256)
        self.assertEqual(ids['stock_s01app']['md5'], STOCK_S01APP_MD5)
        self.assertEqual(ids['stock_s01app']['size'], STOCK_S01APP_SIZE)
        self.assertEqual(ids['stock_s01app']['lines'], STOCK_S01APP_LINES)
        self.assertEqual(ids['helper_proposal']['sha256'], EXPECTED_HELPER_PROPOSAL_SHA256)
        self.assertEqual(ids['replacement_s01app']['sha256'], EXPECTED_REPLACEMENT_S01APP_SHA256)
        self.assertEqual(ids['replacement_s01app']['md5'], EXPECTED_REPLACEMENT_S01APP_MD5)
        self.assertEqual(ids['replacement_s01app']['size'], EXPECTED_REPLACEMENT_S01APP_SIZE)
        self.assertEqual(ids['replacement_s01app']['lines'], EXPECTED_REPLACEMENT_S01APP_LINES)
        self.assertEqual(ids['stock_sdvcam']['sha256'], STOCK_SDVCAM_SHA256)
        self.assertEqual(ids['stock_sdvcam']['md5'], STOCK_SDVCAM_MD5)
        self.assertEqual(ids['stock_sdvcam']['size'], STOCK_SDVCAM_SIZE)
        self.assertEqual(ids['status_candidate']['sha256'], STATUS_CANDIDATE_SHA256)
        self.assertEqual(ids['status_library']['sha256'], STATUS_LIBRARY_SHA256)

    def test_gate_descriptions_follow_revised_route_and_reject_legacy_policy(self):
        manifest = json.loads((self.shared_pkg / 'package-manifest.json').read_text())
        checklist = json.loads((self.shared_pkg / 'review/acceptance-checklist.json').read_text())
        gates = manifest['gates']
        self.assertNotIn('S00cammy-select', json.dumps(gates))
        self.assertEqual(checklist['open_gates'], gates['early_selector_gates'])
        by_id = {g['id']: g for g in gates['early_selector_gates']}
        self.assertIn('S01app replacement', by_id['E2']['requirement'])
        self.assertEqual(by_id['EA']['status'], 'blocked')
        self.assertEqual(by_id['E6']['status'], 'open')
        self.assertIn('power loss', by_id['E6']['requirement'])
        mutated = self.copy_package('legacy-gates')
        manifest['gates'] = trial_policy.gates_report()
        (mutated / 'package-manifest.json').write_text(json.dumps(manifest))
        result = validate_review_package(mutated)
        self.assertFalse(result['ok'])
        self.assertTrue(any('revised S01app route' in r for r in result['reasons']))

    def test_blocked_and_inert_defaults_and_target_mapping(self):
        for rel in PACKAGE_MEMBERS:
            fpath = self.shared_pkg / rel
            st = os.lstat(fpath)
            self.assertEqual(st.st_mode & 0o777, 0o644, rel)
            self.assertEqual(st.st_mode & 0o111, 0, rel)
            self.assertEqual(st.st_nlink, 1, rel)

        self.assertFalse((self.shared_pkg / 'tf-staging/CammyOS-native/arm').exists())
        self.assertEqual(list((self.shared_pkg / 'tf-staging/CammyOS-native').glob('ack-*')), [])
        self.assertEqual(
            (self.shared_pkg / 'internal-bootstrap/native-select.enabled.disabled-template').read_bytes(),
            DISABLED_MODE_TEMPLATE,
        )

        s01_text = (self.shared_pkg / 'internal-bootstrap/S01app.proposal').read_text(encoding='ascii')
        self.assertIn('SELECT_GATE=blocked\n', s01_text)
        self.assertNotIn('SELECT_GATE=chunk5-fixture-open\n', s01_text)

        manifest = json.loads((self.shared_pkg / 'package-manifest.json').read_text(encoding='utf-8'))
        expected_lib_path = (
            f'/tmp/cammy-native-status/{STATUS_LIBRARY_SHA256}/libcammy_native_status.so'
        )
        self.assertEqual(manifest['identities']['status_library']['ram_path'], expected_lib_path)
        self.assertEqual(manifest['identities']['status_candidate']['ram_path'], '/run/cammy-select/sdvcam')

        internal_dirs = {
            d['camera_path']: d for d in manifest['target_layout']['internal_directories']
        }
        self.assertIn('/etc/cammy', internal_dirs)
        self.assertIn('/etc/cammy/native-select', internal_dirs)
        self.assertEqual(internal_dirs['/etc/cammy/native-select']['intended_mode'], '0700')
        self.assertEqual(internal_dirs['/etc/cammy/native-select']['intended_owner'], '0:0')

        for rel, entry in manifest['members'].items():
            self.assertTrue(entry['host_artifact_owner_is_not_camera_root'], rel)
            self.assertEqual(entry['package_mode'], '0644', rel)
            self.assertFalse(entry['executable_in_package'], rel)

    def test_replacement_allowlist_binding_and_stock_s01app_rejection(self):
        allow_bytes = (self.shared_pkg / 'internal-bootstrap/native-select.allow').read_bytes()
        parsed = R.parse(allow_bytes, R.ALLOW_KEYS, R.ALLOW_FORMAT)
        self.assertEqual(parsed['s01app_md5'], EXPECTED_REPLACEMENT_S01APP_MD5)
        self.assertNotEqual(parsed['s01app_md5'], STOCK_S01APP_MD5)
        self.assertEqual(parsed['source_md5'], STOCK_SDVCAM_MD5)
        self.assertEqual(parsed['source_size'], str(STOCK_SDVCAM_SIZE))

        # Tamper allowlist to bind pristine stock S01app MD5 instead of replacement S01app.proposal MD5.
        pkg = self.copy_package('bad-allow-stock-s01')
        cand_md5, cand_size, cand_cls = parsed['candidate'].split(':')
        lib_sha, lib_md5, lib_size = parsed['library'].split(':')
        wrong_allow = R.allow_record(
            STOCK_S01APP_MD5,
            STOCK_SDVCAM_MD5,
            STOCK_SDVCAM_SIZE,
            cand_md5,
            int(cand_size),
            cand_cls,
            lib_sha,
            lib_md5,
            int(lib_size),
        )
        (pkg / 'internal-bootstrap/native-select.allow').write_bytes(wrong_allow)
        os.chmod(pkg / 'internal-bootstrap/native-select.allow', 0o644)
        _refresh_manifest_member_digest(pkg, 'internal-bootstrap/native-select.allow')

        res = validate_review_package(pkg)
        self.assertFalse(res['ok'])
        self.assertEqual(res['verdict'], VERDICT_REJECTED)
        self.assertTrue(
            any('wrongly binds pristine stock S01app' in r for r in res['reasons']),
            res['reasons'],
        )

    def test_stock_rollback_correctness_and_replacement_confusion_rejection(self):
        rollback_bytes = (self.shared_pkg / 'rollback/S01app.stock').read_bytes()
        proposal_bytes = (self.shared_pkg / 'internal-bootstrap/S01app.proposal').read_bytes()
        self.assertEqual(rollback_bytes, SAVED_STOCK_S01APP.read_bytes())
        self.assertEqual(hashlib.sha256(rollback_bytes).hexdigest(), STOCK_S01APP_SHA256)
        self.assertEqual(hashlib.md5(rollback_bytes).hexdigest(), STOCK_S01APP_MD5)
        self.assertEqual(len(rollback_bytes), STOCK_S01APP_SIZE)
        self.assertNotEqual(rollback_bytes, proposal_bytes)

        # Replacing rollback/S01app.stock with S01app.proposal must be rejected even if manifest hashes are updated.
        pkg = self.copy_package('bad-rollback')
        (pkg / 'rollback/S01app.stock').write_bytes(proposal_bytes)
        os.chmod(pkg / 'rollback/S01app.stock', 0o644)
        _refresh_manifest_member_digest(pkg, 'rollback/S01app.stock')
        res = validate_review_package(pkg)
        self.assertFalse(res['ok'])
        self.assertEqual(res['verdict'], VERDICT_REJECTED)

        # Corrupting manifest rollback expected_sha256 to replacement hash must also be rejected.
        pkg2 = self.copy_package('bad-rollback-meta')
        _rewrite_manifest(
            pkg2,
            lambda m: m['rollback'].__setitem__('expected_sha256', EXPECTED_REPLACEMENT_S01APP_SHA256),
        )
        res2 = validate_review_package(pkg2)
        self.assertFalse(res2['ok'])
        self.assertEqual(res2['verdict'], VERDICT_REJECTED)
        self.assertTrue(any('rollback' in r for r in res2['reasons']), res2['reasons'])

    def test_output_path_guards_overwrite_symlink_and_traversal_rejection(self):
        for forbidden in (
            REPO_ROOT / 'docs/bad-pkg',
            REPO_ROOT / 'tools/bad-pkg',
            Path('/etc/cammy-bad-pkg'),
            self.base / 'sub' / '..' / 'escape',
        ):
            with self.subTest(forbidden=str(forbidden)):
                with self.assertRaises(TrialPackageError):
                    build_review_package(forbidden)

        # Non-empty directory overwrite refused.
        non_empty = self.base / 'non-empty'
        non_empty.mkdir()
        (non_empty / 'existing.txt').write_text('do not overwrite', encoding='utf-8')
        with self.assertRaises(TrialPackageError):
            build_review_package(non_empty)

        # Symlinked destination directory refused.
        real_target = self.base / 'real-target'
        real_target.mkdir()
        sym_out = self.base / 'sym-out'
        sym_out.symlink_to(real_target)
        with self.assertRaises(TrialPackageError):
            build_review_package(sym_out)

    def test_validator_rejects_tampering_extra_members_armed_files_exec_bits_symlinks_and_hardlinks(self):
        # 1. Byte corruption in candidate.bin.
        pkg_corrupt = self.copy_package('corrupt-cand')
        cand_path = pkg_corrupt / 'tf-staging/CammyOS-native/candidate.bin'
        raw = bytearray(cand_path.read_bytes())
        raw[0] ^= 0xFF
        cand_path.write_bytes(bytes(raw))
        os.chmod(cand_path, 0o644)
        self.assertFalse(validate_review_package(pkg_corrupt)['ok'])

        # 2. Extra file in package.
        pkg_extra = self.copy_package('extra-file')
        extra_file = pkg_extra / 'unexpected.txt'
        extra_file.write_text('extra', encoding='utf-8')
        os.chmod(extra_file, 0o644)
        res_extra = validate_review_package(pkg_extra)
        self.assertFalse(res_extra['ok'])
        self.assertTrue(any('unexpected extra package members' in r for r in res_extra['reasons']))

        # 3. Armed tf-staging/CammyOS-native/arm file.
        pkg_armed = self.copy_package('armed-tf')
        armed_path = pkg_armed / 'tf-staging/CammyOS-native/arm'
        armed_path.write_bytes((pkg_armed / 'templates/arm.template').read_bytes())
        os.chmod(armed_path, 0o644)
        res_armed = validate_review_package(pkg_armed)
        self.assertFalse(res_armed['ok'])
        self.assertTrue(any('armed tf-staging/CammyOS-native/arm' in r for r in res_armed['reasons']))

        # 4. Execute bit set on S01app.proposal.
        pkg_exec = self.copy_package('exec-bit')
        os.chmod(pkg_exec / 'internal-bootstrap/S01app.proposal', 0o755)
        res_exec = validate_review_package(pkg_exec)
        self.assertFalse(res_exec['ok'])
        self.assertTrue(any('expected 0o644 with no exec bits' in r for r in res_exec['reasons']))

        # 5. Symlinked member file.
        pkg_sym = self.copy_package('symlink-member')
        outside = self.base / 'outside.txt'
        outside.write_text('outside', encoding='utf-8')
        target_member = pkg_sym / 'review/NATIVE-TRIAL-REVIEW.md'
        target_member.unlink()
        target_member.symlink_to(outside)
        res_sym = validate_review_package(pkg_sym)
        self.assertFalse(res_sym['ok'])
        self.assertTrue(any('symlink file not allowed' in r for r in res_sym['reasons']))

        # 6. Hardlinked member file (st_nlink > 1).
        pkg_hl = self.copy_package('hardlink-member')
        hl_other = self.base / 'hardlink-alias.stock'
        os.link(pkg_hl / 'rollback/S01app.stock', hl_other)
        res_hl = validate_review_package(pkg_hl)
        self.assertFalse(res_hl['ok'])
        self.assertTrue(any('hardlink count' in r for r in res_hl['reasons']))

        # 7. Enabled mode template (mode=select instead of mode=disabled).
        pkg_mode = self.copy_package('enabled-mode')
        mode_file = pkg_mode / 'internal-bootstrap/native-select.enabled.disabled-template'
        mode_file.write_bytes(b'mode=select\n')
        os.chmod(mode_file, 0o644)
        _refresh_manifest_member_digest(pkg_mode, 'internal-bootstrap/native-select.enabled.disabled-template')
        res_mode = validate_review_package(pkg_mode)
        self.assertFalse(res_mode['ok'])
        self.assertTrue(any('disabled mode template' in r for r in res_mode['reasons']))

        # 8. Missing stock_sdvcam_path or evidence_root refuses validation.
        self.assertFalse(validate_review_package(self.shared_pkg, stock_sdvcam_path=None)['ok'])
        self.assertFalse(validate_review_package(self.shared_pkg, evidence_root=None)['ok'])

    def test_no_fabricated_hardware_success_and_checklist_guards(self):
        checklist_path = self.shared_pkg / 'review/acceptance-checklist.json'
        checklist = json.loads(checklist_path.read_text(encoding='utf-8'))
        self.assertEqual(checklist['format'], CHECKLIST_FORMAT)
        self.assertFalse(checklist['hardware_performed'])
        self.assertFalse(checklist['hardware_pass'])
        self.assertEqual(checklist['verdict'], VERDICT_LOCAL_BLOCKED)

        for stage_name in ('selected-boot', 'cleanup', 'stock-restore'):
            stage = checklist['hardware_stages'][stage_name]
            self.assertEqual(stage['status'], 'PENDING_HARDWARE_NOT_RUN')
            self.assertFalse(stage['hardware_performed'])
            self.assertIsNone(stage['boot_id'])

        # Fabricating hardware_pass=True or fake PID in checklist must be rejected.
        for label, mutator in (
            ('hw-pass-flag', lambda c: c.__setitem__('hardware_pass', True)),
            ('hw-stage-pass', lambda c: c['hardware_stages']['selected-boot'].__setitem__('status', 'PASS')),
            (
                'fake-pid',
                lambda c: c['hardware_stages']['selected-boot']['observations'].__setitem__(
                    'native_apps', [{'pid': 4242, 'starttime': 100}]
                ),
            ),
            (
                'fake-boot-id',
                lambda c: c['hardware_stages']['selected-boot'].__setitem__(
                    'boot_id', '11111111-2222-4333-8444-555555555555'
                ),
            ),
        ):
            with self.subTest(label=label):
                pkg = self.copy_package(f'fab-{label}')
                cpath = pkg / 'review/acceptance-checklist.json'
                cdata = json.loads(cpath.read_text(encoding='utf-8'))
                mutator(cdata)
                cpath.write_bytes((json.dumps(cdata, indent=2, sort_keys=True) + '\n').encode('ascii'))
                os.chmod(cpath, 0o644)
                _refresh_manifest_member_digest(pkg, 'review/acceptance-checklist.json')
                res = validate_review_package(pkg)
                self.assertFalse(res['ok'], res['reasons'])
                self.assertEqual(res['verdict'], VERDICT_REJECTED)

        # Promoting review.json evidence_class from offline to reviewed must be rejected.
        pkg_rev = self.copy_package('promoted-review')
        rpath = pkg_rev / 'provenance/review.json'
        rdata = json.loads(rpath.read_text(encoding='utf-8'))
        rdata['evidence_class'] = 'reviewed'
        rpath.write_bytes((json.dumps(rdata, indent=2, sort_keys=True) + '\n').encode('ascii'))
        os.chmod(rpath, 0o644)
        _refresh_manifest_member_digest(pkg_rev, 'provenance/review.json')
        res_rev = validate_review_package(pkg_rev)
        self.assertFalse(res_rev['ok'])
        self.assertEqual(res_rev['verdict'], VERDICT_REJECTED)


class SyntheticAndCliSafetyTests(unittest.TestCase):
    """Tests that synthetic fixtures remain ineligible in production and CLI rejects apply/install modes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cammy-chunk6-syn-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_no_fake_digest_override_in_production_api_and_synthetic_rejected(self):
        for fn in (build_review_package, validate_review_package):
            params = set(inspect.signature(fn).parameters)
            for forbidden_param in (
                'allow_synthetic',
                'expected_candidate_sha256',
                'expected_library_sha256',
                'expected_stock_sha256',
                'skip_hash_check',
                'override_digests',
            ):
                self.assertNotIn(forbidden_param, params)

        # Synthetic files passed to build_review_package are rejected.
        syn_cand = self.base / 'syn-candidate.bin'
        syn_lib = self.base / 'syn-lib.so'
        syn_src = self.base / 'syn-sdvcam'
        syn_cand.write_bytes(b'\x7fELF' + b'A' * 4096)
        syn_lib.write_bytes(b'\x7fELF' + b'B' * 1024)
        syn_src.write_bytes(b'\x7fELF' + b'C' * 4096)

        with self.assertRaises(TrialPackageError):
            build_review_package(
                self.base / 'syn-out',
                stock_sdvcam_path=syn_src,
                candidate_path=syn_cand,
                library_path=syn_lib,
            )

    @unittest.skipUnless(
        _real_inputs_present() and MEDIA_CANDIDATE_PRIVATE.is_file(),
        'real inputs or private media candidate not present',
    )
    def test_media_connect_candidate_rejected_by_builder(self):
        with self.assertRaises(TrialPackageError) as ctx:
            build_review_package(
                self.base / 'media-out',
                candidate_path=MEDIA_CANDIDATE_PRIVATE,
            )
        self.assertIn('structural classification', str(ctx.exception))

    def test_cli_refuses_apply_install_flash_adb_and_enable_modes(self):
        for token in (
            'apply',
            'install',
            'flash',
            'adb',
            'deploy',
            'enable',
            '--apply',
            '--install',
            '--flash',
            '--adb',
            '--deploy',
            '--enable',
            '--open-gate',
        ):
            with self.subTest(token=token):
                buf = io.StringIO()
                with mock.patch('sys.stdout', buf):
                    code = pkg_cli.main([token])
                self.assertEqual(code, 3)
                payload = json.loads(buf.getvalue())
                self.assertFalse(payload['ok'])
                self.assertEqual(payload['verdict'], VERDICT_REJECTED)
                self.assertFalse(payload['activation_eligible'])
                self.assertFalse(payload['installation_authorized'])


    def test_fixture_evidence_class_preserved_and_promoted_review_refused(self):
        rev_data = json.loads(SAVED_WP5_REVIEW.read_text(encoding='utf-8'))
        rev_data['evidence_class'] = 'fixture'
        fixture_rev = self.base / 'fixture-review.json'
        fixture_rev.write_text(json.dumps(rev_data, indent=2, sort_keys=True) + '\n', encoding='utf-8')

        pkg_fix = self.base / 'pkg-fixture'
        res = build_review_package(pkg_fix, review_path=fixture_rev)
        self.assertTrue(res['ok'])
        self.assertEqual(res['verdict'], VERDICT_LOCAL_BLOCKED)
        self.assertFalse(res['activation_eligible'])
        self.assertTrue(res['local_only'])

        manifest = json.loads((pkg_fix / 'package-manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['classification_summary']['review_evidence_class'], 'fixture')
        self.assertTrue(manifest['local_only'])
        self.assertFalse(manifest['activation_eligible'])

        # Attempting to build with evidence_class='reviewed' is refused before E1 manager closure.
        rev_data['evidence_class'] = 'reviewed'
        promoted_rev = self.base / 'promoted-review.json'
        promoted_rev.write_text(json.dumps(rev_data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        with self.assertRaises(TrialPackageError):
            build_review_package(self.base / 'pkg-promoted', review_path=promoted_rev)

    def test_cli_build_validate_and_summary_roundtrip(self):
        out_dir = self.base / 'cli-pkg'
        for cmd in (
            ['build', '--out-dir', str(out_dir)],
            ['validate', str(out_dir)],
            ['summary', str(out_dir)],
        ):
            buf = io.StringIO()
            with mock.patch('sys.stdout', buf):
                code = pkg_cli.main(cmd)
            self.assertEqual(code, 0, cmd)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['verdict'], VERDICT_LOCAL_BLOCKED)


if __name__ == '__main__':
    unittest.main()
