"""Cammy Control Center (host/c3): body supervision, voice gating, transcript, API, voice chain.

No hardware, no network: a scripted portal client and a scripted Gemini session.
    .venv-live/bin/python -m unittest tests.test_c3
"""
import asyncio
import collections
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
from c3 import body as B  # noqa: E402
from c3 import voice as V  # noqa: E402
from c3.transcript import Transcript  # noqa: E402
from cammy import Cammy, CammyError  # noqa: E402
import cammy_voicefx  # noqa: E402

Ev = collections.namedtuple('Ev', 'kind pcm')


def run(coro, timeout=10):
    return asyncio.run(asyncio.wait_for(coro, timeout))


# ---- voice chain ------------------------------------------------------------------------
class StreamTests(unittest.TestCase):
    def speech(self, seconds=6):
        t = np.arange(24000 * seconds) / 24000
        return (np.sin(2 * np.pi * (220 + 200 * t) * t) * 9000 * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype('<i2').tobytes()

    def feed(self, pcm, block):
        s, out = cammy_voicefx.Stream(), b''
        for i in range(0, len(pcm), block):
            out += s.feed(pcm[i:i + block])
        return np.frombuffer(out + s.feed(b'', end=True), '<i2')

    def test_block_size_does_not_change_the_output(self):
        pcm = self.speech()
        a, b, c = self.feed(pcm, 48000), self.feed(pcm, 96000), self.feed(pcm, 7778)
        self.assertEqual(len(a), len(b))
        self.assertTrue(np.array_equal(a[48000:], b[48000:]) and np.array_equal(a[48000:], c[48000:]))

    def test_no_padding_before_speech_one_tail_no_gaps_peak_capped(self):
        y = self.feed(self.speech(), 48000).astype(float)
        self.assertTrue(np.any(y[:1600] != 0) and np.all(y[-1600:] == 0))
        body = np.abs(y[1600 + 800:-1600 - 800])
        quiet = np.convolve(body < 30, np.ones(160), 'valid') == 160      # 10 ms of near silence
        self.assertFalse(quiet.any())
        self.assertLessEqual(np.abs(y).max(), cammy_voicefx.LEVELS['normal'])

    def test_leading_silence_is_trimmed_and_bass_cut(self):
        s = cammy_voicefx.Stream()
        self.assertEqual(s.feed(bytes(48000)), b'')
        tone = (np.sin(2 * np.pi * 100 * np.arange(48000) / 24000) * 9000).astype('<i2').tobytes()
        s2 = cammy_voicefx.Stream()
        s2.started, s2.p_in = True, 1.0
        y = s2._filter(np.frombuffer(tone, '<i2').astype(float))
        self.assertLess(np.abs(y[3000:]).max(), 9000 * 0.01)


# ---- body -------------------------------------------------------------------------------
class FakeStream:
    def __init__(self, chunks, stall=False):
        self.chunks, self.stall = list(chunks), stall
        self.closes = 0

    def read(self, n):
        if self.chunks:
            return self.chunks.pop(0)
        if self.stall:
            raise TimeoutError('timed out')
        return b''

    def close(self):
        self.closes += 1


class FakePortal:
    def __init__(self):
        self.log, self.renew_errors, self.preview_phase = [], [], 'sdp-ready'
        self.streams = []

    def state(self):
        return {'owner': None, 'face_pack': 'pikachu'}

    def media_status(self):
        return {'ready': True}

    def acquire(self, ttl):
        self.log.append('acquire')

    def renew(self, ttl):
        if self.renew_errors:
            raise self.renew_errors.pop(0)
        self.log.append('renew')

    def preview_start(self):
        self.log.append('preview_start')
        return {'rtsp_url': 'rtsp://cammy/live'}

    def preview_status(self):
        return {'sdp_ready': True, 'phase': self.preview_phase, 'preview_active': True}

    def preview_stop(self):
        self.log.append('preview_stop')

    def speaker_cancel(self):
        self.log.append('speaker_cancel')

    def open_audio_stream(self):
        if not self.streams:
            raise CammyError(503, 'no stream')
        return self.streams.pop(0)

    def release(self):
        self.log.append('release')

    def face(self, expression, action):
        self.log.append(('face', expression, action))

    def face_status(self):
        return {'available': True}


class FastClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class BodyTests(unittest.TestCase):
    def setUp(self):
        self.sleep = asyncio.sleep
        self.portal = FakePortal()

        async def fast(s, *a):          # the body's waits run instantly, advancing the clock
            self.clock.t += s
            await self.sleep(0)
        self.clock = FastClock()
        B.asyncio.sleep = fast
        self.body = B.Body('http://x', 't', client=self.portal, clock=self.clock)

    def tearDown(self):
        B.asyncio.sleep = self.sleep

    def test_transient_renew_failure_is_retried_without_losing_the_lease(self):
        self.portal.renew_errors = [OSError('wifi blip')]

        async def go():
            task = asyncio.create_task(self.body.run())
            while self.portal.log.count('renew') < 3:
                await self.sleep(0)
            task.cancel()
        run(go())
        self.assertEqual(self.portal.log.count('acquire'), 1)
        self.assertEqual(self.body.state['renew_failures'], 1)
        self.assertEqual(self.body.state['lease_losses'], 0)

    def test_lost_lease_and_failed_preview_are_brought_back(self):
        self.portal.renew_errors = [CammyError(409, 'expired control lease cannot be renewed')]
        healthy_status = self.portal.preview_status

        def preview_status():
            if self.body.epoch == 2 and self.body.up.is_set():
                return {'phase': 'failed', 'preview_active': False}
            return healthy_status()

        self.portal.preview_status = preview_status

        async def go():
            task = asyncio.create_task(self.body.run())
            while self.body.epoch < 3:
                await self.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        run(go())
        self.assertEqual(self.body.state['lease_losses'], 1)
        self.assertEqual(self.portal.log.count('release'), 1)
        self.assertNotIn('preview_stop', self.portal.log)
        self.assertNotIn('speaker_cancel', self.portal.log)

    def test_stale_preview_rebinds_without_touching_the_new_boot(self):
        class Portal(Cammy):
            def __init__(self):
                super().__init__('http://unused', 'fixture', agent='c3')
                self.boot = 'a' * 32
                self.requests = []

            def request(self, method, path, body=None):
                self.requests.append((path, self._boot_binding, self._lease_binding))
                if self._boot_binding not in (None, self.boot):
                    raise CammyError(409, 'stale camera boot binding')
                if path == '/v1/state':
                    return {'boot_id': self.boot, 'owner': None}
                if path == '/v1/media/status':
                    return {'ready': True}
                if path == '/v1/session':
                    return {'lease_id': ('1' if self.boot.startswith('a') else '2') * 32}
                if path == '/v1/preview/start':
                    return {'rtsp_url': 'rtsp://unused'}
                if path == '/v1/preview/status':
                    return {'phase': 'sdp-ready', 'sdp_ready': True}
                raise AssertionError(path)

        portal = Portal()
        portal._speaker_peak = 10000
        self.body = B.Body('http://unused', 'fixture', client=portal, clock=self.clock)

        async def go():
            task = asyncio.create_task(self.body.run())
            try:
                await self.body.up.wait()
                portal.boot = 'b' * 32
                while self.body.epoch < 2:
                    await self.sleep(0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        run(go())
        old_status = ('/v1/preview/status', 'a' * 32, '1' * 32)
        last_old_status = max(i for i, call in enumerate(portal.requests) if call == old_status)
        self.assertEqual(portal.requests[last_old_status + 1], ('/v1/state', None, None))
        self.assertEqual(portal._boot_binding, 'b' * 32)
        self.assertEqual(portal._lease_binding, '2' * 32)
        self.assertIsNone(portal._speaker_peak)
        self.assertEqual(self.body.state['rebinds'], 1)
        self.assertEqual(self.body.state['lease_losses'], 1)
        self.assertEqual(self.body.state['last_error'], '')

    def test_shutdown_waits_for_acquire_and_releases_only_once(self):
        acquiring, finish = threading.Event(), threading.Event()
        acquire = self.portal.acquire

        def blocking_acquire(ttl):
            acquiring.set()
            if not finish.wait(2):
                raise TimeoutError('test acquire was not unblocked')
            acquire(ttl)

        self.portal.acquire = blocking_acquire

        async def go():
            task = asyncio.create_task(self.body.run())
            try:
                self.assertTrue(await asyncio.to_thread(acquiring.wait, 2))
                task.cancel()
                await self.sleep(0)
                self.assertFalse(task.done())
            finally:
                finish.set()
                await asyncio.gather(task, return_exceptions=True)
            self.body.release()
            self.body.release()

        run(go())
        self.assertEqual(self.portal.log, ['acquire', 'release'])
        self.assertFalse(self.body.up.is_set())

    def test_streams_cut_after_working_reopen_at_once_and_only_dead_ones_back_off(self):
        chunk = bytes(B.CHUNK)
        streams = [FakeStream([chunk]) for _ in range(5)]
        self.portal.streams = list(streams)
        open_stream = self.portal.open_audio_stream
        opens = []

        def track_open():
            opens.append(self.clock())
            return open_stream()

        self.portal.open_audio_stream = track_open
        lasted = [10, 10, 10, 1]         # three Wi-Fi 'slow-reader' cuts, then a dead stream

        async def go():
            self.body.up.set()
            self.body.epoch = 1
            stream = self.body.mic()
            try:
                self.assertEqual(await anext(stream), chunk)
                for i in range(4):
                    self.clock.t += lasted[i]
                    self.assertEqual(await anext(stream), chunk)   # stream i ends, i + 1 opens
            finally:
                await stream.aclose()

        run(go())
        gaps = [round(b - a - d, 2) for a, b, d in zip(opens, opens[1:], lasted)]
        self.assertEqual(gaps[:3], [B.MIC_REOPEN_S] * 3)
        self.assertEqual(gaps[3], B.BACKOFF[0])

    def test_normal_stream_cap_reopens_without_restarting_the_preview(self):
        chunk = bytes(B.CHUNK)
        ended = FakeStream([chunk])
        second = FakeStream([chunk])
        self.portal.streams = [ended, second]
        open_stream = self.portal.open_audio_stream
        opens = []

        def track_open():
            opens.append((self.clock(), ended.closes))
            return open_stream()

        self.portal.open_audio_stream = track_open

        async def go():
            self.body.up.set()
            self.body.epoch = 1
            stream = self.body.mic()
            try:
                self.assertEqual(await anext(stream), chunk)
                self.clock.t += B.MIC_STREAM_CAP_S
                self.assertEqual(await anext(stream), chunk)
            finally:
                await stream.aclose()

        run(go())
        self.assertEqual(opens, [(1000, 0), (1000 + B.MIC_STREAM_CAP_S + B.MIC_REOPEN_S, 1)])
        self.assertEqual(second.closes, 1)
        self.assertEqual(self.body.epoch, 1)
        self.assertTrue(self.body.up.is_set())
        self.assertEqual(self.portal.log, [])

    def test_short_streams_back_off_even_when_open_succeeds(self):
        self.portal.streams = [FakeStream([]), FakeStream([]), FakeStream([bytes(B.CHUNK)])]
        open_stream = self.portal.open_audio_stream
        opens = []

        def track_open():
            opens.append(self.clock())
            return open_stream()

        self.portal.open_audio_stream = track_open

        async def go():
            self.body.up.set()
            stream = self.body.mic()
            try:
                self.assertEqual(await anext(stream), bytes(B.CHUNK))
            finally:
                await stream.aclose()

        run(go())
        self.assertEqual(opens, [1000, 1001, 1003])

    def test_trickling_mic_cannot_keep_a_chunk_read_open(self):
        class Stream:
            def read(self, n):
                raise AssertionError('buffered read can stall across multiple socket reads')

            def read1(inner, n):
                self.clock.t += 4
                return b'a'

        with self.assertRaisesRegex(TimeoutError, 'complete microphone chunk'):
            self.body._read_mic(Stream())
        self.assertEqual(self.clock(), 1012)

    def test_mic_survives_stream_end_stall_and_open_failure(self):
        chunk = bytes(3200)
        self.portal.streams = [FakeStream([chunk, chunk]), FakeStream([chunk], stall=True), FakeStream([chunk] * 3)]

        async def go():
            self.body.up.set()
            got = []
            async for c in self.body.mic():
                got.append(c)
                if len(got) == 6:
                    return got
        self.assertEqual(len(run(go())), 6)
        self.assertEqual(self.body.state['mic_reopens'], 2)

    def test_physical_mute_closes_mic_and_waits_to_reopen(self):
        first = FakeStream([b'a' * B.CHUNK, b'b' * B.CHUNK])
        second = FakeStream([b'c' * B.CHUNK])
        self.portal.streams = [first, second]

        async def go():
            self.body.up.set()
            self.body._capture_status({'capture_sequence': 2})
            stream = self.body.mic()
            pending = None
            try:
                self.assertEqual(await anext(stream), b'a' * B.CHUNK)
                self.body._capture_status({'capture_sequence': 3})
                pending = asyncio.create_task(anext(stream))
                await self.sleep(0.05)
                self.assertEqual(first.closes, 1)
                self.assertEqual(self.portal.streams, [second])
                self.assertFalse(pending.done())
                self.body._capture_status({'capture_sequence': 4})
                self.assertEqual(await asyncio.wait_for(pending, 1), b'c' * B.CHUNK)
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await stream.aclose()
        run(go())

    def test_speaker_drops_the_rest_of_a_failed_reply(self):
        async def go():
            finished = asyncio.Event()
            loop = asyncio.get_running_loop()
            attempts = []

            def play(pcm, append):
                attempts.append(pcm)
                if len(attempts) == 1:
                    raise CammyError(503, 'speaker cleanup requires recovery')
                loop.call_soon_threadsafe(finished.set)

            self.portal.play_pcm = play
            self.body.up.set()
            speaker = B.Speaker(self.body)
            runner = asyncio.create_task(speaker.run())
            try:
                passthrough = SimpleNamespace(feed=lambda pcm, end: pcm)
                with patch.object(B.cammy_voicefx, 'Stream', return_value=passthrough):
                    speaker.say(b'a' * B.CHUNK)
                    speaker.say(b'b' * B.CHUNK)
                    speaker.say(b'c' * B.CHUNK, end=True)
                    speaker.say(b'd' * B.CHUNK, end=True)
                    await finished.wait()
                self.assertEqual(attempts, [b'a' * B.CHUNK, b'd' * B.CHUNK])
            finally:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

        run(go())


# ---- voice ------------------------------------------------------------------------------
class FakeSession:
    def __init__(self, script):
        self.sent, self.script = [], script

    async def send_realtime_input(self, **kw):
        key = next(k for k, v in kw.items() if v is not None)
        self.sent.append((key, len(kw[key].data) if key == 'audio' else None))

    async def send_client_content(self, turns, turn_complete):
        self.sent.append(('turn', turns.parts[0].text))

    async def send_tool_response(self, function_responses):
        self.sent.append(('tool_response', [r.name for r in function_responses]))

    async def receive(self):
        while True:
            if self.script:
                yield self.script.pop(0)
            else:
                await asyncio.sleep(0.01)


class FakeLive:
    def __init__(self, sessions, refuse=0):
        self.sessions, self.configs, self.refuse = sessions, [], refuse

    def connect(self, model, config):
        self.configs.append(config)
        live = self

        class Ctx:
            async def __aenter__(self):
                if live.refuse:
                    live.refuse -= 1
                    raise ConnectionError('resume refused')
                return live.sessions.pop(0) if live.sessions else FakeSession([])

            async def __aexit__(self, *a):
                return False
        return Ctx()


class FakeClient:
    def __init__(self, live):
        self.aio = type('A', (), {'live': live})()


class Quiet:
    active = False
    speaking = False

    def __init__(self):
        self.said, self.flushed = [], 0

    def muted(self):
        return False

    def say(self, pcm, end=False):
        self.said.append((len(pcm), end))

    def flush(self):
        self.flushed += 1


class Thing:
    awake = wanted = False
    looking_until = 0.0

    async def look(self):
        return b'\xff\xd8jpeg'


def msg(**kw):
    from google.genai import types
    return types.LiveServerMessage(**kw)


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.transcript = Transcript(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def voice(self, live, idle_s=0.3, clock=time.monotonic):
        self.speaker, self.listener = Quiet(), Quiet()
        return V.Voice(FakeClient(live), 'm', lambda: 'persona', 'v', self.speaker, Thing(), Thing(),
                       self.listener, self.transcript, idle_s=idle_s, clock=clock)

    def test_only_speech_is_sent_with_activity_markers_then_sleeps(self):
        session = FakeSession([])
        live = FakeLive([session])
        v = self.voice(live)

        async def go():
            task = asyncio.create_task(v.run())
            v.on_speech(Ev('start', bytes(6400)))
            v.on_speech(Ev('audio', bytes(3200)))
            v.on_speech(Ev('end', b''))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if v.stats['sleeps']:
                    break
            task.cancel()
        run(go())
        kinds = [k for k, _ in session.sent]
        self.assertEqual(kinds, ['activity_start', 'audio', 'audio', 'audio', 'activity_end'])
        self.assertAlmostEqual(v.stats['audio_s_sent'], 0.3)
        self.assertEqual(v.stats['sleeps'], 1)
        self.assertEqual(v.state, 'asleep')
        cfg = live.configs[0]
        self.assertTrue(cfg.realtime_input_config.automatic_activity_detection.disabled)

    def test_session_updates_do_not_extend_idle_or_reopen_after_a_turn(self):
        from google.genai import types

        class Keepalive(FakeSession):
            async def receive(self):
                while True:
                    await asyncio.sleep(0.05)
                    yield msg(session_resumption_update=types.LiveServerSessionResumptionUpdate(resumable=False))

        session = Keepalive([])
        live = FakeLive([session])
        v = self.voice(live, idle_s=0.25)

        async def go():
            task = asyncio.create_task(v.run())
            try:
                v.wake.set()
                while v.state != 'awake':
                    await asyncio.sleep(0.01)
                v.add_turn([types.Part(text='hello')])
                await asyncio.sleep(0.8)
                self.assertEqual(v.state, 'asleep')
                self.assertEqual(v.stats['turns'], 1)
                self.assertEqual(v.stats['connections'], 1)
                self.assertEqual(v.stats['audio_s_sent'], 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        run(go())

    def test_reply_audio_goes_to_the_speaker_and_transcript(self):
        from google.genai import types
        audio = types.Part(inline_data=types.Blob(mime_type='audio/pcm', data=bytes(24000 * 2 * 3)))
        session = FakeSession([
            msg(server_content=types.LiveServerContent(input_transcription=types.Transcription(text='hi cammy'))),
            msg(server_content=types.LiveServerContent(model_turn=types.Content(parts=[audio]))),
            msg(server_content=types.LiveServerContent(output_transcription=types.Transcription(text='hello'),
                                                       turn_complete=True)),
            msg(session_resumption_update=types.LiveServerSessionResumptionUpdate(resumable=True, new_handle='h1'))])
        v = self.voice(FakeLive([session]))

        async def go():
            task = asyncio.create_task(v.run())
            v.on_speech(Ev('start', bytes(3200)))
            v.on_speech(Ev('end', b''))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if v.stats['sleeps']:
                    break
            task.cancel()
        run(go())
        self.assertEqual(self.speaker.said[-1][1], True)
        self.assertEqual(sum(n for n, _ in self.speaker.said), 24000 * 2 * 3)
        self.assertEqual(v.handle, 'h1')
        lines, _ = self.transcript.since(0)
        self.assertEqual(lines, ['Adam: hi cammy', 'Cammy: hello'])

    def test_an_explicit_resume_refusal_starts_fresh_with_a_summary(self):
        self.transcript.add('adam', 'my cat is called Miso')
        live = FakeLive([FakeSession([])], refuse=1)
        v = self.voice(live)
        v.handle = 'old'
        orig = V.asyncio.sleep

        async def fast(s):
            await orig(0 if s >= 1 else s)
        V.asyncio.sleep = fast
        try:
            async def go():
                task = asyncio.create_task(v.run())
                v.wake.set()
                for _ in range(300):
                    await orig(0.01)
                    if len(live.configs) == 2:
                        break
                task.cancel()
            run(go())
        finally:
            V.asyncio.sleep = orig
        self.assertEqual(live.configs[0].session_resumption.handle, 'old')
        self.assertIsNone(live.configs[1].session_resumption.handle)
        self.assertIn('Miso', live.configs[1].system_instruction)
        self.assertEqual(v.stats['resume_refused'], 1)

    def test_barge_in_flushes_the_speaker(self):
        v = self.voice(FakeLive([]))
        self.speaker.active = True
        v.on_speech(Ev('start', bytes(3200)))
        self.assertEqual(self.speaker.flushed, 1)
        self.assertEqual(v.stats['barge_ins'], 1)

    def test_stale_utterance_is_dropped(self):
        now = [0.0]
        session = FakeSession([])
        v = self.voice(FakeLive([session]), idle_s=1000, clock=lambda: now[0])
        v.on_speech(Ev('start', bytes(3200)))
        v.on_speech(Ev('end', b''))
        now[0] = 60.0

        async def go():
            task = asyncio.create_task(v._send_speech(session))
            await asyncio.sleep(0.05)
            task.cancel()
        run(go())
        self.assertEqual(session.sent, [])
        self.assertEqual(v.stats['stale_dropped'], 1)

    def test_turns_wait_for_quiet_and_look_sends_the_frame(self):
        from google.genai import types
        call = types.FunctionCall(id='1', name='look', args={})
        session = FakeSession([msg(tool_call=types.LiveServerToolCall(function_calls=[call]))])
        v = self.voice(FakeLive([session]))

        async def go():
            task = asyncio.create_task(v.run())
            v.add_turn([types.Part(text='[Brain] done')], 'brain')
            for _ in range(200):
                await asyncio.sleep(0.01)
                if v.stats['sleeps']:
                    break
            task.cancel()
        run(go())
        self.assertIn(('turn', '[Brain] done'), session.sent)
        self.assertIn(('tool_response', ['look']), session.sent)


# ---- transcript -------------------------------------------------------------------------
class TranscriptTests(unittest.TestCase):
    def test_lines_files_marks_and_pruning(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / '2000-01-01.jsonl').write_text('{}\n')
            t = Transcript(d)
            self.assertFalse((Path(d) / '2000-01-01.jsonl').exists())
            t.add('adam', 'hello')
            t.add('tool', 'look -> frame')
            t.add('cammy', 'hi Adam')
            lines, mark = t.since(0)
            self.assertEqual(len(lines), 3)
            self.assertEqual(t.since(mark), ([], mark))
            self.assertEqual(t.recent_text(), 'Adam: hello\nCammy: hi Adam')
            day = next(Path(d).glob('*.jsonl'))
            self.assertEqual(json.loads(day.read_text().splitlines()[0])['text'], 'hello')


# ---- API --------------------------------------------------------------------------------
class ApiTests(unittest.TestCase):
    def test_token_required_and_turns_queue(self):
        from aiohttp.test_utils import TestClient, TestServer
        from c3 import api

        class C3:
            duplex = 'half'
            camera = None
            data_dir = '/nonexistent'

            def __init__(self, d):
                self.transcript = Transcript(d)
                self.voice = type('Vo', (), {'state': 'asleep', 'turns': [],
                                             'add_turn': lambda s, parts, label: s.turns.append(parts[0].text)})()

            def status(self):
                return {'service': 'Cammy Control Center'}

        async def go(d):
            c3 = C3(d)
            client = TestClient(TestServer(api.make_app(c3, 'secret')))
            await client.start_server()
            try:
                self.assertEqual((await client.get('/v1/status')).status, 401)
                h = {'Authorization': 'Bearer secret'}
                self.assertEqual((await (await client.get('/v1/status', headers=h)).json())['service'], 'Cammy Control Center')
                r = await client.post('/v1/message', headers=h, json={'text': 'what time is it'})
                self.assertEqual(r.status, 200)
                self.assertEqual(c3.voice.turns, ['what time is it'])
                self.assertEqual((await client.post('/v1/duplex', headers=h, json={'mode': 'loud'})).status, 400)
                self.assertEqual((await client.post('/v1/capture', headers=h, json={'kind': 'photo'})).status, 503)
            finally:
                await client.close()
        with tempfile.TemporaryDirectory() as d:
            run(go(d))
            self.assertEqual(api.api_token(d), api.api_token(d))
            self.assertEqual((Path(d) / 'api-token').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()


class FullDuplexPathTests(unittest.TestCase):
    """mic -> Echo -> Listener as C3.hear wires it: Cammy's own voice never opens the gate,
    Adam talking over Cammy does (barge-in). Synthetic room; real Silero and real canceller."""

    @staticmethod
    def wav(name):
        import wave
        with wave.open(str(ROOT / 'tests/fixtures/c3' / name)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(float)

    def run_path(self, near_at=None):
        from c3.echo import Echo
        from c3.listener import Listener
        rng = np.random.default_rng(7)
        far = self.wav('echo_far.wav')
        far = (far / np.abs(far).max() * 9000)
        rir = np.exp(-np.arange(1600) / 250.0) * rng.standard_normal(1600) * 0.25
        rir[0] = 1.0
        delay = 6400                                    # 400 ms from hand-over to echo
        echo_sig = np.concatenate([np.zeros(delay), np.tanh(np.convolve(far, rir)[:len(far)] / 20000) * 20000 * 0.6])
        mic = echo_sig + rng.standard_normal(len(echo_sig)) * 30
        if near_at is not None:
            near = self.wav('echo_near.wav')
            near = near / np.abs(near).max() * 6000
            start = int(near_at * 16000)
            end = min(len(mic), start + len(near))
            mic[start:end] += near[:end - start]
        mic = np.clip(mic, -32767, 32767).astype('<i2').tobytes()
        echo, listener = Echo(), Listener()
        pcm = far.astype('<i2').tobytes()
        for i in range(0, len(pcm), 32000):
            echo.play(pcm[i:i + 32000], new_job=(i == 0))
        starts = []
        for i in range(0, len(mic), 3200):
            clean = echo.process(mic[i:i + 3200])
            st = echo.state
            if st.get('converged'):
                events = listener.feed(clean, strict=bool(st.get('echo_active')))
            else:
                events = listener.feed(clean, playing=True)    # half duplex until locked
            starts += [i / 32000 for e in events if e.kind == 'start']
        return starts, echo.state

    def test_cammy_alone_never_opens_the_gate(self):
        starts, state = self.run_path()
        self.assertEqual(starts, [])
        self.assertTrue(state.get('delay_confident'))

    def test_adam_talking_over_cammy_is_heard(self):
        starts, _ = self.run_path(near_at=2.5)
        self.assertTrue(starts, 'barge-in not detected')
        self.assertGreaterEqual(starts[0], 2.5)
        self.assertLess(starts[0], 3.6)


class StockModeTests(unittest.TestCase):
    def test_stock_camera_is_put_in_video_mode_before_media_preparation(self):
        portal = FakePortal()
        prepared = []
        portal.media_status = lambda: {'phase': 'unprepared'} if not prepared else {'ready': True}
        portal.prepare_media = lambda: prepared.append(True)

        class Stock:
            mode, log = B.PHOTO, []

            def state(self):
                return self.mode, False

            def set_mode(self, m):
                self.log.append(m)
                self.mode = m
        stock = Stock()
        body = B.Body('http://x', 't', client=portal, stock=stock)
        slept = []
        body._pause = lambda seconds=0: slept.append(seconds)
        body._prepare_media()
        self.assertEqual(stock.log, [B.VIDEO])
        self.assertEqual(prepared, [True])
        self.assertIn(B.MODE_SETTLE_S, slept)       # settles before preparing

    def test_requires_recovery_says_power_cycle_without_retrying(self):
        portal = FakePortal()
        portal.media_status = lambda: {'phase': 'requires-recovery', 'requires_recovery': True}
        portal.prepare_media = lambda: self.fail('must not retry')
        with self.assertRaisesRegex(RuntimeError, 'power cycle'):
            B.Body('http://x', 't', client=portal)._prepare_media()
