"""Cammy raw-frame/PCM publisher using upstream GStreamer WebRTC wire format.

No ALSA/video device, native camera control, motor or playback sink is opened.
The existing LiveSession remains sole owner of camera capture and its finite lease.
"""
import logging
import threading
import time

from reachy_mini.media.gstreamer_utils import Gst
from reachy_mini.media.media_server import GstMediaServer
from gi.repository import GLib

from cammy_signalling import SignallingServer
from cammy_live import WIDTH, HEIGHT, FPS


class WebRTCPublisher:
    def __init__(self, source, port=8443):
        Gst.init([])
        needed=('appsrc','queue','videoconvert','audioconvert','audioresample',
                'webrtcsink','webrtcsrc','webrtcbin','errorignore','vp8enc','opusenc','nicesrc','srtpenc')
        missing=[name for name in needed if Gst.ElementFactory.find(name) is None]
        if missing:raise RuntimeError('Project WebRTC profile required; missing '+','.join(missing))
        self.source, self.port = source, port
        self._logger = logging.getLogger('cammy.webrtc')
        self.lock=threading.RLock()
        self.thread=None
        self.cancel=threading.Event()
        self.phase='idle'
        self.error=None
        self.server=None
        self.frames=self.samples=0
        self.session=self.boot=None
        self.cleanup_confirmed=True

    def start(self, session):
        with self.lock:
            if self.thread and self.thread.is_alive() or not self.cleanup_confirmed:
                raise RuntimeError('WebRTC cleanup is still active')
            if self.source.status()['session']!=session:
                raise ValueError('Publisher must bind current explicit session')
            self.cancel=threading.Event()
            self.phase,self.error='starting',None
            self.session,self.boot=session,None
            self.frames=self.samples=0
            self.cleanup_confirmed=False
            self.thread=threading.Thread(target=self._run,daemon=True,name='cammy-webrtc-publisher')
            self.thread.start()

    def _consumer(self, sink, peer, webrtcbin):
        # Reuse the pinned SDK's ref-balanced helper. Reading ice-agent naively
        # with older PyGObject can prematurely unref/free it (upstream documents it).
        GstMediaServer._disable_upnp(self,webrtcbin)
        webrtcbin.set_property('latency',20)

    def _pipeline(self, server, status):
        pipeline=Gst.parse_launch(
            f'appsrc name=cammy_video ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! '
            'videoconvert ! stream.video_0 '
            'appsrc name=cammy_audio ! queue max-size-buffers=5 max-size-bytes=0 max-size-time=0 leaky=downstream ! '
            'audioconvert ! audioresample ! stream.audio_0 webrtcsink name=stream')
        sink=pipeline.get_by_name('stream')
        sink.set_property('run-signalling-server',False)
        sink.set_property('stun-server',None)
        sink.set_property('video-caps',Gst.Caps.from_string('video/x-vp8'))
        sink.set_property('audio-caps',Gst.Caps.from_string('audio/x-opus'))
        sink.set_property('min-bitrate',100000)
        sink.set_property('start-bitrate',600000)
        sink.set_property('max-bitrate',2000000)
        sink.connect('consumer-added',self._consumer)
        meta=Gst.Structure.new_empty('meta')
        for name,value in {'name':'reachymini','cammy_session':self.session,
                           'cammy_boot':status['boot_id'],'cammy_source':'synthetic-fixture' if status['synthetic'] else 'M105 Wi-Fi',
                           'cammy_calibrated':False,'cammy_speaker':False}.items():meta.set_value(name,value)
        sink.set_property('meta',meta)
        signaller=sink.get_property('signaller')
        signaller.set_property('uri',f'ws://127.0.0.1:{server.port}/')
        headers=Gst.Structure.new_empty('headers')
        headers.set_value('X-Cammy-Producer',server.producer_token)
        signaller.set_property('headers',headers)
        video=pipeline.get_by_name('cammy_video');audio=pipeline.get_by_name('cammy_audio')
        for app in (video,audio):
            app.set_property('format',Gst.Format.TIME)
            app.set_property('is-live',True)
            app.set_property('block',False)
            app.set_property('leaky-type',2)
            app.set_property('max-buffers',2 if app==video else 5)
        video.set_property('max-bytes',WIDTH*HEIGHT*3*2)
        audio.set_property('max-bytes',160000)
        video.set_property('caps',Gst.Caps.from_string(f'video/x-raw,format=RGB,width={WIDTH},height={HEIGHT},framerate={FPS}/1'))
        audio.set_property('caps',Gst.Caps.from_string('audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved'))
        return pipeline,video,audio

    @staticmethod
    def _push(app, data, pts, duration):
        buffer=Gst.Buffer.new_wrapped(data)
        buffer.pts=pts;buffer.dts=pts;buffer.duration=duration
        result=app.emit('push-buffer',buffer)
        if result!=Gst.FlowReturn.OK:
            raise RuntimeError('WebRTC source refused data')

    def _run(self):
        pipeline=loop=loop_thread=server=None
        try:
            deadline=time.monotonic()+10
            while not self.cancel.is_set():
                state=self.source.status()
                if state['session']!=self.session:raise RuntimeError('Camera session changed')
                if state['phase']=='live':break
                if state['phase']!='starting':return
                if time.monotonic()>deadline:raise RuntimeError('Camera startup timed out')
                self.cancel.wait(.02)
            if self.cancel.is_set():return
            self.boot=state['boot_id']
            server=SignallingServer(self.session,self.port);server.start();self.server=server
            pipeline,video,audio=self._pipeline(server,state)
            loop=GLib.MainLoop()
            loop_thread=threading.Thread(target=loop.run,daemon=True,name='cammy-gstreamer-loop');loop_thread.start()
            anchor=time.monotonic()
            if pipeline.set_state(Gst.State.PLAYING)==Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('WebRTC pipeline failed')
            bus=pipeline.get_bus()
            last_frame=audio_after=0
            last_video_pts=last_audio_end=0
            with self.lock:self.phase='publishing'
            while not self.cancel.is_set():
                state=self.source.status()
                if state['session']!=self.session or state['boot_id']!=self.boot:
                    raise RuntimeError('Session/boot changed')
                if state['phase']!='live':break
                if server.failure:raise RuntimeError('Signalling failed')
                message=bus.pop_filtered(Gst.MessageType.ERROR)
                if message is not None:
                    error,_=message.parse_error();self._logger.error('Publisher pipeline error: %s',error.message)
                    raise RuntimeError('WebRTC pipeline error')
                try:
                    (pixels,sequence,at,_),session,boot=self.source.latest_pixels()
                except RuntimeError:
                    pixels=None
                if pixels is not None and sequence>last_frame:
                    if session!=self.session or boot!=self.boot:raise RuntimeError('Stale frame binding')
                    pts=max(last_video_pts+1,int(max(0,at-anchor)*Gst.SECOND))
                    self._push(video,pixels,pts,Gst.SECOND//FPS)
                    last_video_pts,last_frame=pts,sequence
                    self.frames+=1
                try:data,meta=self.source.audio_tail(audio_after)
                except RuntimeError:data=b''
                if data:
                    if (meta['session']!=self.session or meta['boot_id']!=self.boot or meta['history_gap']):
                        raise RuntimeError('Stale or lost PCM sequence')
                    count=len(data)//2
                    start=meta['last_receipt_monotonic']-count/16000
                    trim=min(count,max(0,int((anchor-start)*16000)))
                    data=data[trim*2:];count-=trim
                    if data:
                        pts=max(last_audio_end,int(max(0,start+trim/16000-anchor)*Gst.SECOND))
                        duration=count*Gst.SECOND//16000
                        self._push(audio,data,pts,duration)
                        last_audio_end=pts+duration
                        self.samples+=count
                    audio_after=meta['latest_sequence']
                self.cancel.wait(.02)
        except Exception as exc:
            self.error=str(exc) if type(exc) in (RuntimeError,ValueError) else type(exc).__name__
            self.source.stop(session=self.session)
        finally:
            # Remove access first, then flush codec/transport buffers. A stuck
            # shutdown is never reported as success or reused for another session.
            self.cancel.set()
            server_done=True if server is None else server.stop()
            if pipeline is not None:pipeline.set_state(Gst.State.NULL)
            if loop is not None:loop.quit()
            if loop_thread is not None:loop_thread.join(2)
            with self.lock:
                self.cleanup_confirmed=server_done and (loop_thread is None or not loop_thread.is_alive())
                self.phase='faulted' if self.error or not self.cleanup_confirmed else 'idle'

    def stop(self, wait=True):
        self.cancel.set()
        if wait and self.thread and self.thread is not threading.current_thread():self.thread.join(6)
        return self.status()

    def status(self):
        with self.lock:
            return {'available':True,'phase':self.phase,'source_session':self.session,'boot_id':self.boot,
                    'video_frames_pushed':self.frames,'mono_samples_pushed':self.samples,
                    'transport':'upstream GStreamer WebRTC','speaker_available':False,
                    'calibration_valid':False,'error':self.error,
                    'cleanup_confirmed':self.cleanup_confirmed and not(self.thread and self.thread.is_alive()),
                    'signalling':None if self.server is None else self.server.status()}
