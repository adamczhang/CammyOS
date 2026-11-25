"""C3 face intents: local state picks the pose; Voice says when a sent turn awaits its reply.

Offline only: a fake portal records face commands, a fake Gemini session is scripted.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
from c3 import body as B  # noqa: E402
from c3 import voice as V  # noqa: E402
from cammy import CammyError  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Portal:
    def __init__(self, status=None, error=None):
        self.log, self.status, self.error, self.status_calls = [], status, error, 0

    def face_status(self):
        self.status_calls += 1
        if self.error:
            raise self.error
        return self.status

    def face(self, expression, action):
        self.log.append((expression, action))


class Body:
    def __init__(self, cammy):
        self.cammy, self.epoch = cammy, 1
        self.up = asyncio.Event()
        self.losses = []

    def _current(self, epoch):
        return epoch == self.epoch

    def _report_loss(self, exc, epoch):
        self.losses.append(exc)


NEW_FW = {'available': True, 'speaking_animation': True}
OLD_FW = {'available': True}               # 0.7.6 before the speaking loop: no speaking_animation


def face(status=NEW_FW, clock=None, **kw):
    portal = Portal(status, **kw)
    return B.Face(Body(portal), clock=clock or Clock()), portal


class IntentTests(unittest.TestCase):
    def test_priority(self):
        I = B.face_intent
        self.assertEqual(I(), 'neutral')
        self.assertEqual(I(waiting=True), 'waiting')
        self.assertEqual(I(waiting=True, looking=True), 'looking')
        self.assertEqual(I(waiting=True, looking=True, listening=True), 'listening')
        self.assertEqual(I(listening=True, speaking=True), 'speaking')
        self.assertEqual(I(speaking=True, hidden=True), 'hide')


class CommandTests(unittest.TestCase):
    def show(self, f, want, epoch=1):
        f._set(want, epoch)

    def test_each_intent_maps_to_an_existing_portal_command(self):
        f, portal = face()
        for want in ('neutral', 'listening', 'waiting', 'speaking', 'hide'):
            f.clock.t += 1
            self.show(f, want)
        self.assertEqual(portal.log, [(None, 'idle'), ('happy', 'show'),
                                      ('doubt', 'show'), (None, 'speak'), (None, 'hide')])

    def test_old_firmware_speaks_with_the_happy_pose(self):
        f, portal = face(OLD_FW)
        self.show(f, 'speaking')
        self.assertEqual(portal.log, [('happy', 'show')])

    def test_no_face_service_sends_nothing(self):
        for kw in ({'error': CammyError(404, 'no face')}, {'status': {'available': False}}):
            f, portal = face(**kw)
            self.show(f, 'listening')
            self.assertEqual(portal.log, [])

    def test_a_new_epoch_rereads_status_and_reshows(self):
        f, portal = face()
        self.show(f, 'neutral')
        self.show(f, 'neutral')
        f.body.epoch = 2
        self.show(f, 'neutral', epoch=2)
        self.assertEqual(portal.log, [(None, 'idle')] * 2)
        self.assertEqual(portal.status_calls, 2)

    def test_a_stale_epoch_sends_nothing(self):
        f, portal = face()
        f.body.epoch = 2
        self.show(f, 'neutral', epoch=1)
        self.assertEqual(portal.log, [])


class ChurnTests(unittest.TestCase):
    def test_gate_flicker_within_the_hold_sends_one_command(self):
        f, portal = face()
        f._set('neutral', 1)
        f.clock.t += B.FACE_HOLD_S       # neutral has been up a while
        for i in range(10):              # the gate flaps every tick
            f._set('listening' if i % 2 == 0 else 'neutral', 1)
            f.clock.t += 0.1
        self.assertEqual(portal.log, [(None, 'idle'), ('happy', 'show')])

    def test_a_held_change_lands_once_the_hold_passes(self):
        f, portal = face()
        f._set('listening', 1)
        f._set('waiting', 1)             # gate closed at once: held
        self.assertEqual(len(portal.log), 1)
        f.clock.t += B.FACE_HOLD_S / 2
        f._set('waiting', 1)         # still held
        f.clock.t += B.FACE_HOLD_S / 2 + 0.01
        f._set('waiting', 1)
        self.assertEqual(portal.log, [('happy', 'show'), ('doubt', 'show')])

    def test_speaking_starts_and_stops_without_waiting(self):
        f, portal = face()
        f._set('waiting', 1)
        f._set('speaking', 1)            # playback began 0 s after the last change
        f._set('neutral', 1)             # and ended: the mouth stops at once
        self.assertEqual(portal.log, [('doubt', 'show'), (None, 'speak'), (None, 'idle')])

    def test_old_firmware_listening_then_speaking_shares_one_pose(self):
        f, portal = face(OLD_FW)
        f._set('listening', 1)
        f._set('speaking', 1)
        f._set('neutral', 1)             # leaving speaking is immediate even on the shared pose
        self.assertEqual(portal.log, [('happy', 'show'), (None, 'idle')])

    def test_hide_and_show_are_immediate(self):
        f, portal = face()
        f._set('neutral', 1)
        f._set('hide', 1)
        f._set('neutral', 1)
        self.assertEqual(portal.log, [(None, 'idle'), (None, 'hide'), (None, 'idle')])

    def test_a_quiet_minute_sends_one_command(self):
        f, portal = face()
        for _ in range(200):             # 60 s of 0.3 s ticks
            f._set('neutral', 1)
            f.clock.t += 0.3
        self.assertEqual(f.commands, 1)


class Speaker:
    active = False


class Listener:
    speaking = False


class Gate:
    """Stands in for Voice in Face.want: only listener and waiting() are read."""
    def __init__(self):
        self.listener, self.pending = Listener(), False

    def waiting(self):
        return self.pending


class WantTests(unittest.TestCase):
    def test_want_reads_local_state(self):
        f, _ = face()
        speaker, voice = Speaker(), Gate()
        self.assertEqual(f.want(speaker), 'neutral')
        voice.state = 'asleep'
        self.assertEqual(f.want(speaker, voice), 'neutral')
        voice.pending = True
        self.assertEqual(f.want(speaker, voice), 'waiting')
        voice.listener.speaking = True
        self.assertEqual(f.want(speaker, voice), 'listening')
        speaker.active = True
        self.assertEqual(f.want(speaker, voice), 'speaking')
        f.set_view('camera', True)
        self.assertEqual(f.want(speaker, voice), 'hide')

    def test_looking_uses_the_face_clock(self):
        f, _ = face()
        f.looking_until = f.clock() + 3
        self.assertEqual(f.want(Speaker()), 'looking')
        f.clock.t += 3
        self.assertEqual(f.want(Speaker()), 'neutral')

    def test_voice_without_waiting_is_tolerated(self):
        f, _ = face()
        self.assertEqual(f.want(Speaker(), SimpleNamespace(listener=None)), 'neutral')


class RunTests(unittest.TestCase):
    def test_run_follows_state_and_backs_off_on_refusal(self):
        async def go():
            f, portal = face(clock=__import__('time').monotonic)
            speaker, voice = Speaker(), Gate()
            f.body.up.set()
            task = asyncio.create_task(f.run(speaker, voice))
            try:
                await asyncio.sleep(0.05)
                voice.pending = True
                await asyncio.sleep(B.FACE_HOLD_S + 0.7)
                speaker.active = True
                await asyncio.sleep(0.4)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return portal.log
        log = asyncio.run(go())
        self.assertEqual(log, [(None, 'idle'), ('doubt', 'show'), (None, 'speak')])


# ---- Voice: when is a reply pending? ------------------------------------------------------
def gm(**kw):
    from google.genai import types
    return types.LiveServerMessage(**kw)


def sc(**kw):
    from google.genai import types
    return gm(server_content=types.LiveServerContent(**kw))


def audio_part(seconds=0.5):
    from google.genai import types
    return types.Content(parts=[types.Part(inline_data=types.Blob(mime_type='audio/pcm', data=bytes(int(24000 * 2 * seconds))))])


class Session:
    """receive() yields what the test puts in `inbox`; one receive() pass ends on None."""
    def __init__(self):
        self.sent, self.inbox = [], asyncio.Queue()

    async def send_realtime_input(self, **kw):
        self.sent.append(next(iter(kw)))

    async def send_client_content(self, turns, turn_complete):
        self.sent.append('client_content')

    async def send_tool_response(self, function_responses):
        self.sent.append('tool_response')

    async def receive(self):
        while True:
            item = await self.inbox.get()
            if item is None:
                return
            yield item


class VSpeaker:
    active = sending = False

    def __init__(self):
        self.queue = asyncio.Queue()
        self.said, self.flushed = [], 0

    def muted(self):
        return False

    def say(self, pcm, end=False):
        self.said.append(end)

    def flush(self):
        self.flushed += 1


class Ev:
    def __init__(self, kind, pcm=b''):
        self.kind, self.pcm = kind, pcm


class Transcript:
    def add(self, *a):
        pass


class VoiceWaitingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.listener = Listener()
        self.speaker = VSpeaker()
        self.v = V.Voice(None, 'm', lambda: 'p', 'v', self.speaker, SimpleNamespace(awake=False, looking_until=0.0),
                         None, self.listener, Transcript(), clock=self.clock)

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)

    def scenario(self, steps):
        """steps(session, task_helpers) runs while _send_speech, _deliver and _receive are live."""
        async def go():
            s = Session()
            tasks = [asyncio.create_task(c) for c in (self.v._send_speech(s), self.v._deliver(s), self.v._receive(s))]
            try:
                await steps(s)
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run(go())

    def utterance(self):
        self.v.on_speech(Ev('start', bytes(3200)))
        self.v.on_speech(Ev('end'))

    def test_an_utterance_waits_until_turn_complete(self):
        async def steps(s):
            self.utterance()
            await self.settle()
            self.assertEqual(s.sent[-1], 'activity_end')
            self.assertTrue(self.v.waiting())
            s.inbox.put_nowait(sc(model_turn=audio_part()))
            await self.settle()
            self.assertTrue(self.v.waiting())           # in_turn only begins with audio
            s.inbox.put_nowait(sc(turn_complete=True))
            await self.settle()
            self.assertFalse(self.v.waiting())
        self.scenario(steps)

    def test_nothing_waits_before_the_utterance_is_sent(self):
        async def steps(s):
            self.v.on_speech(Ev('start', bytes(3200)))
            await self.settle()
            self.assertFalse(self.v.waiting())          # still talking: the listener shows that
        self.scenario(steps)

    def test_a_stale_utterance_is_dropped_and_not_awaited(self):
        async def steps(s):
            self.v.speech.put_nowait((self.clock() - V.STALE_S - 1, Ev('start', bytes(3200))))
            self.v.speech.put_nowait((self.clock(), Ev('end')))
            await self.settle()
            self.assertNotIn('activity_end', s.sent)
            self.assertFalse(self.v.waiting())
        self.scenario(steps)

    def test_a_typed_turn_waits_once_sent(self):
        async def steps(s):
            self.listener.speaking = True               # held back while Adam talks
            from google.genai import types
            self.v.add_turn([types.Part(text='hello')], 'api')
            await self.settle()
            self.assertFalse(self.v.waiting())
            self.listener.speaking = False
            await asyncio.sleep(0.25)
            self.assertEqual(s.sent, ['client_content'])
            self.assertTrue(self.v.waiting())
        self.scenario(steps)

    def test_a_silent_or_tool_only_reply_ends_waiting(self):
        from google.genai import types
        call = types.LiveServerToolCall(function_calls=[types.FunctionCall(id='1', name='unknown_tool', args={})])

        async def tool(c):
            return types.FunctionResponse(id=c.id, name=c.name, response={'result': 'ok'})
        self.v._tool = tool

        async def steps(s):
            self.utterance()
            await self.settle()
            self.clock.t += V.WAITING_MAX_S - 1
            s.inbox.put_nowait(gm(tool_call=call))
            await self.settle()
            self.assertEqual(s.sent[-1], 'tool_response')
            self.clock.t += V.WAITING_MAX_S - 1         # the tool reply restarted the wait
            self.assertTrue(self.v.waiting())
            s.inbox.put_nowait(sc(turn_complete=True))  # no audio at all
            await self.settle()
            self.assertFalse(self.v.waiting())
            self.assertEqual(self.speaker.said, [])
        self.scenario(steps)

    def test_interruption_ends_waiting(self):
        async def steps(s):
            self.utterance()
            await self.settle()
            s.inbox.put_nowait(sc(interrupted=True))
            await self.settle()
            self.assertFalse(self.v.waiting())
        self.scenario(steps)

    def test_no_reply_at_all_times_out(self):
        async def steps(s):
            self.utterance()                            # local noise passed the gate, Gemini says nothing
            await self.settle()
            self.clock.t += V.WAITING_MAX_S - 0.1
            self.assertTrue(self.v.waiting())
            self.clock.t += 0.2
            self.assertFalse(self.v.waiting())
        self.scenario(steps)

    def test_streaming_audio_keeps_a_long_reply_pending_until_complete(self):
        async def steps(s):
            self.utterance()
            await self.settle()
            for _ in range(3):
                self.clock.t += V.WAITING_MAX_S - 1
                s.inbox.put_nowait(sc(model_turn=audio_part()))
                await self.settle()
                self.assertTrue(self.v.waiting())
        self.scenario(steps)

    def test_a_connection_ending_mid_wait_clears_it(self):
        """Disconnect, a model error or go_away: _connection's finally clears the wait."""
        class Broken(Session):
            async def receive(self):
                await asyncio.sleep(0.05)
                raise ConnectionError('model error')
                yield  # noqa: unreachable - makes this a generator

        async def go():
            s = Broken()
            self.utterance()
            with self.assertRaises(ConnectionError):
                await self.v._connection(s)
            self.assertIn('activity_end', s.sent)
        asyncio.run(go())
        self.assertIsNone(self.v.reply_due)
        self.assertFalse(self.v.waiting())

    def test_a_resumed_connection_starts_without_a_wait(self):
        self.v.reply_due = self.clock() + 100           # left over from a dropped link

        async def go():
            s = Session()
            s.inbox.put_nowait(None)                    # Gemini closes at once
            with self.assertRaises(ConnectionError):
                await self.v._connection(s)
        asyncio.run(go())
        self.assertFalse(self.v.waiting())


class LiveConfigTest(unittest.TestCase):
    def test_no_proactive_audio_and_manual_turns(self):
        # 2026-11-21: proactive audio left gemini-3.8-live silent for "Cammy, what is two plus two?".
        cfg = V.live_config('persona', 'Leda', [], None)
        self.assertIsNone(cfg.proactivity)
        self.assertTrue(cfg.realtime_input_config.automatic_activity_detection.disabled)
        self.assertIsNotNone(cfg.input_audio_transcription)


if __name__ == '__main__':
    unittest.main()
