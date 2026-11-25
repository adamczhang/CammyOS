"""C3 echo canceller (host/c3/echo.py). Offline: no devices, no network, no speakers.

Fixtures (16 kHz mono S16LE, peak 0.5), made once offline with Kokoro-82M (voice
af_heart, weights from SERVE/models/hf/hexgrad/Kokoro-82M, voice-keys venv,
HF_HUB_OFFLINE=1), 24 kHz output FFT-resampled to 16 kHz and trimmed at |x| > 0.01:
  echo_far.wav  (6.9 s) Cammy: "Hi Adam! I found three new photos from this morning. ..."
  echo_near.wav (4.9 s) the near-end talker: "Cammy, stop. Wait a second, ..." at speed
                0.9, resampled by 1.3 so its pitch is about 23% lower (a second voice).

Simulated path: speaker = tanh(1.5x)/1.5 soft clip; room = direct path (the delay truth)
plus early reflections at 2-11 ms and a diffuse tail decaying 60 dB in 150 ms; echo
gain 0.8 (echo louder than the near-end talker, peak ~0.3); white noise at -50 dBFS.
The job's echo appears OFFSET ms after play() on the mic timeline.

Measured 2026-11-16 on this PC (run with -v for the table), offsets 180 / 900 ms:
  webrtc: delay error 0 ms, confident 0.43/0.41 s after onset, ERLE 37.2/37.0 dB,
          converged 1.07/1.09 s, double-talk near-end -4.2/-3.1 dB, 60 s in 0.51 s
  speex:  delay error 0 ms, confident 0.43/0.41 s, ERLE 24.0/23.5 dB, converged
          2.48/2.46 s, double-talk -0.5/-0.3 dB, 60 s in 0.39 s
"""
from pathlib import Path
import sys, time, unittest, wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from c3.echo import Echo, available_backends  # noqa: E402

FIX = ROOT / 'tests' / 'fixtures' / 'c3'
R = 16000
F = 160
BACKENDS = available_backends()
RESULTS = {}


def load(name):
    with wave.open(str(FIX / name), 'rb') as w:
        return np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(np.float64) / 32768


FAR, NEAR = load('echo_far.wav'), load('echo_near.wav')


def room(seed=3):
    rng = np.random.default_rng(seed)
    n = int(0.15 * R)
    t = np.arange(n)
    h = rng.standard_normal(n) * 0.054 * np.exp(-6.9 * t / n)
    h[:16] = 0
    h[0] = 1.0
    for d, g in ((35, 0.45), (90, -0.3), (140, 0.25), (180, -0.2)):
        h[d] += g
    return h


H = room()


def conv(x, h):
    n = 1 << int(np.ceil(np.log2(len(x) + len(h))))
    return np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(h, n), n)[:len(x)]


def build(total_s, jobs, near=(), cancel=None, flush_ms=100, seed=1):
    """jobs: [(play_s, offset_ms, signal, [append signals])]; near: [(start_s, signal, gain)].
    Returns (mic int16, echo-only float, near-only float, actions{sample: [...]})."""
    n = int(total_s * R)
    sp = np.zeros(n)
    actions = {}
    for play_s, off_ms, sig, extra in jobs:
        s = int(play_s * R)
        ref = np.concatenate([sig] + list(extra))
        actions.setdefault(s, []).append(('play', (sig * 32767).astype('<i2').tobytes(), True))
        for e in extra:    # appended chunks handed over right after the first (gapless)
            actions[s].append(('play', (e * 32767).astype('<i2').tobytes(), False))
        if cancel is not None and cancel >= play_s:
            c = int(cancel * R)
            actions.setdefault(c, []).append(('cancel',))
            ref = ref[:max(0, c + R * flush_ms // 1000 - s - R * off_ms // 1000)]
        a = s + R * off_ms // 1000
        k = min(len(ref), n - a)
        sp[a:a + k] += ref[:k]
    echo = 0.8 * conv(np.tanh(1.5 * sp) / 1.5, H)
    nr = np.zeros(n)
    for start_s, sig, g in near:
        a = int(start_s * R)
        k = min(len(sig), n - a)
        nr[a:a + k] += g * sig[:k]
    noise = np.random.default_rng(seed).standard_normal(n) * 10 ** (-50 / 20)
    mic = np.clip(np.round((echo + nr + noise) * 32768), -32768, 32767).astype('<i2')
    return mic, echo, nr, actions


def run(ec, mic, actions, chunks=None):
    """Feed mic in chunks (sample counts; default 10 ms), firing actions at their sample index.
    Returns (output int16 aligned to the mic, i.e. the 10 ms lag removed, states per 10 ms)."""
    out, states = [], []
    marks = sorted(actions)
    i, ci = 0, 0
    sizes = chunks or [F]
    while i < len(mic):
        while marks and marks[0] <= i:
            for act in actions[marks.pop(0)]:
                ec.play(act[1], act[2]) if act[0] == 'play' else ec.cancel()
        n = sizes[ci % len(sizes)]
        ci += 1
        stop = min(len(mic), i + n, marks[0] if marks else len(mic))
        if stop == i:
            continue
        o = ec.process(mic[i:stop].tobytes())
        assert len(o) == (stop - i) * 2
        out.append(o)
        if chunks is None:
            states.append(ec.state)
        i = stop
    y = np.frombuffer(b''.join(out), '<i2')
    return np.concatenate([y[F:], np.zeros(F, '<i2')]), states


def frame_e(x):
    x = np.asarray(x, np.float64)[:len(x) // F * F].reshape(-1, F)
    return (x * x).sum(1)


def db(a, b):
    return 10 * np.log10(max(a, 1e-12) / max(b, 1e-12))


class Base:
    backend = None
    erle_min = None

    def echo(self, **kw):
        return Echo(backend=self.backend, **kw)

    def scenario(self, off_ms):
        # silence 1 s, then a 13.8 s job (far twice, appended), near-end talker from 8.5 s
        jobs = [(1.0, off_ms, FAR, [FAR])]
        near = [(8.5, NEAR, 0.6)]
        mic, echo, nr, acts = build(17.0, jobs, near)
        ec = self.echo()
        t0 = time.perf_counter()
        y, st = run(ec, mic, acts)
        return ec, mic, echo, nr, y, st, time.perf_counter() - t0

    def check_offset(self, off_ms):
        ec, mic, echo, nr, y, st, dt = self.scenario(off_ms)
        onset = 1.0 + off_ms / 1000                       # FAR starts at sample 0 of the job
        # 1. delay within +-10 ms, confident within 1 s of echo onset
        first = next(i for i, s in enumerate(st) if s['delay_confident'])
        t_conf = (first + 1) * F / R
        self.assertLessEqual(t_conf, onset + 1.0, f'confident at {t_conf:.2f}s, onset {onset:.2f}s')
        err = st[first]['delay_ms'] - off_ms
        self.assertLessEqual(abs(err), 10)
        self.assertLessEqual(abs(st[int((onset + 6) * R / F)]['delay_ms'] - off_ms), 10)
        # 2. single-talk ERLE over onset+2 .. onset+6 s (before the near-end talker), echo frames only
        a, b = int((onset + 2) * R / F), int(min(onset + 6, 8.4) * R / F)
        ee, me, oe = frame_e(echo * 32768)[a:b], frame_e(mic)[a:b], frame_e(y)[a:b]
        m = ee > (10 ** (-40 / 20) * 32768) ** 2 * F
        erle = db(me[m].sum(), oe[m].sum())
        self.assertGreaterEqual(erle, self.erle_min, f'ERLE {erle:.1f} dB')
        # converged after < 2 s of echo (webrtc only claims it at >= 15 dB)
        conv = [i for i, s in enumerate(st) if s['converged']]
        if self.backend == 'webrtc':
            self.assertTrue(conv)
            self.assertLessEqual(conv[0] * F / R, onset + 2.0)
        # 3. double talk: near-end energy in the output within 6 dB of its clean level
        a, b = int(8.6 * R / F), int(min(1.0 + 13.8 + off_ms / 1000, 8.5 + 4.85) * R / F)
        ne, ee, oe = frame_e(nr * 32768)[a:b], frame_e(echo * 32768)[a:b], frame_e(y)[a:b]
        m = (ne > (10 ** (-35 / 20) * 32768) ** 2 * F) & (ee > (10 ** (-40 / 20) * 32768) ** 2 * F)
        self.assertGreater(m.sum(), 50)
        dtl = db(oe[m].sum(), ne[m].sum())
        self.assertLessEqual(abs(dtl), 6.0, f'double-talk {dtl:+.1f} dB')
        echo_dt = db(me_dt := frame_e(mic)[a:b][m].sum(), ne[m].sum())
        RESULTS[(self.backend, off_ms)] = dict(delay_err_ms=round(err, 1), confident_after_s=round(t_conf - onset, 2),
                                              erle_db=round(erle, 1), state_erle_db=st[int(8.4 * R / F)]['erle_db'],
                                              converged_after_s=round(conv[0] * F / R - onset, 2) if conv else None,
                                              dt_near_db=round(dtl, 1), dt_mic_over_near_db=round(echo_dt, 1),
                                              conv_during_dt=sum(st[i]['converged'] for i in range(a, b)) / (b - a))

    def test_offset_180(self):
        self.check_offset(180)

    def test_offset_900(self):
        self.check_offset(900)

    def test_flags_and_second_job(self):
        jobs = [(1.0, 400, FAR[:3 * R], []), (6.0, 1100, FAR[3 * R:], [])]
        mic, echo, nr, acts = build(12.0, jobs)
        ec = self.echo()
        y, st = run(ec, mic, acts)
        at = lambda t: st[int(t * R / F) - 1]
        for t in (0.5, 0.99):                              # before playback
            self.assertFalse(at(t)['echo_active'])
            self.assertFalse(at(t)['converged'])
            self.assertFalse(at(t)['delay_confident'])
        # first job: active through the echo, then for tail_ms after the aligned reference ends
        ref_end = 1.0 + 0.4 + self.last_energy(FAR[:3 * R])
        for t in np.arange(1.5, ref_end, 0.05):
            if self.ref_energy_near(FAR[:3 * R], t - 1.4):
                self.assertTrue(at(t)['echo_active'], t)
        self.assertTrue(at(ref_end + 0.25)['echo_active'])
        self.assertFalse(at(ref_end + 0.35)['echo_active'])
        # second job: converged drops (delay unknown) at its start, comes back once aligned
        self.assertFalse(at(6.05)['converged'])
        self.assertFalse(at(6.05)['delay_confident'])
        self.assertTrue(at(6.05)['echo_active'])           # unknown delay: conservative
        first = next(i for i in range(int(6.0 * R / F), len(st)) if st[i]['delay_confident'])
        self.assertLessEqual(first * F / R, 7.1 + 1.0)
        self.assertLessEqual(abs(st[first]['delay_ms'] - 1100), 10)
        conv = [i for i in range(int(6.0 * R / F), len(st)) if st[i]['converged']]
        if self.backend == 'webrtc':
            self.assertTrue(conv)
        if conv:
            self.assertGreaterEqual(conv[0], first)
        self.assertEqual(st[-1]['jobs'], 2)

    @staticmethod
    def last_energy(x):
        e = frame_e(x * 32768)
        return (np.nonzero(e > (10 ** (-55 / 20) * 32768) ** 2 * F)[0][-1] + 1) * F / R

    @staticmethod
    def ref_energy_near(x, t, win=0.3):
        e = frame_e(x * 32768)
        a, b = max(0, int((t - win) * R / F)), int(t * R / F) + 1
        return (e[a:b] > (10 ** (-55 / 20) * 32768) ** 2 * F).any()

    def test_cancel_keeps_tail(self):
        off, c = 300, 4.0                                   # cancel 2.7 s into the echo
        mic, echo, nr, acts = build(7.0, [(1.0, off, FAR, [])], cancel=c)
        ec = self.echo()
        y, st = run(ec, mic, acts)
        # echo of what already played (until c + 100 ms flush) is still cancelled
        a, b = int(c * R / F), int((c + 0.4) * R / F)
        ee, me, oe = frame_e(echo * 32768)[a:b], frame_e(mic)[a:b], frame_e(y)[a:b]
        m = ee > (10 ** (-45 / 20) * 32768) ** 2 * F
        self.assertGreater(m.sum(), 5)
        self.assertGreaterEqual(db(me[m].sum(), oe[m].sum()), self.erle_min - 3)
        at = lambda t: st[int(t * R / F) - 1]
        self.assertTrue(at(c + 0.2)['echo_active'])
        # reference kept to c + 250 ms on the echo timeline; echo_active ends tail_ms after
        self.assertFalse(at(c + 0.25 + 0.3 + 0.05)['echo_active'])

    def test_no_echo_never_confident(self):
        # speaker muted: only noise and an unrelated talker reach the mic
        mic, _, _, _ = build(6.0, [], [(1.5, NEAR, 0.6)])
        acts = {R: [('play', (FAR * 32767).astype('<i2').tobytes(), True)]}
        _, st = run(self.echo(), mic, acts)
        self.assertFalse(any(s['delay_confident'] or s['converged'] for s in st))
        self.assertTrue(st[int(3.0 * R / F)]['echo_active'])   # unknown delay: conservative

    def test_chunking_invariant(self):
        mic, echo, nr, acts = build(5.0, [(0.7, 250, FAR[:3 * R], [FAR[3 * R:4 * R]])], [(3.0, NEAR[:R], 0.6)])
        y1, _ = run(self.echo(), mic, acts)
        y2, _ = run(self.echo(), mic, acts, chunks=[1600, 1, 333, 7, 4000, 160, 2])  # samples
        np.testing.assert_array_equal(y1, y2)
        ec = self.echo()                                    # odd byte counts too
        b = mic.tobytes()
        out = bytearray()
        for i in range(0, len(b), 1001):
            out += ec.process(b[i:i + 1001])
        self.assertEqual(len(out), len(b))

    def test_speed(self):
        mic, echo, nr, acts = build(60.0, [(1.0 + 7.5 * i, 200 + 100 * i, FAR, []) for i in range(7)],
                                    [(5.0, NEAR, 0.6), (30.0, NEAR, 0.6)])
        ec = self.echo()
        t0 = time.perf_counter()
        run(ec, mic, acts, chunks=[1600])
        dt = time.perf_counter() - t0
        RESULTS[(self.backend, 'speed_60s')] = round(dt, 2)
        self.assertLess(dt, 3.0)


@unittest.skipUnless('webrtc' in BACKENDS, 'build/c3/libc3aec.so not built (make -C host/c3/aec)')
class WebRtcEcho(Base, unittest.TestCase):
    backend, erle_min = 'webrtc', 20


@unittest.skipUnless('speex' in BACKENDS, 'libspeexdsp not installed')
class SpeexEcho(Base, unittest.TestCase):
    backend, erle_min = 'speex', 12


class Api(unittest.TestCase):
    def test_auto_prefers_webrtc(self):
        self.assertEqual(Echo().state['backend'], BACKENDS[0])

    def test_passthrough_without_reference(self):
        ec = Echo()
        x = (np.random.default_rng(0).standard_normal(R) * 3000).astype('<i2').tobytes()
        y = ec.process(x)
        self.assertEqual(len(y), len(x))
        s = ec.state
        self.assertFalse(s['echo_active'])
        self.assertIsNone(s['delay_ms'])
        self.assertEqual(s['frames'], R // F)


def tearDownModule():
    if RESULTS and '-v' in sys.argv:
        for k, v in sorted(RESULTS.items(), key=str):
            print(k, v)


if __name__ == '__main__':
    unittest.main()
