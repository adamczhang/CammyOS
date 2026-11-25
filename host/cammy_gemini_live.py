#!/usr/bin/env python3
"""Hands-free Cammy conversation over Wi-Fi: Cammy's mic -> Gemini Live -> Cammy's speaker.

Integration milestone I1 (docs/PLAN.md). Input is Cammy's own microphone (0.5.0 portal
/v1/audio/stream, which needs a live preview event session) or the P15 for comparison.
Output goes to Cammy's speaker portal through host/cammy_voicefx.py. Half-duplex: while
Cammy speaks, plus a tail, the mic stream is replaced by silence so Gemini never hears
itself. A `look` tool sends Gemini the newest 1 fps frame from Cammy's camera (RTSP).

Bounded: the session ends after --minutes; leases are renewed only while it runs and are
released, the preview stopped and any playback cancelled on exit. Transcripts print to
stdout; no audio or frames are stored (the newest frame lives in a private RAM dir).
The Gemini API key stays on the PC (private/credentials/gemini.key).
"""
import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sdk'))
sys.path.insert(0, str(ROOT / 'host'))
from cammy import Cammy, CammyError  # noqa: E402
import cammy_voicefx  # noqa: E402
import cammy_brain  # noqa: E402
import cammy_persona  # noqa: E402

BRAIN_PERSONA = (
    " You also have a brain: an agent on Adam's computer that runs his T3 Code projects and "
    "keeps your long-term memory. Call ask_brain with Adam's request in his own words for "
    "anything beyond chat and looking: work on his computer or projects, coding, research, "
    "messages, reminders, or remembering something for later. After calling it, say in a few "
    "words that you've passed it on; do not make up the answer. A message starting with "
    "[Brain] is your brain reporting back: tell Adam what it says, briefly, in your own voice.")
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
    description="Let Adam take a photo himself with Cammy's camera. Cammy's screen switches to the "
                "camera's live view. After calling it, tell Adam to frame the shot and press the red "
                "record button. The photo arrives later as a [Camera] message. Use look instead when "
                "you only need to see something yourself.")
TAKE_VIDEO = types.FunctionDeclaration(
    name='take_video',
    description="Let Adam record a video himself with Cammy's camera. Cammy's screen switches to the "
                "camera's live view. After calling it, tell Adam to press the red record button to "
                "start and again to stop. Frames from the clip arrive later as a [Camera] message.")
FIRST_CHUNK_S, CHUNK_S = 1.0, 2.0
RATE_OUT = 24000
FRAME_DIR = Path('/run/user/%d/cammy-live' % os.getuid())


def _die_with_parent():
    import ctypes
    ctypes.CDLL('libc.so.6', use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


def credentials():
    token_file = json.loads((ROOT / 'private/host/config.json').read_text())['token_file']
    return Path(token_file).read_text().strip(), (ROOT / 'private/credentials/gemini.key').read_text().strip()


class Speaker:
    """Streams each reply to Cammy as one gapless job: the first chunk opens it, the rest
    append (portal stream mode). Knows when the mic must stay muted (playing + tail)."""

    def __init__(self, endpoint, token, level, tail, client=None):
        self.cammy = client or Cammy(endpoint, token, agent='cammy-live-speaker', label=agent_label())
        self.level, self.tail = level, tail
        self.queue = asyncio.Queue()
        self.busy_until = 0.0
        self.active = False
        self.generation = 0
        self.chunks = 0

    def muted(self):
        return self.active or not self.queue.empty() or time.monotonic() < self.busy_until

    def flush(self):
        self.generation += 1
        while not self.queue.empty():
            self.queue.get_nowait()
        if self.active:
            try:
                self.cammy.speaker_cancel()
            except Exception:
                pass

    def _send_blocking(self, pcm24):
        pcm = cammy_voicefx.speaker_pcm(pcm24, self.level)
        for _ in range(80):
            try:
                self.cammy.play_pcm(pcm, append=self.active)
                break
            except CammyError as exc:
                text = str(exc)
                if exc.status == 409 and any(reason in text for reason in
                                             ('queue full', 'already active', 'stream closed', 'stream limit')):
                    time.sleep(0.2)  # wait for the feeder, or for a finishing job to be reaped
                    continue
                raise
        else:
            raise RuntimeError('speaker stayed busy')
        self.active = True
        self.busy_until = time.monotonic() + len(pcm) / 32000 + self.tail
        self.chunks += 1

    async def run(self):
        while True:
            generation, pcm24 = await self.queue.get()
            if generation != self.generation:
                continue
            try:
                await asyncio.to_thread(self._send_blocking, pcm24)
            except Exception as exc:
                print('[speaker] %s: %s' % (type(exc).__name__, str(exc)[:120]), flush=True)

    async def watch(self):
        """While a stream is active, poll its phase; when it ends, keep the mic muted for the tail."""
        while True:
            await asyncio.sleep(0.2)
            if not self.active:
                continue
            try:
                phase = (await asyncio.to_thread(self.cammy.speaker_status)).get('phase')
            except Exception:
                continue
            if phase not in ('running', 'queued', 'starting', 'playing', 'cancelling'):
                self.active = False
                self.busy_until = max(self.busy_until, time.monotonic() + self.tail)
                if phase not in ('finished', 'cancelled'):
                    print('[speaker] stream ended: %s' % phase, flush=True)


class CammyInputs:
    """Lease + preview session on the 0.5.0 portal: Cammy's mic stream and 1 fps frames."""

    def __init__(self, endpoint, token, client=None):
        # On rc3 one portal serves mic, video, speaker and face under one lease, so the
        # speaker's client is shared; on the 0.5.0 + dev speaker pair each has its own.
        self.cammy = client or Cammy(endpoint, token, agent='cammy-live-input', timeout=10)
        self.owns_lease = client is None
        self.rtsp = None
        self.frames = None
        FRAME_DIR.mkdir(mode=0o700, exist_ok=True)
        self.frame_path = FRAME_DIR / 'latest.jpg'
        self.pid_path = FRAME_DIR / 'grabber.pid'

    def _kill_stale_grabber(self):
        try:
            pid = int(self.pid_path.read_text())
            cmdline = Path('/proc/%d/cmdline' % pid).read_bytes()
        except (OSError, ValueError):
            return
        if b'ffmpeg' in cmdline and str(self.frame_path).encode() in cmdline:
            os.kill(pid, signal.SIGKILL)
            print('[live] killed stale frame grabber %d' % pid, flush=True)

    def start(self):
        if self.owns_lease:
            self.cammy.acquire(ttl=60)
        self.rtsp = self.cammy.preview_start()['rtsp_url']
        deadline = time.monotonic() + 20
        while not self.cammy.preview_status().get('sdp_ready'):
            if time.monotonic() > deadline:
                raise RuntimeError('preview did not become ready')
            time.sleep(0.5)
        self.frame_path.unlink(missing_ok=True)
        self._kill_stale_grabber()
        # The stock RTSP server feeds one client at a time, so a leaked grabber blinds the
        # next session: tie ffmpeg's life to ours and record its PID.
        self.frames = subprocess.Popen(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp', '-timeout', '10000000',
             '-i', self.rtsp, '-vf', 'fps=1,scale=768:-2', '-q:v', '5', '-f', 'image2', '-update', '1', '-y',
             str(self.frame_path)], stdin=subprocess.DEVNULL, preexec_fn=_die_with_parent)
        self.pid_path.write_text(str(self.frames.pid))

    def prepare_media(self):
        """rc3 leaves native media uninitialized until asked, once per boot. The portal
        accepts the request only while no control lease is held, so call this first."""
        try:
            status = self.cammy.media_status()
        except CammyError:
            return  # 0.5.0 portal: media starts at boot
        if status.get('ready') or status.get('phase') in ('ready', 'prepared'):
            return
        print('[live] preparing native media (once per boot): %s' % status.get('phase'), flush=True)
        if status.get('phase') == 'unprepared':
            self.cammy.prepare_media()
        deadline = time.monotonic() + 90
        status = {}
        while time.monotonic() < deadline:
            try:
                status = self.cammy.media_status()
            except OSError:  # native init can stall the camera's one CPU for seconds
                time.sleep(2)
                continue
            if status.get('ready') or status.get('phase') in ('ready', 'prepared'):
                print('[live] media ready', flush=True)
                return
            if status.get('phase') in ('failed', 'cancelled', 'requires-recovery') or status.get('requires_recovery'):
                raise RuntimeError('media preparation failed: %s' % status)
            time.sleep(1)
        raise RuntimeError('media preparation timed out: %s' % status)

    def newest_frame(self, max_age=4.0):
        try:
            if time.time() - self.frame_path.stat().st_mtime <= max_age:
                return self.frame_path.read_bytes()
        except FileNotFoundError:
            pass
        return None

    def stop(self):
        if self.frames and self.frames.poll() is None:
            self.frames.terminate()
            try:
                self.frames.wait(3)
            except subprocess.TimeoutExpired:
                self.frames.kill()
                self.frames.wait(3)
        self.pid_path.unlink(missing_ok=True)
        for step in (self.cammy.preview_stop,) + ((self.cammy.release,) if self.owns_lease else ()):
            try:
                step()
            except Exception:
                pass
        self.frame_path.unlink(missing_ok=True)


class Face:
    """Cammy's LCD face follows the conversation: awake (neutral) while connected, happy
    while speaking, doubt while looking. Without a session the native service sleeps."""

    def __init__(self, cammy):
        self.cammy = cammy
        self.shown = None
        self.looking_until = 0.0
        self.hidden = False      # the stock live view shows while Adam takes a picture/video
        try:
            self.available = bool(cammy.face_status().get('available'))
        except CammyError:
            self.available = False

    def set(self, expression):
        if self.available and expression != self.shown:
            try:
                if expression == 'hide':
                    self.cammy.face(None, 'hide')
                else:
                    self.cammy.face(expression, 'show')
                self.shown = expression
            except CammyError as exc:
                print('[face] %s refused: %s' % (expression, exc), flush=True)

    async def run(self, speaker):
        while True:
            if self.hidden:
                want = 'hide'
            elif time.monotonic() < self.looking_until:
                want = 'doubt'
            elif speaker.muted():
                want = 'happy'
            else:
                want = 'neutral'
            await asyncio.to_thread(self.set, want)
            await asyncio.sleep(0.3)


class Camera:
    """take_picture / take_video (host/cammy_capture.py) off the event loop. Each result
    goes to Gemini as a [Camera] turn once Cammy is quiet."""

    def __init__(self, capture):
        self.capture = capture
        self.results = asyncio.Queue()
        self.task = None

    def start(self, kind):
        if self.capture.busy or (self.task and not self.task.done()):
            return False
        self.task = asyncio.create_task(self._run(kind))
        return True

    async def _run(self, kind):
        result = await asyncio.to_thread(self.capture.run, kind)
        print('[camera] %s -> %s' % (kind, result.get('error') or '%s (%s bytes, %s s)' % (
            result['path'], result['size'], result['seconds'])), flush=True)
        await self.results.put(result)


class FaceHooks:
    """Capture's face control: through the Face loop when there is one, else nothing."""

    def __init__(self, face):
        self.face = face

    def hide_face(self):
        if self.face:
            self.face.hidden = True

    def show_face(self):
        if self.face:
            self.face.hidden = False


async def deliver_camera(session, speaker, link, camera):
    """A finished picture or video goes to Gemini with its images, when Cammy is quiet."""
    import cammy_capture
    while True:
        result = await camera.results.get()
        while link.in_turn or speaker.muted():
            await asyncio.sleep(0.2)
        parts = [types.Part(text=cammy_capture.gemini_text(result))]
        parts += [types.Part(inline_data=types.Blob(mime_type='image/jpeg', data=j)) for j in result.get('gemini') or []]
        try:
            await session.send_client_content(turns=types.Content(role='user', parts=parts), turn_complete=True)
        except Exception:
            camera.results.put_nowait(result)
            raise


async def keep_leases(clients):
    while True:
        await asyncio.sleep(15)
        for client in clients:
            await asyncio.to_thread(client.renew, 60)


async def pump_mic(chunks, queue):
    """Moves mic chunks into a small queue that outlives any one Gemini connection.

    While reconnecting nobody reads the queue; the oldest chunks are dropped so the
    model never receives stale speech."""
    async for chunk in chunks:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(chunk)


async def send_mic(session, speaker, queue):
    silence = bytes(3200)
    while True:
        chunk = await queue.get()
        data = silence if speaker.muted() else chunk
        await session.send_realtime_input(audio=types.Blob(data=data, mime_type='audio/pcm;rate=16000'))


async def cammy_mic(inputs):
    """Cammy's microphone, reopened whenever the portal ends the stream.

    The 0.5.0 portal caps one /v1/audio/stream at 600 s (CAMMY_AUDIO_LIMIT), so a long
    conversation spans several streams; the gap is under a second. Gives up after five
    failed opens in a row or three streams that each die within 30 s."""
    open_failures = short_streams = 0
    while True:
        try:
            stream = await asyncio.to_thread(inputs.cammy.open_audio_stream)
        except (CammyError, OSError) as exc:
            open_failures += 1
            if open_failures >= 5:
                raise RuntimeError('Cammy microphone will not reopen: %r' % exc)
            await asyncio.sleep(open_failures)  # the portal reaps the old recorder first
            continue
        open_failures, opened = 0, time.monotonic()
        try:
            while True:
                chunk = await asyncio.to_thread(stream.read, 3200)
                if len(chunk) < 3200:
                    break
                yield chunk
        finally:
            stream.close()
        lived = time.monotonic() - opened
        short_streams = short_streams + 1 if lived < 30 else 0
        if short_streams >= 3:
            raise RuntimeError('Cammy microphone stream keeps ending')
        print('[live] mic stream ended after %.0f s; reopening' % lived, flush=True)
        await asyncio.sleep(0.3)


async def p15_mic():
    out = subprocess.run(['pw-cli', 'ls', 'Node'], capture_output=True, text=True).stdout
    source = next((t for t in out.split('"') if t.startswith('alsa_input.usb-Poly')), None)
    if not source:
        raise SystemExit('P15 microphone not found')
    proc = await asyncio.create_subprocess_exec(
        'pw-record', '--raw', '--target', source, '--rate', '16000', '--channels', '1', '--format', 's16', '-',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        while True:
            yield await proc.stdout.readexactly(3200)
    finally:
        proc.kill()



def agent_label():
    """Agent ID for Cammy's Agent screen (0.7): the brain thread when there is one
    (cammy_brain.thread_id()), else this PC. Portal labels are printable ASCII without quotes."""
    import socket
    thread = cammy_brain.thread_id()
    text = 'Gemini Live, brain %s' % thread if thread else 'Gemini Live on %s' % socket.gethostname()
    return ''.join(c for c in text if 32 <= ord(c) < 127 and c not in '"\\')[:80]


class Link:
    """Gemini connection state kept across reconnects."""
    handle = None      # newest session resumption handle
    go_away = False    # server announced the connection will close
    in_turn = False    # Cammy is mid-reply; do not rotate now
    reconnects = 0


async def receive_loop(session, speaker, inputs, link, face=None, brain=None, log=None, camera=None):
    buf = bytearray()
    first = True
    said, heard = [], []

    def emit():
        nonlocal buf, first
        if buf:
            speaker.queue.put_nowait((speaker.generation, bytes(buf)))
            buf = bytearray()
            first = False

    while True:
        received = 0
        async for msg in session.receive():
            received += 1
            if msg.session_resumption_update:
                update = msg.session_resumption_update
                if update.resumable and update.new_handle:
                    link.handle = update.new_handle
            if msg.go_away:
                link.go_away = True
                print('[gemini] connection ending soon (%s left)' % msg.go_away.time_left, flush=True)
            if msg.tool_call:
                responses = []
                for call in msg.tool_call.function_calls or []:
                    if call.name == 'ask_brain':
                        request = str((call.args or {}).get('request') or '').strip()
                        try:
                            if not (brain and request):
                                raise RuntimeError('no brain or empty request')
                            await brain.ask(request)
                            result = {'result': 'Passed to your brain. Say so in a few words; its answer comes later as a [Brain] message.'}
                        except Exception as e:  # noqa: BLE001 - spoken as an error, never fatal
                            result = {'error': 'brain unreachable: %s' % str(e)[:80]}
                        print('[tool] ask_brain %r -> %s' % (request[:100], next(iter(result))), flush=True)
                        responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result))
                        continue
                    if call.name in ('take_picture', 'take_video'):
                        kind = 'photo' if call.name == 'take_picture' else 'video'
                        if not camera:
                            result = {'error': 'taking pictures is not available'}
                        elif not camera.start(kind):
                            result = {'error': 'already taking one; wait for it to finish'}
                        elif kind == 'photo':
                            result = {'result': 'Live view is on. Tell Adam to frame the shot and press the red record button.'}
                        else:
                            result = {'result': 'Live view is on. Tell Adam to press the red record button to start, and again to stop.'}
                        print('[tool] %s -> %s' % (call.name, next(iter(result.values()))), flush=True)
                        responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result))
                        continue
                    if face and call.name == 'look':
                        face.looking_until = time.monotonic() + 3
                    frame = inputs.newest_frame() if (inputs and call.name == 'look') else None
                    # The photo must travel inside the tool response: a separate realtime video
                    # frame is ignored and Gemini invents a scene (probe 2026-11-16).
                    parts = None
                    if frame:
                        result = {'result': 'Photo attached. Describe only what it shows.'}
                        parts = [types.FunctionResponsePart(inline_data=types.FunctionResponseBlob(
                            mime_type='image/jpeg', data=frame))]
                    else:
                        result = {'error': 'camera unavailable'}
                    print('[tool] %s -> %s' % (call.name, 'frame %d bytes' % len(frame) if frame else 'no frame'), flush=True)
                    responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result, parts=parts))
                await session.send_tool_response(function_responses=responses)
            sc = msg.server_content
            if not sc:
                continue
            if sc.input_transcription and sc.input_transcription.text:
                heard.append(sc.input_transcription.text)
            if sc.output_transcription and sc.output_transcription.text:
                said.append(sc.output_transcription.text)
            if sc.interrupted:
                buf = bytearray()
                await asyncio.to_thread(speaker.flush)
            if sc.model_turn:
                for part in sc.model_turn.parts or []:
                    if part.inline_data and part.inline_data.data:
                        link.in_turn = True
                        buf += part.inline_data.data
                        if len(buf) >= (FIRST_CHUNK_S if first else CHUNK_S) * RATE_OUT * 2:
                            emit()
            if sc.turn_complete or sc.interrupted:
                link.in_turn = False
            if sc.turn_complete:
                emit()
                first = True
                if heard:
                    print('heard: ' + ''.join(heard).strip(), flush=True)
                    if log is not None:
                        log.append('Adam: ' + ''.join(heard).strip())
                if said:
                    print('cammy: ' + ''.join(said).strip(), flush=True)
                    if log is not None:
                        log.append('Cammy: ' + ''.join(said).strip())
                heard, said = [], []
        if not received:
            raise ConnectionError('Gemini connection closed')


async def rotate_when_idle(link, speaker, rotate_s):
    """Returns when this connection should be replaced: the server sent go_away, or the
    test-only --rotate timer ran out. Waits until Cammy is not mid-reply."""
    deadline = time.monotonic() + rotate_s if rotate_s else None
    while True:
        await asyncio.sleep(0.2)
        due = link.go_away or (deadline and time.monotonic() >= deadline)
        if due and not link.in_turn and not speaker.muted():
            return 'go_away' if link.go_away else 'rotate timer'


async def deliver_brain(session, speaker, link, brain):
    """Speak the brain's replies: each waits until Cammy is not talking, then goes to Gemini
    as a [Brain] turn. A reply that fails to send is kept for the next connection."""
    while True:
        text = await brain.replies.get()
        while link.in_turn or speaker.muted():
            await asyncio.sleep(0.2)
        try:
            await session.send_client_content(turns=types.Content(role='user', parts=[types.Part(
                text='[Brain] ' + text)]), turn_complete=True)
        except Exception:
            brain.replies.put_nowait(text)
            raise
        print('[brain] -> ' + text[:160], flush=True)


async def converse(client, args, config, speaker, inputs, micq, link, face=None, brain=None, log=None, camera=None):
    """One Gemini Live conversation over as many connections as it takes (10 min each).

    Each new connection resumes with the newest handle, so the dialogue context survives."""
    failures, greet = 0, args.greet
    while True:
        link.go_away = link.in_turn = False
        cfg = config.model_copy(update={'session_resumption': types.SessionResumptionConfig(handle=link.handle)})
        try:
            async with client.aio.live.connect(model=args.model, config=cfg) as session:
                if link.reconnects:
                    print('[live] resumed (connection %d, handle %s)' % (
                        link.reconnects + 1, 'kept' if link.handle else 'MISSING: context lost'), flush=True)
                else:
                    print('[live] ready: %s voice=%s mic=%s level=%s for %.1f min' % (
                        args.model, args.voice, args.mic, args.level, args.minutes), flush=True)
                tasks = [asyncio.create_task(c) for c in (
                    receive_loop(session, speaker, inputs, link, face, brain, log, camera), send_mic(session, speaker, micq),
                    rotate_when_idle(link, speaker, args.rotate))]
                if brain:
                    tasks.append(asyncio.create_task(deliver_brain(session, speaker, link, brain)))
                if camera:
                    tasks.append(asyncio.create_task(deliver_camera(session, speaker, link, camera)))
                if greet:
                    greet = False
                    await session.send_client_content(turns=types.Content(role='user', parts=[types.Part(
                        text='(You were just switched on. Greet Adam in one short sentence.)')]), turn_complete=True)
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:  # also on cancel (Stop / --minutes), so no task outlives its connection
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for task in done:
                    if task.exception():
                        raise task.exception()
                reason = next(iter(done)).result()
            failures = 0
            # The server needs a moment to drop the old connection; resuming at once is
            # refused with 1008 "already connected to an existing client".
            await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            if failures >= 3:
                raise
            reason = 'error %r' % exc
            await asyncio.sleep(failures)
        link.reconnects += 1
        print('[live] reconnecting: %s' % str(reason)[:160], flush=True)


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--model', default='gemini-3.8-live')
    ap.add_argument('--voice', default='Achernar')
    ap.add_argument('--level', default='normal', choices=sorted(cammy_voicefx.LEVELS))
    ap.add_argument('--mic', default='cammy', choices=['cammy', 'p15'])
    ap.add_argument('--portal', default=__import__('c3.discover', fromlist=['portal']).portal())
    ap.add_argument('--speaker', default=None, help='speaker portal (default: same as --portal, rc3)')
    ap.add_argument('--minutes', type=float, default=5)
    ap.add_argument('--tail', type=float, default=0.8, help='seconds the mic stays muted after Cammy speaks')
    ap.add_argument('--greet', action='store_true', help='Cammy speaks first')
    ap.add_argument('--no-brain', action='store_true', help='run without the brain thread')
    ap.add_argument('--rotate', type=float, default=0,
                    help='test only: replace the Gemini connection every N seconds (resumption check)')
    args = ap.parse_args()

    token, key = credentials()
    client = genai.Client(api_key=key, http_options={'api_version': 'v1alpha'})
    thread = cammy_brain.thread_id()
    brain = cammy_brain.Brain(thread) if thread and not args.no_brain else None
    try:  # the face pack decides who Gemini is ("Hi, I'm Pikachu on CammyOS!")
        reported = Cammy(args.portal, token, agent='cammy-live-probe', timeout=5).state().get('face_pack')
    except Exception:  # noqa: BLE001 - an unreachable portal fails later, with a clearer error
        reported = None
    pack_id, _ = cammy_persona.pack(reported)
    print('[live] face pack: %s%s' % (pack_id, '' if reported == pack_id else ' (portal reported %r)' % reported), flush=True)
    persona = cammy_persona.persona(pack_id)
    if brain:
        persona += BRAIN_PERSONA
        context = cammy_brain.brief()
        if context:
            persona += ' What your brain wants you to know right now: ' + context
    log = []
    import cammy_capture
    from cammy_vision import VisionError
    try:  # the stock camera API (port 8082) needs Cammy's numeric address
        native = cammy_capture.Native(args.portal)
    except VisionError as e:
        native = None
        print('[camera] take_picture/take_video off: %s' % e, flush=True)
    camera_tools = [TAKE_PICTURE, TAKE_VIDEO] if native else []
    config = types.LiveConnectConfig(
        response_modalities=['AUDIO'],
        system_instruction=persona,
        tools=[types.Tool(function_declarations=[LOOK] + camera_tools + ([ASK_BRAIN] if brain else []))],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=args.voice))),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        session_resumption=types.SessionResumptionConfig())
    shared = args.speaker in (None, args.portal)
    speaker = Speaker(args.speaker or args.portal, token, args.level, args.tail)
    inputs = CammyInputs(args.portal, token, client=speaker.cammy if shared else None)
    await asyncio.to_thread(inputs.prepare_media)
    speaker.cammy.acquire(ttl=60)
    face = Face(speaker.cammy) if shared else None
    camera = Camera(cammy_capture.Capture(native, FaceHooks(face))) if native else None
    try:
        await asyncio.to_thread(inputs.start)
        mic = cammy_mic(inputs) if args.mic == 'cammy' else p15_mic()
        link, micq = Link(), asyncio.Queue(maxsize=15)
        leases = [speaker.cammy] if shared else [speaker.cammy, inputs.cammy]
        tasks = [asyncio.create_task(c) for c in (
            speaker.run(), speaker.watch(), keep_leases(leases), pump_mic(mic, micq),
            converse(client, args, config, speaker, inputs, micq, link, face, brain, log, camera))]
        if brain:
            tasks.append(asyncio.create_task(brain.watch()))
            print('[brain] linked to thread %s' % brain.thread, flush=True)
        if face and face.available:
            tasks.append(asyncio.create_task(face.run(speaker)))
            print('[face] awake; follows the conversation', flush=True)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(tasks + [stopper], timeout=args.minutes * 60,
                                     return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not stopper and not task.cancelled() and task.exception():
                print('[live] stopped: %r' % task.exception(), flush=True)
        for task in tasks + [stopper]:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        print('[live] connections: %d' % (link.reconnects + 1), flush=True)
    finally:
        try:
            speaker.cammy.speaker_cancel()
        except Exception:
            pass
        try:
            speaker.cammy.release()
        except Exception:
            pass
        inputs.stop()
        if brain and log:
            try:  # the conversation goes to the brain for memory
                await asyncio.wait_for(brain.transcript(log), 20)
            except Exception as e:  # noqa: BLE001
                print('[brain] transcript not sent: %s' % e, flush=True)
        print('[live] ended: %d speaker chunks' % speaker.chunks, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
