"""Machine-readable stage-5 acceptance spec (docs/work-packages/WP5-ACCEPTANCE.md).

Two profiles share one evidence model:

* ``status-only``: the first native build. Selected boot, one app, native watchdog,
  home Wi-Fi/LCD/portal, bridge hello/status with every physical capability
  unavailable, cleanup/disarm, stock restore and a card-removed stock boot. No
  media lease and no media session may appear at all.
* ``video``: one later video-enabled attempt. Everything above (as a verified
  status-only prerequisite report) plus a CURRENT-BOOT media lease, distinct
  readiness milestones, decoded frames of a changing target, and measured
  Stop/expiry/disconnect cleanup. Selection authorization never stands in for
  the lease.
"""
from __future__ import annotations

REPORT_FORMAT = 'cammy-acceptance-report/1'
PROFILES = ('status-only', 'video')

# Ordered stages. kind=host: offline evidence (host/static/offline-arm) is the real
# thing. kind=hardware: only evidence_class=hardware can count toward a hardware pass.
STAGES = (
    ('planned', 'host'),
    ('staged', 'host'),
    ('structurally-classified', 'host'),
    ('abi-proved', 'host'),
    ('selected-boot', 'hardware'),
    ('working-frame', 'hardware'),
    ('cleanup', 'hardware'),
    ('stock-restore', 'hardware'),
)
STAGE_KIND = dict(STAGES)
STAGE_ORDER = [name for name, _ in STAGES]
REQUIRED = {
    'status-only': ('planned', 'staged', 'structurally-classified', 'abi-proved', 'selected-boot',
                    'cleanup', 'stock-restore'),
    'video': ('planned', 'staged', 'structurally-classified', 'abi-proved', 'selected-boot',
              'working-frame', 'cleanup', 'stock-restore'),
}
# Evidence classes. LOCAL ones can never support a hardware pass.
EVIDENCE_CLASSES = ('hardware', 'offline-arm', 'host', 'static', 'simulated', 'fixture')
LOCAL_CLASSES = ('simulated', 'fixture')
ACCEPTED_CLASSES = {'host': ('host', 'static', 'offline-arm'), 'hardware': ('hardware',)}
RESULTS = ('pass', 'fail', 'unknown')

# Structural class per profile; the video build is gated on bridge lease enforcement (E8).
STRUCTURAL_CLASS = {'status-only': 'status-only', 'video': 'media-lease-gated'}
ABI_PROOFS = {
    'status-only': ('identity_scope', 'device_abi', 'stub_slice', 'lifecycle_fixture', 'status_semantics'),
    'video': ('identity_scope', 'device_abi', 'stub_slice', 'lifecycle_fixture', 'status_semantics',
              'dispatch_fault_suite', 'lease_enforcement'),
}

# Thresholds (reviewable defaults; a stricter runbook may raise them).
WATCHDOG_WINDOW_S = 600          # selected app holds and feeds /dev/watchdog, no reset
MIN_DISTINCT_FRAMES = 2          # changing known target: at least two different decoded frames
MAX_FRAME_AGE_MS = 2000          # latest decoded frame not older than this at capture
MAX_CLEANUP_MS = 5000            # Stop / expiry / disconnect confirmed cleanup bound
MAX_EVIDENCE_AGE_DAYS = 14       # evidence older than this (vs validation time) is STALE
BRIDGE_BACKEND_NATIVE = 2        # PROTOCOL.md: backend_kind 2 = native

# Report-level keys; extra keys are refused so authority cannot be smuggled in.
REPORT_KEYS = {'format', 'profile', 'fixture', 'trial_id', 'window', 'identities',
               'selection_authorization', 'media_lease', 'boots', 'cancelled', 'prerequisite', 'evidence'}
SELECTION_KEYS = {'attempt', 'allowlist_sha256', 'approval_ref', 'scope'}
LEASE_KEYS = {'boot_id', 'generation', 'lease_ref', 'issued_mono_ms', 'expires_mono_ms'}
EVIDENCE_KEYS = {'stage', 'evidence_class', 'result', 'captured_utc', 'boot_id', 'binding',
                 'observations', 'artifacts'}

STATUS_PASS = 'PASS'
STATUS_LOCAL = 'PASS_LOCAL'
VERDICTS = ('REJECTED', 'CANCELLED_CLEANUP_PENDING', 'CANCELLED_CLEAN', 'INCOMPLETE', 'LOCAL_ONLY',
            'HARDWARE_EVIDENCE_COMPLETE')
APPROVAL = 'none: this tool closes no gate; a complete hardware report still needs manager review'


def describe() -> dict:
    return {
        'format': REPORT_FORMAT, 'profiles': PROFILES,
        'stages': [{'stage': s, 'kind': k} for s, k in STAGES], 'required': REQUIRED,
        'evidence_classes': EVIDENCE_CLASSES, 'local_classes': LOCAL_CLASSES,
        'accepted_classes': ACCEPTED_CLASSES, 'structural_class': STRUCTURAL_CLASS, 'abi_proofs': ABI_PROOFS,
        'identities': {
            'source': 'stock sdvcam sha256/md5/size (must equal native_trial.policy)',
            's01app': 'stock S01app sha256/md5 (must equal native_trial.policy)',
            'candidate': 'sha256/md5/size; differs from source; executable bytes of the ONE native app',
            'library': ('sha256/md5/size and ram_path = /tmp/cammy-native-status/<sha256>/'
                        'libcammy_native_status.so; mapped bytes in the app'),
        },
        'authorization': {
            'selection': 'which binary runs for one boot (attempt, allowlist sha256, approval ref); no media',
            'media_lease': 'video only: issued in the SELECTED boot by the portal; never in arm/allowlist',
        },
        'thresholds': {'watchdog_window_s': WATCHDOG_WINDOW_S, 'min_distinct_frames': MIN_DISTINCT_FRAMES,
                       'max_frame_age_ms': MAX_FRAME_AGE_MS, 'max_cleanup_ms': MAX_CLEANUP_MS,
                       'max_evidence_age_days': MAX_EVIDENCE_AGE_DAYS},
        'verdicts': VERDICTS, 'approval': APPROVAL,
    }
