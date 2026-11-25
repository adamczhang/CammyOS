"""Derive a boot/hash-bound RAM media initializer for the finite GUI trial.

This module neither installs nor runs anything. The accepted TF payload remains
unchanged; only the exact candidate process may use this derived RAM script.
"""
import hashlib
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/native-media-start.sh'
CANDIDATE_PATH = '/run/cammy-select/sdvcam'


def build(candidate_sha256, boot_id):
    if not re.fullmatch(r'[0-9a-f]{64}', candidate_sha256):
        raise ValueError('Exact candidate SHA256 required')
    boot_id = str(uuid.UUID(boot_id))
    text = SOURCE.read_text()
    checks = ['owned "$NPID" "$NTICK" /usr/bin/sdvcam']
    if text.count(checks[0]) != 2:
        raise ValueError('Native identity contexts changed')
    text = text.replace(checks[0], 'owned "$NPID" "$NTICK" ' + CANDIDATE_PATH)
    marker = 'B=$R/busybox\n'
    if text.count(marker) != 1:
        raise ValueError('Initializer context changed')
    guard = (
        '# Trial scope: exact current boot and immutable staged native image.\n'
        f'[ "$(cat /proc/sys/kernel/random/boot_id)" = "{boot_id}" ] || exit 1\n'
        f'[ "$("$B" sha256sum {CANDIDATE_PATH} | awk \'{{print $1}}\')" = "{candidate_sha256}" ] || exit 1\n'
    )
    text = text.replace(marker, marker + guard)
    return text.encode()


def provenance(candidate_sha256, boot_id):
    blob = build(candidate_sha256, boot_id)
    return dict(source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                script_sha256=hashlib.sha256(blob).hexdigest(),
                native_exe=CANDIDATE_PATH, candidate_sha256=candidate_sha256,
                boot_id=str(uuid.UUID(boot_id)), accepted_tf_payload_changed=False)
