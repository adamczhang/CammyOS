"""Actual ParamFace / upstream Reachy app API; no camera or device access."""
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'host'), str(ROOT/'sdk')]
APP_ENV = all(importlib.util.find_spec(name) for name in ('fastapi','httpx','PIL','reachy_mini'))
if APP_ENV:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cammy_face import preset
    from cammy_reachy_app.main import configure, FaceLab, CammyFaceApp


class FakeLive:
    def __init__(self):
        self.started = False
        self.calls = []
    def status(self): return {'phase': 'live' if self.started else 'idle', 'synthetic': True, 'session':'fixture-current'}
    def start(self, seconds):
        if type(seconds) is not int or not 5 <= seconds <= 90: raise ValueError('bounded duration')
        if self.started: raise RuntimeError('busy')
        self.started = True; self.calls.append(('start', seconds)); return self.status()
    def stop(self, wait=False): self.started = False; self.calls.append(('stop', wait)); return self.status()
    def latest_frame(self): raise RuntimeError('no fresh frame')
    def audio_tail(self, after): raise RuntimeError('no fresh audio')


@unittest.skipUnless(APP_ENV, 'Use the prepared official Reachy reference venv for app integration tests')
class FaceAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.live = FakeLive()
        self.lab = FaceLab(live=self.live, data_dir=Path(self.temp.name)/'data')
        app = FastAPI()
        configure(app, self.lab)
        self.http = TestClient(app, base_url='http://127.0.0.1:8801')
        self.headers = {'X-Cammy-Lab': self.lab.token}
    def tearDown(self):
        self.http.close(); self.lab.close(); self.temp.cleanup()
    def test_native_validation_precedes_lease_and_old_gesture_cannot_restart(self):
        calls=[]
        class Control:
            thread=None
            def status(self):return {'phase':'idle'}
            def start(self,seconds):calls.append(('start',seconds));raise AssertionError('Unexpected lease')
            def stop(self,wait=False):calls.append(('stop',wait))
        self.lab.native_control.stop(wait=True);self.lab.native_control=Control()
        epoch=self.lab.native_epoch
        response=self.http.post('/api/native-input',json={'epoch':epoch,'sequence':1,'button':'down','action':'hold'},headers=self.headers)
        self.assertEqual(response.status_code,400);self.assertEqual(calls,[])
        # Releasing an idle gesture never takes a lease. Its newer sequence
        # invalidates a late earlier hold before a body request can occur.
        release=self.http.post('/api/native-input',json={'epoch':epoch,'sequence':2,'action':'release'},headers=self.headers)
        self.assertEqual(release.status_code,200);self.assertEqual(calls,[])
        hold=self.http.post('/api/native-input',json={'epoch':epoch,'sequence':1,'button':'left','action':'hold'},headers=self.headers)
        self.assertEqual(hold.status_code,409);self.assertEqual(calls,[])
        self.lab.stop_session();calls.clear()
        stale=self.http.post('/api/native-face',json={'epoch':epoch,'sequence':3,'expression':'happy'},headers=self.headers)
        self.assertEqual(stale.status_code,409);self.assertEqual(calls,[])

    def test_shared_agent_proxy_routes_keep_existing_app_auth_and_validation(self):
        calls=[]
        class Proxy:
            def call(self,*args):calls.append(args);return {'quiet':True,'busy':False}
            def close(self):pass
        self.lab.agent_proxy=Proxy()
        value={'thread_id':'11111111-2222-3333-4444-555555555555','arguments':{}}
        self.assertEqual(self.http.post('/api/agent/status',json=value).status_code,403)
        reply=self.http.post('/api/agent/status',json=value,headers=self.headers)
        self.assertEqual(reply.status_code,200);self.assertTrue(reply.json()['quiet']);self.assertEqual(len(calls),1)
        self.assertEqual(self.http.post('/api/agent/status',json={**value,'shell':'no'},headers=self.headers).status_code,400)
        self.assertIn('agent.js',self.http.get('/').text)

    def test_official_entrypoint_class_does_not_use_mock_media(self):
        from reachy_mini.apps.app import ReachyMiniApp
        self.assertTrue(issubclass(CammyFaceApp, ReachyMiniApp))
        self.assertEqual(CammyFaceApp.request_media_backend, 'no_media')
        self.assertEqual(CammyFaceApp.custom_app_url, 'http://127.0.0.1:8801')
    def test_editor_face_contract_and_actual_render(self):
        self.assertTrue(self.http.get('/status').json()['ok'])
        self.assertEqual(self.http.get('/face').json()['version'], 1)
        before = self.http.get('/face.png').content
        response = self.http.post('/face?save=1', content=preset('cat'), headers={'Content-Type':'text/plain'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['saved_to'], 'host-private-data')
        self.assertEqual(json.loads((self.lab.data_dir/'face.json').read_text()), json.loads(preset('cat')))
        self.assertEqual((self.lab.data_dir/'face.json').stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(self.http.get('/face.png').content, before)
        self.assertFalse(self.http.get('/status').json()['lcd_verified'])
        self.assertEqual(self.live.calls, [])
    def test_idle_loop_cycles_preview_and_manual_controls_cancel_it(self):
        from unittest.mock import patch
        self.lab.load(preset('pikachu'))
        with patch('cammy_reachy_app.main.time.monotonic',return_value=100):
            response=self.http.post('/api/face/idle',json={'enabled':True},headers=self.headers)
            self.assertEqual(response.status_code,200)
            self.lab.tick();self.assertEqual(self.lab.renderer.expression,'neutral')
        with patch('cammy_reachy_app.main.time.monotonic',return_value=104):
            self.lab.tick();self.assertEqual(self.lab.renderer.expression,'happy')
        with patch('cammy_reachy_app.main.time.monotonic',return_value=108):
            self.lab.tick();self.assertEqual(self.lab.renderer.expression,'angry')
        self.http.get('/face?expr=sad&hold=1')
        self.assertFalse(self.lab.status()['idle_loop']['enabled'])
        self.assertEqual(self.live.calls,[])
        self.assertFalse(self.lab.status()['lcd_verified'])

    def test_idle_loop_bounds_and_stop(self):
        for data in ({'enabled':'yes'},{'enabled':True,'seconds_per_expression':16},
                     {'enabled':True,'seconds_per_expression':0}, {'enabled':True,'extra':1}):
            self.assertEqual(self.http.post('/api/face/idle',json=data,headers=self.headers).status_code,400)
        self.assertEqual(self.http.post('/api/face/idle',json={'enabled':True},headers=self.headers).status_code,200)
        self.http.post('/api/face/drive',json={'mouth':.5},headers=self.headers)
        self.assertFalse(self.lab.status()['idle_loop']['enabled'])
        self.http.post('/api/face/idle',json={'enabled':True},headers=self.headers)
        stopped=self.http.post('/api/face/idle',json={'enabled':False},headers=self.headers).json()
        self.assertFalse(stopped['idle_loop']['enabled'])
        self.assertEqual(stopped['face']['expression'],'neutral')
        self.assertEqual(self.live.calls,[])

    def test_invalid_face_does_not_replace_previous_or_persist(self):
        before = self.lab.renderer.json
        for raw in (b'{}', b'{"version":1,"version":2}', b'x'*65537):
            self.assertIn(self.http.post('/face?save=1', content=raw).status_code, (400,413))
        self.assertEqual(self.lab.renderer.json, before)
        self.assertFalse((self.lab.data_dir/'face.json').exists())
    def test_expressions_hold_timeout_and_drives(self):
        self.assertEqual(self.http.get('/face?expr=happy&hold=1').status_code, 200)
        self.assertIsNone(self.lab.expression_until)
        self.http.get('/face?expr=sad')
        self.lab.expression_until = 0
        self.http.get('/face.png')
        self.assertEqual(self.lab.renderer.expression, 'neutral')
        self.assertEqual(self.http.get('/face?expr=unsupported').status_code, 400)
        self.assertEqual(self.http.post('/api/face/drive', headers=self.headers, json={'mouth': .8,'gaze':[.2,-.5]}).status_code, 200)
        for value in ({'mouth':2}, {'gaze': [1]}, {'gaze':1}, {'device':'fb0'}):
            self.assertEqual(self.http.post('/api/face/drive', headers=self.headers, json=value).status_code, 400)
    def test_cross_origin_and_hostile_host_cannot_control_or_read(self):
        for headers in ({'Origin':'http://evil.example'}, {'Host':'evil.example:8801'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.http.get('/face?expr=happy', headers=headers).status_code, 403)
            self.assertEqual(self.http.get('/', headers=headers).status_code, 403)
        self.assertEqual(self.http.post('/api/live/start', json={'seconds':30}).status_code, 403)
        self.assertEqual(self.http.get('/api/live/status').status_code, 403)
        self.assertEqual(self.live.calls, [])
    def test_native_desktop_iframe_can_load_but_cannot_bypass_sensor_origin(self):
        navigation = {'Sec-Fetch-Dest':'iframe','Sec-Fetch-Mode':'navigate',
                      'Sec-Fetch-Site':'cross-site','Origin':'tauri://localhost'}
        response = self.http.get('/?embedded=1&theme=dark',headers=navigation)
        self.assertEqual(response.status_code,200)
        policy = response.headers['Content-Security-Policy']
        self.assertIn("frame-ancestors 'self' tauri://localhost http://tauri.localhost;",policy)
        self.assertNotIn('https:',policy)
        navigation['Origin']='http://evil.example'
        self.assertEqual(self.http.get('/',headers=navigation).status_code,403)
        for origin in ('tauri://localhost','http://evil.example'):
            headers=dict(self.headers,Origin=origin)
            self.assertEqual(self.http.post('/api/live/start',headers=headers,json={'seconds':5}).status_code,403)
        self.assertFalse(self.live.calls)

    def test_old_sdk_stop_cannot_cancel_new_app_session(self):
        self.http.post('/api/live/start',headers=self.headers,json={'seconds':5})
        self.assertEqual(self.http.post('/api/live/stop',headers=self.headers,json={'session':'old-fixture'}).status_code,409)
        self.assertTrue(self.live.started)
        self.assertEqual(self.live.calls,[('start',5)])
        self.assertEqual(self.http.post('/api/live/stop',headers=self.headers,json={'session':'fixture-current'}).status_code,200)
        self.assertFalse(self.live.started)

    def test_bounded_explicit_sensing_and_stop(self):
        self.assertEqual(self.http.post('/api/live/start', headers=self.headers, json={'seconds':5}).status_code, 200)
        self.assertEqual(self.http.post('/api/live/start', headers=self.headers, json={'seconds':5}).status_code, 409)
        self.assertEqual(self.http.get('/api/live/frame.jpg', headers=self.headers).status_code, 409)
        self.assertEqual(self.http.post('/api/live/stop', headers=self.headers, json={}).status_code, 200)
        for value in ({'seconds':91},{'seconds':True},{'seconds':1},{'url':'rtsp://other'}):
            self.assertEqual(self.http.post('/api/live/start', headers=self.headers, json=value).status_code, 400)
        self.assertEqual(self.http.post('/api/live/start', headers=self.headers, content='{}').status_code, 415)
        self.assertEqual(self.http.post('/api/live/start', headers=self.headers, json={'extra':'x'*1500}).status_code, 413)
        self.assertEqual(self.live.calls, [('start',5),('stop',False)])
    def test_editor_static_routes_and_private_token_only_in_local_index(self):
        text = self.http.get('/').text
        self.assertIn(self.lab.token, text)
        self.assertNotIn(self.lab.token, self.http.get('/status').text)
        response = self.http.get('/face.png')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'self'", response.headers['Content-Security-Policy'])
        self.assertEqual(self.http.get('/assets/lab.js').status_code, 200)
        self.assertEqual(self.http.get('/editor/').status_code, 200)
        self.assertEqual(self.http.get('/editor/paramface.wasm').content[:4], b'\0asm')
    def test_persisted_host_face_restores_without_opening_camera(self):
        self.lab.load(preset('moon'), save=True)
        another = FaceLab(live=FakeLive(), data_dir=self.lab.data_dir)
        try: self.assertEqual(another.renderer.json, self.lab.renderer.json)
        finally: another.close()
