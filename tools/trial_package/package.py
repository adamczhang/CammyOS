"""Disarmed offline review-package builder and validator (Chunk 6).

Reuses the existing read-only generator, records, classifier/review verifier,
and acceptance specification modules without duplicating their logic:
- tools.boot_launch.generator.generate_proposal (pure proposal generator)
- tools.native_trial.records (strict record parser/renderer)
- tools.native_trial.bundle.classify + tools.native_trial.review.verify
- tools.native_trial.policy + tools.native_acceptance.spec

All generated packages are written ONLY under ignored
private/ownership-program/chunk6/, build/native-trial-review/, or temporary
directories, with mode 0644, no execute bits, no armed TF 'arm' file, and
explicit LOCAL_ONLY/BLOCKED_FOR_ACTIVATION verdicts.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / 'tools'
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from boot_launch import generator as boot_generator  # noqa: E402
from native_acceptance import spec as acceptance_spec  # noqa: E402
from native_trial import bundle as trial_bundle  # noqa: E402
from native_trial import policy as trial_policy  # noqa: E402
from native_trial import records as R  # noqa: E402

PACKAGE_FORMAT = 'cammy-native-trial-review-package/1'
CHECKLIST_FORMAT = 'cammy-native-trial-acceptance-checklist/1'
VERDICT_LOCAL_BLOCKED = 'LOCAL_ONLY/BLOCKED_FOR_ACTIVATION'
VERDICT_REJECTED = 'REJECTED'

PRIVATE_CHUNK6_OUT_ROOT = ROOT / 'private/ownership-program/chunk6'
BUILD_REVIEW_OUT_ROOT = ROOT / 'build/native-trial-review'
DEFAULT_PACKAGE_DIR = PRIVATE_CHUNK6_OUT_ROOT / 'review-package'

# Pinned read-only inputs.
SAVED_STOCK_S01APP = boot_generator.SAVED_STOCK_S01APP
SAVED_STOCK_SDVCAM = Path(
    os.environ.get('CAMMY_STOCK_WORK', str(Path(__file__).resolve().parents[2].parent / 'Cammy Bot/M105 investigation/work')) + '/firmware-rootfs/usr/bin/sdvcam'
)
HELPER_PROPOSAL_PATH = boot_generator.HELPER_PROPOSAL_PATH
SAVED_STATUS_CANDIDATE = ROOT / 'private/ownership-program/wp2a/candidate/sdvcam-status-db34fa035a26'
SAVED_STATUS_LIBRARY = ROOT / 'private/ownership-program/wp2a/library/libcammy_native_status.so'
SAVED_WP5_REVIEW = ROOT / 'private/ownership-program/wp5/real-review/review.json'
SAVED_WP2A_EVIDENCE_ROOT = ROOT / 'private/ownership-program/wp2a'
RUNBOOK_DOC_PATH = ROOT / 'docs/NATIVE-TRIAL-REVIEW.md'

RECIPE_INPUT_PATHS = {
    'boot_launch_generator': ROOT / 'tools/boot_launch/generator.py',
    'helper_proposal': HELPER_PROPOSAL_PATH,
    'wp2a_candidate_builder': ROOT / 'tools/build_native_status_candidate.py',
    'wp2a_status_stub_asm': ROOT / 'native/loader/status_stub.S',
}

# Pinned cryptographic identities.
STOCK_S01APP_SHA256 = trial_policy.STOCK_S01APP_SHA256
STOCK_S01APP_MD5 = trial_policy.STOCK_S01APP_MD5
STOCK_S01APP_SIZE = boot_generator.STOCK_S01APP_SIZE
STOCK_S01APP_LINES = boot_generator.STOCK_S01APP_LINES

STOCK_SDVCAM_SHA256 = trial_policy.STOCK_SDVCAM_SHA256
STOCK_SDVCAM_MD5 = trial_policy.STOCK_SDVCAM_MD5
STOCK_SDVCAM_SIZE = trial_policy.STOCK_SDVCAM_SIZE

EXPECTED_HELPER_PROPOSAL_SHA256 = 'ea98f04f3c35fe3cdc70a9e1196926fd3ef1b4b45f71736669c65ea91d457e9c'
EXPECTED_REPLACEMENT_S01APP_SHA256 = '9147dc54128563d83c4ec7ad08ca598df01b20e52e68a660dcd48c5ab870063c'
EXPECTED_REPLACEMENT_S01APP_MD5 = '625a3dd753225142d668c9501a930e07'
EXPECTED_REPLACEMENT_S01APP_SIZE = 41657
EXPECTED_REPLACEMENT_S01APP_LINES = 1398

STATUS_CANDIDATE_SHA256 = 'a9dedc985cc79d933601390df36b4458784e8dd4807b867cd53cfe0a8a9caba0'
STATUS_LIBRARY_SHA256 = 'db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065'

DEFAULT_TEMPLATE_ATTEMPT = '0' * 31 + '1'
DEFAULT_TEMPLATE_OBSERVED_BOOT = '00000000-0000-4000-8000-000000000000'
DEFAULT_TEMPLATE_ACK_OUTCOME = 'stock-restored'
DISABLED_MODE_TEMPLATE = b'mode=disabled\n'

CANDIDATE_RAM_PATH = '/run/cammy-select/sdvcam'

PACKAGE_MEMBER_SPECS: dict[str, dict] = {
    'package-manifest.json': {
        'role': 'Deterministic hash-bound review package manifest and target mapping',
        'target_plane': 'host-review-only',
        'intended_camera_path': None,
        'intended_installed_owner': None,
        'intended_installed_mode': None,
        'approval_boundary': 'Host review artifact only; never copied to camera or TF card.',
    },
    'internal-bootstrap/S01app.proposal': {
        'role': 'Generated inactive replacement /etc/init.d/S01app proposal (SELECT_GATE=blocked)',
        'target_plane': 'internal-bootstrap',
        'intended_camera_path': '/etc/init.d/S01app',
        'intended_installed_owner': '0:0',
        'intended_installed_mode': '0755',
        'approval_boundary': (
            'Gate E2 (Adam must approve the exact internal write; '
            'as shipped with SELECT_GATE=blocked, supports stock-fallback bootstrap verification only, '
            'never candidate selection).'
        ),
    },
    'internal-bootstrap/S01app.proposal.diff': {
        'role': 'Unified diff from verified stock S01app to generated S01app.proposal',
        'target_plane': 'host-review-only',
        'intended_camera_path': None,
        'intended_installed_owner': None,
        'intended_installed_mode': None,
        'approval_boundary': 'Host review artifact only; never copied to camera or TF card.',
    },
    'internal-bootstrap/native-select.allow': {
        'role': 'Strict external allowlist (cammy-native-allow/2) binding replacement S01app MD5, stock sdvcam, status-only candidate, and library',
        'target_plane': 'internal-bootstrap',
        'intended_camera_path': '/etc/cammy/native-select.allow',
        'intended_installed_owner': '0:0',
        'intended_installed_mode': '0600',
        'approval_boundary': (
            'Gate E2 + Gate E1 (Adam must approve the exact internal write; '
            's01app_md5 binds generated replacement S01app.proposal, not stock S01app input).'
        ),
    },
    'internal-bootstrap/native-select.enabled.disabled-template': {
        'role': 'Disabled selector mode configuration template (mode=disabled)',
        'target_plane': 'internal-bootstrap',
        'intended_camera_path': '/etc/cammy/native-select.enabled',
        'intended_installed_owner': '0:0',
        'intended_installed_mode': '0600',
        'approval_boundary': (
            'Gate E2 (disabled by default; missing or mode=disabled skips TF probing and flash state writes).'
        ),
    },
    'rollback/S01app.stock': {
        'role': 'Exact pristine stock /etc/init.d/S01app rollback copy matching saved rootfs and live camera snapshot',
        'target_plane': 'internal-rollback',
        'intended_camera_path': '/etc/init.d/S01app',
        'intended_installed_owner': '0:0',
        'intended_installed_mode': '0755',
        'approval_boundary': (
            'Rollback restoration material for /etc/init.d/S01app; must verify SHA256/MD5/size before and after restore.'
        ),
    },
    'tf-staging/CammyOS-native/candidate.bin': {
        'role': 'Status-only native candidate binary (sdvcam-status-db34fa035a26; staged to /run/cammy-select/sdvcam at runtime only)',
        'target_plane': 'tf-card',
        'intended_camera_path': '/mnt/extsd/CammyOS-native/candidate.bin',
        'intended_installed_owner': 'vfat-mount',
        'intended_installed_mode': '0644',
        'approval_boundary': (
            'Gates EA, E1, E2, E3, E4, E6, E7 (inert on TF without an approved live gate and separate arm record; '
            'if ever selected in a future approved trial, copied to /run/cammy-select/sdvcam mode 0500 root:root).'
        ),
    },
    'tf-staging/CammyOS-native/libcammy_native_status.so': {
        'role': 'Status-only native bridge shared library (staged to /tmp/cammy-native-status/<sha256>/libcammy_native_status.so at runtime only)',
        'target_plane': 'tf-card',
        'intended_camera_path': '/mnt/extsd/CammyOS-native/libcammy_native_status.so',
        'intended_installed_owner': 'vfat-mount',
        'intended_installed_mode': '0644',
        'approval_boundary': (
            'Gates EA, E1, E2, E3, E4, E6, E7 (if ever selected in a future approved trial, '
            'copied to /tmp/cammy-native-status/<sha256>/libcammy_native_status.so mode 0444 root:root).'
        ),
    },
    'templates/arm.template': {
        'role': 'NON-CONSUMED one-shot arm record template (cammy-native-arm/2; NOT placed in tf-staging/CammyOS-native/arm)',
        'target_plane': 'tf-template-not-staged',
        'intended_camera_path': '/mnt/extsd/CammyOS-native/arm',
        'intended_installed_owner': 'vfat-mount',
        'intended_installed_mode': '0644',
        'approval_boundary': (
            'Template only (not armed, not staged under tf-staging/). Live arming is blocked until Gates EA and E1-E7 close.'
        ),
    },
    'templates/ack.template': {
        'role': 'NON-CONSUMED post-attempt acknowledgment template (cammy-native-ack/1; placeholder UUID must be replaced by real observed_boot)',
        'target_plane': 'tf-template-not-staged',
        'intended_camera_path': f'/mnt/extsd/CammyOS-native/ack-{DEFAULT_TEMPLATE_ATTEMPT}',
        'intended_installed_owner': 'vfat-mount',
        'intended_installed_mode': '0644',
        'approval_boundary': (
            'Template only (not consumed, placeholder observed_boot UUID; real re-arm requires quoting actual observed-<attempt> boot UUID).'
        ),
    },
    'provenance/launch_selected_or_stock.proposal': {
        'role': 'Exact helper proposal source (SELECT_GATE=blocked) embedded by generator into S01app.proposal',
        'target_plane': 'host-review-only',
        'intended_camera_path': None,
        'intended_installed_owner': None,
        'intended_installed_mode': None,
        'approval_boundary': 'Host provenance artifact only; never copied to camera or TF card.',
    },
    'provenance/review.json': {
        'role': 'SHA256-bound WP2A/WP5 offline review and ABI proof record (cammy-native-review/1)',
        'target_plane': 'host-review-only',
        'intended_camera_path': None,
        'intended_installed_owner': None,
        'intended_installed_mode': None,
        'approval_boundary': 'Host provenance artifact only (evidence_class remains offline/fixture; E1 manager closure pending).',
    },
    'review/acceptance-checklist.json': {
        'role': 'Machine-readable stage-5 status-only acceptance checklist with pending hardware observation slots (unpopulated)',
        'target_plane': 'host-review-only',
        'intended_camera_path': None,
        'intended_installed_owner': None,
        'intended_installed_mode': None,
        'approval_boundary': 'Host review checklist only; hardware stages remain PENDING_HARDWARE_NOT_RUN.',
    },
    'review/NATIVE-TRIAL-REVIEW.md': {
        'role': 'Concise operator review and stock rollback runbook',
        'target_plane': 'host-review-only',
        'intended_camera_path': None,
        'intended_installed_owner': None,
        'intended_installed_mode': None,
        'approval_boundary': 'Host operator runbook for human/manager review only.',
    },
}

PACKAGE_MEMBERS = tuple(PACKAGE_MEMBER_SPECS.keys())
NON_MANIFEST_MEMBERS = tuple(m for m in PACKAGE_MEMBERS if m != 'package-manifest.json')


class TrialPackageError(ValueError):
    """Raised when review-package inputs, output paths, or invariants fail."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _blob_hashes(data: bytes, *, count_lines: bool = False) -> dict:
    info = {
        'sha256': _sha256_bytes(data),
        'md5': _md5_bytes(data),
        'size': len(data),
    }
    if count_lines:
        info['lines'] = len(data.splitlines())
    return info


def _tree_sha256(member_blobs: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for rel_path in sorted(member_blobs):
        if rel_path == 'package-manifest.json':
            continue
        blob = member_blobs[rel_path]
        entry = f'{rel_path}:{len(blob)}:{_sha256_bytes(blob)}:{_md5_bytes(blob)}\n'
        digest.update(entry.encode('ascii'))
    return digest.hexdigest()


def _read_regular_file(path: Path, label: str) -> bytes:
    p = Path(path)
    try:
        st = os.lstat(p)
    except OSError as exc:
        raise TrialPackageError(f'{label} missing or unreadable ({p}): {exc}') from exc
    if stat.S_ISLNK(st.st_mode):
        raise TrialPackageError(f'{label} must not be a symlink: {p}')
    if not stat.S_ISREG(st.st_mode):
        raise TrialPackageError(f'{label} must be a regular file: {p}')
    return p.read_bytes()


def _detect_git_head(repo_root: Path = ROOT) -> str:
    try:
        proc = subprocess.run(
            ['git', '-C', str(repo_root), 'rev-parse', 'HEAD'],
            check=True,
            capture_output=True,
            text=True,
        )
        commit = proc.stdout.strip()
        if len(commit) == 40 and all(c in '0123456789abcdef' for c in commit):
            return commit
    except Exception as exc:
        raise TrialPackageError(f'unable to resolve git HEAD commit for provenance: {exc}') from exc
    raise TrialPackageError('invalid git HEAD commit output')


def _assert_no_symlink_chain(path: Path) -> None:
    raw = Path(os.path.abspath(os.fspath(path)))
    cur = Path(raw.anchor)
    for part in raw.parts[1:]:
        cur = cur / part
        if cur.exists() or cur.is_symlink():
            if cur.is_symlink():
                raise TrialPackageError(f'refusing path traversing symlink component: {cur}')


def _assert_allowed_output_dir(out_dir: Path) -> Path:
    raw_parts = Path(out_dir).parts
    if '..' in raw_parts:
        raise TrialPackageError(f'refusing output path with parent traversal (..): {out_dir}')
    _assert_no_symlink_chain(Path(out_dir))
    resolved = Path(os.path.abspath(os.fspath(out_dir))).resolve()

    chunk6_root = PRIVATE_CHUNK6_OUT_ROOT.resolve()
    build_root = BUILD_REVIEW_OUT_ROOT.resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()

    in_chunk6 = chunk6_root in resolved.parents
    in_build = resolved == build_root or build_root in resolved.parents
    in_tmp = tmp_root in resolved.parents

    if not (in_chunk6 or in_build or in_tmp):
        raise TrialPackageError(
            f'refusing output directory outside private/ownership-program/chunk6/, '
            f'build/native-trial-review/, or temp dir: {out_dir}'
        )
    if resolved.exists():
        if not resolved.is_dir() or resolved.is_symlink():
            raise TrialPackageError(f'output destination is not a regular directory: {out_dir}')
        if any(resolved.iterdir()):
            raise TrialPackageError(f'refusing to overwrite non-empty output directory: {out_dir}')
    return resolved


def _ensure_dir_no_symlink(base: Path, rel_dir: PurePosixPath) -> Path:
    cur = base
    cur.mkdir(parents=True, exist_ok=True)
    if cur.is_symlink():
        raise TrialPackageError(f'output root is a symlink: {cur}')
    for part in rel_dir.parts:
        if part in ('', '.', '..'):
            raise TrialPackageError(f'invalid relative directory component: {rel_dir}')
        cur = cur / part
        if cur.is_symlink():
            raise TrialPackageError(f'refusing symlinked directory inside package: {cur}')
        if not cur.exists():
            cur.mkdir(mode=0o755)
        elif not cur.is_dir():
            raise TrialPackageError(f'non-directory obstructs package directory path: {cur}')
    return cur


def _write_inert_0644(root: Path, rel_posix: str, data: bytes) -> Path:
    rel = PurePosixPath(rel_posix)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise TrialPackageError(f'invalid package member relative path: {rel_posix}')
    if rel_posix not in PACKAGE_MEMBER_SPECS:
        raise TrialPackageError(f'member not in package whitelist: {rel_posix}')
    parent = _ensure_dir_no_symlink(root, rel.parent)
    target = parent / rel.name
    if target.exists() or target.is_symlink():
        raise TrialPackageError(f'refusing to overwrite existing package member: {target}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(target, flags, 0o644)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        os.fchmod(handle.fileno(), 0o644)
        handle.flush()
        os.fsync(handle.fileno())
    st = os.lstat(target)
    if not stat.S_ISREG(st.st_mode) or (st.st_mode & 0o777) != 0o644 or (st.st_mode & 0o111) != 0:
        raise TrialPackageError(f'written member failed mode 0644 check: {target}')
    if st.st_nlink != 1:
        raise TrialPackageError(f'written member has unexpected hardlink count {st.st_nlink}: {target}')
    return target


def _package_gates_report() -> dict:
    """Describe the revised S01app route without changing legacy WP4 policy."""
    report = trial_policy.gates_report()
    report['decision'] = {
        'selected_route': 'S01app launch substitution (offline proposal)',
        'activation': 'blocked; no approved live gate or internal installation',
        'mechanism': ('Preserve stock startup setup and route all five launch sites through '
                      'one verified stock-or-RAM launcher; never mount over stock sdvcam.'),
        'media_authorization': 'Status-only selection grants no media authority.',
        'late_handoff_status': 'ineligible; no warm kill/restart route',
    }
    requirements = {
        'EA': 'Review the revised S01app substitution and approve a separate live gate; the shipped gate remains blocked.',
        'E2': ('Adam approves the exact S01app replacement, native-select.allow, disabled '
               'enable file and private state directory, with reviewed rollback. Separate from TF updates.'),
        'E3': ('Late-boot inventory is partial. Verify actual early S01app shell semantics, '
               'exclusive creation, card availability, memory and watchdog/card-I/O timing.'),
        'E4': ('Separately authorized stock-fallback bootstrap check, followed by any future '
               'approved observe-mode check: one verified stock app, detached private TF mount, '
               'normal LCD/home Wi-Fi and bounded startup time.'),
        'E6': ('Verify physical JFFS2 record durability across power loss and stock restore: '
               'next boot records post-attempt-boot, card-removed boot is stock, and ack binds '
               'the observed boot. Sync/read-back alone is not durability proof.'),
        'E7': ('Verify the candidate subshell enforces ulimit -c 0 before exec on hardware; '
               'stock core policy stays unchanged. No candidate core may fill internal storage.'),
    }
    for gate in report['early_selector_gates']:
        if gate['id'] in requirements:
            gate['requirement'] = requirements[gate['id']]
    report['e3_live_facts'] = {
        'source': 'docs/CAMERA-COMPATIBILITY-2026-11-14.md',
        'scope': 'read-only late-boot metadata; no early-boot acceptance',
    }
    return report


def _build_acceptance_checklist(
    *,
    attempt: str,
    allowlist_sha256: str,
    runbook_sha256: str,
    rollback_sha256: str,
    replacement_s01app_sha256: str,
    replacement_s01app_md5: str,
    classification: dict,
    review_sha256: str,
) -> dict:
    spec_desc = acceptance_spec.describe()
    gates_info = _package_gates_report()
    lib = classification['library']
    review_info = classification['review']
    return {
        'format': CHECKLIST_FORMAT,
        'acceptance_spec_format': spec_desc['format'],
        'profile': R.CLASS_STATUS_ONLY,
        'verdict': VERDICT_LOCAL_BLOCKED,
        'review_only': True,
        'activation_eligible': False,
        'installation_authorized': False,
        'deployed': False,
        'hardware_performed': False,
        'hardware_pass': False,
        'local_only': True,
        'select_gate': 'blocked',
        'runbook_sha256': runbook_sha256,
        'allowlist_sha256': allowlist_sha256,
        'rollback_s01app_sha256': rollback_sha256,
        'replacement_s01app_sha256': replacement_s01app_sha256,
        'replacement_s01app_md5': replacement_s01app_md5,
        'template_attempt': attempt,
        'thresholds': spec_desc['thresholds'],
        'evidence_separation_contract': {
            'status_record': {
                'path': '/run/cammy-select/status',
                'format': 'cammy-boot-launch-status/1',
                'meaning': 'PREPARATION ONLY (immutable preparation decision before launch phase)',
                'proves_exec_attempt': False,
                'proves_running_process': False,
                'proves_bridge_readiness': False,
            },
            'launch_stamp': {
                'path': '/run/cammy-select/launch.stamp',
                'format': 'cammy-boot-launch-stamp/1',
                'meaning': 'EXEC ATTEMPT ONLY (written immediately before background subshell exec)',
                'proves_running_process': False,
                'proves_bridge_readiness': False,
            },
            'independent_camera_observations_required': [
                'Single sdvcam PID and starttime in /proc/<pid>/stat with /proc/<pid>/exe -> /run/cammy-select/sdvcam and SHA256 matching candidate',
                '/proc/<pid>/limits Max core file size == 0 (ulimit -c 0 verified on hardware)',
                '/proc/<pid>/maps entry for /tmp/cammy-native-status/<sha256>/libcammy_native_status.so with SHA256 matching library',
                '/proc/<pid>/fd/3 -> /dev/watchdog held by that single PID across >= 600s with 0 resets and unchanged boot_id',
                'Home Wi-Fi unchanged, HTTP portal reachable, LCD normal view confirmed',
                'Native bridge socket under /tmp/cammy-bridge responds to HELLO/STATUS with backend_kind=2, hardware_actions_enabled=false, physical_capabilities=all-unavailable, media.sessions_started=0',
            ],
        },
        'host_stages': {
            'planned': {
                'kind': 'host',
                'status': 'LOCAL_ONLY',
                'plan_ref': 'docs/work-packages/CHUNK3-BOOT-LAUNCH-CONTRACT.md',
                'runbook_ref': 'docs/NATIVE-TRIAL-REVIEW.md',
                'runbook_sha256': runbook_sha256,
                'rollback_ref': 'rollback/S01app.stock',
                'rollback_s01app_sha256': rollback_sha256,
            },
            'staged': {
                'kind': 'host',
                'status': 'LOCAL_ONLY',
                'installed': False,
                'deployed': False,
                'local_only': True,
                'candidate_sha256': classification['candidate_sha256'],
                'library_sha256': lib['sha256'],
                'library_ram_path': lib['ram_path'],
                'candidate_ram_path': CANDIDATE_RAM_PATH,
            },
            'structurally-classified': {
                'kind': 'host',
                'status': 'LOCAL_ONLY',
                'structural_class': classification['structural_class'],
                'structural_ok': classification['structural_ok'],
                'connect_trigger_sites_modified': classification['connect_trigger_sites_modified'],
                'reproduced_by_wp2a_generator': bool(
                    classification.get('reproduction') and classification['reproduction'].get('identical')
                ),
                'source_sha256': classification['source']['sha256'],
                'candidate_sha256': classification['candidate_sha256'],
            },
            'abi-proved': {
                'kind': 'host',
                'status': 'LOCAL_ONLY',
                'review_sha256': review_sha256,
                'review_evidence_class': review_info['evidence_class'],
                'local_only': review_info['local_only'],
                'proofs': {name: 'pass' for name in acceptance_spec.ABI_PROOFS['status-only']},
                'note': 'Offline QEMU/saved-libc and static proofs only; E1 manager closure and hardware proofs remain open.',
            },
        },
        'hardware_stages': {
            'selected-boot': {
                'kind': 'hardware',
                'status': 'PENDING_HARDWARE_NOT_RUN',
                'hardware_performed': False,
                'boot_id': None,
                'observations': {
                    'selector_status': {
                        'decision': None,
                        'reason': None,
                        'mode': None,
                        'attempt': None,
                        'boot': None,
                    },
                    'launch_stamp': {
                        'target': None,
                        'attempt': None,
                        'app': None,
                        'md5': None,
                        'size': None,
                        'lib_sha256': None,
                    },
                    'native_apps': None,
                    'core_limit_zero_verified': None,
                    'library_mapping': {
                        'pid': None,
                        'path': lib['ram_path'],
                        'mapped_sha256': None,
                    },
                    'watchdog': {
                        'holder_pid': None,
                        'window_s': None,
                        'required_min_window_s': acceptance_spec.WATCHDOG_WINDOW_S,
                        'resets': None,
                        'boot_id_after': None,
                    },
                    'network': {
                        'home_wifi_unchanged': None,
                        'portal_reachable': None,
                    },
                    'lcd': {
                        'normal_view': None,
                    },
                    'bridge': {
                        'hello_ok': None,
                        'backend_kind': None,
                        'expected_backend_kind': acceptance_spec.BRIDGE_BACKEND_NATIVE,
                        'boot_id': None,
                        'hardware_actions_enabled': None,
                        'physical_capabilities': None,
                    },
                    'media': {
                        'sessions_started': None,
                    },
                },
            },
            'cleanup': {
                'kind': 'hardware',
                'status': 'PENDING_HARDWARE_NOT_RUN',
                'hardware_performed': False,
                'boot_id': None,
                'observations': {
                    'bridge_session_closed': None,
                    'orphan_processes': None,
                    'unexpected_listeners': None,
                    'media_sessions': None,
                    'disarm': {
                        'method': None,
                        'verified': None,
                    },
                },
            },
            'stock-restore': {
                'kind': 'hardware',
                'status': 'PENDING_HARDWARE_NOT_RUN',
                'hardware_performed': False,
                'boot_id': None,
                'observations': {
                    'post_attempt': {
                        'boot_id': None,
                        'selector_status': None,
                        'observed_record_boot': None,
                        'native_apps_exe_sha256': None,
                        'lcd_normal': None,
                        'home_wifi': None,
                    },
                    'card_removed': {
                        'boot_id': None,
                        'card_present': None,
                        'native_apps_exe_sha256': None,
                        'selector_decision': None,
                        'lcd_normal': None,
                        'home_wifi': None,
                    },
                    'ack': {
                        'attempt': None,
                        'observed_boot': None,
                        'outcome': None,
                    },
                    'intentional_selector_state_records': [
                        '/etc/cammy/native-select/inflight (transient during TF probe)',
                        '/etc/cammy/native-select/claimed-<attempt>',
                        '/etc/cammy/native-select/observed-<attempt>',
                    ],
                    'durable_side_effects': None,
                },
            },
        },
        'open_gates': gates_info['early_selector_gates'],
        'late_handoff_gates': gates_info['late_handoff_gates'],
    }


def _build_target_layout(lib_sha256: str, attempt: str) -> dict:
    lib_ram = R.library_path(lib_sha256)
    return {
        'host_artifact_ownership_note': (
            'All files in this review package are host-owned mode 0644 files on the build machine. '
            'They do NOT possess root:root (0:0) camera ownership or executable permissions.'
        ),
        'internal_directories': [
            {
                'camera_path': '/etc/init.d',
                'intended_owner': '0:0',
                'intended_mode': '0755',
                'precondition': 'Existing non-symlink init directory on rootfs/internal overlay.',
            },
            {
                'camera_path': '/etc/cammy',
                'intended_owner': '0:0',
                'intended_mode': '0755',
                'precondition': 'Parent configuration directory on internal writable JFFS2 overlay (non-symlink).',
            },
            {
                'camera_path': '/etc/cammy/native-select',
                'intended_owner': '0:0',
                'intended_mode': '0700',
                'precondition': (
                    'Private non-symlink state directory (checked by cammy_dir_private_ok: drwx------ 0:0). '
                    'Stores intentional flash state records (inflight, claimed-<attempt>, observed-<attempt>).'
                ),
            },
        ],
        'internal_files': [
            {
                'camera_path': '/etc/init.d/S01app',
                'package_member': 'internal-bootstrap/S01app.proposal',
                'rollback_member': 'rollback/S01app.stock',
                'intended_owner': '0:0',
                'intended_mode': '0755',
                'gate': 'E2',
            },
            {
                'camera_path': '/etc/cammy/native-select.allow',
                'package_member': 'internal-bootstrap/native-select.allow',
                'intended_owner': '0:0',
                'intended_mode': '0600',
                'gate': 'E2',
            },
            {
                'camera_path': '/etc/cammy/native-select.enabled',
                'package_member': 'internal-bootstrap/native-select.enabled.disabled-template',
                'intended_owner': '0:0',
                'intended_mode': '0600',
                'default_content': 'mode=disabled',
                'gate': 'E2',
            },
        ],
        'tf_card_paths': [
            {
                'card_path': '/mnt/extsd/CammyOS-native/candidate.bin',
                'probed_path': '/run/cammy-select/tf/CammyOS-native/candidate.bin',
                'package_member': 'tf-staging/CammyOS-native/candidate.bin',
                'staged_in_package': True,
            },
            {
                'card_path': '/mnt/extsd/CammyOS-native/libcammy_native_status.so',
                'probed_path': '/run/cammy-select/tf/CammyOS-native/libcammy_native_status.so',
                'package_member': 'tf-staging/CammyOS-native/libcammy_native_status.so',
                'staged_in_package': True,
            },
            {
                'card_path': '/mnt/extsd/CammyOS-native/arm',
                'probed_path': '/run/cammy-select/tf/CammyOS-native/arm',
                'package_member': 'templates/arm.template',
                'staged_in_package': False,
                'note': 'Non-consumed template only; NOT placed in tf-staging/CammyOS-native/.',
            },
            {
                'card_path': f'/mnt/extsd/CammyOS-native/ack-{attempt}',
                'probed_path': f'/run/cammy-select/tf/CammyOS-native/ack-{attempt}',
                'package_member': 'templates/ack.template',
                'staged_in_package': False,
                'note': 'Non-consumed template only; requires real observed-<attempt> boot UUID.',
            },
        ],
        'ram_runtime_paths': [
            {
                'camera_path': '/run/cammy-select',
                'kind': 'directory',
                'intended_owner': '0:0',
                'intended_mode': '0700',
                'filesystem': 'tmpfs (without noexec)',
            },
            {
                'camera_path': '/run/cammy-select/tf',
                'kind': 'temporary-mountpoint',
                'intended_owner': '0:0',
                'intended_mode': '0700',
                'mount_options': 'vfat ro,nosuid,nodev,noexec (detached and rmdired before claim/launch)',
            },
            {
                'camera_path': CANDIDATE_RAM_PATH,
                'kind': 'executable-copy',
                'intended_owner': '0:0',
                'intended_mode': '0500',
                'note': 'Candidate staged in private RAM only; never bind-mounted over /usr/bin/sdvcam.',
            },
            {
                'camera_path': '/run/cammy-select/status',
                'kind': 'preparation-record',
                'intended_owner': '0:0',
                'intended_mode': '0600',
                'note': 'PREPARATION ONLY (cammy-boot-launch-status/1); does not prove exec or running process.',
            },
            {
                'camera_path': '/run/cammy-select/launch.stamp',
                'kind': 'exec-attempt-record',
                'intended_owner': '0:0',
                'intended_mode': '0600',
                'note': 'EXEC ATTEMPT ONLY (cammy-boot-launch-stamp/1); prevents second app launch in same boot.',
            },
            {
                'camera_path': '/tmp/cammy-native-status',
                'kind': 'directory',
                'intended_owner': '0:0',
                'intended_mode': '0700',
                'filesystem': 'tmpfs (without noexec)',
            },
            {
                'camera_path': f'/tmp/cammy-native-status/{lib_sha256}',
                'kind': 'directory',
                'intended_owner': '0:0',
                'intended_mode': '0700',
                'filesystem': 'tmpfs (without noexec)',
            },
            {
                'camera_path': lib_ram,
                'kind': 'shared-library-copy',
                'intended_owner': '0:0',
                'intended_mode': '0444',
                'note': 'Exact full-SHA256 and underscore path baked into candidate stub.',
            },
            {
                'camera_path': '/tmp/cammy-bridge',
                'kind': 'socket-directory',
                'intended_owner': '0:0',
                'intended_mode': '0700',
                'filesystem': 'tmpfs (without noexec)',
            },
        ],
        'out_of_scope': [
            'Replacing /usr/bin/sdvcam in rootfs/overlay or bind-mounting over /usr/bin/sdvcam',
            'Kernel, bootloader, or full SPI NOR flash image replacement',
            'Media-connect or media-lease-gated activation (Gate E8 blocked)',
            'Warm process restart, SIGTERM/SIGKILL handoff, or /etc/init.d/S01app stop/restart (Gates L1-L5 open)',
        ],
    }


def build_review_package(
    out_dir: Path = DEFAULT_PACKAGE_DIR,
    *,
    stock_s01app_path: Path = SAVED_STOCK_S01APP,
    stock_sdvcam_path: Path = SAVED_STOCK_SDVCAM,
    helper_path: Path = HELPER_PROPOSAL_PATH,
    candidate_path: Path = SAVED_STATUS_CANDIDATE,
    library_path: Path = SAVED_STATUS_LIBRARY,
    review_path: Path = SAVED_WP5_REVIEW,
    evidence_root: Path = SAVED_WP2A_EVIDENCE_ROOT,
    runbook_path: Path = RUNBOOK_DOC_PATH,
    attempt: str = DEFAULT_TEMPLATE_ATTEMPT,
    observed_boot_placeholder: str = DEFAULT_TEMPLATE_OBSERVED_BOOT,
    ack_outcome: str = DEFAULT_TEMPLATE_ACK_OUTCOME,
) -> dict:
    """Build a deterministic, disarmed, mode-0644 offline review package."""
    target_dir = _assert_allowed_output_dir(Path(out_dir))

    if not R.is_hex32(attempt):
        raise TrialPackageError('template attempt must be 32 lowercase hex characters')
    if not R.is_uuid(observed_boot_placeholder):
        raise TrialPackageError('observed_boot_placeholder must be a 36-char UUID string')
    if ack_outcome not in R.ACK_OUTCOMES:
        raise TrialPackageError(f'ack_outcome must be one of {R.ACK_OUTCOMES}')

    stock_s01_bytes = _read_regular_file(stock_s01app_path, 'stock S01app')
    helper_bytes = _read_regular_file(helper_path, 'helper proposal')
    _read_regular_file(stock_sdvcam_path, 'stock sdvcam')
    candidate_bytes = _read_regular_file(candidate_path, 'status-only candidate')
    library_bytes = _read_regular_file(library_path, 'status-only library')
    review_bytes = _read_regular_file(review_path, 'review record')
    runbook_bytes = _read_regular_file(runbook_path, 'operator runbook')

    try:
        proposal = boot_generator.generate_proposal(stock_s01_bytes, helper_bytes)
    except (boot_generator.SourceVerificationError, UnicodeDecodeError) as exc:
        raise TrialPackageError(f'boot_launch proposal generation failed: {exc}') from exc

    if proposal.source_sha256 != STOCK_S01APP_SHA256 or proposal.source_md5 != STOCK_S01APP_MD5:
        raise TrialPackageError('stock S01app digest mismatch against pinned policy')
    if proposal.helper_sha256 != EXPECTED_HELPER_PROPOSAL_SHA256:
        raise TrialPackageError(
            f'helper proposal sha256 mismatch: expected {EXPECTED_HELPER_PROPOSAL_SHA256}, '
            f'got {proposal.helper_sha256}'
        )
    if (
        proposal.replacement_sha256 != EXPECTED_REPLACEMENT_S01APP_SHA256
        or proposal.replacement_md5 != EXPECTED_REPLACEMENT_S01APP_MD5
        or proposal.replacement_size != EXPECTED_REPLACEMENT_S01APP_SIZE
        or proposal.replacement_lines != EXPECTED_REPLACEMENT_S01APP_LINES
    ):
        raise TrialPackageError(
            'generated S01app.proposal identity differs from pinned portable-cleanup revision: '
            f'sha256={proposal.replacement_sha256} md5={proposal.replacement_md5} '
            f'size={proposal.replacement_size} lines={proposal.replacement_lines}'
        )

    rep_lines = proposal.replacement_text.splitlines(keepends=True)
    if 'SELECT_GATE=blocked\n' not in rep_lines:
        raise TrialPackageError('generated S01app.proposal does not pin SELECT_GATE=blocked')
    if any(line.strip().startswith('SELECT_GATE=') and line.strip() != 'SELECT_GATE=blocked' for line in rep_lines):
        raise TrialPackageError('generated S01app.proposal contains a non-blocked SELECT_GATE assignment')

    classification = trial_bundle.classify(
        Path(candidate_path),
        Path(stock_sdvcam_path),
        R.CLASS_STATUS_ONLY,
        Path(library_path),
        Path(review_path),
        Path(evidence_root),
        reproduce=True,
    )
    if not classification.get('structural_ok') or classification.get('structural_class') != R.CLASS_STATUS_ONLY:
        raise TrialPackageError(
            'candidate failed status-only structural classification: '
            + '; '.join(classification.get('reasons', []))
        )
    if classification.get('connect_trigger_sites_modified') != []:
        raise TrialPackageError('candidate modifies media connect-trigger sites; ineligible')
    if not classification.get('eligible_for_staging'):
        raise TrialPackageError(
            'candidate/review not eligible for offline staging: ' + '; '.join(classification.get('reasons', []))
        )
    repro = classification.get('reproduction') or {}
    if repro.get('available') and repro.get('identical') is not True:
        raise TrialPackageError('candidate is not byte-identical to approved WP2A generator output')

    if classification['source']['sha256'] != STOCK_SDVCAM_SHA256:
        raise TrialPackageError('stock sdvcam sha256 mismatch against pinned policy')
    if classification['candidate_sha256'] != STATUS_CANDIDATE_SHA256:
        raise TrialPackageError(
            f'status candidate sha256 mismatch: expected {STATUS_CANDIDATE_SHA256}, '
            f'got {classification["candidate_sha256"]}'
        )
    lib_info = classification['library']
    if lib_info['sha256'] != STATUS_LIBRARY_SHA256:
        raise TrialPackageError(
            f'status library sha256 mismatch: expected {STATUS_LIBRARY_SHA256}, got {lib_info["sha256"]}'
        )
    expected_lib_ram_path = R.library_path(lib_info['sha256'])
    if lib_info['ram_path'] != expected_lib_ram_path:
        raise TrialPackageError('library RAM path mismatch')

    review_info = classification.get('review') or {}
    if not review_info.get('ok'):
        raise TrialPackageError('review record verification failed')
    if review_info.get('evidence_class') not in ('offline', 'fixture'):
        raise TrialPackageError(
            f'refusing to package unverified or promoted review evidence_class {review_info.get("evidence_class")!r}'
        )
    if classification.get('local_only') is not True or classification.get('activation_eligible') is not False:
        raise TrialPackageError('classification must remain local_only=True and activation_eligible=False')

    # Derive external allowlist AFTER generating final S01app.proposal (no hash cycle).
    # s01app_md5 MUST bind the generated replacement S01app.proposal, NEVER stock S01app.
    allow_bytes = R.allow_record(
        proposal.replacement_md5,
        classification['source']['md5'],
        classification['source']['size'],
        classification['candidate_md5'],
        classification['candidate_size'],
        R.CLASS_STATUS_ONLY,
        lib_info['sha256'],
        lib_info['md5'],
        lib_info['size'],
    )
    arm_template_bytes = R.arm_record(
        attempt,
        classification['candidate_md5'],
        classification['candidate_size'],
        R.CLASS_STATUS_ONLY,
        lib_info['sha256'],
        lib_info['md5'],
        lib_info['size'],
    )
    ack_template_bytes = R.ack_record(attempt, observed_boot_placeholder, ack_outcome)

    checklist_data = _build_acceptance_checklist(
        attempt=attempt,
        allowlist_sha256=_sha256_bytes(allow_bytes),
        runbook_sha256=_sha256_bytes(runbook_bytes),
        rollback_sha256=proposal.source_sha256,
        replacement_s01app_sha256=proposal.replacement_sha256,
        replacement_s01app_md5=proposal.replacement_md5,
        classification=classification,
        review_sha256=_sha256_bytes(review_bytes),
    )
    checklist_bytes = (json.dumps(checklist_data, indent=2, sort_keys=True) + '\n').encode('ascii')

    member_blobs: dict[str, bytes] = {
        'internal-bootstrap/S01app.proposal': proposal.replacement_text.encode('ascii'),
        'internal-bootstrap/S01app.proposal.diff': proposal.unified_diff.encode('ascii'),
        'internal-bootstrap/native-select.allow': allow_bytes,
        'internal-bootstrap/native-select.enabled.disabled-template': DISABLED_MODE_TEMPLATE,
        'rollback/S01app.stock': stock_s01_bytes,
        'tf-staging/CammyOS-native/candidate.bin': candidate_bytes,
        'tf-staging/CammyOS-native/libcammy_native_status.so': library_bytes,
        'templates/arm.template': arm_template_bytes,
        'templates/ack.template': ack_template_bytes,
        'provenance/launch_selected_or_stock.proposal': helper_bytes,
        'provenance/review.json': review_bytes,
        'review/acceptance-checklist.json': checklist_bytes,
        'review/NATIVE-TRIAL-REVIEW.md': runbook_bytes,
    }

    line_counted_members = {
        'internal-bootstrap/S01app.proposal',
        'internal-bootstrap/S01app.proposal.diff',
        'internal-bootstrap/native-select.allow',
        'internal-bootstrap/native-select.enabled.disabled-template',
        'rollback/S01app.stock',
        'templates/arm.template',
        'templates/ack.template',
        'provenance/launch_selected_or_stock.proposal',
        'review/NATIVE-TRIAL-REVIEW.md',
    }

    members_manifest: dict[str, dict] = {}
    for rel_path in NON_MANIFEST_MEMBERS:
        spec = PACKAGE_MEMBER_SPECS[rel_path]
        blob = member_blobs[rel_path]
        hashes = _blob_hashes(blob, count_lines=rel_path in line_counted_members)
        members_manifest[rel_path] = {
            **hashes,
            'package_mode': '0644',
            'executable_in_package': False,
            'host_artifact_owner_is_not_camera_root': True,
            'role': spec['role'],
            'target_plane': spec['target_plane'],
            'intended_camera_path': spec['intended_camera_path'],
            'intended_installed_owner': spec['intended_installed_owner'],
            'intended_installed_mode': spec['intended_installed_mode'],
            'approval_boundary': spec['approval_boundary'],
        }

    recipe_hashes = {}
    for name, rpath in sorted(RECIPE_INPUT_PATHS.items()):
        rbytes = _read_regular_file(rpath, f'recipe input {name}')
        recipe_hashes[name] = {
            'path': str(rpath.relative_to(ROOT)),
            'sha256': _sha256_bytes(rbytes),
            'size': len(rbytes),
        }

    review_json = json.loads(review_bytes)
    repo_head_commit = _detect_git_head(ROOT)
    tree_sha256 = _tree_sha256(member_blobs)

    manifest = {
        'format': PACKAGE_FORMAT,
        'chunk': 'chunk6',
        'status': 'disarmed-offline-review-package',
        'validation_verdict': VERDICT_LOCAL_BLOCKED,
        'review_only': True,
        'activation_eligible': False,
        'installation_authorized': False,
        'deployed': False,
        'installed': False,
        'hardware_validated': False,
        'local_only': True,
        'select_gate': 'blocked',
        'live_activation_path_approved': False,
        'fixture_gate_note': (
            'SELECT_GATE=chunk5-fixture-open is for synthetic host test relocation only. '
            'NEVER use it on a camera, NEVER edit or bypass SELECT_GATE=blocked, and '
            'NEVER emit an enabled helper without a separately approved and committed gate.'
        ),
        'contents_count': len(PACKAGE_MEMBERS),
        'non_manifest_member_count': len(NON_MANIFEST_MEMBERS),
        'package_tree_sha256': tree_sha256,
        'identities': {
            'stock_s01app': {
                'role': 'build-time exact source input and exact rollback copy (rollback/S01app.stock)',
                'sha256': proposal.source_sha256,
                'md5': proposal.source_md5,
                'size': proposal.source_size,
                'lines': proposal.source_lines,
            },
            'helper_proposal': {
                'role': 'embedded boot-launch helper proposal with SELECT_GATE=blocked',
                'path': 'native/activation/launch_selected_or_stock.proposal',
                'sha256': proposal.helper_sha256,
                'md5': proposal.helper_md5,
                'size': proposal.helper_size,
                'lines': len(helper_bytes.splitlines()),
            },
            'replacement_s01app': {
                'role': 'generated inactive replacement /etc/init.d/S01app proposal (bound by allowlist s01app_md5)',
                'sha256': proposal.replacement_sha256,
                'md5': proposal.replacement_md5,
                'size': proposal.replacement_size,
                'lines': proposal.replacement_lines,
            },
            'stock_sdvcam': {
                'role': 'runtime stock /usr/bin/sdvcam verified before any fallback launch; never replaced',
                'sha256': classification['source']['sha256'],
                'md5': classification['source']['md5'],
                'size': classification['source']['size'],
            },
            'status_candidate': {
                'role': 'status-only candidate executable staged to /run/cammy-select/sdvcam in future approved trial',
                'sha256': classification['candidate_sha256'],
                'md5': classification['candidate_md5'],
                'size': classification['candidate_size'],
                'structural_class': classification['structural_class'],
                'ram_path': CANDIDATE_RAM_PATH,
            },
            'status_library': {
                'role': 'status-only native bridge shared library staged to SHA256-scoped /tmp path',
                'sha256': lib_info['sha256'],
                'md5': lib_info['md5'],
                'size': lib_info['size'],
                'ram_path': expected_lib_ram_path,
            },
            'external_allowlist': {
                'role': 'strict external allowlist (/etc/cammy/native-select.allow) binding replacement S01app MD5',
                'format': R.ALLOW_FORMAT,
                'sha256': _sha256_bytes(allow_bytes),
                'md5': _md5_bytes(allow_bytes),
                'size': len(allow_bytes),
                'binds_replacement_s01app_md5': proposal.replacement_md5,
                'binds_stock_s01app_md5': False,
            },
        },
        'templates': {
            'arm': {
                'package_member': 'templates/arm.template',
                'format': R.ARM_FORMAT,
                'attempt': attempt,
                'consumed': False,
                'armed': False,
                'staged_in_tf_dir': False,
                'sha256': _sha256_bytes(arm_template_bytes),
            },
            'ack': {
                'package_member': 'templates/ack.template',
                'format': R.ACK_FORMAT,
                'attempt': attempt,
                'observed_boot_placeholder': observed_boot_placeholder,
                'outcome': ack_outcome,
                'consumed': False,
                'staged_in_tf_dir': False,
                'sha256': _sha256_bytes(ack_template_bytes),
            },
        },
        'classification_summary': {
            'structural_class': classification['structural_class'],
            'structural_ok': classification['structural_ok'],
            'connect_trigger_sites_modified': classification['connect_trigger_sites_modified'],
            'reproduced_by_wp2a_generator': bool(repro.get('identical')),
            'review_format': review_json.get('format'),
            'review_evidence_class': review_info['evidence_class'],
            'review_sha256': _sha256_bytes(review_bytes),
            'local_only': classification['local_only'],
            'activation_eligible': False,
            'trust_boundary_note': (
                'Structural range/stub checks and on-camera MD5+size checks are mismatch guards, '
                'not cryptographic authentication or hardware approval. Offline/fixture review '
                'records remain LOCAL_ONLY until manager E1 closure and hardware gate verification.'
            ),
        },
        'provenance': {
            'repo_head_commit': repo_head_commit,
            'wp2a_candidate_source_commit': review_json.get('provenance', {}).get('source_commit'),
            'wp2a_build_recipe_sha256': review_json.get('provenance', {}).get('build_recipe_sha256'),
            'recipe_inputs': recipe_hashes,
        },
        'target_layout': _build_target_layout(lib_info['sha256'], attempt),
        'rollback': {
            'rollback_member': 'rollback/S01app.stock',
            'target_camera_path': '/etc/init.d/S01app',
            'expected_sha256': proposal.source_sha256,
            'expected_md5': proposal.source_md5,
            'expected_size': proposal.source_size,
            'expected_lines': proposal.source_lines,
            'stock_sdvcam_untouched_sha256': STOCK_SDVCAM_SHA256,
            'stock_sdvcam_untouched_md5': STOCK_SDVCAM_MD5,
            'stock_sdvcam_untouched_size': STOCK_SDVCAM_SIZE,
            'disarm_without_removal': [
                'Keep SELECT_GATE=blocked (shipped default: never selects candidate)',
                'Set /etc/cammy/native-select.enabled to mode=disabled or remove /etc/cammy/native-select.enabled',
                'Remove /mnt/extsd/CammyOS-native/arm or remove the TF card before cold boot',
            ],
            'files_and_state_to_remove_on_full_rollback': [
                'Restore /etc/init.d/S01app from rollback/S01app.stock (0755, 0:0) and verify MD5/size/SHA256',
                '/etc/cammy/native-select.enabled',
                '/etc/cammy/native-select.allow',
                '/etc/cammy/native-select/inflight',
                '/etc/cammy/native-select/claimed-*',
                '/etc/cammy/native-select/observed-*',
                '/etc/cammy/native-select (directory)',
                '/mnt/extsd/CammyOS-native/arm, ack-*, candidate.bin, libcammy_native_status.so',
            ],
            'recovery_limits': [
                'Cold reboot only: never send SIGTERM/SIGKILL to sdvcam or run /etc/init.d/S01app stop|restart (stock stop sends kill -9; watchdog handoff unproven).',
                'In-band rollback requires early boot and USB/ADB access to come up.',
                'FEL mode entry proves only USB maskrom enumeration, NOT verified SPI NOR flash dump/restore.',
            ],
        },
        'gates': _package_gates_report(),
        'members': members_manifest,
    }

    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode('ascii')
    member_blobs['package-manifest.json'] = manifest_bytes

    for rel_path in PACKAGE_MEMBERS:
        _write_inert_0644(target_dir, rel_path, member_blobs[rel_path])

    validation = validate_review_package(
        target_dir,
        stock_s01app_path=stock_s01app_path,
        stock_sdvcam_path=stock_sdvcam_path,
        helper_path=helper_path,
        evidence_root=evidence_root,
    )
    if not validation['ok']:
        raise TrialPackageError(
            'newly built package failed self-validation: ' + '; '.join(validation['reasons'])
        )

    return {
        'ok': True,
        'package_dir': str(target_dir),
        'verdict': validation['verdict'],
        'manifest_sha256': _sha256_bytes(manifest_bytes),
        'manifest_md5': _md5_bytes(manifest_bytes),
        'manifest_size': len(manifest_bytes),
        'package_tree_sha256': tree_sha256,
        'contents_count': len(PACKAGE_MEMBERS),
        'review_only': True,
        'activation_eligible': False,
        'installation_authorized': False,
        'deployed': False,
        'hardware_validated': False,
        'local_only': True,
        'select_gate': 'blocked',
    }


def validate_review_package(
    package_dir: Path,
    *,
    stock_s01app_path: Path | None = SAVED_STOCK_S01APP,
    stock_sdvcam_path: Path | None = SAVED_STOCK_SDVCAM,
    helper_path: Path | None = HELPER_PROPOSAL_PATH,
    evidence_root: Path | None = SAVED_WP2A_EVIDENCE_ROOT,
) -> dict:
    """Re-hash every member, verify structure/identities/bindings, and return LOCAL_ONLY/BLOCKED_FOR_ACTIVATION."""
    pkg = Path(package_dir)
    reasons: list[str] = []
    base_result = {
        'ok': False,
        'verdict': VERDICT_REJECTED,
        'package_dir': str(pkg),
        'review_only': True,
        'activation_eligible': False,
        'installation_authorized': False,
        'deployed': False,
        'hardware_validated': False,
        'local_only': True,
        'select_gate': 'blocked',
        'reasons': reasons,
    }

    try:
        st_root = os.lstat(pkg)
    except OSError as exc:
        reasons.append(f'package directory unreadable: {exc}')
        return base_result
    if stat.S_ISLNK(st_root.st_mode) or not stat.S_ISDIR(st_root.st_mode):
        reasons.append('package root must be a regular non-symlink directory')
        return base_result

    present_files: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(pkg, topdown=True, followlinks=False):
        cur_dir = Path(dirpath)
        for dname in list(dirnames):
            dpath = cur_dir / dname
            dst = os.lstat(dpath)
            if stat.S_ISLNK(dst.st_mode):
                rel_d = dpath.relative_to(pkg).as_posix()
                reasons.append(f'symlink directory not allowed: {rel_d}')
                dirnames.remove(dname)
        for fname in filenames:
            fpath = cur_dir / fname
            rel_f = fpath.relative_to(pkg).as_posix()
            present_files.add(rel_f)
            fst = os.lstat(fpath)
            if stat.S_ISLNK(fst.st_mode):
                reasons.append(f'symlink file not allowed: {rel_f}')
                continue
            if not stat.S_ISREG(fst.st_mode):
                reasons.append(f'non-regular file not allowed: {rel_f}')
                continue
            if (fst.st_mode & 0o111) != 0 or (fst.st_mode & 0o777) != 0o644:
                reasons.append(f'member {rel_f} mode is { oct(fst.st_mode & 0o777) }, expected 0o644 with no exec bits')
            if fst.st_nlink != 1:
                reasons.append(f'member {rel_f} has hardlink count {fst.st_nlink} != 1')

    expected_set = set(PACKAGE_MEMBERS)
    extra = sorted(present_files - expected_set)
    missing = sorted(expected_set - present_files)
    if extra:
        reasons.append(f'unexpected extra package members: {extra}')
    if missing:
        reasons.append(f'missing required package members: {missing}')
    if 'tf-staging/CammyOS-native/arm' in present_files:
        reasons.append('armed tf-staging/CammyOS-native/arm file is forbidden in review package')
    if any(p.startswith('tf-staging/CammyOS-native/ack-') for p in present_files):
        reasons.append('consumed/staged ack-* file in tf-staging/CammyOS-native/ is forbidden')
    if reasons:
        return base_result

    member_blobs = {rel: (pkg / rel).read_bytes() for rel in PACKAGE_MEMBERS}
    try:
        manifest = json.loads(member_blobs['package-manifest.json'].decode('utf-8'))
        checklist = json.loads(member_blobs['review/acceptance-checklist.json'].decode('utf-8'))
        review_json = json.loads(member_blobs['provenance/review.json'].decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        reasons.append(f'JSON member parse failure: {exc}')
        return base_result

    if not isinstance(manifest, dict) or manifest.get('format') != PACKAGE_FORMAT:
        reasons.append(f'manifest format must be {PACKAGE_FORMAT}')
        return base_result

    required_bool_flags = {
        'review_only': True,
        'activation_eligible': False,
        'installation_authorized': False,
        'deployed': False,
        'installed': False,
        'hardware_validated': False,
        'local_only': True,
        'live_activation_path_approved': False,
    }
    for flag, expected_val in required_bool_flags.items():
        if manifest.get(flag) is not expected_val:
            reasons.append(f'manifest flag {flag} must be {expected_val!r}, got {manifest.get(flag)!r}')
    if manifest.get('select_gate') != 'blocked':
        reasons.append(f'manifest select_gate must be "blocked", got {manifest.get("select_gate")!r}')
    if manifest.get('validation_verdict') != VERDICT_LOCAL_BLOCKED:
        reasons.append(
            f'manifest validation_verdict must be {VERDICT_LOCAL_BLOCKED!r}, '
            f'got {manifest.get("validation_verdict")!r}'
        )
    if manifest.get('contents_count') != len(PACKAGE_MEMBERS):
        reasons.append(f'manifest contents_count != {len(PACKAGE_MEMBERS)}')

    computed_tree_sha = _tree_sha256(member_blobs)
    if manifest.get('package_tree_sha256') != computed_tree_sha:
        reasons.append('manifest package_tree_sha256 mismatch against member files')

    manifest_members = manifest.get('members')
    if not isinstance(manifest_members, dict) or set(manifest_members) != set(NON_MANIFEST_MEMBERS):
        reasons.append('manifest members table keys do not match NON_MANIFEST_MEMBERS')
    else:
        for rel_path in NON_MANIFEST_MEMBERS:
            m_entry = manifest_members[rel_path]
            spec = PACKAGE_MEMBER_SPECS[rel_path]
            blob = member_blobs[rel_path]
            if not isinstance(m_entry, dict):
                reasons.append(f'manifest member entry {rel_path} is not an object')
                continue
            if m_entry.get('sha256') != _sha256_bytes(blob):
                reasons.append(f'member sha256 mismatch: {rel_path}')
            if m_entry.get('md5') != _md5_bytes(blob):
                reasons.append(f'member md5 mismatch: {rel_path}')
            if m_entry.get('size') != len(blob):
                reasons.append(f'member size mismatch: {rel_path}')
            if 'lines' in m_entry and m_entry.get('lines') != len(blob.splitlines()):
                reasons.append(f'member line count mismatch: {rel_path}')
            if m_entry.get('package_mode') != '0644' or m_entry.get('executable_in_package') is not False:
                reasons.append(f'member {rel_path} manifest mode/executable flags invalid')
            if m_entry.get('host_artifact_owner_is_not_camera_root') is not True:
                reasons.append(f'member {rel_path} must declare host_artifact_owner_is_not_camera_root=True')
            for k in ('target_plane', 'intended_camera_path', 'intended_installed_owner', 'intended_installed_mode'):
                if m_entry.get(k) != spec[k]:
                    reasons.append(f'member {rel_path} {k} mismatch: got {m_entry.get(k)!r}, expected {spec[k]!r}')

    # Verify stock rollback S01app, helper provenance, and generated S01app.proposal via generate_proposal.
    rollback_bytes = member_blobs['rollback/S01app.stock']
    packaged_helper_bytes = member_blobs['provenance/launch_selected_or_stock.proposal']
    packaged_proposal_bytes = member_blobs['internal-bootstrap/S01app.proposal']
    packaged_diff_bytes = member_blobs['internal-bootstrap/S01app.proposal.diff']

    if stock_s01app_path is not None:
        try:
            ext_stock_s01 = _read_regular_file(Path(stock_s01app_path), 'stock S01app')
            if rollback_bytes != ext_stock_s01:
                reasons.append('rollback/S01app.stock differs from external stock S01app source')
        except TrialPackageError as exc:
            reasons.append(str(exc))

    if helper_path is not None:
        try:
            ext_helper = _read_regular_file(Path(helper_path), 'helper proposal')
            if packaged_helper_bytes != ext_helper:
                reasons.append('provenance/launch_selected_or_stock.proposal differs from repository helper proposal')
        except TrialPackageError as exc:
            reasons.append(str(exc))

    try:
        regen = boot_generator.generate_proposal(rollback_bytes, packaged_helper_bytes)
        if regen.source_sha256 != STOCK_S01APP_SHA256 or regen.source_md5 != STOCK_S01APP_MD5:
            reasons.append('rollback/S01app.stock does not match pinned stock S01app identity')
        if regen.helper_sha256 != EXPECTED_HELPER_PROPOSAL_SHA256:
            reasons.append('provenance helper sha256 does not match pinned Chunk 5/6 helper identity')
        if packaged_proposal_bytes != regen.replacement_text.encode('ascii'):
            reasons.append('internal-bootstrap/S01app.proposal does not match generate_proposal() output')
        if packaged_diff_bytes != regen.unified_diff.encode('ascii'):
            reasons.append('internal-bootstrap/S01app.proposal.diff does not match generate_proposal() diff')
        if (
            regen.replacement_sha256 != EXPECTED_REPLACEMENT_S01APP_SHA256
            or regen.replacement_md5 != EXPECTED_REPLACEMENT_S01APP_MD5
            or regen.replacement_size != EXPECTED_REPLACEMENT_S01APP_SIZE
            or regen.replacement_lines != EXPECTED_REPLACEMENT_S01APP_LINES
        ):
            reasons.append('generated S01app.proposal digest/size/lines differ from pinned portable-cleanup revision')
    except (boot_generator.SourceVerificationError, UnicodeDecodeError) as exc:
        reasons.append(f'S01app proposal regeneration check failed: {exc}')
        regen = None

    if member_blobs['internal-bootstrap/native-select.enabled.disabled-template'] != DISABLED_MODE_TEMPLATE:
        reasons.append('disabled mode template must be exactly "mode=disabled\\n"')

    # Verify allowlist, arm template, and ack template records.
    cand_hashes = _blob_hashes(member_blobs['tf-staging/CammyOS-native/candidate.bin'])
    lib_hashes = _blob_hashes(member_blobs['tf-staging/CammyOS-native/libcammy_native_status.so'])

    if cand_hashes['sha256'] != STATUS_CANDIDATE_SHA256:
        reasons.append(f'candidate.bin sha256 mismatch: got {cand_hashes["sha256"]}')
    if lib_hashes['sha256'] != STATUS_LIBRARY_SHA256:
        reasons.append(f'libcammy_native_status.so sha256 mismatch: got {lib_hashes["sha256"]}')

    try:
        allow = R.parse(member_blobs['internal-bootstrap/native-select.allow'], R.ALLOW_KEYS, R.ALLOW_FORMAT)
        arm_t = R.parse(member_blobs['templates/arm.template'], R.ARM_KEYS, R.ARM_FORMAT)
        ack_t = R.parse(member_blobs['templates/ack.template'], R.ACK_KEYS, R.ACK_FORMAT)
    except R.RecordError as exc:
        reasons.append(f'record parse failure: {exc}')
        allow = arm_t = ack_t = None

    if allow is not None:
        if allow['s01app_md5'] == STOCK_S01APP_MD5:
            reasons.append('allowlist s01app_md5 wrongly binds pristine stock S01app instead of replacement S01app.proposal')
        if regen is not None and allow['s01app_md5'] != regen.replacement_md5:
            reasons.append('allowlist s01app_md5 does not match generated replacement S01app.proposal MD5')
        if (allow['source_md5'], allow['source_size']) != (STOCK_SDVCAM_MD5, str(STOCK_SDVCAM_SIZE)):
            reasons.append('allowlist source_md5/source_size mismatch against stock sdvcam')
        if allow['candidate'] != f"{cand_hashes['md5']}:{cand_hashes['size']}:{R.CLASS_STATUS_ONLY}":
            reasons.append('allowlist candidate field does not match packaged status-only candidate.bin')
        if allow['library'] != f"{lib_hashes['sha256']}:{lib_hashes['md5']}:{lib_hashes['size']}":
            reasons.append('allowlist library field does not match packaged libcammy_native_status.so')

    if arm_t is not None and ack_t is not None:
        if not R.is_hex32(arm_t['attempt']) or arm_t['candidate'] != R.CANDIDATE_NAME:
            reasons.append('arm.template attempt or candidate name invalid')
        if (arm_t['md5'], arm_t['size'], arm_t['class']) != (
            cand_hashes['md5'],
            str(cand_hashes['size']),
            R.CLASS_STATUS_ONLY,
        ):
            reasons.append('arm.template candidate identity/class mismatch')
        if (arm_t['lib_sha256'], arm_t['lib_md5'], arm_t['lib_size']) != (
            lib_hashes['sha256'],
            lib_hashes['md5'],
            str(lib_hashes['size']),
        ):
            reasons.append('arm.template library identity mismatch')
        if ack_t['attempt'] != arm_t['attempt']:
            reasons.append('ack.template attempt differs from arm.template attempt')
        if ack_t['observed_boot'] != DEFAULT_TEMPLATE_OBSERVED_BOOT:
            reasons.append('ack.template must keep placeholder observed_boot UUID in offline package')
        if ack_t['outcome'] not in R.ACK_OUTCOMES:
            reasons.append('ack.template outcome invalid')

    t_meta = manifest.get('templates', {})
    for t_name in ('arm', 'ack'):
        t_obj = t_meta.get(t_name, {})
        if t_obj.get('consumed') is not False or t_obj.get('staged_in_tf_dir') is not False:
            reasons.append(f'manifest templates.{t_name} must declare consumed=False and staged_in_tf_dir=False')
    if t_meta.get('arm', {}).get('armed') is not False:
        reasons.append('manifest templates.arm must declare armed=False')

    # Re-classify candidate and library against stock sdvcam and evidence root.
    if stock_sdvcam_path is None:
        reasons.append('stock_sdvcam_path is required to re-classify packaged candidate bytes')
    if evidence_root is None:
        reasons.append('evidence_root is required to re-verify review proof artifacts')
    if stock_sdvcam_path is not None and evidence_root is not None:
        if not Path(stock_sdvcam_path).is_file():
            reasons.append(f'stock sdvcam source missing: {stock_sdvcam_path}')
        elif not Path(evidence_root).is_dir():
            reasons.append(f'evidence_root directory missing: {evidence_root}')
        else:
            again = trial_bundle.classify(
                pkg / 'tf-staging/CammyOS-native/candidate.bin',
                Path(stock_sdvcam_path),
                R.CLASS_STATUS_ONLY,
                pkg / 'tf-staging/CammyOS-native/libcammy_native_status.so',
                pkg / 'provenance/review.json',
                Path(evidence_root),
                reproduce=True,
            )
            if not again.get('structural_ok') or again.get('structural_class') != R.CLASS_STATUS_ONLY:
                reasons.append('re-classification failed status-only structural checks')
            if again.get('connect_trigger_sites_modified') != []:
                reasons.append('re-classification found modified media connect-trigger sites')
            if not again.get('eligible_for_staging'):
                reasons.append(
                    're-classification rejected staging eligibility: ' + '; '.join(again.get('reasons', []))
                )
            repro = again.get('reproduction') or {}
            if repro.get('available') and repro.get('identical') is not True:
                reasons.append('re-classification found candidate differs from WP2A generator output')
            rev = again.get('review') or {}
            if not rev.get('ok') or rev.get('evidence_class') not in ('offline', 'fixture'):
                reasons.append('review.json failed verification or claimed unapproved evidence_class')
            if again.get('local_only') is not True or again.get('activation_eligible') is not False:
                reasons.append('re-classification did not preserve local_only=True / activation_eligible=False')

    # Check runtime library path and rollback identity in manifest.
    expected_lib_ram = R.library_path(lib_hashes['sha256'])
    ids_meta = manifest.get('identities', {})
    if ids_meta.get('status_library', {}).get('ram_path') != expected_lib_ram:
        reasons.append(f'manifest status_library.ram_path must be {expected_lib_ram}')
    if ids_meta.get('status_candidate', {}).get('ram_path') != CANDIDATE_RAM_PATH:
        reasons.append(f'manifest status_candidate.ram_path must be {CANDIDATE_RAM_PATH}')
    rb_meta = manifest.get('rollback', {})
    if (
        rb_meta.get('expected_sha256') != STOCK_S01APP_SHA256
        or rb_meta.get('expected_md5') != STOCK_S01APP_MD5
        or rb_meta.get('expected_size') != STOCK_S01APP_SIZE
    ):
        reasons.append('manifest rollback digest/size does not match pristine stock S01app')
    if rb_meta.get('expected_sha256') == EXPECTED_REPLACEMENT_S01APP_SHA256:
        reasons.append('manifest rollback digest wrongly matches replacement S01app instead of stock S01app')

    # Check acceptance checklist invariants (no fabricated hardware pass or fake PIDs/boots).
    if not isinstance(checklist, dict) or checklist.get('format') != CHECKLIST_FORMAT:
        reasons.append(f'checklist format must be {CHECKLIST_FORMAT}')
    else:
        if checklist.get('hardware_performed') is not False or checklist.get('hardware_pass') is not False:
            reasons.append('checklist claims hardware execution or hardware pass')
        if checklist.get('verdict') != VERDICT_LOCAL_BLOCKED:
            reasons.append(f'checklist verdict must be {VERDICT_LOCAL_BLOCKED}')
        if checklist.get('runbook_sha256') != _sha256_bytes(member_blobs['review/NATIVE-TRIAL-REVIEW.md']):
            reasons.append('checklist runbook_sha256 mismatch against review/NATIVE-TRIAL-REVIEW.md')
        hw_stages = checklist.get('hardware_stages')
        if not isinstance(hw_stages, dict) or set(hw_stages) != {'selected-boot', 'cleanup', 'stock-restore'}:
            reasons.append('checklist hardware_stages must be selected-boot, cleanup, stock-restore')
        else:
            for st_name, st_obj in hw_stages.items():
                if not isinstance(st_obj, dict):
                    reasons.append(f'hardware stage {st_name} is not an object')
                    continue
                if st_obj.get('status') != 'PENDING_HARDWARE_NOT_RUN':
                    reasons.append(f'hardware stage {st_name} status must be PENDING_HARDWARE_NOT_RUN')
                if st_obj.get('hardware_performed') is not False or st_obj.get('boot_id') is not None:
                    reasons.append(f'hardware stage {st_name} has fabricated hardware_performed or boot_id')
            sel_obs = hw_stages.get('selected-boot', {}).get('observations', {})
            if sel_obs.get('native_apps') is not None:
                reasons.append('selected-boot native_apps must be None in offline checklist (no fabricated PID)')
            if sel_obs.get('watchdog', {}).get('holder_pid') is not None:
                reasons.append('selected-boot watchdog.holder_pid must be None in offline checklist')

    expected_gates = _package_gates_report()
    if manifest.get('gates') != expected_gates:
        reasons.append('manifest gates must match the revised S01app route and open statuses')
    if checklist.get('open_gates') != expected_gates['early_selector_gates']:
        reasons.append('checklist gates must match the revised S01app route and open statuses')

    if review_json.get('evidence_class') not in ('offline', 'fixture'):
        reasons.append('provenance/review.json evidence_class must remain offline or fixture')

    ok = not reasons
    return {
        'ok': ok,
        'verdict': VERDICT_LOCAL_BLOCKED if ok else VERDICT_REJECTED,
        'package_dir': str(pkg),
        'manifest_sha256': _sha256_bytes(member_blobs['package-manifest.json']),
        'package_tree_sha256': computed_tree_sha,
        'contents_count': len(present_files),
        'review_only': True,
        'activation_eligible': False,
        'installation_authorized': False,
        'deployed': False,
        'hardware_validated': False,
        'local_only': True,
        'select_gate': 'blocked',
        'reasons': reasons,
    }


def compact_public_summary(package_dir: Path) -> dict:
    """Return a compact, public-safe metadata summary without exposing vendor binary contents."""
    pkg = Path(package_dir)
    validation = validate_review_package(pkg)
    if not validation['ok']:
        return validation
    manifest_bytes = (pkg / 'package-manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes.decode('utf-8'))
    return {
        'ok': True,
        'verdict': validation['verdict'],
        'package_dir': str(pkg),
        'manifest_sha256': _sha256_bytes(manifest_bytes),
        'manifest_md5': _md5_bytes(manifest_bytes),
        'manifest_size': len(manifest_bytes),
        'package_tree_sha256': manifest['package_tree_sha256'],
        'contents_count': manifest['contents_count'],
        'flags': {
            'review_only': manifest['review_only'],
            'activation_eligible': manifest['activation_eligible'],
            'installation_authorized': manifest['installation_authorized'],
            'deployed': manifest['deployed'],
            'hardware_validated': manifest['hardware_validated'],
            'local_only': manifest['local_only'],
            'select_gate': manifest['select_gate'],
            'live_activation_path_approved': manifest['live_activation_path_approved'],
        },
        'identities': manifest['identities'],
        'member_digests': {
            rel: {
                'sha256': info['sha256'],
                'md5': info['md5'],
                'size': info['size'],
                'package_mode': info['package_mode'],
                'target_plane': info['target_plane'],
                'intended_camera_path': info['intended_camera_path'],
                'intended_installed_mode': info['intended_installed_mode'],
            }
            for rel, info in manifest['members'].items()
        },
        'gates': manifest['gates']['early_selector_gates'],
    }
