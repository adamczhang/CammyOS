import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"sdk"))
from cammy import Cammy, CammyError
ROOT=Path(__file__).resolve().parents[1]

class PortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.token="test-token-"+"a"*48
        tokenfile=Path(cls.tmp.name)/"token";tokenfile.write_text(cls.token)
        with socket.socket() as s:
            s.bind(("127.0.0.1",0));cls.port=s.getsockname()[1]
        cls.command=json.loads(os.environ.get('CAMMY_TEST_COMMAND','[]')) or [str(ROOT/'build/host/cammy-portal')]
        cls.proc=subprocess.Popen(cls.command+['--port',str(cls.port),'--token-file',str(tokenfile)],stdout=subprocess.DEVNULL,env=dict(os.environ,CAMMY_FACE_PACK_FILE=str(Path(cls.tmp.name)/'face-pack')))
        cls.base=f'http://127.0.0.1:{cls.port}'
        deadline=time.monotonic()+3
        while True:
            try:
                with urllib.request.urlopen(cls.base+'/health',timeout=.2) as r:cls.health=json.load(r)
                break
            except OSError:
                if cls.proc.poll() is not None or time.monotonic()>deadline:raise
                time.sleep(.02)
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate();cls.proc.wait(timeout=5);cls.tmp.cleanup()
    def setUp(self):
        self.a=Cammy(self.base,self.token,'alpha');self.b=Cammy(self.base,self.token,'beta');self.a.stop()
    def assert_status(self,status,fn):
        with self.assertRaises(CammyError) as e:fn()
        self.assertEqual(e.exception.status,status)
    def test_live_audio_is_opt_in_and_reports_no_hardware_cleanup(self):
        self.a.acquire()
        self.assert_status(501,self.a.open_audio_stream)
        status=self.a.audio_stream_status()
        self.assertFalse(status['configured'])
        self.assertFalse(status['hardware_cleanup_confirmed'])
        self.assertFalse(status['capture_child_alive'])

    def test_context_cleanup_after_explicit_stop_or_release(self):
        with self.a as body:
            self.assertEqual(body.state()['owner'], 'alpha')
            body.stop()
        self.assertIsNone(self.a.state()['owner'])
        with self.a as body:
            body.release()
        self.assertIsNone(self.a.state()['owner'])

    def test_diagnostics_is_authenticated_read_only_and_bounded(self):
        self.assert_status(401,lambda:Cammy(self.base,'wrong').diagnostics())
        before=self.a.state()
        result=self.a.diagnostics()
        self.assertTrue(result['read_only'])
        self.assertEqual(result['portal_version'],(ROOT/'VERSION').read_text().strip())
        self.assertFalse(result['hardware_adapters_available'])
        self.assertLess(len(json.dumps(result)),16000)
        self.assertNotIn(self.token,json.dumps(result))
        self.assertEqual(before,self.a.state())
        self.assert_status(404,lambda:self.a.request('GET','/v1/diagnostics?path=/etc/cammy/portal.token'))

    def test_probe_catalogue_jobs_and_strict_inputs(self):
        self.assert_status(401,lambda:Cammy(self.base,'wrong').probes())
        catalogue=self.a.probes()
        self.assertEqual({p['id'] for p in catalogue['probes']},
                         {'runtime','network-bindings','display-layout','audio-state'})
        self.assertTrue(all(not p['requires_lease'] for p in catalogue['probes']))
        for body in ({'probe':'../../etc/shadow'}, {'probe':'runtime','ttl':1},
                     {'probe':'runtime','agent':'alpha'}, {'path':'/etc/shadow'}):
            self.assert_status(400,lambda:self.a.request('POST','/v1/probes',body))
        job=self.a.probe_start('runtime')
        deadline=time.monotonic()+2
        while job['status']=='running':
            self.assertLess(time.monotonic(),deadline)
            time.sleep(.03);job=self.a.probe_status(job['job_id'])
        self.assertEqual(job['status'],'completed')
        self.assertIsNone(self.a.state()['owner'])
        self.assertTrue(job['read_only'])
        self.assertIn('boot_id',job['result'])
        self.assertLess(len(json.dumps(job)),12600)
        self.assert_status(409,lambda:self.b.probe_cancel(job['job_id']))
        self.assertEqual(self.a.probe_cancel(job['job_id'])['status'],'completed')
        self.assert_status(404,lambda:self.a.request('GET','/v1/probes/job-0'))
        self.assert_status(404,lambda:self.a.request('GET','/v1/probes/runtime'))
        with self.assertRaises(ValueError):self.a.probe_status('job-1/../../state')

    def test_partial_http_client_does_not_stall_stop_or_expiry(self):
        self.a.acquire(1)
        with socket.create_connection(('127.0.0.1',self.port),timeout=2) as partial:
            partial.sendall(b'POST /v1/session HTTP/1.1\r\nContent-Length: 100\r\n\r\n{')
            time.sleep(.03)
            started=time.monotonic();self.b.stop()
            self.assertLess(time.monotonic()-started,.5)
            self.a.acquire(1);time.sleep(1.1)
            self.assertIsNone(self.a.state()['owner'])
            self.assertIsNone(self.proc.poll())

    def test_health_and_authentication(self):
        self.assertEqual(self.health['version'],(ROOT/'VERSION').read_text().strip())
        self.assert_status(401,lambda:Cammy(self.base,'wrong').state())
        self.assertEqual(self.a.state()['owner'],None)
    def test_control_ownership_and_renewal(self):
        self.a.acquire();self.assertEqual(self.a.state()['owner'],'alpha')
        self.assert_status(409,lambda:self.b.acquire())
        self.assert_status(409,lambda:self.b.preview_stop())
        self.a.renew(15);self.a.release();self.b.acquire();self.assertEqual(self.b.state()['owner'],'beta')
    def test_agent_identity_on_state(self):
        a=Cammy(self.base,self.token,'alpha',label='t3 thread 6f2c1a9e (Cammy brain)')
        a.acquire();s=a.state()
        self.assertEqual((s['owner'],s['owner_label'],s['owner_address']),('alpha','t3 thread 6f2c1a9e (Cammy brain)','127.0.0.1'))
        self.assertEqual(s['owner_since_seconds'],0)
        a.label=None;a.renew(15);self.assertEqual(a.state()['owner_label'],'t3 thread 6f2c1a9e (Cammy brain)')
        a.release();s=a.state();self.assertEqual((s['owner'],s['owner_label'],s['owner_address']),(None,'',''))
        self.a.acquire();self.assertEqual(self.a.state()['owner_label'],'')  # a new lease starts unlabelled
        self.a.release()
        for bad in ['','x'*81,'quote"','back\\slash','tab\t']:
            with self.subTest(label=bad):
                self.assert_status(400,lambda:Cammy(self.base,self.token,'gamma',label=bad or None).request('POST','/v1/session',{'agent':'gamma','label':bad}))
    def test_face_pack_defaults_to_pikachu_and_reads_the_setting(self):
        self.assertEqual(self.a.state()['face_pack'],'pikachu')
        pack=Path(self.tmp.name)/'face-pack'
        for raw,want in [('eevee\n','eevee'),('pika-2',"pika-2"),('Bad Pack\n','pikachu'),('x'*33,'pikachu'),('','pikachu')]:
            with self.subTest(raw=raw):
                pack.write_text(raw);self.assertEqual(self.a.state()['face_pack'],want)
    def test_expiry_and_emergency_stop(self):
        self.a.acquire(1);time.sleep(1.15);self.b.acquire();self.assertEqual(self.b.state()['owner'],'beta')
        self.a.stop();self.assertIsNone(self.b.state()['owner'])
    def test_delayed_renewal_cannot_reacquire_an_expired_lease(self):
        self.a.acquire(1);old=self.a._lease_binding
        time.sleep(1.1)
        self.assert_status(409,lambda:self.a.renew(15))
        self.assertIsNone(self.b.state()['owner'])
        self.a.acquire(15)
        self.assertNotEqual(old,self.a._lease_binding)
        self.a.release()
        self.assert_status(409,lambda:self.a.renew())
    def test_capability_truthfulness(self):
        caps=self.a.capabilities();self.assertFalse(caps['preview']['available'])
        self.assertFalse(caps['gimbal']['available']);self.assertFalse(caps['arbitrary_shell'])
        self.a.acquire();self.assert_status(501,lambda:self.a.preview_start())
        self.assert_status(501,lambda:self.a.request('POST','/v1/look',{'direction':'left'}))
    def test_preview_url_uses_the_connected_interface(self):
        self.assertEqual(self.a.capabilities()['preview']['rtsp_url'],
                         'rtsp://127.0.0.1:8554/ch00')
    def test_strict_json_commands(self):
        for data in ({'agent':'bad"owner'},{'agent':'alpha','ttl':0},{'agent':'alpha','ttl':121},{'agent':'alpha','unexpected':1}):
            self.assert_status(400,lambda:self.a.request('POST','/v1/session',data))
        self.assert_status(400,lambda:self.a.request('POST','/v1/session',{'agent':'beta'}))
    def raw(self,request):
        with socket.create_connection(('127.0.0.1',self.port),timeout=2) as s:
            s.sendall(request);s.shutdown(socket.SHUT_WR);result=b''
            while True:
                chunk=s.recv(4096)
                if not chunk:break
                result+=chunk
            return result
    def test_malformed_http_cannot_kill_service(self):
        requests=[b'GET /health HTTP/1.1\r\nContent-Length: 999999\r\n\r\n',b'POST /v1/session HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n',b'GET /health HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n',b'GET /health HTTP/1.1\r\nBadheader\r\n\r\n']
        for r in requests:
            self.assertIn(self.raw(r).split(b' ')[1],(b'400',b'413'))
        self.assertIsNone(self.proc.poll());self.assertIn('owner',self.a.state())
    def fragmented(self,pieces):
        with socket.create_connection(('127.0.0.1',self.port),timeout=2) as s:
            s.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            for piece in pieces:s.sendall(piece);time.sleep(.01)
            result=b''
            while chunk:=s.recv(4096):result+=chunk
            return result
    def test_fragmented_requests_keep_exact_length_checks(self):
        body=b'{"agent":"alpha","ttl":5}';head=b'POST /v1/session HTTP/1.1\r\nAuthorization: Bearer %s\r\nContent-Length: %d\r\n\r\n'%(self.token.encode(),len(body))
        reply=self.fragmented([head[:3],head[3:-3],head[-3:-1],head[-1:],body[:4],body[4:]]) # terminator split across reads
        self.assertTrue(reply.startswith(b'HTTP/1.1 200 '),reply[:40]);self.assertEqual(self.a.state()['owner'],'alpha')
        self.assertEqual(self.fragmented([head,body[:-1],body[-1:]+b'x']).split(b' ')[1],b'400') # one byte too many
        self.assertEqual(self.fragmented([head,body[:5]+b'\0'+body[6:]]).split(b' ')[1],b'400')   # NUL in body
        self.assertEqual(self.fragmented([b'GET /health HTTP/1.1\r\nX: '+b'a'*4096,b'a'*4200]).split(b' ')[1],b'413')
        self.assertEqual(self.raw(b'POST /v1/session HTTP/1.1\r\nContent-Length: 1025\r\n\r\n').split(b' ')[1],b'413')
        self.assertIsNone(self.proc.poll())
    def test_idle_portal_sleeps_between_timed_work(self):
        self.a.stop();status=Path(f'/proc/{self.proc.pid}/status')
        def switches():return int(next(l for l in status.read_text().splitlines() if l.startswith('voluntary_ctxt_switches')).split()[1])
        time.sleep(.1);before=switches();time.sleep(1);idle=switches()-before
        self.assertLessEqual(idle,10) # a fixed 25 ms poll made about 40
        self.a.acquire(1);time.sleep(1.1);self.assertIsNone(self.a.state()['owner']) # lease deadline still serviced
    def test_event_ring_and_context_manager(self):
        with self.a as c:self.assertEqual(c.state()['owner'],'alpha')
        self.assertIsNone(self.a.state()['owner'])
        for _ in range(40):self.a.stop()
        result=self.a.events(after=1);self.assertTrue(result['history_gap']);self.assertEqual(len(result['events']),32)
        self.assertEqual(self.a.events(after=result['latest'])['events'],[])
    def test_background_startup_writes_pid_and_stops(self):
        pidfile=Path(self.tmp.name)/'background.pid'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        tokenfile=Path(self.tmp.name)/'token'
        subprocess.run(self.command+['--port',str(port),'--token-file',str(tokenfile),'--pid-file',str(pidfile),'--daemonize'],check=True,timeout=3)
        deadline=time.monotonic()+3
        try:
            while True:
                try:
                    client=Cammy(f'http://127.0.0.1:{port}',self.token,'background-test',timeout=.3)
                    self.assertEqual(client.state()['owner'],None)
                    self.assertTrue(pidfile.exists())
                    break
                except OSError:
                    if time.monotonic()>deadline:raise
                    time.sleep(.02)
        finally:
            if pidfile.exists():
                pid=int(pidfile.read_text())
                cmdline=Path(f'/proc/{pid}/cmdline')
                if cmdline.exists() and b'cammy-portal' in cmdline.read_bytes():os.kill(pid,15)
    def test_identity_does_not_claim_new_kernel(self):
        identity=self.a.identity();self.assertEqual(identity['os'],'CammyOS');self.assertFalse(identity['kernel_replaced'])
    def test_loopback_default(self):
        self.assertEqual(self.a.state()['sensing_default'],'off')
        self.assertEqual(self.a.preview_status()['phase'],'idle')
        self.assertEqual(self.a.preview_status()['keepalive_interval_seconds'],30)
        self.assert_status(404,lambda:self.a.request('GET','/v1/not-a-route'))

if __name__=='__main__':unittest.main()
