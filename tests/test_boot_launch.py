"""Chunk 4 boot-launch proposal generator and stock-fallback local tests.

Exercises:
1. Exact-source generator verification and refusal of wrong source before output;
2. Mechanical preservation of unrelated setup/stop/case ranges and replacement of
   the single active legacy backup condition (line 78) and all five launch sites
   (lines 91, 99, 101, 114, 118);
3. Distinct build-input S01app hash vs generated replacement S01app hash, mode 0644
   inactive emission only into ignored directories, and refusal of public paths;
4. Parse-only checks of both helper and generated S01app proposal under dash,
   busybox sh, bash, and bash --posix;
5. Relocated dash execution + Python reference model equivalence across all five
   launch branches and fallback decisions:
   - missing/disabled/invalid config and attempt-to-enable (mode=observe / mode=select)
     with SELECT_GATE=blocked skipping TF/card I/O and selector flash writes;
   - runtime stock sdvcam mismatch, non-executable stock, and broken/missing tools
     (md5sum, tail -c, awk) resulting in NO LAUNCH (recovery-required);
   - exclusive per-boot RAM launch stamp preventing duplicate helper/start execution
     and preserving established status/stamp evidence;
   - existing native owner detection (/proc/[0-9]*/comm == sdvcam) refusing launch;
   - tightened backup eligibility: valid normal card firstboot (line 91), existing
     backup MD5 mismatch (line 99), existing backup MD5 match (line 101), normal
     missing-.sdvcam backup copy (line 114), and no-eligible-backup launch-only (line 118);
   - unrelated vfat mount and private staging residue (/run/cammy-select/tf mount or
     directory, /run/cammy-select/sdvcam residue) refusing backup authorization.
"""
from __future__ import annotations

import hashlib
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

from boot_launch import (  # noqa: E402
    EXPECTED_BACKUP_LINE,
    EXPECTED_BACKUP_LINE_NO,
    EXPECTED_LAUNCH_LINES,
    HELPER_PROPOSAL_PATH,
    STOCK_S01APP_LINES,
    STOCK_S01APP_MD5,
    STOCK_S01APP_SHA256,
    STOCK_S01APP_SIZE,
    STOCK_SDVCAM_MD5,
    STOCK_SDVCAM_SHA256,
    STOCK_SDVCAM_SIZE,
    BootLaunchEnv,
    BootLaunchFakeTree,
    SourceVerificationError,
    emit_inactive_proposal_bundle,
    generate_proposal,
    run_helper_model,
    run_s01app_branch_model,
    verify_stock_s01app,
)
from boot_launch.cli import main as cli_main  # noqa: E402
from boot_launch.generator import SAVED_STOCK_S01APP  # noqa: E402
from boot_launch.model import (  # noqa: E402
    DASH,
    RELOCATABLE_PATHS,
    relocate_helper_for_test,
    run_relocated_s01app_branch_shell,
)
from native_trial import policy as trial_policy  # noqa: E402


def _body_system_paths(text: str) -> list[str]:
    """Return any un-relocated camera paths outside the marked header and comments."""
    found: list[str] = []
    in_header = False
    for line in text.splitlines():
        if line.startswith(('# --- relocatable paths', '# --- stock runtime identity')):
            in_header = True
        elif line.startswith(('# --- end relocatable paths', '# --- end stock runtime identity')):
            in_header = False
        elif not in_header and not line.lstrip().startswith('#'):
            found += re.findall(
                r'(?<![\w$}])/(?:etc|usr|run|proc|dev(?!/null)|mnt|data|overlay|tmp)\b',
                line,
            )
    return found


class GeneratorAndContractTests(unittest.TestCase):
    """Exact-source generator, identity separation, and static/parse invariants."""

    @classmethod
    def setUpClass(cls):
        cls.stock_bytes = SAVED_STOCK_S01APP.read_bytes()
        cls.helper_bytes = HELPER_PROPOSAL_PATH.read_bytes()
        cls.proposal = generate_proposal(cls.stock_bytes, cls.helper_bytes)

    def test_stock_identities_match_policy_and_contract(self):
        self.assertEqual(STOCK_S01APP_SHA256, '3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136')
        self.assertEqual(STOCK_S01APP_MD5, 'daeb505e811834088f64cb07696c5cbe')
        self.assertEqual(STOCK_S01APP_SIZE, 4246)
        self.assertEqual(STOCK_S01APP_LINES, 156)
        self.assertEqual(STOCK_SDVCAM_SHA256, '8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec')
        self.assertEqual(STOCK_SDVCAM_MD5, '8689ce53c4674563a7a6935f97c0d727')
        self.assertEqual(STOCK_SDVCAM_SIZE, 11335176)
        self.assertEqual(STOCK_S01APP_SHA256, trial_policy.STOCK_S01APP_SHA256)
        self.assertEqual(STOCK_SDVCAM_SHA256, trial_policy.STOCK_SDVCAM_SHA256)
        self.assertEqual(STOCK_SDVCAM_MD5, trial_policy.STOCK_SDVCAM_MD5)
        self.assertEqual(STOCK_SDVCAM_SIZE, trial_policy.STOCK_SDVCAM_SIZE)
        # EA remains blocked on old S00 bind route.
        self.assertIn(('EA', 'blocked'), [(g[0], g[1]) for g in trial_policy.EARLY_SELECTOR_GATES])

    def test_helper_proposal_permissions_and_relocatable_paths(self):
        mode = HELPER_PROPOSAL_PATH.stat().st_mode & 0o777
        self.assertEqual(mode, 0o644, 'helper proposal must be mode 0644 (not executable)')
        helper_text = self.helper_bytes.decode('ascii')
        self.assertEqual(_body_system_paths(helper_text), [])
        self.assertIn('SELECT_GATE=blocked\n', helper_text)
        # Ensure no environment override variable is consulted in production helper.
        self.assertNotIn('CAMMY_', helper_text)
        self.assertNotIn('${STOCK_SDVCAM_MD5:-', helper_text)

    def test_wrong_source_refused_before_any_output(self):
        # 1. Single byte flip
        bad_bytes = bytearray(self.stock_bytes)
        bad_bytes[10] ^= 0x01
        with self.assertRaises(SourceVerificationError):
            verify_stock_s01app(bytes(bad_bytes))
        with self.assertRaises(SourceVerificationError):
            generate_proposal(bytes(bad_bytes), self.helper_bytes)

        # 2. Truncated input
        with self.assertRaises(SourceVerificationError):
            generate_proposal(self.stock_bytes[:-1], self.helper_bytes)

        # 3. CLI emit-proposal with wrong source writes nothing to output dir
        with tempfile.TemporaryDirectory(prefix='cammy-chunk4-wrong-') as tmp:
            bad_src = Path(tmp) / 'bad_S01app'
            bad_src.write_bytes(bytes(bad_bytes))
            out_dir = Path(tmp) / 'out'
            rc = cli_main(['emit-proposal', '--source', str(bad_src), '--out-dir', str(out_dir)])
            self.assertEqual(rc, 3)
            self.assertFalse(out_dir.exists(), 'wrong source must emit no directory or files')

    def test_generator_replaces_exact_sites_and_preserves_unrelated_ranges(self):
        p = self.proposal
        self.assertEqual(p.source_sha256, STOCK_S01APP_SHA256)
        self.assertEqual(p.source_size, STOCK_S01APP_SIZE)
        self.assertEqual(p.source_lines, STOCK_S01APP_LINES)
        self.assertNotEqual(p.replacement_sha256, p.source_sha256)
        self.assertNotEqual(p.replacement_md5, p.source_md5)

        # Exactly 6 replaced sites: 1 backup gate (line 78) + 5 launch sites (91, 99, 101, 114, 118).
        self.assertEqual(len(p.replaced_sites), 6)
        self.assertEqual(p.replaced_sites[0]['kind'], 'backup_gate')
        self.assertEqual(p.replaced_sites[0]['stock_line'], EXPECTED_BACKUP_LINE_NO)
        launch_sites = [s['stock_line'] for s in p.replaced_sites if s['kind'] == 'launch_site']
        self.assertEqual(launch_sites, [91, 99, 101, 114, 118])

        # No active `/usr/bin/sdvcam &` or active `grep '\<vfat\>'` remains in replacement.
        for line in p.replacement_text.splitlines():
            if not line.lstrip().startswith('#'):
                self.assertNotIn('/usr/bin/sdvcam &', line)
        # Exactly 5 invocations of cammy_launch_selected_or_stock in start()
        calls = [
            line.strip()
            for line in p.replacement_text.splitlines()
            if line.strip() == 'cammy_launch_selected_or_stock'
        ]
        self.assertEqual(len(calls), 5)

        # All 8 preserved stock line ranges verified:
        # 1..23, 24..77, 79..90, 92..98, 100..100, 102..113, 115..117, 119..156
        expected_stock_ranges = [
            [1, 23], [24, 77], [79, 90], [92, 98],
            [100, 100], [102, 113], [115, 117], [119, 156],
        ]
        self.assertEqual([r['stock_lines'] for r in p.preserved_ranges], expected_stock_ranges)
        # Sum of preserved lines + 6 replaced lines == 156 total stock lines.
        total_preserved = sum(r['line_count'] for r in p.preserved_ranges)
        self.assertEqual(total_preserved + 6, STOCK_S01APP_LINES)

        # Generated proposal never compares modified S01app to stock S01app input hash at runtime.
        self.assertNotIn(STOCK_S01APP_SHA256, p.replacement_text)
        self.assertNotIn(STOCK_S01APP_MD5, p.replacement_text)

    def test_emit_bundle_guards_output_location_and_sets_0644_inactive_metadata(self):
        # Refuses writing into tracked repo directories.
        with self.assertRaises(ValueError):
            emit_inactive_proposal_bundle(ROOT / 'docs/work-packages')
        with self.assertRaises(ValueError):
            emit_inactive_proposal_bundle(ROOT / 'native/activation')

        with tempfile.TemporaryDirectory(prefix='cammy-chunk4-emit-') as tmp:
            out_dir = Path(tmp) / 'bundle'
            res = emit_inactive_proposal_bundle(out_dir)
            self.assertTrue(res['ok'])
            manifest = res['manifest']
            self.assertFalse(manifest['deployed'])
            self.assertFalse(manifest['activation_eligible'])
            self.assertEqual(manifest['select_gate'], 'blocked')
            self.assertFalse(manifest['candidate_selection_implemented'])
            self.assertNotEqual(
                manifest['identities']['build_input_s01app']['sha256'],
                manifest['identities']['generated_replacement_s01app']['sha256'],
            )
            for key in ('proposal', 'diff', 'manifest'):
                fpath = Path(res['files'][key])
                self.assertTrue(fpath.is_file())
                self.assertEqual(fpath.stat().st_mode & 0o777, 0o644)

    def test_parse_only_across_shells(self):
        with tempfile.TemporaryDirectory(prefix='cammy-chunk4-parse-') as tmp:
            prop_file = Path(tmp) / 'S01app.proposal'
            prop_file.write_text(self.proposal.replacement_text)
            for cmd in (
                ['dash', '-n'],
                ['busybox', 'sh', '-n'],
                ['bash', '-n'],
                ['bash', '--posix', '-n'],
            ):
                if shutil.which(cmd[0]) is None:
                    continue
                for target in (HELPER_PROPOSAL_PATH, prop_file):
                    proc = subprocess.run([*cmd, str(target)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(proc.returncode, 0, f'{cmd} failed on {target}: {proc.stderr}')

    def test_output_symlinks_are_refused_without_writes(self):
        for name in ('S01app.proposal', 'S01app.proposal.diff', 'manifest.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='cammy-chunk4-link-') as tmp:
                base = Path(tmp)
                victim = base / 'keep.txt'
                victim.write_text('preserve me')
                out = base / 'out'
                out.mkdir()
                (out / name).symlink_to(victim)
                with self.assertRaises(ValueError):
                    emit_inactive_proposal_bundle(out)
                self.assertEqual(victim.read_text(), 'preserve me')
                self.assertEqual(sorted(p.name for p in out.iterdir()), [name])


@unittest.skipUnless(DASH, 'dash is required to run relocated boot-launch shell tests')
class BootLaunchFallbackAndBranchTests(unittest.TestCase):
    """Shell (dash) and Python reference model equivalence across all 5 branches and fault cases."""

    @classmethod
    def setUpClass(cls):
        cls.proposal = generate_proposal(
            SAVED_STOCK_S01APP.read_bytes(),
            HELPER_PROPOSAL_PATH.read_bytes(),
        )

    def build_pair(self, **kwargs) -> tuple[BootLaunchFakeTree, BootLaunchFakeTree]:
        tmp = tempfile.TemporaryDirectory(prefix='cammy-chunk4-case-')
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        shell_tree = BootLaunchFakeTree.build(base / 'shell', **kwargs)
        model_tree = BootLaunchFakeTree.build(base / 'model', **kwargs)
        return shell_tree, model_tree

    def assert_shell_and_model_match(
        self,
        *,
        expected_branch: str,
        expected_site_line: int,
        expected_prep_decision: str,
        expected_prep_reason: str,
        expected_backup_eligible: int,
        expected_app_started: int,
        select_gate: str = 'blocked',
        **tree_kwargs,
    ):
        shell_tree, model_tree = self.build_pair(**tree_kwargs)
        proc = run_relocated_s01app_branch_shell(
            self.proposal.replacement_text,
            shell_tree,
            select_gate=select_gate,
        )
        self.assertEqual(proc.returncode, 0, f'stderr={proc.stderr}\nstdout={proc.stdout}')
        outcome = run_s01app_branch_model(BootLaunchEnv(model_tree), select_gate=select_gate)

        shell_snap = shell_tree.snapshot()
        model_snap = model_tree.snapshot()
        self.assertEqual(shell_snap['violations'], [])
        self.assertEqual(model_snap['violations'], [])
        self.assertEqual(shell_snap, model_snap)

        self.assertEqual(outcome.branch, expected_branch)
        self.assertEqual(outcome.launch_site_stock_line, expected_site_line)
        self.assertEqual(outcome.helper.prepare_decision, expected_prep_decision)
        self.assertEqual(outcome.helper.prepare_reason, expected_prep_reason)
        self.assertEqual(outcome.helper.backup_eligible, expected_backup_eligible)
        self.assertEqual(outcome.app_started_count, expected_app_started)
        self.assertEqual(shell_snap['launched_count'], expected_app_started)
        self.assertEqual(shell_snap['state_files'], {}, 'Chunk 4 must never write durable selector state')
        return shell_snap, outcome

    # ---- Config handling (missing, disabled, invalid, attempt-to-enable) ----

    def test_missing_config_skips_tf_and_launches_verified_stock_site_118(self):
        snap, _ = self.assert_shell_and_model_match(
            expected_branch='no-eligible-backup-launch-only',
            expected_site_line=118,
            expected_prep_decision='stock',
            expected_prep_reason='not-enabled',
            expected_backup_eligible=0,
            expected_app_started=1,
            enabled_content=None,
            extsd_vfat=False,
        )
        self.assertIn('decision=stock\nreason=not-enabled\n', snap['status'])
        self.assertIn('target=stock\n', snap['stamp'])
        self.assertIsNone(snap['card_sdvcam_md5'])

    def test_disabled_invalid_and_attempt_to_enable_configs_stay_stock(self):
        cases = [
            ('mode=disabled\n', 'disabled'),
            ('mode=observe\n', 'select-gate-blocked'),
            ('mode=select\n', 'select-gate-blocked'),
            ('mode=SELECT\n', 'config-invalid'),
            ('garbage\n', 'config-invalid'),
            ('', 'config-invalid'),
        ]
        for content, expected_reason in cases:
            with self.subTest(content=content):
                snap, _ = self.assert_shell_and_model_match(
                    expected_branch='no-eligible-backup-launch-only',
                    expected_site_line=118,
                    expected_prep_decision='stock',
                    expected_prep_reason=expected_reason,
                    expected_backup_eligible=0,
                    expected_app_started=1,
                    enabled_content=content,
                )
                self.assertIn(f'reason={expected_reason}\n', snap['status'])

    def test_tampered_select_gate_refuses_launch(self):
        snap, _ = self.assert_shell_and_model_match(
            expected_branch='no-eligible-backup-launch-only',
            expected_site_line=118,
            expected_prep_decision='recovery-required',
            expected_prep_reason='select-gate-tampered',
            expected_backup_eligible=0,
            expected_app_started=0,
            select_gate='open',
            enabled_content='mode=select\n',
            extsd_vfat=True,
        )
        self.assertIsNone(snap['stamp'])
        self.assertIsNone(snap['card_sdvcam_md5'])

    # ---- All 5 S01app launch sites (lines 91, 99, 101, 114, 118) ----

    def test_branch_line_91_valid_normal_card_firstboot(self):
        snap, outcome = self.assert_shell_and_model_match(
            expected_branch='firstboot-backup',
            expected_site_line=91,
            expected_prep_decision='stock',
            expected_prep_reason='not-enabled',
            expected_backup_eligible=1,
            expected_app_started=1,
            extsd_vfat=True,
            firstboot=True,
        )
        self.assertTrue(outcome.backup_written)
        self.assertTrue(outcome.firstboot_cleared)
        self.assertTrue(outcome.isp_cleared)
        self.assertFalse(snap['firstboot_exists'])
        self.assertEqual(snap['isp_remaining'], [])
        self.assertIsNotNone(snap['card_sdvcam_md5'])
        self.assertEqual(snap['card_sdvcam_md5'], snap['data_app_md5'])

    def test_branch_line_99_existing_backup_md5_mismatch_removes_backup_and_launches_stock(self):
        snap, outcome = self.assert_shell_and_model_match(
            expected_branch='existing-backup-md5-mismatch',
            expected_site_line=99,
            expected_prep_decision='stock',
            expected_prep_reason='not-enabled',
            expected_backup_eligible=1,
            expected_app_started=1,
            extsd_vfat=True,
            existing_card_sdvcam=b'stale-card-backup-bytes',
            existing_data_app_md5='0' * 32,
        )
        self.assertTrue(outcome.backup_removed)
        self.assertIsNone(snap['card_sdvcam_md5'])

    def test_branch_line_101_existing_backup_md5_match_retains_backup_and_launches_stock(self):
        card_bytes = b'matching-card-backup-bytes'
        card_md5 = hashlib.md5(card_bytes).hexdigest()
        snap, outcome = self.assert_shell_and_model_match(
            expected_branch='existing-backup-md5-match',
            expected_site_line=101,
            expected_prep_decision='stock',
            expected_prep_reason='not-enabled',
            expected_backup_eligible=1,
            expected_app_started=1,
            extsd_vfat=True,
            existing_card_sdvcam=card_bytes,
            existing_data_app_md5=card_md5,
        )
        self.assertFalse(outcome.backup_removed)
        self.assertEqual(snap['card_sdvcam_md5'], card_md5)

    def test_branch_line_114_normal_boot_populates_missing_backup_and_launches_stock(self):
        snap, outcome = self.assert_shell_and_model_match(
            expected_branch='normal-backup-populate',
            expected_site_line=114,
            expected_prep_decision='stock',
            expected_prep_reason='not-enabled',
            expected_backup_eligible=1,
            expected_app_started=1,
            extsd_vfat=True,
            firstboot=False,
            existing_card_sdvcam=None,
        )
        self.assertTrue(outcome.backup_written)
        self.assertIsNotNone(snap['card_sdvcam_md5'])
        self.assertEqual(snap['card_sdvcam_md5'], snap['data_app_md5'])

    def test_branch_line_118_no_vfat_mount_launches_stock_without_backup_writes(self):
        snap, outcome = self.assert_shell_and_model_match(
            expected_branch='no-eligible-backup-launch-only',
            expected_site_line=118,
            expected_prep_decision='stock',
            expected_prep_reason='not-enabled',
            expected_backup_eligible=0,
            expected_app_started=1,
            extsd_vfat=False,
            firstboot=True,
        )
        self.assertFalse(outcome.backup_written)
        self.assertIsNone(snap['card_sdvcam_md5'])
        self.assertIsNone(snap['data_app_md5'])
        # Firstboot marker on internal overlay is not touched when no card is mounted at /mnt/extsd.
        self.assertTrue(snap['firstboot_exists'])

    # ---- Tightened backup gate: unrelated vfat and private staging residue ----

    def test_unrelated_vfat_mount_cannot_trigger_backup_into_internal_overlay(self):
        snap, outcome = self.assert_shell_and_model_match(
            expected_branch='no-eligible-backup-launch-only',
            expected_site_line=118,
            expected_prep_decision='stock',
            expected_prep_reason='not-enabled',
            expected_backup_eligible=0,
            expected_app_started=1,
            extsd_vfat=False,
            unrelated_vfat_mount='/tmp/other-vfat',
            firstboot=True,
        )
        self.assertFalse(outcome.backup_written)
        self.assertIsNone(snap['card_sdvcam_md5'])
        self.assertIsNone(snap['data_app_md5'])

    def test_private_staging_residue_suppresses_backup_even_when_extsd_is_vfat(self):
        residue_cases = [
            {'private_tf_mounted': True},
            {'private_tf_dir_exists': True},
            {'private_sdvcam_residue': b'leftover-staged-bytes'},
        ]
        for kwargs in residue_cases:
            with self.subTest(**kwargs):
                snap, outcome = self.assert_shell_and_model_match(
                    expected_branch='no-eligible-backup-launch-only',
                    expected_site_line=118,
                    expected_prep_decision='stock',
                    expected_prep_reason='not-enabled-staging-residue',
                    expected_backup_eligible=0,
                    expected_app_started=1,
                    extsd_vfat=True,
                    firstboot=True,
                    **kwargs,
                )
                self.assertFalse(outcome.backup_written)
                self.assertIsNone(snap['card_sdvcam_md5'])
                self.assertIsNone(snap['data_app_md5'])

    # ---- Runtime stock mismatch & missing/broken tools -> NO LAUNCH ----

    def test_runtime_stock_mismatch_and_non_executable_stock_refuse_launch(self):
        for kwargs in (
            {'stock_app_matches': False},
            {'stock_app_executable': False},
        ):
            with self.subTest(**kwargs):
                snap, _ = self.assert_shell_and_model_match(
                    expected_branch='no-eligible-backup-launch-only',
                    expected_site_line=118,
                    expected_prep_decision='recovery-required',
                    expected_prep_reason='stock-source-mismatch',
                    expected_backup_eligible=0,
                    expected_app_started=0,
                    extsd_vfat=True,
                    firstboot=True,
                    **kwargs,
                )
                self.assertIsNone(snap['stamp'])
                self.assertIsNone(snap['card_sdvcam_md5'])
                self.assertIn('decision=recovery-required\nreason=stock-source-mismatch\n', snap['status'])

    def test_missing_or_broken_hash_and_shell_tools_refuse_launch(self):
        for fault, expected_reason in (
            ('md5-broken', 'tools-unverified'),
            ('tail-broken', 'tools-unverified'),
            ('tools-broken', 'tools-unverified'),
            ('awk-broken', 'run-not-tmpfs'),
        ):
            with self.subTest(fault=fault):
                snap, _ = self.assert_shell_and_model_match(
                    expected_branch='no-eligible-backup-launch-only',
                    expected_site_line=118,
                    expected_prep_decision='recovery-required',
                    expected_prep_reason=expected_reason,
                    expected_backup_eligible=0,
                    expected_app_started=0,
                    extsd_vfat=True,
                    faults=(fault,),
                )
                self.assertIsNone(snap['stamp'])
                self.assertEqual(snap['launched_count'], 0)

    def test_run_mount_and_boot_id_failures_refuse_launch(self):
        self.assert_shell_and_model_match(
            expected_branch='no-eligible-backup-launch-only',
            expected_site_line=118,
            expected_prep_decision='recovery-required',
            expected_prep_reason='run-not-tmpfs',
            expected_backup_eligible=0,
            expected_app_started=0,
            run_tmpfs=False,
        )
        self.assert_shell_and_model_match(
            expected_branch='no-eligible-backup-launch-only',
            expected_site_line=118,
            expected_prep_decision='recovery-required',
            expected_prep_reason='run-noexec',
            expected_backup_eligible=0,
            expected_app_started=0,
            run_noexec=True,
        )
        self.assert_shell_and_model_match(
            expected_branch='no-eligible-backup-launch-only',
            expected_site_line=118,
            expected_prep_decision='recovery-required',
            expected_prep_reason='boot-id-invalid',
            expected_backup_eligible=0,
            expected_app_started=0,
            boot_id='invalid-boot-id',
        )

    # ---- Exclusive per-boot launch stamp & existing native owner ----

    def test_duplicate_start_or_helper_invocation_does_not_start_second_app_or_overwrite_evidence(self):
        shell_tree, model_tree = self.build_pair(extsd_vfat=True, enabled_content='mode=observe\n')
        # First invocation
        proc1 = run_relocated_s01app_branch_shell(self.proposal.replacement_text, shell_tree)
        self.assertEqual(proc1.returncode, 0)
        out1 = run_s01app_branch_model(BootLaunchEnv(model_tree))
        self.assertEqual(out1.app_started_count, 1)

        snap1 = shell_tree.snapshot()
        self.assertEqual(snap1, model_tree.snapshot())
        status_before = snap1['status']
        stamp_before = snap1['stamp']
        self.assertIsNotNone(status_before)
        self.assertIsNotNone(stamp_before)

        # Mutate config before second invocation in same boot: status & stamp must NOT be overwritten,
        # and no second app may start.
        for tree in (shell_tree, model_tree):
            tree.path('/etc/cammy/native-select.enabled').write_text('mode=disabled\n')

        proc2 = run_relocated_s01app_branch_shell(self.proposal.replacement_text, shell_tree)
        self.assertEqual(proc2.returncode, 0)
        out2 = run_s01app_branch_model(BootLaunchEnv(model_tree))

        self.assertEqual(out2.helper.prepare_decision, 'noop')
        self.assertEqual(out2.helper.prepare_reason, 'launch-stamp-exists')
        self.assertEqual(out2.app_started_count, 1, 'second invocation must not start a second app')
        snap2 = shell_tree.snapshot()
        self.assertEqual(snap2, model_tree.snapshot())
        self.assertEqual(snap2['status'], status_before, 'existing status evidence must not be overwritten')
        self.assertEqual(snap2['stamp'], stamp_before, 'existing launch stamp must not be overwritten')

    def test_existing_native_owner_running_refuses_launch_and_preserves_evidence(self):
        snap, _ = self.assert_shell_and_model_match(
            expected_branch='no-eligible-backup-launch-only',
            expected_site_line=118,
            expected_prep_decision='noop',
            expected_prep_reason='app-already-running',
            expected_backup_eligible=0,
            expected_app_started=0,
            extsd_vfat=True,
            running_comm='sdvcam',
        )
        self.assertIsNone(snap['status'])
        self.assertIsNone(snap['stamp'])

    def test_direct_second_call_to_launch_helper_in_same_shell_is_blocked_by_stamp(self):
        shell_tree, _ = self.build_pair()
        relocated = relocate_helper_for_test(HELPER_PROPOSAL_PATH.read_text(), shell_tree)
        harness = (
            relocated
            + '\ncammy_prepare_launch\n'
            + 'cammy_launch_selected_or_stock\n'
            + 'rc1=$?\n'
            + 'cammy_launch_selected_or_stock\n'
            + 'rc2=$?\n'
            + 'echo "rcs=$rc1,$rc2"\n'
        )
        script = shell_tree.ctl / 'double_launch.sh'
        script.write_text(harness)
        env = {'PATH': f'{shell_tree.ctl / "bin"}:/usr/bin:/bin', 'LC_ALL': 'C'}
        proc = subprocess.run([DASH, str(script)], capture_output=True, text=True, env=env, timeout=15, cwd=shell_tree.root)
        shell_tree.path('/run/cammy-select').chmod(0o700)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('rcs=0,0', proc.stdout)
        self.assertIn('launch=refused decision=noop reason=launch-stamp-exists', proc.stdout)
        self.assertEqual(shell_tree.launched_Count(), 1)



    def test_launch_stamp_write_failure_refuses_launch(self):
        # Pre-create a directory at /run/cammy-select/launch.stamp (and set fault for model)
        # so exclusive stamp creation after cammy_prepare_launch fails -> NO LAUNCH.
        shell_tree, model_tree = self.build_pair(extsd_vfat=True, faults=("stamp-write-fail",))
        relocated = relocate_helper_for_test(HELPER_PROPOSAL_PATH.read_text(), shell_tree)
        # In shell, simulate stamp write failure after prepare by placing a readonly dir where stamp goes
        # right before cammy_launch_selected_or_stock.
        harness = (
            relocated
            + "\ncammy_prepare_launch\n"
            + "cammy_write_exclusive() { return 1; }\n"
            + "cammy_launch_selected_or_stock\n"
        )
        script = shell_tree.ctl / "stamp_fail.sh"
        script.write_text(harness)
        env = {"PATH": f"{shell_tree.ctl / 'bin'}:/usr/bin:/bin", "LC_ALL": "C"}
        proc = subprocess.run([DASH, str(script)], capture_output=True, text=True, env=env, timeout=15, cwd=shell_tree.root)
        shell_tree.path('/run/cammy-select').chmod(0o700)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("launch=refused decision=recovery-required reason=launch-stamp-failed", proc.stdout)
        self.assertEqual(shell_tree.launched_Count(), 0)

        out = run_helper_model(BootLaunchEnv(model_tree), call_launch=True)
        self.assertFalse(out.launch_started)
        self.assertEqual(out.launch_decision, "recovery-required")
        self.assertEqual(out.launch_reason, "launch-stamp-failed")
        self.assertEqual(model_tree.launched_Count(), 0)

    def test_existing_rundir_requires_private_mode_and_numeric_owner(self):
        for fault in ('mode', 'uid', 'gid'):
            with self.subTest(fault=fault):
                shell_tree, _ = self.build_pair()
                directory = shell_tree.path('/run/cammy-select')
                directory.mkdir(mode=0o700)
                helper = relocate_helper_for_test(HELPER_PROPOSAL_PATH.read_text(), shell_tree)
                if fault == 'mode':
                    directory.chmod(0o755)
                elif fault == 'uid':
                    helper = helper.replace(f'REQUIRED_UID={os.getuid()}\n', f'REQUIRED_UID={os.getuid()+1}\n')
                else:
                    helper = helper.replace(f'REQUIRED_GID={os.getgid()}\n', f'REQUIRED_GID={os.getgid()+1}\n')
                proc = subprocess.run(
                    [DASH], input=helper+'\ncammy_prepare_launch\ncammy_launch_selected_or_stock\nwait\n',
                    text=True, capture_output=True, timeout=15, cwd=shell_tree.root,
                    env={'PATH': f'{shell_tree.ctl / "bin"}:/usr/bin:/bin', 'LC_ALL': 'C'})
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn('reason=rundir-not-private', proc.stdout)
                self.assertEqual(shell_tree.launched_Count(), 0)
                self.assertEqual(list(directory.iterdir()), [])

    def test_vfat_from_wrong_source_cannot_authorize_backup(self):
        shell_tree, model_tree = self.build_pair(extsd_vfat=True, firstboot=True)
        for tree in (shell_tree, model_tree):
            mounts = tree.path('/proc/mounts')
            mounts.write_text(mounts.read_text().replace('/dev/mmcblk0 ', '/dev/sdb1 '))
        proc = run_relocated_s01app_branch_shell(self.proposal.replacement_text, shell_tree)
        outcome = run_s01app_branch_model(BootLaunchEnv(model_tree))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(outcome.helper.backup_eligible, 0)
        self.assertEqual(shell_tree.snapshot(), model_tree.snapshot())
        self.assertFalse(shell_tree.path('/data/app_md5').exists())
        self.assertTrue(shell_tree.path('/overlay/.firstboot').exists())

    def test_launch_log_reports_attempt_without_claiming_ready_or_started(self):
        shell_tree, _ = self.build_pair()
        proc = run_relocated_s01app_branch_shell(self.proposal.replacement_text, shell_tree)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('launch=exec-attempted target=stock', proc.stdout)
        self.assertNotIn('launch=started', proc.stdout)
        self.assertEqual(shell_tree.launched_Count(), 1)  # separately observed fixture execution


if __name__ == '__main__':
    unittest.main()
