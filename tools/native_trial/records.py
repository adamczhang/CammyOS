"""Strict key=value records shared by the host tools and the shell selector.

The camera-side proposal parses these with sed/case only, so the formats are
deliberately tiny: fixed keys in fixed order, one line each, newline-terminated,
at most 512 bytes. Nothing here carries a lease, token, credential or media
request; selecting a binary grants no authority.
"""
from __future__ import annotations

import re

MAX_RECORD_BYTES = 512
MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
MAX_ATTEMPTS = 4

ARM_FORMAT = 'cammy-native-arm/2'  # /2: lib_sha256 key (SHA256-scoped path)
ACK_FORMAT = 'cammy-native-ack/1'
ALLOW_FORMAT = 'cammy-native-allow/2'  # /2: library=<sha256>:<md5>:<size>
CLAIM_FORMAT = 'cammy-native-claim/1'
OBSERVED_FORMAT = 'cammy-native-observed/1'
STATUS_FORMAT = 'cammy-native-select-status/1'

ARM_KEYS = ('format', 'attempt', 'candidate', 'md5', 'size', 'class', 'lib_sha256', 'lib_md5', 'lib_size')
ACK_KEYS = ('format', 'attempt', 'observed_boot', 'outcome')
ALLOW_KEYS = ('format', 's01app_md5', 'source_md5', 'source_size', 'candidate', 'library')
CLAIM_KEYS = ('format', 'attempt', 'boot', 'md5')
OBSERVED_KEYS = ('format', 'attempt', 'boot')

CANDIDATE_NAME = 'candidate.bin'
# Status-bridge library: TF name and the fixed hash-scoped RAM path the candidate
# embeds. Manager/WP2A path contract (2026-11-14):
#   /tmp/cammy-native-status/<library-SHA256>/libcammy_native_status.so
# The directory is named by the host-pinned SHA256 of the library bytes. The camera
# has no verified sha256 tool, so on the camera that name is a label copied from the
# reviewed internal allowlist; the bytes are checked there only by MD5 + size, a weak
# mismatch check that detects corruption or a wrong file but does NOT authenticate
# the SHA256 identity (it cannot detect a deliberately crafted MD5-colliding twin).
LIBRARY_NAME = 'libcammy_native_status.so'
LIBRARY_ROOT = '/tmp/cammy-native-status'


def library_path(lib_sha256: str) -> str:
    if not is_hex64(lib_sha256):
        raise RecordError('library path needs the library SHA256 (64 lowercase hex)')
    return f'{LIBRARY_ROOT}/{lib_sha256}/{LIBRARY_NAME}'

ACK_OUTCOMES = ('stock-restored', 'recovered')

# Activation classes. Only status-only may be selected by the v1 proposal.
CLASS_STATUS_ONLY = 'status-only'
CLASS_MEDIA_LEASE = 'media-lease-gated'
CLASS_MEDIA_CONNECT = 'media-connect-triggered'
KNOWN_ALLOW_CLASSES = (CLASS_STATUS_ONLY, CLASS_MEDIA_LEASE)
SELECTABLE_CLASSES = (CLASS_STATUS_ONLY,)

_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_UUID = re.compile(r'[0-9a-f-]{36}')


class RecordError(ValueError):
    pass


def is_hex32(value: str) -> bool:
    return bool(_HEX32.fullmatch(value or ''))


def is_hex64(value: str) -> bool:
    return bool(_HEX64.fullmatch(value or ''))


def is_uuid(value: str) -> bool:
    # Mirrors the shell: charset and length only.
    return bool(_UUID.fullmatch(value or ''))


def is_size(value: str) -> bool:
    if not value or not value.isdigit() or not value.isascii() or value[0] == '0':
        return False
    return len(value) <= 9 and int(value) <= MAX_CANDIDATE_BYTES


def render(keys: tuple[str, ...], values: dict[str, str]) -> bytes:
    """Render a record; refuses missing/extra keys and multi-line values."""
    if set(values) != set(keys):
        raise RecordError(f'record keys {sorted(values)} != {list(keys)}')
    lines = []
    for key in keys:
        value = str(values[key])
        if not value or '\n' in value or '\r' in value or '\0' in value:
            raise RecordError(f'invalid value for {key}')
        lines.append(f'{key}={value}')
    data = ('\n'.join(lines) + '\n').encode('ascii')
    if len(data) > MAX_RECORD_BYTES:
        raise RecordError('record too large')
    return data


def record_ok(data: bytes | None, keys: tuple[str, ...]) -> bool:
    """Exact Python mirror of the shell ``record_ok``."""
    if data is None or not 0 < len(data) <= MAX_RECORD_BYTES:
        return False
    if not data.endswith(b'\n') or data.count(b'\n') != len(keys):
        return False
    lines = data[:-1].split(b'\n')
    for line, key in zip(lines, keys):
        prefix = key.encode() + b'='
        if not line.startswith(prefix) or len(line) == len(prefix):
            return False
    return True


def get(data: bytes | None, key: str) -> str:
    """Mirror of ``sed -n "s/^key=//p" file`` captured by ``$(...)``."""
    if data is None:
        return ''
    text = data.decode('latin-1')
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    prefix = key + '='
    return '\n'.join(line[len(prefix):] for line in lines if line.startswith(prefix)).rstrip('\n')


def parse(data: bytes, keys: tuple[str, ...], fmt: str) -> dict[str, str]:
    if not record_ok(data, keys):
        raise RecordError('record shape invalid')
    values = {key: get(data, key) for key in keys}
    if values['format'] != fmt:
        raise RecordError(f'format is not {fmt}')
    return values


def arm_record(attempt: str, md5: str, size: int, klass: str, lib_sha256: str, lib_md5: str,
               lib_size: int) -> bytes:
    if not is_hex32(attempt) or not is_hex32(md5) or not is_size(str(size)):
        raise RecordError('arm values invalid')
    if not is_hex64(lib_sha256) or not is_hex32(lib_md5) or not is_size(str(lib_size)):
        raise RecordError('arm library values invalid')
    return render(ARM_KEYS, {'format': ARM_FORMAT, 'attempt': attempt, 'candidate': CANDIDATE_NAME,
                             'md5': md5, 'size': str(size), 'class': klass, 'lib_sha256': lib_sha256,
                             'lib_md5': lib_md5, 'lib_size': str(lib_size)})


def ack_record(attempt: str, observed_boot: str, outcome: str) -> bytes:
    if not is_hex32(attempt) or not is_uuid(observed_boot) or outcome not in ACK_OUTCOMES:
        raise RecordError('ack values invalid')
    return render(ACK_KEYS, {'format': ACK_FORMAT, 'attempt': attempt,
                             'observed_boot': observed_boot, 'outcome': outcome})


def allow_record(s01app_md5: str, source_md5: str, source_size: int,
                 candidate_md5: str, candidate_size: int, klass: str,
                 lib_sha256: str, lib_md5: str, lib_size: int) -> bytes:
    if klass not in KNOWN_ALLOW_CLASSES:
        raise RecordError(f'class {klass!r} can never be allowlisted')
    if not all(map(is_hex32, (s01app_md5, source_md5, candidate_md5))):
        raise RecordError('allowlist hash invalid')
    if not is_size(str(source_size)) or not is_size(str(candidate_size)) or candidate_md5 == source_md5:
        raise RecordError('allowlist size/identity invalid')
    if not is_hex64(lib_sha256) or not is_hex32(lib_md5) or not is_size(str(lib_size)):
        raise RecordError('allowlist library invalid')
    return render(ALLOW_KEYS, {'format': ALLOW_FORMAT, 's01app_md5': s01app_md5, 'source_md5': source_md5,
                               'source_size': str(source_size),
                               'candidate': f'{candidate_md5}:{candidate_size}:{klass}',
                               'library': f'{lib_sha256}:{lib_md5}:{lib_size}'})


def claim_record(attempt: str, boot: str, md5: str) -> bytes:
    return render(CLAIM_KEYS, {'format': CLAIM_FORMAT, 'attempt': attempt, 'boot': boot, 'md5': md5})


def observed_record(attempt: str, boot: str) -> bytes:
    return render(OBSERVED_KEYS, {'format': OBSERVED_FORMAT, 'attempt': attempt, 'boot': boot})
