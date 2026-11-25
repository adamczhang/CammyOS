"""Official Reachy app extension; real Cammy media and host-only ParamFace.

The reference Reachy daemon remains a simulator. This app does not advertise
Cammy as a calibrated Reachy body or implement its MediaManager/WebRTC backend.
"""
import io
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import tempfile
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from reachy_mini.apps.app import ReachyMiniApp

from . import ROOT
from cammy_face import FaceRenderer, preset, PRESETS, EXPRESSIONS
from cammy_live import LiveSession, real_client, boot_id
from cammy_control import ControlSession
from cammy_reachy_agent import AgentProxy
from cammy_agent_core import AgentError
import asyncio

ORIGIN = 'http://127.0.0.1:8801'
NATIVE_PARENTS = ('tauri://localhost', 'http://tauri.localhost')


async def bounded_body(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, 'Request exceeds size limit')
        body.extend(chunk)
    return bytes(body)


class FaceLab:
    def __init__(self, renderer=None, live=None, data_dir=None, webrtc=False):
        self.renderer = renderer or FaceRenderer()
        self.live = live or LiveSession()
        self.native_control = ControlSession(client_factory=getattr(self.live,"client_factory",None) or real_client,
                                             read_boot=getattr(self.live,"read_boot",None) or boot_id)
        self.webrtc = None
        if webrtc:
            from cammy_webrtc import WebRTCPublisher
            self.webrtc = WebRTCPublisher(self.live)
        self.token = secrets.token_urlsafe(32)
        self.data_dir = data_dir or ROOT / 'private/paramface/lab'
        self.lock = threading.RLock()
        self.body_lock = threading.RLock()
        self.agent_proxy = AgentProxy()
        self.native_epoch = secrets.randbits(32)
        self.native_sequence = {"face":0,"input_button":0}
        self.last_tick = time.monotonic()
        self.expression_until = None
        self.idle_started = None
        self.idle_interval = 4.0
        self.idle_index = None
        self.saved = False
        saved = self.data_dir / 'face.json'
        if saved.is_file() and not saved.is_symlink():
            if saved.stat().st_size <= 65536:
                self.renderer.load(saved.read_bytes())
                self.saved = True

    def native_command(self, operation, epoch, sequence, **value):
        # Validate before taking a lease or advancing any intent sequence.
        if operation=='face':
            if value.get('action','show') not in ('show','hide','replay','idle') or value.get('expression','neutral') not in EXPRESSIONS:
                raise ValueError('Unsupported native face command')
        elif operation=='input_button':
            action=value.get('action','tap');button=value.get('button')
            if action not in ('tap','hold','release') or (action!='release' and button not in ('left','right','zoom_in','zoom_out','turn_180','center','playback_cycle')) or (action=='hold' and button not in ('left','right')):
                raise ValueError('Unsupported native input command')
        with self.body_lock:
            if type(epoch) is not int or epoch!=self.native_epoch:
                raise RuntimeError('Stale native control generation')
            if operation not in self.native_sequence or type(sequence) is not int or not 0<sequence<=9007199254740991 or sequence<=self.native_sequence[operation]:
                raise RuntimeError('Stale native intent sequence')
            self.native_sequence[operation]=sequence
            if self.live.status()['phase'] in ('starting','preparing','live','stopping'):
                return self.live.body_command(operation, **value)
            if self.native_control.status()['phase'] not in ('starting','control'):
                if operation=='input_button' and value.get('action')=='release':
                    return {'queued':False,'already_idle':True}
                self.native_control.start(30)
            deadline=time.monotonic()+3
            while self.native_control.status()['phase']=='starting' and time.monotonic()<deadline:
                time.sleep(.02)
            if epoch!=self.native_epoch:
                raise RuntimeError('Native control was stopped')
            return self.native_control.body_command(operation, **value)

    def expression(self, name, hold):
        self.idle_started = None
        self.renderer.set_expression(name)
        self.expression_until = None if hold else time.monotonic() + 30

    def tick(self):
        with self.lock:
            now = time.monotonic()
            if self.idle_started is not None:
                index = int(max(0, now-self.idle_started)/self.idle_interval) % len(EXPRESSIONS)
                if index != self.idle_index:
                    self.renderer.set_expression(EXPRESSIONS[index])
                    self.idle_index = index
            if self.expression_until is not None and now >= self.expression_until:
                self.renderer.set_expression('neutral')
                self.expression_until = None
            dt = min(250, max(0, (now - self.last_tick) * 1000))
            self.last_tick = now
            return self.renderer.frame(dt)

    def png(self):
        pixels = self.tick()
        output = io.BytesIO()
        Image.frombytes('RGB', (self.renderer.width, self.renderer.height), pixels).save(output, 'PNG')
        return output.getvalue()

    def load(self, raw, save=False):
        with self.lock:
            self.renderer.load(raw)
            self.idle_started = None
            if save:
                self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                info = self.data_dir.lstat()
                if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
                    raise ValueError('Private host face directory required')
                fd, filename = tempfile.mkstemp(prefix='face-', dir=self.data_dir)
                try:
                    with os.fdopen(fd, 'wb') as output:
                        output.write(self.renderer.json)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(filename, self.data_dir / 'face.json')
                finally:
                    if os.path.exists(filename):
                        os.unlink(filename)
                self.saved = True

    def status(self):
        return {'ok': True, 'face': self.renderer.state(), 'presets': PRESETS,
                'idle_loop': {'enabled': self.idle_started is not None,
                              'seconds_per_expression': self.idle_interval,
                              'order': EXPRESSIONS, 'target': 'host-preview'},
                'expressions': EXPRESSIONS, 'saved_to': 'host-private-data' if self.saved else None,
                'reachy_integration': 'official-app-extension',
                'reachy_body': 'reference-simulator', 'lcd_verified': False,
                'speaker_available': False, 'gimbal_adapter_available': False,
                'native_control':self.native_control.status(), 'native_epoch':self.native_epoch, 'native_sequence':dict(self.native_sequence), 'native_control_requires':'0.6 candidate; physical acceptance pending',
                'webrtc_configured': self.webrtc is not None}

    def idle_loop(self, enabled, interval=4):
        if type(enabled) is not bool or type(interval) not in (int,float) or not 2 <= interval <= 15:
            raise ValueError('Boolean enabled and interval2..15 required')
        with self.lock:
            if enabled and getattr(self,'conversation',None) is not None and self.conversation.status()['phase'] != 'idle':
                raise ValueError('Stop the conversation before starting the preview loop')
            self.idle_started = time.monotonic() if enabled else None
            self.idle_interval = float(interval)
            self.idle_index = None
            self.expression_until = None
            self.renderer.drive(mouth=0)
            if not enabled:self.renderer.set_expression('neutral')
            return self.status()

    def live_status(self):
        value = self.live.status()
        value['webrtc'] = {'available':False} if self.webrtc is None else self.webrtc.status()
        return value

    def start_session(self, seconds):
        with self.body_lock:
            self.native_epoch+=1
            self.native_sequence={"face":0,"input_button":0}
            self.native_control.stop(wait=True)
            if self.native_control.thread and self.native_control.thread.is_alive():
                raise RuntimeError('Previous native control cleanup pending')
            with self.lock:
                if self.webrtc is not None and not self.webrtc.status()['cleanup_confirmed']:
                    raise RuntimeError('Previous WebRTC cleanup is still active')
                value = self.live.start(seconds)
                if self.webrtc is not None:
                    try:
                        self.webrtc.start(value['session'])
                    except Exception:
                        self.live.stop()
                        raise
                return self.live_status()

    def stop_session(self, session=None, wait=False):
        with self.body_lock:
            with self.lock:
                if session is not None and self.live.status().get('session') != session:
                    raise RuntimeError('A different app session is active')
                self.native_epoch+=1
                self.native_sequence={"face":0,"input_button":0}
                self.native_control.stop(wait=True)
                self.live.stop(wait=False)
                if self.webrtc is not None:
                    self.webrtc.stop(wait=wait)
                if wait:
                    self.live.stop(wait=True)
                return self.live_status()

    def close(self):
        self.agent_proxy.close()
        self.native_epoch+=1
        self.native_control.stop(wait=True)
        if hasattr(self,'conversation'):
            self.conversation.close()
        self.stop_session(wait=True)
        self.renderer.close()


def configure(app: FastAPI, lab: FaceLab):
    from cammy_conversation import Conversation
    def face(expression):
        with lab.lock:
            lab.expression(expression,False)
    lab.conversation=Conversation(lab.live,lab.start_session,
        lambda session:lab.stop_session(session=session),face,
        cleanup_ready=lambda:lab.webrtc is None or lab.webrtc.status()['cleanup_confirmed'])
    @app.middleware('http')
    async def local_origin(request: Request, call_next):
        # Localhost DNS rebinding and browser-origin checks; no wildcard CORS.
        iframe_navigation = (request.method == 'GET' and request.url.path == '/'
                             and request.headers.get('sec-fetch-dest') == 'iframe'
                             and request.headers.get('sec-fetch-mode') == 'navigate')
        allowed_origins = (None, ORIGIN) + (NATIVE_PARENTS if iframe_navigation else ())
        if (request.headers.get('host') != '127.0.0.1:8801'
                or request.headers.get('origin') not in allowed_origins
                or request.headers.get('sec-fetch-site') in ('cross-site', 'same-site')
                and not iframe_navigation):
            return Response('Same-origin loopback access required', status_code=403)
        sensitive = request.url.path.startswith('/api/')
        if sensitive and not secrets.compare_digest(request.headers.get('x-cammy-lab', ''), lab.token):
            return Response('App session token required', status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; connect-src 'self'; frame-src 'self'; "
            "frame-ancestors 'self' tauri://localhost http://tauri.localhost; object-src 'none'")
        return response

    @app.get('/')
    def index():
        raw = (Path(__file__).parent / 'ui.html').read_text()
        return HTMLResponse(raw.replace('{{SESSION_TOKEN}}', lab.token))

    @app.get('/status')
    def status():
        return lab.status()

    @app.get('/face')
    def face(expr: str | None = None, hold: str = '0'):
        try:
            if expr is not None:
                if hold not in ('0', '1'):
                    raise ValueError('hold must be0 or1')
                lab.expression(expr, hold == '1')
                return {'ok': True, 'target': 'host-preview', 'physical_display_verified': False}
            return Response(lab.renderer.json, media_type='application/json')
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post('/face')
    async def push_face(request: Request, save: str = '0'):
        try:
            if save not in ('0', '1'):
                raise ValueError('save must be0 or1')
            lab.load(await bounded_body(request, 65536), save == '1')
            return {'ok': True, 'target': 'host-preview', 'saved_to': 'host-private-data' if save == '1' else None}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get('/face.png')
    def face_png():
        return Response(lab.png(), media_type='image/png')

    @app.post('/api/face/preset/{name}')
    def choose_preset(name: str):
        try:
            lab.load(preset(name))
            return lab.status()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    async def json_object(request):
        if request.headers.get('content-type') != 'application/json':
            raise HTTPException(415, 'application/json required')
        try:
            value = json.loads(await bounded_body(request, 1024))
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, 'JSON object required') from None
        if not isinstance(value, dict):
            raise HTTPException(400, 'JSON object required')
        return value

    @app.post('/api/face/drive')
    async def drive(request: Request):
        value = await json_object(request)
        try:
            if value.keys() - {'mouth', 'gaze'}:
                raise ValueError('Unknown face drive')
            lab.renderer.drive(value.get('mouth'), value.get('gaze'))
            lab.idle_started = None
            return lab.renderer.state()
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, 'Invalid face drive') from None

    @app.post('/api/face/idle')
    async def idle(request: Request):
        value = await json_object(request)
        try:
            if value.keys() - {'enabled','seconds_per_expression'}:raise ValueError('Unknown idle option')
            return lab.idle_loop(value.get('enabled'),value.get('seconds_per_expression',4))
        except (TypeError,ValueError):
            raise HTTPException(400,'Invalid preview idle loop') from None

    @app.post('/api/agent/{operation}')
    async def agent_action(operation: str,request: Request):
        if request.headers.get('content-type')!='application/json':raise HTTPException(415,'JSON required')
        try:value=json.loads(await bounded_body(request,4096))
        except (ValueError,UnicodeError):raise HTTPException(400,'JSON object required') from None
        if not isinstance(value,dict) or value.keys()-{'thread_id','session','arguments'}:raise HTTPException(400,'Unknown agent envelope')
        try:
            result=await asyncio.to_thread(lab.agent_proxy.call,operation,value.get('thread_id'),value.get('arguments',{}),value.get('session'))
            if operation=='frame':
                raw,metadata=result;return Response(raw,media_type='image/jpeg',headers={'X-Cammy-Frame':json.dumps(metadata)})
            return result
        except AgentError as e:raise HTTPException(e.code,str(e)) from None
        except Exception:raise HTTPException(503,'Shared Cammy agent service unavailable') from None

    @app.get('/api/live/status')
    def live_status():
        return lab.live_status()

    @app.get('/api/conversation/status')
    def conversation_status():
        return lab.conversation.status()

    @app.post('/api/conversation/{operation}')
    async def conversation_action(operation: str,request: Request):
        value=await json_object(request)
        keys={'start':{'seconds','speaker'},'listen':{'session','seconds'},
              'finish':{'session'},'reply':{'session','turn','text'},'stop':{'session'}}
        if operation not in keys or value.keys()-keys[operation]:
            raise HTTPException(400,'Unknown conversation operation/field')
        try:
            if operation=='start':return lab.conversation.start(**value)
            if operation=='stop':
                if value.get('session')!=lab.conversation.status()['session']:
                    raise RuntimeError('Current session binding required')
                return lab.conversation.stop()
            if operation=='listen':return lab.conversation.listen(**value)
            if operation=='finish':return lab.conversation.finish(**value)
            return lab.conversation.reply(**value)
        except (ValueError,TypeError):
            raise HTTPException(400,'Invalid conversation request') from None
        except RuntimeError:
            raise HTTPException(409,'Conversation unavailable or session/turn changed') from None

    @app.post('/api/native-face')
    async def native_face(request: Request):
        value=await json_object(request)
        if value.keys()-{'expression','action','epoch','sequence'} or type(value.get('epoch')) is not int or type(value.get('sequence')) is not int:raise HTTPException(400,'Unknown native face field')
        try:return await asyncio.to_thread(lab.native_command,'face',**value)
        except (ValueError,TypeError):raise HTTPException(400,'Invalid native face command') from None
        except Exception:raise HTTPException(409,'Native face unavailable or another controller owns Cammy') from None

    @app.post('/api/native-input')
    async def native_input(request: Request):
        value=await json_object(request)
        if value.keys()-{'button','action','epoch','sequence'} or type(value.get('epoch')) is not int or type(value.get('sequence')) is not int:raise HTTPException(400,'Unknown native input field')
        try:return await asyncio.to_thread(lab.native_command,'input_button',**value)
        except (ValueError,TypeError):raise HTTPException(400,'Invalid native input command') from None
        except Exception:raise HTTPException(409,'Native input unavailable or another controller owns Cammy') from None

    @app.post('/api/native-stop')
    async def native_stop(request: Request):
        value=await json_object(request)
        if value:raise HTTPException(400,'Expected empty object')
        return lab.stop_session(wait=True)

    @app.post('/api/live/start')
    async def live_start(request: Request):
        value = await json_object(request)
        if value.keys() - {'seconds'}:
            raise HTTPException(400, 'Unknown session option')
        try:
            return lab.start_session(value.get('seconds', 30))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post('/api/live/stop')
    async def live_stop(request: Request):
        value = await json_object(request)
        if value.keys() - {'session'} or ('session' in value and not isinstance(value['session'],str)):
            raise HTTPException(400, 'Optional session binding required')
        try:
            return lab.stop_session(value.get('session'))
        except RuntimeError as exc:
            raise HTTPException(409,str(exc)) from None

    @app.get('/api/live/frame.jpg')
    def live_frame():
        try:
            (data, sequence, received_at, utc), session, boot = lab.live.latest_frame()
            return Response(data, media_type='image/jpeg', headers={
                'X-Cammy-Session': session, 'X-Cammy-Boot-ID': boot,
                'X-Cammy-Frame-Sequence': str(sequence),
                'X-Cammy-PC-Receipt-UTC': str(utc),
                'X-Cammy-Frame-Age': str(round(time.monotonic()-received_at, 3))})
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get('/api/live/audio.pcm')
    def live_audio(after: int = 0):
        if after < 0:
            raise HTTPException(400, 'Nonnegative sequence required')
        try:
            data, metadata = lab.live.audio_tail(after)
            return Response(data, media_type='application/octet-stream', headers={
                'X-Cammy-Audio-Format': 'S16_LE', 'X-Cammy-Audio-Rate': '16000',
                'X-Cammy-Audio-Channels': '1', 'X-Cammy-Audio-Metadata': json.dumps(metadata)})
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None

    editor = ROOT / 'downloads/stackchan-face-editor/web/dist'
    if editor.is_dir():
        app.mount('/editor', StaticFiles(directory=editor, html=True), name='paramface-editor')
    app.mount('/assets', StaticFiles(directory=Path(__file__).parent / 'static'), name='cammy-face-assets')


class CammyFaceApp(ReachyMiniApp):
    custom_app_url: str | None = 'http://127.0.0.1:8801'
    request_media_backend: str | None = 'no_media'

    def __init__(self):
        super().__init__()
        self.lab = FaceLab(webrtc=os.environ.get('CAMMY_REACHY_WEBRTC') == '1')
        configure(self.settings_app, self.lab)

    def run(self, reachy_mini, stop_event):
        # Never dispatch reference simulator poses as real camera motion.
        try:
            stop_event.wait()
        finally:
            self.lab.stop_session(wait=True)


def main():
    app = CammyFaceApp()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: app.stop())
    try:
        app.wrapped_run()
    finally:
        app.lab.close()


if __name__ == '__main__':
    main()
