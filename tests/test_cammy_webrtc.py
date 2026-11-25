"""Real GStreamer/official SDK loopback; all source pixels/PCM are synthetic."""
import importlib.util
import logging
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'host'),str(ROOT/'sdk')]
ENV=importlib.util.find_spec('reachy_mini') is not None
if ENV:
    import numpy as np
    from reachy_mini.media.gstreamer_utils import Gst
    Gst.init([])
    ENV=all(Gst.ElementFactory.find(n) for n in ('webrtcsink','webrtcsrc','webrtcbin','errorignore'))
if ENV:
    from cammy_webrtc import WebRTCPublisher
    from cammy_reachy_media import CammyMediaManager,camera_specs
    from reachy_mini.media.media_manager import MediaManager,MediaBackend
    from reachy_mini.media.webrtc_utils import get_producer_list


def wait_for(predicate,timeout=6):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('WebRTC fixture timed out')
        time.sleep(.02)


class SyntheticSource:
    def __init__(self):
        self.phase='idle';self.session=None;self.boot='11111111-2222-3333-4444-555555555555'
        self.patterns=(bytes((220,40,20))*(640*360),bytes((20,80,220))*(640*360))
        self.pcm=(np.sin(np.arange(1600)*2*np.pi*440/16000)*12000).astype('<i2').tobytes()
        self.stops=0
    def start(self,name='synthetic-one'):
        self.session=name;self.phase='live';self.since=time.monotonic()
    def stop(self,wait=False,session=None):
        if session is not None and session != self.session:return
        self.phase='idle';self.stops+=1
    def status(self):
        return {'phase':self.phase,'session':self.session,'boot_id':self.boot,
                'synthetic':True,'frame_age_seconds':.01,'audio_age_seconds':.01}
    def latest_pixels(self):
        if self.phase!='live':raise RuntimeError('not live')
        sequence=int((time.monotonic()-self.since)*5)+1
        at=self.since+(sequence-1)/5
        return (self.patterns[sequence%2],sequence,at,time.time()),self.session,self.boot
    def audio_tail(self,after):
        if self.phase!='live':raise RuntimeError('not live')
        latest=int((time.monotonic()-self.since)*10)
        first=max(after+1,latest-49)
        return self.pcm*max(0,latest-first+1),{'session':self.session,'boot_id':self.boot,
                'latest_sequence':latest,'first_sequence':first,
                'history_gap':bool(after and first>after+1),
                'last_receipt_monotonic':self.since+latest/10,'samples_total':latest*1600}


class FixtureControl:
    def __init__(self,source,publisher):self.source,self.publisher=source,publisher;self.stops=[];self.starts=0
    def state(self):return dict(self.source.status(),webrtc=self.publisher.status())
    def start(self,seconds):
        self.starts+=1;self.source.start('owned-'+str(self.starts));self.publisher.start(self.source.session)
        return self.state()
    def stop(self,session):
        self.stops.append(session)
        if self.source.session!=session:raise RuntimeError('different owner')
        self.source.stop();self.publisher.stop()
        return self.state()


@unittest.skipUnless(ENV,'Use project WebRTC dependency profile and official Reachy venv')
class WebRTCTests(unittest.TestCase):
    def setUp(self):
        self.doa=mock.patch('reachy_mini.media.audio_base.AudioDoA')
        self.doa.start()  # no physical USB probing in fixtures
        self.source=SyntheticSource();self.pub=WebRTCPublisher(self.source)
        self.control=FixtureControl(self.source,self.pub)
        self.media=None
    def tearDown(self):
        if self.media is not None:self.media.close()
        self.source.stop();self.pub.stop()
        self.doa.stop()
        self.assertTrue(self.pub.status()['cleanup_confirmed'],self.pub.status())
    def ready(self):
        wait_for(lambda:self.pub.server and self.pub.server.advertised.is_set())
    def test_actual_upstream_media_manager_receives_real_webrtc_from_synthetic_source(self):
        self.source.start();self.pub.start(self.source.session);self.ready()
        rows=get_producer_list('127.0.0.1',8443)
        self.assertEqual(len(rows),1)
        self.assertEqual(next(iter(rows.values()))['cammy_source'],'synthetic-fixture')
        # The unchanged public SDK factory proves protocol interoperability.
        media=MediaManager(MediaBackend.WEBRTC,signalling_host='127.0.0.1',camera_specs=camera_specs())
        try:
            image=sample=None;deadline=time.monotonic()+5
            while time.monotonic()<deadline and (image is None or sample is None):
                frame=media.get_frame();audio=media.get_audio_sample()
                if frame is not None:image=frame
                if audio is not None:sample=audio
            self.assertIsNotNone(image);self.assertEqual(image.shape,(360,640,3))
            # Public SDK returns BGR; known lossy-coded source stays close.
            mean=image.mean(axis=(0,1))
            self.assertTrue(any(np.max(np.abs(mean-np.array(v)))<35 for v in ((20,40,220),(220,80,20))),mean)
            self.assertIsNotNone(sample);self.assertEqual(sample.shape[1],2)
            self.assertGreater(float(np.max(np.abs(sample))),.05)
            self.assertTrue(np.allclose(sample[:,0],sample[:,1],atol=.01))
            self.assertFalse(np.isfinite(media.camera.K).all())
        finally:
            client=media.camera;media.close();client._loop.quit();client._thread_bus_calls.join(2)
    def test_managed_sdk_freshness_stop_unknown_calibration_and_no_speaker(self):
        self.media=CammyMediaManager(start_seconds=10,control=self.control)
        self.assertIsInstance(self.media,MediaManager)
        self.assertIsNone(self.media.camera.K);self.assertIsNone(self.media.camera.D)
        self.assertIsNone(self.media.get_DoA())
        with self.assertRaises(NotImplementedError):self.media.push_audio_sample(np.zeros((10,2)))
        with self.assertRaises(NotImplementedError):self.media.enable_wobbling(lambda _:None)
        with self.assertRaises(NotImplementedError):self.media.get_output_channels()
        with self.assertRaises(NotImplementedError):self.media.audio.apply_audio_config()
        deadline=time.monotonic()+5;image=sample=None
        while time.monotonic()<deadline and (image is None or sample is None):
            image=self.media.get_frame() if image is None else image
            sample=self.media.get_audio_sample() if sample is None else sample
            time.sleep(.01)
        self.assertIsNotNone(image);self.assertIsNotNone(sample)
        jpeg=None;deadline=time.monotonic()+3
        while jpeg is None and time.monotonic()<deadline:jpeg=self.media.get_frame_jpeg()
        self.assertIsNotNone(jpeg);self.assertTrue(jpeg.startswith(b'\xff\xd8'))
        self.source.stop()
        wait_for(lambda:self.media._cleanup_done.is_set())
        self.assertIsNone(self.media.get_frame());self.assertIsNone(self.media.get_audio_sample())
        self.assertIsNone(self.media.camera);self.assertEqual(self.control.stops,['owned-1'])
    def test_delayed_old_close_cannot_stop_a_new_app_session(self):
        self.media=CammyMediaManager(start_seconds=10,control=self.control)
        self.source.session='replacement-owner'
        wait_for(lambda:self.media._cleanup_done.is_set())
        self.assertEqual(self.source.phase,'live')
        self.assertEqual(self.control.stops,['owned-1'])
        self.assertEqual(self.source.stops,0)
    def test_sdk_disconnect_and_reconnect_during_same_explicit_window(self):
        self.source.start();self.pub.start(self.source.session);self.ready()
        self.media=CammyMediaManager(control=self.control)
        wait_for(lambda:self.media.get_frame() is not None)
        self.media.close()
        wait_for(lambda:not self.pub.server.sessions)
        self.assertEqual(self.source.phase,'live')
        self.assertEqual(self.pub.phase,'publishing')
        self.media=CammyMediaManager(control=self.control)
        wait_for(lambda:self.media.get_frame() is not None)
        self.assertEqual(self.source.phase,'live')

    def test_no_implicit_sensing_when_constructed_at_idle(self):
        with self.assertRaises(RuntimeError):CammyMediaManager(control=self.control)
        self.assertEqual(self.control.starts,0)
        self.assertEqual(self.source.phase,'idle')


@unittest.skipUnless(ENV,'Use project WebRTC dependency profile and official Reachy venv')
class SDKCleanupFailureTests(unittest.TestCase):
    def test_owner_release_is_attempted_even_if_upstream_sdk_cleanup_raises(self):
        manager=CammyMediaManager.__new__(CammyMediaManager)
        manager._lock=threading.RLock();manager._closed=False;manager._valid=True
        manager._watch_cancel=threading.Event();manager._cleanup_done=threading.Event()
        manager._watcher=None;manager._owned=True;manager._binding=('owned-session','boot')
        manager._requested_session='owned-session';manager.camera=None;manager.audio=None
        manager.control=mock.Mock()
        with mock.patch.object(MediaManager,'close',side_effect=RuntimeError('fixture SDK failure')):
            with self.assertRaises(RuntimeError):manager.close()
        manager.control.stop.assert_called_once_with('owned-session')
        self.assertTrue(manager._cleanup_done.is_set())
        self.assertTrue(manager._closed)
