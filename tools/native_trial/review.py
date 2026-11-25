"""SHA256-bound review/ABI evidence for a status-only candidate (WP5 finding F3).

Structural classification (changed byte ranges, a bl into the cave, one embedded
library path, an ARM ELF header) shows WHERE bytes changed, not WHAT they do.
Arbitrary code in the allowed cave, or any ARM shared object, can pass it. So the
class string ``status-only`` never makes a candidate trusted on its own: E1 also
needs this record, which binds the exact source, candidate and library SHA256s to
the NATIVE-ABI section 7 proofs (each a hashed artifact on disk) and to build
provenance.

What this module can and cannot establish:

* It checks that the record is well-formed, bound to the exact bytes, complete,
  and that every proof artifact exists with the stated SHA256.
* It cannot tell a genuine review from a typed one. ``evidence_class`` is a
  declaration: ``fixture`` and ``offline`` records are LOCAL_ONLY; ``reviewed``
  records still need the manager to close E1. Nothing here closes a gate.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from . import records as R

FORMAT = 'cammy-native-review/1'
EVIDENCE_CLASSES = ('fixture', 'offline', 'reviewed')
LOCAL_ONLY_CLASSES = ('fixture', 'offline')
# docs/native-bridge/NATIVE-ABI.md section 7, proofs 1-5 (6 is for later dispatch).
REQUIRED_PROOFS = ('identity_scope', 'device_abi', 'stub_slice', 'lifecycle_fixture', 'status_semantics')
BEHAVIOR = {'physical_capabilities': 'all-unavailable', 'media_paths': 'none', 'native_dispatch': 'none'}
PROVENANCE_KEYS = ('source_commit', 'build_recipe_sha256', 'builder', 'reviewer', 'reviewed_utc')
TOP_KEYS = {'format', 'evidence_class', 'profile', 'source_sha256', 'candidate_sha256', 'library_sha256',
            'library_ram_path', 'provenance', 'proofs', 'behavior'}
_COMMIT = re.compile(r'[0-9a-f]{7,40}')
MAX_BYTES = 64 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path | None, *, source_sha256: str, candidate_sha256: str, library_sha256: str,
           evidence_root: Path | None = None) -> dict:
    """Verify a review record against exact identities. Never raises on bad input."""
    out = {'provided': path is not None, 'ok': False, 'evidence_class': None, 'local_only': True,
           'record_sha256': None, 'reasons': []}
    reasons = out['reasons']
    if path is None:
        reasons.append('no review/ABI evidence record: structural classification only')
        return out
    path = Path(path)
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError('record too large')
        record = json.loads(raw)
    except (OSError, ValueError) as error:
        reasons.append(f'review record unreadable: {error}')
        return out
    out['record_sha256'] = hashlib.sha256(raw).hexdigest()
    if not isinstance(record, dict):
        reasons.append('review record is not an object')
        return out
    if set(record) != TOP_KEYS:
        reasons.append(f'review keys differ: extra={sorted(set(record) - TOP_KEYS)} '
                       f'missing={sorted(TOP_KEYS - set(record))}')
        return out
    if record['format'] != FORMAT:
        reasons.append(f'review format is not {FORMAT}')
    klass = record['evidence_class']
    if klass not in EVIDENCE_CLASSES:
        reasons.append(f'unknown evidence_class {klass!r}')
    else:
        out['evidence_class'] = klass
        out['local_only'] = klass in LOCAL_ONLY_CLASSES
    if record['profile'] != R.CLASS_STATUS_ONLY:
        reasons.append('review profile is not status-only')
    for key, expected in (('source_sha256', source_sha256), ('candidate_sha256', candidate_sha256),
                          ('library_sha256', library_sha256)):
        if record[key] != expected:
            reasons.append(f'review {key} does not match the supplied bytes')
    if R.is_hex64(library_sha256) and record['library_ram_path'] != R.library_path(library_sha256):
        reasons.append('review library_ram_path is not the manager SHA256-scoped path')
    prov = record['provenance']
    if not isinstance(prov, dict) or set(prov) != set(PROVENANCE_KEYS):
        reasons.append('review provenance keys differ')
    else:
        if not _COMMIT.fullmatch(str(prov['source_commit'])):
            reasons.append('provenance source_commit is not a hex commit id')
        if not R.is_hex64(str(prov['build_recipe_sha256'])):
            reasons.append('provenance build_recipe_sha256 invalid')
        for key in ('builder', 'reviewer', 'reviewed_utc'):
            if not isinstance(prov[key], str) or not prov[key].strip():
                reasons.append(f'provenance {key} missing')
    if record['behavior'] != BEHAVIOR:
        reasons.append('review behavior must state no physical capability, media path or native dispatch')
    proofs = record['proofs']
    root = Path(evidence_root) if evidence_root is not None else path.parent
    if not isinstance(proofs, dict) or set(proofs) != set(REQUIRED_PROOFS):
        reasons.append(f'review proofs must be exactly {list(REQUIRED_PROOFS)}')
    else:
        for name in REQUIRED_PROOFS:
            proof = proofs[name]
            if not isinstance(proof, dict) or set(proof) != {'result', 'artifact', 'sha256'}:
                reasons.append(f'proof {name}: malformed')
                continue
            if proof['result'] != 'pass':
                reasons.append(f'proof {name}: result is {proof["result"]!r}, not pass')
            rel = PurePosixPath(str(proof['artifact']))
            if rel.is_absolute() or '..' in rel.parts or not rel.parts:
                reasons.append(f'proof {name}: artifact path must be relative inside the evidence root')
                continue
            artifact = root / rel
            if not artifact.is_file() or artifact.is_symlink():
                reasons.append(f'proof {name}: artifact missing')
            elif _sha256(artifact) != proof['sha256']:
                reasons.append(f'proof {name}: artifact sha256 mismatch')
    out['ok'] = not reasons
    return out


def template(*, source_sha256: str, candidate_sha256: str, library_sha256: str,
             evidence_class: str = 'fixture') -> dict:
    """Skeleton record (every proof pending) for a builder to fill in."""
    return {
        'format': FORMAT, 'evidence_class': evidence_class, 'profile': R.CLASS_STATUS_ONLY,
        'source_sha256': source_sha256, 'candidate_sha256': candidate_sha256,
        'library_sha256': library_sha256, 'library_ram_path': R.library_path(library_sha256),
        'provenance': {key: '' for key in PROVENANCE_KEYS},
        'proofs': {name: {'result': 'pending', 'artifact': f'proofs/{name}.txt', 'sha256': ''}
                   for name in REQUIRED_PROOFS},
        'behavior': dict(BEHAVIOR),
    }
