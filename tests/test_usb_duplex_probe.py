import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from usb_duplex_probe import validate_formats, supervise_pair


class UsbDuplexTests(unittest.TestCase):
    def test_formats_require_both_exact_directions(self):
        valid = 'Playback:\nFormat: S16_LE\nChannels: 1\nRates: 16000\nCapture:\nFormat: S16_LE\nChannels: 1\nRates: 16000'
        validate_formats(valid)
        for bad in [valid.replace('Playback:', 'Missing:'), valid.replace('Channels: 1', 'Channels: 2'),
                    valid.replace('16000', '48000')]:
            with self.assertRaises(RuntimeError):
                validate_formats(bad)

    def test_stop_or_lease_loss_refuses_late_phase(self):
        capture, playback, client = Mock(), Mock(), Mock(agent='test')
        capture.poll.return_value = None
        client.state.return_value = {'owner': None, 'lease_remaining_seconds': 0}
        start = Mock()
        with self.assertRaisesRegex(RuntimeError, 'lease lost'):
            supervise_pair(capture, playback, client, lambda: None, start, 10, clock=lambda: 0)
        start.assert_not_called()

    def test_disconnect_and_deadline_are_bounded(self):
        capture, playback, client = Mock(), Mock(), Mock(agent='test')
        capture.poll.return_value = None
        client.state.return_value = {'owner': 'test', 'lease_remaining_seconds': 5}
        with self.assertRaisesRegex(RuntimeError, 'deadline'):
            supervise_pair(capture, playback, client, lambda: None, Mock(), 1, clock=lambda: 2)
        with self.assertRaisesRegex(RuntimeError, 'device lost'):
            supervise_pair(capture, playback, client, Mock(side_effect=RuntimeError('device lost')), Mock(), 10,
                           clock=lambda: 0)

    def test_near_phase_requires_completed_far_playback(self):
        capture, playback, client = Mock(), Mock(), Mock(agent='test')
        capture.poll.return_value = None
        playback.poll.return_value = None
        client.state.return_value = {'owner': 'test', 'lease_remaining_seconds': 5}
        clock = Mock(side_effect=[0, 3, 3])
        with self.assertRaisesRegex(RuntimeError, 'Far-end playback'):
            supervise_pair(capture, playback, client, lambda: None, Mock(), 10, clock=clock)
