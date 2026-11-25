"""Deterministic simulator tests and loopback gateway/SDK acceptance."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
import urllib.error
from unittest.mock import Mock
import wave
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from cammy_simulator import Simulator
from cammy_host import Gateway, make_server
from cammy import Cammy, CammyError


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.sim = Simulator(clock=lambda: self.now)

    def request(self, path, body=None, agent='first', method=None):
        return self.sim.request(method or ('POST' if body is not None else 'GET'), path,
                                json.dumps(body).encode() if body is not None else None, agent)

    def acquire(self, ttl=30):
        return self.request('/v1/session', {'agent': 'first', 'ttl': ttl})

    def test_probe_jobs_timeout_cancel_history_and_strict_schema(self):
        self.assertEqual(len(self.request('/v1/probes')[1]['probes']),4)
        self.sim.settings({'probe_hang':True})
        code,job=self.request('/v1/probes',{'probe':'runtime'});self.assertEqual(code,202)
        self.assertIsNone(self.request('/v1/state')[1]['owner'])
        self.assertEqual(self.request('/v1/probes',{'probe':'audio-state'})[0],409)
        self.assertEqual(self.request('/v1/probes/'+job['job_id']+'/cancel',{},'second')[0],409)
        self.now+=1.01;timed=self.request('/v1/probes/'+job['job_id'])[1]
        self.assertEqual(timed['status'],'timed_out');self.assertIsNone(timed['result'])
        self.sim.settings({'probe_hang':False})
        second=self.request('/v1/probes',{'probe':'display-layout'})[1]
        self.request('/v1/stop',{})
        self.assertEqual(self.request('/v1/probes/'+second['job_id'])[1]['status'],'cancelled')
        third=self.request('/v1/probes',{'probe':'network-bindings'})[1]
        self.now+=.1;done=self.request('/v1/probes/'+third['job_id'])[1]
        self.assertEqual(done['status'],'completed');self.assertTrue(done['result']['simulation'])
        self.assertEqual(self.request('/v1/probes/'+job['job_id'])[0],404)
        self.assertEqual(self.request('/v1/probes',{'probe':'runtime','path':'/etc/shadow'})[0],400)
        self.assertEqual(self.sim.request('POST','/v1/probes',b'{"probe":"runtime","probe":"audio-state"}','first')[0],400)

    def test_preview_readiness_faults_are_separate_from_synthetic_frames(self):
        self.acquire(30);self.sim.settings({'preview_mode':'no-listener'})
        self.request('/v1/preview/start',{})
        first=self.request('/v1/preview/status')[1]
        self.assertEqual(first['phase'],'waiting-for-listener');self.assertIsNone(first['frame_fresh'])
        self.now+=5.1
        self.assertEqual(self.request('/v1/preview/status')[1]['failure_reason'],'rtsp-unreachable')
        remaining=self.request('/v1/state')[1]['lease_remaining_seconds']
        self.sim.settings({'preview_mode':'sdp-ready'});self.request('/v1/preview/check',{})
        self.assertTrue(self.request('/v1/preview/status')[1]['sdp_ready'])
        self.assertEqual(self.request('/v1/state')[1]['lease_remaining_seconds'],remaining)
        self.now+=25
        self.assertEqual(self.request('/v1/preview/status')[1]['phase'],'idle')

    def test_expiry_stop_and_ownership(self):
        self.assertEqual(self.acquire(1)[0], 200)
        self.assertEqual(self.request('/v1/session', {'agent': 'second'}, 'second')[0], 409)
        self.request('/v1/preview/start', {})
        self.now += 1.1
        state = self.request('/v1/state')[1]
        self.assertIsNone(state['owner'])
        self.assertFalse(state['preview_active'])
        self.assertEqual(self.request('/v1/look', {'pan_degrees': 10})[0], 409)
        self.acquire()
        self.request('/v1/look', {'pan_degrees': 20})
        self.request('/v1/face', {'expression': 'thinking'})
        self.assertEqual(self.request('/v1/stop', {}, 'observer')[0], 200)
        state = self.request('/v1/state')[1]
        self.assertIsNone(state['owner']); self.assertEqual(state['face'], 'idle')
        self.assertEqual(state['commanded_pan_degrees'], 20)
        self.assertFalse(state['position_measured'])

    def test_validation_prevents_invalid_commands(self):
        for ttl in [0, 121, True, 1.2]:
            self.assertEqual(self.acquire(ttl)[0], 400)
        self.assertEqual(self.sim.request('POST', '/v1/session', b'[]')[0], 400)
        self.acquire()
        for value in [-46, 46, float('nan'), True, '20']:
            self.assertEqual(self.request('/v1/look', {'pan_degrees': value})[0], 400)
        self.assertEqual(self.request('/v1/face', {'expression': 'unknown'})[0], 400)
        self.assertEqual(self.request('/v1/speak', {'text': ''})[0], 400)
        self.assertEqual(self.sim.settings({'latency_ms': 1501})[0], 400)
        self.assertEqual(self.sim.settings({'disconnected': 'yes'})[0], 400)
        self.assertEqual(self.sim.settings({'latency_ms': 10, 'stale_frame': 'bad'})[0], 400)
        self.assertEqual(self.sim.faults['latency_ms'], 0)

    def test_stale_frames_are_rejected_and_recover(self):
        self.acquire()
        self.assertEqual(self.request('/v1/simulation/frame')[0], 409)
        self.request('/v1/preview/start', {})
        first = self.request('/v1/simulation/frame')[1]
        self.assertTrue(first['fresh']); self.assertTrue(first['synthetic'])
        self.sim.settings({'stale_frame': True})
        self.now += 1.1
        status, frozen = self.request('/v1/simulation/frame')
        self.assertEqual(status, 503)
        self.assertFalse(frozen['fresh']); self.assertEqual(frozen['sequence'], first['sequence'])
        self.sim.settings({'stale_frame': False})
        status, fresh = self.request('/v1/simulation/frame')
        self.assertEqual(status, 200); self.assertGreater(fresh['sequence'], first['sequence'])

    def test_stock_profile_does_not_advertise_future_adapters(self):
        self.sim = Simulator('stock')
        caps = self.request('/v1/capabilities')[1]
        for feature in ['preview', 'face', 'gimbal', 'speech', 'audio_capture']:
            self.assertFalse(caps[feature]['available'])
        self.acquire()
        for path in ['/v1/look', '/v1/face', '/v1/speak', '/v1/audio/capture', '/v1/preview/start']:
            self.assertEqual(self.request(path, {})[0], 501)

    def test_audio_and_events_are_synthetic_and_bounded(self):
        self.acquire()
        audio = self.request('/v1/audio/capture', {})[1]
        self.assertTrue(audio['simulation'])
        with wave.open(io.BytesIO(base64.b64decode(audio['wav_base64']))) as wav:
            self.assertEqual(wav.getframerate(), 16000); self.assertEqual(wav.getnframes(), 1600)
        for _ in range(40): self.request('/v1/face', {'expression': 'idle'})
        self.assertEqual(len(self.request('/v1/events')[1]['events']), 32)


class SimulatorGatewayTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator()
        # Poisoned external configuration demonstrates the simulator bypasses HTTP entirely.
        self.gateway = Gateway('test-sim-token', ['http://192.0.2.1:8787'], simulator=self.sim)
        self.gateway.opener = Mock()
        self.gateway.opener.open.side_effect = AssertionError('Simulator attempted external I/O')
        self.gateway.refresh()
        self.server = make_server(self.gateway, ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever); self.thread.start()
        self.client = Cammy(f'http://127.0.0.1:{self.server.server_port}', 'test-sim-token', 'tester')

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.gateway.opener.open.assert_not_called()

    def test_sdk_virtual_body_and_fault_recovery(self):
        self.assertTrue(self.client.identity()['simulation'])
        self.client.acquire()
        self.client.preview_start()
        self.assertTrue(self.client.request('GET', '/v1/simulation/frame')['fresh'])
        self.assertFalse(self.client.request('POST', '/v1/look', {'pan_degrees': -20})['measured'])
        self.client.request('POST', '/v1/simulation', {'disconnected': True})
        self.assertFalse(self.gateway.snapshot()['connected'])
        with self.assertRaises(CammyError) as failure: self.client.state()
        self.assertEqual(failure.exception.status, 503)
        self.assertTrue(self.client.request('GET', '/v1/simulation')['simulation'])
        self.client.request('POST', '/v1/simulation', {'disconnected': False})
        self.assertTrue(self.gateway.snapshot()['connected'])
        self.assertIsNone(self.client.state()['owner'])
        with self.assertRaises(CammyError) as failure: self.client.preview_start()
        self.assertEqual(failure.exception.status, 409)
        self.client.acquire(); self.client.stop()

    def test_admin_requires_authentication_and_rejects_malformed_settings(self):
        other = Cammy(self.client.endpoint, 'wrong', 'intruder')
        with self.assertRaises(CammyError) as failure:
            other.request('POST', '/v1/simulation', {'disconnected': True})
        self.assertEqual(failure.exception.status, 401)
        self.assertFalse(self.sim.faults['disconnected'])
        with self.assertRaises(CammyError) as failure:
            self.client.request('POST', '/v1/simulation', {'latency_ms': -1})
        self.assertEqual(failure.exception.status, 400)
        with self.assertRaises(CammyError) as failure:
            self.client.request('POST', '/v1/simulation', [])
        self.assertEqual(failure.exception.status, 400)
