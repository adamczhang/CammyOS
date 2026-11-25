"""Synthetic live pipes and mocked body/network only. No camera or raw devices."""
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import live_media_probe as probe


class LiveMediaTests(unittest.TestCase):
    def factory(self, audio=True, silent=False):
        def args(fd):
            command = [shutil.which('ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error',
                       '-filter_threads', '1', '-re', '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=5']
            if audio:
                source = 'anullsrc=r=16000:cl=mono' if silent else 'sine=frequency=800:sample_rate=16000'
                command += ['-re', '-f', 'lavfi', '-i', source]
            command += ['-map', '0:v:0', '-an', '-pix_fmt', 'gray', '-threads', '1', '-f', 'rawvideo', 'pipe:1']
            if audio:
                command += ['-map', '1:a:0', '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:' + str(fd)]
            return command
        return args

    def test_real_time_synthetic_video_and_audio_arrive_before_source_stops(self):
        result = probe.receive(self.factory(), 2, True, lambda: None)
        self.assertTrue(result['video_and_nonzero_audio_received'])
        self.assertGreater(result['video_frames'], 2)
        self.assertGreater(result['changed_frame_transitions'], 0)
        self.assertGreater(result['audio_samples'], 0)
        self.assertLess(result['first_video_seconds'], result['observed_seconds'])
        self.assertLess(result['first_audio_seconds'], result['observed_seconds'])
        self.assertFalse(result['latency_end_to_end_measured'])
        self.assertFalse(result['physical_capabilities_verified'])

    def test_silent_audio_does_not_pass_mic_acceptance(self):
        result = probe.receive(self.factory(silent=True), 2, True, lambda: None)
        self.assertGreater(result['audio_samples'], 0)
        self.assertEqual(result['audio_nonzero_samples'], 0)
        self.assertFalse(result['video_and_nonzero_audio_received'])

    def test_video_only_is_reported_without_claiming_microphone(self):
        result = probe.receive(self.factory(audio=False), 2, False, lambda: None)
        self.assertTrue(result['live_data_received'])
        self.assertEqual(result['audio_samples'], 0)
        self.assertFalse(result['video_and_nonzero_audio_received'])

    def test_cancellation_reaps_only_the_owned_decoder(self):
        created = []
        spawn = subprocess.Popen
        def start(*args, **kwargs):
            result = spawn(*args, **kwargs); created.append(result); return result
        def cancelled():
            raise RuntimeError('lease lost')
        with patch.object(probe.subprocess, 'Popen', side_effect=start):
            with self.assertRaisesRegex(RuntimeError, 'lease lost'):
                probe.receive(self.factory(), 5, True, cancelled)
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].poll())

    def test_missing_native_listener_does_not_start_session_or_preview(self):
        client = Mock(agent='ours')
        client.identity.return_value = {'os': 'CammyOS'}
        client.state.return_value = {'owner': None}
        with patch.object(probe.socket, 'create_connection', side_effect=ConnectionRefusedError()):
            with self.assertRaises(ConnectionRefusedError):
                probe.run(client, probe.HOSTS[0], 10)
        client.acquire.assert_not_called(); client.preview_start.assert_not_called()
        client.release.assert_not_called()

    def test_lease_loss_does_not_dispatch_global_stop(self):
        client = Mock(agent='ours')
        client.identity.return_value = {'os': 'CammyOS'}
        client.state.side_effect = [{'owner': None}, {'owner': 'new-owner', 'lease_remaining_seconds': 30}]
        with patch.object(probe.socket, 'create_connection', return_value=MagicMock()), \
                patch.object(probe, 'describe', return_value=True), patch.object(probe, 'receive',
                side_effect=lambda factory, seconds, audio, check: check()):
            with self.assertRaisesRegex(RuntimeError, 'lease loss'):
                probe.run(client, probe.HOSTS[0], 10)
        client.release.assert_called_once(); client.stop.assert_not_called()

    def test_another_owner_or_unconfigured_source_refused(self):
        client = Mock()
        client.identity.return_value = {'os': 'CammyOS'}
        client.state.return_value = {'owner': 'other'}
        with self.assertRaises(RuntimeError):
            probe.run(client, probe.HOSTS[0], 10)
        for host in ('127.0.0.1', 'example.com', '192.0.2.53/path'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                probe.command(host)
        client.acquire.assert_not_called()

    def test_bounded_sdp_video_and_audio_and_duplicate_header_refusal(self):
        body = b'v=0\r\nm=video 0 RTP/AVP 96\r\nm=audio 0 RTP/AVP 97\r\n'
        header = b'RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Type: application/sdp\r\nContent-Length: ' + str(len(body)).encode()
        for duplicate in (False, True):
            stream = Mock()
            stream.__enter__ = Mock(return_value=stream)
            stream.__exit__ = Mock(return_value=False)
            stream.recv.side_effect = [header + (b'\r\nCSeq: 1' if duplicate else b'') + b'\r\n\r\n' + body]
            with patch.object(probe.socket, 'create_connection', return_value=stream):
                if duplicate:
                    with self.assertRaisesRegex(ValueError, 'Duplicate'):
                        probe.describe(probe.HOSTS[0])
                else:
                    self.assertTrue(probe.describe(probe.HOSTS[0]))


if __name__ == '__main__':
    unittest.main()
