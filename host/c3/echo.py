"""Acoustic echo canceller for C3: removes Cammy's own voice from Cammy's microphone.

Role. Cammy's mic reaches the PC as PCM S16LE mono 16 kHz; C3 sends replies to the
camera's speaker as the same format, one job per reply (later chunks of a reply are
appended gaplessly). Speaker and mic share one codec, so there is no clock drift,
but the offset D from "C3 handed this PCM over" to "its echo is in the mic stream"
differs per job (Wi-Fi, portal queue, playback start: roughly 50-1200 ms) and is
constant within a job. Echo.process() returns the cleaned mic for the speech gate
(listener.py). C3 reads state: if echo_active and not converged it treats the turn
as half-duplex (gate ignores the mic); if converged it runs the gate on the cleaned
mic with stricter thresholds, so Adam can barge in over Cammy.

Alignment. The backends (WebRTC AEC3 or SpeexDSP) cope badly with a large unknown
offset, so Echo aligns the reference itself. play() places the job on the mic
timeline at the mic sample count at handover. Every 100 ms until the job's delay is
known, GCC-PHAT (numpy FFT, 150-5000 Hz) correlates up to 1 s of reference from its
first energetic frame with the mic over lags 0..max_delay_ms; a peak at least 2x the
largest side lobe (outside +-2 ms) counts as an estimate, and two estimates within
2 ms make the delay confident (median). While the job plays, 0.5 s windows refine it
(+-50 ms search; a shift of 2 ms or more in the median of the last 5 moves it).
The backend then gets the reference delayed by D minus a 10 ms lead (so its residual
delay is small and causal; WebRTC also gets set_stream_delay_ms(10)). A job with no
confident delay feeds a zero reference: its echo is not cancelled and converged is
False, so C3 stays half-duplex for it. The adaptive filter persists across jobs: once
aligned, the residual echo path (room + speaker) is the same for every job.

Flags. echo_active: aligned reference energy (above -55 dBFS) now or within tail_ms;
for a job whose delay is not yet known, conservatively from handover until
end + max_delay_ms + tail_ms. erle_db: per-frame 10*log10(mic energy / output
energy) over the last 50 single-talk echo frames, reported as the 70th percentile; a
frame counts as single-talk when the aligned reference is above -45 dBFS and
mic/reference energy is within 3 dB of the 25th percentile of that ratio over the
last 2 s (the echo-path loss). Weak near-end speech can hide inside that 3 dB, so the
percentile reads the filter through the near-end talker's pauses: double-talk does
not drag the number down and converged survives barge-in. converged: the current
job's delay is confident and erle_db >= 15 over at least 25 such frames.

WebRTC tuning. The 1.3 API cannot configure AEC3, but the library reads WebRTC
field trials (process-wide, when an AudioProcessing is created). AEC3_TRIALS makes
the suppressor's near-end (double-talk) state more transparent and quicker to enter;
with defaults the tests lost ~6.5 dB of the near-end talker, with it ~4 dB, and
single-talk ERLE is unchanged. AGC is off, high-pass on, noise suppression moderate.

Other choices. The mic always goes through the backend, even with no reference
(one consistent output path: high-pass, noise suppression). Output is the input
delayed by exactly one 10 ms frame (primed with 320 zero bytes), so every call
returns as many bytes as it was given whatever the chunking. cancel() keeps the
reference up to 250 ms past the mic position at the time of the call (flush latency
is unknown; keeping reference that never played costs little, dropping reference
that did play leaves echo), ends the job, and the echo of what played stays
cancelled through tail_ms. A chunk handed over after the job's audio would already
have played out is placed after zero padding (speaker underrun, same offset).

Numbers (tests/test_c3_echo.py, synthetic: Kokoro speech, 150 ms room response,
tanh speaker clip, echo louder than the talker, -50 dBFS noise, offsets 180/900 ms):
delay error 0 ms, confident 0.41-0.43 s after echo onset (both backends). Single-talk
ERLE 2-6 s after onset: WebRTC 37 dB (converged 1.1 s after onset), Speex 24 dB
(converged ~2.5 s). Double-talk, output vs clean near-end level: WebRTC -3 to -4 dB,
Speex -0.5 dB (it keeps more residual echo). 60 s of audio: ~0.5 s (WebRTC), ~0.4 s
(Speex) on this PC.

Limits. Speaker nonlinearity bounds the linear filter (WebRTC's suppressor hides
most of the rest; Speex's residual suppressor is weaker). The tests use a synthetic
room and speaker; Cammy's real speaker response, its distortion at volume and the
true offset spread are unknown until a hardware calibration with a test clip on
Cammy (done later by the parent, with Adam's consent). A delay change inside a job
larger than 50 ms is not followed (refinement window). Synchronous, single thread.
"""
from collections import deque
import ctypes, ctypes.util, math
from pathlib import Path
import numpy as np

SHIM = Path(__file__).resolve().parents[2] / 'build' / 'c3' / 'libc3aec.so'
SPEEXDSP = '/usr/lib/x86_64-linux-gnu/libspeexdsp.so.1'
LEAD_MS = 10            # reference fed this far ahead of its echo
ACTIVE_DBFS = -55.0     # aligned reference above this = echo present
SINGLE_DBFS = -45.0     # aligned reference above this may count toward ERLE
GCC_WIN_S = 1.0         # longest reference window for the first estimate
GCC_MIN_S = 0.25        # shortest overlap worth correlating
PSR_MIN = 2.0           # GCC peak / largest side lobe
AGREE_MS = 2.0          # two estimates this close = confident
REFINE_S, REFINE_MS = 0.5, 50
CANCEL_KEEP_MS = 250
ST_RATIO = 2.0          # mic/ref energy within 3 dB of the echo-path loss = single-talk
ERLE_PCT = 70           # near-end pauses inside double-talk still show the filter's ERLE
# AEC3 suppressor in its near-end (double-talk) state: more transparent masks and a
# faster switch into it, so Adam's voice survives barge-in (defaults lost ~6.5 dB of
# it in the tests; these ~4 dB). Single-talk tuning is untouched. WebRTC field trials
# are process-wide and read when an AudioProcessing is created.
AEC3_TRIALS = ''.join(f'WebRTC-Aec3Suppressor{k}Override/{v}/' for k, v in (
    ('NearendLfMaskTransparent', 2), ('NearendLfMaskSuppress', 2.1),
    ('NearendHfMaskTransparent', 1), ('NearendHfMaskSuppress', 1.2),
    ('DominantNearendTriggerThreshold', 2), ('DominantNearendSnrThreshold', 10)))


def _db_energy(dbfs, n):
    return (10 ** (dbfs / 20) * 32768) ** 2 * n


class _WebRtc:
    name = 'webrtc'

    def __init__(self, rate, frame, tail_ms, ns):
        lib = ctypes.CDLL(str(SHIM))
        lib.c3aec_create.restype = ctypes.c_void_p
        lib.c3aec_create.argtypes = [ctypes.c_int, ctypes.c_int]
        for f in ('c3aec_process', 'c3aec_process_reverse', 'c3aec_stats'):
            getattr(lib, f).argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.c3aec_set_stream_delay_ms.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.c3aec_destroy.argtypes = [ctypes.c_void_p]
        lib.c3aec_field_trials.argtypes = [ctypes.c_char_p]
        lib.c3aec_field_trials(AEC3_TRIALS.encode())
        self.lib, self.h = lib, lib.c3aec_create(rate, int(bool(ns)))
        if not self.h:
            raise OSError('c3aec_create failed')
        lib.c3aec_set_stream_delay_ms(self.h, LEAD_MS)
        self._st = np.zeros(5)

    def frame(self, mic, ref):
        self.lib.c3aec_process_reverse(self.h, ref.ctypes.data)
        self.lib.c3aec_process(self.h, mic.ctypes.data)
        return mic

    def stats(self):
        self.lib.c3aec_stats(self.h, self._st.ctypes.data)
        f = lambda v: None if math.isnan(v) else round(float(v), 1)
        return {'erl_db': f(self._st[0]), 'erle_db': f(self._st[1]), 'delay_ms': f(self._st[2])}

    def close(self):
        if self.h:
            self.lib.c3aec_destroy(self.h)
            self.h = None


class _Speex:
    name = 'speex'

    def __init__(self, rate, frame, tail_ms, ns):
        lib = ctypes.CDLL(SPEEXDSP if Path(SPEEXDSP).exists() else ctypes.util.find_library('speexdsp'))
        vp, i = ctypes.c_void_p, ctypes.c_int
        lib.speex_echo_state_init.restype = vp
        lib.speex_echo_state_init.argtypes = [i, i]
        lib.speex_preprocess_state_init.restype = vp
        lib.speex_preprocess_state_init.argtypes = [i, i]
        lib.speex_echo_ctl.argtypes = lib.speex_preprocess_ctl.argtypes = [vp, i, vp]
        lib.speex_echo_cancellation.argtypes = [vp, vp, vp, vp]
        lib.speex_preprocess_run.argtypes = [vp, vp]
        lib.speex_echo_state_destroy.argtypes = lib.speex_preprocess_state_destroy.argtypes = [vp]
        self.lib = lib
        self.echo = lib.speex_echo_state_init(frame, rate * tail_ms // 1000)
        self.pp = lib.speex_preprocess_state_init(frame, rate)
        ctl = lambda f, st, req, v: f(st, req, ctypes.cast(ctypes.pointer(ctypes.c_int(v)), vp))
        ctl(lib.speex_echo_ctl, self.echo, 24, rate)               # SPEEX_ECHO_SET_SAMPLING_RATE
        lib.speex_preprocess_ctl(self.pp, 24, self.echo)           # SPEEX_PREPROCESS_SET_ECHO_STATE
        ctl(lib.speex_preprocess_ctl, self.pp, 0, int(bool(ns)))   # SET_DENOISE
        ctl(lib.speex_preprocess_ctl, self.pp, 2, 0)               # SET_AGC off
        ctl(lib.speex_preprocess_ctl, self.pp, 20, -40)            # SET_ECHO_SUPPRESS (dB)
        ctl(lib.speex_preprocess_ctl, self.pp, 22, -15)            # SET_ECHO_SUPPRESS_ACTIVE (dB)
        self.out = np.zeros(frame, np.int16)

    def frame(self, mic, ref):
        out = self.out
        self.lib.speex_echo_cancellation(self.echo, mic.ctypes.data, ref.ctypes.data, out.ctypes.data)
        self.lib.speex_preprocess_run(self.pp, out.ctypes.data)
        return out

    def stats(self):
        return {}

    def close(self):
        if self.echo:
            self.lib.speex_preprocess_state_destroy(self.pp)
            self.lib.speex_echo_state_destroy(self.echo)
            self.echo = self.pp = None


BACKENDS = {'webrtc': _WebRtc, 'speex': _Speex}


def available_backends(rate=16000):
    """Backends that load on this machine, preferred first."""
    ok = []
    for name, cls in BACKENDS.items():
        try:
            cls(rate, rate // 100, 100, False).close()
            ok.append(name)
        except (OSError, AttributeError, TypeError):
            pass
    return ok


class _Ring:
    """Absolute-indexed float32 history of the last n mic samples."""

    def __init__(self, n):
        self.buf, self.n, self.end = np.zeros(n, np.float32), n, 0

    def push(self, x):
        i = self.end % self.n
        k = min(len(x), self.n - i)
        self.buf[i:i + k] = x[:k]
        self.buf[:len(x) - k] = x[k:]
        self.end += len(x)

    def get(self, a, b):
        a = max(a, self.end - self.n, 0)
        b = min(b, self.end)
        if b <= a:
            return a, np.zeros(0, np.float32)
        i, j = a % self.n, b % self.n
        return a, (self.buf[i:j].copy() if i < j else np.concatenate([self.buf[i:], self.buf[:j]]))


class _Job:
    def __init__(self, start):
        self.start = start          # mic sample index at handover
        self.ref = np.zeros(0, np.int16)
        self.onset = None           # first reference sample of an energetic frame
        self.delay = None           # samples, once confident
        self.cands = []             # accepted first-pass estimates
        self.refined = deque(maxlen=5)
        self.closed = False         # cancelled: further play() starts a new job
        self.next_est = 0           # mic index of the next estimate

    def seg(self, k, n):
        """ref[k:k+n] with zeros outside, as int32."""
        out = np.zeros(n, np.int32)
        a, b = max(k, 0), min(k + n, len(self.ref))
        if b > a:
            out[a - k:b - k] = self.ref[a:b]
        return out


def _gcc(ref, mic, lo, hi, rate):
    """GCC-PHAT lag in [lo, hi] of ref inside mic, and peak/side-lobe ratio."""
    n = 1 << int(math.ceil(math.log2(len(ref) + len(mic))))
    X = np.fft.rfft(mic, n) * np.conj(np.fft.rfft(ref, n))
    f = np.arange(len(X)) * rate / n
    X[(f < 150) | (f > 5000)] = 0
    X /= np.abs(X) + 1e-9
    c = np.fft.irfft(X, n)[lo:hi + 1]
    if len(c) < 3:
        return None, 0.0
    i = int(np.argmax(c))
    g = int(rate * AGREE_MS / 1000)
    side = np.concatenate([c[:max(i - g, 0)], c[i + g + 1:]])
    psr = c[i] / max(float(np.max(np.abs(side))) if len(side) else 0.0, 1e-9)
    return lo + i, float(psr)


class Echo:
    def __init__(self, rate=16000, backend='auto', max_delay_ms=1500, tail_ms=300, noise_suppression=True):
        self.rate, self.F = rate, rate // 100
        self.FB = self.F * 2
        self.max_delay = rate * max_delay_ms // 1000
        self.tail = rate * tail_ms // 1000
        self.lead = rate * LEAD_MS // 1000
        names = list(BACKENDS) if backend == 'auto' else [backend]
        err = None
        for name in names:
            try:
                self.be = BACKENDS[name](rate, self.F, tail_ms, noise_suppression)
                break
            except (OSError, AttributeError, TypeError) as e:
                err = e
        else:
            raise RuntimeError(f'no echo backend loads ({names}): {err}')
        self._hist = _Ring(self.max_delay + int(rate * (GCC_WIN_S + 1.5)))
        self._in = bytearray()
        self._out = bytearray(self.FB)       # 10 ms priming: output length == input length
        self._received = 0                   # mic bytes received
        self._pos = 0                        # mic sample index of the next frame
        self._jobs = []
        self._njobs = 0
        self._last_job = None
        self._last_ref = -10 ** 12           # mic index (frame end) of the last aligned reference energy
        self._refs = 0.0                     # smoothed aligned reference energy
        self._ratios = deque(maxlen=200)
        self._erl = None
        self._single = deque(maxlen=50)
        self._active_e = _db_energy(ACTIVE_DBFS, self.F)
        self._single_e = _db_energy(SINGLE_DBFS, self.F)
        self._noise_e = _db_energy(-60.0, self.F)

    # --- reference side ---------------------------------------------------------
    def play(self, pcm: bytes, new_job: bool) -> None:
        """C3 hands this PCM (S16LE mono) to the speaker now."""
        now = self._received // 2
        job = self._last_job
        if new_job or job is None or job.closed:
            job = _Job(now)
            job.next_est = now
            self._jobs.append(job)
            self._last_job = job
            self._njobs += 1
        else:
            played_by = job.start + len(job.ref) + (job.delay or 0)
            if now > played_by:          # speaker ran dry before this chunk arrived
                job.ref = np.concatenate([job.ref, np.zeros(now - job.start - len(job.ref), np.int16)])
        x = np.frombuffer(pcm[:len(pcm) // 2 * 2], '<i2')
        base = len(job.ref)
        job.ref = np.concatenate([job.ref, x])
        if job.onset is None:
            F = self.F
            for k in range(base - base % F, len(job.ref) - F + 1, F):
                if float(np.dot(job.ref[k:k + F].astype(np.float32), job.ref[k:k + F])) > self._active_e:
                    job.onset = k
                    break

    def cancel(self) -> None:
        """Speaker flushed: reference beyond what can have played is discarded."""
        now = self._received // 2
        for job in self._jobs:
            if not job.closed:
                cut = now + self.rate * CANCEL_KEEP_MS // 1000 - job.start - (job.delay or 0)
                job.ref = job.ref[:max(0, min(len(job.ref), cut))]
                job.closed = True

    # --- mic side -----------------------------------------------------------------
    def process(self, mic: bytes) -> bytes:
        """Mic PCM in (any size), cleaned mic out, same length, 10 ms behind."""
        self._in += mic
        self._received += len(mic)
        n = len(self._in) // self.FB * self.FB
        if n:
            frames = np.frombuffer(bytes(self._in[:n]), '<i2').reshape(-1, self.F)
            del self._in[:n]
            for row in frames:
                self._out += self._frame(row.copy()).tobytes()
        out = bytes(self._out[:len(mic)])
        del self._out[:len(mic)]
        return out

    def _frame(self, mic):
        F, p = self.F, self._pos
        mf = mic.astype(np.float32)
        self._hist.push(mf)
        self._pos = p + F
        ref = np.zeros(F, np.int32)
        ref_e, keep = 0.0, []
        for job in self._jobs:
            end = job.start + len(job.ref)
            if job.delay is None:
                if p + F >= job.next_est:
                    self._estimate(job, p + F)
                if job.delay is None:
                    if job.start <= p < end + self.max_delay and job.onset is not None:
                        self._last_ref = p + F
                    if p < end + self.max_delay + self.tail:
                        keep.append(job)
                    continue
            k = p - job.start - job.delay
            ref += job.seg(k + self.lead, F)
            a = job.seg(k, F).astype(np.float32)
            e = float(np.dot(a, a))
            ref_e += e
            if e > self._active_e:
                self._last_ref = p + F
            if p + F >= job.next_est:
                self._refine(job, p + F)
            if p < end + job.delay + self.tail + self.rate:
                keep.append(job)
        self._jobs = keep
        out = self.be.frame(mic, np.clip(ref, -32768, 32767).astype(np.int16))
        self._stats(mf, out, ref_e)
        return out

    def _estimate(self, job, M):
        """First-pass delay search over 0..max_delay on the latest mic."""
        job.next_est = M + self.rate // 10
        if job.onset is None:
            return
        r = self.rate
        a = max(job.onset, M - job.start - self.max_delay - int(r * GCC_WIN_S))
        K = min(len(job.ref) - a, M - job.start - a, int(r * GCC_WIN_S))
        if K < r * GCC_MIN_S:
            return
        m0, mic = self._hist.get(job.start + a, job.start + a + K + self.max_delay)
        if m0 != job.start + a:
            return
        hi = min(self.max_delay, len(mic) - int(r * GCC_MIN_S))
        if hi < 0:
            return
        d, psr = _gcc(job.ref[a:a + K].astype(np.float32), mic, 0, hi, r)
        if d is None or psr < PSR_MIN:
            return
        job.cands.append(d)
        g = r * AGREE_MS / 1000
        if len(job.cands) >= 2 and abs(job.cands[-1] - job.cands[-2]) <= g:
            job.delay = int(np.median(job.cands[-2:]))
            job.refined.append(job.delay)
            job.next_est = M + int(r * REFINE_S)

    def _refine(self, job, M):
        """Re-estimate around the current delay on the latest fully echoed window."""
        r, K, w = self.rate, int(self.rate * REFINE_S), self.rate * REFINE_MS // 1000
        job.next_est = M + K
        a = M - job.start - job.delay - w - K
        if a < 0 or a + K > len(job.ref):
            return
        seg = job.ref[a:a + K].astype(np.float32)
        if float(np.dot(seg, seg)) < self._active_e * K / self.F * 10:
            return
        m0, mic = self._hist.get(job.start + a + job.delay - w, job.start + a + job.delay + K + w)
        if len(mic) < K + 2 * w:
            return
        d, psr = _gcc(seg, mic, 0, 2 * w, r)
        if d is None or psr < PSR_MIN:
            return
        job.refined.append(job.delay - w + d)
        med = int(np.median(job.refined))
        if abs(med - job.delay) >= r * AGREE_MS / 1000:
            job.delay = med

    def _stats(self, mf, out, ref_e):
        self._refs = max(ref_e, self._refs * 0.7)
        job = self._last_job
        if job is None or job.delay is None or self._refs < self._single_e:
            return
        mic_e = float(np.dot(mf, mf))
        if mic_e < self._noise_e:
            return
        ratio = mic_e / self._refs
        self._ratios.append(ratio)
        if len(self._ratios) >= 10 and (self._erl is None or len(self._ratios) % 5 == 0):
            self._erl = float(np.percentile(self._ratios, 25))
        if self._erl is not None and ratio <= self._erl * ST_RATIO:
            o = out.astype(np.float32)
            self._single.append(10 * math.log10(mic_e / max(float(np.dot(o, o)), 1.0)))

    # --- flags ----------------------------------------------------------------------
    @property
    def erle_db(self):
        """ERLE_PCT percentile of per-frame ERLE over the last 50 single-talk frames."""
        return round(float(np.percentile(self._single, ERLE_PCT)), 1) if self._single else None

    @property
    def state(self) -> dict:
        job = self._last_job
        erle = self.erle_db
        conf = job is not None and job.delay is not None
        return {
            'backend': self.be.name,
            'delay_ms': None if job is None or job.delay is None else round(job.delay * 1000 / self.rate, 1),
            'delay_confident': conf,
            'echo_active': self._pos - self._last_ref <= self.tail,
            'erle_db': erle,
            'converged': conf and erle is not None and len(self._single) >= 25 and erle >= 15,
            'frames': self._pos // self.F,
            'jobs': self._njobs,
            'aec': self.be.stats(),
        }

    def close(self):
        self.be.close()

    def __del__(self):
        try:
            self.be.close()
        except Exception:
            pass
