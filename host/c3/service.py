"""Cammy Control Center (C3): the one PC service between agents and Cammy.

    .venv-live/bin/python -m c3            (from host/, or PYTHONPATH=host)

Runs until stopped (SIGINT/SIGTERM, or systemd). Parts, each restarted with backoff if it
fails (only the API port and missing credentials stop the service):

  body      lease, preview, mic, speaker, face, eyes          c3/body.py
  hear      mic -> Echo -> Listener -> Voice                  c3/echo.py, c3/listener.py
  voice     Gemini Live, asleep when idle, resumes            c3/voice.py
  brain     resident profiles and explicit requests; T3 transcript sync in legacy mode
  api       127.0.0.1:8792 for T3, MCP and the panel           c3/api.py
  family    kids/adult mode from the face pack, Calendar access PIN, local play   c3/family.py, c3/local_play.py

Deployments (--deployment): 'family' (default) never uses the Gemini Developer API: no key
is read, no client is made and no microphone, text or vision goes to it, in Kids or Adult
mode; it runs device controls and local play, plus the resident Brain and the other adult
routes while Cammy shows the Cammy face pack and CCC has no Kids override. 'adult-standalone' is the
Gemini Live service above, for adult-only use, and must be chosen explicitly.

Duplex: 'half' ignores the mic while Cammy speaks (plus the tail). 'auto' allows barge-in
for a reply once Echo has converged on it. 'full' allows it whenever Echo is running.
The default is 'half' until the echo calibration on Cammy has passed.

Data (git-ignored): private/c3/ holds api-token, logs/c3.log (5 MB x 3) and transcript/.
"""
import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import socket
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit

from google import genai
from google.genai import types

from . import api, body as bodymod, family as familymod, instructions, local_play, moves as movesmod, voice as voicemod
from .transcript import Transcript
from .body import ROOT

import cammy_brain  # noqa: E402  (host/ is on sys.path via c3.body)
import cammy_capture  # noqa: E402
import cammy_persona  # noqa: E402
import cammy_voicefx  # noqa: E402
from cammy_vision import VisionError  # noqa: E402

log = logging.getLogger('c3')
DATA = ROOT / 'private/c3'
FAMILY_POLL_S = 2.0          # face-pack reads that keep the family mode on Cammy's face

RESIDENT_BRAIN_PERSONA = (
    " You can pass a request to Adam's selected brain profile with ask_brain when he explicitly "
    "asks you to consult it. Keep ordinary conversation and device commands here. If the request "
    "is accepted, acknowledge it briefly and wait for the result. If the brain is unavailable, "
    "say so and continue helping. A message starting with [Brain] is a result Adam requested "
    "aloud. Tell him what it says briefly, in your own voice.")
RESIDENT_ASK_BRAIN = voicemod.ASK_BRAIN.model_copy(update={
    'description': "Send Adam's explicit request to his selected resident brain profile. "
                   "The answer arrives later as a [Brain] message.",
})


def _plain(value, depth=0):
    """JSON-safe copy of a status dict: scalars and nested dicts only, so no frame bytes leak."""
    return {str(k): (_plain(v, depth + 1) if isinstance(v, dict) else v) for k, v in value.items()
            if isinstance(v, (int, float, str, bool, type(None))) or (isinstance(v, dict) and depth < 2)}


class ResidentVoice(voicemod.Voice):
    def tools(self):
        return [RESIDENT_ASK_BRAIN if tool is voicemod.ASK_BRAIN else tool
                for tool in super().tools()]


class _InvalidBrainStorage(ValueError):
    """The saved profiles cannot safely be loaded."""


def _load_resident_brain(root):
    from .resident_brain import ResidentBrain, _MAX_TEXT, _PROVIDERS, _profile_id, _read_json, _session, _text

    def exists_as(path, expected):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return False
        if not expected(mode):
            raise _InvalidBrainStorage()
        return True

    class CheckedResidentBrain(ResidentBrain):
        def _load(self):
            # ResidentBrain has the exclusive lock here. Check all saved state
            # before its normal load can mark interrupted turns or trim history.
            try:
                self._validate_storage()
            except (ValueError, KeyError, TypeError) as exc:
                raise _InvalidBrainStorage() from exc
            super()._load()

        def _validate_storage(self):
            profiles = set()
            if exists_as(self._profiles_dir, stat.S_ISDIR):
                for path in self._profiles_dir.iterdir():
                    # Non-directory entries are ignored by the normal loader.
                    if path.is_symlink():
                        raise _InvalidBrainStorage()
                    if not path.is_dir():
                        continue
                    manifest = path / 'profile.json'
                    work = path / 'work'
                    has_work = exists_as(work, stat.S_ISDIR)
                    if not exists_as(manifest, stat.S_ISREG):
                        # An interrupted create can leave an empty workspace.
                        # Never attach orphaned working files to a new identity.
                        if has_work and next(work.iterdir(), None) is not None:
                            raise _InvalidBrainStorage()
                        continue
                    profile = _read_json(manifest)
                    profile_id = _profile_id(path.name)
                    if profile['id'] != profile_id or profile['provider'] not in _PROVIDERS:
                        raise _InvalidBrainStorage()
                    _text(profile['name'], 'profile name', 128)
                    _text(profile['instructions'], 'profile instructions', _MAX_TEXT, empty=True)
                    if profile['session_id'] is not None:
                        _session(profile['session_id'])
                    if not isinstance(profile['history'], list):
                        raise _InvalidBrainStorage()
                    for turn in profile['history']:
                        if (turn['role'] not in ('user', 'assistant')
                                or turn['status'] not in ('pending', 'interrupted', 'completed', 'failed', 'cancelled')):
                            raise _InvalidBrainStorage()
                        _text(turn['id'], 'turn id', 64)
                        _text(turn['text'], 'turn text', _MAX_TEXT)
                    profiles.add(profile_id)
            if exists_as(self._selection, stat.S_ISREG):
                if _profile_id(_read_json(self._selection)['active_profile']) not in profiles:
                    raise _InvalidBrainStorage()

    if exists_as(root, stat.S_ISDIR):
        exists_as(root / 'lock', stat.S_ISREG)
    return CheckedResidentBrain(root)


_BRAIN_FAILURES = {
    'storage_busy': ('Resident Brain storage is in use.',
                     'Stop the other Brain owner, then restart C3.'),
    'storage_invalid': ('Resident Brain storage is invalid.',
                        'Back up private/c3/brain and repair or restore its saved state before restarting C3.'),
    'storage_error': ('Resident Brain storage could not be accessed.',
                      'Check the mount and service account permissions for private/c3/brain, then restart C3.'),
    'initialization_failed': ('Resident Brain could not initialize.',
                              'Check the C3 log and fix the startup failure, then restart C3.'),
}


def credentials(cloud=True):
    """(portal token, Gemini key). The family deployment passes cloud=False: the key is not read."""
    token_file = json.loads((ROOT / 'private/host/config.json').read_text())['token_file']
    from pathlib import Path
    token = Path(token_file).read_text().strip()
    return token, (ROOT / 'private/credentials/gemini.key').read_text().strip() if cloud else None


def setup_logging(folder, verbose=False):
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fmt = logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s', '%H:%M:%S')
    handlers = [logging.handlers.RotatingFileHandler(folder / 'c3.log', maxBytes=5 << 20, backupCount=3),
                logging.StreamHandler()]
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    for noisy in ('google_genai', 'websockets', 'httpx', 'aiohttp.access'):
        logging.getLogger(noisy).setLevel(logging.WARNING)


PORTAL_LOST_S = 60      # camera unreachable this long: look for it at another address
PORTAL_WATCH_S = 15


async def portal_watch(body, current, *, sweep=None, restart=None, clock=time.monotonic):
    """Cammy gets its address by DHCP. After PORTAL_LOST_S without the body up, sweep the
    subnet; if the portal answers elsewhere, save that and restart C3 on it (systemd's
    Restart=always brings it back in 10 s). Only when --portal was not given."""
    from . import discover
    sweep = sweep or discover.sweep
    restart = restart or (lambda: os.kill(os.getpid(), signal.SIGTERM))
    down_since = None
    while True:
        await asyncio.sleep(PORTAL_WATCH_S)
        if body.up.is_set():
            down_since = None
            continue
        now = clock()
        down_since = down_since or now
        if now - down_since < PORTAL_LOST_S:
            continue
        found = await asyncio.to_thread(sweep, None, urlsplit(current).hostname)
        down_since = now                     # sweep at most once a minute
        if found and found != current:
            log.warning('Cammy moved to %s (was %s): restarting C3 on the new address', found, current)
            discover.remember(found)
            restart()
            return


async def supervise(name, factory, stats):
    """Run factory() forever; a crash is logged and the part restarted (1..30 s backoff)."""
    failures = 0
    while True:
        started = time.monotonic()
        try:
            await factory()
            log.warning('%s returned; restarting', name)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception('%s crashed', name)
        stats[name] = stats.get(name, 0) + 1
        failures = 0 if time.monotonic() - started > 300 else failures + 1
        await asyncio.sleep(min(30, 2 ** failures))


def voice_health(body, listener, voice, speaker):
    failing, degraded = [], []
    if not body['up']:
        failing.append('camera unavailable')
    elif body.get('capture_muted'):
        degraded.append('microphone muted')
    elif body['mic_chunk_age_s'] is None or body['mic_chunk_age_s'] > 5:
        degraded.append('microphone chunks missing')
    if error := voice.get('last_error'):
        reasons = failing if error['kind'] == 'fatal' else degraded
        reasons.append('Gemini: ' + error['detail'])
    if reason := speaker.get('failure_reason'):
        reasons = failing if speaker.get('state') == 'failing' else degraded
        reasons.append('speaker: ' + reason)
    if voice.get('reply') in ('timed-out', 'completed-without-audio', 'stalled', 'discarded', 'connection-ended'):
        degraded.append('last reply ' + voice['reply'].replace('-', ' '))

    pcm = voice.get('pcm_age_s')
    finished = speaker.get('last_finished_age_s')
    reply_window = voicemod.REPLY_STALL_S + 5
    if (pcm is not None and reply_window < pcm < 120 and (finished is None or finished > pcm)
            and not speaker.get('active')):
        degraded.append('recent model audio without finished playback')

    probe = voice.get('canary')
    probe_age = time.time() - probe['checked_at'] if probe and probe['epoch'] == body['epoch'] else None
    if probe_age is not None and probe_age <= voicemod.CANARY_INTERVAL_S + 60:
        for reason in probe['reasons']:
            observed = pcm if reason.startswith('Gemini') else finished
            if observed is None or observed >= probe_age:
                degraded.append(reason)
    elif pcm is None or pcm > voicemod.CANARY_INTERVAL_S:
        degraded.append('awaiting model audio or silent check')
    return ('failing' if failing else 'degraded' if degraded else 'ok'), list(dict.fromkeys(failing + degraded))


class C3:
    def __init__(self, args):
        self.args = args
        self.duplex = args.duplex
        self.data_dir = DATA
        self.restarts = {}
        self.started = time.time()
        # Fail closed: a caller that names no deployment gets the family one (no cloud).
        self.deployment = getattr(args, 'deployment', familymod.FAMILY)
        self.cloud = self.deployment == familymod.STANDALONE
        if not self.cloud and args.brain == 't3':
            raise ValueError('the family deployment supports --brain resident or off')
        self.family = familymod.FamilyPolicy(DATA / 'family', deployment=self.deployment)
        self.calendar_access = familymod.CalendarAccess(
            familymod.PinStore(DATA / 'family'), self.family, profile=self._calendar_profile_key,
            access_s=60 * float(getattr(args, 'calendar_access_minutes', familymod.CALENDAR_S / 60)))
        self.play = None             # family: made in run()
        self.local_voice = None
        self._teardowns = set()
        token, key = credentials(cloud=self.cloud)
        transcript = Transcript(DATA / 'transcript')
        self.transcript = transcript if self.cloud else familymod.GatedTranscript(
            transcript, lambda: self.family.allows('transcript'))
        from .memory import Memory
        self.memory = Memory(DATA / 'memory')
        self.echo = self._echo() if args.duplex != 'off' else None
        from .listener import Listener
        self.listener = Listener()
        from .loopwatch import LoopWatch
        self.loopwatch = LoopWatch()
        from .gate import TalkGate
        from .spotter import Spotter
        mode = getattr(args, 'talk_gate', 'open')
        self.gate = TalkGate(self._to_voice, spotter=Spotter() if mode != 'open' else None, busy=self._voice_busy,
                             mode=mode, near_dbfs=getattr(args, 'near_dbfs', None) or -38.0,
                             names=lambda: self._pack_names())
        self.speaker = None          # made in run(): needs the loop for Echo hand-off
        self.brain = None
        self.brain_backend = None
        self.provider_health = None
        self._face_pack = cammy_persona.DEFAULT_PACK
        self._brain_failure = None
        self.provider_problem = None
        if args.brain == 't3':
            thread = cammy_brain.thread_id()
            if thread:
                self.brain = cammy_brain.Brain(thread, idle_poll_s=10.0)
                self.brain_backend = 't3'
        self.client = genai.Client(api_key=key, http_options={'api_version': 'v1alpha'}) if self.cloud else None
        try:
            native = cammy_capture.Native(args.portal)
        except VisionError as exc:
            native = None
            log.warning('capture off: %s', exc)
        self.native = native
        self.body = bodymod.Body(args.portal, token, label=self.label(), stock=native)
        self.face = bodymod.Face(self.body)
        self.eyes = bodymod.Eyes(self.body)
        self.moves = movesmod.Moves(self.body, self.face, self.eyes, sweep_s=args.sweep, stock=native)
        self.camera = None
        self.voice = None
        self.brain_mark = 0
        self.inject = bytearray()    # test speech mixed into Cammy's mic (API /v1/inject)
        if args.brain == 'resident':
            self.brain_backend = 'resident'
            self._init_resident()
        self._configure_eyes_history()
        self.calendar = None
        if self.brain_backend == 'resident' and self.brain is not None:
            self._init_calendar()
            self._init_local_router()

    def active_profile(self):
        """The selected profile for camera and agent access, if the camera mode allows it."""
        if not self.family.allows('look_back'):
            return None
        return self._resident_profile()

    def _calendar_profile_key(self):
        """(profile id, selection generation) of the selected resident profile, or None: a
        reselection of the same profile is a new key, so Calendar access closes on it."""
        profile = self._resident_profile()
        if not profile:
            return None
        return profile[0], getattr(self.brain, 'generation', None)

    def selected_profile(self):
        """The selected profile for local desktop management in either camera mode."""
        return self._resident_profile()

    def _resident_profile(self):
        """(profile id, profile name, workdir) of the selected resident profile, or None.

        Resident truth only: the Brain's own selection and profile table, and the workdir it made
        under DATA/brain/profiles/<id>/work. Never a path from a caller, so camera history can
        only ever live in the selected profile's workspace, and none without one."""
        brain = self.brain
        if (self.brain_backend != 'resident' or brain is None or self.brain_problem()
                or getattr(brain, '_closing', None) is not None):
            return None
        profile_id = getattr(brain, '_active', None)
        profile = getattr(brain, '_profiles', {}).get(profile_id) if isinstance(profile_id, str) else None
        if not isinstance(profile, dict) or profile.get('id') != profile_id:
            return None
        try:
            from .resident_brain import _profile_id
            profile_id = _profile_id(profile_id)
        except (ImportError, ValueError):
            return None
        profiles = (self.data_dir / 'brain' / 'profiles').absolute()
        workdir = profiles / profile_id / 'work'
        if workdir.parent.parent != profiles or Path(getattr(brain, 'root', self.data_dir / 'brain')).absolute() != profiles.parent:
            return None
        return profile_id, str(profile.get('name') or profile_id), workdir

    def _configure_eyes_history(self):
        """Keep desktop history readable and stop recording during Kids mode."""
        configure = getattr(self.eyes, 'configure_profile_history', None)
        if callable(configure):
            self.eyes.history_recording = lambda: self.family.allows('look_back')
            configure(self.selected_profile)
        else:
            log.warning('Eyes has no profile history; camera history stays as Eyes keeps it')

    def _init_resident(self):
        try:
            self.brain = _load_resident_brain(self.data_dir / 'brain')
        except Exception as exc:
            if isinstance(exc, _InvalidBrainStorage):
                code = 'storage_invalid'
            elif isinstance(exc, RuntimeError) and str(exc) == 'resident brain root is already in use':
                code = 'storage_busy'
            elif isinstance(exc, OSError):
                code = 'storage_error'
            else:
                code = 'initialization_failed'
            self._brain_failure = code
            log.warning('Resident Brain unavailable (%s, %s)', code, type(exc).__name__)

        try:
            from .provider_health import ProviderHealth
            self.provider_health = ProviderHealth(self.data_dir)
        except Exception as exc:
            self.provider_problem = api.unavailable(
                'initialization_failed', 'Provider diagnostics could not initialize.',
                'Check the C3 log, then restart C3 and refresh provider status.')
            log.warning('Provider diagnostics unavailable (%s)', type(exc).__name__)

    def _init_calendar(self):
        """Route Calendar requests locally before the resident model sees them."""
        try:
            from . import calendar
            self.calendar_panel_token = calendar.panel_token(DATA)
            self.calendar = calendar.CalendarService(self)
        except Exception as exc:  # noqa: BLE001
            log.warning('Calendar unavailable (%s)', type(exc).__name__)

    def _init_local_router(self):
        from .os_router import OsRouter, compose

        os_router = OsRouter(self)

        async def route(text, *, speak):
            return await compose(os_router, self.calendar, text, speak=speak)

        self.brain.local_router = route

    def brain_problem(self):
        code = self._brain_failure
        if self.brain is not None and self.brain._storage_failed:
            code = 'storage_error'
        return api.unavailable(code, *_BRAIN_FAILURES[code]) if code else None

    def label(self):
        text = ('C3, brain %s' % self.brain.thread if self.brain_backend == 't3'
                else 'C3 on %s' % socket.gethostname())
        return ''.join(c for c in text if 32 <= ord(c) < 127 and c not in '"\\')[:80]

    def _echo(self):
        try:
            from .echo import Echo
            echo = Echo()
            log.info('echo canceller: %s', echo.state.get('backend'))
            return echo
        except Exception as exc:  # noqa: BLE001 - half duplex still works without it
            log.warning('echo canceller unavailable (%s); half duplex only', exc)
            return None

    def persona(self):
        # The body's newest /v1/state reply (about 2 s old while up), not a round trip per
        # wake. Without one (a camera outage) the selected pack stays as it was.
        cached = getattr(self.body, 'last_state', None)
        if cached is not None:
            state = cached[1]
            self._face_pack, _ = cammy_persona.pack(state.get('face_pack_shown', state.get('face_pack')))
        text = cammy_persona.persona(getattr(self, '_face_pack', cammy_persona.DEFAULT_PACK))
        if self.brain_backend == 'resident' and self.brain is not None:
            text += RESIDENT_BRAIN_PERSONA
        elif self.brain_backend == 't3':
            text += voicemod.BRAIN_PERSONA
            brief = cammy_brain.brief()
            if brief:
                text += ' What your brain wants you to know right now: ' + brief
        return text + self.profile_guidance()

    def profile_guidance(self):
        """The selected profile's AGENT.md and skills for the Gemini voice only ('' without a
        profile). Part of the persona base, so a change drops the resume handle at the next
        connection (c3/voice.py) and never touches a live one."""
        try:
            profile = self.active_profile()
        except Exception:  # noqa: BLE001 - a brain blip must not stop the voice connecting
            log.warning('active profile unavailable for the voice instructions')
            return ''
        return instructions.compose(profile)

    def voice_tools(self):
        """[{name, description}] of the fixed tools Gemini gets (code-defined, not editable)."""
        return voicemod.tool_catalog(self.voice.tools()) if self.voice is not None else []

    def eyes_status(self):
        """Eyes plus its profile-gated frame history on disk (counts and times only, never images)."""
        eyes = {'restarts': getattr(self.eyes, 'restarts', 0)}
        if hasattr(self.eyes, 'wanted'):
            eyes['wanted'] = bool(self.eyes.wanted)
        history = getattr(self.eyes, 'history_status', None)
        try:
            history = history() if callable(history) else history
        except Exception as e:  # noqa: BLE001 - status never fails because of Eyes
            history = {'error': str(e)[:80]}
        if isinstance(history, dict):
            eyes['history'] = _plain(history)
            owner = history.get('profile')
            owner = owner.get('id') if isinstance(owner, dict) else owner
            getter = getattr(self, 'active_profile', None)
            active = getter() if callable(getter) else None
            if active is not None and owner == active[0]:
                eyes['history']['profile_name'] = active[1]
        return eyes

    def status(self, *, desktop=False):
        out = self._status()
        out['deployment'] = self.deployment
        out['family'] = self.family.snapshot()
        if self.play is not None:
            out['play'] = self.play.state()
        history = (out.get('eyes') or {}).get('history')
        if isinstance(history, dict):
            history['recording_allowed'] = self.family.allows('look_back')
        if not desktop and not self.family.allows('outward'):     # kids: device health only
            for key in ('brain', 'memory', 'transcript_entries'):
                out.pop(key, None)
            if isinstance(out.get('eyes'), dict):
                out['eyes'].pop('history', None)
        return out

    def _status(self):
        speaker = self.speaker
        now = time.monotonic()

        def age(stamp):
            return round(max(0, now - stamp), 1) if isinstance(stamp, (int, float)) else None

        body = dict(self.body.state, up=self.body.up.is_set(), epoch=self.body.epoch,
                    mic_chunk_age_s=age(getattr(self.body, 'last_mic_chunk', None)))
        listener = dict(self.listener.stats, speaking=self.listener.speaking,
                        last_heard_age_s=age(getattr(self.listener, 'last_heard', None)))
        voice = self.voice.status() if self.voice else None
        playback = {
            'active': speaker.active, 'chunks': speaker.chunks, 'seconds': round(speaker.seconds, 1),
            'last_outcome': speaker.last_outcome, 'failure_reason': speaker.failure_reason,
            'state': 'failing' if speaker.failing else 'playing' if speaker.active else 'ready',
            'consecutive_failures': speaker.consecutive_failures, 'lease_recycles': speaker.lease_recycles,
            'last_finished_age_s': age(speaker.last_finished),
        } if speaker else None
        brain = None
        if self.brain_backend == 'resident':
            brain = ({'backend': 'resident', **problem} if (problem := self.brain_problem())
                     else self.brain.state)
        elif self.brain_backend == 't3':
            brain = {'thread': self.brain.thread, 'pending': self.brain.pending}
        out = {
            'service': 'Cammy Control Center', 'uptime_s': round(time.time() - self.started),
            'duplex': self.duplex, 'body': body, 'voice': voice, 'listener': listener,
            'talk_gate': self.gate.state if getattr(self, 'gate', None) else None,
            'loop': self.loopwatch.state if getattr(self, 'loopwatch', None) else None,
            'echo': self.echo.state if self.echo else None,
            'speaker': playback,
            'eyes': self.eyes_status(),
            'moves': self.moves.state,
            'capture': {'available': bool(self.camera), 'busy': bool(self.camera and self.camera.busy),
                        'blocked': self.camera.capture.blocked() if self.camera else None},
            'brain': brain,
            'memory': dict(self.memory.stats, facts=self._fact_count()),
            'restarts': self.restarts, 'transcript_entries': self.transcript.count,
        }
        if voice is not None:
            out['voice_health'], out['voice_health_reasons'] = voice_health(body, listener, voice, playback or {})
        return out

    def _fact_count(self):
        """len(memory.facts()), re-read only when facts.jsonl changes (status runs often)."""
        try:
            st = self.memory.path.stat()
            key = (st.st_mtime_ns, st.st_size, st.st_ino)
        except (AttributeError, OSError):
            key = None
        cached = getattr(self, '_facts_cache', None)
        if key is None or cached is None or cached[0] != key:
            cached = self._facts_cache = (key, len(self.memory.facts()))
        return cached[1]

    # ---- the hearing path: mic -> Echo -> Listener -> Voice ---------------------------
    def full_duplex_now(self):
        if not self.echo or self.duplex == 'half':
            return False
        state = self.echo.state
        return self.duplex == 'full' or bool(state.get('converged'))

    def mic_closed(self):
        """Half duplex: no new utterance while Cammy speaks, nor while Gemini is still sending
        a reply that has not reached the speaker yet. On 2026-11-16 room noise in that gap
        counted as a barge-in and wiped the reply before a word was played."""
        return self.speaker.muted() or bool(self.voice and self.voice.in_turn)

    def _mix(self, chunk):
        """Adds queued test speech to a mic chunk, as if it reached Cammy's microphone."""
        import numpy as np
        n = min(len(chunk), len(self.inject))
        add = np.frombuffer(bytes(self.inject[:n]), '<i2').astype(np.int32)
        del self.inject[:n]
        mic = np.frombuffer(chunk, '<i2').astype(np.int32)
        mic[:len(add)] += add
        return np.clip(mic, -32768, 32767).astype('<i2').tobytes()

    async def echo_check(self, pcm24, seconds):
        """Plays a 24 kHz clip as if it were a reply and follows Echo while it plays."""
        if not self.echo:
            return {'error': 'no echo canceller'}
        if self.speaker.active or self.voice.state != 'asleep':
            return {'error': 'Cammy is busy; put Voice to sleep first'}
        t0 = time.monotonic()
        self.speaker.say(pcm24, end=True)
        trace, converged_at = [], None
        while time.monotonic() - t0 < seconds + 3:
            await asyncio.sleep(0.25)
            st = self.echo.state
            trace.append((round(time.monotonic() - t0, 2), st.get('delay_ms'), st.get('erle_db'), st.get('converged'), st.get('echo_active')))
            if st.get('converged') and converged_at is None:
                converged_at = round(time.monotonic() - t0, 2)
        erles = [t[2] for t in trace if t[2] is not None and t[4]]
        result = {'backend': self.echo.state.get('backend'), 'delay_ms': self.echo.state.get('delay_ms'),
                  'delay_confident': self.echo.state.get('delay_confident'), 'converged_after_s': converged_at,
                  'erle_db_median': round(sorted(erles)[len(erles) // 2], 1) if erles else None,
                  'erle_db_max': round(max(erles), 1) if erles else None, 'trace': trace}
        log.info('echo check: %s', json.dumps({k: v for k, v in result.items() if k != 'trace'}))
        return result

    async def playback_check(self, pieces=(1.0, 2.0, 2.0), gap_s=0.15):
        """Plays a 600 Hz tone the way a reply arrives (one say() per piece, gap_s apart),
        records Cammy's own mic meanwhile, and reports silent stretches inside the playback
        (2026-11-16: Adam heard replies stutter). The recording stays in private/c3/debug."""
        import numpy as np
        import wave
        if self.speaker.active or self.voice.state != 'asleep':
            return {'error': 'Cammy is busy; put Voice to sleep first'}
        total = sum(pieces)
        t = np.arange(int(24000 * total)) / 24000
        tone = (np.sin(2 * np.pi * 600 * t) * 6000).astype('<i2').tobytes()
        self.mic_tap = bytearray()
        t0 = time.monotonic()
        at = 0
        for i, p in enumerate(pieces):
            n = int(24000 * p) * 2
            self.speaker.say(tone[at:at + n], end=i == len(pieces) - 1)
            at += n
            await asyncio.sleep(gap_s)
        await asyncio.sleep(total + 3.0)
        rec, self.mic_tap = bytes(self.mic_tap), None
        folder = DATA / 'debug'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / ('playback-%s.wav' % time.strftime('%H%M%S'))
        with wave.open(str(path), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(rec)
        x = np.frombuffer(rec, '<i2').astype(np.float64)
        f = 320                                                   # 20 ms frames
        rms = np.sqrt(np.mean(x[:len(x) // f * f].reshape(-1, f) ** 2, axis=1)) if len(x) >= f else np.zeros(0)
        loud = rms > max(200.0, np.percentile(rms, 95) * 0.25) if len(rms) else rms
        idx = np.where(loud)[0]
        gaps = []
        if len(idx):
            run = 0
            for k in range(idx[0], idx[-1] + 1):
                if not loud[k]:
                    run += 1
                elif run:
                    if run >= 3:
                        gaps.append({'at_s': round((k - run) * 0.02, 2), 'ms': run * 20})
                    run = 0
        result = {'pieces': list(pieces), 'tone_s': total, 'recorded_s': round(len(x) / 16000, 2),
                  'sound_s': round((idx[-1] - idx[0] + 1) * 0.02, 2) if len(idx) else 0,
                  'starts_at_s': round(idx[0] * 0.02, 2) if len(idx) else None, 'gaps': gaps,
                  'wall_s': round(time.monotonic() - t0, 2), 'file': str(path)}
        log.info('playback check: %s', json.dumps({k: v for k, v in result.items() if k != 'file'}))
        return result

    def _to_voice(self, event):
        if self.voice is not None:
            self.voice.on_speech(event)

    def _voice_busy(self):
        """Cammy is talking or a reply is on its way: the conversation is open."""
        v = self.voice
        return v is not None and (v.in_turn or v.reply_due is not None
                                  or bool(self.speaker is not None and self.speaker.active))

    def _pack_names(self):
        _, p = cammy_persona.pack(getattr(self, '_face_pack', cammy_persona.DEFAULT_PACK))
        return [w for w in str(p.get('name', '')).replace(' and ', ' ').split() if len(w) >= 3]

    def _reset_hearing(self):
        events = self.listener.reset()
        if self.voice is not None:
            if not events and (self.voice.utterance_open or not self.voice.speech.empty()):
                from .listener import Event
                events = [Event('end', reason='reset')]
            gate = getattr(self, 'gate', None)
            for event in events:
                (gate.feed if gate is not None else self.voice.on_speech)(event)

    async def hear(self):
        capture = getattr(self.body, 'capture', (0, True))
        async for chunk in self.body.mic():
            latest = getattr(self.body, 'capture', (0, True))
            if latest != capture:
                self._reset_hearing()
                capture = latest
            if not capture[1]:
                continue
            if getattr(self, 'mic_tap', None) is not None:
                self.mic_tap.extend(chunk)
            if self.inject:
                chunk = self._mix(chunk)
            clean = self.echo.process(chunk) if self.echo else chunk
            if self.moves.sweeping:
                events = self.listener.feed(clean, playing=True)
            elif self.full_duplex_now():
                events = self.listener.feed(clean, strict=bool(self.echo.state.get('echo_active')))
            else:
                events = self.listener.feed(clean, playing=self.mic_closed())
            gate = getattr(self, 'gate', None)
            if self.voice is not None and gate is not None:
                gate.tick()
            for event in events:
                if self.voice is not None:
                    (gate.feed if gate is not None else self.voice.on_speech)(event)
                elif self.local_voice is not None and self.play is not None:
                    self.local_voice.on_speech(event, self.play, self.calendar)   # family: stays on this PC

    # ---- brain -----------------------------------------------------------------------
    async def brain_in(self):
        while True:
            text = await self.brain.replies.get()
            self.transcript.add('brain', text)
            self.voice.add_turn([types.Part(text='[Brain] ' + text)], 'brain')

    async def brain_out(self):
        """New transcript lines go to the brain thread every 10 minutes, or when Voice
        falls asleep, so its memory keeps up and nothing waits for the service to exit."""
        last = time.monotonic()
        while True:
            await asyncio.sleep(5)
            due = time.monotonic() - last > 600 or (self.voice.state == 'asleep' and time.monotonic() - last > 30)
            if not due:
                continue
            last = time.monotonic()
            await self.sync_brain()

    async def sync_brain(self):
        if self.brain_backend != 't3':
            return
        lines, mark = self.transcript.since(self.brain_mark)
        if not any(l.startswith(('Adam:', 'Cammy:')) for l in lines):
            self.brain_mark = mark
            return
        try:
            await asyncio.wait_for(self.brain.transcript(lines), 30)
            self.brain_mark = mark
        except Exception as exc:  # noqa: BLE001 - kept for the next try
            log.warning('transcript to brain failed: %s', exc)

    # ---- family -----------------------------------------------------------------------

    async def read_device_state(self):
        """What Cammy reports about its face, from one camera response, or None (offline,
        another camera or boot, or unreadable). Ownership of the control or face lease is not
        part of it: the lease is transport, and the family mode never needs it."""
        epoch = self.body.epoch
        if not self.body._current(epoch):
            return None
        cammy = self.body.cammy
        try:
            state = await asyncio.to_thread(self._family_face_state, cammy)
        except Exception:  # noqa: BLE001 - offline
            return None
        if not self.body._current(epoch) or not isinstance(state, dict):
            return None
        if state.get('boot_id') != cammy._boot_binding:
            return None
        return dict(state, connection_epoch=epoch)

    @staticmethod
    def _family_face_state(cammy):
        try:
            reply = cammy.face_pack_status()
        except bodymod.CammyError as exc:
            if exc.status not in (404, 501):
                raise
            state = cammy.state()            # older firmware: no CCC pack, no pack control
            shown = state.get('face_pack_shown', state.get('face_pack'))
            return {'boot_id': state.get('boot_id'), 'version': state.get('version'),
                    'face_pack': shown if isinstance(shown, str) else None,
                    'face_pack_select': False}
        pack, link = reply['pack'], reply['link']
        shown = pack.get('shown')
        grant = pack.get('ccc_grant') if isinstance(pack.get('ccc_grant'), dict) else {}
        return {
            'boot_id': reply['boot_id'], 'version': reply['version'],
            'face_live': pack.get('live') is True,
            'face_pack': shown if isinstance(shown, str) else None,
            'face_grant_end': grant.get('last_end') if isinstance(grant.get('last_end'), str) else None,
            'face_pack_select': pack.get('ccc_available') is True and link.get('face_lease') is True,
        }

    async def family_watch(self):
        """Keeps the family mode on the face pack Cammy reports."""
        while True:
            await self.family_refresh_access()
            await asyncio.sleep(FAMILY_POLL_S)

    async def family_refresh_access(self):
        self.family.observe(await self.read_device_state())

    def _on_family_change(self, event):
        task = asyncio.ensure_future(self.family_teardown(event))
        self._teardowns.add(task)
        task.add_done_callback(self._teardowns.discard)

    async def family_teardown(self, event):
        """After any kids/adult change: drop queued test audio, end the game and its speech,
        cancel a running Brain turn (its result is not kept for speaking) and empty the reply
        queue. Look Back and the adult routes are already closed by the policy."""
        self.inject.clear()
        self.mic_tap = None
        if self.play is not None:
            self.play.reset(str(event.get('reason', '')))
        elif self.speaker is not None:
            self.speaker.flush()
        brain = self.brain
        job = getattr(brain, '_job', None)
        if self.brain_backend == 'resident' and job is not None and not getattr(job, 'desktop', False):
            try:
                await brain.cancel()
            except Exception as exc:  # noqa: BLE001 - the generation check still withholds it
                log.warning('family: Brain cancel failed (%s)', type(exc).__name__)
        replies = getattr(brain, 'replies', None)
        while replies is not None and not replies.empty():
            replies.get_nowait()

    # ---- Calendar access (the calendar service asks these) ----------------------------
    def calendar_access_allows(self, profile_id: str) -> bool:
        """True only while Calendar access is open for this resident profile: Adult mode plus
        the Calendar access PIN. Everything else, including any error, is False."""
        try:
            return self.calendar_access.allows(profile_id) is True
        except Exception:  # noqa: BLE001
            return False

    def calendar_access_grant(self, profile_id: str):
        """The generation to quote to calendar_access_current() before committing account
        work, or None while access is closed."""
        try:
            return self.calendar_access.grant(profile_id)
        except Exception:  # noqa: BLE001
            return None

    def calendar_access_current(self, profile_id: str, generation) -> bool:
        try:
            return self.calendar_access.current(profile_id, generation) is True
        except Exception:  # noqa: BLE001
            return False

    async def _face_pack_applier(self):
        """A portal call that changes the face pack, if this firmware advertises one."""
        state = await self.read_device_state()
        if not (isinstance(state, dict) and state.get('face_pack_select') is True):
            return None

        async def apply(pack_id):
            if pack_id in familymod.KIDS_PACKS:
                raise familymod.FamilyError('physical_pack', 'Choose Pikachu or Fin and Fur on Cammy.', status=409)
            try:
                await asyncio.to_thread(self.body.cammy.face_pack, pack_id)
            except bodymod.CammyError as exc:
                raise familymod.FamilyError('face_selection_failed', 'Cammy did not accept the face change.',
                                            status=409 if exc.status == 409 else 503) from None
        return apply

    async def select_face_pack(self, pack_id):
        """Change the face from CCC (a Kids choice closes Adult first) and wait for the read-back."""
        return await familymod.select_face_pack(
            self.family, pack_id, apply=await self._face_pack_applier(), read_state=self.read_device_state,
            teardown=self.family_teardown)

    # ---- run -------------------------------------------------------------------------
    def _clear_capture_halt(self):
        """A rebooted camera starts in its own stock mode, so an unconfirmed restore
        from the previous boot no longer blocks media preparation and the lease. It also
        boots showing the face: a live camera view asked for on the previous boot (Gemini's
        show_camera tool, 05:36 on 2026-11-24) kept the face hidden after a reboot."""
        face = getattr(self, 'face', None)
        if face is not None and face.views & {'camera', 'follow'}:
            face.set_view('camera', False)
            face.set_view('follow', False)
            log.info('camera rebooted: the screen shows the face again')
        capture = self.camera.capture if self.camera else None
        if capture is not None and capture.restore_uncertain and not self.camera.busy:
            capture.restore_uncertain = False
            # The halt also kept the face hidden (capture skips show_face() when the restore
            # is uncertain). Without this the face stayed off after every reboot until C3
            # restarted (2026-11-23, 0.9.8 and 0.9.9 boots).
            if face is not None:
                face.show_face()
            log.warning('camera rebooted: cleared the capture halt from the previous boot')

    async def run(self):
        loop = asyncio.get_running_loop()
        runner = None
        tasks = []
        try:
            self.speaker = bodymod.Speaker(self.body, self.args.level, self.args.tail, echo=self.echo, loop=loop, moves=self.moves)
            if self.native and self.cloud:     # captures are turns for Gemini
                self.camera = voicemod.Camera(cammy_capture.Capture(self.native, self.face),
                                              done=lambda r: self.voice.camera_done(r), moves=self.moves)
                self.moves.capture_busy = lambda: self.camera.busy      # no motion during a capture
                self.body.capture_transition = lambda: self.camera.busy or self.camera.capture.restore_uncertain
                self.eyes.paused = self.body.capture_transition
                self.body.on_rebind = self._clear_capture_halt
            if self.cloud:
                resident_ready = self.brain_backend == 'resident' and self.brain is not None
                voice_class = ResidentVoice if resident_ready else voicemod.Voice
                self.voice = voice_class(self.client, self.args.model, self.persona, self.args.voice, self.speaker,
                                         self.face, self.eyes, self.listener, self.transcript, self.brain, self.camera,
                                         idle_s=self.args.idle, turn=self.body.turn_around, moves=self.moves,
                                         memory=self.memory, handle_file=DATA / 'session.json')
                self.voice.active_profile = self.active_profile     # look_back pins and rechecks it
                self.body.on_mic_reset = self._reset_hearing
                if not self.voice.handle and self.transcript.earlier:    # restarted: carry on from the transcript
                    self.voice.fresh_summary = True
            else:
                await asyncio.to_thread(self.calendar_access.pins.prepare)
                if getattr(self.args, 'local_voice', False):
                    self.local_voice = local_play.LocalVoice(self.speaker, loop=loop)
                self.play = local_play.LocalPlay(voice=self.local_voice)
                self.family.add_listener(lambda event: loop.call_soon_threadsafe(self._on_family_change, event))
            runner = await api.serve(self, api.api_token(DATA), self.args.api_port, api.desktop_token(DATA))
            self.body.conversation_active = lambda: (
                self.listener.speaking or self.speaker.muted()
                or bool(self.voice and (self.voice.in_turn or self.voice.waiting())))
            hearing = self.voice is not None or bool(self.local_voice and self.local_voice.available)
            parts = {'body': self.body.run, 'hear': self.hear if hearing else None,
                     'voice': self.voice.run if self.voice is not None else None, 'speaker': self.speaker.run,
                     'voice-canary': self.voice.canary_watch if self.voice is not None else None,
                     'speaker-watch': self.speaker.watch, 'face': lambda: self.face.run(self.speaker, self.voice),
                     'eyes': self.eyes.run, 'moves': self.moves.run,
                     'family-watch': None if self.cloud else self.family_watch,
                     'loop-watch': self.loopwatch.run if getattr(self, 'loopwatch', None) else None,
                     'portal-watch': (lambda: portal_watch(self.body, self.args.portal))
                     if getattr(self.args, 'portal_auto', False) else None}
            parts = {name: part for name, part in parts.items() if part is not None}
            if self.brain is not None and self.voice is not None:
                parts['brain-in'] = self.brain_in
            if self.brain_backend == 't3':
                parts.update({'brain-watch': self.brain.watch, 'brain-out': self.brain_out})
            tasks = [asyncio.create_task(supervise(name, f, self.restarts), name=name) for name, f in parts.items()]
            stop = asyncio.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            if self.args.greet and self.voice is not None:
                self.voice.add_turn([types.Part(text='(You were just switched on. Greet Adam in one short sentence.)')], 'greet')
            log.info('C3 running: %s deployment, portal %s, model %s, duplex %s, idle %.0f s, api 127.0.0.1:%d, brain %s',
                     self.deployment, self.args.portal, self.args.model if self.cloud else 'none', self.duplex,
                     self.args.idle, self.args.api_port,
                     self.brain.thread if self.brain_backend == 't3' else self.brain_backend or 'off')
            try:
                await asyncio.wait_for(stop.wait(), self.args.minutes * 60 if self.args.minutes else None)
            except asyncio.TimeoutError:
                log.info('--minutes cap reached')
        finally:
            log.info('stopping')
            if self.play is not None:
                self.play.reset('stopping')
            for task in [*tasks, *self._teardowns]:
                task.cancel()
            await asyncio.gather(*tasks, *self._teardowns, return_exceptions=True)
            try:
                if runner is not None:
                    await runner.cleanup()
            finally:
                try:
                    if self.brain_backend == 'resident' and self.brain is not None:
                        await self.brain.close()
                finally:
                    try:
                        await asyncio.to_thread(self.eyes.stop)
                    finally:
                        await asyncio.to_thread(self.body.release)
            await self.sync_brain()
            voice = self.voice
            log.info('stopped: %s', json.dumps(
                dict(voice.stats, state=voice.state, handle=bool(voice.handle)) if voice else None))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='c3', description='Cammy Control Center')
    ap.add_argument('--portal', default=None,
                    help='camera portal URL; default: found on the network (c3/discover.py)')
    ap.add_argument('--model', default='gemini-3.1-flash-live-preview')
    ap.add_argument('--voice', default='Leda')   # Gemini's youthful voice (Adam 2026-11-16)
    ap.add_argument('--level', default='normal', choices=sorted(cammy_voicefx.LEVELS))
    ap.add_argument('--tail', type=float, default=bodymod.SPEAKER_TAIL_S,
                    help='half duplex: seconds the mic stays ignored after Cammy speaks')
    ap.add_argument('--sweep', type=float, default=movesmod.SWEEP_S,
                    help='look around: seconds of pan from one end stop to the other (calibrate on Cammy)')
    ap.add_argument('--idle', type=float, default=45, help='seconds without speech before the Gemini connection sleeps')
    ap.add_argument('--duplex', default='half', choices=('off', 'half', 'auto', 'full'),
                    help="off: no echo canceller; half (default until calibrated); auto: barge-in once Echo converged")
    ap.add_argument('--talk-gate', default='smart', choices=('smart', 'name', 'open'),
                    help='smart: near speech, follow-ups and anything naming Cammy reach Gemini; '
                         'name: only follow-ups and the name; open: all speech (before 2026-11-23)')
    ap.add_argument('--near-dbfs', type=float, default=-38.0,
                    help='talk gate: onset level (dBFS) that counts as someone close talking to Cammy')
    ap.add_argument('--api-port', type=int, default=8792)
    ap.add_argument('--minutes', type=float, default=0, help='optional run cap; 0 runs until stopped')
    ap.add_argument('--greet', action='store_true')
    brain = ap.add_mutually_exclusive_group()
    brain.add_argument('--brain', choices=('resident', 't3', 'off'), default='resident',
                       help='brain backend (default: resident, t3: legacy thread)')
    brain.add_argument('--no-brain', dest='brain', action='store_const', const='off',
                       help='disable the brain, equivalent to --brain off')
    ap.add_argument('--deployment', choices=familymod.DEPLOYMENTS, default=familymod.FAMILY,
                    help='family (default): no Gemini Developer API, kids/adult gate; '
                         'adult-standalone: Gemini Live voice, adult-only use')
    ap.add_argument('--calendar-access-minutes', type=float, default=familymod.CALENDAR_S / 60,
                    help='family: a Calendar access PIN unlock closes after this many minutes')
    ap.add_argument('--local-voice', action='store_true',
                    help='family: local whisper.cpp/Kokoro speech for local play, if installed')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args(argv)
    if args.deployment == familymod.FAMILY and args.brain == 't3':
        ap.error('the family deployment supports --brain resident or off')
    if not 1 <= args.calendar_access_minutes <= 240:
        ap.error('--calendar-access-minutes: 1 to 240')
    setup_logging(DATA / 'logs', args.verbose)
    from . import discover
    args.portal_auto = not args.portal
    args.portal = discover.resolve(args.portal)
    asyncio.run(C3(args).run())
