"""Connection logic of host/cammy_gemini_live.py (no network, no hardware).

Needs the live venv: .venv-live/bin/python -m unittest tests.test_gemini_live
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import cammy_gemini_live as live  # noqa: E402


class FakeSpeaker:
    def __init__(self):
        self.playing = False

    def muted(self):
        return self.playing


async def chunks(items):
    for item in items:
        yield item


class MicQueueTest(unittest.TestCase):
    def test_full_queue_drops_oldest_chunk(self):
        async def run():
            queue = asyncio.Queue(maxsize=3)
            await live.pump_mic(chunks([b'1', b'2', b'3', b'4', b'5']), queue)
            return [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertEqual(asyncio.run(run()), [b'3', b'4', b'5'])


class RotateTest(unittest.TestCase):
    def test_go_away_waits_until_reply_and_playback_finish(self):
        async def run():
            link, speaker = live.Link(), FakeSpeaker()
            link.go_away, link.in_turn, speaker.playing = True, True, True
            task = asyncio.create_task(live.rotate_when_idle(link, speaker, 0))
            await asyncio.sleep(0.5)
            mid_reply = task.done()
            link.in_turn = False
            await asyncio.sleep(0.5)
            mid_playback = task.done()
            speaker.playing = False
            return mid_reply, mid_playback, await asyncio.wait_for(task, 2)
        self.assertEqual(asyncio.run(run()), (False, False, 'go_away'))

    def test_timer_rotates_when_idle_and_zero_means_never(self):
        async def run():
            speaker = FakeSpeaker()
            timed = await asyncio.wait_for(live.rotate_when_idle(live.Link(), speaker, 0.3), 2)
            never = asyncio.create_task(live.rotate_when_idle(live.Link(), speaker, 0))
            await asyncio.sleep(0.6)
            still_running = not never.done()
            never.cancel()
            return timed, still_running
        self.assertEqual(asyncio.run(run()), ('rotate timer', True))



class FakeCapture:
    busy = False

    def run(self, kind):
        return {'kind': kind, 'name': 'ANMP0001.jpg', 'path': '/x/ANMP0001.jpg', 'size': 3, 'seconds': 1.0,
                'gemini': [b'\xff\xd8jpeg\xff\xd9']}


class FakeSession:
    def __init__(self, messages):
        self.messages, self.tool_responses, self.turns = messages, [], []

    async def receive(self):   # one turn per call, like the SDK; then the stream is over
        messages, self.messages = self.messages, []
        for m in messages:
            yield m
        await asyncio.sleep(0.2)   # let the capture finish before the stream ends

    async def send_tool_response(self, function_responses):
        self.tool_responses.extend(function_responses)

    async def send_client_content(self, turns, turn_complete):
        self.turns.append(turns)


class CameraToolTest(unittest.TestCase):
    def test_take_picture_answers_at_once_then_delivers_the_photo(self):
        from google.genai import types
        call = types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
            types.FunctionCall(id='c1', name='take_picture', args={}),
            types.FunctionCall(id='c2', name='take_video', args={})]))

        async def go():
            camera = live.Camera(FakeCapture())
            session, link = FakeSession([call]), live.Link()
            with self.assertRaises(ConnectionError):   # the fake stream ends: same as a dropped link
                await live.receive_loop(session, FakeSpeaker(), None, link, camera=camera)
            deliver = asyncio.create_task(live.deliver_camera(session, FakeSpeaker(), link, camera))
            await asyncio.sleep(0.1)
            deliver.cancel()
            return session

        session = asyncio.run(go())
        first, second = session.tool_responses
        self.assertIn('red record button', first.response['result'])
        self.assertIn('already taking one', second.response['error'])   # one capture at a time
        parts = session.turns[0].parts
        self.assertTrue(parts[0].text.startswith('[Camera] Adam took this photo'))
        self.assertEqual(parts[1].inline_data.mime_type, 'image/jpeg')

    def test_face_hides_for_the_capture(self):
        class Portal:
            calls = []

            def face_status(self):
                return {'available': True}

            def face(self, expression=None, action='show'):
                self.calls.append((expression, action))
        face = live.Face(Portal())
        hooks = live.FaceHooks(face)
        hooks.hide_face()
        face.set('hide' if face.hidden else 'neutral')
        hooks.show_face()
        face.set('hide' if face.hidden else 'neutral')
        self.assertEqual(Portal.calls, [(None, 'hide'), ('neutral', 'show')])


if __name__ == '__main__':
    unittest.main()
