"""Host-side candidate classification and inert bundle/ack/allowlist staging.

Reads candidate/source/library files read-only. Writes only under the
repository's ``build/native-activation/`` (or the system temporary directory, for
tests) and refuses any other destination, so it never writes a TF card, /etc or
a camera. Staging installs nothing: the arm, candidate and library are laid out
for the reviewed powered-off TF workflow, and the allowlist is an *internal
proposal* file that only a separately approved install (gate E2) could place.

Classification never trusts a declared class alone, and it is STRUCTURAL only.
``structural_class == status-only`` requires a candidate built from the pristine
stock sdvcam whose changed bytes lie only in the WP1 status-only scope (bl site,
zero code cave, executable LOAD sizes), with the bl site re-targeted into the cave,
the text LOAD extended only over the cave, both media connect-trigger sites
untouched, no stray absolute path in the cave, and exactly one embedded
SHA256-scoped library path naming the supplied library. That shows where bytes
changed, not what they do (WP5 finding F3): arbitrary code in the cave or any ARM
shared object can pass it. ``eligible`` additionally needs a verified SHA256-bound
review/ABI record (:mod:`native_trial.review`) whose evidence class is not
local-only, and even then only means "E1 evidence complete, manager closure
pending". Activation stays blocked (``policy.ACTIVATION_BLOCKED``).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import struct
import re
import tempfile
from pathlib import Path

from . import policy
from . import records as R
from . import review as RV

REPO = Path(__file__).resolve().parents[2]
STAGING_ROOT = REPO / 'build' / 'native-activation'
CHUNK = 1 << 20
LIB_PATH_PREFIX = R.LIBRARY_ROOT.encode() + b'/'


class BundleError(RuntimeError):
    pass


def hashes(path: Path) -> dict:
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with open(path, 'rb') as handle:
        while chunk := handle.read(CHUNK):
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    return {'sha256': sha.hexdigest(), 'md5': md5.hexdigest(), 'size': size}


def modified_ranges(source: Path, candidate: Path, limit: int = 64) -> list[tuple[int, int]] | None:
    """Byte ranges that differ (merged), or None when sizes differ."""
    if source.stat().st_size != candidate.stat().st_size:
        return None
    ranges: list[tuple[int, int]] = []
    offset = 0
    with open(source, 'rb') as a, open(candidate, 'rb') as b:
        while x := a.read(CHUNK):
            y = b.read(CHUNK)
            if x != y:
                i = 0
                while i < len(x):
                    if x[i] == y[i]:
                        i += 1
                        continue
                    start = i
                    while i < len(x) and x[i] != y[i]:
                        i += 1
                    if ranges and sum(ranges[-1]) == offset + start:
                        ranges[-1] = (ranges[-1][0], ranges[-1][1] + i - start)
                    else:
                        ranges.append((offset + start, i - start))
                    if len(ranges) > limit:
                        return ranges
            offset += len(x)
    return ranges


def _overlaps(ranges, site) -> bool:
    off, length = site[0], site[1]
    return any(o < off + length and off < o + n for o, n in ranges)


def _within(ranges, allowed) -> bool:
    return all(any(a <= o and o + n <= a + length for a, length, _ in allowed) for o, n in ranges)


def library_facts(library: Path) -> dict:
    """ELF header checks only; full device-ABI proof is WP2's gate."""
    data = library.read_bytes()[:52]
    reasons = []
    if len(data) < 52 or data[:4] != b'\x7fELF':
        return {'elf': False, 'reasons': ['library is not an ELF object']}
    ei_class, ei_data = data[4], data[5]
    e_type, e_machine = struct.unpack_from('<HH', data, 16)
    e_flags, = struct.unpack_from('<I', data, 36)
    if ei_class != 1 or ei_data != 1:
        reasons.append('library is not ELF32 little-endian')
    if e_type != 3:
        reasons.append('library is not ET_DYN')
    if e_machine != 40:
        reasons.append('library is not ARM')
    if e_flags & 0xFF000400 != policy.ARM_EABI5_HARD_FLOAT:
        reasons.append('library is not EABI5 hard-float')
    return {'elf': True, 'e_type': e_type, 'e_machine': e_machine, 'e_flags': hex(e_flags), 'reasons': reasons}


def _bl_target_file_offset(word: int, site: int) -> int | None:
    """File offset a BL (cond AL) at file offset ``site`` branches to, else None."""
    if word >> 24 != 0xEB:
        return None
    imm = word & 0xFFFFFF
    if imm & 0x800000:
        imm -= 1 << 24
    return site + 8 + (imm << 2)  # file offset and VA differ by the same bias


_PATHLIKE = re.compile(rb'/[\x21-\x7e]{3,}')


def _structural_status_checks(source: Path, candidate: Path, ranges) -> list[str]:
    """WP2A reviewed layout: bl to the stub start, LOAD = stub end, no stray cave paths."""
    reasons = []
    with open(source, 'rb') as handle:
        src_head = handle.read(0xAC)
    with open(candidate, 'rb') as handle:
        head = handle.read(0xAC)
        handle.seek(policy.STATUS_BL_SITE[0])
        bl = struct.unpack('<I', handle.read(4))[0]
        handle.seek(policy.CAVE[0])
        cave = handle.read(policy.CAVE[1] - policy.CAVE[0])
    if _bl_target_file_offset(bl, policy.STATUS_BL_SITE[0]) != policy.STATUS_STUB_START:
        reasons.append('status bl does not branch to the reviewed stub start (VA 0x8E2E40)')
    filesz, memsz = struct.unpack_from('<II', head, 0xA4)
    old_filesz, _ = struct.unpack_from('<II', src_head, 0xA4)
    if filesz != memsz or old_filesz != policy.STOCK_TEXT_LOAD_SIZE or \
            not policy.STATUS_STUB_START < filesz <= policy.LAST_TEXT_PAGE_END:
        reasons.append('text LOAD sizes must grow equally from stock to the stub end inside the last text page')
    elif max((o + n for o, n in ranges if o >= policy.STATUS_STUB_START), default=0) > filesz:
        reasons.append('patched stub bytes extend past the text LOAD size')
    expected = R.LIBRARY_ROOT.encode() + b'/'
    stray = [m.group() for m in _PATHLIKE.finditer(cave) if not m.group().startswith(expected)]
    if stray:
        reasons.append('unexpected absolute path string(s) in the code cave')
    return reasons


def reproduce_with_generator(source: Path, library_sha256: str) -> dict:
    """Byte-compare against the reviewed WP2A generator (imported read-only).

    Returns {'available', 'identical', 'expected_sha256', 'error'}. The generator
    checks the pristine stock bytes itself and refuses anything else.
    """
    import importlib.util
    tool = REPO / 'tools' / 'build_native_status_candidate.py'
    out = {'available': tool.is_file(), 'identical': None, 'expected_sha256': None, 'error': None}
    if not out['available']:
        return out
    try:
        spec = importlib.util.spec_from_file_location('_wp2a_generator_readonly', tool)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        patched = module.patch_image(Path(source).read_bytes(), library_sha256)
        image = patched[0] if isinstance(patched, tuple) else patched
        out['expected_sha256'] = hashlib.sha256(image).hexdigest()
    except Exception as error:  # the generator refusing is a result, not a crash
        out['error'] = f'{type(error).__name__}: {error}'
    return out


def _embedded_library_paths(candidate: Path) -> list[bytes]:
    data = candidate.read_bytes()
    found, start = [], 0
    while (i := data.find(LIB_PATH_PREFIX, start)) >= 0:
        end = data.find(b'\0', i)
        found.append(data[i:end if end >= 0 else len(data)])
        start = i + 1
    return found


def classify(candidate: Path, source: Path | None, declared_class: str | None,
             library: Path | None = None, review: Path | None = None,
             evidence_root: Path | None = None, reproduce: bool = False) -> dict:
    """Structural class plus review-bound trust for the early selector.

    ``effective_class`` is kept as an alias of ``structural_class`` for older
    readers; it is never a behavioural claim.
    """
    reasons: list[str] = []
    cand = hashes(candidate)
    report = {'candidate': str(candidate), 'candidate_sha256': cand['sha256'], 'candidate_md5': cand['md5'],
              'candidate_size': cand['size'], 'declared_class': declared_class, 'source': None,
              'library': None, 'modified_ranges': None, 'connect_trigger_sites_modified': None,
              'embedded_library_paths': None}
    effective = declared_class if declared_class in (R.CLASS_STATUS_ONLY, R.CLASS_MEDIA_LEASE,
                                                     R.CLASS_MEDIA_CONNECT) else 'unclassified'
    if declared_class is None:
        reasons.append('no declared activation class')
    ranges = None
    if source is None:
        reasons.append('stock source not provided; patch scope unverifiable')
    else:
        src = hashes(source)
        report['source'] = src
        if src['sha256'] != policy.STOCK_SDVCAM_SHA256:
            reasons.append('source is not the verified stock sdvcam')
        if cand['sha256'] == src['sha256']:
            reasons.append('candidate is identical to stock')
        ranges = modified_ranges(source, candidate)
        if ranges is None:
            reasons.append('candidate size differs from source; not an exact in-place copy')
        else:
            report['modified_ranges'] = [{'offset': hex(o), 'bytes': n} for o, n in ranges]
            hit = [desc for off, length, desc in policy.CONNECT_TRIGGER_SITES if _overlaps(ranges, (off, length))]
            report['connect_trigger_sites_modified'] = hit
            if hit:
                effective = R.CLASS_MEDIA_CONNECT
    lib = None
    if effective == R.CLASS_STATUS_ONLY:
        if ranges is not None and not _within(ranges, policy.STATUS_ONLY_ALLOWED_RANGES):
            reasons.append('changes outside the WP1 status-only scope')
            effective = 'unclassified'
        if ranges is not None and not _overlaps(ranges, policy.STATUS_BL_SITE):
            reasons.append('status attach bl site unchanged; not a status build')
        if ranges is not None and effective == R.CLASS_STATUS_ONLY:
            reasons += _structural_status_checks(source, candidate, ranges)
        paths = _embedded_library_paths(candidate)
        report['embedded_library_paths'] = [p.decode('latin-1') for p in paths]
        if library is None:
            reasons.append('status bridge library not provided')
        else:
            lib = hashes(library)
            facts = library_facts(library)
            report['library'] = {**lib, **facts, 'ram_path': R.library_path(lib['sha256'])}
            reasons += facts['reasons']
            if not R.is_size(str(lib['size'])):
                reasons.append('library size outside selector bounds')
            if paths != [R.library_path(lib['sha256']).encode()]:
                reasons.append('candidate must embed exactly the SHA256-scoped path of this library')
    if effective == R.CLASS_MEDIA_CONNECT:
        reasons.append('media starts on an unauthenticated native client connect; never selectable')
    elif effective == R.CLASS_MEDIA_LEASE:
        reasons.append('media-lease-gated is gated on bridge lease enforcement (gate E8)')
    elif effective == 'unclassified':
        reasons.append('unknown or unproven activation class')
    if not R.is_size(str(cand['size'])):
        reasons.append('candidate size outside selector bounds')
    report['reproduction'] = None
    if reproduce and effective == R.CLASS_STATUS_ONLY and lib is not None and source is not None:
        rep = reproduce_with_generator(source, lib['sha256'])
        rep['identical'] = rep['expected_sha256'] == cand['sha256'] if rep['expected_sha256'] else False
        report['reproduction'] = rep
        if not rep['identical']:
            reasons.append('candidate is not byte-identical to the reviewed WP2A generator output')
    structural_ok = not reasons and effective in R.SELECTABLE_CLASSES
    review_report = None
    if structural_ok:
        review_report = RV.verify(review, source_sha256=report['source']['sha256'],
                                  candidate_sha256=cand['sha256'], library_sha256=lib['sha256'],
                                  evidence_root=evidence_root)
    report['structural_class'] = effective
    report['effective_class'] = effective
    report['structural_ok'] = structural_ok
    report['structural_reasons'] = list(reasons)
    report['review'] = review_report
    if review_report is not None:
        reasons += review_report['reasons']
    staging = structural_ok and bool(review_report and review_report['ok'])
    report['trust'] = ('review-bound' if staging else 'structural-only' if structural_ok else 'none')
    report['eligible_for_staging'] = staging
    report['local_only'] = not staging or review_report['local_only']
    if staging and review_report['local_only']:
        reasons.append(f"review evidence class {review_report['evidence_class']!r} is LOCAL_ONLY")
    report['eligible'] = staging and not review_report['local_only']
    report['e1'] = ('evidence complete and bound; manager closure pending' if report['eligible']
                    else 'open')
    report['activation_eligible'] = False
    report['activation'] = policy.ACTIVATION_BLOCKED
    report['reasons'] = reasons
    return report


def _staging_dir(out: Path) -> Path:
    out = Path(os.path.abspath(out))
    allowed = [STAGING_ROOT.resolve(), Path(tempfile.gettempdir()).resolve()]
    resolved = out.resolve()
    if not any(root in resolved.parents for root in allowed):
        raise BundleError(f'refusing destination outside {STAGING_ROOT} or the temp dir: {out}')
    if out.exists() and any(out.iterdir()):
        raise BundleError(f'destination is not empty: {out}')
    return out


def _write_new(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


TF_DIR = 'tf/CammyOS-native'
BUNDLE_FILES = ('bundle-manifest.json', 'review.json', f'{TF_DIR}/arm', f'{TF_DIR}/{R.CANDIDATE_NAME}',
                f'{TF_DIR}/{R.LIBRARY_NAME}', 'internal-proposal/native-select.allow')
ON_CAMERA_CHECK = ('md5+size only (no verified sha256 tool on the camera): a weak mismatch check that '
                   'detects a wrong or corrupt file, not a crafted MD5 collision; SHA256 identity is '
                   'pinned here on the host')


def make_bundle(candidate: Path, source: Path, library: Path, declared_class: str, out: Path,
                attempt: str | None = None, review: Path | None = None,
                evidence_root: Path | None = None) -> dict:
    """Stage an INERT bundle. Needs structural status-only AND a verified review record.

    A fixture/offline review stages a LOCAL_ONLY bundle. No bundle is activation
    eligible while ``policy.ACTIVATION_BLOCKED`` stands; proposal-patch refuses
    every bundle.
    """
    report = classify(candidate, source, declared_class, library, review, evidence_root)
    if not report['eligible_for_staging']:
        raise BundleError('candidate not eligible for staging: ' + '; '.join(report['reasons']))
    out = _staging_dir(out)
    attempt = attempt or secrets.token_hex(16)
    lib = report['library']
    arm = R.arm_record(attempt, report['candidate_md5'], report['candidate_size'], report['structural_class'],
                       lib['sha256'], lib['md5'], lib['size'])
    allow = R.allow_record(policy.STOCK_S01APP_MD5, policy.STOCK_SDVCAM_MD5, policy.STOCK_SDVCAM_SIZE,
                           report['candidate_md5'], report['candidate_size'], report['structural_class'],
                           lib['sha256'], lib['md5'], lib['size'])
    review_bytes = Path(review).read_bytes()
    _write_new(out / TF_DIR / R.CANDIDATE_NAME, candidate.read_bytes())
    _write_new(out / TF_DIR / R.LIBRARY_NAME, library.read_bytes())
    _write_new(out / TF_DIR / 'arm', arm)
    _write_new(out / 'internal-proposal' / 'native-select.allow', allow, 0o600)
    _write_new(out / 'review.json', review_bytes)
    manifest = {
        'format': 'cammy-native-bundle/2', 'attempt': attempt, 'installed': False, 'deployed': False,
        'hardware_validated': False, 'local_only': report['local_only'],
        'activation_eligible': False, 'activation': policy.ACTIVATION_BLOCKED,
        'classification': report,
        'identities': {
            'source': {k: report['source'][k] for k in ('sha256', 'md5', 'size')},
            'candidate': {'sha256': report['candidate_sha256'], 'md5': report['candidate_md5'],
                          'size': report['candidate_size']},
            'library': {k: lib[k] for k in ('sha256', 'md5', 'size')},
            's01app': {'sha256': policy.STOCK_S01APP_SHA256, 'md5': policy.STOCK_S01APP_MD5},
        },
        'on_camera_check': ON_CAMERA_CHECK,
        'review_sha256': hashlib.sha256(review_bytes).hexdigest(),
        'library_ram_path': R.library_path(lib['sha256']),
        'tf_files': {'CammyOS-native/arm': hashlib.sha256(arm).hexdigest(),
                     f'CammyOS-native/{R.CANDIDATE_NAME}': report['candidate_sha256'],
                     f'CammyOS-native/{R.LIBRARY_NAME}': lib['sha256']},
        'internal_proposal_files': {'native-select.allow': hashlib.sha256(allow).hexdigest()},
        'stock': {'sdvcam_sha256': policy.STOCK_SDVCAM_SHA256, 's01app_sha256': policy.STOCK_S01APP_SHA256},
        'media_authorization': 'none; selection carries no lease, token or media request',
        'note': ('Staging only. Activation is blocked (gate EA); the allowlist would also need gate E2. '
                 'TF copy, if ever approved, uses the powered-off reviewed workflow.'),
    }
    _write_new(out / 'bundle-manifest.json', (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())
    return manifest


def validate_bundle(out: Path, source: Path | None = None, evidence_root: Path | None = None) -> dict:
    """Strict re-check of a staged bundle. Returns {'ok': bool, 'reasons': [...]}.

    ``ok`` needs the stock source (to reclassify the bytes) and the evidence root
    (to re-hash the review's proof artifacts); self-consistent files alone never pass.
    """
    out = Path(out)
    reasons: list[str] = []
    present = {str(p.relative_to(out)) for p in out.rglob('*') if p.is_file() or p.is_symlink()}
    expected = set(BUNDLE_FILES)
    if present != expected:
        reasons.append(f'unexpected file set: extra={sorted(present - expected)} missing={sorted(expected - present)}')
    reasons += [f'symlink not allowed: {rel}' for rel in sorted(present & expected) if (out / rel).is_symlink()]
    if reasons:
        return {'ok': False, 'reasons': reasons}
    try:
        arm = R.parse((out / TF_DIR / 'arm').read_bytes(), R.ARM_KEYS, R.ARM_FORMAT)
        allow = R.parse((out / 'internal-proposal/native-select.allow').read_bytes(), R.ALLOW_KEYS, R.ALLOW_FORMAT)
        manifest = json.loads((out / 'bundle-manifest.json').read_text())
    except (R.RecordError, ValueError) as error:
        return {'ok': False, 'reasons': [f'unparseable: {error}']}
    cand = hashes(out / TF_DIR / R.CANDIDATE_NAME)
    lib = hashes(out / TF_DIR / R.LIBRARY_NAME)
    if not R.is_hex32(arm['attempt']) or arm['candidate'] != R.CANDIDATE_NAME:
        reasons.append('arm attempt/candidate invalid')
    if (arm['md5'], arm['size']) != (cand['md5'], str(cand['size'])):
        reasons.append('candidate bytes do not match arm')
    if (arm['lib_sha256'], arm['lib_md5'], arm['lib_size']) != (lib['sha256'], lib['md5'], str(lib['size'])):
        reasons.append('library bytes do not match arm')
    if allow['candidate'] != f"{arm['md5']}:{arm['size']}:{arm['class']}":
        reasons.append('allowlist candidate differs from arm')
    if allow['library'] != f"{arm['lib_sha256']}:{arm['lib_md5']}:{arm['lib_size']}":
        reasons.append('allowlist library differs from arm')
    if (allow['s01app_md5'], allow['source_md5'], allow['source_size']) != (
            policy.STOCK_S01APP_MD5, policy.STOCK_SDVCAM_MD5, str(policy.STOCK_SDVCAM_SIZE)):
        reasons.append('allowlist stock identities differ from verified stock')
    if arm['class'] not in R.SELECTABLE_CLASSES:
        reasons.append(f"class {arm['class']} not selectable")
    if manifest.get('attempt') != arm['attempt'] or manifest.get('installed') is not False:
        reasons.append('manifest inconsistent with arm or claims installation')
    if manifest.get('activation_eligible') is not False:
        reasons.append('manifest claims activation eligibility')
    if manifest.get('review_sha256') != hashlib.sha256((out / 'review.json').read_bytes()).hexdigest():
        reasons.append('manifest review_sha256 mismatch')
    files = manifest.get('tf_files', {})
    if (files.get(f'CammyOS-native/{R.CANDIDATE_NAME}'), files.get(f'CammyOS-native/{R.LIBRARY_NAME}')) != (
            cand['sha256'], lib['sha256']):
        reasons.append('manifest sha256 mismatch')
    again = None
    if source is None:
        reasons.append('stock source required to reclassify the staged bytes')
    if evidence_root is None:
        reasons.append('evidence root required to re-hash the review proof artifacts')
    if source is not None and evidence_root is not None:
        again = classify(out / TF_DIR / R.CANDIDATE_NAME, source, arm['class'], out / TF_DIR / R.LIBRARY_NAME,
                         out / 'review.json', evidence_root)
        reasons += [f'reclassified: {r}' for r in again['reasons'] if 'is LOCAL_ONLY' not in r]
        if again['local_only'] != manifest.get('local_only'):
            reasons.append('manifest local_only differs from the review evidence class')
    return {'ok': not reasons, 'reasons': reasons, 'attempt': arm['attempt'], 'class': arm['class'],
            'candidate_sha256': cand['sha256'], 'library_sha256': lib['sha256'],
            'local_only': manifest.get('local_only', True), 'activation_eligible': False,
            'library_ram_path': R.library_path(arm['lib_sha256']) if R.is_hex64(arm['lib_sha256']) else None}


def make_ack(attempt: str, observed_boot: str, outcome: str, out: Path) -> Path:
    data = R.ack_record(attempt, observed_boot, outcome)
    out = _staging_dir(out)
    path = out / TF_DIR / f'ack-{attempt}'
    _write_new(path, data)
    return path
