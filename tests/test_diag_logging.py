"""Opt-in TF diagnostics in the runtime startup scripts (PLAN milestone 1)."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STARTUP = (ROOT / 'scripts/tf-runtime-startup.sh').read_text()
NATIVE = (ROOT / 'scripts/native-media-start.sh').read_text()


def function(source, name):
    match = re.search(r'^%s\(\) ?\{.*?^\}\n' % re.escape(name), source, re.M | re.S)
    assert match, name
    return match.group(0)


class DiagLoggingTests(unittest.TestCase):
    def run_sh(self, script, env=None):
        return subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                              env={**os.environ, **(env or {})}, timeout=20)

    def test_diag_appends_uptime_and_message(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'boot.log'
            out = self.run_sh('DIAG_LOG=%s\n%sdiag "phase radio_load"\ndiag second' % (log, function(STARTUP, 'diag')))
            self.assertEqual(out.returncode, 0, out.stderr)
            lines = log.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertRegex(lines[0], r'^\d+\.\d+ phase radio_load$')

    def test_diag_is_noop_without_flag(self):
        out = self.run_sh('DIAG_LOG=\n%sdiag nothing' % function(STARTUP, 'diag'))
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_heartbeat_stops_after_window(self):
        # Host uptime is far past the 420 s window, so it must end immediately.
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'boot.log'
            script = 'DIAG_LOG=%s\nR=%s\n%s%sdiag_heartbeat' % (
                log, d, function(STARTUP, 'diag'), function(STARTUP, 'diag_heartbeat'))
            out = self.run_sh(script)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue(log.read_text().rstrip().endswith('hb end'))

    def test_diag_requires_exact_opt_in_and_is_bounded(self):
        self.assertIn('[ "$(cat "$CARD/enable-diag")" = cammy-diag-v1 ]', STARTUP)
        self.assertIn('[ ! -L "$CARD/enable-diag" ]', STARTUP)
        self.assertIn('-lt 200 ]', STARTUP)
        self.assertIn('[ "$up" -lt 420 ] || break', STARTUP)

    def test_phases_cover_running_and_latch(self):
        self.assertIn('diag "phase $1"', STARTUP)
        self.assertLess(STARTUP.index('native-media exit'), STARTUP.index('phase running'))
        self.assertIn("diag 'latch cleared'", STARTUP)

    def test_native_notes_bracket_the_one_shot_command(self):
        send, ack = NATIVE.index('note init-send'), NATIVE.index('note "init-ack=$ACK"')
        self.assertLess(send, NATIVE.index('"$C" init'))
        self.assertLess(NATIVE.index('"$C" init'), ack)
        for step in ('acquired', 'guard-ready', 'restored', 'mode-photo', 'mode-video', 'started', 'released'):
            self.assertIn('note %s' % step, NATIVE)

    def test_kernel_key_dumps_are_filtered(self):
        line = re.search(r"sed -e '(/key: [^']*/d)'", STARTUP).group(1)
        sample = ('[ 51.76] [ecrnx] add_key() enter\n'
                  '[ 51.76] key: 00000000: b9 63 ec 6f c1 53 7a 21  .c.o.Sz!\n'
                  '[ 51.77] wlan0: connected\n')
        out = subprocess.run(['sed', '-e', line], input=sample, capture_output=True, text=True).stdout
        self.assertNotIn('key: 0000', out)
        self.assertEqual(out.count('\n'), 2)

    def test_native_note_respects_env(self):
        note = re.search(r'^note\(\)\{.*\}$', NATIVE, re.M).group(0)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'n.log'
            self.assertEqual(self.run_sh('%s\nnote x' % note, {'CAMMY_DIAG_LOG': ''}).returncode, 0)
            self.assertFalse(log.exists())
            self.run_sh('%s\nnote init-send' % note, {'CAMMY_DIAG_LOG': str(log)})
            self.assertRegex(log.read_text(), r'^\d+\.\d+ nv init-send\n$')


class FirstFaultTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.proc = self.root / 'proc'
        self.card = self.root / 'card'
        self.proc.mkdir()
        self.card.mkdir()
        self.reports = self.card / 'CammyOS-faults'
        (self.proc / 'mounts').write_text(f'/dev/mmcblk0p1 {self.card} vfat rw 0 0\n')
        (self.proc / 'meminfo').write_text('MemFree: 1234 kB\n')
        for pid, name in [(9, 'sdvcam'), (10, 'secret-process')]:
            folder = self.proc / str(pid)
            folder.mkdir()
            (folder / 'stat').write_text(f'{pid} ({name}) S ' + '0 '*18 + '42 4096 3\n')
        wrappers = self.root / 'wrappers.c'
        wrappers.write_text(r"""
#define _GNU_SOURCE
#include <errno.h>
#include <linux/magic.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <unistd.h>

int __wrap_fstatfs(int fd, struct statfs *fs)
{
    (void)fd;
    memset(fs, 0, sizeof *fs);
    fs->f_type = MSDOS_SUPER_MAGIC;
    fs->f_bsize = 4096;
    fs->f_bavail = getenv("LOW") ? 1 : 1024;
    return 0;
}
long __wrap_syscall(long number, ...)
{
    if (number != SYS_syslog) return -1;
    const char *log = "<4>[ 1.000001] sdvcam[9]: segfault secret-argv\n"
                      "<6>[ 2.000002] wifi key: secret-key\n"
                      "<4>[ 3.000003] Killed process 9 secret-cmdline\n";
    va_list args;
    va_start(args, number);
    (void)va_arg(args, int);
    memcpy(va_arg(args, char *), log, strlen(log));
    va_end(args);
    return (long)strlen(log);
}
int __wrap_fsync(int fd)
{
    (void)fd;
    if (getenv("STALL")) sleep(10);
    if (getenv("ENOSPC")) { errno = ENOSPC; return -1; }
    return 0;
}
""")
        self.binary = self.root / 'fault-evidence'
        self.native_log = self.root / 'sdvcam.INFO'
        self.native_log.write_bytes(b'x' * 20000 + b'\nI0108 01:02:34.5 main.cpp:950] \x1b[60D\x1b[60C<Update> msg received\n'
                                    b'I0108 01:02:35.0 net.cpp:1] connect ssid=HomeNet password=secret\n'
                                    b'F0108 01:02:36.0 media.cpp:77] Check failed: venc != nullptr\n')
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', f'-DPROC="{self.proc}"', f'-DNATIVE_LOG="{self.native_log}"',
                        f'-DCARD="{self.card}"', str(ROOT/'src/boot/fault_evidence.c'), str(wrappers),
                        '-Wl,--wrap=syscall,--wrap=fstatfs,--wrap=fsync', '-o', str(self.binary)],
                       check=True, capture_output=True)

    def snapshot(self, i=1, **flags):
        return subprocess.run([str(self.binary), 'snapshot', f'{i:08d}-0000-4000-8000-{i:012d}',
                               '0'*24, 'native-pid', 'idle_ready', '1234'],
                              env={**os.environ, **flags}, capture_output=True, timeout=6)

    def test_bounded_rotation_and_only_allowlisted_data(self):
        for i in range(1, 8):
            self.assertEqual(self.snapshot(i).returncode, 0)
        files = sorted(self.reports.iterdir())
        self.assertEqual(len(files), 4)
        for file in files:
            data = file.read_text()
            self.assertLess(len(data), 32 * 1024)
            self.assertIn('MemFree: 1234 kB', data)
            self.assertIn('9 sdvcam S 42 3', data)
            self.assertIn('10 other S 42 3', data)
            self.assertIn('1.000001 sdvcam-segfault', data)
            self.assertIn('3.000003 oom', data)
            self.assertNotIn('secret', data)
            self.assertNotIn('HomeNet', data)
            tail = data.split('[sdvcam log tail]\n', 1)[1]
            self.assertIn('main.cpp:950] <Update> msg received\n', tail)
            self.assertIn('[line withheld]\n', tail)
            self.assertTrue(tail.endswith('Check failed: venc != nullptr\n'))
            self.assertNotIn('\x1b', tail)
            self.assertTrue(all(len(line) <= 200 for line in tail.splitlines()))

    def test_absent_card_or_low_space_never_creates_report_directory(self):
        self.assertNotEqual(self.snapshot(LOW='1').returncode, 0)
        (self.proc / 'mounts').write_text('')
        self.assertNotEqual(self.snapshot().returncode, 0)
        self.assertFalse(self.reports.exists())

    def test_failed_and_stalled_flush_leave_only_partial_file(self):
        self.assertNotEqual(self.snapshot(ENOSPC='1').returncode, 0)
        self.assertEqual([p.name for p in self.reports.iterdir()], ['.pending'])
        self.assertLess(self.snapshot(STALL='1').returncode, 0)
        self.assertEqual([p.name for p in self.reports.iterdir()], ['.pending'])
        self.assertEqual(self.snapshot().returncode, 0)
        self.assertEqual([p.name for p in self.reports.iterdir()], ['fault-0.txt'])


if __name__ == '__main__':
    unittest.main()
