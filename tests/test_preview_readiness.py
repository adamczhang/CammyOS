"""Local fake native event/RTSP services; never binds a camera address."""
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sdk'))
from cammy import Cammy, CammyError

SDP=b'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=PRIVATE-NATIVE-NAME\r\nt=0 0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n'


@unittest.skipUnless(shutil.which('cc') or os.environ.get('CAMMY_PREVIEW_CC'), 'C compiler required')
class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory();root=Path(cls.build.name)
        harness=root/'harness.c';cls.binary=root/'portal'
        harness.write_text('#define main portal_main\n#include "'+str(ROOT/'src/cammy_portal.c')+'"\n#undef main\n'
            'int main(int argc,char **argv){if(argc<5)return 2;preview_event_port=atoi(argv[1]);'
            'preview_rtsp_port=atoi(argv[2]);preview_keepalive_seconds=0.12;probe_prefix=argv[3];'
            'return portal_main(argc-3,argv+3);}\n')
        subprocess.run((json.loads(os.environ['CAMMY_PREVIEW_CC']) if 'CAMMY_PREVIEW_CC' in os.environ else ['cc'])+['-O2','-std=c11','-Wall','-Wextra','-Werror',str(harness),'-o',str(cls.binary)],check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.mode='valid';self.commands=[];self.requests=[];self.closed=threading.Event();self.connected=threading.Event()
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        test=self
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads=True
        class EventHandler(socketserver.BaseRequestHandler):
            def handle(self):
                test.connected.set();self.request.settimeout(.1);pending=b''
                while not test.closed.is_set():
                    try:part=self.request.recv(1024)
                    except socket.timeout:continue
                    if not part:return
                    pending+=part
                    while len(pending)>=17:
                        test.commands.append(pending[:17]);pending=pending[17:]
                    if test.mode=='event-disconnect':return
        class RTSPHandler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(.2);request=b''
                try:
                    while b'\r\n\r\n' not in request:
                        request+=self.request.recv(1024)
                        if len(request)>2048:return
                    test.requests.append(request)
                    if test.mode=='slow':
                        while not test.closed.wait(.05):
                            try:
                                if not self.request.recv(1024):return
                            except socket.timeout:continue
                        return
                    response=b'RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Type: application/sdp\r\nContent-Length: '+str(len(SDP)).encode()+b'\r\n\r\n'+SDP
                    if test.mode=='bad-cseq':response=response.replace(b'CSeq: 1',b'CSeq: 9')
                    if test.mode=='bad-length':response=response.replace(str(len(SDP)).encode()+b'\r\n\r\n',b'9000\r\n\r\n')
                    if test.mode=='duplicate-length':response=response.replace(b'CSeq: 1',b'CSeq: 1\r\nContent-Length: 1')
                    if test.mode=='bad-status':response=b'RTSP/1.0 404 Not Found\r\nCSeq: 1\r\n\r\n'
                    if test.mode=='no-video':response=response.replace(b'm=video',b'm=audio')
                    if test.mode=='embedded-nul':response=response.replace(b'v=0',b'v=\x00')
                    for start in range(0,len(response),23):
                        self.request.sendall(response[start:start+23]);time.sleep(.001)
                except (OSError,ConnectionError):return
        self.events=Server(('127.0.0.1',0),EventHandler);self.rtsp=Server(('127.0.0.1',0),RTSPHandler)
        self.threads=[]
        for server in [self.events,self.rtsp]:
            t=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02});t.start();self.threads.append(t)
        with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
        self.token='preview-test-'+'x'*40;tokenfile=self.root/'token';tokenfile.write_text(self.token)
        self.proc=subprocess.Popen(json.loads(os.environ.get('CAMMY_PREVIEW_RUNNER','[]'))+[str(self.binary),str(self.events.server_address[1]),str(self.rtsp.server_address[1]),str(self.root),
            '--port',str(self.port),'--token-file',str(tokenfile),'--stock-preview'],stdout=subprocess.DEVNULL)
        self.a=Cammy(f'http://127.0.0.1:{self.port}',self.token,'alpha',timeout=1)
        self.b=Cammy(self.a.endpoint,self.token,'beta',timeout=1)
        deadline=time.monotonic()+3
        while True:
            try:self.a.state();break
            except OSError:
                if self.proc.poll() is not None or time.monotonic()>deadline:raise
                time.sleep(.01)

    def tearDown(self):
        self.closed.set();self.proc.terminate();self.proc.wait(timeout=3)
        for server,t in zip([self.events,self.rtsp],self.threads):server.shutdown();server.server_close();t.join()
        self.tmp.cleanup()

    def status_until(self, predicate, timeout=2):
        deadline=time.monotonic()+timeout
        while True:
            state=self.a.preview_status()
            if predicate(state):return state
            if time.monotonic()>deadline:self.fail('Readiness did not reach expected state: '+str(state))
            time.sleep(.01)

    def start(self, ttl=10): self.a.acquire(ttl);return self.a.preview_start()

    def test_active_preview_waits_on_deadlines_not_a_fixed_tick(self):
        """0.9: an active preview no longer ticks at 40 Hz; keepalives (0.12 s here) still go out."""
        self.start(10);self.status_until(lambda s:s['phase']=='sdp-ready')
        status=Path(f'/proc/{self.proc.pid}/status')
        def switches():return int(next(l for l in status.read_text().splitlines() if l.startswith('voluntary_ctxt_switches')).split()[1])
        sent=len(self.commands);before=switches();time.sleep(1);woke=switches()-before
        self.assertLessEqual(woke,25) # about 9 (8 keepalives + 1 s cap); the 25 ms tick made over 40
        self.assertGreaterEqual(len(self.commands)-sent,6)
        self.assertEqual(self.a.preview_status()['phase'],'sdp-ready')

    def test_real_protocol_readiness_is_distinct_from_a_frame(self):
        with self.assertRaises(CammyError) as auth:Cammy(self.a.endpoint,'wrong').preview_status()
        self.assertEqual(auth.exception.status,401)
        self.assertEqual(self.a.preview_status()['phase'],'idle')
        self.start();ready=self.status_until(lambda s:s['phase']=='sdp-ready')
        self.assertTrue(ready['event_connected']);self.assertTrue(ready['listener_reachable']);self.assertTrue(ready['sdp_ready'])
        self.assertIsNone(ready['frame_fresh']);self.assertEqual(ready['frame_evidence'],'not-collected')
        self.assertNotIn('PRIVATE-NATIVE-NAME',json.dumps(ready))
        self.assertTrue(self.requests[0].startswith(b'DESCRIBE rtsp://127.0.0.1:'))
        self.assertTrue(self.requests[0].split(b' ')[1].endswith(b'/ch00'))
        self.assertEqual(set(self.commands),{b'{"cmd_id":"4016"}'})
        self.a.preview_stop();self.assertEqual(self.a.preview_status()['phase'],'idle')

    def test_keepalive_and_idempotent_start_do_not_renew_or_reconnect(self):
        self.start(2);self.status_until(lambda s:s['commands_sent']>=3)
        remaining=self.a.state()['lease_remaining_seconds']
        self.a.preview_start();time.sleep(.04)
        self.assertLess(self.a.state()['lease_remaining_seconds'],remaining)
        self.assertEqual(len(self.requests),1)
        self.a.release();count=len(self.commands);time.sleep(.2)
        self.assertEqual(len(self.commands),count);self.assertFalse(self.a.preview_status()['event_connected'])

    def test_protocol_errors_are_terminal_and_bounded_but_keep_owned_session(self):
        for mode in ['bad-cseq','bad-length','duplicate-length','bad-status','no-video','embedded-nul']:
            with self.subTest(mode=mode):
                self.a.stop();self.mode=mode;self.start()
                failed=self.status_until(lambda s:s['phase']=='failed')
                self.assertEqual(failed['failure_reason'],'rtsp-protocol-error')
                self.assertFalse(failed['sdp_ready']);self.assertTrue(failed['preview_active'])
                self.assertTrue(failed['listener_reachable'])

    def test_stop_cancels_slow_sdp_and_partial_http_input(self):
        self.mode='slow';self.start();self.status_until(lambda s:s['phase']=='checking-sdp')
        with socket.create_connection(('127.0.0.1',self.port)) as partial:
            partial.sendall(b'POST /v1/preview/start HTTP/1.1\r\nContent-Length: 100\r\n\r\n{')
            began=time.monotonic();self.b.stop();self.assertLess(time.monotonic()-began,.5)
        self.assertEqual(self.a.preview_status()['phase'],'idle');self.assertIsNone(self.a.state()['owner'])
        time.sleep(.04);count=len(self.commands);time.sleep(.2);self.assertEqual(len(self.commands),count)

    def test_expiry_closes_slow_sdp_and_event_session(self):
        self.mode='slow';self.start(1)
        self.status_until(lambda s:s['phase']=='checking-sdp')
        self.status_until(lambda s:s['phase']=='idle',timeout=1.5)
        self.assertIsNone(self.a.state()['owner'])

    def test_readiness_deadline_does_not_close_ordinary_preview(self):
        self.mode='slow';self.start(10)
        failed=self.status_until(lambda s:s['phase']=='failed',timeout=5.7)
        self.assertEqual(failed['failure_reason'],'rtsp-timeout')
        self.assertTrue(failed['preview_active']);self.assertFalse(failed['sdp_ready'])
        self.mode='valid';self.a.preview_check();self.status_until(lambda s:s['phase']=='sdp-ready')
        self.assertLess(self.a.state()['lease_remaining_seconds'],5)

    def test_absent_listener_is_distinct_from_sdp_timeout(self):
        self.rtsp.shutdown();self.rtsp.server_close();self.start(10)
        state=self.status_until(lambda s:s['phase']=='failed',timeout=5.7)
        self.assertEqual(state['failure_reason'],'rtsp-unreachable')
        self.assertFalse(state['listener_reachable']);self.assertFalse(state['sdp_ready'])
        self.assertTrue(state['event_connected']);self.assertEqual(self.requests,[])

    def test_unavailable_event_service_does_not_stall_control(self):
        self.events.shutdown();self.events.server_close();self.a.acquire()
        began=time.monotonic()
        try:self.a.preview_start()
        except CammyError as error:self.assertEqual(error.status,503)
        self.assertLess(time.monotonic()-began,.5)
        state=self.status_until(lambda s:s['phase']=='failed')
        self.assertEqual(state['failure_reason'],'event-unavailable')
        self.assertFalse(state['preview_active']);self.b.stop()

    def test_event_loss_clears_readiness_and_foreign_check_is_rejected(self):
        self.mode='event-disconnect';self.start()
        state=self.status_until(lambda s:s['phase']=='failed')
        self.assertEqual(state['failure_reason'],'event-disconnected');self.assertFalse(state['preview_active'])
        with self.assertRaises(CammyError) as denied:self.b.preview_check()
        self.assertEqual(denied.exception.status,409)
