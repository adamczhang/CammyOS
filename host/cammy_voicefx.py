"""Speaker-friendly voice chain for Cammy's tiny speaker (PC side).

Takes 24 kHz mono speech (Gemini Live, Kokoro) and returns 16 kHz S16 PCM ready for
/v1/speaker/play: trims silence, resamples band-limited, cuts bass below about 300 Hz
(the small cone overdrives and crackles on it), softens treble above 5 kHz, applies a
smooth look-ahead compressor, and scales to a preset peak.

Levels chosen by Adam on 2026-11-16 by ear at LINEOUT 27: "normal" peak 6000 (default),
"loud" peak 9000; 13000 clipped badly, so the camera caps samples at 10000.
"""
import numpy as np

LEVELS = {'normal': 6000, 'loud': 9000}
STREAM_PREROLL_SAMPLES = 1200  # up to 50 ms at 24 kHz before the first loud sample
STREAM_FADE_SAMPLES = 160      # 10 ms at 16 kHz
STREAM_TAIL_SAMPLES = 1600     # 100 ms at 16 kHz

def process(x24, hpf=300.0, shelf_hz=5000.0, shelf_db=-6.0, ratio=4.0, thr_db=-24.0):
    x = np.asarray(x24, np.float64)
    a = np.abs(x); k = np.where(a > a.max() * 0.02)[0]          # trim leading/trailing silence
    if len(k): x = x[max(0, k[0] - 1200):k[-1] + 2400]
    n = len(x) * 2 // 3
    X = np.fft.rfft(x)[:n // 2 + 1]                               # band-limited 24k -> 16k
    f = np.fft.rfftfreq(n, 1 / 16000)
    if hpf: X = X * np.clip((f - hpf * 0.6) / (hpf * 0.4), 0, 1) ** 2
    if shelf_db: X = X * np.where(f > shelf_hz, 10 ** (shelf_db / 20), 1.0) ** np.clip((f - shelf_hz * 0.8) / (shelf_hz * 0.2), 0, 1)
    y = np.fft.irfft(X, n); y /= max(1e-9, np.abs(y).max())
    if ratio > 1:                                                 # smooth envelope compressor
        pk = np.lib.stride_tricks.sliding_window_view(np.pad(np.abs(y), 80, mode='edge'), 161).max(axis=1)
        env = np.empty_like(y); e = 0.0; rel = np.exp(-1 / (0.06 * 16000))   # look-ahead peak hold, 60 ms release
        for i, v in enumerate(pk):
            e = v if v > e else rel * e + (1 - rel) * v
            env[i] = e
        thr = 10 ** (thr_db / 20)
        g = np.where(env > thr, (np.maximum(env, 1e-9) / thr) ** (1 / ratio - 1), 1.0)
        y = y * g; y /= max(1e-9, np.abs(y).max())
    w = np.ones(len(y)); m = 160; w[:m] = np.linspace(0, 1, m); w[-m:] = np.linspace(1, 0, m)
    return np.concatenate([np.zeros(1600), y * w, np.zeros(1600)])


def speaker_pcm(pcm24, level='normal'):
    """24 kHz S16LE mono bytes -> 16 kHz S16LE mono bytes at the named level."""
    x = np.frombuffer(pcm24, '<i2').astype(np.float64)
    if not len(x) or not np.abs(x).max():
        raise ValueError('No speech audio')
    y = np.clip(process(x) * LEVELS[level], -LEVELS[level], LEVELS[level])
    return y.astype('<i2').tobytes()


def _stream_fir(taps=1601, rate=48000, hpf=300.0, shelf_hz=5000.0, shelf_db=-6.0, cut=7200.0, stop=7900.0):
    """One linear-phase FIR at 48 kHz: the anti-alias low-pass for 16 kHz output, the same
    bass cut and treble shelf as process(), and x2 gain for the zero-stuffed input."""
    n = 1 << 15
    f = np.fft.rfftfreq(n, 1 / rate)
    h = np.clip((f - hpf * 0.6) / (hpf * 0.4), 0, 1) ** 2
    h = h * np.where(f > shelf_hz, 10 ** (shelf_db / 20), 1.0) ** np.clip((f - shelf_hz * 0.8) / (shelf_hz * 0.2), 0, 1)
    h = h * np.clip((stop - f) / (stop - cut), 0, 1)
    ir = np.fft.irfft(h, n)
    ir = np.roll(ir, taps // 2)[:taps] * np.hanning(taps)
    return ir * 2.0


class Stream:
    """The same voice chain as speaker_pcm(), for a reply that arrives in blocks.

    speaker_pcm() works on one finished clip: it trims silence, normalizes and pads 100 ms
    of zeros on both ends. Fed block by block, that left ~200 ms gaps and a loudness jump at
    every 1-2 s block of a long reply (audit 2026-11-16). Stream keeps the filter history,
    the compressor envelope and the gain across blocks: one fade-in at the
    start of a reply, one fade-out and 100 ms tail at its end (end=True), nothing between.
    Gain only ever drops within a reply (running peak), so a loud word cannot clip and the
    level never pumps up. 24 kHz S16LE in, 16 kHz S16LE out."""

    FIR = None

    def __init__(self, level='normal', ratio=4.0, thr_db=-24.0, silence=300):
        if Stream.FIR is None:
            Stream.FIR = _stream_fir()
        self.peak_out = LEVELS[level]
        self.ratio, self.thr = ratio, 10 ** (thr_db / 20)
        self.silence = silence
        self.hist = np.zeros(len(self.FIR) - 1)     # 48 kHz zero-stuffed input history
        self.phase = 0                              # decimation phase across blocks
        self.started = False
        self.p_in = self.p_out = 1e-9
        self.env = 0.0
        self.rel = np.exp(-1 / (0.06 * 16000))
        self.abs_hist = np.zeros(160)               # look-ahead window of the compressor
        self.delay = np.zeros(80)

    def _filter(self, x24):
        up = np.zeros(len(x24) * 2)
        up[::2] = x24
        full = np.concatenate([self.hist, up])
        n = len(full) + len(self.FIR) - 1
        size = 1 << (n - 1).bit_length()
        y = np.fft.irfft(np.fft.rfft(full, size) * np.fft.rfft(self.FIR, size), size)
        y = y[len(self.FIR) - 1:len(full)]          # valid part: exactly len(up) samples
        self.hist = full[-(len(self.FIR) - 1):]
        out = y[(-self.phase) % 3::3]
        self.phase = (self.phase + len(up)) % 3
        return out

    def _compress(self, y):
        a = np.abs(y)
        window = np.concatenate([self.abs_hist, a])
        pk = np.lib.stride_tricks.sliding_window_view(window, 161).max(axis=1)
        self.abs_hist = window[-160:]
        env = np.empty_like(pk)
        e, rel = self.env, self.rel
        for i, v in enumerate(pk):
            e = v if v > e else rel * e + (1 - rel) * v
            env[i] = e
        self.env = e
        thr = self.thr
        g = np.where(env > thr, (np.maximum(env, 1e-12) / thr) ** (1 / self.ratio - 1), 1.0)
        delayed = np.concatenate([self.delay, y])   # signal 80 samples behind its envelope
        self.delay = delayed[-80:]
        return delayed[:len(y)] * g

    @staticmethod
    def _running_peak(y, start):
        """Per-sample running peak: the gain never depends on how the reply was split."""
        p = np.maximum.accumulate(np.concatenate([[start], np.abs(y)]))
        return p[1:], p[-1]

    def _scale(self, y):
        p, self.p_out = self._running_peak(y, self.p_out)
        return np.clip(y / p * self.peak_out, -self.peak_out, self.peak_out)

    def feed(self, pcm24, end=False):
        x = np.frombuffer(pcm24, '<i2').astype(np.float64) if pcm24 else np.zeros(0)
        starting = not self.started
        if starting:
            loud = np.where(np.abs(x) > self.silence)[0]
            if not len(loud):
                return b''                          # nothing but leading silence so far
            x = x[max(0, loud[0] - STREAM_PREROLL_SAMPLES):]
            self.started = True
        if end:
            x = np.concatenate([x, np.zeros(len(self.FIR) // 2 + 240)])   # flush FIR + compressor
        y = self._filter(x)
        p, self.p_in = self._running_peak(y, self.p_in)
        y = self._scale(self._compress(y / p))
        if starting:
            m = min(STREAM_FADE_SAMPLES, len(y))
            y[:m] *= np.linspace(0, 1, m)
        if end:
            m = min(STREAM_FADE_SAMPLES, len(y))
            y[-m:] *= np.linspace(1, 0, m)
            y = np.concatenate([y, np.zeros(STREAM_TAIL_SAMPLES)])
        return y.astype('<i2').tobytes()
