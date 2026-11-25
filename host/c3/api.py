"""C3 local API: the one door for T3 threads, MCP tools and the desktop panel.

127.0.0.1 only, bearer token from private/c3/api-token (made at first start, mode 0600).
Clients never open their own lease, preview or mic; they ask C3.
The local desktop also presents X-Cammy-Desktop-Token for management routes in Kids mode.

  GET  /v1/status              body, listener, echo, voice and speaker state, counters
  GET  /v1/transcript?n=50     newest transcript lines
  GET  /v1/brain               resident brain state
  GET  /v1/brain/history       resident brain turns (optional ?profile=id)
  POST /v1/brain/profiles {"id","name","provider","instructions"?}
  POST /v1/brain/select   {"id"}
  POST /v1/brain/message  {"text"}  private resident brain chat
  POST /v1/brain/profiles/{id}/message {"text"}  requires this profile to be active
  POST /v1/brain/cancel   {}
  GET  /v1/calendar           calendar setup status for the selected profile (no events)
  POST /v1/calendar/connect {"profile","connection"}  start Executor sign-in: {authorize_url}
  POST /v1/calendar/cancel | test | disconnect {"profile"}
  POST /v1/calendar/query {"profile","range"}  today | tomorrow | next, masked events
  GET  /v1/calendar/result?profile=<id>        newest result while that selection lasts
                               Every calendar POST and the result also need the header
                               X-Cammy-Panel-Token (private/c3/calendar-panel-token): only the
                               Control Center window shows calendar data or changes accounts.
  GET  /v1/providers          resident provider status (optional ?refresh=1)
  POST /v1/providers/test {"id"}  run an explicit provider check and return its row
  POST /v1/message {"text"}    a user-role turn for Gemini (wakes Voice)
  POST /v1/say    {"text"}     Cammy says this, in its own voice
  POST /v1/look                newest camera frame (image/jpeg)
  POST /v1/capture {"kind"}    photo | video, Adam presses the red button
  POST /v1/sleep | /v1/wake    close or open the Gemini connection now
  POST /v1/duplex {"mode"}     half | auto | full
  POST /v1/talk-gate {"mode", "near_dbfs", "follow_dbfs"}   smart | name | open (c3/gate.py)
  GET  /v1/instructions        selected profile's voice Markdown: {profile, profile_name,
                               agent:{text,revision}, skills:[{id,text,revision}], tools:[{name,
                               description}]}; no profile -> nulls, [] and a message
  PUT  /v1/instructions/agent        {"profile","text","revision"}  work/AGENT.md
  PUT  /v1/instructions/skills/{id}  {"profile","text","revision"}  work/skills/<id>.md
                               revision null creates; 200 the new snapshot; 409 when the profile
                               is not the active one (or changed) or the revision is stale.
                               Gemini voice only; used from the next Gemini connection.
  GET  /v1/preview/history     selected profile's camera history: counts and Unix times, no images
  GET  /v1/preview/frame?profile=<active id>&at=<Unix s>   the stored JPEG nearest `at`
                               (headers X-Cammy-Frame-Time, X-Cammy-Profile); 404 gap or
                               overwritten, 409 the active profile changed
  POST /v1/turn-around         press Cammy's turn-180 button
  POST /v1/follow {"on"}       face follow on/off (the stock DOWN-button toggle, state checked)
  POST /v1/camera-view {"on"}  the screen shows the live camera view (true) or the face (false)
  POST /v1/look-around         sweep end stop to end stop with looks; photos go to Gemini
  POST /v1/inject {"file"}     test: mix private/c3/inject/<file> (16 kHz mono WAV) into the mic
  POST /v1/echo-check {"file"} test: play private/c3/inject/<file> (24 kHz mono WAV), measure Echo

Family deployment (c3/family.py): every route is public (device controls, status, family,
play, Calendar access, Calendar cancel and disconnect), cloud (503: the routes that reach
Gemini) or adult (403 'kids_mode' while Kids; anything not listed). Desktop management
routes use their separate credential. Adult is open while
Cammy shows the Cammy face pack and CCC has no Kids override; no PIN is involved. An adult
result is withheld if Kids started while the request ran.
  GET  /v1/family              mode, reason, generation, known face pack, Kids override,
                               and calendar_access
  POST /v1/family/mode {"mode": "kids" | "automatic"}   CCC's saved Kids override, or reset
  POST /v1/family/face-pack {"id": "cammy" | "child"}   change the face, wait for the read-back
  GET  /v1/calendar-access     open, profile, expiry, PIN and limit state
  POST /v1/calendar-access/unlock {"pin", "profile"?}   private Calendar for the selected
                               profile (local desktop or Adult mode)
  POST /v1/calendar-access/lock
  POST /v1/calendar-access/pin {"new_pin", "setup_code" | "current_pin"}   never unlocks
  GET  /v1/play | POST /v1/play/start {"game"} | /v1/play/answer {"text"} | /v1/play/stop
"""
import asyncio
import hmac
import json
import logging
import math
import os
import stat
import wave
import secrets
from pathlib import Path

from aiohttp import web
from google.genai import types

DUPLEX = ('half', 'auto', 'full')
PREVIEW_FRAME_MAX_DISTANCE_S = 2.0   # frames are ~1 s apart: farther than this is a gap
log = logging.getLogger('c3.api')


def unavailable(code, message, recovery):
    return {'available': False, 'code': code, 'error': message + ' ' + recovery, 'recovery': recovery}


def _service_unavailable(problem):
    return web.HTTPServiceUnavailable(text=json.dumps(problem), content_type='application/json')


def api_token(folder):
    path = Path(folder) / 'api-token'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.touch(mode=0o600)
        path.write_text(secrets.token_hex(24))
    return path.read_text().strip()


def desktop_token(folder):
    path = Path(folder) / 'desktop-token'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_hex(32))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('desktop-token must be a private file owned by the service user')
        value = f.read(65).strip()
    if len(value) != 64 or any(ch not in '0123456789abcdef' for ch in value):
        raise ValueError('invalid desktop token')
    return value


def make_app(c3, token, desktop=None):
    @web.middleware
    async def auth(request, handler):
        given = request.headers.get('Authorization', '')
        if not hmac.compare_digest(given.encode(), ('Bearer ' + token).encode()):
            return web.json_response({'error': 'unauthorized'}, status=401)
        request['desktop'] = bool(desktop and request.remote in ('127.0.0.1', '::1') and
                                  hmac.compare_digest(request.headers.get('X-Cammy-Desktop-Token', '').encode(),
                                                      desktop.encode()))
        return await handler(request)

    async def body(request):
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text='JSON body required')
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text='JSON object required')
        return data

    def text_of(data, limit=4000):
        text = data.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise web.HTTPBadRequest(text='text: 1..%d characters' % limit)
        return text.strip()

    def clip(data, rate):
        name = data.get('file')
        folder = (Path(c3.data_dir) / 'inject').resolve()
        if not isinstance(name, str) or '/' in name or not name.endswith('.wav'):
            raise web.HTTPBadRequest(text='file: a .wav name in private/c3/inject/')
        try:
            with wave.open(str(folder / name)) as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (rate, 1, 2):
                    raise web.HTTPBadRequest(text='need %d Hz mono 16-bit' % rate)
                return w.readframes(w.getnframes())
        except FileNotFoundError:
            raise web.HTTPNotFound(text='no such clip')

    async def inject(request):
        pcm = clip(await body(request), 16000)
        c3.inject += pcm
        c3.transcript.add('system', 'test speech injected: %s (%.1f s)' % ((await body(request))['file'], len(pcm) / 32000))
        return web.json_response({'queued_s': round(len(c3.inject) / 32000, 1)})

    async def echo_check(request):
        pcm = clip(await body(request), 24000)
        return web.json_response(await c3.echo_check(pcm, len(pcm) / 48000))

    async def playback_check(request):
        data = await body(request)
        pieces = data.get('pieces') or [1.0, 2.0, 2.0]
        if not (isinstance(pieces, list) and 1 <= len(pieces) <= 8 and all(isinstance(p, (int, float)) and 0.2 <= p <= 4 for p in pieces)):
            raise web.HTTPBadRequest(text='pieces: 1-8 numbers of seconds, 0.2-4 each')
        return web.json_response(await c3.playback_check(tuple(float(p) for p in pieces), float(data.get('gap_s', 0.15))))

    async def debug(request):
        data = await body(request)
        if 'save_replies' in data:
            c3.speaker.save_dir = (c3.data_dir / 'debug') if data['save_replies'] else None
        return web.json_response({'save_replies': c3.speaker.save_dir is not None})

    async def turn_around(_):
        try:                    # the same path as the voice tool: settle, follow off, motion lock
            ok, message = await c3.moves.turn_around()
        except Exception as exc:  # noqa: BLE001
            return web.json_response({'error': str(exc)[:160]}, status=502)
        if not ok:
            return web.json_response({'error': message}, status=409)
        c3.transcript.add('system', 'turn around')
        return web.json_response({'turned': True, 'moves': c3.moves.state})

    def flag(data):
        on = data.get('on')
        if not isinstance(on, bool):
            raise web.HTTPBadRequest(text='on: true or false')
        return on

    async def follow(request):
        on = flag(await body(request))
        try:
            ok, message = await c3.moves.set_follow(on)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({'error': str(exc)[:160]}, status=502)
        c3.transcript.add('system', 'follow %s: %s' % (on, message))
        return web.json_response({'ok': ok, 'message': message, 'moves': c3.moves.state}, status=200 if ok else 409)

    async def camera_view(request):
        message = c3.moves.show_camera(flag(await body(request)))
        return web.json_response({'message': message, 'moves': c3.moves.state})

    async def look_around(_):
        if not c3.voice.start_sweep():
            return web.json_response({'error': getattr(c3.voice, 'sweep_refusal', None) or 'already looking around'},
                                     status=409)
        c3.transcript.add('system', 'look around started')
        return web.json_response({'started': True, 'sweep_s': c3.moves.sweep_s})

    async def status(request):
        return web.json_response(c3.status(desktop=True) if request.get('desktop') else c3.status())

    async def transcript(request):
        try:
            n = max(1, min(400, int(request.query.get('n', 50))))
        except ValueError:
            raise web.HTTPBadRequest(text='n must be a number')
        lines = [e for _, e in list(c3.transcript.recent)[-n:]]
        return web.json_response({'entries': lines})

    async def turn(request):
        text = text_of(await body(request))
        c3.transcript.add('system', 'turn: ' + text)
        c3.voice.add_turn([types.Part(text=text)], 'api')
        return web.json_response({'queued': True, 'voice': c3.voice.state})

    async def say(request):
        text = text_of(await body(request), 1000)
        c3.transcript.add('system', 'say: ' + text)
        c3.voice.add_turn([types.Part(text='(Say this to Adam, in your own voice and words, briefly: %s)' % text)], 'say')
        return web.json_response({'queued': True, 'voice': c3.voice.state})

    async def look(_):
        frame = await c3.eyes.look(wait=6.0)
        if not frame:
            return web.json_response({'error': 'no frame (camera down or preview not ready)'}, status=503)
        return web.Response(body=frame, content_type='image/jpeg')

    async def capture(request):
        kind = (await body(request)).get('kind')
        if kind not in ('photo', 'video'):
            raise web.HTTPBadRequest(text='kind: photo or video')
        if not c3.camera:
            return web.json_response({'error': 'capture unavailable'}, status=503)
        if not c3.camera.start(kind):
            return web.json_response({'error': getattr(c3.camera, 'refusal', None) or 'already taking one'}, status=409)
        return web.json_response({'started': kind})

    async def sleep(_):
        c3.voice.idle_now()
        return web.json_response({'voice': c3.voice.state})

    async def wake(_):
        c3.voice.wake.set()
        return web.json_response({'voice': c3.voice.state})

    async def duplex(request):
        mode = (await body(request)).get('mode')
        if mode not in DUPLEX:
            raise web.HTTPBadRequest(text='mode: ' + ' | '.join(DUPLEX))
        c3.duplex = mode
        return web.json_response({'duplex': mode})

    async def talk_gate(request):
        from .gate import MODES
        data = await body(request)
        gate = c3.gate
        mode = data.get('mode', gate.mode)
        if mode not in MODES:
            raise web.HTTPBadRequest(text='mode: ' + ' | '.join(MODES))
        try:
            near = float(data.get('near_dbfs', gate.near))
            follow = float(data.get('follow_dbfs', gate.follow))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text='near_dbfs and follow_dbfs are numbers (dBFS)')
        if not (-90 <= follow <= near <= 0):
            raise web.HTTPBadRequest(text='need -90 <= follow_dbfs <= near_dbfs <= 0')
        if mode != 'open' and gate.spotter is None:
            from .spotter import Spotter
            gate.spotter = Spotter()
        gate.mode, gate.near, gate.follow = mode, near, follow
        return web.json_response({'talk_gate': gate.state})

    from . import family as familymod
    family = getattr(c3, 'family', None)
    family = family if isinstance(family, familymod.FamilyPolicy) else None
    middlewares = [auth] + ([familymod.api_middleware(family)] if family is not None else [])
    app = web.Application(middlewares=middlewares, client_max_size=64 * 1024)
    app.add_routes([web.get('/v1/status', status), web.get('/v1/transcript', transcript),
                    web.post('/v1/message', turn), web.post('/v1/say', say), web.post('/v1/look', look),
                    web.post('/v1/capture', capture), web.post('/v1/sleep', sleep), web.post('/v1/wake', wake),
                    web.post('/v1/duplex', duplex), web.post('/v1/talk-gate', talk_gate), web.post('/v1/inject', inject),
                    web.post('/v1/echo-check', echo_check), web.post('/v1/playback-check', playback_check), web.post('/v1/debug', debug), web.post('/v1/turn-around', turn_around),
                    web.post('/v1/follow', follow), web.post('/v1/camera-view', camera_view),
                    web.post('/v1/look-around', look_around)])
    _add_preview_routes(app, c3)
    _add_instruction_routes(app, c3, body)
    if family is not None:
        access = getattr(c3, 'calendar_access', None)
        familymod.add_routes(app, c3, family, body,
                             access if isinstance(access, familymod.CalendarAccess) else None)
        play = getattr(c3, 'play', None)
        if play is not None:
            from . import local_play
            local_play.add_routes(app, play, body)
    if getattr(c3, 'brain_backend', None) == 'resident':
        brain_problem = getattr(c3, 'brain_problem', lambda: None)
        _add_brain_routes(app, c3.brain, brain_problem, body, getattr(c3, 'calendar', None))
        _add_provider_routes(app, getattr(c3, 'provider_health', None),
                             getattr(c3, 'provider_problem', None), brain_problem, body)
        if getattr(c3, 'calendar', None) is not None:
            _add_calendar_routes(app, c3.calendar, c3.calendar_panel_token, body)
    return app


def _add_calendar_routes(app, calendar, panel_token, body):
    from .calendar import ACCESS_CODES, CalendarBlocked, RANGES
    statuses = {'no_profile': 409, 'profile_changed': 409, 'connecting': 409, 'busy': 409, 'changed': 409,
                'invalid_connection': 400, 'invalid_range': 400, 'invalid_profile': 400, 'bad_request': 400,
                'not_connected': 409, 'reconnect_required': 409, 'broker_unavailable': 503}

    def panel(handler):
        async def handle(request):
            given = request.headers.get('X-Cammy-Panel-Token', '')
            if not hmac.compare_digest(given.encode(), panel_token.encode()):
                return web.json_response({'error': 'the Control Center window does this'}, status=403)
            try:
                return await handler(request)
            except CalendarBlocked as exc:
                code = 403 if exc.code in ACCESS_CODES else statuses.get(exc.code, 502)
                return web.json_response({'code': exc.code, 'error': exc.message}, status=code)
        return handle

    def field(data, key, allowed=None):
        value = data.get(key)
        if not isinstance(value, str) or not value or (allowed is not None and value not in allowed):
            raise web.HTTPBadRequest(text=key + ': ' + (' | '.join(allowed) if allowed else 'nonempty string required'))
        return value

    async def status(request):
        given = request.headers.get('X-Cammy-Panel-Token', '')
        window = bool(given) and hmac.compare_digest(given.encode(), panel_token.encode())
        options = {'desktop': True} if window and request.get('desktop') else {}
        return web.json_response(await calendar.status(panel=window, **options))

    def desktop_options(request):
        return {'desktop': True} if request.get('desktop') else {}

    async def connect(request):
        data = await body(request)
        return web.json_response(await calendar.connect(field(data, 'profile'), field(data, 'connection'),
                                                        **desktop_options(request)))

    async def cancel(request):
        return web.json_response(await calendar.cancel_connect(field(await body(request), 'profile')))

    async def test(request):
        return web.json_response(await calendar.test(field(await body(request), 'profile'), **desktop_options(request)))

    async def disconnect(request):
        return web.json_response(await calendar.disconnect(field(await body(request), 'profile')))

    async def query(request):
        data = await body(request)
        return web.json_response(await calendar.query(field(data, 'range', RANGES), profile=field(data, 'profile'),
                                                      **desktop_options(request)))

    async def result(request):
        profile = request.query.get('profile')
        if not profile:
            raise web.HTTPBadRequest(text='profile: required')
        options = desktop_options(request)
        if calendar.last(profile, **options) is not None:
            await calendar.refresh_access()
        return web.json_response({'result': calendar.last(profile, **options)},
                                 headers={'Cache-Control': 'no-store'})

    app.add_routes([web.get('/v1/calendar', status),
                    web.post('/v1/calendar/connect', panel(connect)), web.post('/v1/calendar/cancel', panel(cancel)),
                    web.post('/v1/calendar/test', panel(test)), web.post('/v1/calendar/disconnect', panel(disconnect)),
                    web.post('/v1/calendar/query', panel(query)), web.get('/v1/calendar/result', panel(result))])


def _identity(profile):
    """(id, workdir) of an active_profile() result, the part that must not change mid-request."""
    return None if profile is None else (profile[0], str(profile[2]))


def _history_profile(status):
    """The profile id Eyes says its history belongs to (a string, or a dict with an id)."""
    profile = status.get('profile') if isinstance(status, dict) else None
    if isinstance(profile, dict):
        profile = profile.get('id')
    return profile if isinstance(profile, str) else None


def _unix(status, *keys):
    for key in keys:
        value = status.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 1e8:
            return float(value)
    return None


def _add_preview_routes(app, c3):
    """The look-back viewer's door into the selected profile's camera history.

    Each request pins the active profile, does every disk-backed read in a worker thread, then
    checks the pin again (Brain selection and Eyes' history owner) before answering, so a
    profile switch mid-request yields 409 and never another profile's frames or counts."""
    def active(request):
        getter = getattr(c3, 'selected_profile' if request.get('desktop') else 'active_profile', None)
        return getter() if callable(getter) else None

    def no_history():
        return _service_unavailable(unavailable(
            'no_profile', 'Camera history is off: no resident brain profile is selected.',
            'Select a profile on the Brain tab.'))

    def changed():
        return web.HTTPConflict(text=json.dumps({'error': 'the active profile changed; reload the history'}),
                                content_type='application/json')

    def eyes_status():
        status = getattr(c3.eyes, 'history_status', None)
        return status() if callable(status) else status

    def broken():
        return _service_unavailable(unavailable('no_history', 'Camera history is not available.',
                                                'Check the C3 log.'))

    async def read(func, *args, **kwargs):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a disk or store fault is a 503, never a crash
            log.warning('camera history read failed (%s)', type(exc).__name__)
            raise broken() from None

    async def pinned_status(request, pinned):
        status = await read(eyes_status)
        if not isinstance(status, dict):
            raise broken()
        if _identity(active(request)) != _identity(pinned) or _history_profile(status) != pinned[0]:
            raise changed()
        return status

    async def history(request):
        pinned = active(request)
        if pinned is None:
            raise no_history()
        status = await pinned_status(request, pinned)
        return web.json_response({
            'profile': pinned[0], 'profile_name': pinned[1],
            'bytes': int(status.get('bytes') or 0), 'capacity_bytes': int(status.get('capacity_bytes') or 0),
            'recording_allowed': bool(getattr(c3.eyes, 'history_recording', lambda: True)()),
            'frames': int(status.get('frames') or 0),
            'oldest': _unix(status, 'oldest', 'oldest_time'), 'newest': _unix(status, 'newest', 'newest_time')},
            headers={'Cache-Control': 'no-store'})

    async def frame(request):
        asked = request.query.get('profile', '')
        try:
            at = float(request.query.get('at', ''))
        except ValueError:
            at = math.nan
        if not asked or not math.isfinite(at) or at <= 0:
            raise web.HTTPBadRequest(text='profile (the active profile id) and at (Unix seconds) required')
        pinned = active(request)
        if pinned is None:
            raise no_history()
        if asked != pinned[0]:
            raise changed()
        lookup = getattr(c3.eyes, 'history_frame', None)
        if not callable(lookup):
            raise broken()
        await pinned_status(request, pinned)
        found = await read(lookup, at, max_distance_s=PREVIEW_FRAME_MAX_DISTANCE_S)
        await pinned_status(request, pinned)      # the frame is sent only if the owner held throughout
        if not found or not found[1]:
            raise web.HTTPNotFound(text=json.dumps({'error': 'no stored frame near that time'}),
                                   content_type='application/json')
        wall, jpeg = found
        return web.Response(body=bytes(jpeg), content_type='image/jpeg', headers={
            'X-Cammy-Frame-Time': '%.6f' % wall, 'X-Cammy-Profile': pinned[0], 'Cache-Control': 'no-store'})

    app.add_routes([web.get('/v1/preview/history', history), web.get('/v1/preview/frame', frame)])


def _add_instruction_routes(app, c3, body):
    """The selected profile's voice Markdown (c3/instructions.py), for Gemini only.

    Like the preview routes, each request pins active_profile() and checks it again after the
    worker-thread disk work; a save also checks it just before its atomic rename. A save never
    wakes or interrupts the voice: the composed persona changes and the next Gemini connection
    starts fresh (c3/voice.py drops the resume handle when the persona base changed)."""
    from . import instructions

    def active(request):
        getter = getattr(c3, 'selected_profile' if request.get('desktop') else 'active_profile', None)
        return getter() if callable(getter) else None

    def tools():
        getter = getattr(c3, 'voice_tools', None)
        try:
            return getter() if callable(getter) else []
        except Exception:  # noqa: BLE001 - the list is informational
            log.warning('voice tool list unavailable')
            return []

    def conflict(message, **extra):
        return web.HTTPConflict(text=json.dumps({'error': message, **extra}), content_type='application/json')

    def unreadable():
        return _service_unavailable(unavailable(
            'instructions_unavailable', 'The profile instructions could not be read or written.',
            'Check the profile workspace under private/c3/brain and the C3 log.'))

    async def run(func, *args):
        try:
            return await asyncio.to_thread(func, *args)
        except instructions.Invalid as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        except instructions.Conflict as exc:
            extra = {'revision': exc.revision} if exc.revision else {}
            raise conflict(str(exc), **extra) from None
        except Exception as exc:  # noqa: BLE001 - storage faults are a 503, never a crash
            log.warning('profile instructions failed (%s)', type(exc).__name__)
            raise unreadable() from None

    def respond(view):
        return web.json_response({**view, 'tools': tools()}, headers={'Cache-Control': 'no-store'})

    async def get(request):
        pinned = active(request)
        if pinned is None:
            return web.json_response({
                'profile': None, 'profile_name': None, 'agent': None, 'skills': [], 'tools': tools(),
                'message': 'No resident brain profile is selected, so Cammy uses no profile notes or '
                           'skills. Select a profile on the Brain tab to edit them.'},
                headers={'Cache-Control': 'no-store'})
        view = await run(instructions.snapshot, pinned)
        if _identity(active(request)) != _identity(pinned):
            raise conflict('the active profile changed; reload the instructions')
        return respond(view)

    async def save(request, func, *key):
        data = await body(request)
        if set(data) != {'profile', 'text', 'revision'}:
            raise web.HTTPBadRequest(text='JSON object with exactly profile, text and revision required')
        if not isinstance(data['profile'], str) or not isinstance(data['text'], str):
            raise web.HTTPBadRequest(text='profile and text: strings required')
        if data['revision'] is not None and not isinstance(data['revision'], str):
            raise web.HTTPBadRequest(text='revision: a string, or null to create')
        pinned = active(request)
        if pinned is None:
            raise conflict('no resident brain profile is selected; nothing was saved')
        if data['profile'] != pinned[0]:
            raise conflict('%r is not the active profile; nothing was saved' % data['profile'],
                           active_profile=pinned[0])

        def still_active():
            return _identity(active(request)) == _identity(pinned)
        view = await run(func, pinned, *key, data['text'], data['revision'], still_active)
        if not still_active():
            raise conflict('saved to %r, but the active profile changed; reload the instructions' % pinned[0],
                           saved=True)
        return respond(view)

    async def put_agent(request):
        return await save(request, instructions.save_agent)

    async def put_skill(request):
        return await save(request, instructions.save_skill, request.match_info['id'])

    app.add_routes([web.get('/v1/instructions', get, allow_head=False),
                    web.put('/v1/instructions/agent', put_agent),
                    web.put('/v1/instructions/skills/{id}', put_skill)])


def _add_brain_routes(app, brain, brain_problem, body, calendar=None):
    def guarded(handler, *, cancellation=False):
        async def handle(request):
            problem = brain_problem()
            # A Brain with a write failure may still own a running model job.
            if brain is None or (problem and not cancellation):
                raise _service_unavailable(problem or unavailable(
                    'initialization_failed', 'Resident Brain is unavailable.', 'Check the C3 log, then restart C3.'))
            try:
                return await handler(request)
            except OSError:
                log.warning('Resident Brain request failed to access storage')
                raise _service_unavailable(brain_problem() or unavailable(
                    'storage_error', 'Resident Brain storage could not be accessed.',
                    'Check private/c3/brain and the service account permissions, then restart C3.')) from None
        return handle

    def field(data, key):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise web.HTTPBadRequest(text=key + ': nonempty string required')
        return value.strip()

    async def change(operation):
        try:
            return await operation
        except (ValueError, KeyError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except RuntimeError as exc:
            raise web.HTTPConflict(text=str(exc)) from exc

    async def state(_):
        return web.json_response(brain.state)

    async def history(request):
        profile = request.query.get('profile')
        if profile is not None:
            profile = field(request.query, 'profile')
        try:
            entries = brain.history(profile)
        except (ValueError, KeyError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        return web.json_response({'entries': entries})

    async def create(request):
        data = await body(request)
        instructions = data.get('instructions', '')
        if not isinstance(instructions, str):
            raise web.HTTPBadRequest(text='instructions: string required')
        await change(brain.create_profile(field(data, 'id'), field(data, 'name'),
                                          field(data, 'provider'), instructions))
        return web.json_response(brain.state)

    async def select(request):
        await change(brain.select_profile(field(await body(request), 'id')))
        return web.json_response(brain.state)

    async def message(request):
        text = field(await body(request), 'text')
        profile = request.match_info.get('profile')
        if profile is not None and profile != brain.state['active_profile']:
            raise web.HTTPConflict(text='the requested brain profile is no longer active')
        desktop = bool(request.get('desktop'))
        outcome = await calendar.route(text, desktop=True) if desktop and calendar is not None else None
        if outcome is None:
            options = {'desktop': True} if desktop else {}
            outcome = await change(brain.submit(text, speak=False, **options))
        extra = {'profile': profile} if profile is not None else {}
        if isinstance(outcome, dict) and outcome.get('capability'):
            # Answered or refused on this PC; the reply carries no calendar data.
            local = {k: outcome.get(k) for k in ('capability', 'status', 'code', 'message', 'range')}
            return web.json_response({'queued': False, 'local': local, **extra})
        return web.json_response({'queued': True, **extra})

    async def cancel(request):
        if await body(request):
            raise web.HTTPBadRequest(text='empty JSON object required')
        await change(brain.cancel())
        problem = brain_problem()
        return web.json_response({'backend': 'resident', 'pending': 0, **problem} if problem else brain.state)

    app.add_routes([web.get('/v1/brain', guarded(state)), web.get('/v1/brain/history', guarded(history)),
                    web.post('/v1/brain/profiles', guarded(create)), web.post('/v1/brain/select', guarded(select)),
                    web.post('/v1/brain/message', guarded(message)),
                    web.post('/v1/brain/profiles/{profile}/message', guarded(message)),
                    web.post('/v1/brain/cancel', guarded(cancel, cancellation=True))])


def _add_provider_routes(app, provider_health, provider_problem, brain_problem, body):
    active = set()
    closing = False

    async def check(operation, *args, **kwargs):
        if closing:
            raise RuntimeError('Provider checks are shutting down')
        task = asyncio.current_task()
        active.add(task)
        try:
            return await operation(*args, **kwargs)
        finally:
            active.remove(task)

    async def stop_checks(_):
        nonlocal closing
        closing = True
        pending = tuple(active)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def status(request):
        refresh = request.query.getall('refresh', [])
        if refresh and refresh not in (['0'], ['1']):
            raise web.HTTPBadRequest(text='refresh: 0 or 1 required')
        if provider_health is None:
            raise _service_unavailable(provider_problem or unavailable(
                'initialization_failed', 'Provider diagnostics are unavailable.',
                'Check the C3 log, then restart C3 and refresh provider status.'))
        try:
            result = await check(provider_health.status, refresh=refresh == ['1'])
        except Exception:
            log.warning('Provider status check failed')
            raise _service_unavailable(unavailable(
                'check_failed', 'Provider status could not be checked.',
                'Check the C3 log and native CLI installation, then refresh provider status.')) from None
        if problem := brain_problem():
            result = {**result, 'brain': problem}
        return web.json_response(result)

    async def test(request):
        data = await body(request)
        provider_id = data.get('id')
        if data.keys() != {'id'} or not isinstance(provider_id, str) or not provider_id.strip():
            raise web.HTTPBadRequest(text='Expected a JSON object with a nonempty provider id')
        if provider_health is None:
            raise _service_unavailable(provider_problem or unavailable(
                'initialization_failed', 'Provider diagnostics are unavailable.',
                'Check the C3 log, then restart C3 and refresh provider status.'))
        try:
            return web.json_response(await check(provider_health.test, provider_id))
        except ValueError:
            raise web.HTTPBadRequest(text='Unknown provider id') from None
        except Exception:
            log.warning('Provider test failed')
            raise _service_unavailable(unavailable(
                'check_failed', 'Provider test could not run.',
                'Check the C3 log and native CLI installation, then retry the test.')) from None

    app.add_routes([web.get('/v1/providers', status, allow_head=False),
                    web.post('/v1/providers/test', test)])
    app.on_shutdown.append(stop_checks)


async def serve(c3, token, port, desktop=None):
    runner = web.AppRunner(make_app(c3, token, desktop), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, '127.0.0.1', port).start()
    except BaseException:
        await runner.cleanup()
        raise
    return runner
