"""C3 speech gate (host/c3/listener.py). No devices, no network, no speakers.

Deterministic tests inject a fake VAD: every sample of a frame holds round(p*1000),
except sample 1, which carries a frame tag, so the fake reads p back from x[0] and
the tests can check exactly which frames went out.

tests/fixtures/c3/speech.wav (16 kHz mono S16LE, about 4.1 s) was made once, offline,
with Kokoro-82M (voice af_heart, weights from SERVE/models/hf/hexgrad/Kokoro-82M, run
from the voice-keys venv with HF_HUB_OFFLINE=1) saying "Hello Cammy, what is the
weather like today?": 24 kHz output FFT-resampled to 16 kHz, trimmed at |x| > 0.01,
peak-normalised to 0.5 and padded with 1 s of digital silence on each side.
"""
from pathlib import Path
import sys, time, unittest, wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from c3.listener import Listener, Event, default_model_path, FRAME_BYTES  # noqa: E402

SPEECH = ROOT / 'tests' / 'fixtures' / 'c3' / 'speech.wav'
MODEL = default_model_path()


def frame(p, tag=0):
    x = np.full(512, int(round(p * 1000)), '<i2')
    x[1] = tag
    return x.tobytes()


def stream(probs, tag0=0):
    return b''.join(frame(p, tag0 + i) for i, p in enumerate(probs))


def tags(pcm):
    return [int(v) for v in np.frombuffer(pcm, '<i2')[1::512]]


class FakeVAD:
    def __init__(self):
        self.calls = self.resets = 0

    def __call__(self, x):
        self.calls += 1
        return x[0] * 32768 / 1000

    def reset(self):
        self.resets += 1


def merged(events):
    """Join consecutive 'audio' events so different chunkings compare equal."""
    out = []
    for e in events:
        if e.kind == 'audio' and out and out[-1].kind == 'audio':
            out[-1] = Event('audio', pcm=out[-1].pcm + e.pcm)
        else:
            out.append(e)
    return out


def feed_chunks(lis, data, size, playing=False):
    ev = []
    for i in range(0, len(data), size):
        ev += lis.feed(data[i:i + size], playing)
    return ev


def make(**kw):
    return Listener(vad=FakeVAD(), start_ms=192, preroll_ms=416, **kw)


class Gate(unittest.TestCase):
    def test_default_gate_closes_after_480_ms_of_quiet(self):
        lis = make()
        lis.feed(stream([0.9] * 6))
        self.assertTrue(lis.speaking)
        lis.feed(stream([0] * 14))
        self.assertTrue(lis.speaking)
        self.assertIn('end', [e.kind for e in lis.feed(frame(0))])

    def test_start_after_start_ms_with_exact_preroll(self):
        lis = make()
        ev = lis.feed(stream([0.1] * 20 + [0.9] * 5))
        self.assertEqual(ev, [])
        self.assertFalse(lis.speaking)
        ev = lis.feed(frame(0.9, 25))
        self.assertEqual([e.kind for e in ev], ['start'])
        self.assertTrue(lis.speaking)
        # 13 frames (416 ms) before the trigger + the 6 trigger frames (192 ms)
        self.assertEqual(tags(ev[0].pcm), list(range(7, 26)))
        self.assertEqual(len(ev[0].pcm), 19 * FRAME_BYTES)

    def test_broken_run_does_not_start(self):
        lis = make()
        self.assertEqual(lis.feed(stream([0.9] * 5 + [0.4] + [0.9] * 5)), [])
        self.assertEqual(lis.feed(frame(0.9))[0].kind, 'start')

    def test_hangover_end_after_end_ms(self):
        lis = make(end_ms=704)
        ev = lis.feed(stream([0.9] * 10))  # start at frame 5, frames 6..9 are audio
        self.assertEqual([e.kind for e in ev], ['start', 'audio'])
        self.assertEqual(tags(ev[1].pcm), [6, 7, 8, 9])
        self.assertEqual(lis.feed(stream([0.1] * 21, 10))[0].kind, 'audio')
        self.assertTrue(lis.speaking)
        ev = lis.feed(stream([0.1, 0.1], 31))
        self.assertEqual([e.kind for e in ev], ['audio', 'end', ])
        self.assertEqual(tags(ev[0].pcm), [31])  # the 22nd quiet frame closes it
        self.assertEqual(ev[1].reason, 'silence')
        self.assertAlmostEqual(ev[1].seconds, 32 * 0.032)
        self.assertFalse(lis.speaking)
        self.assertEqual(lis.stats['utterances'], 1)
        self.assertEqual(lis.stats['frames_sent'], 32)

    def test_dips_between_thresholds_keep_it_open(self):
        lis = make()
        lis.feed(stream([0.9] * 6))
        ev = lis.feed(stream([0.4] * 40 + [0.2] * 14 + [0.4] + [0.2] * 14))
        self.assertEqual([e.kind for e in ev], ['audio'])
        self.assertTrue(lis.speaking)
        ev = lis.feed(frame(0.2))
        self.assertEqual(ev[-1].reason, 'silence')

    def test_playback_ends_utterance_and_never_becomes_preroll(self):
        lis = make()
        lis.feed(stream([0.9] * 8))
        ev = lis.feed(stream([0.9] * 10, 100), playing=True)
        self.assertEqual([(e.kind, e.reason) for e in ev], [('end', 'playback')])
        self.assertAlmostEqual(ev[0].seconds, 8 * 0.032)
        self.assertEqual(lis.feed(stream([0.9] * 3, 200), playing=True), [])
        self.assertFalse(lis.speaking)
        self.assertEqual(lis.stats['frames_dropped_playback'], 13)
        resets = lis.vad.resets
        ev = lis.feed(stream([0.1] * 4 + [0.9] * 6, 300))
        self.assertGreater(lis.vad.resets, resets)
        self.assertEqual([e.kind for e in ev], ['start'])
        self.assertEqual(tags(ev[0].pcm), list(range(300, 310)))  # nothing from before

    def test_playback_with_odd_chunks_keeps_alignment(self):
        speech = stream([0.1] * 3 + [0.9] * 8)
        lis = make()
        lis.feed(b'\x01\x02\x03', playing=True)
        lis.feed(b'\x04\x05\x06\x07' * 100 + b'\x08', playing=True)  # 404 bytes in total
        a = merged(feed_chunks(lis, speech, 777))
        b = merged(make().feed(speech))
        self.assertEqual(a, b)

    def test_max_length_then_reopens(self):
        lis = make(max_utterance_s=1.0)  # 31 frames
        ev = lis.feed(stream([0.9] * 30))
        self.assertEqual([e.kind for e in ev], ['start', 'audio'])
        ev = lis.feed(frame(0.9, 30))
        self.assertEqual([(e.kind, e.reason) for e in ev], [('audio', ''), ('end', 'max_length')])
        self.assertAlmostEqual(ev[1].seconds, 31 * 0.032)
        self.assertEqual(lis.feed(stream([0.9] * 5, 31)), [])
        ev = lis.feed(frame(0.9, 36))
        self.assertEqual([e.kind for e in ev], ['start'])
        self.assertEqual(tags(ev[0].pcm), list(range(31, 37)))

    def test_reset(self):
        lis = make()
        self.assertEqual(lis.reset(), [])
        lis.feed(stream([0.9] * 7) + b'\x01')
        resets = lis.vad.resets
        ev = lis.reset()
        self.assertEqual([(e.kind, e.reason) for e in ev], [('end', 'reset')])
        self.assertAlmostEqual(ev[0].seconds, 7 * 0.032)
        self.assertFalse(lis.speaking)
        self.assertEqual(lis.vad.resets, resets + 1)
        self.assertEqual(lis.feed(stream([0.9] * 5)), [])  # buffers cleared: needs 6 again
        self.assertEqual(lis.feed(frame(0.9))[0].kind, 'start')

    def test_chunk_sizes_give_identical_events(self):
        probs = ([0.1] * 30 + [0.9] * 40 + [0.4] * 10 + [0.1] * 25 + [0.9] * 3 + [0.1] * 5
                 + [0.6] * 50 + [0.2] * 30 + [0.9] * 7 + [0.1] * 3)
        data = stream(probs) + b'\x05'  # trailing partial frame is buffered, never sent
        ref = merged(make().feed(data))
        self.assertEqual([e.kind for e in ref].count('start'), 3)
        self.assertEqual([e.kind for e in ref].count('end'), 2)
        for size in (1, 3199, 3200, 7777):
            with self.subTest(size=size):
                lis = make()
                self.assertEqual(merged(feed_chunks(lis, data, size)), ref)
                self.assertTrue(lis.speaking)

    def test_one_audio_event_per_feed(self):
        lis = make()
        lis.feed(stream([0.9] * 6))
        ev = lis.feed(stream([0.9] * 50))
        self.assertEqual(len(ev), 1)
        self.assertEqual(len(ev[0].pcm), 50 * FRAME_BYTES)

    def test_floor_skips_model(self):
        def boom(x):
            raise AssertionError('model called below the floor')
        lis = Listener(vad=boom)
        quiet = (np.random.default_rng(1).standard_normal(512 * 50) * 32768 * 10 ** (-65 / 20))
        self.assertEqual(lis.feed(np.zeros(512 * 50, '<i2').tobytes()), [])
        self.assertEqual(lis.feed(quiet.astype('<i2').tobytes()), [])
        self.assertEqual(lis.stats['model_calls'], 0)
        self.assertEqual(lis.stats['frames_in'], 100)
        lis = make()
        lis.feed(stream([0.1] * 10))  # 100/32768 = -50 dBFS, above the floor
        self.assertEqual(lis.stats['model_calls'], 10)

    def test_no_model_no_vad_is_a_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, 'no Silero VAD model'):
            Listener(model_path='/nonexistent/silero_vad.onnx')


def read_wav(path):
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        return w.readframes(w.getnframes())


@unittest.skipUnless(MODEL, 'pipecat silero_vad.onnx not found')
class RealModel(unittest.TestCase):
    def test_silence_and_noise_never_start(self):
        lis = Listener()
        self.assertEqual(feed_chunks(lis, bytes(32000 * 5), 3200), [])
        self.assertEqual(lis.stats['model_calls'], 0)
        noise = np.random.default_rng(2).standard_normal(16000 * 10) * 32768 * 10 ** (-30 / 20)
        ev = feed_chunks(lis, np.clip(noise, -32768, 32767).astype('<i2').tobytes(), 3200)
        self.assertNotIn('start', [e.kind for e in ev])
        self.assertEqual(lis.stats['model_calls'], 312)

    def test_speech_fixture_one_utterance(self):
        pcm = read_wav(SPEECH)
        x = np.abs(np.frombuffer(pcm, '<i2').astype(np.float32)) / 32768
        loud = np.nonzero(x > 0.02)[0]
        onset, offset = loud[0] / 16000, loud[-1] / 16000
        lis, ev = Listener(), []
        for i in range(0, len(pcm), FRAME_BYTES):
            for e in lis.feed(pcm[i:i + FRAME_BYTES]):
                ev.append((e, (i + FRAME_BYTES) / 32000))
        kinds = [e.kind for e, _ in ev if e.kind != 'audio']
        self.assertEqual(kinds, ['start', 'end'])
        (start, t0), = [(e, t) for e, t in ev if e.kind == 'start']
        (end, t1), = [(e, t) for e, t in ev if e.kind == 'end']
        self.assertLess(abs(t0 - onset), lis.start_n * 0.032 + 0.1, (t0, onset))
        self.assertLess(t0 - len(start.pcm) / 32000, onset)  # pre-roll reaches before onset
        self.assertEqual(end.reason, 'silence')
        self.assertLess(abs(t1 - offset), 1.0, (t1, offset))
        sent = len(start.pcm) + sum(len(e.pcm) for e, _ in ev if e.kind == 'audio')
        self.assertAlmostEqual(end.seconds, sent / 32000)

    def test_speed_60s(self):
        speech = read_wav(SPEECH)
        noise = np.random.default_rng(3).standard_normal(16000 * 2) * 32768 * 10 ** (-35 / 20)
        block = speech + noise.astype('<i2').tobytes()
        data = (block * (60 * 32000 // len(block) + 1))[:60 * 32000]
        lis = Listener()
        t = time.perf_counter()
        feed_chunks(lis, data, 3200)
        dt = time.perf_counter() - t
        self.assertGreater(lis.stats['model_calls'], 1000)
        self.assertLess(dt, 6.0, f'60 s of audio took {dt:.2f} s')


if __name__ == '__main__':
    unittest.main()


class StrictTests(unittest.TestCase):
    """Barge-in thresholds (full duplex): added by the C3 integration, 2026-11-16."""

    def test_strict_needs_surer_and_longer_speech(self):
        import numpy as np
        from c3.listener import Listener, FRAME
        probs = iter([0.6] * 20 + [0.9] * 12)
        L = Listener(vad=lambda f: next(probs), floor_dbfs=-120)
        frame = (np.ones(FRAME) * 1000).astype('<i2').tobytes()
        starts = []
        for i in range(32):
            for e in L.feed(frame, strict=True):
                if e.kind == 'start':
                    starts.append((i, len(e.pcm) // len(frame)))
        self.assertEqual(starts[0][0], 20 + L.strict_n - 1)     # 0.6 never opens it; 0.9 for 320 ms does
        self.assertEqual(starts[0][1], L.preroll_n + L.strict_n)
