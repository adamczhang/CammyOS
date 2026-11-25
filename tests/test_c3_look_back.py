"""look_back: Gemini sees buffered camera frames only when asked, inside the window asked.

Offline: no camera, no Gemini, no service. Eyes is a fake honouring the history contract
(Eyes.history(seconds_ago, duration_s, limit) -> chronological [(time.time(), jpeg)], and
Eyes.history_status -> counts and times).
"""
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
try:                         # the Gemini SDK lives in .venv-live; GTK in the system Python
    from google.genai import types
    from c3 import service
    from c3 import voice as V
    from c3.transcript import Transcript
except ImportError:
    V = None
try:
    from c3 import gui
except (ImportError, ValueError):
    gui = None
needs_genai = unittest.skipUnless(V, 'google-genai not installed (use .venv-live)')
needs_gtk = unittest.skipUnless(gui, 'GTK bindings not installed (use the system python3)')


def run(coro):
    return asyncio.run(coro)


def jpeg(tag, size=100):
    body = ('%s|' % tag).encode()
    return b'\xff\xd8' + body + bytes(max(0, size - len(body) - 2))


class HistoryEyes:
    """1 fps frames from `start_ago` seconds ago up to `end_ago` seconds ago (wall clock)."""

    def __init__(self, start_ago=600, end_ago=0, size=100, now=None):
        now = time.time() if now is None else now
        self.frames = [(now - a, jpeg('t%d' % a, size)) for a in range(int(start_ago), int(end_ago) - 1, -1)]
        self.calls, self.looks = [], 0

    def history(self, seconds_ago, duration_s, limit):
        self.calls.append((seconds_ago, duration_s, limit))
        now = time.time()
        start, end = now - seconds_ago - 1, now - seconds_ago + duration_s + 1
        inside = [f for f in self.frames if start <= f[0] <= end]
        if len(inside) > limit:
            step = (len(inside) - 1) / max(1, limit - 1)
            inside = [inside[round(i * step)] for i in range(limit)] if limit > 1 else [inside[len(inside) // 2]]
        return inside

    @property
    def history_status(self):
        return {'bytes': sum(len(j) for _, j in self.frames), 'capacity_bytes': 250_000_000,
                'frames': len(self.frames), 'oldest': self.frames[0][0] if self.frames else None,
                'newest': self.frames[-1][0] if self.frames else None}

    async def look(self, wait=4.0, after=0.0):
        self.looks += 1
        return jpeg('now')


class LiveOnlyEyes:
    """An Eyes without history (older body.py)."""
    async def look(self, wait=4.0, after=0.0):
        return jpeg('now')


class Quiet:
    active = speaking = sending = sweeping = False
    awake = False
    looking_until = 0.0

    def muted(self):
        return False

    def say(self, pcm, end=False):
        pass

    def flush(self):
        pass


class RecordingSession:
    """Keeps every payload sent to Gemini, so a test can assert no image left."""

    def __init__(self):
        self.sent = []

    async def send_realtime_input(self, **kw):
        self.sent.append(('realtime', kw))

    async def send_client_content(self, turns, turn_complete):
        self.sent.append(('turn', turns))

    async def send_tool_response(self, function_responses):
        self.sent.append(('tool', function_responses))

    async def receive(self):
        while True:
            await asyncio.sleep(0.01)
            if False:
                yield None


class FakeLive:
    def __init__(self, session):
        self.session, self.configs = session, []

    def connect(self, model, config):
        self.configs.append(config)
        session = self.session

        class Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *a):
                return False
        return Ctx()


def call(name, **args):
    return types.FunctionCall(id='c1', name=name, args=args)


def images(parts):
    return [p.inline_data.data for p in parts or [] if p.inline_data]


@needs_genai
class RequestBoundaries(unittest.TestCase):
    def test_invalid_times_are_refused_not_clamped(self):
        for bad in (0, -5, 'soon', None, float('nan'), float('inf'), True, V.LOOK_BACK_MAX_AGO_S + 1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                V.look_back_request({'seconds_ago': bad})
        with self.assertRaises(ValueError):
            V.look_back_request({})
        with self.assertRaises(ValueError):
            V.look_back_request({'seconds_ago': 60, 'duration_s': -1})

    def test_window_and_count_are_bounded(self):
        self.assertEqual(V.look_back_request({'seconds_ago': 300}), (300.0, 0.0, 1, []))
        ago, duration, count, notes = V.look_back_request({'seconds_ago': 60, 'duration_s': 600, 'count': 50})
        self.assertEqual((ago, duration, count), (60.0, 60.0, V.LOOK_BACK_MAX_FRAMES))
        self.assertIn('end now', notes[0])
        ago, duration, count, notes = V.look_back_request({'seconds_ago': 7200, 'duration_s': 7200, 'count': 0})
        self.assertEqual((duration, count), (V.LOOK_BACK_MAX_SPAN_S, 1))
        self.assertTrue(any('first 30 minutes' in n for n in notes))
        self.assertEqual(V.look_back_request({'seconds_ago': '90', 'duration_s': '30', 'count': 3.7})[:3], (90.0, 30.0, 3))


@needs_genai
class FrameSelection(unittest.TestCase):
    def setUp(self):
        self.now = 2_000_000_000.0
        self.frames = [(self.now - a, jpeg(a)) for a in range(120, -1, -1)]

    def test_only_frames_inside_the_window_and_evenly_spread(self):
        picked = V.look_back_frames(self.frames, 100, 50, 6, self.now)
        stamps = [round(self.now - t) for t, _ in picked]
        self.assertEqual(len(picked), 6)
        self.assertEqual(stamps, sorted(stamps, reverse=True))           # chronological
        self.assertTrue(all(48 <= s <= 102 for s in stamps), stamps)      # 1.5 s slack at most
        self.assertEqual((stamps[0], stamps[-1]), (101, 49))

    def test_current_or_outside_frames_never_substitute(self):
        current_only = [(self.now, jpeg('now'))]
        self.assertEqual(V.look_back_frames(current_only, 300, 0, 1, self.now), [])
        self.assertEqual(V.look_back_frames(self.frames, 600, 60, 6, self.now), [])   # history starts 120 s ago
        self.assertEqual(V.look_back_frames([], 30, 10, 6, self.now), [])
        self.assertEqual(V.look_back_frames([(self.now - 30, b'')], 30, 0, 1, self.now), [])

    def test_total_payload_is_capped(self):
        big = [(self.now - a, jpeg(a, 400_000)) for a in range(60, -1, -1)]
        picked = V.look_back_frames(big, 60, 60, 6, self.now)
        self.assertEqual(len(picked), 3)                                  # 3 x 400 kB <= 1.5 MB
        self.assertLessEqual(sum(len(j) for _, j in picked), V.LOOK_BACK_MAX_BYTES)
        self.assertEqual(round(self.now - picked[0][0]), 60)              # still spread over the window
        self.assertEqual(round(self.now - picked[-1][0]), 0)
        huge = [(self.now - 10, jpeg('x', V.LOOK_BACK_MAX_BYTES + 1))]
        self.assertEqual(V.look_back_frames(huge, 10, 0, 1, self.now), [])


@needs_genai
class VoiceLookBack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.transcript = Transcript(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def voice(self, eyes, live=None):
        return V.Voice(SimpleNamespace(aio=SimpleNamespace(live=live)), 'm', lambda: 'persona', 'v', Quiet(),
                       Quiet(), eyes, Quiet(), self.transcript, idle_s=0.2)

    def test_tool_is_offered_only_with_history_and_live_look_stays(self):
        names = [t.name for t in self.voice(HistoryEyes()).tools()]
        self.assertEqual(names[:2], ['look', 'look_back'])
        self.assertNotIn('look_back', [t.name for t in self.voice(LiveOnlyEyes()).tools()])
        schema = V.LOOK_BACK.parameters
        self.assertEqual(schema.required, ['seconds_ago'])
        self.assertEqual(set(schema.properties), {'seconds_ago', 'duration_s', 'count'})
        self.assertIn('only when Adam asks', V.LOOK_BACK.description)

    def test_photos_travel_inside_the_tool_response_with_labels(self):
        eyes = HistoryEyes()
        v = self.voice(eyes)
        response = run(v._tool(call('look_back', seconds_ago=300, duration_s=60, count=4)))
        self.assertEqual(eyes.calls, [(300.0, 60.0, 4)])
        blobs = images(response.parts)
        self.assertEqual(len(blobs), 4)
        self.assertTrue(all(p.inline_data.mime_type == 'image/jpeg' for p in response.parts))
        labels = response.response['photos']
        self.assertEqual(len(labels), 4)
        self.assertTrue(labels[0].startswith('Photo 1 of 4: 5 min 00 s ago ('), labels[0])
        self.assertRegex(labels[0], r'\(\d\d:\d\d:\d\d\)$')
        self.assertRegex(labels[-1], r'^Photo 4 of 4: (3 min 59|4 min 0[01]) s ago')   # 1 fps slack
        ages = [int(b.split(b'|')[0][3:]) for b in blobs]                  # jpeg('t<age>')
        self.assertEqual(ages, sorted(ages, reverse=True))
        self.assertTrue(all(239 <= a <= 301 for a in ages), ages)
        self.assertIn('4 photos attached in time order', response.response['result'])
        self.assertNotIn('partly', response.response['result'])
        self.assertEqual((v.stats['look_backs'], v.stats['look_back_frames']), (1, 4))
        last = '\n'.join(self.transcript.since(0)[0])
        self.assertIn('look_back', last)
        self.assertNotIn('\xff\xd8', last)

    def test_single_moment(self):
        v = self.voice(HistoryEyes())
        response = run(v._tool(call('look_back', seconds_ago=30)))
        self.assertEqual(len(images(response.parts)), 1)
        self.assertIn('at 30 s ago', response.response['result'])

    def test_no_coverage_says_unavailable_and_sends_nothing(self):
        for eyes in (HistoryEyes(start_ago=0), HistoryEyes(start_ago=100, end_ago=50)):
            with self.subTest(frames=len(eyes.frames)):
                v = self.voice(eyes)
                response = run(v._tool(call('look_back', seconds_ago=20, duration_s=10)))
                self.assertIsNone(response.parts)
                self.assertNotIn('result', response.response)
                self.assertIn('cannot see that time', response.response['error'])
                self.assertEqual(eyes.looks, 0)                            # no live substitute

    def test_current_frame_from_a_loose_history_is_not_used(self):
        eyes = HistoryEyes(start_ago=5)
        eyes.history = lambda *a: [(time.time(), jpeg('now'))]
        response = run(self.voice(eyes)._tool(call('look_back', seconds_ago=600)))
        self.assertIsNone(response.parts)
        self.assertIn('error', response.response)

    def test_partial_coverage_is_reported(self):
        eyes = HistoryEyes(start_ago=40)
        response = run(self.voice(eyes)._tool(call('look_back', seconds_ago=120, duration_s=100)))
        self.assertTrue(images(response.parts))
        self.assertIn('only partly cover', response.response['result'])
        self.assertIn('nothing before 40 s ago', response.response['result'])

    def test_bad_args_and_missing_history_never_touch_frames(self):
        eyes = HistoryEyes()
        v = self.voice(eyes)
        for args in ({}, {'seconds_ago': -3}, {'seconds_ago': 'x'}, {'seconds_ago': 10 ** 9}):
            response = run(v._tool(call('look_back', **args)))
            self.assertIsNone(response.parts)
            self.assertIn('error', response.response)
        self.assertEqual(eyes.calls, [])
        response = run(self.voice(LiveOnlyEyes())._tool(call('look_back', seconds_ago=10)))
        self.assertIn('not available', response.response['error'])

    def test_history_failure_is_unavailable(self):
        eyes = HistoryEyes()

        def broken(*a):
            raise RuntimeError('ring gone')
        eyes.history = broken
        response = run(self.voice(eyes)._tool(call('look_back', seconds_ago=10)))
        self.assertIsNone(response.parts)
        self.assertIn('cannot see', response.response['error'])

    def test_waking_and_talking_send_no_history_and_leave_eyes_alone(self):
        from c3.listener import Event
        eyes = HistoryEyes()
        eyes.wanted = 'untouched'
        class TurnSession(RecordingSession):
            async def receive(self):
                while not any('activity_end' in fields for kind, fields in self.sent if kind == 'realtime'):
                    await asyncio.sleep(0.01)
                yield types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))
                async for message in super().receive():
                    yield message

        session = TurnSession()
        live = FakeLive(session)
        v = self.voice(eyes, live)

        async def go():
            task = asyncio.create_task(v.run())
            try:
                v.on_speech(Event('start', bytes(3200)))
                v.on_speech(Event('end', b''))
                v.add_turn([types.Part(text='[Brain] done')], 'brain')
                for _ in range(300):
                    await asyncio.sleep(0.01)
                    if v.stats['sleeps']:
                        break
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        run(go())
        self.assertEqual(v.stats['sleeps'], 1)
        self.assertEqual(eyes.calls, [])
        self.assertEqual(eyes.wanted, 'untouched')                         # Eyes runs on its own
        jpegs = [kw[k] for kind, kw in session.sent if kind == 'realtime' for k in kw
                 if getattr(kw[k], 'mime_type', '') == 'image/jpeg']
        turn_images = [p for kind, c in session.sent if kind == 'turn' for p in c.parts if p.inline_data]
        self.assertEqual((jpegs, turn_images), ([], []))
        self.assertFalse(any(kind == 'tool' for kind, _ in session.sent))
        self.assertTrue(any(kind == 'turn' for kind, _ in session.sent))
        self.assertIn('look_back', [t.name for t in live.configs[0].tools[0].function_declarations])


@needs_genai
class RealEyesRing(unittest.TestCase):
    """The same tool against body.Eyes and its FrameRing, frames injected, no ffmpeg."""

    def test_look_back_over_the_real_ring(self):
        from c3 import body
        from c3.preview_history import Frame
        mono, wall = 1000.0, 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            eyes = body.Eyes(SimpleNamespace(), clock=lambda: mono, wall=lambda: wall, frame_dir=tmp)
            for age in range(300, 99, -1):                                 # frames 300..100 s ago
                eyes.ring.append(Frame(wall=wall - age, mono=mono - age, data=jpeg('t%d' % age)))
            v = V.Voice(SimpleNamespace(aio=None), 'm', lambda: 'p', 'v', Quiet(), Quiet(), eyes, Quiet(),
                        Transcript(tmp))
            real_time = V.time.time
            V.time.time = lambda: wall
            try:
                got = run(v._tool(call('look_back', seconds_ago=250, duration_s=100, count=6)))
                gone = run(v._tool(call('look_back', seconds_ago=60, duration_s=30)))
            finally:
                V.time.time = real_time
            self.assertEqual(len(images(got.parts)), 6)
            self.assertTrue(got.response['photos'][0].startswith('Photo 1 of 6: 4 min 10 s ago'), got.response)
            self.assertTrue(got.response['photos'][-1].startswith('Photo 6 of 6: 2 min 30 s ago'), got.response)
            self.assertIsNone(gone.parts)                                  # 60..30 s ago: nothing kept
            self.assertIn('cannot see that time', gone.response['error'])
            status = service.C3.eyes_status(SimpleNamespace(eyes=eyes))['history']
            self.assertEqual((status['frames'], status['capacity_bytes']), (201, 250_000_000))


class StatusSurface(unittest.TestCase):
    @staticmethod
    def status(eyes):
        return service.C3.eyes_status(SimpleNamespace(eyes=eyes))

    @needs_genai
    def test_history_metadata_without_images(self):
        eyes = HistoryEyes(start_ago=59)
        eyes.restarts = 2
        status = self.status(eyes)
        self.assertEqual(status['restarts'], 2)
        self.assertNotIn('wanted', status)
        self.assertEqual(status['history']['frames'], 60)
        self.assertEqual(status['history']['capacity_bytes'], 250_000_000)
        callable_eyes = SimpleNamespace(restarts=0, wanted=False, history_status=lambda: {
            'frames': 1, 'bytes': 9, 'capacity_bytes': 10, 'oldest': 1.0, 'newest': 1.0, 'frame': b'jpeg'})
        status = self.status(callable_eyes)
        self.assertEqual(status['wanted'], False)
        self.assertNotIn('frame', status['history'])                       # bytes never reach the API
        nested = SimpleNamespace(restarts=0, history_status={'frames': 2, 'decoder': {'running': True, 'raw': b'x'}})
        self.assertEqual(self.status(nested)['history']['decoder'], {'running': True})

        def broken():
            raise RuntimeError('boom')
        self.assertIn('error', self.status(SimpleNamespace(restarts=0, history_status=broken))['history'])
        self.assertNotIn('history', self.status(SimpleNamespace(restarts=0)))

    @needs_gtk
    def test_gui_row(self):
        now = time.time()
        title, detail, pill, colour = gui.history_row(
            {'profile': 'desk', 'frames': 3600, 'bytes': 150e6, 'capacity_bytes': 250e6, 'oldest': now - 3600,
             'newest': now - 1}, 2)
        self.assertEqual((title, pill, colour), ('Camera memory', 'recording', 'green'))
        self.assertEqual(detail, 'desk · 3600 frames · 60 min · 150 of 250 MB on disk · 2 look-backs')
        self.assertEqual(gui.history_row({'profile': 'desk', 'frames': 0, 'bytes': 0, 'capacity_bytes': 250e6})[2:],
                         ('empty', 'grey'))
        self.assertEqual(gui.history_row({'profile': 'desk', 'frames': 5, 'oldest': now - 900, 'newest': now - 600})[2], 'kept')
        self.assertEqual(gui.history_row({'frames': 5})[2], 'off')               # no profile: no history
        real = {'profile': 'desk', 'profile_name': 'Desk helper', 'frames': 120, 'bytes': 5e6, 'capacity_bytes': 250e6, 'oldest_time': now - 200,     # body.Eyes keys
                'newest_time': now - 80, 'span_s': 119.5, 'fresh': False, 'decoder': {'running': False}}
        self.assertEqual(gui.history_row(real)[1:3], ('Desk helper · 120 frames · 2 min · 5 of 250 MB on disk · 0 look-backs', 'kept'))
        self.assertEqual(gui.history_row(dict(real, fresh=True))[2], 'recording')
        status, portal, _, _ = gui.demo_data('asleep')
        self.assertIn('Camera memory', [r[0] for r in gui.rows(status, portal)])
        status['eyes'] = {'restarts': 0}
        self.assertNotIn('Camera memory', [r[0] for r in gui.rows(status, portal)])


if __name__ == '__main__':
    unittest.main()
