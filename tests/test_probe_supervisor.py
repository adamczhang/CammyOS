"""Fault fixtures for the real C supervisor; loopback only, no camera paths.

The harness sets an internal collector prefix unavailable in the shipped CLI.
"""
import json
import ctypes
import threading
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sdk'))
from cammy import Cammy, CammyError


@unittest.skipUnless(shutil.which('cc'), 'C compiler required')
class ProbeSupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / 'portal-fixture'
        harness = Path(cls.build.name) / 'harness.c'
        harness.write_text('#define main portal_main\n#include "' +
            str(ROOT / 'src/cammy_portal.c') + '"\n#undef main\n'
            'int main(int argc,char **argv){if(argc<2)return 2;'
            'probe_prefix=argv[1];return portal_main(argc-1,argv+1);}\n')
        subprocess.run(['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        str(harness), '-o', str(cls.binary)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def write(self, path, data):
        target = self.root / path.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.write('/proc/sys/kernel/random/boot_id', b'fixture-boot\n')
        self.write('/tmp/cammy-runtime/RELEASE', b'fixture-version\n')
        self.write('/tmp/cammy-runtime/state', b'HOME_STATION\n')
        self.write('/proc/uptime', b'123.45 90.00\n')
        self.token = 'fixture-token-' + 'b'*32
        tokenfile = self.root / 'token'; tokenfile.write_text(self.token)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        self.process = subprocess.Popen([str(self.binary), str(self.root), '--port',
            str(port), '--token-file', str(tokenfile)], stdout=subprocess.DEVNULL)
        self.a = Cammy(f'http://127.0.0.1:{port}', self.token, 'alpha', timeout=1)
        self.b = Cammy(self.a.endpoint, self.token, 'beta', timeout=1)
        deadline = time.monotonic()+3
        while True:
            try:
                self.a.state(); break
            except OSError:
                if self.process.poll() is not None or time.monotonic() > deadline: raise
                time.sleep(.01)

    def tearDown(self):
        self.process.terminate(); self.process.wait(timeout=3); self.tmp.cleanup()

    def blocked_runtime(self):
        path = self.root / 'tmp/cammy-runtime/RELEASE'
        path.unlink(); os.mkfifo(path)
        return self.a.probe_start('runtime')

    def finished(self, job):
        deadline = time.monotonic()+2
        while job['status'] == 'running':
            if time.monotonic() > deadline: self.fail('Probe failed to terminate within its budget')
            time.sleep(.02); job = self.a.probe_status(job['job_id'])
        return job

    def ready(self):
        deadline = time.monotonic()+1
        while self.a.probes()['supervisor'] != 'ready':
            if time.monotonic() > deadline: self.fail('Worker was not reaped')
            time.sleep(.02)

    def test_hung_collector_deadline_reaps_and_allows_next_job(self):
        job = self.blocked_runtime()
        with self.assertRaises(CammyError) as busy: self.b.probe_start('display-layout')
        self.assertEqual(busy.exception.status, 409)
        result = self.finished(job)
        self.assertEqual(result['status'], 'timed_out')
        self.assertIsNone(result['result'])
        self.assertLess(result['finished_monotonic_seconds']-result['started_monotonic_seconds'], 1.3)
        self.ready()
        self.assertEqual(self.finished(self.b.probe_start('display-layout'))['status'], 'completed')

    def test_stop_is_responsive_during_blocked_probe_and_partial_http(self):
        self.a.acquire()
        job = self.blocked_runtime()
        port = int(self.a.endpoint.rsplit(':', 1)[1])
        with socket.create_connection(('127.0.0.1', port)) as partial:
            partial.sendall(b'GET /v1/state HTTP/1.1\r\nAuthorization: ')
            time.sleep(.03)
            started = time.monotonic(); self.b.stop()
            self.assertLess(time.monotonic()-started, .5)
        self.assertIsNone(self.a.state()['owner'])
        self.assertEqual(self.a.probe_status(job['job_id'])['status'], 'cancelled')
        self.ready()

    def test_only_job_owner_can_cancel_and_no_lease_is_required(self):
        job = self.blocked_runtime()
        self.assertIsNone(self.a.state()['owner'])
        with self.assertRaises(CammyError) as denied: self.b.probe_cancel(job['job_id'])
        self.assertEqual(denied.exception.status, 409)
        with self.assertRaises(CammyError) as invalid:
            self.a.request('POST', '/v1/probes/'+job['job_id']+'/cancel', {'probe':'runtime'})
        self.assertEqual(invalid.exception.status, 400)
        self.assertEqual(self.a.probe_cancel(job['job_id'])['status'], 'cancelled')
        self.ready()

    def test_results_escape_input_mark_missing_truncation_and_bound_history(self):
        value = b'"quoted"\\\n\x00\xff' + b'x'*300
        self.write('/sys/class/graphics/fb0/virtual_size', value)
        first = self.finished(self.a.probe_start('display-layout'))
        samples = first['result']['samples']
        self.assertEqual(samples[0]['value'], value[:256].decode('latin1'))
        self.assertTrue(samples[0]['truncated'])
        self.assertTrue(samples[0]['available'])
        self.assertFalse(samples[1]['available'])
        self.assertIsNone(samples[1]['value'])
        self.assertNotEqual(samples[1]['error_errno'], 0)
        self.assertLess(len(json.dumps(first)), 12600)
        self.assertEqual(first['result']['boot_id']['value'], 'fixture-boot\n')
        self.ready(); second = self.finished(self.a.probe_start('runtime'))
        self.ready(); third = self.finished(self.a.probe_start('audio-state'))
        self.assertNotEqual(third['job_id'], second['job_id'])
        self.assertEqual(self.a.probe_status(second['job_id'])['job_id'], second['job_id'])
        with self.assertRaises(CammyError) as expired: self.a.probe_status(first['job_id'])
        self.assertEqual(expired.exception.status, 404)

    def test_network_inventory_limits_ports_and_preserves_bindings(self):
        data = b'  sl  local_address rem_address st\n'
        for i, (address, port, state) in enumerate([
                ('00000000',8554,'0A'),('0100007F',9999,'0A'),
                ('3500A8C0',8787,'0A'),('00000000',22,'0A'),('0100007F',8554,'01')]):
            data += f' {i}: {address}:{port:04X} 00000000:0000 {state} 00000000\n'.encode()
        self.write('/proc/net/tcp', data)
        result = self.finished(self.a.probe_start('network-bindings'))['result']['samples'][0]
        self.assertEqual(result['listeners'], [
            {'address':'0.0.0.0','port':8554}, {'address':'127.0.0.1','port':9999},
            {'address':'192.168.0.53','port':8787}])
        self.assertFalse(result['truncated'])
        self.assertNotIn('sdp_ready', result)

    def test_compat_diagnostics_worker_cannot_stall_stop(self):
        path=self.root/'tmp/cammy-runtime/RELEASE';path.unlink();os.mkfifo(path)
        results=[]
        def read_diagnostics():
            try: results.append(self.a.diagnostics())
            except CammyError as error:results.append(error.status)
        thread=threading.Thread(target=read_diagnostics);thread.start()
        deadline=time.monotonic()+.5
        while self.b.probes()['supervisor']!='busy':
            if time.monotonic()>deadline:self.fail('Compatibility collector did not start')
            time.sleep(.01)
        started=time.monotonic();self.b.stop()
        self.assertLess(time.monotonic()-started,.5)
        thread.join(timeout=2);self.assertFalse(thread.is_alive());self.assertEqual(results,[503])
        self.ready()

    def test_abnormal_worker_exit_is_failed_and_reaped(self):
        job = self.blocked_runtime()
        children = Path(f'/proc/{self.process.pid}/task/{self.process.pid}/children').read_text().split()
        self.assertEqual(len(children), 1)
        os.kill(int(children[0]), 9)
        result = self.finished(job)
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['result'])
        self.ready()

    def test_worker_dies_if_portal_is_killed(self):
        # Adopt/reap the orphan in this test process instead of relying on PID1.
        libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(previous), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        worker = None
        try:
            self.blocked_runtime()
            children = Path(f'/proc/{self.process.pid}/task/{self.process.pid}/children').read_text().split()
            self.assertEqual(len(children), 1); worker = int(children[0])
            self.process.kill(); self.process.wait(timeout=2)
            deadline = time.monotonic()+1
            while True:
                done, status = os.waitpid(worker, os.WNOHANG)
                if done:
                    worker = None
                    self.assertTrue(os.WIFSIGNALED(status))
                    self.assertEqual(os.WTERMSIG(status), 9)
                    break
                if time.monotonic() > deadline: self.fail('Worker survived portal crash')
                time.sleep(.01)
        finally:
            if worker is not None:
                try: os.kill(worker, 9); os.waitpid(worker, 0)
                except ProcessLookupError: pass
            libc.prctl(36, previous.value, 0, 0, 0)

    def test_http_cannot_supply_paths_or_enable_other_collectors(self):
        for body in [{'probe':'runtime','path':str(self.root/'token')},
                     {'probe':'native-ownership'}, {'probe':'runtime','command':'id'}]:
            with self.assertRaises(CammyError) as invalid: self.a.request('POST','/v1/probes',body)
            self.assertEqual(invalid.exception.status, 400)
        self.assertEqual(self.a.probes()['supervisor'], 'ready')


if __name__ == '__main__': unittest.main()
