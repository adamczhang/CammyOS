"""Read-only USB contracts and confined synthetic one-file transaction fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import bootstrap_readiness as B


BOOT = '10000000-0000-0000-0000-000000000001'
# Nonzero exits make accidental execution fail the transaction fixtures.
STOCK = b'#!/bin/sh\n# synthetic stock\nexit 73\n'
NEXT = b'#!/bin/sh\n# synthetic replacement\nexit 74\n'
APP = b'synthetic application, never executed\n'
META = f'''BOOT={BOOT}
UPTIME=300.00 0
--IDENTITY--
Uid: 0 0 0 0
Gid: 0 0 0 0
--PATHS--
drwxr-xr-x 2 0 0 4096 Nov 14 00:00 /etc/init.d
-rwxr-xr-x 1 0 0 4246 Nov 14 00:00 /etc/init.d/S01app
--COMMANDS--
cat=1
chmod=1
mkdir=1
rm=1
mv=1
sync=1
md5sum=1
wc=1
cut=1
awk=1
ls=1
--MOUNTS--
tmpfs /run tmpfs rw,relatime 0 0
--NATIVE--
PID=778
/usr/bin/sdvcam
/dev/watchdog
'''.encode()


class Reader:
    def __init__(self, *, bad_control=False, reboot=False, metadata=META):
        self.calls = []
        self.bad_control = bad_control
        self.reboot = reboot
        self.metadata = metadata

    def shell(self, cmd, data=b''):
        self.calls.append((cmd, data))
        if cmd == 'cat /proc/sys/kernel/random/boot_id':
            return ((BOOT if len(self.calls) == 1 or not self.reboot else
                     '20000000-0000-0000-0000-000000000002') + '\n').encode()
        if cmd == 'cat /etc/init.d/S01app':
            return STOCK
        if cmd == '/tmp/cammy-runtime/busybox sha256sum /etc/init.d/S01app':
            return (B.sha(STOCK) + '  /etc/init.d/S01app\n').encode()
        if cmd == '/tmp/cammy-runtime/busybox sha256sum /usr/bin/sdvcam':
            return (B.sha(APP) + '  /usr/bin/sdvcam\n').encode()
        if cmd == B.PARSE_COMMAND:
            rc = 73 if self.bad_control else (2 if data == b'if then\n' else 0)
            return f'CAMMY_PARSE_RC={rc}\n'.encode()
        if cmd == B.META_COMMAND:
            return self.metadata
        raise AssertionError(f'unexpected remote command {cmd}')


class ReadOnlyTests(unittest.TestCase):
    def collect(self, reader):
        with patch.object(B.package, 'STOCK_S01APP_SHA256', B.sha(STOCK)), \
             patch.object(B.package, 'STOCK_SDVCAM_SHA256', B.sha(APP)):
            return B.collect(reader, NEXT, B.TRANSACTION.read_bytes())

    def test_exact_parse_controls_precede_startup_script_and_no_write_commands(self):
        reader = Reader()
        report = self.collect(reader)
        self.assertTrue(report['read_only_preflight_passed'], report['checks'])
        data = [blob for cmd, blob in reader.calls if cmd == B.PARSE_COMMAND]
        self.assertEqual(data[:2], [b'exit 73\n', b'if then\n'])
        self.assertEqual(data[2], NEXT)
        self.assertFalse(report['camera_files_written'])
        self.assertFalse(report['boot_test_performed'])

    def test_failed_control_never_sends_real_script(self):
        reader = Reader(bad_control=True)
        result = self.collect(reader)
        self.assertFalse(result['read_only_preflight_passed'])
        self.assertEqual(result['parse_results'], {})
        self.assertNotIn(NEXT, [data for _, data in reader.calls])

    def test_changed_boot_or_missing_owner_or_noexec_fails_preflight(self):
        for reader in [Reader(reboot=True), Reader(metadata=META.replace(b'/dev/watchdog', b'/dev/null')),
                       Reader(metadata=META.replace(b'rw,relatime', b'rw,noexec')),
                       Reader(metadata=META.replace(b'mv=1', b'mv=0')),
                       Reader(metadata=META.replace(b'Uid: 0 0', b'Uid: 0 1'))]:
            with self.subTest(reader=reader.metadata):
                self.assertFalse(self.collect(reader)['read_only_preflight_passed'])

    def test_pty_colour_does_not_invalidate_directory_evidence(self):
        r = Reader(metadata=META.replace(b'/etc/init.d', b'\x1b[1;34m/etc/init.d\x1b[0m'))
        self.assertTrue(self.collect(r)['read_only_preflight_passed'])

    def test_unmarked_parse_result_is_not_success(self):
        with self.assertRaises(ValueError):
            B.parser_result(b'')

    def test_legacy_pty_parser_hashes_streamed_input_before_syntax_parse(self):
        completed = subprocess.CompletedProcess([], 0, b'CAMMY_PARSE_RC=0\r\n', b'')
        with patch.object(B.subprocess, 'run', return_value=completed) as run:
            result = B.AdbReader(Path('/test/adb'), 'test-serial', 5038).shell(B.PARSE_COMMAND, NEXT)
        args, kwargs = run.call_args
        self.assertNotIn('-T', args[0])
        self.assertEqual(kwargs['input'], NEXT + b'__CAMMY_EOF__\n\x04')
        self.assertIn(B.sha(NEXT), args[0][-1])
        self.assertIn('printf "%s" "$code" | /bin/sh -n;', args[0][-1])
        self.assertNotIn(' >', args[0][-1])
        self.assertEqual(result, b'CAMMY_PARSE_RC=0\n')

    def test_parser_refuses_embedded_terminal_controls(self):
        with patch.object(B.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                B.AdbReader(Path('/test/adb'), 'serial', 5038).shell(B.PARSE_COMMAND, b'exit\x03\n')
        run.assert_not_called()

    def test_builder_has_only_four_inert_members_and_refuses_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'kit'
            result = B.build(B.DEFAULT_PACKAGE, dest)
            self.assertEqual(result['internal_write_set'], ['/etc/init.d/S01app'])
            self.assertEqual(result['tf_changes'], [])
            self.assertEqual(result['config_changes'], [])
            self.assertIsNone(result['preflight'])
            self.assertEqual(set(p.name for p in dest.iterdir()),
                             {'S01app.stock', 'S01app.proposal', 'transaction.review.sh', 'readiness.json'})
            for p in dest.iterdir():
                self.assertEqual(p.stat().st_mode & 0o777, 0o644)
            self.assertTrue(B.verify(dest)['verified'])
            with self.assertRaises(ValueError):
                B.build(B.DEFAULT_PACKAGE, dest)
            (dest/'transaction.review.sh').write_text('# altered\n')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                B.verify(dest)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.init = self.root / 'init.d'
        self.init.mkdir()
        self.init.chmod(0o755)  # Match live init metadata, independent of host umask.
        self.target = self.init / 'S01app'
        self.target.write_bytes(STOCK)
        self.target.chmod(0o755)
        self.app = self.root / 'app'
        self.app.write_bytes(APP)
        self.kit = self.root / 'kit'
        self.kit.mkdir(mode=0o700)
        (self.kit / 'S01app.stock').write_bytes(STOCK)
        (self.kit / 'S01app.proposal').write_bytes(NEXT)
        proc = self.root / 'proc/self'
        proc.mkdir(parents=True)
        (proc / 'status').write_text(f'Uid: {os.getuid()} {os.getuid()}\nGid: {os.getgid()} {os.getgid()}\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'sync').write_text('#!/bin/sh\nexit 0\n')
        (self.bin / 'sync').chmod(0o755)
        for missing in ('chown', 'rmdir'):
            (self.bin/missing).write_text('#!/bin/sh\nexit 99\n')
            (self.bin/missing).chmod(0o755)
        self.script = self.root / 'transaction.sh'
        source = B.TRANSACTION.read_text()
        values = {'TARGET': str(self.target), 'APP': str(self.app), 'PROCFS': str(self.root/'proc'),
                  'PATH': str(self.bin)+':/bin:/sbin:/usr/bin:/usr/sbin',
                  'EXPECT_UID': str(os.getuid()), 'EXPECT_GID': str(os.getgid()),
                  'STOCK_MD5': hashlib.md5(STOCK).hexdigest(), 'STOCK_SIZE': str(len(STOCK)),
                  'NEXT_MD5': hashlib.md5(NEXT).hexdigest(), 'NEXT_SIZE': str(len(NEXT)),
                  'APP_MD5': hashlib.md5(APP).hexdigest(), 'APP_SIZE': str(len(APP))}
        # Only exact constant assignments are relocated; all executed paths stay in the fixture.
        for key, value in values.items():
            lines = source.splitlines(keepends=True)
            found = [i for i, line in enumerate(lines) if line.startswith(key+'=')]
            self.assertEqual(len(found), 1)
            lines[found[0]] = f'{key}={value}\n'
            source = ''.join(lines)
        self.script.write_text(source)

    def run_action(self, action):
        env = dict(os.environ, PATH=str(self.bin)+':'+os.environ['PATH'])
        return subprocess.run(['/bin/dash', str(self.script), action, str(self.kit)],
                              capture_output=True, timeout=5, env=env)

    def assert_stock(self):
        self.assertEqual(self.target.read_bytes(), STOCK)
        self.assertEqual(self.app.read_bytes(), APP)

    def test_install_and_exact_restore_without_launch_or_config(self):
        result = self.run_action('install')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), NEXT)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.app.read_bytes(), APP)
        result = self.run_action('restore')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_stock()
        self.assertEqual(list(self.init.iterdir()), [self.target])

    def test_wrong_current_source_or_app_refuses_before_mutation(self):
        self.target.write_bytes(b'unknown current init')
        self.assertNotEqual(self.run_action('install').returncode, 0)
        self.assertEqual(self.target.read_bytes(), b'unknown current init')
        self.target.write_bytes(STOCK)
        for name in ['S01app.stock', 'S01app.proposal']:
            p = self.kit/name
            blob = p.read_bytes()
            p.write_bytes(b'bad')
            self.assertNotEqual(self.run_action('install').returncode, 0)
            self.assert_stock()
            p.write_bytes(blob)
        self.app.write_bytes(b'wrong app')
        self.assertNotEqual(self.run_action('install').returncode, 0)
        self.assertEqual(self.target.read_bytes(), STOCK)

    def test_existing_lock_symlink_and_nonprivate_kit_refuse(self):
        lock = Path(str(self.target)+'.cammy-transaction')
        lock.mkdir()
        (lock/'sentinel').write_text('preserve')
        self.assertNotEqual(self.run_action('install').returncode, 0)
        self.assertEqual((lock/'sentinel').read_text(), 'preserve')
        self.assert_stock()
        (lock/'sentinel').unlink(); lock.rmdir()
        self.init.chmod(0o777)
        self.assertNotEqual(self.run_action('install').returncode, 0)
        self.assert_stock()
        self.init.chmod(0o755)
        self.kit.chmod(0o755)
        self.assertNotEqual(self.run_action('install').returncode, 0)
        self.assert_stock()
        self.kit.chmod(0o700)
        self.target.unlink(); self.target.symlink_to(self.kit/'S01app.stock')
        self.assertNotEqual(self.run_action('install').returncode, 0)
        self.assertTrue(self.target.is_symlink())

    def test_sync_failure_before_rename_preserves_original(self):
        (self.bin/'sync').write_text('#!/bin/sh\nexit 1\n')
        self.assertNotEqual(self.run_action('install').returncode, 0)
        self.assert_stock()
        self.assertFalse(Path(str(self.target)+'.cammy-transaction').exists())

    def test_sync_failure_after_rename_reports_failure_and_leaves_known_replacement(self):
        count = self.root/'sync-count'
        (self.bin/'sync').write_text(f'#!/bin/sh\nif [ -f "{count}" ]; then exit 1; fi\n: >"{count}"\n')
        result = self.run_action('install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'do not reboot', result.stderr)
        self.assertEqual(self.target.read_bytes(), NEXT)
        self.assertEqual(self.app.read_bytes(), APP)


if __name__ == '__main__':
    unittest.main()
