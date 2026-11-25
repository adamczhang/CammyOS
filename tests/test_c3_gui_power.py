"""CCC window Start/Stop: the button follows systemctl is-active of the C3 service."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
try:
    from c3 import gui
except (ImportError, ValueError):
    gui = None


@unittest.skipUnless(gui, 'GTK bindings not installed (use the system python3)')
class PowerButtonTests(unittest.TestCase):
    def test_button_follows_the_service_state(self):
        self.assertEqual(gui.service_button('active')[:3], ('Stop', 'destructive-action', True))
        for state in ('inactive', 'failed'):
            self.assertEqual(gui.service_button(state)[:3], ('Start', 'suggested-action', True))
        self.assertFalse(gui.service_button('activating')[2])
        self.assertFalse(gui.service_button('deactivating')[2])
        self.assertFalse(gui.service_button('unknown')[2])     # never act blind

    def test_systemctl_targets_only_the_c3_user_service(self):
        done = subprocess.CompletedProcess([], 3, stdout='inactive\n', stderr='')
        with mock.patch.object(gui.subprocess, 'run', return_value=done) as run:
            self.assertEqual(gui.systemctl('is-active'), (3, 'inactive'))
        self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'is-active', 'cammy-c3.service'])
        with mock.patch.object(gui.subprocess, 'run', side_effect=subprocess.TimeoutExpired('systemctl', 30)):
            self.assertEqual(gui.systemctl('stop')[0], 1)


if __name__ == '__main__':
    unittest.main()
