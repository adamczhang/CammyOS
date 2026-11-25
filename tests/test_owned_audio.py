"""Synthetic sink only; the fixture has no audio/device/network implementation."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ('completion', 'cancel_queued', 'lease_before_dispatch', 'deadline_before_dispatch',
             'stop_pins_buffer', 'stop_refused', 'submit_no_effect', 'submit_uncertain',
             'binding_replay', 'pcm_bounds', 'stale_events', 'finite_timeout',
             'lease_expiry', 'native_unavailable', 'overflow_clock', 'disconnect_queued',
             'backend_fault_cleanup', 'queued_failure_event')


class OwnedAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        command = os.environ.get('CAMMY_AUDIO_FIXTURE')
        if command:
            cls.command = json.loads(command)
        else:
            subprocess.run(['make', '-s', '-C', 'native/audio', 'host'], cwd=ROOT,
                           check=True, capture_output=True, timeout=30)
            cls.command = [str(ROOT / 'build/native-audio/host/audio-fixture')]

    def scenario(self, number):
        result = subprocess.run([*self.command, str(number)], cwd=ROOT, capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['scenario'], number)
        self.assertGreater(data['checks'], 4)
        self.assertTrue(data['synthetic'])
        self.assertFalse(data['physical_available'])


def _case(number):
    def test(self):
        self.scenario(number)
    return test


for _number, _name in enumerate(SCENARIOS):
    setattr(OwnedAudioTests, 'test_' + _name, _case(_number))
