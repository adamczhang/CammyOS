"""Stage-5 acceptance evidence validator. Pure function over a supplied report.

Rules that make a hardware pass impossible without real, current, bound evidence:

* every required stage needs a passing item; a missing stage is INCOMPLETE;
* a failed, unknown, stale, wrong-boot, identity-mismatched or malformed item
  blocks (fail dominates any number of passes for the same stage);
* ``simulated``/``fixture`` evidence, a report marked ``fixture``, a fixture or
  offline review, a local-only bundle, a host class on a hardware stage, or
  artifacts that were not re-hashed all cap the verdict at LOCAL_ONLY;
* selection authorization and the media lease are separate objects: a lease
  never rides in the selection record, a status-only trial carries no lease or
  media at all, and video frames need a lease issued in the selected boot.

The best verdict, HARDWARE_EVIDENCE_COMPLETE, is not an approval (spec.APPROVAL).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from . import spec as S

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_trial import policy  # noqa: E402
from native_trial import records as R  # noqa: E402

_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_PRIORITY = ('FAIL', 'MISMATCH', 'WRONG_BOOT', 'STALE', 'INVALID', 'UNKNOWN')
_BLOCKING = ('FAIL', 'MISMATCH', 'WRONG_BOOT', 'STALE', 'INVALID')
SKEW = timedelta(minutes=5)


def is_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID.fullmatch(value))


def parse_utc(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        return None


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class _Checks:
    """Collects (code, message) problems for one evidence item."""

    def __init__(self):
        self.problems: list[tuple[str, str]] = []
        self.local: list[str] = []

    def need(self, condition, message: str, code: str = 'INVALID') -> bool:
        if not condition:
            self.problems.append((code, message))
        return bool(condition)

    def obj(self, parent, key: str) -> dict:
        value = parent.get(key) if isinstance(parent, dict) else None
        self.need(isinstance(value, dict), f'observation {key} missing')
        return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- report level
def _check_identities(ids, errors) -> dict:
    if not isinstance(ids, dict) or set(ids) != {'source', 's01app', 'candidate', 'library'}:
        errors.append('identities must be exactly source, s01app, candidate, library')
        return {}
    src, s01, cand, lib = (ids[k] if isinstance(ids[k], dict) else {} for k in
                           ('source', 's01app', 'candidate', 'library'))
    if (src.get('sha256'), src.get('md5'), src.get('size')) != (
            policy.STOCK_SDVCAM_SHA256, policy.STOCK_SDVCAM_MD5, policy.STOCK_SDVCAM_SIZE):
        errors.append('source identity is not the verified stock sdvcam')
    if (s01.get('sha256'), s01.get('md5')) != (policy.STOCK_S01APP_SHA256, policy.STOCK_S01APP_MD5):
        errors.append('s01app identity is not the verified stock S01app')
    if not (R.is_hex64(cand.get('sha256')) and R.is_hex32(cand.get('md5')) and _int(cand.get('size'))):
        errors.append('candidate identity needs sha256, md5 and size')
    elif cand['sha256'] == src.get('sha256'):
        errors.append('candidate identity equals stock')
    if not (R.is_hex64(lib.get('sha256')) and R.is_hex32(lib.get('md5')) and _int(lib.get('size'))):
        errors.append('library identity needs sha256, md5 and size')
    elif lib.get('ram_path') != R.library_path(lib['sha256']):
        errors.append('library ram_path is not the manager SHA256-scoped path')
    return {'source': src, 'candidate': cand, 'library': lib}


def _check_authorization(report, profile, errors):
    sel = report.get('selection_authorization')
    if not isinstance(sel, dict):
        errors.append('selection_authorization missing')
        sel = {}
    elif set(sel) != S.SELECTION_KEYS:
        extra = sorted(set(sel) - S.SELECTION_KEYS)
        errors.append(f'selection_authorization keys differ (extra={extra}); selection never carries '
                      'media authority')
    else:
        if not (R.is_hex32(sel['attempt']) and R.is_hex64(sel['allowlist_sha256'])):
            errors.append('selection attempt/allowlist_sha256 invalid')
        if not isinstance(sel['approval_ref'], str) or not sel['approval_ref'].strip():
            errors.append('selection approval_ref missing')
        if sel['scope'] != 'select-one-boot':
            errors.append('selection scope must be select-one-boot')
    lease = report.get('media_lease')
    if profile == 'status-only':
        if lease is not None:
            errors.append('status-only trial carries a media lease')
        lease = None
    elif lease is not None:
        if not isinstance(lease, dict) or set(lease) != S.LEASE_KEYS:
            errors.append('media_lease keys differ')
            lease = None
        else:
            if not (is_uuid(lease['boot_id']) and _int(lease['generation']) and lease['generation'] >= 1):
                errors.append('media_lease boot_id/generation invalid')
            if not isinstance(lease['lease_ref'], str) or not lease['lease_ref'].strip() or \
                    lease['lease_ref'] == sel.get('approval_ref'):
                errors.append('media_lease must have its own lease_ref, distinct from the selection approval')
            if not (_int(lease['issued_mono_ms']) and _int(lease['expires_mono_ms'])
                    and lease['issued_mono_ms'] < lease['expires_mono_ms']):
                errors.append('media_lease issued/expires invalid')
    return sel, lease


# --------------------------------------------------------------------------- stage observations
def _planned(c, o, ctx, item):
    c.need(isinstance(o.get('plan_ref'), str) and o['plan_ref'].strip(), 'plan_ref missing')
    c.need(R.is_hex64(o.get('runbook_sha256')), 'runbook_sha256 invalid')
    c.need(isinstance(o.get('rollback_ref'), str) and o['rollback_ref'].strip(), 'rollback_ref missing')
    c.need(o.get('profile') == ctx['profile'], 'plan profile differs from report profile', 'MISMATCH')


def _staged(c, o, ctx, item):
    ids = ctx['ids']
    c.need(R.is_hex64(o.get('bundle_manifest_sha256')), 'bundle_manifest_sha256 invalid')
    c.need(o.get('candidate_sha256') == ids['candidate'].get('sha256'), 'staged candidate differs', 'MISMATCH')
    c.need(o.get('library_sha256') == ids['library'].get('sha256'), 'staged library differs', 'MISMATCH')
    c.need(o.get('library_ram_path') == ids['library'].get('ram_path'), 'staged library path differs', 'MISMATCH')
    c.need(o.get('installed') is False, 'staging must not claim installation')
    c.need(isinstance(o.get('local_only'), bool), 'local_only flag missing')
    if o.get('local_only') is not False:
        c.local.append('bundle is LOCAL_ONLY (fixture/offline review)')


def _structural(c, o, ctx, item):
    ids = ctx['ids']
    c.need(o.get('structural_class') == S.STRUCTURAL_CLASS[ctx['profile']],
           f"structural_class must be {S.STRUCTURAL_CLASS[ctx['profile']]}")
    c.need(o.get('structural_ok') is True, 'structural_ok is not true')
    c.need(o.get('connect_trigger_sites_modified') == [], 'connect-trigger sites modified or unknown')
    c.need(o.get('source_sha256') == ids['source'].get('sha256'), 'classified source differs', 'MISMATCH')
    c.need(o.get('candidate_sha256') == ids['candidate'].get('sha256'), 'classified candidate differs', 'MISMATCH')


def _abi(c, o, ctx, item):
    c.need(R.is_hex64(o.get('review_sha256')), 'review_sha256 invalid')
    klass = o.get('review_evidence_class')
    c.need(klass in ('fixture', 'offline', 'reviewed'), 'review_evidence_class invalid')
    if klass != 'reviewed':
        c.local.append(f'review evidence class {klass!r} is LOCAL_ONLY')
    proofs = o.get('proofs')
    want = S.ABI_PROOFS[ctx['profile']]
    c.need(isinstance(proofs, dict) and set(proofs) == set(want) and all(v == 'pass' for v in proofs.values()),
           f'proofs must be exactly {list(want)}, all pass')


def _selected(c, o, ctx, item):
    boots, ids, sel = ctx['boots'], ctx['ids'], ctx['sel']
    selected = boots.get('selected')
    c.need(item.get('boot_id') == selected, 'evidence is not from the selected boot', 'WRONG_BOOT')
    st = c.obj(o, 'selector_status')
    c.need((st.get('decision'), st.get('reason'), st.get('mode')) == ('candidate', 'selected', 'select'),
           'selector status is not candidate/selected/select')
    c.need(st.get('attempt') == sel.get('attempt'), 'selector attempt differs from the authorization', 'MISMATCH')
    c.need(st.get('boot') == selected, 'selector status is from another boot', 'WRONG_BOOT')
    apps = o.get('native_apps')
    app = {}
    if c.need(isinstance(apps, list) and len(apps) == 1 and isinstance(apps[0], dict), 'exactly one native app'):
        app = apps[0]
        exe = app.get('exe_sha256')
        if c.need(R.is_hex64(exe), 'app identity by name/path only; exe_sha256 of /proc/<pid>/exe required'):
            c.need(exe != ids['source'].get('sha256'), 'the running app is stock, not the candidate', 'MISMATCH')
            c.need(exe == ids['candidate'].get('sha256'), 'running executable bytes are not the candidate',
                   'MISMATCH')
        c.need(_int(app.get('pid')) and _int(app.get('starttime')), 'app pid/starttime missing')
    lib = c.obj(o, 'library_mapping')
    c.need(lib.get('pid') == app.get('pid'), 'library mapping is not in the selected app')
    c.need(lib.get('path') == ids['library'].get('ram_path'), 'mapped library path differs', 'MISMATCH')
    c.need(lib.get('mapped_sha256') == ids['library'].get('sha256'), 'mapped library bytes differ', 'MISMATCH')
    wd = c.obj(o, 'watchdog')
    c.need(wd.get('holder_pid') == app.get('pid'), 'watchdog is not held by the selected app')
    c.need(_int(wd.get('window_s')) and wd['window_s'] >= S.WATCHDOG_WINDOW_S,
           f'watchdog stability window shorter than {S.WATCHDOG_WINDOW_S} s')
    c.need(wd.get('resets') == 0 and wd.get('boot_id_after') == selected, 'watchdog reset or boot changed')
    net = c.obj(o, 'network')
    c.need(net.get('home_wifi_unchanged') is True and net.get('portal_reachable') is True,
           'home Wi-Fi/portal not confirmed')
    c.need(c.obj(o, 'lcd').get('normal_view') is True, 'LCD normal view not confirmed')
    br = c.obj(o, 'bridge')
    c.need(br.get('hello_ok') is True and br.get('backend_kind') == S.BRIDGE_BACKEND_NATIVE,
           'bridge hello from the native backend not confirmed')
    c.need(br.get('boot_id') == selected, 'bridge reports another boot', 'WRONG_BOOT')
    c.need(br.get('hardware_actions_enabled') is False and br.get('physical_capabilities') == 'all-unavailable',
           'bridge must report hardware actions disabled and every physical capability unavailable')
    c.need(c.obj(o, 'media').get('sessions_started') == 0, 'a media session started before/without a lease')


def _frame(c, o, ctx, item):
    selected, lease = ctx['boots'].get('selected'), ctx['lease']
    c.need(item.get('boot_id') == selected, 'frame evidence is not from the selected boot', 'WRONG_BOOT')
    if not c.need(isinstance(lease, dict), 'no current-boot media lease: selection never authorizes media'):
        return
    c.need(lease.get('boot_id') == selected, 'media lease was issued in another boot', 'WRONG_BOOT')
    c.need(o.get('lease_generation') == lease.get('generation') and o.get('lease_ref') == lease.get('lease_ref'),
           'frames are not bound to the media lease', 'MISMATCH')
    ms = c.obj(o, 'milestones')
    order = [ms.get(k) for k in ('event_connected_ms', 'listener_ready_ms', 'sdp_ready_ms',
                                 'first_decoded_frame_ms')]
    c.need(all(_int(v) for v in order) and all(a < b for a, b in zip(order, order[1:])),
           'readiness milestones must be distinct and ordered (frame readiness is separate)')
    frames = o.get('frames')
    if c.need(isinstance(frames, list) and len(frames) >= S.MIN_DISTINCT_FRAMES
              and all(isinstance(f, dict) for f in frames), f'at least {S.MIN_DISTINCT_FRAMES} decoded frames'):
        times = [f.get('mono_ms') for f in frames]
        c.need(all(_int(t) for t in times) and all(a < b for a, b in zip(times, times[1:])),
               'frame timestamps must strictly increase')
        c.need(len({f.get('sha256') for f in frames if R.is_hex64(f.get('sha256'))}) >= S.MIN_DISTINCT_FRAMES,
               'frames do not change: a changing known target needs distinct decoded frames')
        c.need(all(_int(f.get('width')) and _int(f.get('height')) and f['width'] > 0 and f['height'] > 0
                   for f in frames), 'frame dimensions missing')
        if all(_int(t) for t in times) and _int(lease.get('issued_mono_ms')):
            c.need(lease['issued_mono_ms'] <= times[0] and times[-1] <= lease['expires_mono_ms'],
                   'frames outside the lease window')
            c.need(not _int(order[3]) or times[0] >= order[3], 'frame precedes first-decoded milestone')
    c.need(_int(o.get('latest_frame_age_ms')) and o['latest_frame_age_ms'] <= S.MAX_FRAME_AGE_MS,
           'latest frame is not fresh')
    c.need(o.get('target') == 'changing-known-target', 'target must be a changing known target')
    c.need(o.get('source_sha256_unchanged') is True, 'original stock hash retention not confirmed')


def _cleanup(c, o, ctx, item):
    c.need(item.get('boot_id') == ctx['boots'].get('selected'), 'cleanup is not from the selected boot',
           'WRONG_BOOT')
    c.need(o.get('bridge_session_closed') is True, 'bridge session close not confirmed')
    c.need(o.get('orphan_processes') == 0 and o.get('unexpected_listeners') == 0,
           'orphan process or unexpected listener remains')
    disarm = c.obj(o, 'disarm')
    c.need(disarm.get('method') in ('arm-removed', 'enabled-removed') and disarm.get('verified') is True,
           'disarm not verified')
    if ctx['profile'] == 'status-only':
        c.need(o.get('media_sessions') == 0, 'status-only trial reports media sessions')
        return
    for case in ('stop', 'expiry', 'disconnect'):
        d = c.obj(o, case)
        trig, done = d.get('trigger_ms'), d.get('confirmed_cleanup_ms')
        ok = _int(trig) and _int(done) and trig <= done <= trig + S.MAX_CLEANUP_MS
        c.need(ok, f'{case}: confirmed hardware cleanup missing or slower than {S.MAX_CLEANUP_MS} ms')
        c.need(d.get('encoder_released') is True and d.get('no_late_command') is True,
               f'{case}: encoder release / no late command after lease end not confirmed')
        if case == 'stop':
            # A queued Stop acknowledgement is distinct from confirmed cleanup.
            c.need(_int(d.get('ack_ms')) and _int(done) and d['ack_ms'] <= done,
                   'stop: queued ack and confirmed cleanup must both be reported')


def _restore(c, o, ctx, item):
    boots, ids, sel = ctx['boots'], ctx['ids'], ctx['sel']
    stock = ids['source'].get('sha256')
    c.need(item.get('boot_id') in (boots.get('post_attempt'), boots.get('card_removed')),
           'restore evidence is not from the post-attempt or card-removed boot', 'WRONG_BOOT')
    pa = c.obj(o, 'post_attempt')
    c.need(pa.get('boot_id') == boots.get('post_attempt'), 'post-attempt boot id differs', 'WRONG_BOOT')
    st = pa.get('selector_status') if isinstance(pa.get('selector_status'), dict) else {}
    c.need((st.get('decision'), st.get('reason')) == ('stock', 'post-attempt-boot'),
           'post-attempt boot did not report stock/post-attempt-boot')
    c.need(pa.get('observed_record_boot') == boots.get('post_attempt'), 'observed record names another boot',
           'WRONG_BOOT')
    c.need(pa.get('native_apps_exe_sha256') == [stock], 'post-attempt boot is not exactly one stock app',
           'MISMATCH')
    c.need(pa.get('lcd_normal') is True and pa.get('home_wifi') is True, 'post-attempt LCD/Wi-Fi not confirmed')
    cr = c.obj(o, 'card_removed')
    c.need(cr.get('boot_id') == boots.get('card_removed'), 'card-removed boot id differs', 'WRONG_BOOT')
    c.need(cr.get('card_present') is False, 'card-removed boot still had a card')
    c.need(cr.get('native_apps_exe_sha256') == [stock], 'card-removed boot is not exactly one stock app',
           'MISMATCH')
    c.need(cr.get('selector_decision') in ('stock', 'silent'), 'card-removed boot selected something')
    c.need(cr.get('lcd_normal') is True and cr.get('home_wifi') is True, 'card-removed LCD/Wi-Fi not confirmed')
    ack = c.obj(o, 'ack')
    c.need(ack.get('attempt') == sel.get('attempt'), 'ack names another attempt', 'MISMATCH')
    c.need(ack.get('observed_boot') == boots.get('post_attempt'), 'ack quotes another observed boot',
           'WRONG_BOOT')
    c.need(ack.get('outcome') in R.ACK_OUTCOMES, 'ack outcome invalid')
    c.need(o.get('durable_side_effects') == [],
           'durable side effects (internal core, /data/app_md5, internal .sdvcam) not proven absent')


OBSERVERS = {'planned': _planned, 'staged': _staged, 'structurally-classified': _structural,
             'abi-proved': _abi, 'selected-boot': _selected, 'working-frame': _frame,
             'cleanup': _cleanup, 'stock-restore': _restore}


# --------------------------------------------------------------------------- items and verdict
def _artifacts(c, item, root: Path | None) -> bool:
    arts = item.get('artifacts')
    if not c.need(isinstance(arts, list) and all(isinstance(a, dict) and set(a) == {'path', 'sha256'}
                                                 for a in arts), 'artifacts malformed'):
        return False
    if S.STAGE_KIND.get(item.get('stage')) == 'hardware':
        c.need(len(arts) >= 1, 'hardware evidence needs at least one raw artifact')
    if root is None:
        return False
    for art in arts:
        rel = PurePosixPath(str(art['path']))
        if not c.need(not rel.is_absolute() and '..' not in rel.parts and rel.parts,
                      'artifact path must stay inside the evidence root'):
            continue
        path = root / rel
        if c.need(path.is_file() and not path.is_symlink(), f'artifact missing: {rel}'):
            c.need(hashlib.sha256(path.read_bytes()).hexdigest() == art['sha256'], f'artifact hash mismatch: {rel}')
    return True


def _item(item, ctx) -> dict:
    c = _Checks()
    if not isinstance(item, dict) or set(item) != S.EVIDENCE_KEYS:
        return {'stage': item.get('stage') if isinstance(item, dict) else None, 'status': 'INVALID',
                'reasons': ['evidence item keys differ'], 'local': [], 'time': None}
    stage = item['stage']
    klass = item['evidence_class']
    c.need(klass in S.EVIDENCE_CLASSES, f'unknown evidence class {klass!r}')
    if ctx['fixture']:
        c.local.append('report is a FIXTURE')
    if klass in S.LOCAL_CLASSES:
        c.local.append(f'{klass} evidence')
    elif klass in S.EVIDENCE_CLASSES and klass not in S.ACCEPTED_CLASSES[S.STAGE_KIND[stage]]:
        c.local.append(f'{klass} evidence cannot stand in for a {S.STAGE_KIND[stage]} stage')
    when = parse_utc(item['captured_utc'])
    window = ctx['window']
    if c.need(when is not None, 'captured_utc invalid'):
        c.need(when >= window[0] and (window[1] is None or when <= window[1]), 'captured outside the trial window',
               'STALE')
        c.need(when <= ctx['now'] + SKEW, 'captured in the future', 'STALE')
        c.need(ctx['now'] - when <= timedelta(days=S.MAX_EVIDENCE_AGE_DAYS), 'evidence too old', 'STALE')
    binding = item['binding'] if isinstance(item['binding'], dict) else {}
    c.need((binding.get('candidate_sha256'), binding.get('library_sha256')) ==
           (ctx['ids']['candidate'].get('sha256'), ctx['ids']['library'].get('sha256')),
           'evidence is bound to other candidate/library bytes', 'MISMATCH')
    verified = _artifacts(c, item, ctx['root'])
    if not verified and S.STAGE_KIND[stage] == 'hardware':
        c.local.append('artifacts not re-hashed (no evidence root)')
    if item['result'] == 'fail':
        c.problems.append(('FAIL', 'evidence reports failure'))
    elif item['result'] != 'pass':
        c.problems.append(('UNKNOWN', 'result is not pass (ambiguous or unknown)'))
    else:
        OBSERVERS[stage](c, item['observations'] if isinstance(item['observations'], dict) else {}, ctx, item)
    codes = [code for code, _ in c.problems]
    status = next((p for p in _PRIORITY if p in codes), S.STATUS_LOCAL if c.local else S.STATUS_PASS)
    return {'stage': stage, 'status': status, 'reasons': [m for _, m in c.problems], 'local': c.local,
            'time': when}


def _aggregate(results: list[dict]) -> str:
    statuses = [r['status'] for r in results]
    for p in _PRIORITY:
        if p in statuses:
            return p
    # A local item cannot be hidden by another passing item for the same stage.
    if S.STATUS_LOCAL in statuses:
        return S.STATUS_LOCAL
    return S.STATUS_PASS if S.STATUS_PASS in statuses else 'MISSING'


def validate(report, *, now: datetime | None = None, evidence_root: Path | None = None,
             prerequisite: dict | None = None) -> dict:
    """Validate one report. ``prerequisite`` is the validated status-only result for video."""
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    out = {'format': 'cammy-acceptance-result/1', 'verdict': 'REJECTED', 'hardware_pass': False,
           'approval': S.APPROVAL, 'errors': errors, 'stages': {}, 'local_reasons': [],
           'validated_at': now.isoformat().replace('+00:00', 'Z')}
    if not isinstance(report, dict) or set(report) != S.REPORT_KEYS:
        keys = sorted(report) if isinstance(report, dict) else []
        errors.append(f'report keys differ: extra={sorted(set(keys) - S.REPORT_KEYS)} '
                      f'missing={sorted(S.REPORT_KEYS - set(keys))}')
        return out
    profile = report['profile']
    out['profile'] = profile
    if report['format'] != S.REPORT_FORMAT or profile not in S.PROFILES:
        errors.append('format/profile invalid')
        return out
    if not isinstance(report['fixture'], bool):
        errors.append('fixture flag must be boolean')
    if not R.is_hex32(report['trial_id']):
        errors.append('trial_id invalid')
    win = report['window'] if isinstance(report['window'], dict) else {}
    opened, closed = parse_utc(win.get('opened_utc')), win.get('closed_utc')
    if closed is not None:
        closed = parse_utc(closed)
        if closed is None:
            errors.append('trial window closed_utc invalid')
            closed = opened
    if opened is None or (closed is not None and closed < opened):
        errors.append('trial window invalid')
        opened = opened or now
    ids = _check_identities(report['identities'], errors) or {'source': {}, 'candidate': {}, 'library': {}}
    sel, lease = _check_authorization(report, profile, errors)
    boots = report['boots'] if isinstance(report['boots'], dict) else {}
    if set(boots) != {'selected', 'post_attempt', 'card_removed'}:
        errors.append('boots must name selected, post_attempt and card_removed')
    named = [b for b in boots.values() if b is not None]
    if not all(is_uuid(b) for b in named) or len(set(named)) != len(named):
        errors.append('boot ids must be valid and distinct')
    if profile == 'video':
        pre = report['prerequisite']
        if not (isinstance(pre, dict) and set(pre) == {'report_sha256'} and R.is_hex64(pre['report_sha256'])):
            errors.append('video needs prerequisite.report_sha256 of a passed status-only report')
    elif report['prerequisite'] is not None:
        errors.append('status-only report takes no prerequisite')
    ctx = {'profile': profile, 'fixture': report['fixture'] is not False, 'ids': ids, 'sel': sel, 'lease': lease,
           'boots': boots, 'window': (opened, closed), 'now': now,
           'root': Path(evidence_root) if evidence_root is not None else None}
    evidence = report['evidence'] if isinstance(report['evidence'], list) else []
    if not isinstance(report['evidence'], list):
        errors.append('evidence must be a list')
    results: dict[str, list] = {name: [] for name in S.STAGE_ORDER}
    for item in evidence:
        stage = item.get('stage') if isinstance(item, dict) else None
        if stage not in S.STAGE_KIND:
            errors.append(f'unknown stage {stage!r}')
            continue
        if stage not in S.REQUIRED[profile]:
            errors.append(f'{stage} evidence in a {profile} trial (status-only must have no media)')
            continue
        results[stage].append(_item(item, ctx))
    # Cancellation: nothing but cleanup/restore may be evidenced after it.
    cancel = report['cancelled']
    cancel_at = None
    if cancel is not None:
        if not (isinstance(cancel, dict) and set(cancel) == {'at_utc', 'reason', 'after_stage'}
                and parse_utc(cancel['at_utc']) and cancel['after_stage'] in S.STAGE_ORDER + ['none']):
            errors.append('cancelled record invalid')
        else:
            cancel_at = parse_utc(cancel['at_utc'])
            for stage, items in results.items():
                if stage in ('cleanup', 'stock-restore'):
                    continue
                if any(r['time'] and r['time'] > cancel_at for r in items):
                    errors.append(f'{stage} evidence captured after cancellation')
    # Hardware stage order: selected-boot < working-frame <= cleanup < stock-restore.
    firsts = [(s, min(r['time'] for r in results[s] if r['time'])) for s in S.STAGE_ORDER
              if S.STAGE_KIND[s] == 'hardware' and any(r['time'] for r in results[s])]
    for index, (b, tb) in enumerate(firsts):
        for a, ta in firsts[:index]:
            if tb < ta:
                errors.append(f'{b} evidence predates {a} evidence')
    for stage in S.STAGE_ORDER:
        if stage not in S.REQUIRED[profile]:
            continue
        items = results[stage]
        status = _aggregate(items)
        out['stages'][stage] = {'status': status, 'items': len(items),
                                'reasons': sorted({m for r in items for m in r['reasons']})}
        out['local_reasons'] += sorted({f'{stage}: {m}' for r in items for m in r['local']})
    statuses = {s: v['status'] for s, v in out['stages'].items()}
    if profile == 'video':
        pre = report['prerequisite'] if isinstance(report['prerequisite'], dict) else {}
        pre_ok = (isinstance(prerequisite, dict) and prerequisite.get('hardware_pass') is True
                  and prerequisite.get('profile') == 'status-only'
                  and prerequisite.get('report_sha256') == pre.get('report_sha256'))
        out['prerequisite_verified'] = pre_ok
    else:
        pre_ok = True
    if cancel is not None and not errors:
        started = any(results[s] for s in S.STAGE_ORDER if S.STAGE_KIND[s] == 'hardware')
        clean = not started or all(statuses.get(s) in (S.STATUS_PASS, S.STATUS_LOCAL)
                                   for s in ('cleanup', 'stock-restore'))
        out['verdict'] = 'CANCELLED_CLEAN' if clean else 'CANCELLED_CLEANUP_PENDING'
        return out
    if errors or any(v in _BLOCKING for v in statuses.values()):
        out['verdict'] = 'REJECTED'
    elif any(v in ('MISSING', 'UNKNOWN') for v in statuses.values()) or not pre_ok:
        out['verdict'] = 'INCOMPLETE'
    elif any(v == S.STATUS_LOCAL for v in statuses.values()) or ctx['fixture']:
        out['verdict'] = 'LOCAL_ONLY'
    else:
        out['verdict'] = 'HARDWARE_EVIDENCE_COMPLETE'
        out['hardware_pass'] = True
    return out


def load(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('report too large')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()
