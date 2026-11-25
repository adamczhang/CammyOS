"""Receive-only Cammy extension of the pinned official Reachy MediaManager.

Uses upstream GstWebRTCClient/MediaManager and the existing GStreamer wire
protocol. Does not create a fake Reachy pose, calibration, speaker or microphone
array. Constructing this manager attaches to an explicitly active app session;
only start_seconds=... requests a new finite sensing session.
"""
import json
import logging
from enum import Enum
import re
import threading
import time
import urllib.error
import urllib.request

import numpy as np
from reachy_mini.media.camera_constants import CameraSpecs
from reachy_mini.media.gstreamer_utils import Gst
from reachy_mini.media.media_manager import MediaManager, MediaBackend
from reachy_mini.media.media_server import GstMediaServer
from reachy_mini.media.webrtc_client_gstreamer import GstWebRTCClient
from reachy_mini.media.webrtc_utils import get_producer_list


class PreviewResolution(Enum):
    PREVIEW=(640,360,5,1.0)


def camera_specs():
    # Explicit invalid matrices prevent adoption of the SDK's calibrated Reachy
    # camera defaults. This is a stream profile, not measured M105 intrinsics.
    return CameraSpecs(name='cammy-m105-uncalibrated',
        available_resolutions=[PreviewResolution.PREVIEW],default_resolution=PreviewResolution.PREVIEW,
        K=np.full((3,3),np.nan),D=np.full((5,),np.nan))


class LabControl:
    def __init__(self):
        self.base='http://127.0.0.1:8801'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.opener.open(self.base+'/',timeout=.5) as response:
            html=response.read(32768).decode()
        match=re.search(r'data-token="([A-Za-z0-9_-]{32,80})"',html)
        if match is None:raise RuntimeError('Cammy Reachy app session token unavailable')
        self.token=match[1]

    def request(self,path,value=None):
        started=time.monotonic()
        req=urllib.request.Request(self.base+path,
            data=None if value is None else json.dumps(value).encode(),
            headers={'X-Cammy-Lab':self.token,'Content-Type':'application/json'})
        with self.opener.open(req,timeout=.5) as response:
            raw=response.read(32769)
        if len(raw)>32768 or time.monotonic()-started>.5:
            raise RuntimeError('Fresh bounded app response required')
        return json.loads(raw)

    def state(self):return self.request('/api/live/status')
    def start(self,seconds):return self.request('/api/live/start',{'seconds':seconds})
    def stop(self,session):return self.request('/api/live/stop',{'session':session})


class _CammyWebRTCClient(GstWebRTCClient):
    def __init__(self,guard,**kwargs):
        self.guard=guard
        super().__init__(**kwargs)
        self._appsink_audio.set_property('max-buffers',5)
        self.resized_K=None

    @property
    def D(self):return None

    def _apply_resolution(self,resolution):
        super()._apply_resolution(resolution)
        self.resized_K=None

    def _configure_webrtcsrc(self,*args):
        source=super()._configure_webrtcsrc(*args)
        source.set_property('stun-server',None)
        return source

    def _on_new_transceiver(self,webrtcbin,transceiver):
        import gi
        gi.require_version('GstWebRTC','1.0')
        from gi.repository import GstWebRTC
        transceiver.set_property('direction',GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY)

    def _setup_audio_send_chain(self):
        # This hardware profile is receive-only; never create a pretend speaker path.
        self._audio_send_ready=False

    def _on_deep_element_added(self,source,subbin,element):
        super()._on_deep_element_added(source,subbin,element)
        factory=element.get_factory()
        if factory and factory.get_name()=='webrtcbin':
            element.set_property('stun-server',None)
            GstMediaServer._disable_upnp(self,element)

    @property
    def _logger(self):return self.logger

    def _sample(self,sink):
        if not self.guard():return None
        sample=sink.try_pull_sample(20_000_000)
        if sample is None or not self.guard():return None
        buffer=sample.get_buffer()
        clock=self._pipeline_record.get_clock()
        if buffer is None or clock is None or buffer.pts==Gst.CLOCK_TIME_NONE:return None
        now=clock.get_time()-self._pipeline_record.get_base_time()
        if now-buffer.pts>2*Gst.SECOND or buffer.pts-now>Gst.SECOND:return None
        return buffer.extract_dup(0,buffer.get_size())

    def read(self):
        data=self._sample(self._appsink_video)
        return None if data is None else np.frombuffer(data,dtype=np.uint8).reshape(self.resolution[1],self.resolution[0],3)

    def get_audio_sample(self):
        data=self._sample(self._appsink_audio)
        return None if data is None else np.frombuffer(data,dtype=np.float32).reshape(-1,2)

    def get_DoA(self):return None
    def apply_audio_config(self,*args,**kwargs):raise NotImplementedError('Cammy has no ReSpeaker control interface')
    def start_playing(self):raise NotImplementedError('Cammy speaker streaming is unavailable')
    def push_audio_sample(self,*args):raise NotImplementedError('Cammy speaker streaming is unavailable')


class CammyMediaManager(MediaManager):
    def __init__(self,start_seconds=None,control=None):
        self.control=control or LabControl()
        self.camera=self.audio=None
        self._lock=threading.RLock()
        self._closed=False
        self._cleanup_done=threading.Event()
        self._owned=start_seconds is not None
        self._watch_cancel=threading.Event()
        self._watcher=None
        self._last_health=0
        self._requested_session=None
        self._binding=None
        self._last_state=None
        self._valid=False
        try:
            Gst.init([])
            if any(Gst.ElementFactory.find(name) is None for name in ('webrtcsrc','webrtcbin','nicesrc')):
                raise RuntimeError('Run this SDK client with the project WebRTC environment')
            state=self.control.start(start_seconds) if self._owned else self.control.state()
            target=state['session']
            self._requested_session=target
            deadline=time.monotonic()+10
            while True:
                rtc=state.get('webrtc',{})
                if (state['phase']=='live' and rtc.get('phase')=='publishing'
                        and rtc.get('signalling',{}).get('producer_ready')):break
                if state['phase'] not in ('starting','live') or time.monotonic()>=deadline:
                    raise RuntimeError('An explicit active Cammy WebRTC session is required')
                time.sleep(.04);state=self.control.state()
                if state.get('session')!=target:raise RuntimeError('App session changed during connect')
            if not isinstance(state.get('boot_id'),str):raise RuntimeError('Boot identity missing')
            self._binding=(target,state['boot_id'])
            self._last_state=state;self._last_health=time.monotonic();self._valid=True
            super().__init__(MediaBackend.WEBRTC,signalling_host='127.0.0.1',camera_specs=camera_specs())
            self._watcher=threading.Thread(target=self._watch,daemon=True,name='cammy-sdk-media-guard')
            self._watcher.start()
        except Exception:
            self.close()
            raise

    def _init_webrtc(self,log_level,signalling_host,signalling_port,camera_specs=None,daemon_url=''):
        rows=get_producer_list(signalling_host,signalling_port)
        match=[peer for peer,meta in rows.items() if meta and meta.get('name')=='reachymini'
               and (meta.get('cammy_session'),meta.get('cammy_boot'))==self._binding]
        if len(match)!=1:raise RuntimeError('Exact Cammy producer binding unavailable')
        client=_CammyWebRTCClient(self._guard,log_level=log_level,peer_id=match[0],
                 signaling_host=signalling_host,signaling_port=signalling_port,camera_specs=camera_specs)
        self.camera=self.audio=client
        client.open()

    def _guard(self):
        with self._lock:
            if self._closed or not self._valid:return False
            now=time.monotonic()
            if now-self._last_health<.1:return True
            try:state=self.control.state()
            except Exception:self._valid=False;return False
            rtc=state.get('webrtc',{})
            self._last_state=state;self._last_health=time.monotonic()
            self._valid=(state.get('phase')=='live' and (state.get('session'),state.get('boot_id'))==self._binding
                and rtc.get('phase')=='publishing' and rtc.get('error') is None
                and state.get('frame_age_seconds') is not None and state['frame_age_seconds']<=2
                and state.get('audio_age_seconds') is not None and state['audio_age_seconds']<=2)
            return self._valid

    def _watch(self):
        while not self._watch_cancel.wait(.1):
            if not self._guard():
                self.close()
                return

    def get_frame(self):
        with self._lock:
            if not self._guard() or self.camera is None:return None
            value=super().get_frame()
            return value if self._guard() else None

    def get_frame_jpeg(self):
        with self._lock:
            if not self._guard() or self.camera is None:return None
            value=super().get_frame_jpeg()
            return value if self._guard() else None

    def get_audio_sample(self):
        with self._lock:
            if not self._guard() or self.audio is None:return None
            value=super().get_audio_sample()
            return value if self._guard() else None

    def get_output_audio_samplerate(self):raise NotImplementedError('Cammy speaker streaming is unavailable')
    def get_output_channels(self):raise NotImplementedError('Cammy speaker streaming is unavailable')
    def stop_recording(self):self.close()

    def play_sound(self,*args):raise NotImplementedError('Cammy speaker streaming is unavailable')
    def start_playing(self):raise NotImplementedError('Cammy speaker streaming is unavailable')
    def push_audio_sample(self,*args):raise NotImplementedError('Cammy speaker streaming is unavailable')
    def enable_wobbling(self,*args,**kwargs):raise NotImplementedError('Cammy motion backend unavailable')
    def get_DoA(self):return None

    def close(self):
        if not hasattr(self,'_lock'):return
        with self._lock:
            already=self._closed
            if not already:
                self._closed=True;self._valid=False;self._watch_cancel.set()
        if already:
            if self._watcher is not threading.current_thread():self._cleanup_done.wait(6)
            return
        client=None
        target=self._binding[0] if self._binding else self._requested_session
        try:
            with self._lock:
                client=self.camera
                try:
                    super().close()
                finally:
                    if client is not None:
                        client._loop.quit()
                        if client._thread_bus_calls is not threading.current_thread():client._thread_bus_calls.join(2)
        finally:
            try:
                if self._owned and target:
                    try:self.control.stop(target)
                    except Exception:pass  # Never stop a replacement app session.
            finally:
                self._cleanup_done.set()  # Attempt finished; not a physical-cleanup claim.
        watcher=self._watcher
        if watcher and watcher is not threading.current_thread():watcher.join(2)

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
