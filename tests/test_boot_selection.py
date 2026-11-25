"""Chunk 5 tests: one-shot status-only candidate selection behind the blocked gate.

Safety rules enforced:
- Never accesses the camera, ADB, network, /dev/mmcblk0, or real mounts.
- Never executes original/full patched sdvcam, original S01app, or full unguarded
  generated init. Executes only the relocated helper + S01app backup/launch branch
  block (stock lines 78-119) under dash (never busybox ash) with confined PATH
  stubs and synthetic non-vendor shell scripts.
- Shipped SELECT_GATE remains blocked; test-only opening uses the explicit
  synthetic fixture token SELECT_GATE=chunk5-fixture-open inside temporary
  relocated fixtures marked synthetic_fixture=True, activation_eligible=False.
- Every scenario compares the relocated dash execution snapshot against the
  Python reference model snapshot and checks for zero path/mount violations.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from boot_launch import (  # noqa: E402
    HELPER_PROPOSAL_PATH,
    INFLIGHT_FORMAT,
    STOCK_S01APP_MD5,
    STOCK_SDVCAM_MD5,
    STOCK_SDVCAM_SHA256,
    STOCK_SDVCAM_SIZE,
    SYNTHETIC_CANDIDATE_BYTES,
    SYNTHETIC_LIBRARY_BYTES,
    SYNTHETIC_STOCK_BYTES,
    SourceVerificationError,
    BootLaunchEnv,
    BootLaunchFakeTree,
    PowerLoss,
    generate_proposal,
    inflight_record,
    run_helper_model,
    run_s01app_branch_model,
    verify_helper_proposal,
)
from boot_launch.generator import SAVED_STOCK_S01APP  # noqa: E402
from boot_launch.model import (  # noqa: E402
    DASH,
    SYNTHETIC_INIT_BYTES,
    relocate_helper_for_test,
    run_relocated_s01app_branch_shell,
)
from native_trial import bundle as trial_bundle  # noqa: E402
from native_trial import policy as trial_policy  # noqa: E402
from native_trial import records as R  # noqa: E402

FIXTURE_OPEN_GATE = 'chunk5-fixture-open'

BOOT_1 = '00000001-0000-4000-8000-000000000001'
BOOT_2 = '00000002-0000-4000-8000-000000000002'
BOOT_3 = '00000003-0000-4000-8000-000000000003'
BOOT_4 = '00000004-0000-4000-8000-000000000004'

ATTEMPT_1 = '11111111111111111111111111111111'
ATTEMPT_2 = '22222222222222222222222222222222'
ATTEMPT_3 = '33333333333333333333333333333333'
ATTEMPT_4 = '44444444444444444444444444444444'
ATTEMPT_5 = '55555555555555555555555555555555'

SYN_STOCK_MD5 = hashlib.md5(SYNTHETIC_STOCK_BYTES).hexdigest()
SYN_STOCK_SIZE = len(SYNTHETIC_STOCK_BYTES)
SYN_CAND_MD5 = hashlib.md5(SYNTHETIC_CANDIDATE_BYTES).hexdigest()
SYN_CAND_SIZE = len(SYNTHETIC_CANDIDATE_BYTES)
SYN_LIB_SHA256 = hashlib.sha256(SYNTHETIC_LIBRARY_BYTES).hexdigest()
SYN_LIB_MD5 = hashlib.md5(SYNTHETIC_LIBRARY_BYTES).hexdigest()
SYN_LIB_SIZE = len(SYNTHETIC_LIBRARY_BYTES)

# Pinned real status-only candidate and library identities (read-only verification).
EXPECTED_STATUS_CANDIDATE_SHA256 = 'a9dedc985cc79d933601390df36b4458784e8dd4807b867cd53cfe0a8a9caba0'
EXPECTED_STATUS_LIBRARY_SHA256 = 'db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065'
EXPECTED_STATUS_LIBRARY_RAM_PATH = (
    '/tmp/cammy-native-status/'
    'db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065/'
    'libcammy_native_status.so'
)

PRIVATE_WP2A_DIR = ROOT / 'private/ownership-program/wp2a'
PRIVATE_STATUS_CANDIDATE = PRIVATE_WP2A_DIR / 'candidate/sdvcam-status-db34fa035a26'
PRIVATE_STATUS_LIBRARY = PRIVATE_WP2A_DIR / 'library/libcammy_native_status.so'
SAVED_STOCK_SDVCAM = Path(
    os.environ.get('CAMMY_STOCK_WORK', str(Path(__file__).resolve().parents[1].parent / 'Cammy Bot/M105 investigation/work')) + '/firmware-rootfs/usr/bin/sdvcam'
)
OLD_MEDIA_CANDIDATE = Path(
    os.environ.get('CAMMY_STOCK_WORK', str(Path(__file__).resolve().parents[1].parent / 'Cammy Bot/M105 investigation/work')) + '/patched/sdvcam.cammy-native-v1'
)


def make_synthetic_allow(
    *,
    klass: str = R.CLASS_STATUS_ONLY,
    cand_md5: str = SYN_CAND_MD5,
    cand_size: int = SYN_CAND_SIZE,
    lib_sha256: str = SYN_LIB_SHA256,
    lib_md5: str = SYN_LIB_MD5,
    lib_size: int = SYN_LIB_SIZE,
    src_md5: str = SYN_STOCK_MD5,
    src_size: int = SYN_STOCK_SIZE,
    s01_md5: str = hashlib.md5(SYNTHETIC_INIT_BYTES).hexdigest(),
) -> bytes:
    return R.allow_record(
        s01_md5,
        src_md5,
        src_size,
        cand_md5,
        cand_size,
        klass,
        lib_sha256,
        lib_md5,
        lib_size,
    )


def make_synthetic_arm(
    attempt: str = ATTEMPT_1,
    *,
    klass: str = R.CLASS_STATUS_ONLY,
    cand_md5: str = SYN_CAND_MD5,
    cand_size: int = SYN_CAND_SIZE,
    lib_sha256: str = SYN_LIB_SHA256,
    lib_md5: str = SYN_LIB_MD5,
    lib_size: int = SYN_LIB_SIZE,
) -> bytes:
    return R.arm_record(
        attempt,
        cand_md5,
        cand_size,
        klass,
        lib_sha256,
        lib_md5,
        lib_size,
    )


def make_synthetic_card(
    attempt: str = ATTEMPT_1,
    *,
    klass: str = R.CLASS_STATUS_ONLY,
    candidate_bytes: bytes | None = SYNTHETIC_CANDIDATE_BYTES,
    library_bytes: bytes | None = SYNTHETIC_LIBRARY_BYTES,
    arm_bytes: bytes | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    if arm_bytes is None:
        arm_bytes = make_synthetic_arm(attempt, klass=klass)
    if arm_bytes is not None:
        files['arm'] = arm_bytes
    if candidate_bytes is not None:
        files[R.CANDIDATE_NAME] = candidate_bytes
    if library_bytes is not None:
        files[R.LIBRARY_NAME] = library_bytes
    if extra_files:
        files.update(extra_files)
    return files


class GateAndIdentityClassificationTests(unittest.TestCase):
    """Gate stays blocked in shipped code/generator; read-only classifier binds real hashes."""

    @classmethod
    def setUpClass(cls):
        cls.stock_s01_bytes = SAVED_STOCK_S01APP.read_bytes()
        cls.helper_bytes = HELPER_PROPOSAL_PATH.read_bytes()
        cls.proposal = generate_proposal(cls.stock_s01_bytes, cls.helper_bytes)

    def test_public_generator_refuses_non_blocked_helper(self):
        unblocked = self.helper_bytes.replace(
            b'SELECT_GATE=blocked\n',
            f'SELECT_GATE={FIXTURE_OPEN_GATE}\n'.encode('ascii'),
        )
        with self.assertRaises(SourceVerificationError):
            verify_helper_proposal(unblocked)
        with self.assertRaises(SourceVerificationError):
            generate_proposal(self.stock_s01_bytes, unblocked)

    def test_manifest_reports_selection_logic_present_and_live_routing_disabled(self):
        manifest = self.proposal.manifest()
        self.assertEqual(manifest['chunk'], 'chunk5')
        self.assertEqual(manifest['select_gate'], 'blocked')
        self.assertFalse(manifest['deployed'])
        self.assertFalse(manifest['activation_eligible'])
        self.assertFalse(manifest['candidate_selection_implemented'])
        self.assertTrue(manifest['candidate_selection_logic_present'])
        self.assertFalse(manifest['candidate_selection_live_routing'])
        self.assertEqual(manifest['chunk5_hook']['status'], 'logic_present_behind_blocked_gate')

    def test_library_path_contract_matches_pinned_sha256(self):
        self.assertEqual(
            R.library_path(EXPECTED_STATUS_LIBRARY_SHA256),
            EXPECTED_STATUS_LIBRARY_RAM_PATH,
        )

    @unittest.skipUnless(
        SAVED_STOCK_SDVCAM.is_file()
        and PRIVATE_STATUS_CANDIDATE.is_file()
        and PRIVATE_STATUS_LIBRARY.is_file(),
        'private WP2A candidate/library or saved stock sdvcam not present',
    )
    def test_read_only_classifier_verifies_status_candidate_and_rejects_connect_media(self):
        report = trial_bundle.classify(
            PRIVATE_STATUS_CANDIDATE,
            SAVED_STOCK_SDVCAM,
            R.CLASS_STATUS_ONLY,
            PRIVATE_STATUS_LIBRARY,
            reproduce=False,
        )
        self.assertEqual(report['source']['sha256'], STOCK_SDVCAM_SHA256)
        self.assertEqual(report['source']['md5'], STOCK_SDVCAM_MD5)
        self.assertEqual(report['source']['size'], STOCK_SDVCAM_SIZE)
        self.assertEqual(report['candidate_sha256'], EXPECTED_STATUS_CANDIDATE_SHA256)
        self.assertEqual(report['library']['sha256'], EXPECTED_STATUS_LIBRARY_SHA256)
        self.assertEqual(report['library']['ram_path'], EXPECTED_STATUS_LIBRARY_RAM_PATH)
        self.assertTrue(report['structural_ok'], report['reasons'])
        self.assertEqual(report['structural_class'], R.CLASS_STATUS_ONLY)
        self.assertEqual(report['connect_trigger_sites_modified'], [])
        self.assertFalse(report['activation_eligible'])

        if OLD_MEDIA_CANDIDATE.is_file():
            media_report = trial_bundle.classify(
                OLD_MEDIA_CANDIDATE,
                SAVED_STOCK_SDVCAM,
                R.CLASS_STATUS_ONLY,
                PRIVATE_STATUS_LIBRARY,
                reproduce=False,
            )
            self.assertFalse(media_report['structural_ok'])
            self.assertEqual(media_report['structural_class'], R.CLASS_MEDIA_CONNECT)
            self.assertFalse(media_report['eligible_for_staging'])
            self.assertFalse(media_report['activation_eligible'])


@unittest.skipUnless(DASH, 'dash is required to execute relocated boot-selection fixtures')
class OneShotSelectionAndFaultTests(unittest.TestCase):
    """Relocated dash + stub execution and Python reference model equivalence for Chunk 5."""

    @classmethod
    def setUpClass(cls):
        cls.proposal = generate_proposal(
            SAVED_STOCK_S01APP.read_bytes(),
            HELPER_PROPOSAL_PATH.read_bytes(),
        )

    def build_pair(
        self,
        *,
        boot_id: str = BOOT_1,
        enabled_content: str | bytes | None = 'mode=select\n',
        allow_bytes: bytes | None = None,
        card_files: dict[str, bytes] | None = None,
        use_default_allow: bool = True,
        use_default_card: bool = True,
        **kwargs,
    ) -> tuple[BootLaunchFakeTree, BootLaunchFakeTree]:
        if allow_bytes is None and use_default_allow:
            allow_bytes = make_synthetic_allow()
        if card_files is None and use_default_card:
            card_files = make_synthetic_card()
        tmp = tempfile.TemporaryDirectory(prefix='cammy-chunk5-case-')
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        common = dict(
            boot_id=boot_id,
            enabled_content=enabled_content,
            allow_bytes=allow_bytes,
            card_files=card_files,
            **kwargs,
        )
        shell_tree = BootLaunchFakeTree.build(base / 'shell', **common)
        model_tree = BootLaunchFakeTree.build(base / 'model', **common)
        return shell_tree, model_tree

    def run_pair(
        self,
        shell_tree: BootLaunchFakeTree,
        model_tree: BootLaunchFakeTree,
        *,
        select_gate: str = FIXTURE_OPEN_GATE,
    ):
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
        return shell_snap, outcome, proc

    # ---- 1. Shipped gate blocked vs synthetic fixture open ----

    def test_shipped_blocked_gate_never_touches_tf_or_writes_selector_state_even_when_armed(self):
        shell_tree, model_tree = self.build_pair(enabled_content='mode=select\n')
        snap, outcome, _ = self.run_pair(shell_tree, model_tree, select_gate='blocked')
        self.assertEqual(outcome.helper.prepare_decision, 'stock')
        self.assertEqual(outcome.helper.prepare_reason, 'select-gate-blocked')
        self.assertEqual(snap['state_files'], {})
        self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])
        self.assertIsNone(snap['staged_sdvcam_md5'])
        self.assertEqual(snap['staged_lib_files'], [])
        self.assertFalse(any(c.startswith('mount ') for c in shell_tree.calls()))

    def test_valid_one_shot_select_stages_claims_suppresses_core_and_execs_candidate_once(self):
        shell_tree, model_tree = self.build_pair(enabled_content='mode=select\n')
        snap, outcome, proc = self.run_pair(shell_tree, model_tree)

        self.assertEqual(outcome.helper.prepare_decision, 'candidate')
        self.assertEqual(outcome.helper.prepare_reason, 'selected')
        self.assertEqual(outcome.helper.launch_decision, 'candidate')
        self.assertEqual(outcome.helper.attempt, ATTEMPT_1)
        self.assertEqual(outcome.helper.backup_eligible, 0)
        self.assertEqual(snap['launched_entries'], ['candidate-stub'])
        self.assertEqual(snap['candidate_core_logs'], ['candidate-core=0'])
        self.assertEqual(snap['staged_sdvcam_md5'], SYN_CAND_MD5)
        self.assertEqual(snap['staged_sdvcam_mode'], '0o500')
        self.assertEqual(
            snap['staged_lib_files'],
            [f'tmp/cammy-native-status/{SYN_LIB_SHA256}/libcammy_native_status.so'],
        )
        self.assertTrue(snap['bridge_dir_exists'])
        self.assertIn(f'claimed-{ATTEMPT_1}', snap['state_files'])
        self.assertNotIn('inflight', snap['state_files'])
        self.assertIsNone(snap['card_sdvcam_md5'])
        self.assertIsNone(snap['data_app_md5'])
        self.assertIn('target=candidate\n', snap['stamp'])
        self.assertIn(f'attempt={ATTEMPT_1}\n', snap['stamp'])
        self.assertIn('launch=exec-attempted target=candidate', proc.stdout)
        self.assertNotIn('launch=started', proc.stdout)
        # Verify inflight was synced before mount in call log.
        calls = shell_tree.calls()
        self.assertLess(calls.index('sync'), next(i for i, c in enumerate(calls) if c.startswith('mount ')))

    def test_valid_observe_mode_validates_stages_detaches_cleans_ram_and_launches_stock(self):
        shell_tree, model_tree = self.build_pair(enabled_content='mode=observe\n')
        snap, outcome, _ = self.run_pair(shell_tree, model_tree)

        self.assertEqual(outcome.helper.prepare_decision, 'stock')
        self.assertEqual(outcome.helper.prepare_reason, 'observe-only')
        self.assertEqual(outcome.helper.attempt, ATTEMPT_1)
        self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])
        self.assertEqual(snap['state_files'], {})
        self.assertIsNone(snap['staged_sdvcam_md5'])
        self.assertEqual(snap['staged_lib_files'], [])
        self.assertIn('target=stock\n', snap['stamp'])

    # ---- 2. Multi-boot lifecycle: next boot stock, card removed, rearm/ack, replay ----

    def test_next_boot_with_card_present_or_removed_records_observed_and_runs_stock_without_tf_mount(self):
        for card_present in (True, False):
            with self.subTest(card_present=card_present):
                shell_tree, model_tree = self.build_pair(boot_id=BOOT_1, enabled_content='mode=select\n')
                snap1, out1, _ = self.run_pair(shell_tree, model_tree)
                self.assertEqual(out1.helper.launch_decision, 'candidate')
                self.assertEqual(snap1['launched_entries'], ['candidate-stub'])

                # Reboot into BOOT_2 (with card still armed or card physically removed).
                for tree in (shell_tree, model_tree):
                    tree.reboot(BOOT_2, card_present=card_present)
                    (tree.ctl / 'calls.log').unlink(missing_ok=True)
                    (tree.ctl / 'launched.log').unlink(missing_ok=True)

                snap2, out2, _ = self.run_pair(shell_tree, model_tree)
                self.assertEqual(out2.helper.prepare_decision, 'stock')
                self.assertEqual(out2.helper.prepare_reason, 'post-attempt-boot')
                self.assertEqual(out2.helper.attempt, ATTEMPT_1)
                self.assertEqual(snap2['launched_entries'], ['sdvcam-stub'])
                self.assertIn(f'claimed-{ATTEMPT_1}', snap2['state_files'])
                self.assertIn(f'observed-{ATTEMPT_1}', snap2['state_files'])
                obs = R.parse(
                    snap2['state_files'][f'observed-{ATTEMPT_1}'].encode('ascii'),
                    R.OBSERVED_KEYS,
                    R.OBSERVED_FORMAT,
                )
                self.assertEqual(obs['boot'], BOOT_2)
                # Persistent claimed/observed check governed BOOT_2 BEFORE any TF mount.
                self.assertFalse(any(c.startswith('mount ') for c in shell_tree.calls()))

    def test_rearm_requires_new_attempt_and_valid_ack_quoting_observed_boot(self):
        shell_tree, model_tree = self.build_pair(boot_id=BOOT_1, enabled_content='mode=select\n')
        self.run_pair(shell_tree, model_tree)

        # Boot 2: records observed-ATTEMPT_1 with BOOT_2.
        for tree in (shell_tree, model_tree):
            tree.reboot(BOOT_2)
            (tree.ctl / 'launched.log').unlink(missing_ok=True)
        snap2, out2, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out2.helper.prepare_reason, 'post-attempt-boot')

        # Boot 3a: same ATTEMPT_1 still on card -> attempt-replayed.
        for tree in (shell_tree, model_tree):
            tree.reboot(BOOT_3)
            (tree.ctl / 'launched.log').unlink(missing_ok=True)
        snap3a, out3a, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out3a.helper.prepare_decision, 'stock')
        self.assertEqual(out3a.helper.prepare_reason, 'attempt-replayed')
        self.assertEqual(snap3a['launched_entries'], ['sdvcam-stub'])

        # Boot 3b: new ATTEMPT_2 on card, but missing ack-ATTEMPT_1 -> unacknowledged-attempt.
        for tree in (shell_tree, model_tree):
            tree.reboot(BOOT_3)
            (tree.ctl / 'launched.log').unlink(missing_ok=True)
            (tree.card / 'CammyOS-native/arm').write_bytes(make_synthetic_arm(ATTEMPT_2))
        snap3b, out3b, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out3b.helper.prepare_decision, 'stock')
        self.assertEqual(out3b.helper.prepare_reason, 'unacknowledged-attempt')
        self.assertEqual(snap3b['launched_entries'], ['sdvcam-stub'])

        # Boot 3c: ack-ATTEMPT_1 quotes wrong boot (BOOT_1 instead of BOOT_2) -> unacknowledged-attempt.
        for tree in (shell_tree, model_tree):
            tree.reboot(BOOT_3)
            (tree.ctl / 'launched.log').unlink(missing_ok=True)
            (tree.card / f'CammyOS-native/ack-{ATTEMPT_1}').write_bytes(
                R.ack_record(ATTEMPT_1, BOOT_1, 'stock-restored')
            )
        snap3c, out3c, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out3c.helper.prepare_decision, 'stock')
        self.assertEqual(out3c.helper.prepare_reason, 'unacknowledged-attempt')

        # Boot 4: ack-ATTEMPT_1 quotes exact observed BOOT_2 -> ATTEMPT_2 selects candidate once.
        for tree in (shell_tree, model_tree):
            tree.reboot(BOOT_4)
            (tree.ctl / 'launched.log').unlink(missing_ok=True)
            (tree.card / f'CammyOS-native/ack-{ATTEMPT_1}').write_bytes(
                R.ack_record(ATTEMPT_1, BOOT_2, 'stock-restored')
            )
        snap4, out4, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out4.helper.prepare_decision, 'candidate')
        self.assertEqual(out4.helper.prepare_reason, 'selected')
        self.assertEqual(out4.helper.attempt, ATTEMPT_2)
        self.assertEqual(snap4['launched_entries'], ['candidate-stub'])
        self.assertIn(f'claimed-{ATTEMPT_2}', snap4['state_files'])

    # ---- 3. Candidate crash / exec failure / repeat call in same boot ----

    def test_candidate_crash_or_nonzero_exit_never_launches_second_app_in_same_boot(self):
        crashing_candidate = (
            b'#!/bin/sh\n'
            b'echo "candidate-crash-stub" >> .fake/launched.log\n'
            b'echo "candidate-core=$(ulimit -c)" >> .fake/candidate-core.log\n'
            b'exit 139\n'
        )
        c_md5 = hashlib.md5(crashing_candidate).hexdigest()
        c_size = len(crashing_candidate)
        allow = make_synthetic_allow(cand_md5=c_md5, cand_size=c_size)
        arm = make_synthetic_arm(ATTEMPT_1, cand_md5=c_md5, cand_size=c_size)
        card = make_synthetic_card(ATTEMPT_1, candidate_bytes=crashing_candidate, arm_bytes=arm)

        shell_tree, model_tree = self.build_pair(
            allow_bytes=allow,
            card_files=card,
            candidate_stub_bytes=crashing_candidate,
        )
        snap1, out1, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out1.helper.launch_decision, 'candidate')
        self.assertEqual(snap1['launched_entries'], ['candidate-crash-stub'])
        self.assertEqual(snap1['candidate_core_logs'], ['candidate-core=0'])

        # Repeat invocation in the same boot is blocked by launch.stamp and never falls back to stock.
        snap2, out2, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out2.helper.prepare_decision, 'noop')
        self.assertEqual(out2.helper.prepare_reason, 'launch-stamp-exists')
        self.assertEqual(snap2['launched_entries'], ['candidate-crash-stub'])

        # Even if RAM stamp is cleared while in the same boot, claimed-<id> for this boot blocks re-probe/exec
        # and launches stock only if invoked as fresh boot prep, whereas launch.stamp blocks in-boot retry.
        for tree in (shell_tree, model_tree):
            tree.reboot(BOOT_2)
            (tree.ctl / 'launched.log').unlink(missing_ok=True)
        snap3, out3, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out3.helper.prepare_decision, 'stock')
        self.assertEqual(out3.helper.prepare_reason, 'post-attempt-boot')
        self.assertEqual(snap3['launched_entries'], ['sdvcam-stub'])

    # ---- 4. Pre-card, allowlist, arm, class, payload, copy, private-dir, core, claim, detach faults ----

    def test_firstboot_and_vfat_premounted_skip_trial_and_select_stock(self):
        # Firstboot active
        shell_tree, model_tree = self.build_pair(firstboot=True, extsd_vfat=True)
        snap, out, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out.helper.prepare_decision, 'stock')
        self.assertEqual(out.helper.prepare_reason, 'firstboot-active')
        self.assertEqual(out.branch, 'firstboot-backup')
        self.assertEqual(snap['state_files'], {})
        self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])

        # vfat already mounted before prepare_one_shot
        shell_tree2, model_tree2 = self.build_pair(extsd_vfat=True, firstboot=False)
        snap2, out2, _ = self.run_pair(shell_tree2, model_tree2)
        self.assertEqual(out2.helper.prepare_decision, 'stock')
        self.assertEqual(out2.helper.prepare_reason, 'vfat-premounted')
        self.assertEqual(snap2['state_files'], {})
        self.assertEqual(snap2['launched_entries'], ['sdvcam-stub'])

    def test_persistent_state_anomalies_and_attempt_limit_refuse_tf_probe(self):
        valid_claim_1 = R.claim_record(ATTEMPT_1, BOOT_1, SYN_CAND_MD5)
        valid_obs_1 = R.observed_record(ATTEMPT_1, BOOT_2)
        cases = [
            ('prior-inflight', {'inflight': inflight_record(BOOT_2, 'select')}),
            ('inflight-same-boot', {'inflight': inflight_record(BOOT_1, 'select')}),
            ('already-claimed-this-boot', {f'claimed-{ATTEMPT_1}': valid_claim_1}),
            ('state-invalid', {'unexpected-file': b'bad\n'}),
            ('state-invalid', {'inflight': b'format=cammy-boot-launch-inflight/1\nboot=not-a-uuid\nmode=select\n'}),
            ('state-invalid', {f'observed-{ATTEMPT_1}': valid_obs_1}),  # orphan observed without claimed
            (
                'attempt-limit',
                {
                    f'claimed-{ATTEMPT_1}': R.claim_record(ATTEMPT_1, BOOT_2, SYN_CAND_MD5),
                    f'observed-{ATTEMPT_1}': R.observed_record(ATTEMPT_1, BOOT_3),
                    f'claimed-{ATTEMPT_2}': R.claim_record(ATTEMPT_2, BOOT_2, SYN_CAND_MD5),
                    f'observed-{ATTEMPT_2}': R.observed_record(ATTEMPT_2, BOOT_3),
                    f'claimed-{ATTEMPT_3}': R.claim_record(ATTEMPT_3, BOOT_2, SYN_CAND_MD5),
                    f'observed-{ATTEMPT_3}': R.observed_record(ATTEMPT_3, BOOT_3),
                    f'claimed-{ATTEMPT_4}': R.claim_record(ATTEMPT_4, BOOT_2, SYN_CAND_MD5),
                    f'observed-{ATTEMPT_4}': R.observed_record(ATTEMPT_4, BOOT_3),
                },
            ),
        ]
        for expected_reason, state_files in cases:
            with self.subTest(reason=expected_reason, state_keys= sorted(state_files)):
                shell_tree, model_tree = self.build_pair(boot_id=BOOT_1, state_files=state_files)
                snap, out, _ = self.run_pair(shell_tree, model_tree)
                self.assertEqual(out.helper.prepare_decision, 'stock')
                self.assertEqual(out.helper.prepare_reason, expected_reason)
                self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])
                self.assertFalse(any(c.startswith('mount ') for c in shell_tree.calls()))

    def test_allowlist_arm_class_and_extra_authority_key_rejections(self):
        # 1. Missing allowlist
        s1, m1 = self.build_pair(allow_bytes=None, use_default_allow=False)
        snap1, out1, _ = self.run_pair(s1, m1)
        self.assertEqual(out1.helper.prepare_reason, 'allowlist-invalid')
        self.assertEqual(snap1['state_files'], {})

        # 2. Extra lease/token key in allowlist -> rejected
        extra_allow = make_synthetic_allow() + b'media_lease=token123\n'
        s2, m2 = self.build_pair(allow_bytes=extra_allow)
        _, out2, _ = self.run_pair(s2, m2)
        self.assertEqual(out2.helper.prepare_reason, 'allowlist-invalid')

        # 3. Allowlist source_md5 mismatch against runtime stock
        wrong_src_allow = make_synthetic_allow(src_md5='f' * 32)
        s3, m3 = self.build_pair(allow_bytes=wrong_src_allow)
        _, out3, _ = self.run_pair(s3, m3)
        self.assertEqual(out3.helper.prepare_reason, 'source-mismatch')

        # 4. Missing arm on card -> inflight created then removed, stock launched
        s4, m4 = self.build_pair(card_files={R.CANDIDATE_NAME: SYNTHETIC_CANDIDATE_BYTES, R.LIBRARY_NAME: SYNTHETIC_LIBRARY_BYTES})
        snap4, out4, _ = self.run_pair(s4, m4)
        self.assertEqual(out4.helper.prepare_reason, 'arm-missing')
        self.assertEqual(snap4['state_files'], {})

        # 5. Extra media_lease key in arm -> arm-invalid, inflight removed
        extra_arm = make_synthetic_arm(ATTEMPT_1) + b'lease_ref=lease-1\n'
        s5, m5 = self.build_pair(card_files=make_synthetic_card(arm_bytes=extra_arm))
        snap5, out5, _ = self.run_pair(s5, m5)
        self.assertEqual(out5.helper.prepare_reason, 'arm-invalid')
        self.assertEqual(snap5['state_files'], {})

        # 6. media-lease-gated class allowlisted & armed -> class-ineligible in status-only v1
        media_allow = make_synthetic_allow(klass=R.CLASS_MEDIA_LEASE)
        media_arm = make_synthetic_arm(ATTEMPT_1, klass=R.CLASS_MEDIA_LEASE)
        s6, m6 = self.build_pair(allow_bytes=media_allow, card_files=make_synthetic_card(arm_bytes=media_arm))
        snap6, out6, _ = self.run_pair(s6, m6)
        self.assertEqual(out6.helper.prepare_reason, 'class-ineligible')
        self.assertEqual(snap6['state_files'], {})

        # 7. Arm candidate hash differs from allowlist -> candidate-not-allowlisted
        other_arm = make_synthetic_arm(ATTEMPT_1, cand_md5='a' * 32)
        s7, m7 = self.build_pair(card_files=make_synthetic_card(arm_bytes=other_arm))
        _, out7, _ = self.run_pair(s7, m7)
        self.assertEqual(out7.helper.prepare_reason, 'candidate-not-allowlisted')

    def test_payload_mismatches_memory_low_and_copy_or_staging_faults_clean_up_and_select_stock(self):
        cases = [
            ('candidate-mismatch', {'card_files': make_synthetic_card(candidate_bytes=b'wrong-candidate-bytes')}),
            ('library-mismatch', {'card_files': make_synthetic_card(library_bytes=b'wrong-lib-bytes')}),
            ('memory-low', {'mem_available_kb': 512}),
            ('copy-failed', {'faults': ('copy-fail-1',)}),
            ('copy-mismatch', {'faults': ('copy-corrupt-1',)}),
            ('copy-mismatch', {'faults': ('chmod-fail-1',)}),
            ('library-copy-failed', {'faults': ('copy-fail-2',)}),
            ('library-copy-failed', {'faults': ('copy-corrupt-2',)}),
            ('library-copy-failed', {'faults': ('chmod-fail-2',)}),
            ('core-limit-failed', {'faults': ('core-limit-fail',)}),
            ('claim-failed', {'faults': ('claim-write-fail',)}),
            ('inflight-write-failed', {'faults': ('inflight-write-fail',)}),
            ('tf-unavailable', {'faults': ('tf-mount-fail',)}),
            ('tf-unavailable', {'devices': ()}),
            ('inflight-write-failed', {'faults': ('sync-fail-1',)}),
            ('claim-failed', {'faults': ('sync-fail-2',)}),
            ('inflight-clear-failed', {'faults': ('sync-fail-3',)}),
            ('inflight-write-failed', {'faults': ('corrupt-on-sync-1',)}),
            ('claim-failed', {'faults': ('corrupt-on-sync-2',)}),
            ('lib-dir-noexec', {'tmp_noexec': True}),
            ('lib-dir-not-tmpfs', {'tmp_tmpfs': False}),
        ]
        for expected_reason, kwargs in cases:
            with self.subTest(reason=expected_reason, kwargs=kwargs):
                shell_tree, model_tree = self.build_pair(**kwargs)
                snap, out, _ = self.run_pair(shell_tree, model_tree)
                self.assertEqual(out.helper.prepare_decision, 'stock')
                self.assertEqual(out.helper.prepare_reason, expected_reason)
                self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])
                self.assertIsNone(snap['staged_sdvcam_md5'])
                self.assertEqual(snap['staged_lib_files'], [])
                faults = kwargs.get('faults', ())
                if any(f in faults for f in ('sync-fail-1', 'corrupt-on-sync-1')):
                    self.assertEqual(set(snap['state_files']), {'inflight'})
                elif any(f in faults for f in ('sync-fail-2', 'sync-fail-3', 'corrupt-on-sync-2')):
                    self.assertEqual(set(snap['state_files']), {f'claimed-{ATTEMPT_1}'})
                else:
                    self.assertEqual(snap['state_files'], {})

    def test_wrong_runtime_init_or_allowlist_init_hash_refuses_before_card_access(self):
        for change_runtime in (False, True):
            with self.subTest(change_runtime=change_runtime):
                kwargs = {} if change_runtime else {'allow_bytes': make_synthetic_allow(s01_md5='a' * 32)}
                shell_tree, model_tree = self.build_pair(**kwargs)
                if change_runtime:
                    for tree in (shell_tree, model_tree):
                        tree.path('/etc/init.d/S01app').write_bytes(b'changed init bytes')
                snap, outcome, _ = self.run_pair(shell_tree, model_tree)
                self.assertEqual(outcome.helper.prepare_reason, 's01app-mismatch')
                self.assertEqual(snap['state_files'], {})
                self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])
                self.assertFalse(any(line.startswith('mount ') for line in shell_tree.calls()))
                self.assertEqual(snap['staged_lib_files'], [])

    def test_all_prior_claims_are_observed_even_if_one_already_names_this_boot(self):
        state = {
            f'claimed-{ATTEMPT_1}': R.claim_record(ATTEMPT_1, BOOT_1, SYN_CAND_MD5),
            f'observed-{ATTEMPT_1}': R.observed_record(ATTEMPT_1, BOOT_2),
            f'claimed-{ATTEMPT_2}': R.claim_record(ATTEMPT_2, BOOT_1, SYN_CAND_MD5),
        }
        shell_tree, model_tree = self.build_pair(boot_id=BOOT_2, state_files=state)
        snap, outcome, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(outcome.helper.prepare_reason, 'post-attempt-boot')
        self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])
        for attempt in (ATTEMPT_1, ATTEMPT_2):
            self.assertEqual(shell_tree.state_dir.joinpath(f'observed-{attempt}').read_bytes(),
                             R.observed_record(attempt, BOOT_2))
        self.assertFalse(any(line.startswith('mount ') for line in shell_tree.calls()))

    def test_nonprivate_persistent_state_is_not_read_or_changed_for_selection(self):
        shell_tree, model_tree = self.build_pair(state_files={})
        for tree in (shell_tree, model_tree):
            tree.state_dir.chmod(0o777)
        snap, outcome, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(outcome.helper.prepare_reason, 'state-not-private')
        self.assertEqual(snap['state_files'], {})
        self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])
        self.assertFalse(any(line.startswith('mount ') for line in shell_tree.calls()))

    def test_private_tmp_directory_hazards_refuse_selection_and_fall_back_to_stock(self):
        for hazard, expected_reason in (
            ('libroot_mode', 'lib-dir-exists'),
            ('libdir_exists', 'lib-dir-exists'),
            ('bridgedir_mode', 'bridge-dir-invalid'),
        ):
            with self.subTest(hazard=hazard):
                shell_tree, model_tree = self.build_pair()
                for tree in (shell_tree, model_tree):
                    if hazard == 'libroot_mode':
                        p = tree.path('/tmp/cammy-native-status')
                        p.mkdir(parents=True, exist_ok=True)
                        p.chmod(0o755)
                    elif hazard == 'libdir_exists':
                        p = tree.path(f'/tmp/cammy-native-status/{SYN_LIB_SHA256}')
                        p.mkdir(parents=True, exist_ok=True)
                        p.chmod(0o700)
                    elif hazard == 'bridgedir_mode':
                        p = tree.path('/tmp/cammy-bridge')
                        p.mkdir(parents=True, exist_ok=True)
                        p.chmod(0o777)
                snap, out, _ = self.run_pair(shell_tree, model_tree)
                self.assertEqual(out.helper.prepare_decision, 'stock')
                self.assertEqual(out.helper.prepare_reason, expected_reason)
                self.assertEqual(snap['launched_entries'], ['sdvcam-stub'])
                self.assertEqual(snap['state_files'], {})

    def test_tf_detach_failure_suppresses_backup_cleans_staged_payloads_and_launches_nothing(self):
        shell_tree, model_tree = self.build_pair(
            firstboot=False,
            faults=('tf-umount-fail', 'tf-umount-lazy-fail'),
        )
        snap, out, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out.helper.prepare_decision, 'recovery-required')
        self.assertEqual(out.helper.prepare_reason, 'tf-detach-failed')
        self.assertEqual(out.helper.backup_eligible, 0)
        self.assertEqual(out.app_started_count, 0)
        self.assertEqual(snap['launched_entries'], [])
        self.assertIsNone(snap['stamp'])
        self.assertIsNone(snap['staged_sdvcam_md5'])
        self.assertEqual(snap['staged_lib_files'], [])
        self.assertIsNone(snap['card_sdvcam_md5'])
        self.assertIsNone(snap['data_app_md5'])

    def test_unverifiable_stock_on_selection_fallback_launches_nothing(self):
        shell_tree, _ = self.build_pair()
        # Corrupt staged candidate right before cammy_launch_selected_or_stock AND
        # simultaneously corrupt /usr/bin/sdvcam so fallback stock verification fails -> NO LAUNCH.
        relocated = relocate_helper_for_test(
            HELPER_PROPOSAL_PATH.read_text(),
            shell_tree,
            select_gate=FIXTURE_OPEN_GATE,
        )
        harness = (
            relocated
            + '\ncammy_prepare_launch\n'
            + f'chmod 0700 "{shell_tree.p("/run/cammy-select/sdvcam")}"\n'
            + f'printf "corrupt" > "{shell_tree.p("/run/cammy-select/sdvcam")}"\n'
            + f'chmod 0500 "{shell_tree.p("/run/cammy-select/sdvcam")}"\n'
            + f'printf "corrupt-stock" > "{shell_tree.p("/usr/bin/sdvcam")}"\n'
            + 'cammy_launch_selected_or_stock\n'
            + 'wait\n'
        )
        script = shell_tree.ctl / 'unverifiable_fallback.sh'
        script.write_text(harness)
        env = {'PATH': f'{shell_tree.ctl / "bin"}:/usr/bin:/bin', 'LC_ALL': 'C'}
        proc = subprocess.run([DASH, str(script)], capture_output=True, text=True, env=env, timeout=15, cwd=shell_tree.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('launch=refused decision=recovery-required reason=stock-source-mismatch', proc.stdout)
        self.assertEqual(shell_tree.launched_entries(), [])
        self.assertFalse(shell_tree.path('/run/cammy-select/sdvcam').exists())
        self.assertEqual(list(shell_tree.path('/tmp').rglob('libcammy_native_status.so')), [])

    # ---- 5. Interruption / power-loss boundary simulations (Gate E6 explicit) ----

    def test_interruption_during_inflight_sync_before_card_mount(self):
        # Case A: unsynced inflight vanishes across power loss (unsynced='lose') ->
        # demonstrates Gate E6 explicitly: if flash does not persist inflight across
        # abrupt reset, the next boot has no breadcrumb and can re-enter selection.
        shell_lose, model_lose = self.build_pair(boot_id=BOOT_1, faults=('powerloss-at-sync-1',))
        proc = run_relocated_s01app_branch_shell(
            self.proposal.replacement_text,
            shell_lose,
            select_gate=FIXTURE_OPEN_GATE,
        )
        self.assertNotEqual(proc.returncode, 0)
        with self.assertRaises(PowerLoss):
            run_s01app_branch_model(BootLaunchEnv(model_lose), select_gate=FIXTURE_OPEN_GATE)
        # Verify TF was never mounted before the interruption at sync-1.
        self.assertFalse(any(c.startswith('mount ') for c in shell_lose.calls()))

        for tree in (shell_lose, model_lose):
            tree.reboot(BOOT_2, unsynced='lose')
        snap_lose, out_lose, _ = self.run_pair(shell_lose, model_lose)
        self.assertEqual(out_lose.helper.prepare_decision, 'candidate')
        self.assertEqual(snap_lose['launched_entries'], ['candidate-stub'])

        # Case B: partial/truncated inflight survives on flash (unsynced='truncate') ->
        # next boot detects malformed state before TF access and forces verified stock.
        shell_trunc, model_trunc = self.build_pair(boot_id=BOOT_1, faults=('powerloss-at-sync-1',))
        run_relocated_s01app_branch_shell(
            self.proposal.replacement_text,
            shell_trunc,
            select_gate=FIXTURE_OPEN_GATE,
        )
        with self.assertRaises(PowerLoss):
            run_s01app_branch_model(BootLaunchEnv(model_trunc), select_gate=FIXTURE_OPEN_GATE)
        for tree in (shell_trunc, model_trunc):
            tree.reboot(BOOT_2, unsynced='truncate')
            (tree.ctl / 'calls.log').unlink(missing_ok=True)
        snap_trunc, out_trunc, _ = self.run_pair(shell_trunc, model_trunc)
        self.assertEqual(out_trunc.helper.prepare_decision, 'stock')
        self.assertEqual(out_trunc.helper.prepare_reason, 'state-invalid')
        self.assertEqual(snap_trunc['launched_entries'], ['sdvcam-stub'])
        self.assertFalse(any(c.startswith('mount ') for c in shell_trunc.calls()))

    def test_interruption_after_inflight_synced_during_tf_mount_forces_prior_inflight_stock_on_next_boot(self):
        # Assumption: sync-1 (inflight) reached flash before power loss during TF mount.
        shell_tree, model_tree = self.build_pair(boot_id=BOOT_1, faults=('powerloss-at-mount-1',))
        proc = run_relocated_s01app_branch_shell(
            self.proposal.replacement_text,
            shell_tree,
            select_gate=FIXTURE_OPEN_GATE,
        )
        self.assertNotEqual(proc.returncode, 0)
        with self.assertRaises(PowerLoss):
            run_s01app_branch_model(BootLaunchEnv(model_tree), select_gate=FIXTURE_OPEN_GATE)

        for tree in (shell_tree, model_tree):
            tree.reboot(BOOT_2, unsynced='lose')
            (tree.ctl / 'calls.log').unlink(missing_ok=True)
        snap2, out2, _ = self.run_pair(shell_tree, model_tree)
        self.assertEqual(out2.helper.prepare_decision, 'stock')
        self.assertEqual(out2.helper.prepare_reason, 'prior-inflight')
        self.assertEqual(snap2['launched_entries'], ['sdvcam-stub'])
        self.assertIn('inflight', snap2['state_files'])
        self.assertFalse(any(c.startswith('mount ') for c in shell_tree.calls()))

    def test_interruption_at_claim_sync_and_at_inflight_clear_sync(self):
        # 1. Power loss at sync-2 (when writing claimed-<attempt> while synced inflight exists):
        # If unsynced claim is lost on reboot, surviving synced inflight still forces stock (prior-inflight).
        shell_c, model_c = self.build_pair(boot_id=BOOT_1, faults=('powerloss-at-sync-2',))
        run_relocated_s01app_branch_shell(
            self.proposal.replacement_text,
            shell_c,
            select_gate=FIXTURE_OPEN_GATE,
        )
        with self.assertRaises(PowerLoss):
            run_s01app_branch_model(BootLaunchEnv(model_c), select_gate=FIXTURE_OPEN_GATE)
        for tree in (shell_c, model_c):
            tree.reboot(BOOT_2, unsynced='lose')
            (tree.ctl / 'calls.log').unlink(missing_ok=True)
        snap_c, out_c, _ = self.run_pair(shell_c, model_c)
        self.assertEqual(out_c.helper.prepare_decision, 'stock')
        self.assertEqual(out_c.helper.prepare_reason, 'prior-inflight')
        self.assertEqual(snap_c['launched_entries'], ['sdvcam-stub'])
        self.assertFalse(any(c.startswith('mount ') for c in shell_c.calls()))

        # 2. Power loss at sync-3 (after claimed-<attempt> is synced, while removing inflight):
        # Next boot sees claimed-<attempt>, records observed-<attempt> for BOOT_2, and runs stock.
        shell_i, model_i = self.build_pair(boot_id=BOOT_1, faults=('powerloss-at-sync-3',))
        run_relocated_s01app_branch_shell(
            self.proposal.replacement_text,
            shell_i,
            select_gate=FIXTURE_OPEN_GATE,
        )
        with self.assertRaises(PowerLoss):
            run_s01app_branch_model(BootLaunchEnv(model_i), select_gate=FIXTURE_OPEN_GATE)
        for tree in (shell_i, model_i):
            tree.reboot(BOOT_2, unsynced='lose')
            (tree.ctl / 'calls.log').unlink(missing_ok=True)
        snap_i, out_i, _ = self.run_pair(shell_i, model_i)
        self.assertEqual(out_i.helper.prepare_decision, 'stock')
        self.assertEqual(out_i.helper.prepare_reason, 'post-attempt-boot')
        self.assertEqual(out_i.helper.attempt, ATTEMPT_1)
        self.assertIn(f'observed-{ATTEMPT_1}', snap_i['state_files'])
        self.assertEqual(snap_i['launched_entries'], ['sdvcam-stub'])
        self.assertFalse(any(c.startswith('mount ') for c in shell_i.calls()))


if __name__ == '__main__':
    unittest.main()
