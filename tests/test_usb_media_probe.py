"""Host-only tests. No hardware device is opened."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('usb_probe',ROOT/'host/usb_media_probe.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


class USBProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.usb=self.root/'bus/usb/devices/1-4';self.usb.mkdir(parents=True)
        for name,text in [('idVendor','18d1'),('idProduct','100d'),('serial','20080411')]:
            (self.usb/name).write_text(text)
        interface=self.usb/'1-4:1.2';interface.mkdir()
        card=self.root/'class/sound/card7';card.mkdir(parents=True)
        (card/'device').symlink_to(interface)

    def test_exact_identity_and_topology_not_default_audio(self):
        self.assertEqual(probe.identify(self.root)['cards'],[7])
        (self.usb/'serial').write_text('another-camera')
        with self.assertRaises(RuntimeError):probe.identify(self.root)

    def test_missing_audio_card_does_not_fallback(self):
        (self.root/'class/sound/card7/device').unlink()
        with self.assertRaises(RuntimeError):probe.identify(self.root)

    def test_stop_or_expired_lease_aborts_capture(self):
        process=Mock();process.poll.return_value=None
        client=Mock(agent='ours');client.state.return_value={'owner':None,'lease_remaining_seconds':0}
        with self.assertRaisesRegex(RuntimeError,'lease lost'):
            probe.supervise(process,client,lambda:None,10,clock=lambda:0,sleep=lambda _:None)

    def test_deadline_and_device_disconnect_abort(self):
        process=Mock();process.poll.return_value=None
        client=Mock(agent='ours');client.state.return_value={'owner':'ours','lease_remaining_seconds':3}
        with self.assertRaisesRegex(RuntimeError,'deadline'):
            probe.supervise(process,client,lambda:None,1,clock=lambda:2,sleep=lambda _:None)
        def disconnected():raise RuntimeError('device lost')
        with self.assertRaisesRegex(RuntimeError,'device lost'):
            probe.supervise(process,client,disconnected,10,clock=lambda:0,sleep=lambda _:None)

    def test_timeout_cleanup_terminates_and_reaps_only_probe_process(self):
        process=Mock();process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('arecord',.3),0]
        probe.stop_process(process)
        process.terminate.assert_called_once();process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count,2)

    def test_capture_failure_still_closes_probe_and_releases_lease(self):
        proc=self.root/'proc';stream=proc/'asound/card7/stream0'
        stream.parent.mkdir(parents=True);stream.write_text('Capture:\nFormat: S16_LE\nRates: 16000\n')
        client=Mock(agent='ours');client.identity.return_value={'os':'CammyOS'}
        client.state.return_value={'owner':None,'lease_remaining_seconds':0}
        process=Mock();process.poll.return_value=None
        with patch.object(probe.shutil,'which',return_value='/usr/bin/arecord'), patch.object(probe.subprocess,'Popen',return_value=process) as spawn:
            with self.assertRaisesRegex(RuntimeError,'lease lost'):
                probe.capture(client,self.root/'sample.wav',3,self.root,proc)
        client.release.assert_called_once();process.terminate.assert_called_once()
        args=spawn.call_args.args[0]
        self.assertEqual(args[args.index('-D')+1],'hw:7,0')
