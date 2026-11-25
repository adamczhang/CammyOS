import importlib.util
import json
from pathlib import Path
import threading
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec=importlib.util.spec_from_file_location('cammy_host',Path(__file__).resolve().parents[1]/'host/cammy_host.py')
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
from cammy import Cammy, CammyError

class HostTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];calls=self.calls
        class Body(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                calls.append((self.path,self.headers.get('Authorization')))
                payload={'service':'cammy-portal'} if self.path=='/health' else {'os':'CammyOS'}
                data=json.dumps(payload).encode();self.send_response(200);self.end_headers();self.wfile.write(data)
        self.body=ThreadingHTTPServer(('127.0.0.1',0),Body)
        self.thread=threading.Thread(target=self.body.serve_forever);self.thread.start()
        self.gateway=host.Gateway('test-token', ['http://127.0.0.1:'+str(self.body.server_port)])
        self.http=host.make_server(self.gateway,('127.0.0.1',0))
        self.web=threading.Thread(target=self.http.serve_forever);self.web.start()
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.web.join()
        self.body.shutdown();self.body.server_close();self.thread.join()
    def request(self,path,token='test-token'):
        request=urllib.request.Request('http://127.0.0.1:'+str(self.http.server_port)+path,
                                      headers={'Authorization':'Bearer '+token})
        return urllib.request.urlopen(request,timeout=2)
    def test_disconnected_is_not_fake_success(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:self.request('/v1/state')
        self.assertEqual(exc.exception.code,503)
    def test_auth_and_path_allowlist(self):
        self.gateway.refresh();before=len(self.calls)
        for path,token,status in [('/v1/state','wrong',401),('/arbitrary/path','test-token',404)]:
            with self.assertRaises(urllib.error.HTTPError) as exc:self.request(path,token)
            self.assertEqual(exc.exception.code,status)
        self.assertEqual(len(self.calls),before)
    def test_monitor_checks_the_camera_every_ten_seconds(self):
        waits=[]
        class Stop:
            def is_set(self): return len(waits)>=2
            def wait(self, seconds): waits.append(seconds)
        refresh,stop=self.gateway.refresh,self.gateway.stop
        self.gateway.refresh,self.gateway.stop=(lambda: None),Stop()
        try:
            self.gateway.monitor()
        finally:
            self.gateway.refresh,self.gateway.stop=refresh,stop
        self.assertEqual(waits,[10,10])

    def test_refresh_and_authenticated_proxy(self):
        self.assertTrue(self.gateway.refresh()['connected'])
        with self.request('/v1/identity') as reply:self.assertEqual(json.load(reply)['os'],'CammyOS')
        self.assertEqual(self.calls[-1],('/v1/identity','Bearer test-token'))
        self.assertFalse(self.gateway.snapshot()['sensing_started_by_gateway'])

    def test_real_portal_control_through_gateway(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            token='integration-token-'+'a'*32
            tokenfile=Path(temporary)/'token';tokenfile.write_text(token)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            process=subprocess.Popen([str(root/'build/host/cammy-portal'),
                '--port',str(port),'--token-file',str(tokenfile)])
            try:
                self.gateway.token=token
                self.gateway.endpoints=['http://127.0.0.1:'+str(port)]
                for _ in range(50):
                    if self.gateway.refresh()['connected']:break
                    time.sleep(.02)
                self.assertTrue(self.gateway.snapshot()['connected'])
                endpoint='http://127.0.0.1:'+str(self.http.server_port)
                first=Cammy(endpoint,token,'first');second=Cammy(endpoint,token,'second')
                self.assertEqual(first.acquire(17)['ttl'],17)
                self.assertEqual(second.state()['owner'],'first')
                with self.assertRaises(CammyError) as conflict:second.acquire()
                self.assertEqual(conflict.exception.status,409)
                self.assertTrue(first.release()['released'])
                self.assertEqual(second.acquire()['owner'],'second')
                second.release()
                self.assertFalse(first.state()['preview_active'])
                self.assertEqual(first.preview_status()['phase'],'idle')
                self.assertEqual(len(first.probes()['probes']),4)
                job=first.probe_start('display-layout')
                for _ in range(60):
                    job=first.probe_status(job['job_id'])
                    if job['status']!='running':break
                    time.sleep(.03)
                self.assertEqual(job['status'],'completed')
                self.assertEqual(first.probe_cancel(job['job_id'])['status'],'completed')
                with self.assertRaises(CammyError) as invalid:
                    first.request('GET','/v1/probes/job-1?path=/etc/shadow')
                self.assertEqual(invalid.exception.status,404)
            finally:
                process.terminate();process.wait(timeout=5)
