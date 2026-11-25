"""FIXTURE acceptance reports. Synthetic data only; every report built here is fake.

``report(...)`` returns a complete, internally consistent report for a profile.
By default it is marked ``fixture: true`` and hardware stages use the
``simulated`` class, so the validator can at best return LOCAL_ONLY. Unit tests
flip those labels only to exercise the validator's top verdict; a relabelled
fixture is still fabricated data and is never evidence (the CLI caps any report
outside private/ to LOCAL_ONLY).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_trial import policy  # noqa: E402
from native_trial import records as R  # noqa: E402

from . import spec as S  # noqa: E402

CANDIDATE = hashlib.sha256(b'fixture candidate').hexdigest()
CANDIDATE_MD5 = hashlib.md5(b'fixture candidate').hexdigest()
LIBRARY = hashlib.sha256(b'fixture library').hexdigest()
LIBRARY_MD5 = hashlib.md5(b'fixture library').hexdigest()
ATTEMPT = 'a5' * 16
BOOTS = {'selected': '11111111-2222-4333-8444-555555555551',
         'post_attempt': '11111111-2222-4333-8444-555555555552',
         'card_removed': '11111111-2222-4333-8444-555555555553'}
T0 = '2026-11-14T18:00:00Z'


def _t(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f'2026-11-14T{18 + hours:02d}:{mins:02d}:00Z'


def observations(profile: str) -> dict:
    stock = policy.STOCK_SDVCAM_SHA256
    ram_path = R.library_path(LIBRARY)
    obs = {
        'planned': {'plan_ref': 'docs/work-packages/WP5-ACCEPTANCE.md', 'runbook_sha256': 'e' * 64,
                    'rollback_ref': 'WP4 section 3 rollback', 'profile': profile},
        'staged': {'bundle_manifest_sha256': 'f' * 64, 'candidate_sha256': CANDIDATE, 'library_sha256': LIBRARY,
                   'library_ram_path': ram_path, 'installed': False, 'local_only': False},
        'structurally-classified': {'structural_class': S.STRUCTURAL_CLASS[profile], 'structural_ok': True,
                                    'connect_trigger_sites_modified': [], 'source_sha256': stock,
                                    'candidate_sha256': CANDIDATE},
        'abi-proved': {'review_sha256': 'd' * 64, 'review_evidence_class': 'reviewed',
                       'proofs': {name: 'pass' for name in S.ABI_PROOFS[profile]}},
        'selected-boot': {
            'selector_status': {'decision': 'candidate', 'reason': 'selected', 'mode': 'select',
                                'attempt': ATTEMPT, 'boot': BOOTS['selected']},
            'native_apps': [{'pid': 412, 'starttime': 1834, 'comm': 'sdvcam', 'exe_path': '/usr/bin/sdvcam',
                             'exe_sha256': CANDIDATE}],
            'library_mapping': {'pid': 412, 'path': ram_path, 'mapped_sha256': LIBRARY},
            'watchdog': {'holder_pid': 412, 'window_s': S.WATCHDOG_WINDOW_S, 'resets': 0,
                         'boot_id_after': BOOTS['selected']},
            'network': {'home_wifi_unchanged': True, 'portal_reachable': True},
            'lcd': {'normal_view': True},
            'bridge': {'hello_ok': True, 'backend_kind': S.BRIDGE_BACKEND_NATIVE, 'boot_id': BOOTS['selected'],
                       'hardware_actions_enabled': False, 'physical_capabilities': 'all-unavailable'},
            'media': {'sessions_started': 0},
        },
        'working-frame': {
            'lease_generation': 3, 'lease_ref': 'portal-lease-fixture',
            'milestones': {'event_connected_ms': 100100, 'listener_ready_ms': 100300, 'sdp_ready_ms': 100500,
                           'first_decoded_frame_ms': 101200},
            'frames': [{'mono_ms': 101200, 'sha256': '1' * 64, 'width': 1280, 'height': 720},
                       {'mono_ms': 102200, 'sha256': '2' * 64, 'width': 1280, 'height': 720}],
            'latest_frame_age_ms': 400, 'target': 'changing-known-target', 'source_sha256_unchanged': True,
        },
        'cleanup': {'bridge_session_closed': True, 'orphan_processes': 0, 'unexpected_listeners': 0,
                    'disarm': {'method': 'arm-removed', 'verified': True}},
        'stock-restore': {
            'post_attempt': {'boot_id': BOOTS['post_attempt'],
                             'selector_status': {'decision': 'stock', 'reason': 'post-attempt-boot'},
                             'observed_record_boot': BOOTS['post_attempt'], 'native_apps_exe_sha256': [stock],
                             'lcd_normal': True, 'home_wifi': True},
            'card_removed': {'boot_id': BOOTS['card_removed'], 'card_present': False,
                             'native_apps_exe_sha256': [stock], 'selector_decision': 'silent',
                             'lcd_normal': True, 'home_wifi': True},
            'ack': {'attempt': ATTEMPT, 'observed_boot': BOOTS['post_attempt'], 'outcome': 'stock-restored'},
            'durable_side_effects': [],
        },
    }
    if profile == 'status-only':
        obs['cleanup']['media_sessions'] = 0
    else:
        for case, trig in (('stop', 103000), ('expiry', 104000), ('disconnect', 105000)):
            obs['cleanup'][case] = {'trigger_ms': trig, 'confirmed_cleanup_ms': trig + 900,
                                    'encoder_released': True, 'no_late_command': True}
        obs['cleanup']['stop']['ack_ms'] = 103050
    return obs


def report(profile: str = 'status-only', *, fixture: bool = True, hardware_class: str = 'simulated',
           artifact_sha256: str = '0' * 64) -> dict:
    obs = observations(profile)
    times = {'planned': 1, 'staged': 2, 'structurally-classified': 3, 'abi-proved': 4, 'selected-boot': 20,
             'working-frame': 30, 'cleanup': 40, 'stock-restore': 60}
    boot_of = {'selected-boot': BOOTS['selected'], 'working-frame': BOOTS['selected'],
               'cleanup': BOOTS['selected'], 'stock-restore': BOOTS['post_attempt']}
    evidence = []
    for stage in S.REQUIRED[profile]:
        kind = S.STAGE_KIND[stage]
        evidence.append({
            'stage': stage, 'evidence_class': hardware_class if kind == 'hardware' else 'host',
            'result': 'pass', 'captured_utc': _t(times[stage]), 'boot_id': boot_of.get(stage),
            'binding': {'candidate_sha256': CANDIDATE, 'library_sha256': LIBRARY},
            'observations': obs[stage],
            'artifacts': [{'path': f'raw/{stage}.txt', 'sha256': artifact_sha256}],
        })
    return {
        'format': S.REPORT_FORMAT, 'profile': profile, 'fixture': fixture, 'trial_id': 'c0' * 16,
        'window': {'opened_utc': T0, 'closed_utc': None},
        'identities': {
            'source': {'sha256': policy.STOCK_SDVCAM_SHA256, 'md5': policy.STOCK_SDVCAM_MD5,
                       'size': policy.STOCK_SDVCAM_SIZE},
            's01app': {'sha256': policy.STOCK_S01APP_SHA256, 'md5': policy.STOCK_S01APP_MD5},
            'candidate': {'sha256': CANDIDATE, 'md5': CANDIDATE_MD5, 'size': 11335176},
            'library': {'sha256': LIBRARY, 'md5': LIBRARY_MD5, 'size': 54448, 'ram_path': R.library_path(LIBRARY)},
        },
        'selection_authorization': {'attempt': ATTEMPT, 'allowlist_sha256': 'b' * 64,
                                    'approval_ref': 'manager-approval-fixture', 'scope': 'select-one-boot'},
        'media_lease': None if profile == 'status-only' else {
            'boot_id': BOOTS['selected'], 'generation': 3, 'lease_ref': 'portal-lease-fixture',
            'issued_mono_ms': 100000, 'expires_mono_ms': 160000},
        'boots': dict(BOOTS),
        'cancelled': None,
        'prerequisite': None if profile == 'status-only' else {'report_sha256': '9' * 64},
        'evidence': evidence,
    }
