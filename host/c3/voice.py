"""C3 voice: Gemini Live driven by the speech gate, for hours.

Automatic activity detection is off: each gated utterance goes as activity_start, its
audio (pre-roll included), activity_end. Idle microphone audio is never sent.

States: asleep (no connection) -> connecting -> awake. Speech, a brain reply, a finished
capture or an API turn wakes Voice; the utterance waits in the queue while it connects
(about 1 s) and is then sent whole. After `idle_s` without speech or replies, the
connection closes and the resumption handle is kept, so the next wake continues the same
conversation. A refused resume or two silent replies trigger a fresh session with a
summary of the recent transcript.

Full duplex: when the Listener opens while Cammy is speaking (possible only once Echo has
converged, see c3/echo.py), Voice flushes the speaker and sends activity_start; Gemini
stops its reply and listens (barge-in).
"""
import asyncio
import hashlib
import json
import logging
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from google.genai import types

from .body import RUNNING_SPEAKER_PHASES

log = logging.getLogger('c3.voice')
PIECE = 3200                     # bytes per realtime audio message (100 ms)
FIRST_CHUNK_S, CHUNK_S, RATE_OUT = 0.5, 2.0, 24000
STALE_S = 20.0                   # an utterance older than this when the link is up is dropped
WAITING_MAX_S = 8.0              # face: a sent turn with no reply news for this long stops looking pending
REPLY_STALL_S = 10
SETUP_TIMEOUT_S = 20
FATAL_RETRY_S = 600
PROTOCOL_RETRY_MIN_S = 5
PROTOCOL_RETRY_MAX_S = 60
SILENT_REPLY_LIMIT = 2
CANARY_INTERVAL_S = 3 * 3600
CANARY_EPOCH_MIN_S = 600   # a new bring-up probes only if the last passing check is older
CANARY_REPLY_S = 20


def connection_error(exc, resuming):
    """Classify provider/transport failures without logging raw provider payloads."""
    close = getattr(exc, 'rcvd', None)
    response = getattr(exc, 'response', None)
    code = getattr(exc, 'code', None) or getattr(close, 'code', None) or getattr(response, 'status_code', None)
    message = str(exc).lower()
    reason = 'code %s' % code if isinstance(code, int) else type(exc).__name__
    if isinstance(exc, TimeoutError):
        return 'transient', str(exc) if isinstance(exc, SetupTimeout) else 'connection timed out'
    if 'inactivity' in message:
        return 'transient', 'provider inactivity timeout'
    if code == 429 or any(s in message for s in ('quota', 'resource_exhausted', 'rate limit')):
        return 'transient', 'provider rate limit or quota'
    if isinstance(code, int) and (500 <= code < 600 or code in (1001, 1006, 1011, 1012, 1013, 1014)):
        return 'transient', reason
    if resuming and (
        any(s in message for s in ('resume refused', 'session resumption', 'resumption handle',
                                    'resume handle', 'cannot resume', 'failed to resume', 'invalid resume',
                                    'cannot be resumed', 'session expired', 'expired session', 'session not found'))
    ):
        return 'resume-refused', reason
    auth_error = any(s in message for s in ('api_key_invalid', 'api key not valid', 'invalid api key'))
    missing_model = 'model not found' in message or ('models/' in message and 'not found' in message)
    if code in (401, 403) or auth_error or missing_model:
        return 'fatal', reason
    if code in (400, 1007, 1008):
        return 'protocol', reason
    return 'transient', reason


class SetupTimeout(TimeoutError):
    pass


BRAIN_PERSONA = (
    " You also have a brain: an agent on Adam's computer that runs his T3 Code projects and "
    "keeps your long-term memory. Call ask_brain with Adam's request in his own words for "
    "anything beyond chat and looking: work on his computer or projects, coding, research, "
    "messages, reminders, or remembering something for later. After calling it, say in a few "
    "words that you've passed it on; do not make up the answer. A message starting with "
    "[Brain] is your brain reporting back: tell Adam what it says, briefly, in your own voice.")
MEMORY_PERSONA = (
    " You have a lasting memory that carries over between conversations. Call remember when "
    "Adam tells you something about himself, his family, friends, plans, likes or dislikes that "
    "is worth keeping, or asks you to remember something: one short fact per call, in the third "
    "person (\"Sam's favourite colour is green\"). Call recall_memory when asked about something "
    "from before that is not in what you remember below. Call forget when Adam asks you to "
    "forget something. Never keep passwords, PINs, codes or keys.")
REMEMBER = types.FunctionDeclaration(
    name='remember',
    description='Keep one short fact for future conversations (lasting memory).',
    parameters=types.Schema(type='OBJECT', properties={'fact': types.Schema(
        type='STRING', description='The fact, short, in the third person')}, required=['fact']))
RECALL = types.FunctionDeclaration(
    name='recall_memory',
    description='Search your lasting memory. Takes a few seconds.',
    parameters=types.Schema(type='OBJECT', properties={'query': types.Schema(
        type='STRING', description='What to look for, in a few words')}, required=['query']))
FORGET = types.FunctionDeclaration(
    name='forget',
    description='Forget the remembered facts about something, when Adam asks.',
    parameters=types.Schema(type='OBJECT', properties={'about': types.Schema(
        type='STRING', description='A few words naming what to forget')}, required=['about']))
ASK_BRAIN = types.FunctionDeclaration(
    name='ask_brain',
    description="Pass a request to Cammy's brain, the agent on Adam's computer that manages his "
                'T3 Code projects and long-term memory. The answer arrives later as a [Brain] message.',
    parameters=types.Schema(type='OBJECT', properties={'request': types.Schema(
        type='STRING', description="Adam's request, in his words, with any detail he gave")}, required=['request']))
LOOK = types.FunctionDeclaration(
    name='look',
    description='Take one photo with your own camera and see it. Use it whenever asked what '
                'you see, or about objects, people or the room in front of you.')
TAKE_PICTURE = types.FunctionDeclaration(
    name='take_picture',
    description="Let Adam take a photo himself with Cammy's camera. Call it only when someone has just "
                "asked in so many words to take a photo or picture now; never on your own, and never "
                "because something might be shown later. Cammy's screen switches to the camera's live "
                "view for up to a minute. After calling it, tell Adam to frame the shot and press the "
                "red record button. The photo arrives later as a [Camera] message. Use look instead "
                "when you only need to see something yourself.")
TURN_AROUND = types.FunctionDeclaration(
    name='turn_around',
    description="Turn your camera head 180 degrees, between facing Adam and facing forward (away "
                "from him). Use it when asked to turn around, look behind you, face Adam or look the "
                "other way; then use look to see what is there.")
FOLLOW_ME = types.FunctionDeclaration(
    name='follow_me',
    description="Turn the camera's face follow on or off. While it is on, the gimbal turns to keep "
                "the nearest face in view and the screen shows the live camera view with the "
                "face-follow icon. Use it when Adam says follow me, track me or keep me in frame, "
                "and with on=false when he says stop following.",
    parameters=types.Schema(type='OBJECT', properties={'on': types.Schema(type='BOOLEAN')}, required=['on']))
SHOW_CAMERA = types.FunctionDeclaration(
    name='show_camera',
    description="Switch Cammy's screen between the live camera view (on=true) and your face "
                "(on=false). Use it when Adam says show the camera, show me what you see, or show "
                "your face again.",
    parameters=types.Schema(type='OBJECT', properties={'on': types.Schema(type='BOOLEAN')}, required=['on']))
LOOK_AROUND = types.FunctionDeclaration(
    name='look_around',
    description="Sweep the camera head from one end of its travel to the other (nearly a full "
                "circle, about 350 degrees), taking photos on the way, then come back to the middle. "
                "It takes about 20 seconds; the photos arrive later as a [Look around] message. Wait "
                "for the sweep to finish before describing what the photos show.")
TAKE_VIDEO = types.FunctionDeclaration(
    name='take_video',
    description="Let Adam record a video himself with Cammy's camera. Cammy's screen switches to the "
                "camera's live view. After calling it, tell Adam to press the red record button to "
                "start and again to stop. Frames from the clip arrive later as a [Camera] message.")
LOOK_BACK = types.FunctionDeclaration(
    name='look_back',
    description="See photos your camera took earlier (about one a second, kept only on Adam's "
                "computer, in his selected brain profile). Call it only when Adam asks what was there or what happened at an "
                "earlier time; use look for now. Pass how long ago the period started and how long it "
                "lasted; at most six photos come back, evenly spread over that period, each labelled "
                "with its time. If the result says no photos cover that time, say you cannot see it; "
                "never describe the present or guess instead.",
    parameters=types.Schema(type='OBJECT', properties={
        'seconds_ago': types.Schema(type='NUMBER', description='How many seconds before now the period starts (for example 300 for five minutes ago)'),
        'duration_s': types.Schema(type='NUMBER', description='How many seconds the period lasts, toward now; 0 or left out for one moment'),
        'count': types.Schema(type='INTEGER', description='How many photos to see, 1 to 6')},
        required=['seconds_ago']))
LOOK_BACK_MAX_AGO_S = 365 * 86400    # numeric guard; actual coverage depends on this profile's JPEG sizes
LOOK_BACK_MAX_SPAN_S = 1800.0        # one window covers at most 30 minutes
LOOK_BACK_MAX_FRAMES = 6
LOOK_BACK_MAX_BYTES = 1_500_000      # all photos of one answer together
LOOK_BACK_SLACK_S = 1.5              # frames come about 1 s apart: a frame this close to the window counts


def _ago(seconds):
    seconds = max(0, round(seconds))
    if seconds < 60:
        return '%d s ago' % seconds
    if seconds < 3600:
        return '%d min %02d s ago' % divmod(seconds, 60)
    h, rest = divmod(seconds, 3600)
    return '%d h %02d min ago' % (h, rest // 60)


def _clock_now(stamp):
    """History stamps are expected to be time.time(); a monotonic stamp is handled too."""
    return time.time() if stamp > 1e8 else time.monotonic()


def _label(stamp, now):
    text = _ago(now - stamp)
    if stamp > 1e8:
        text += ' (%s)' % time.strftime('%H:%M:%S', time.localtime(stamp))
    return text


def look_back_request(args):
    """Validated (seconds_ago, duration_s, count, notes) from tool args, or raises ValueError
    with a reason Gemini can say. Nothing is clamped toward the present."""
    def number(key, default=None):
        value = args.get(key, default)
        if value is None:
            return default
        if isinstance(value, bool):
            raise ValueError('%s must be a number' % key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError('%s must be a number' % key) from None
        if value != value or value in (float('inf'), float('-inf')):
            raise ValueError('%s must be a finite number' % key)
        return value
    ago = number('seconds_ago')
    if ago is None or ago <= 0:
        raise ValueError('seconds_ago must be more than 0 (use look for now)')
    if ago > LOOK_BACK_MAX_AGO_S:
        raise ValueError('camera history does not reach %s; at most %d minutes back'
                         % (_ago(ago).replace(' ago', ' back'), LOOK_BACK_MAX_AGO_S // 60))
    duration = number('duration_s', 0.0)
    if duration < 0:
        raise ValueError('duration_s cannot be negative')
    notes = []
    if duration > ago:
        duration = ago
        notes.append('the period was cut to end now')
    if duration > LOOK_BACK_MAX_SPAN_S:
        duration = LOOK_BACK_MAX_SPAN_S
        notes.append('the period was cut to its first %d minutes' % (LOOK_BACK_MAX_SPAN_S // 60))
    count = number('count', float(LOOK_BACK_MAX_FRAMES))
    count = 1 if duration == 0 else int(min(LOOK_BACK_MAX_FRAMES, max(1, count)))
    return ago, duration, count, notes


def _even(items, n):
    if len(items) <= n:
        return list(items)
    if n == 1:
        return [items[len(items) // 2]]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def look_back_frames(frames, ago, duration, count, now, max_bytes=LOOK_BACK_MAX_BYTES):
    """Frames from Eyes.history kept only if they fall inside the asked window (never a
    newer or older substitute), evenly thinned to `count` and to `max_bytes` together."""
    start = now - ago
    end = start + duration
    inside = sorted((t, j) for t, j in frames or ()
                    if j and start - LOOK_BACK_SLACK_S <= t <= end + LOOK_BACK_SLACK_S)
    picked = _even(inside, count)
    while len(picked) > 1 and sum(len(j) for _, j in picked) > max_bytes:
        picked = _even(inside, len(picked) - 1)
    if picked and sum(len(j) for _, j in picked) > max_bytes:
        picked = []
    return picked


def live_config(persona, voice, tools, handle=None, *, resumable=True):
    return types.LiveConnectConfig(
        response_modalities=['AUDIO'],
        system_instruction=persona,
        tools=[types.Tool(function_declarations=tools)] if tools else None,
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        # No proactive audio: probed 2026-11-16 it answered "Cammy, ..." and ignored TV, but on
        # 2026-11-21 gemini-3.8-live with it on gave no reply or transcription at all to a clear
        # "Cammy, what is two plus two?" (off: answered in 6.6 s). The persona's answer-only-
        # when-addressed rule and the local speech gate remain.
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)),
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        session_resumption=types.SessionResumptionConfig(handle=handle) if resumable else None)


def tool_catalog(tools):
    """[{name, description}] of FunctionDeclarations, for the instructions API."""
    return [{'name': t.name, 'description': t.description or ''} for t in tools]


class Camera:
    """take_picture / take_video (host/cammy_capture.py) off the event loop."""

    def __init__(self, capture, done, moves=None):
        self.capture, self.done = capture, done      # done(result) is called on the loop
        self.moves = moves                           # no capture while the camera moves
        self.task = None
        self.refusal = None

    @property
    def busy(self):
        return self.capture.busy or bool(self.task and not self.task.done())

    def start(self, kind):
        moving = self.moves is not None and self.moves.busy
        self.refusal = ('already taking one' if self.busy else 'the camera is moving' if moving
                        else self.capture.blocked())
        if self.refusal:
            return False
        self.task = asyncio.create_task(self._run(kind))
        return True

    async def _run(self, kind):
        result = await asyncio.to_thread(self.capture.run, kind)
        log.info('capture %s -> %s', kind, 'failed' if 'error' in result else 'saved')
        self.done(result)


class Voice:
    def __init__(self, client, model, persona, voice_name, speaker, face, eyes, listener,
                 transcript, brain=None, camera=None, idle_s=45.0, clock=time.monotonic, turn=None, moves=None,
                 memory=None, handle_file=None):
        self.client, self.model, self.voice_name = client, model, voice_name
        self.persona = persona                  # blocking callable -> base system instruction
        self.base = None                        # the base the current handle was made with
        self.speaker, self.face, self.eyes, self.listener = speaker, face, eyes, listener
        self.body = getattr(speaker, 'body', None)
        self.transcript, self.brain, self.camera = transcript, brain, camera
        # Set by the service: () -> (profile id, name, workdir) or None. When set, look_back
        # pins the owner of the camera history and rechecks it before photos are sent.
        self.active_profile = None
        self._look_back_owners = {}             # tool call id -> owner its photos were read under
        self.idle_s, self.clock = idle_s, clock
        self.turn = turn                        # blocking callable: press Cammy's turn-180 button
        self.moves = moves                      # host/c3/moves.Moves: follow, live view, sweep
        self.memory = memory                    # host/c3/memory.Memory: lasting facts
        self.handle_file = handle_file          # the resume handle survives a C3 restart
        self.sleep_requested = False
        self.sweep = None
        self.sweep_refusal = None               # why start_sweep() last said no
        self.speech = asyncio.Queue(maxsize=1200)
        self.turns = asyncio.Queue()
        self._pending_turn = None
        self.wake = asyncio.Event()
        self.state = 'asleep'
        self.handle = None
        self.fresh_summary = False
        self._load_handle()
        self.go_away = self.in_turn = False
        self.utterance_open = False
        self.skipping = False
        self.reply_due = None                   # clock deadline while a sent turn awaits its reply
        self.last_active = clock()
        self.last_pcm = None
        self._reply_progress = None
        self._since_audio = []                  # (seconds after the last audio, kinds) for the stall log
        self.last_stall = None
        self.last_reply = None
        self._reply_origin = None
        self.last_error = None
        self.next_retry = None
        self.canary = None
        self._canary_ok_at = None               # clock of the last passing silent check
        self._restart_for_silence = False
        self._handling_tools = False
        self._reset_reply_audio()
        self.stats = {'connections': 0, 'wakes': 0, 'sleeps': 0, 'utterances': 0, 'audio_s_sent': 0.0,
                      'barge_ins': 0, 'resume_refused': 0, 'stale_dropped': 0, 'turns': 0, 'errors': 0, 'usage_reports': 0, 'last_reported_tokens': None,
                      'silent_replies': 0, 'ignored_speech_turns': 0, 'discarded_replies': 0,
                      'look_backs': 0, 'look_back_frames': 0, 'look_back_bytes': 0, 'persona_changes': 0}

    # ---- resume handle on disk ----------------------------------------------------------
    HANDLE_MAX_S = 2 * 3600       # Gemini keeps a resumable session about 2 h after it ends

    def _load_handle(self):
        if not self.handle_file:
            return
        try:
            saved = json.loads(Path(self.handle_file).read_text())
        except (OSError, ValueError):
            return
        if time.time() - saved.get('time', 0) < self.HANDLE_MAX_S and saved.get('handle'):
            self.handle, self.saved_base = saved['handle'], saved.get('base')

    def _save_handle(self):
        if not self.handle_file:
            return
        try:
            p = Path(self.handle_file)
            tmp = p.with_suffix('.tmp')
            tmp.write_text(json.dumps({'handle': self.handle, 'time': time.time(),
                                       'base': hashlib.sha256((self.base or '').encode()).hexdigest()}))
            tmp.chmod(0o600)
            tmp.replace(p)
        except OSError as e:
            log.info('could not save the resume handle: %s', e)

    def _drop_resume(self):
        self.handle, self.fresh_summary = None, True
        self._save_handle()

    def _memory_text(self):
        """Lasting memory for a fresh connection; not part of `base`, so a new fact does not
        drop a resumable session (the live session already heard it)."""
        if not self.memory:
            return ''
        brief = self.memory.brief()
        return MEMORY_PERSONA + (' What you remember (dates are when you learned it): ' + brief if brief
                                 else ' You do not remember anything yet.')

    # ---- inputs ----------------------------------------------------------------------
    def tools(self):
        return [LOOK] + ([LOOK_BACK] if callable(getattr(getattr(self, 'eyes', None), 'history', None)) else []) + ([TURN_AROUND] if self.turn else []) + ([FOLLOW_ME, SHOW_CAMERA, LOOK_AROUND] if self.moves else []) + ([TAKE_PICTURE, TAKE_VIDEO] if self.camera else []) + ([ASK_BRAIN] if self.brain else []) + ([REMEMBER, RECALL, FORGET] if self.memory else [])

    def on_speech(self, event):
        """Listener events, in order. Never blocks; the oldest event goes if the queue is full."""
        if getattr(event, 'reason', None) == 'reset':
            needs_end = self.utterance_open
            while not self.speech.empty():
                _, queued = self.speech.get_nowait()
                needs_end |= getattr(queued, 'reason', None) == 'reset'
            self.utterance_open = self.skipping = False
            if not needs_end or not self._camera_up(None):
                return
        if self.speech.full():
            self.speech.get_nowait()
        self.speech.put_nowait((self.clock(), event))
        self.last_active = self.clock()
        if event.kind == 'start':
            if self.speaker.active or self.in_turn:
                self.stats['barge_ins'] += 1
                self.speaker.flush()            # Cammy stops talking at once
            if self.state == 'asleep':
                self.wake.set()

    def add_turn(self, parts, label='turn'):
        """A user-role turn for Gemini (brain reply, capture, API). Wakes Voice."""
        self.sleep_requested = False            # new work cancels a pending API sleep
        self.turns.put_nowait((label, parts))
        if self.state == 'asleep':
            self.wake.set()

    def camera_done(self, result):
        import cammy_capture
        parts = [types.Part(text=cammy_capture.gemini_text(result))]
        parts += [types.Part(inline_data=types.Blob(mime_type='image/jpeg', data=j)) for j in result.get('gemini') or []]
        self.transcript.add('camera', cammy_capture.gemini_text(result))
        self.add_turn(parts, 'camera')

    def start_sweep(self):
        """Run look_around in the background; its photos come back as a user turn. False (with
        `sweep_refusal`) while a sweep, another motion or a capture runs."""
        if not self.moves:
            self.sweep_refusal = 'camera moves are not available'
        elif self.sweep and not self.sweep.done():
            self.sweep_refusal = 'already looking around'
        elif self.camera and self.camera.busy:
            self.sweep_refusal = 'a photo or video is being taken'
        else:
            self.sweep_refusal = self.moves.refusal()
        if self.sweep_refusal:
            return False
        self.sweep = asyncio.ensure_future(self._sweep())
        return True

    async def _sweep(self):
        try:
            while self.speaker.muted() or self.speaker.sending:
                await asyncio.sleep(0.1)
            frames = await self.moves.look_around()
        except Exception as e:  # noqa: BLE001
            self.transcript.add('tool', 'look_around failed: %s' % str(e)[:120])
            self.add_turn([types.Part(text='[Look around] The sweep failed: %s. Tell Adam briefly.' % str(e)[:80])], 'look_around')
            return
        parts = [types.Part(text='[Look around] %d photos from your sweep, far left to far right. '
                                 'Describe briefly what is around you.' % len(frames))]
        for label, jpeg in frames:
            parts += [types.Part(text=label), types.Part(inline_data=types.Blob(mime_type='image/jpeg', data=jpeg))]
        self.transcript.add('camera', 'look_around: %d frames' % len(frames))
        self.add_turn(parts, 'look_around')

    # ---- lifecycle -------------------------------------------------------------------
    async def run(self):
        if not self.speech.empty() or self._pending_turn is not None or not self.turns.empty():
            self.wake.set()
        while True:
            await self.wake.wait()
            self.wake.clear()
            self.stats['wakes'] += 1
            await self._awake()
            self.stats['sleeps'] += 1
            if not self.speech.empty() or self._pending_turn is not None or not self.turns.empty():
                self.wake.set()

    @asynccontextmanager
    async def _connect(self, config):
        async with AsyncExitStack() as stack:
            try:
                async with asyncio.timeout(SETUP_TIMEOUT_S):
                    session = await stack.enter_async_context(
                        self.client.aio.live.connect(model=self.model, config=config))
            except TimeoutError as exc:
                raise SetupTimeout('Gemini setup timed out') from exc
            yield session

    async def _retry(self, delay):
        self.next_retry = time.time() + delay
        self.state = 'backoff'
        try:
            await asyncio.sleep(delay)
        finally:
            self.next_retry = None

    async def _awake(self):
        failures = 0
        try:
            while True:
                if self.body is not None and not self.body.up.is_set():
                    self.state = 'waiting-camera'
                    await self.body.up.wait()
                epoch = self.body.epoch if self.body is not None else None
                self.state = 'connecting'
                # The base is the persona: face pack, brain sentence and the selected profile's
                # AGENT.md and skills. A resumed session keeps the persona it was opened with, so
                # when the base changed (pack, profile, or a saved instruction) the handle goes
                # and a fresh session starts with a summary. Saving never wakes Voice or touches
                # a live connection; the change lands at the next connection.
                base = await asyncio.to_thread(self.persona)
                saved = getattr(self, 'saved_base', None)   # a handle loaded from disk
                if self.handle and ((self.base is not None and base != self.base)
                                    or (saved and saved != hashlib.sha256(base.encode()).hexdigest())):
                    self._drop_resume()
                    self.stats['persona_changes'] += 1
                    log.info('persona changed since the resume handle was made: starting fresh')
                self.saved_base = None
                self.base = base
                summary = self.transcript.recent_text() if self.fresh_summary else ''
                text = base + self._memory_text() + ('\n\nYou were talking with Adam earlier; the last part of that conversation:\n'
                               + summary if summary else '')
                handle, last_pcm = self.handle, self.last_pcm
                config = live_config(text, self.voice_name, self.tools(), handle)
                try:
                    async with self._connect(config) as session:
                        self.stats['connections'] += 1
                        self.state = 'awake'
                        self.last_error = None
                        self.last_active = self.clock()
                        log.info('connected (%s)', 'resumed' if handle else
                                 'fresh with summary' if summary else 'fresh')
                        self.fresh_summary = False
                        reason = await self._connection(session, epoch)
                    failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep the other service parts available
                    failures = 1 if self.last_pcm != last_pcm else failures + 1
                    self.stats['errors'] += 1
                    kind, detail = connection_error(exc, bool(handle))
                    if detail == 'provider inactivity timeout' and self._quiet():
                        return
                    self.last_error = {'kind': kind, 'detail': detail}
                    log.warning('connection failed (%d): %s: %s', failures, kind, detail)
                    if kind == 'fatal':
                        delay = FATAL_RETRY_S
                    elif kind == 'protocol':
                        delay = min(PROTOCOL_RETRY_MAX_S, PROTOCOL_RETRY_MIN_S * 2 ** min(failures - 1, 4))
                    elif kind == 'resume-refused':
                        self.stats['resume_refused'] += 1
                        self._drop_resume()
                        delay = 1.5
                    else:
                        if failures >= 3 and self._quiet():
                            return          # nobody is waiting; the next wake tries again
                        delay = 30 if detail == 'provider rate limit or quota' else min(30, 2 ** min(failures, 5))
                    await self._retry(delay)
                    continue
                if reason == 'idle':
                    log.info('idle %.0f s: sleeping (handle %s)', self.idle_s, 'kept' if self.handle else 'none')
                    return
                if reason == 'camera-down' and self._quiet():
                    return
                await self._retry(1.5)   # the server must drop the old connection first
        finally:
            self.state = 'asleep'
            self.in_turn = self.go_away = False
            self.reply_due = None

    def idle_now(self):
        """API sleep: close the connection at the next quiet moment. A flag, because every
        message from Gemini (resumption updates too) refreshes last_active (2026-11-16)."""
        self.sleep_requested = True

    def waiting(self):
        """A turn (speech or text) reached Gemini and no reply has finished yet. For the face only:
        it ends at turn_complete, an interruption, the connection's end or WAITING_MAX_S without
        news (a silent model, a dropped reply), so it can never hold the face."""
        return self.reply_due is not None and self.clock() < self.reply_due

    def status(self):
        now = self.clock()
        reply = 'waiting' if self.waiting() else 'timed-out' if self.reply_due is not None else self.last_reply
        return dict(self.stats, state=self.state, handle=bool(self.handle), reply=reply,
                    last_error=self.last_error, next_retry=self.next_retry, canary=self.canary,
                    last_stall=self.last_stall,
                    pcm_age_s=None if self.last_pcm is None else round(now - self.last_pcm, 1))

    def _expect_reply(self, origin='direct'):
        self._reply_origin = origin
        self.reply_due = self.clock() + WAITING_MAX_S
        self.last_reply = None

    def _silent_reply(self, outcome):
        self.last_reply = outcome
        self.reply_due = None
        self.stats['silent_replies'] += 1
        if self.stats['silent_replies'] % SILENT_REPLY_LIMIT == 0:
            log.warning('%d consecutive silent replies; reconnecting without resumption', self.stats['silent_replies'])
            self._restart_for_silence = True
            self._drop_resume()

    def _quiet(self):
        return (self.speech.empty() and self.turns.empty() and self._pending_turn is None
                and not self.speaker.muted() and not self.in_turn and not self.utterance_open
                and not self.listener.speaking and not self._handling_tools
                and not (self.camera and self.camera.busy))

    def _camera_up(self, epoch):
        return self.body is None or (self.body.up.is_set() and (epoch is None or self.body.epoch == epoch))

    async def canary_watch(self):
        """Silent check every CANARY_INTERVAL_S, and on a new bring-up unless one passed in the
        last CANARY_EPOCH_MIN_S (a flapping camera must not open a Gemini session per reboot)."""
        epoch, due = None, 0
        while True:
            await self.body.up.wait()
            current = self.body.epoch
            if self.state == 'asleep' and not self.wake.is_set() and self._quiet():
                recent = (self._canary_ok_at is not None
                          and self.clock() - self._canary_ok_at < CANARY_EPOCH_MIN_S)
                if epoch != current and recent and self.clock() < due:
                    epoch = current
                elif epoch != current or self.clock() >= due:
                    result = await self._probe_voice(current)
                    if not result['reasons']:
                        self._canary_ok_at = self.clock()
                    if self.body._current(current):
                        if self.canary is None or result['reasons'] != self.canary['reasons']:
                            report = log.warning if result['reasons'] else log.info
                            report('silent voice check: %s', ', '.join(result['reasons']) or 'passed')
                        self.canary = result
                        epoch, due = current, self.clock() + CANARY_INTERVAL_S
            await asyncio.sleep(1)

    async def _probe_voice(self, epoch):
        """Check Gemini and read the portal's speaker status without entering the audio path."""
        reasons = []
        try:
            status = await asyncio.to_thread(self.body.cammy.speaker_status)
            phase = status.get('phase')
            if status.get('configured') is False:
                reasons.append('speaker not configured')
            elif phase not in (*RUNNING_SPEAKER_PHASES, 'idle', 'finished', 'cancelled'):
                reason = status.get('reason')
                reasons.append('speaker: %s' % (reason if reason and reason != 'none' else phase or 'unknown phase'))
        except Exception as exc:  # read-only probe; Body owns recovery
            reasons.append('speaker status: %s' % type(exc).__name__)
        if self.body._current(epoch):
            config = live_config('Answer the following service check aloud in one word.',
                                 self.voice_name, [], resumable=False)
            try:
                async with self._connect(config) as session:
                    async with asyncio.timeout(CANARY_REPLY_S):
                        prompt = types.Content(role='user', parts=[types.Part(text='Say ready.')])
                        await session.send_client_content(turns=prompt, turn_complete=True)
                        audio = complete = False
                        async for msg in session.receive():
                            sc = msg.server_content
                            if sc and sc.model_turn:
                                audio |= any(part.inline_data and part.inline_data.data
                                             and (part.inline_data.mime_type or '').startswith('audio/')
                                             for part in sc.model_turn.parts or [])
                            if sc and sc.turn_complete:
                                complete = True
                                break
                        if not audio:
                            reasons.append('Gemini produced no audio')
                        elif not complete:
                            reasons.append('Gemini turn did not complete')
            except Exception as exc:
                _, detail = connection_error(exc, False)
                reasons.append('Gemini: ' + detail)
        return {'state': 'degraded' if reasons else 'ok', 'reasons': reasons,
                'epoch': epoch, 'checked_at': time.time()}

    async def _connection(self, session, epoch=None):
        self.go_away = self.in_turn = False
        self._restart_for_silence = False
        self._reply_origin = self.reply_due = None
        tasks = [asyncio.create_task(c) for c in (
            self._receive(session, epoch), self._send_speech(session, epoch),
            self._deliver(session, epoch), self._watch(epoch))]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._look_back_owners.clear()
            if self.in_turn:
                self.last_reply = 'connection-ended' if self._finish_audio(epoch) else 'discarded'
            if self.reply_due is not None:
                if (not self.in_turn and self._reply_origin != 'mic-reset'
                        and self.clock() >= self.reply_due
                        and self._camera_up(epoch)):
                    self._silent_reply('timed-out')
                elif not self.in_turn:
                    self.last_reply = 'connection-ended'
            self.in_turn = False
            self.reply_due = None
        for task in done:
            if task.exception():
                raise task.exception()
        return next(iter(done)).result()

    async def _watch(self, epoch=None):
        while True:
            await asyncio.sleep(0.25)
            if not self._camera_up(epoch):
                return 'camera-down'
            now = self.clock()
            if (self.in_turn and not self._handling_tools and self._reply_progress is not None
                    and now - self._reply_progress >= REPLY_STALL_S):
                self._finish_audio(epoch)
                self.in_turn = False
                self.reply_due = None
                self.last_reply = 'stalled'
                self.last_stall = {'idle_s': round(now - self._reply_progress, 1), 'since_audio': self._since_audio[-12:]}
                log.warning('Gemini audio stalled before turn completion (%.1f s; since audio: %s); reconnecting',
                            now - self._reply_progress,
                            ' '.join(f'{dt}:{k}' for dt, k in self._since_audio[-12:]) or 'nothing')
                return 'stalled'
            if not self.in_turn and not self._handling_tools and self.reply_due is not None and now >= self.reply_due:
                if self._reply_origin == 'mic-reset':
                    self.last_reply, self.reply_due = 'interrupted', None
                else:
                    self._silent_reply('timed-out')
            if self._restart_for_silence:
                return 'silent'
            busy = self.in_turn or self._handling_tools or self.speaker.muted()
            if self.go_away and not busy and not self.utterance_open:
                return 'go_away'
            if (self.sleep_requested or now - self.last_active > self.idle_s) and self._quiet():
                self.sleep_requested = False
                return 'idle'

    # ---- to Gemini -------------------------------------------------------------------
    async def _audio(self, session, pcm, epoch):
        for i in range(0, len(pcm), PIECE):
            if not self.utterance_open or not self._camera_up(epoch):
                break
            piece = pcm[i:i + PIECE]
            await session.send_realtime_input(audio=types.Blob(data=piece, mime_type='audio/pcm;rate=16000'))
            self.stats['audio_s_sent'] += len(piece) / 32000

    async def _send_speech(self, session, epoch=None):
        activity_open = self.utterance_open
        if activity_open and self._camera_up(epoch):  # an utterance spans the reconnect
            await session.send_realtime_input(activity_start=types.ActivityStart())
        while True:
            at, ev = await self.speech.get()
            if not self._camera_up(epoch):
                return 'camera-down'
            if ev.kind == 'start':
                self.skipping = self.clock() - at > STALE_S
                if self.skipping:
                    self.stats['stale_dropped'] += 1
                    continue
                activity_open = self.utterance_open = True
                await session.send_realtime_input(activity_start=types.ActivityStart())
                if not self.utterance_open:  # the mic reset while the send was in flight
                    continue
                self.stats['utterances'] += 1
                await self._audio(session, ev.pcm, epoch)
            elif ev.kind == 'audio':
                if self.utterance_open and not self.skipping:
                    await self._audio(session, ev.pcm, epoch)
            elif ev.kind == 'end':
                reset = getattr(ev, 'reason', None) == 'reset'
                if activity_open and (reset or self.utterance_open and not self.skipping):
                    self._expect_reply('mic-reset' if reset else 'speech')
                    await session.send_realtime_input(activity_end=types.ActivityEnd())
                activity_open = self.utterance_open = self.skipping = False
            self.last_active = self.clock()

    async def _deliver(self, session, epoch=None):
        while True:
            # Keep the head of the queue across connection teardown, including cancellation
            # while waiting to send it. Later turns stay in FIFO order.
            if self._pending_turn is None:
                self._pending_turn = await self.turns.get()
            _, parts = self._pending_turn
            while (not self._camera_up(epoch) or self._restart_for_silence or self.in_turn or self.reply_due is not None
                   or self._handling_tools or self.speaker.muted() or self.listener.speaking
                   or self.utterance_open or not self.speech.empty()
                   or (self.moves and self.moves.sweeping)):
                await asyncio.sleep(0.2)
            self._expect_reply()
            await session.send_client_content(turns=types.Content(role='user', parts=parts), turn_complete=True)
            self._pending_turn = None
            self.stats['turns'] += 1
            self.last_active = self.clock()

    # ---- from Gemini -----------------------------------------------------------------
    async def _tool(self, call):
        name, args = call.name, call.args or {}
        parts = None
        if name == 'ask_brain':
            request = str(args.get('request') or '').strip()
            try:
                if not (self.brain and request):
                    raise RuntimeError('no brain or empty request')
                outcome = await self.brain.ask(request)
                local = outcome.get('status') if isinstance(outcome, dict) and outcome.get('capability') else None
                if local == 'local':
                    # The answer stays on Adam's PC; Gemini never receives it.
                    result = {'result': 'Answered on Adam\'s PC (%s). You do not have the answer; tell him in a '
                                        'few words to look there.' % outcome.get('message', 'Cammy Control Center')}
                elif local == 'blocked':
                    result = {'error': 'not done: %s' % outcome.get('message', 'not available')}
                else:
                    result = {'result': 'Passed to your brain. Say so in a few words; its answer comes later as a [Brain] message.'}
            except Exception as e:  # noqa: BLE001 - spoken as an error, never fatal
                result = {'error': 'brain unreachable: %s' % str(e)[:80]}
            self.transcript.add('tool', 'ask_brain %r -> %s' % (request[:200], next(iter(result))))
        elif name in ('take_picture', 'take_video'):
            kind = 'photo' if name == 'take_picture' else 'video'
            if not self.camera:
                result = {'error': 'taking pictures is not available'}
            elif not self.camera.start(kind):
                result = {'error': getattr(self.camera, 'refusal', None) or 'already taking one'}
            elif kind == 'photo':
                result = {'result': 'Live view is on. Tell Adam to frame the shot and press the red record button.'}
            else:
                result = {'result': 'Live view is on. Tell Adam to press the red record button to start, and again to stop.'}
            self.transcript.add('tool', '%s -> %s' % (name, next(iter(result.values()))))
        elif name in ('turn_around', 'follow_me', 'show_camera', 'look_around') and not self.moves:
            result = {'error': 'camera moves are not available'}
        elif name == 'turn_around':
            try:                                # the same path as the API's /v1/turn-around
                ok, message = await self.moves.turn_around()
                result = {'result' if ok else 'error': message}
            except Exception as e:  # noqa: BLE001
                result = {'error': 'could not turn: %s' % str(e)[:80]}
            self.transcript.add('tool', 'turn_around -> %s' % next(iter(result)))
        elif name == 'follow_me':
            try:
                ok, message = await self.moves.set_follow(bool(args.get('on', True)))
                result = {'result' if ok else 'error': message}
            except Exception as e:  # noqa: BLE001
                result = {'error': 'could not reach the camera: %s' % str(e)[:80]}
            self.transcript.add('tool', 'follow_me %s -> %s' % (args.get('on'), next(iter(result.values()))))
        elif name == 'show_camera':
            result = {'result': self.moves.show_camera(bool(args.get('on', True)))}
            self.transcript.add('tool', 'show_camera %s' % args.get('on'))
        elif name == 'look_around':
            if self.start_sweep():
                result = {'result': 'Sweep started. Wait silently for the [Look around] photos, then describe them.'}
            else:
                result = {'error': self.sweep_refusal or 'already looking around'}
            self.transcript.add('tool', 'look_around -> %s' % next(iter(result)))
        elif name == 'look':
            self.face.looking_until = time.monotonic() + 3
            frame = await self.eyes.look()
            # The photo must travel inside the tool response: a separate realtime video
            # frame is ignored and Gemini invents a scene (probe 2026-11-16).
            if frame:
                result = {'result': 'Photo attached. Describe only what it shows.'}
                parts = [types.FunctionResponsePart(inline_data=types.FunctionResponseBlob(
                    mime_type='image/jpeg', data=frame))]
            else:
                result = {'error': 'camera unavailable'}
            self.transcript.add('tool', 'look -> %s' % ('frame %d bytes' % len(frame) if frame else 'no frame'))
        elif name == 'look_back':
            result, parts, owner = await self._look_back(args)
            if parts:
                self._look_back_owners[call.id] = owner
        elif name in ('remember', 'recall_memory', 'forget') and not self.memory:
            result = {'error': 'memory is not available'}
        elif name == 'remember':
            ok, message = self.memory.remember(args.get('fact'))
            result = {'result' if ok else 'error': message}
            self.transcript.add('tool', 'remember %r -> %s' % (str(args.get('fact'))[:200], message))
        elif name == 'recall_memory':
            found = await asyncio.to_thread(self.memory.recall, args.get('query'))
            result = {'result': found} if found else {'result': 'nothing remembered about that'}
            self.transcript.add('tool', 'recall_memory %r -> %d found' % (str(args.get('query'))[:120], len(found)))
        elif name == 'forget':
            count, gone = self.memory.forget(args.get('about'))
            result = {'result': 'forgot %d fact%s: %s' % (count, '' if count == 1 else 's', '; '.join(gone)[:400])
                      if count else 'nothing remembered matched that'}
            self.transcript.add('tool', 'forget %r -> %d' % (str(args.get('about'))[:120], count))
        else:
            result = {'error': 'unknown tool'}
        return types.FunctionResponse(id=call.id, name=name, response=result, parts=parts)

    UNGATED = ('ungated',)                      # no profile gate given (t3/off backends, tests)

    def _history_owner(self):
        """Who the camera history belongs to now: (profile id, workdir) when the service's
        selected profile and Eyes' own history owner agree, None when they do not or nothing is
        selected. Blocking (Eyes may read its disk store): call it in a worker thread."""
        gate = self.active_profile
        if gate is None:
            return self.UNGATED
        profile = gate()
        if profile is None:
            return None
        status = getattr(self.eyes, 'history_status', None)
        status = status() if callable(status) else status
        owner = status.get('profile') if isinstance(status, dict) else None
        if isinstance(owner, dict):
            owner = owner.get('id')
        return (profile[0], str(profile[2])) if owner == profile[0] else None

    async def _owner_now(self):
        try:
            return await asyncio.to_thread(self._history_owner)
        except Exception as e:  # noqa: BLE001 - an unknown owner shares nothing
            log.info('look_back owner check failed: %s', e)
            return None

    async def _recheck_owners(self, responses):
        """Just before tool results go to Gemini: photos read under an owner that is no longer
        current (the profile changed meanwhile) are withheld and replaced by an error."""
        pinned = {r.id: self._look_back_owners.pop(r.id) for r in responses if r.id in self._look_back_owners}
        if not pinned:
            return responses
        now = await self._owner_now()
        out = []
        for r in responses:
            if r.id in pinned and pinned[r.id] != now:
                self.transcript.add('tool', 'look_back -> withheld: the brain profile changed')
                r = types.FunctionResponse(id=r.id, name=r.name, response={
                    'error': "Adam's brain profile changed, so those earlier photos are not available. "
                             'Say you cannot see that time.'})
            out.append(r)
        return out

    async def _look_back(self, args):
        """Only here do stored frames leave the computer: the photos Adam asked about travel
        inside this tool response (the pattern look uses), labelled in order. Returns
        (result, parts, owner): the history owner pinned for the read, rechecked before sending."""
        history = getattr(self.eyes, 'history', None)
        if not callable(history):
            self.transcript.add('tool', 'look_back -> unavailable')
            return {'error': 'camera history is not available; you cannot see earlier moments'}, None, None
        try:
            ago, duration, count, notes = look_back_request(args)
        except ValueError as e:
            self.transcript.add('tool', 'look_back -> refused: %s' % e)
            return {'error': str(e)}, None, None
        owner = await self._owner_now()
        if owner is None:
            self.transcript.add('tool', 'look_back -> no profile history')
            return {'error': 'camera history is off because no brain profile is selected; you cannot '
                             'see earlier moments'}, None, None
        try:
            frames = await asyncio.to_thread(history, ago, duration, count)
        except Exception as e:  # noqa: BLE001 - spoken as an error, never fatal
            frames = []
            log.info('look_back history failed: %s', e)
        if frames and await self._owner_now() != owner:
            self.transcript.add('tool', 'look_back -> withheld: the brain profile changed')
            return {'error': "Adam's brain profile changed while looking back, so those photos are not "
                             'available. Say you cannot see that time.'}, None, None
        now = _clock_now(frames[0][0]) if frames else time.time()
        picked = look_back_frames(frames, ago, duration, count, now)
        window = 'from %s for %d s' % (_ago(ago), round(duration)) if duration else 'at %s' % _ago(ago)
        self.stats['look_backs'] += 1
        if not picked:
            self.transcript.add('tool', 'look_back %s -> no coverage' % window)
            return {'error': 'No camera photos cover %s (nothing was recorded then, or it is older than '
                             'the history). Tell Adam you cannot see that time; do not describe the '
                             'present or guess.' % window}, None, None
        size = sum(len(j) for _, j in picked)
        self.stats['look_back_frames'] += len(picked)
        self.stats['look_back_bytes'] += size
        labels = ['Photo %d of %d: %s' % (i + 1, len(picked), _label(t, now)) for i, (t, _) in enumerate(picked)]
        first, last = picked[0][0], picked[-1][0]
        missing = []
        if first - (now - ago) > LOOK_BACK_SLACK_S + 1:
            missing.append('nothing before %s' % _label(first, now))
        if duration and (now - ago + duration) - last > LOOK_BACK_SLACK_S + 1:
            missing.append('nothing after %s' % _label(last, now))
        text = ('%d photo%s attached in time order, %s. Describe only what they show; say plainly '
                'that anything they do not show is unknown.' % (len(picked), '' if len(picked) == 1 else 's', window))
        if missing:
            text += ' The photos only partly cover the period: %s.' % '; '.join(missing)
        if notes:
            text += ' Note: %s.' % '; '.join(notes)
        self.transcript.add('tool', 'look_back %s -> %d frames %d bytes' % (window, len(picked), size))
        return {'result': text, 'photos': labels}, [types.FunctionResponsePart(inline_data=types.FunctionResponseBlob(
            mime_type='image/jpeg', data=jpeg)) for _, jpeg in picked], owner

    @staticmethod
    def _kinds(msg):
        """Short names for what one server message carries."""
        kinds, sc = [], msg.server_content
        if sc:
            for part in (sc.model_turn.parts or []) if sc.model_turn else []:
                if part.inline_data and part.inline_data.data:
                    kinds.append('audio')
                elif part.text:
                    kinds.append('thought' if part.thought else 'text')
                else:
                    kinds.append('part')
            for name, tag in (('input_transcription', 'heard'), ('output_transcription', 'said'),
                              ('generation_complete', 'gen-done'), ('turn_complete', 'turn-done'),
                              ('interrupted', 'interrupted'), ('waiting_for_input', 'waiting')):
                if getattr(sc, name, None):
                    kinds.append(tag)
        for name, tag in (('tool_call', 'tool'), ('tool_call_cancellation', 'tool-cancel'),
                          ('session_resumption_update', 'resume'), ('go_away', 'go-away'),
                          ('usage_metadata', 'usage')):
            if getattr(msg, name, None) is not None:
                kinds.append(tag)
        return '+'.join(dict.fromkeys(kinds)) or 'empty'

    def _trace(self, msg):
        kinds = self._kinds(msg)
        if 'audio' in kinds:
            self._since_audio = []
        elif self.last_pcm is not None:
            self._since_audio = (self._since_audio + [(round(self.clock() - self.last_pcm, 1), kinds)])[-24:]

    def _reset_reply_audio(self):
        self._reply_audio = bytearray()
        self._reply_streamed = self._reply_discarded = False

    def _can_play_reply(self, epoch):
        if not self._camera_up(epoch) and not self._reply_discarded:
            self.stats['discarded_replies'] += 1
            self._reply_discarded = True
            self._reply_audio.clear()
        return not self._reply_discarded

    def _finish_audio(self, epoch):
        played = self.in_turn and self._can_play_reply(epoch)
        if played:
            self.speaker.say(bytes(self._reply_audio), end=True)
        self._reset_reply_audio()
        return played

    async def _receive(self, session, epoch=None):
        said, heard = [], []
        text_answer = False
        while True:
            received = 0
            async for msg in session.receive():
                received += 1
                self._trace(msg)
                if msg.usage_metadata is not None:
                    self.stats['usage_reports'] += 1
                    self.stats['last_reported_tokens'] = msg.usage_metadata.total_token_count
                if msg.server_content or msg.tool_call:
                    self.last_active = self.clock()
                if msg.session_resumption_update:
                    update = msg.session_resumption_update
                    if update.resumable and update.new_handle and not self._restart_for_silence:
                        self.handle = update.new_handle
                        self._save_handle()
                if msg.go_away:
                    self.go_away = True
                if msg.tool_call:
                    self._handling_tools = True
                    try:
                        responses = [await self._tool(call) for call in msg.tool_call.function_calls or []]
                        responses = await self._recheck_owners(responses)
                        await session.send_tool_response(function_responses=responses)
                        if self.reply_due is not None:
                            self._expect_reply('tool')
                        if self.in_turn:
                            self._reply_progress = self.clock()
                    finally:
                        self._handling_tools = False
                sc = msg.server_content
                if not sc:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    heard.append(sc.input_transcription.text)
                if sc.output_transcription and sc.output_transcription.text:
                    said.append(sc.output_transcription.text)
                if sc.interrupted:
                    self._reset_reply_audio()
                    self.speaker.flush()
                if sc.model_turn and not sc.interrupted:
                    for part in sc.model_turn.parts or []:
                        if part.text and not part.thought and part.text.strip():
                            text_answer = True
                        if (part.inline_data and part.inline_data.data
                                and (part.inline_data.mime_type or '').startswith('audio/')):
                            self.in_turn = True
                            if self.reply_due is not None:
                                self.reply_due = self.clock() + WAITING_MAX_S
                            self.last_pcm = self._reply_progress = self.clock()
                            self.stats['silent_replies'] = 0
                            if self._can_play_reply(epoch):
                                self._reply_audio += part.inline_data.data
                                chunk_s = CHUNK_S if self._reply_streamed else FIRST_CHUNK_S
                                if len(self._reply_audio) >= chunk_s * RATE_OUT * 2:
                                    self.speaker.say(bytes(self._reply_audio))
                                    self._reply_audio.clear()
                                    self._reply_streamed = True
                if sc.interrupted:
                    self.last_reply = 'interrupted'
                    self.in_turn = False
                    self.reply_due = None
                if sc.turn_complete:
                    played = self._finish_audio(epoch)
                    if self.in_turn:
                        self.last_reply = 'received-audio' if played else 'discarded'
                    elif self._reply_origin == 'speech' and not (said or text_answer) and self.last_reply != 'interrupted':
                        self.stats['ignored_speech_turns'] += 1
                        # A late empty completion disambiguates an earlier timeout.
                        if self.last_reply == 'timed-out':
                            self.stats['silent_replies'] = max(0, self.stats['silent_replies'] - 1)
                        self.last_reply = 'ignored-speech'
                    elif self._reply_origin == 'mic-reset':
                        self.last_reply = 'interrupted'
                    elif self.reply_due is not None:
                        self._silent_reply('completed-without-audio')
                    self._reply_origin = None
                    self.in_turn = False
                    self.reply_due = None
                    if heard:
                        self.transcript.add('adam', ''.join(heard).strip())
                    if said and played:
                        self.transcript.add('cammy', ''.join(said).strip())
                    heard, said = [], []
                    text_answer = False
                    if self._restart_for_silence:
                        return 'silent'
            if not received:
                raise ConnectionError('Gemini connection closed')
