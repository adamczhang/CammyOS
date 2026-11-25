"""Real threads, fake body/pipe sources: bounded stream ownership and cleanup."""
import io
import importlib.util
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'host'), str(ROOT/'sdk')]
MEDIA_ENV = importlib.util.find_spec('PIL') is not None
if MEDIA_ENV:
    import cammy_live
    from cammy_live import LiveSession, WIDTH, HEIGHT, video_command
    cammy_live.HOSTS = ('192.0.2.53', '192.168.10.1')   # not the address C3 saved on this PC


class FakeStream:
    def __init__(self): self.closed = False
    def read(self, n):
        time.sleep(.01)
        if self.closed: return b''
        return b'\x01\x01' * (n//2)
    def close(self): self.closed = True


class FakeClient:
    agent = 'unique-fixture-owner'
    def __init__(self):
        self.owner = None; self.preview = False; self.released = 0
        self.acquired = 0; self.conflict = False; self.stream = FakeStream()
    def identity(self): return {'os':'CammyOS','simulation':False}
    def state(self):
        return {'owner':'other' if self.conflict else self.owner,
                'lease_remaining_seconds':30 if self.owner else 0,
                'event_sequence':10,'preview_active':self.preview}
    def acquire(self, ttl): self.owner = self.agent; self.acquired += 1; return {}
    def renew(self, ttl): raise AssertionError('Short fixture should not renew')
    def preview_start(self): self.preview = True; return {'rtsp_url':'rtsp://192.0.2.53:8554/ch00'}
    def preview_status(self): return {'event_connected':self.preview,'commands_sent':1,'sdp_ready':True}
    def open_audio_stream(self): return self.stream
    def release(self):
        self.released += 1
        if not self.conflict: self.owner = None; self.preview = False
        self.stream.close(); return {'released':not self.conflict}


def wait_for(predicate, timeout=4):
    deadline = time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline: raise AssertionError('Fixture timed out')
        time.sleep(.02)


def fake_process(*_, **__):
    code = f'import sys,time\nf=bytes([30,100,50])*{WIDTH*HEIGHT}\nwhile True:\n sys.stdout.buffer.write(f);sys.stdout.buffer.flush();time.sleep(.04)'
    return subprocess.Popen([sys.executable,'-c',code], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


@unittest.skipUnless(MEDIA_ENV, 'Pillow is required for real pipe decode tests')
class LiveTests(unittest.TestCase):
    def setUp(self):
        self.body = FakeClient()
        self.live = LiveSession(lambda:self.body, fake_process, lambda _:False,
                                lambda _:'11111111-2222-3333-4444-555555555555', synthetic=True)
    def tearDown(self): self.live.stop(wait=True)
    def test_waits_for_portal_event_readiness_without_second_rtsp_probe(self):
        ready=threading.Event();starts=[]
        self.body.preview_status=lambda:dict(event_connected=True,commands_sent=1,sdp_ready=ready.is_set())
        def launch(*args,**kwargs):
            self.assertTrue(ready.is_set());starts.append(True)
            return fake_process(*args,**kwargs)
        def forbidden_probe(_):raise AssertionError('Duplicate desktop RTSP probe')
        self.live.process_factory=launch;self.live.describe_source=forbidden_probe
        self.live.start(5)
        wait_for(lambda:self.body.acquired==1)
        time.sleep(.1);self.assertEqual(starts,[])
        ready.set()
        wait_for(lambda:self.live.status()['frames']>=1 and self.live.status()['audio_samples']>0)
        self.live.stop(wait=True)
        self.assertEqual(len(starts),1);self.assertEqual(self.body.released,1)

    def test_404_retry_is_bounded_and_microphone_waits_for_a_frame(self):
        attempts=[];opened=[]
        def launch(*args,**kwargs):
            attempts.append(True)
            if len(attempts)==1:
                return subprocess.Popen([sys.executable,'-c',
                    'import sys;sys.stderr.write("method DESCRIBE failed: 404 Stream Not Found\\n");sys.exit(8)'],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            return fake_process(*args,**kwargs)
        def audio():
            self.assertGreater(self.live.frames,0);opened.append(True);return self.body.stream
        self.live.process_factory=launch;self.body.open_audio_stream=audio
        self.live.start(5)
        wait_for(lambda:self.live.status()['audio_samples']>0)
        self.assertEqual(len(attempts),2);self.assertEqual(opened,[True])
        self.assertEqual(self.body.acquired,1)
        self.live.stop(wait=True);self.assertEqual(self.body.released,1)

    def test_persistent_404_stops_after_four_owned_attempts(self):
        self.live.process_factory=lambda *a,**k:subprocess.Popen([sys.executable,'-c',
            'import sys;sys.stderr.write("method DESCRIBE failed: 404 Stream Not Found\\n");sys.exit(8)'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.live.start(5)
        wait_for(lambda:self.live.status()['phase']=='faulted')
        self.assertEqual(self.live.status()['startup_attempts'],4)
        self.assertEqual(self.body.acquired,1);self.assertEqual(self.body.released,1)
        self.assertEqual(self.live.samples,0)
        self.assertTrue(self.live.status()['decoder_reaped'])

    def test_idle_never_acquires_or_starts_hardware(self):
        self.assertEqual(self.body.acquired,0)
        self.assertTrue(self.live.status()['synthetic'])
        with self.assertRaises(RuntimeError): self.live.latest_frame()
    def test_real_pipe_decode_pcm_bounded_and_stop_clears_media(self):
        self.live.start(5)
        wait_for(lambda:self.live.status()['frames']>=2 and self.live.status()['audio_samples']>0)
        frame, session, boot = self.live.latest_frame()
        self.assertTrue(frame[0].startswith(b'\xff\xd8'))
        self.assertEqual(boot,'11111111-2222-3333-4444-555555555555')
        self.assertEqual(session,self.live.status()['session'])
        time.sleep(.6)
        data, metadata = self.live.audio_tail()
        self.assertLessEqual(len(data),160000)
        self.assertGreater(metadata['latest_sequence'],0)
        with self.assertRaises(RuntimeError): self.live.start(5)
        self.live.stop(wait=True)
        self.assertEqual(self.body.released,1)
        self.assertEqual(self.live.status()['phase'],'idle')
        for key in ('decoder_reaped','worker_threads_joined','microphone_stream_closed'):
            self.assertTrue(self.live.status()[key])
        self.assertFalse(self.live.status()['hardware_cleanup_confirmed'])
        self.assertFalse(self.live.pcm)
        with self.assertRaises(RuntimeError): self.live.latest_frame()
    def test_lease_conflict_halts_without_global_stop_or_reacquire(self):
        self.live.start(5)
        wait_for(lambda:self.live.status()['frames']>0)
        self.body.conflict = True
        wait_for(lambda:self.live.status()['phase']=='faulted')
        self.assertEqual(self.body.acquired,1)
        self.assertEqual(self.body.state()['owner'],'other')
        with self.assertRaises(RuntimeError): self.live.latest_frame()
    def test_stop_during_delayed_acquire_never_opens_stream(self):
        entered = threading.Event(); finish = threading.Event()
        original = self.body.acquire
        def acquire(ttl): entered.set(); finish.wait(2); return original(ttl)
        self.body.acquire = acquire
        self.body.preview_start = lambda: self.fail('late preview start')
        self.live.start(5); self.assertTrue(entered.wait(1))
        self.live.stop(); finish.set()
        wait_for(lambda:self.live.status()['phase']=='idle')
        self.assertEqual(self.body.released,1)
        self.assertFalse(self.live.pcm)
    def test_session_expiry_cleans_and_restart_has_new_identity(self):
        self.live.start(5)
        wait_for(lambda:self.live.status()['frames']>0)
        first = self.live.session
        self.live.ends = time.monotonic()-.1
        wait_for(lambda:self.live.status()['phase']=='idle')
        self.body.stream = FakeStream()
        self.live.start(5)
        wait_for(lambda:self.live.status()['frames']>0)
        self.assertNotEqual(first,self.live.session)
    def test_other_owner_is_refused_before_acquire(self):
        self.body.conflict=True
        self.live.start(5)
        wait_for(lambda:self.live.status()['phase']=='faulted')
        self.assertEqual(self.body.acquired,0)
        self.assertEqual(self.body.released,0)
    def test_decoder_exit_is_faulted_and_owner_released(self):
        self.live.process_factory = lambda *a,**k: subprocess.Popen(
            [sys.executable,'-c','import sys;sys.stderr.write("fixture decode failure");sys.exit(7)'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.live.start(5)
        wait_for(lambda:self.live.status()['phase']=='faulted')
        self.assertEqual(self.body.released,1)
        self.assertTrue(self.live.status()['decoder_reaped'])
        self.assertTrue(self.live.status()['microphone_stream_closed'])
        self.assertIsNone(self.live.frame)
        self.assertEqual(self.live.status()['decoder_exit_code'],7)
        self.assertIn('fixture decode failure',self.live.status()['decoder_log'])
    def test_unconfirmed_cleanup_cannot_start_new_session(self):
        self.live.worker_threads_joined=False
        with self.assertRaises(RuntimeError): self.live.start(5)
        self.assertEqual(self.body.acquired,0)
        self.live.worker_threads_joined=True

    def test_bound_stop_does_not_touch_another_session_or_pixels(self):
        self.live.start(5)
        wait_for(lambda:self.live.status()['phase']=='live')
        self.live.stop(session='old-session')
        self.assertEqual(self.live.status()['phase'],'live')
        self.assertEqual(self.body.released,0)
        self.assertIsNotNone(self.live.latest_pixels())
        self.live.stop(session=self.live.session,wait=True)
        with self.assertRaises(RuntimeError): self.live.latest_pixels()

    def test_url_not_a_user_chosen_network_or_file_reader(self):
        for url in ('file:///etc/passwd','rtsp://example.com:8554/ch00',
                    'rtsp://user@192.0.2.53:8554/ch00',
                    'rtsp://192.0.2.53:8554/ch00?extra=1'):
            with self.assertRaises(ValueError): video_command(url)
        self.assertIn('scale=640:360,fps=5',video_command('rtsp://192.0.2.53:8554/ch00'))
    def test_decoder_receives_parent_death_signal(self):
        helper = ROOT/'host/media_child.py'
        code = ('import os,subprocess,sys,time\n'
                f'p=subprocess.Popen([sys.executable,{str(helper)!r},str(os.getpid()),"/usr/bin/sleep","30"])\n'
                'print(p.pid,flush=True)\ntime.sleep(30)')
        parent = subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,text=True)
        pidfd = None
        try:
            child = int(parent.stdout.readline())
            pidfd = os.pidfd_open(child)
            time.sleep(.1)
            parent.kill(); parent.wait(timeout=2)
            selector = selectors.DefaultSelector()
            try:
                selector.register(pidfd,selectors.EVENT_READ)
                self.assertTrue(selector.select(2),'Decoder survived dead parent')
            finally: selector.close()
        finally:
            if pidfd is not None: os.close(pidfd)
            if parent.poll() is None: parent.kill(); parent.wait()
            parent.stdout.close()
