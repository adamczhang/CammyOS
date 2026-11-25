"""Prepare the ONE-FILE stock-bootstrap test; optional USB work is read-only.

No install/restore/reboot commands are exposed by this CLI. The transaction shell
file is an inert review artifact; its later use needs approval of the exact change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from tools.trial_package import package as package

ROOT = Path(__file__).resolve().parents[1]
TRANSACTION = ROOT / 'native/activation/stock_bootstrap_transaction.review.sh'
DEFAULT_PACKAGE = ROOT / 'private/ownership-program/chunk6/manager-final-package'
OUT_ROOT = ROOT / 'private/bootstrap-readiness'
HASHES = {
    'S01app.stock': package.STOCK_S01APP_SHA256,
    'S01app.proposal': package.EXPECTED_REPLACEMENT_S01APP_SHA256,
}
PARSE_COMMAND = '/bin/sh -n; rc=$?; printf "CAMMY_PARSE_RC=%s\\n" "$rc"'
META_COMMAND = r'''printf 'BOOT='; cat /proc/sys/kernel/random/boot_id
printf 'UPTIME='; cat /proc/uptime
printf '\n--IDENTITY--\n'; cat /proc/self/status
printf '\n--PATHS--\n'; ls -ldn /etc/init.d /etc/init.d/S01app /run /tmp
printf '\n--COMMANDS--\n'
for c in cat chmod mkdir rm mv sync md5sum wc cut awk ls; do
    found=0
    for d in /bin /sbin /usr/bin /usr/sbin; do
        if [ -x "$d/$c" ]; then found=1; fi
    done
    printf '%s=%s\n' "$c" "$found"
done
printf '\n--MOUNTS--\n'; cat /proc/mounts
printf '\n--NATIVE--\n'
for f in /proc/[0-9]*/comm; do
    read -r name <"$f" || continue
    if [ "$name" = sdvcam ]; then
        pid=${f#/proc/}; pid=${pid%/comm}
        printf 'PID=%s\n' "$pid"
        readlink "/proc/$pid/exe"
        readlink "/proc/$pid/fd/3"
    fi
done
'''


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


class AdbReader:
    """Uses an explicitly selected existing USB connection, bounded per command."""

    def __init__(self, executable: Path, serial: str, port: int):
        self.base = [str(executable), '-P', str(port), '-s', serial]

    def shell(self, command: str, data: bytes = b'') -> bytes:
        # Stock adbd supports only the legacy PTY shell, not shell -T/exec-out.
        # Feed parser bytes into a shell variable over the legacy PTY, then verify
        # their exact hash before parsing. No large command argument or heredoc.
        # No terminal input, temporary file, or target startup execution is used.
        terminal_input = b''
        if data:
            if command != PARSE_COMMAND:
                raise ValueError('input is permitted only for controlled syntax parsing')
            if any(byte < 32 and byte not in (9, 10) for byte in data):
                raise ValueError('parser input contains a terminal control character')
            command = (
                'code=$(cat); code=${code%__CAMMY_EOF__}; '
                'actual=$(printf "%s" "$code" | /tmp/cammy-runtime/busybox sha256sum); '
                f'[ "${{actual%% *}}" = "{sha(data)}" ] || exit 1; '
                'printf "%s" "$code" | ' + PARSE_COMMAND
            )
            # Sentinel preserves trailing newlines removed by command substitution.
            # Canonical EOF terminates cat, not the outer shell's fixed command.
            terminal_input = data + b'__CAMMY_EOF__\n\x04'
        result = subprocess.run(self.base + ['shell', command], input=terminal_input,
                                capture_output=True, timeout=15, check=False)
        if result.returncode or len(result.stdout) > 1_000_000:
            raise ValueError('USB read failed or exceeded output bound: ' +
                             result.stderr.decode(errors='replace')[:160])
        return result.stdout.replace(b'\r\n', b'\n')


def parser_result(blob: bytes) -> int:
    # A remote exit status alone is insufficient on some ADB versions.
    match = re.search(rb'(?:^|\n)CAMMY_PARSE_RC=([0-9]+)\r?\n?$', blob)
    if not match:
        raise ValueError('missing target-shell parse marker')
    return int(match[1])


def collect(reader: AdbReader, proposal: bytes, transaction: bytes) -> dict:
    """Only cat/metadata and controlled shell -n parsing; no camera file writes."""
    before = reader.shell('cat /proc/sys/kernel/random/boot_id').decode().strip()
    stock = reader.shell('cat /etc/init.d/S01app')
    init_hash = reader.shell('/tmp/cammy-runtime/busybox sha256sum /etc/init.d/S01app').decode().split()
    app_hash = reader.shell('/tmp/cammy-runtime/busybox sha256sum /usr/bin/sdvcam').decode().split()
    valid = parser_result(reader.shell(PARSE_COMMAND, b'exit 73\n'))
    invalid = parser_result(reader.shell(PARSE_COMMAND, b'if then\n'))
    controls_ok = valid == 0 and invalid != 0
    parses = {}
    # Never send actual startup code until -n behavior has passed both controls.
    if controls_ok:
        for name, blob in [('proposal', proposal), ('transaction', transaction), ('stock', stock)]:
            parses[name] = parser_result(reader.shell(PARSE_COMMAND, blob))
    # Legacy PTY makes BusyBox ls emit ANSI colour codes, even for captured output.
    metadata = re.sub(r'\x1b\[[0-9;]*m', '', reader.shell(META_COMMAND).decode(errors='replace'))
    after = reader.shell('cat /proc/sys/kernel/random/boot_id').decode().strip()
    uid = re.search(r'^Uid:\s+\d+\s+(\d+)', metadata, re.M)
    gid = re.search(r'^Gid:\s+\d+\s+(\d+)', metadata, re.M)
    mounts = metadata.split('--MOUNTS--\n', 1)[-1].split('--NATIVE--', 1)[0]
    run_mounts = [line.split() for line in mounts.splitlines()
                  if len(line.split()) >= 4 and line.split()[1] == '/run']
    run_ok = bool(run_mounts and run_mounts[-1][2] == 'tmpfs'
                  and 'noexec' not in run_mounts[-1][3].split(','))
    native = metadata.split('--NATIVE--\n', 1)[-1]
    pids = re.findall(r'^PID=([0-9]+)$', native, re.M)
    paths = metadata.split('--PATHS--\n', 1)[-1].split('--MOUNTS--', 1)[0]
    init_dir_ok = bool(re.search(r'^drwxr-xr-x\s+\S+\s+0\s+0\s+.* /etc/init.d$', paths, re.M))
    init_file_ok = bool(re.search(r'^-rwxr-xr-x\s+\S+\s+0\s+0\s+.* /etc/init.d/S01app$', paths, re.M))
    commands = metadata.split('--COMMANDS--\n', 1)[-1].split('--MOUNTS--', 1)[0]
    expected_commands = {f'{name}=1' for name in 'cat chmod mkdir rm mv sync md5sum wc cut awk ls'.split()}
    checks = {
        'same_boot': before == after and bool(re.fullmatch(r'[0-9a-f-]{36}', before)),
        'root_identity': bool(uid and gid and uid[1] == '0' and gid[1] == '0'),
        'stock_init_sha256': bool(init_hash and init_hash[0] == sha(stock) == package.STOCK_S01APP_SHA256),
        'stock_app_sha256': bool(app_hash and app_hash[0] == package.STOCK_SDVCAM_SHA256),
        'parse_controls': controls_ok,
        'exact_scripts_parse': bool(controls_ok and parses and all(v == 0 for v in parses.values())),
        'run_executable_tmpfs': run_ok,
        'owned_init_directory': init_dir_ok,
        'owned_init_file': init_file_ok,
        'transaction_commands_available': set(commands.strip().splitlines()) == expected_commands,
        'one_stock_watchdog_owner': len(pids) == 1 and native.strip().splitlines() == [
            f'PID={pids[0]}', '/usr/bin/sdvcam', '/dev/watchdog'],
    }
    return {'observed_at': datetime.now(timezone.utc).isoformat(), 'boot_id': before,
            'checks': checks, 'read_only_preflight_passed': all(checks.values()),
            'parse_controls': {'valid_exit_73': valid, 'invalid_syntax': invalid},
            'parse_results': parses, 'stock_init_sha256': sha(stock), 'metadata': metadata,
            'camera_files_written': False, 'boot_test_performed': False,
            'installation_authorized': False}


def build(source: Path, out: Path, reader: AdbReader | None = None) -> dict:
    validation = package.validate_review_package(source)
    if not validation['ok']:
        raise ValueError(f'review package refused: {validation["reasons"]}')
    # Only private output is allowed; create every member exclusively without symlinks.
    out = Path(out).absolute()
    if '..' in out.parts or not (out.is_relative_to(OUT_ROOT) or out.is_relative_to(Path(tempfile.gettempdir()))):
        raise ValueError('output must be private/bootstrap-readiness or a temporary test directory')
    package._assert_no_symlink_chain(out)
    if out.exists():
        raise ValueError('output already exists; choose a fresh directory')
    blobs = {
        'S01app.stock': (source / 'rollback/S01app.stock').read_bytes(),
        'S01app.proposal': (source / 'internal-bootstrap/S01app.proposal').read_bytes(),
        'transaction.review.sh': TRANSACTION.read_bytes(),
    }
    for name, expected in HASHES.items():
        if sha(blobs[name]) != expected:
            raise ValueError(f'{name} identity mismatch')
    observation = collect(reader, blobs['S01app.proposal'], blobs['transaction.review.sh']) if reader else None
    report = {
        'format': 'cammy-stock-bootstrap-readiness/1', 'review_only': True,
        'installation_authorized': False, 'candidate_activation_allowed': False,
        'boot_test_performed': False, 'internal_write_set': ['/etc/init.d/S01app'],
        'tf_changes': [], 'config_changes': [], 'select_gate': 'blocked',
        'source_package_manifest_sha256': validation['manifest_sha256'],
        'members': {name: {'sha256': sha(blob), 'size': len(blob), 'mode': '0644'}
                    for name, blob in blobs.items()},
        'preflight': observation,
        'next': 'Adam reviews exact one-file change and USB-only recovery boundary before installation.',
    }
    blobs['readiness.json'] = (json.dumps(report, indent=2, sort_keys=True) + '\n').encode()
    out.mkdir(parents=True, mode=0o700)
    for name, blob in blobs.items():
        fd = os.open(out / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
    return report


def verify(kit: Path) -> dict:
    """Recheck local kit bytes and inert metadata; this grants no write authority."""
    expected = dict(HASHES, **{'transaction.review.sh': sha(TRANSACTION.read_bytes())})
    package._assert_no_symlink_chain(kit.absolute())
    if {p.name for p in kit.iterdir()} != set(expected) | {'readiness.json'}:
        raise ValueError('unexpected or missing kit member')
    for name in [*expected, 'readiness.json']:
        p = kit/name
        st = p.lstat()
        if not p.is_file() or p.is_symlink() or st.st_nlink != 1 or st.st_mode & 0o777 != 0o644:
            raise ValueError(f'inert regular single-link file required: {name}')
    receipt = json.loads((kit/'readiness.json').read_text())
    if (receipt.get('format') != 'cammy-stock-bootstrap-readiness/1'
            or receipt.get('installation_authorized') is not False
            or receipt.get('candidate_activation_allowed') is not False
            or receipt.get('boot_test_performed') is not False
            or receipt.get('review_only') is not True
            or receipt.get('internal_write_set') != ['/etc/init.d/S01app']
            or receipt.get('tf_changes') != [] or receipt.get('config_changes') != []
            or receipt.get('select_gate') != 'blocked'):
        raise ValueError('kit scope or authority metadata changed')
    for name, digest in expected.items():
        blob = (kit/name).read_bytes()
        if sha(blob) != digest or receipt['members'].get(name) != {
                'sha256': digest, 'size': len(blob), 'mode': '0644'}:
            raise ValueError(f'kit identity mismatch: {name}')
    return {'verified': True, 'review_only': True, 'installation_authorized': False,
            'receipt_sha256': sha((kit/'readiness.json').read_bytes()), 'members': expected}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, default=DEFAULT_PACKAGE)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--out-dir', type=Path)
    mode.add_argument('--verify', type=Path, help='verify a local kit without device access')
    parser.add_argument('--adb', type=Path, help='optional read-only USB preflight; no camera writes')
    parser.add_argument('--serial', default='20080411')
    parser.add_argument('--port', type=int, default=5038)
    args = parser.parse_args(argv)
    try:
        if args.verify:
            if args.adb:
                raise ValueError('--verify does not use ADB')
            print(json.dumps(verify(args.verify)))
            return 0
        result = build(args.package, args.out_dir,
                       AdbReader(args.adb, args.serial, args.port) if args.adb else None)
        verify(args.out_dir)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'ok': False, 'reason': str(exc)}))
        return 1
    print(json.dumps({'kit': str(args.out_dir), 'review_only': True,
                      'installation_authorized': False,
                      'read_only_preflight_passed': result['preflight']['read_only_preflight_passed']
                      if result['preflight'] else None}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
