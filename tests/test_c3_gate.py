"""Talk gate: near speech streams, quiet speech needs the name or an open conversation."""
import asyncio
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import gate as G  # noqa: E402
from c3.listener import Event, Listener, FRAME  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeSpotter:
    def __init__(self, text='', fail=False):
        self.text, self.fail, self.heard = text, fail, []
        self.state = {'fake': True}

    async def transcribe(self, pcm):
        self.heard.append(pcm)
        if self.fail:
            raise RuntimeError('no model')
        return self.text


def start(level, pcm=b'ab'):
    return Event('start', pcm=pcm, level=level, noise=-70.0)


def end(level, seconds=2.0):
    return Event('end', reason='silence', seconds=seconds, level=level)


class GateTests(unittest.IsolatedAsyncioTestCase):
    def make(self, text='', mode='smart', fail=False, busy=False, names=()):
        self.out, self.clock = [], Clock()
        self.spotter = FakeSpotter(text, fail)
        self.busy = busy
        return G.TalkGate(self.out.append, spotter=self.spotter, busy=lambda: self.busy, clock=self.clock,
                          mode=mode, names=lambda: names)

    async def settle(self, gate):
        await asyncio.gather(*list(gate.pending))

    def kinds(self):
        return [e.kind for e in self.out]

    async def test_near_speech_streams_at_once_and_opens_the_conversation(self):
        gate = self.make()
        gate.feed(start(-30))
        self.assertEqual(self.kinds(), ['start'])
        gate.feed(Event('audio', pcm=b'cd'))
        gate.feed(end(-31))
        self.assertEqual(self.kinds(), ['start', 'audio', 'end'])
        self.assertEqual(self.spotter.heard, [])
        self.assertTrue(gate.engaged())
        self.clock.t += G.ENGAGED_S + 1
        self.assertFalse(gate.engaged())

    async def test_quiet_speech_naming_cammy_goes_on_whole_after_it_ends(self):
        gate = self.make('Hey Cammy, what time is it?')
        gate.feed(start(-48, b'ab'))
        gate.feed(Event('audio', pcm=b'cd'))
        self.assertEqual(self.out, [])
        gate.feed(end(-47))
        await self.settle(gate)
        self.assertEqual(self.kinds(), ['start', 'end'])
        self.assertEqual(self.out[0].pcm, b'abcd')
        lead = int(G.LEAD_IN_S * 16000) * 2
        self.assertEqual(self.spotter.heard, [bytes(lead) + b'abcd'])
        self.assertEqual(gate.counts['named'], 1)
        self.assertTrue(gate.engaged())

    async def test_quiet_speech_without_the_name_never_leaves_the_pc(self):
        gate = self.make('so I told him the meeting moved')
        gate.feed(start(-48))
        gate.feed(end(-47))
        await self.settle(gate)
        self.assertEqual(self.out, [])
        self.assertEqual(gate.recent[-1]['why'], 'not-addressed')
        self.assertFalse(gate.engaged())

    async def test_follow_up_in_an_open_conversation_needs_no_name(self):
        gate = self.make()
        gate.engage()
        gate.feed(start(-48))
        self.assertEqual(self.kinds(), ['start'])
        gate.feed(end(-48))
        self.out.clear()
        gate.feed(start(G.FOLLOW_DBFS - 3))   # too faint even for a follow-up: held
        self.assertEqual(self.out, [])

    async def test_cammy_talking_keeps_the_conversation_open(self):
        gate = self.make(busy=True)
        gate.tick()
        self.busy = False
        self.clock.t += G.ENGAGED_S - 1
        self.assertTrue(gate.engaged())
        gate.feed(start(-45))
        self.assertEqual(self.kinds(), ['start'])

    async def test_faint_utterance_is_dropped_without_the_spotter(self):
        gate = self.make('Cammy')
        gate.feed(start(-62))
        gate.feed(end(G.FAINT_DBFS - 1))
        await self.settle(gate)
        self.assertEqual((self.out, self.spotter.heard), ([], []))
        self.assertEqual(gate.recent[-1]['why'], 'faint')

    async def test_spotter_failure_fails_open(self):
        gate = self.make(fail=True)
        gate.feed(start(-48))
        gate.feed(end(-48))
        await self.settle(gate)
        self.assertEqual(self.kinds(), ['start', 'end'])
        self.assertEqual(gate.counts['spotter_errors'], 1)

    async def test_held_utterance_that_was_loud_overall_passes_without_the_spotter(self):
        gate = self.make()
        gate.feed(start(-45))          # soft first word
        gate.feed(end(-33))            # then clearly close
        await self.settle(gate)
        self.assertEqual(self.kinds(), ['start', 'end'])
        self.assertEqual(self.spotter.heard, [])

    async def test_modes(self):
        gate = self.make(mode='open')
        gate.feed(start(-70))
        self.assertEqual(self.kinds(), ['start'])
        gate = self.make(mode='name')
        gate.feed(start(-20))          # loud, but name mode wants the name
        self.assertEqual(self.out, [])
        with self.assertRaises(ValueError):
            self.make(mode='kids')

    async def test_face_pack_name_counts(self):
        gate = self.make('Pikachu, are you there?', names=['Pikachu'])
        gate.feed(start(-48))
        gate.feed(end(-48))
        await self.settle(gate)
        self.assertEqual(self.kinds(), ['start', 'end'])
        self.assertIsNone(G.name_pattern([]).search('can I have some tea'))
        self.assertIsNotNone(G.name_pattern([]).search('hey cam me, look'))

    async def test_reset_drops_a_held_utterance_and_is_forwarded(self):
        gate = self.make('Cammy')
        gate.feed(start(-48))
        gate.feed(Event('end', reason='reset'))
        self.assertEqual(self.kinds(), ['end'])
        self.assertIsNone(gate.held)

    async def test_decision_that_lands_during_a_newer_utterance_is_dropped(self):
        gate = self.make('Cammy')
        gate.feed(start(-48))
        gate.feed(end(-48))
        gate.feed(start(-30))          # near speech starts before the spotter answers
        await self.settle(gate)
        self.assertEqual(self.kinds(), ['start'])
        self.assertEqual(gate.recent[-1]['why'], 'overlap')


class ListenerLevelTests(unittest.TestCase):
    class VAD:
        def __init__(self):
            self.p = 0.0

        def __call__(self, x):
            return self.p

    def test_start_and_end_carry_levels_and_the_noise_floor(self):
        vad = self.VAD()
        lis = Listener(vad=vad)
        rng = np.random.default_rng(1)
        quiet = (rng.standard_normal(FRAME * 40) * 0.003 * 32768).astype('<i2').tobytes()
        self.assertEqual(lis.feed(quiet), [])
        tone = (np.sin(np.arange(FRAME * 30) * 0.2) * 0.1 * 32768).astype('<i2').tobytes()
        vad.p = 0.9
        events = lis.feed(tone)
        first = events[0]
        self.assertEqual(first.kind, 'start')
        self.assertAlmostEqual(first.level, -23.0, delta=0.5)      # 0.1 sine: 0.005 power
        self.assertAlmostEqual(first.noise, -50.5, delta=1.5)      # 0.003 rms noise
        vad.p = 0.0
        events = lis.feed(bytes(FRAME * 2 * 20))
        last = [e for e in events if e.kind == 'end'][0]
        self.assertAlmostEqual(last.level, -23.0, delta=0.5)


if __name__ == '__main__':
    unittest.main()
