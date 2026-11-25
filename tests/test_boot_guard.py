"""Failure-latch acceptance using a relocated init filesystem and fake launcher."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / 'etc/cammy'
        self.card = self.root / 'mnt/extsd/CammyOS'
        for directory in [self.config, self.card, self.root / 'tmp', self.root / 'bin',
                          self.root / 'proc/sys/kernel/random']:
            directory.mkdir(parents=True, exist_ok=True)
        for name in ['home.conf', 'portal.token']:
            (self.config / name).write_text('test-fixture\n')
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('fixture-boot\n')
        (self.root / 'bin/sync').write_text('#!/bin/sh\nexit 0\n')
        (self.root / 'bin/sync').chmod(0o700)
        self.marker = self.config / 'startup.pending'
        self.invoked = self.root / 'invoked'
        self.launcher = self.card / 'daemon-exec'
        self.launcher.write_text('#!/bin/sh\n'
            f'test -s "{self.marker}" || exit 80\n'
            f'echo invoked >>"{self.invoked}"\nexit 0\n')
        self.launcher.chmod(0o700)
        source = (ROOT / 'scripts/S99cammy-tf').read_text()
        # One substitution pass prevents rewritten /tmp fixture paths being
        # rewritten again. Actual system directories are never used by the hook.
        relocated = re.sub(r'/(?:etc/cammy|mnt/extsd|tmp|proc)(?=/|[\s";])',
                           lambda match: str(self.root) + match.group(), source)
        self.hook = self.root / 'hook'
        self.hook.write_text(relocated)
        self.env = {**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH']}

    def start(self):
        return subprocess.run(['/bin/sh', str(self.hook), 'start'], env=self.env,
                              capture_output=True, text=True, timeout=3)

    def test_previous_failure_skips_without_touching_marker(self):
        self.marker.write_text('phase=radio_load\nboot_id=earlier\n')
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('previous attempt', result.stdout)
        self.assertFalse(self.invoked.exists())
        self.assertIn('radio_load', self.marker.read_text())

    def test_fresh_attempt_latches_before_launch(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.invoked.read_text(), 'invoked\n')
        self.assertIn('phase=launch', self.marker.read_text())
        self.assertIn('boot_id=fixture-boot', self.marker.read_text())

    def test_failed_launch_does_not_retry_on_next_boot(self):
        self.launcher.write_text(self.launcher.read_text().replace('exit 0', 'exit 12'))
        first = self.start()
        self.assertEqual(first.returncode, 12, first.stderr)
        self.assertTrue(self.marker.exists())
        second = self.start()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.invoked.read_text(), 'invoked\n')

    def test_missing_private_config_stays_stock(self):
        (self.config / 'home.conf').unlink()
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.invoked.exists())
        self.assertFalse(self.marker.exists())
