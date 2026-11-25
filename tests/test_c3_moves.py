"""C3 0.7.4: follow me, show the camera, look around, and the retryable media refusal.

No hardware: a scripted portal client that models the stock DOWN toggle.
    .venv-live/bin/python -m unittest tests.test_c3_moves
"""
import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
from c3 import api  # noqa: E402
from c3 import body as B  # noqa: E402
from c3 import moves as M  # noqa: E402
from c3 import voice as V  # noqa: E402


def run(coro, timeout=10):
    return asyncio.run(asyncio.wait_for(coro, timeout))


class Cammy:
    """Stock follow toggles on a DOWN tap unless refused; pans are ignored while it is on."""

    def __init__(self, reports=True, refuse=False):
        self.on, self.reports, self.refuse = False, reports, refuse
        self.presses = []

    def face_status(self):
        return {'available': True, **({'tracking': self.on} if self.reports else {})}

    def input_button(self, button=None, action='tap'):
        self.presses.append((button, action))
        if button == 'zoom_out' and action == 'tap' and not self.refuse:
            self.on = not self.on
        if action == 'hold' and self.on:
            raise AssertionError('panned while face follow was on')


class Face:
    def __init__(self):
        self.views = set()

    def set_view(self, reason, on):
        (self.views.add if on else self.views.discard)(reason)


class Eyes:
    def __init__(self):
        self.looks = 0

    async def look(self, wait=4.0, after=0.0):
        self.looks += 1
        return b'jpeg%d' % self.looks


class Body:
    def __init__(self, cammy):
        self.cammy, self.up = cammy, asyncio.Event()
        self.epoch = 1

    def turn_around(self):
        self.cammy.input_button('turn_180', 'tap')
        self.cammy.on = False             # stock turn_180 switches face follow off


def moves(cammy, **kw):
    return M.Moves(Body(cammy), Face(), Eyes(), **kw)


class FollowTests(unittest.TestCase):
    def test_on_presses_down_once_and_checks_the_state(self):
        cammy = Cammy()
        m = moves(cammy)
        self.assertEqual(m.follow(True, settle=0), (True, 'face follow is on'))
        self.assertEqual(m.follow(True, settle=0)[0], True)        # already on: no second press
        self.assertEqual(cammy.presses, [('zoom_out', 'tap')])
        self.assertEqual(m.face.views, set())                     # cammy-ui shows the live view
        self.assertTrue(m.state['following'] and m.state['reported'])

    def test_a_refused_toggle_is_reported(self):
        ok, message = moves(Cammy(refuse=True)).follow(True, settle=0)
        self.assertFalse(ok)
        self.assertIn('did not switch', message)

    def test_older_firmware_assumes_off_and_hides_the_face_itself(self):
        m = moves(Cammy(reports=False))
        self.assertTrue(m.follow(True, settle=0)[0])
        self.assertEqual(m.face.views, {'follow'})
        self.assertTrue(m.follow(False, settle=0)[0])
        self.assertEqual(m.face.views, set())
        self.assertFalse(m.state['reported'])

    def test_show_camera_is_its_own_reason(self):
        m = moves(Cammy())
        m.show_camera(True)
        m.face.set_view('capture', False)                        # a capture ending keeps it
        self.assertEqual(m.face.views, {'camera'})
        m.show_camera(False)
        self.assertEqual(m.face.views, set())


class SweepTests(unittest.TestCase):
    def test_end_to_end_with_looks_then_middle_and_follow_restored(self):
        cammy = Cammy()
        cammy.on = True
        m = moves(cammy, sweep_s=0.3, looks=4)

        class Stock:
            steps = 0

            def zoom_out_step(self):
                self.steps += 1
        m.stock = Stock()
        orig = M.time.sleep
        M.time.sleep = lambda s: None
        self.addCleanup(setattr, M.time, 'sleep', orig)
        frames = run(m.look_around())
        self.assertEqual([f for _, f in frames], [b'jpeg1', b'jpeg2', b'jpeg3', b'jpeg4'])
        holds = [b for b, a in cammy.presses if a == 'hold']
        order = [b for i, b in enumerate(holds) if i == 0 or holds[i - 1] != b]
        self.assertEqual(order, ['left', 'right', 'left'])
        self.assertEqual(cammy.presses[0], ('zoom_out', 'tap'))    # follow off first
        self.assertEqual(cammy.presses[-1], ('zoom_out', 'tap'))   # and back on after
        self.assertTrue(cammy.on)
        self.assertFalse(m.sweeping)
        self.assertEqual(m.stats['sweeps'], 1)
        self.assertEqual(m.stock.steps, M.ZOOM_STEPS)                # zoomed fully out first
        releases = sum(1 for b, a in cammy.presses if a == 'release')
        self.assertEqual(releases, 3)  # left, uninterrupted right sweep, return to middle


class Transcript:
    def __init__(self):
        self.lines = []

    def add(self, who, text):
        self.lines.append((who, text))


class Call:
    def __init__(self, name, args=None):
        self.name, self.args, self.id = name, args or {}, 'c1'


def voice(m):
    v = V.Voice.__new__(V.Voice)
    v.moves, v.sweep, v.transcript, v.turns = m, None, Transcript(), []
    v.speaker = SimpleNamespace(muted=lambda: False, sending=False)
    v.camera = v.brain = v.turn = v.memory = None
    v.add_turn = lambda parts, label: v.turns.append((label, parts))
    return v


class VoiceToolTests(unittest.TestCase):
    def test_sweep_waits_for_speaker_submission(self):
        cammy = Cammy()
        v = voice(moves(cammy, sweep_s=0.1, looks=2))

        async def go():
            v.speaker.sending = True
            self.assertTrue(v.start_sweep())
            await asyncio.sleep(0.15)
            self.assertFalse(cammy.presses)
            v.speaker.sending = False
            await v.sweep
            self.assertEqual(v.moves.stats['sweeps'], 1)

        run(go())

    def test_queued_audio_waits_until_motion_finishes(self):
        async def go():
            moved = SimpleNamespace(sweeping=True)
            played = asyncio.Event()
            loop = asyncio.get_running_loop()

            def play(pcm, append):
                loop.call_soon_threadsafe(played.set)

            body = B.Body('http://unused', 'fixture', client=SimpleNamespace(play_pcm=play))
            body.up.set()
            speaker = B.Speaker(body, moves=moved)
            speaker.chain = SimpleNamespace(feed=lambda pcm, end: pcm)
            runner = asyncio.create_task(speaker.run())
            try:
                speaker.say(bytes(3200), end=True)
                await asyncio.sleep(0.15)
                self.assertFalse(played.is_set())
                moved.sweeping = False
                await asyncio.wait_for(played.wait(), 1)
            finally:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

        run(go())

    def test_tools_are_offered_with_moves(self):
        names = [t.name for t in voice(moves(Cammy())).tools()] if hasattr(V.Voice, 'tools') else None
        if names is not None:
            self.assertTrue({'follow_me', 'show_camera', 'look_around'} <= set(names))

    def test_follow_me_and_show_camera(self):
        cammy = Cammy()
        v = voice(moves(cammy))
        orig = M.TOGGLE_SETTLE_S
        reply = run(v._tool(Call('follow_me', {'on': True})))
        self.assertEqual(reply.response, {'result': 'face follow is on'})
        self.assertTrue(cammy.on)
        reply = run(v._tool(Call('show_camera', {'on': True})))
        self.assertIn('live camera view', reply.response['result'])
        self.assertEqual(orig, M.TOGGLE_SETTLE_S)

    def test_look_around_runs_in_the_background_and_sends_photos(self):
        cammy = Cammy()
        m = moves(cammy, sweep_s=0.2, looks=3)

        async def go():
            v = voice(m)
            reply = await v._tool(Call('look_around'))
            self.assertIn('Sweep started', reply.response['result'])
            again = await v._tool(Call('look_around'))
            self.assertIn('error', again.response)
            await v.sweep
            return v
        v = run(go())
        label, parts = v.turns[-1]
        self.assertEqual(label, 'look_around')
        self.assertEqual(sum(1 for p in parts if p.inline_data), 3)


class RefusedMediaTests(unittest.TestCase):
    def test_a_refused_preparation_is_retried_not_a_power_cycle(self):
        statuses = iter([{'phase': 'refused'}, {'phase': 'initializing'}, {'ready': True}])
        prepared = []

        class Portal:
            def media_status(self):
                return next(statuses)

            def prepare_media(self):
                prepared.append(True)
        body = B.Body('http://x', 't', client=Portal())
        orig = B.time.sleep
        B.time.sleep = lambda s: None
        self.addCleanup(setattr, B.time, 'sleep', orig)
        body._prepare_media()
        self.assertEqual(prepared, [True])

    def test_refused_while_waiting_raises_a_retryable_error(self):
        statuses = iter([{'phase': 'unprepared'}, {'phase': 'refused'}])

        class Portal:
            def media_status(self):
                return next(statuses)

            def prepare_media(self):
                pass
        orig = B.time.sleep
        B.time.sleep = lambda s: None
        self.addCleanup(setattr, B.time, 'sleep', orig)
        with self.assertRaises(RuntimeError) as err:
            B.Body('http://x', 't', client=Portal())._prepare_media()
        self.assertNotIn('power cycle', str(err.exception))


class SpeakerSplitTests(unittest.TestCase):
    def test_a_long_block_goes_to_the_portal_in_pieces_of_at_most_5_s(self):
        sent = []

        class Portal:
            def play_pcm(self, pcm, append=False):
                if not 1600 <= len(pcm) <= 160000:
                    raise ValueError('Finite mono16k S16_LE PCM required')
                sent.append((len(pcm), append))

        body = B.Body('http://unused', 'fixture', client=Portal())
        body.up.set()
        speaker = B.Speaker(body, level='normal', tail=0.0, echo=None, loop=None)

        async def go():
            task = asyncio.ensure_future(speaker.run())
            import numpy as np
            tone = (np.sin(np.arange(24000 * 12) * 0.05) * 8000).astype('<i2').tobytes()   # 12 s at 24 kHz
            speaker.say(tone, end=True)
            for _ in range(500):
                await asyncio.sleep(0.02)
                if sum(n for n, _ in sent) >= 12 * 32000:
                    break
            task.cancel()
        orig = B.time.sleep
        B.time.sleep = lambda s: None
        self.addCleanup(setattr, B.time, 'sleep', orig)
        run(go())
        self.assertGreaterEqual(len(sent), 3)
        self.assertTrue(all(n <= 160000 for n, _ in sent))
        self.assertEqual([a for _, a in sent], [False] + [True] * (len(sent) - 1))   # one job


class RebindTests(unittest.TestCase):
    def test_a_camera_reboot_clears_the_boot_binding(self):
        class Portal:
            _boot_binding = 'a' * 32
        body = B.Body('http://x', 't', client=Portal())
        body.rebind()
        self.assertIsNone(body.cammy._boot_binding)
        self.assertEqual(body.state['rebinds'], 1)

    def test_a_camera_reboot_clears_an_unconfirmed_capture_restore(self):
        from c3 import service as S
        calls = []
        body = B.Body('http://x', 't', client=SimpleNamespace())
        body.on_rebind = lambda: calls.append(1)
        body.rebind()
        self.assertEqual(calls, [1])
        capture = SimpleNamespace(restore_uncertain=True)
        face = B.Face(body)
        face.set_view('capture', True)                    # the halted capture never showed the face
        svc = SimpleNamespace(camera=SimpleNamespace(capture=capture, busy=True), face=face)
        S.C3._clear_capture_halt(svc)
        self.assertTrue(capture.restore_uncertain)        # a capture still running keeps its halt
        self.assertTrue(face.hidden)
        svc.camera.busy = False
        S.C3._clear_capture_halt(svc)
        self.assertFalse(capture.restore_uncertain)
        self.assertFalse(face.hidden)                     # the face comes back with the new boot
        S.C3._clear_capture_halt(SimpleNamespace(camera=None))

    def test_a_camera_reboot_returns_the_screen_to_the_face(self):
        from c3 import service as S
        body = B.Body('http://x', 't', client=SimpleNamespace())
        face = B.Face(body)
        face.set_view('camera', True)                     # show_camera on the previous boot
        face.set_view('follow', True)
        S.C3._clear_capture_halt(SimpleNamespace(camera=None, face=face))
        self.assertFalse(face.hidden)


class MotionLockTests(unittest.IsolatedAsyncioTestCase):
    """Follow, turn-around and the sweep: one motion at a time, none during a capture."""

    async def test_follow_and_turn_are_refused_while_a_sweep_runs(self):
        cammy = Cammy()
        m = moves(cammy, sweep_s=0.3, looks=2)
        with patch.object(M.time, 'sleep', lambda s: None):
            sweep = asyncio.create_task(m.look_around())
            await asyncio.sleep(0.05)
            self.assertTrue(m.busy)
            self.assertEqual(await m.set_follow(True, settle=0), (False, 'already looking around'))
            self.assertEqual(await m.turn_around(settle=0), (False, 'already looking around'))
            with self.assertRaisesRegex(M.MotionRefused, 'already looking around'):
                await m.look_around()
            await sweep
        self.assertNotIn(('turn_180', 'tap'), cammy.presses)
        self.assertFalse(cammy.on)                     # follow was never toggled by the refused call
        self.assertEqual(m.stats['refused'], 3)
        self.assertFalse(m.busy)

    async def test_a_second_motion_is_refused_not_queued(self):
        cammy = Cammy()
        m = moves(cammy)
        first = asyncio.create_task(m.turn_around(settle=0.2))
        await asyncio.sleep(0.05)
        self.assertEqual(await m.set_follow(True, settle=0), (False, 'the camera is already moving'))
        self.assertEqual(await first, (True, 'Turned around. Use look to see what you now face.'))
        self.assertEqual(cammy.presses, [('turn_180', 'tap')])
        self.assertEqual(await m.set_follow(True, settle=0), (True, 'face follow is on'))

    async def test_no_motion_during_a_capture_and_no_capture_during_motion(self):
        cammy = Cammy()
        m = moves(cammy)
        capturing = [True]
        m.capture_busy = lambda: capturing[0]
        refusal = 'a photo or video is being taken'
        self.assertEqual(await m.set_follow(True, settle=0), (False, refusal))
        self.assertEqual(await m.turn_around(settle=0), (False, refusal))
        v = voice(m)
        self.assertFalse(v.start_sweep())
        self.assertEqual(v.sweep_refusal, refusal)
        self.assertEqual(cammy.presses, [])

        capturing[0] = False
        capture = SimpleNamespace(busy=False, blocked=lambda: None, run=lambda kind: {})
        camera = V.Camera(capture, done=lambda result: None, moves=m)
        turning = asyncio.create_task(m.turn_around(settle=0.2))
        await asyncio.sleep(0.05)
        self.assertFalse(camera.start('photo'))
        self.assertEqual(camera.refusal, 'the camera is moving')
        await turning
        self.assertTrue(camera.start('photo'))
        await camera.task

    async def test_voice_tool_and_api_turn_around_share_one_path(self):
        cammy = Cammy()
        m = moves(cammy)
        m.belief = True
        v = voice(m)
        with patch.object(M, 'TURN_SETTLE_S', 0.05):
            reply = await v._tool(Call('turn_around'))
            self.assertIn('Turned around', reply.response['result'])
            self.assertFalse(m.belief)                 # follow is off after turn_180
            m.belief = True
            c3 = SimpleNamespace(moves=m, transcript=Transcript())
            app = api.make_app(c3, 'secret')
            async with TestClient(TestServer(app)) as client:
                auth = {'Authorization': 'Bearer secret'}
                response = await client.post('/v1/turn-around', headers=auth)
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())['turned'])
                capture = [True]
                m.capture_busy = lambda: capture[0]
                response = await client.post('/v1/turn-around', headers=auth)
                self.assertEqual(response.status, 409)
                self.assertEqual((await response.json())['error'], 'a photo or video is being taken')
        self.assertFalse(m.belief)
        self.assertEqual(m.stats['turns'], 2)
        self.assertEqual(cammy.presses, [('turn_180', 'tap')] * 2)


class FollowPollTests(unittest.IsolatedAsyncioTestCase):
    async def test_follow_state_is_read_per_bring_up_and_while_on_only(self):
        cammy = Cammy()
        reads = []
        status = cammy.face_status
        cammy.face_status = lambda: (reads.append(1), status())[1]
        m = moves(cammy)
        m.body.up.set()
        with patch.object(M, 'FOLLOW_POLL_S', 0.01):
            task = asyncio.create_task(m.run())
            try:
                await asyncio.sleep(0.1)
                self.assertEqual(len(reads), 1)        # off: one read for this bring-up
                cammy.on = True
                m.body.epoch += 1                       # e.g. a recycled lease keeps it on
                await asyncio.sleep(0.1)
                self.assertGreater(len(reads), 3)      # on: polled for status
                self.assertTrue(m.state['following'])
                cammy.on = False                        # a button press switched it off
                await asyncio.sleep(0.05)
                settled = len(reads)
                await asyncio.sleep(0.1)
                self.assertEqual(len(reads), settled)
                self.assertFalse(m.state['following'])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
