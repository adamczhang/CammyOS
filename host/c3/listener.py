"""Speech gate for C3: decides when a person is talking to Cammy.

Cammy's microphone reaches the PC as raw PCM (S16LE, mono, 16 kHz) in chunks of
any size. Only speech goes on to Gemini Live, framed by explicit activity_start
and activity_end, because every second of silence sent costs API tokens and
gives the model a chance to answer noise.

The gate scores 32 ms frames (512 samples) with Silero VAD, the ONNX model that
ships with pipecat, fed exactly as pipecat feeds it: 64 samples of context in
front of each frame, a recurrent state, and a state reset after about 2 s of
audio once the room is clearly quiet. Frames quieter than floor_dbfs (-58 dBFS)
are scored 0 without running the model.

Hysteresis: an utterance opens after start_ms (320 ms, 10 frames; 192 ms let short
noises through on Cammy, 2026-11-16) of frames at or
above start_prob (0.5) and closes after end_ms (480 ms, 15 frames) of frames below
end_prob (0.35). Frames in between keep an open utterance open. On opening, the
'start' event carries preroll_ms (640 ms) of audio from before the trigger plus
the trigger frames, so the first syllable is not clipped. An utterance is cut at
max_utterance_s (60 s) so a stuck gate cannot stream forever.

Levels: each 'start' carries the onset level (mean power of the trigger frames, dBFS)
and the room's noise floor (a slow average of frames the model scored as silence);
each 'end' carries the level of the utterance's speech frames. c3/gate.py uses them to
tell a person talking to Cammy from speech across the room.

Cammy is half-duplex: while its own speaker plays (the caller includes the tail),
feed(..., playing=True) drops the mic audio, ends any open utterance, and clears
the pre-roll and model state, so Cammy's own voice never becomes pre-roll.
"""
from dataclasses import dataclass
from collections import deque
import importlib.util, math
import time
from pathlib import Path
import numpy as np

FRAME = 512                 # samples per frame (Silero v5 at 16 kHz)
FRAME_BYTES = FRAME * 2
CONTEXT = 64                # samples of the previous frame prepended to each frame
RESET_EVERY_S = 2.0         # pipecat: reset the model state about every 2 s of audio,
RESET_MAX_PROB = 0.1        # but only when the gate is closed and the last
RESET_LOW_FRAMES = 5        # 5 frames (160 ms) all scored below 0.1
VAD_END_MS = 480             # 15 Silero frames at 16 kHz


@dataclass(frozen=True)
class Event:
    kind: str            # 'start' | 'audio' | 'end'
    pcm: bytes = b''     # 'start': pre-roll incl. trigger frames; 'audio': continuation
    reason: str = ''     # 'end': 'silence' | 'playback' | 'max_length' | 'reset'
    seconds: float = 0.0 # 'end': utterance length including pre-roll
    level: float = None  # 'start': trigger frames, 'end': all speech frames (dBFS)
    noise: float = None  # 'start': the room's noise floor (dBFS), None before it is known


def dbfs(power):
    """Mean square amplitude (full scale 1.0) in dB."""
    return round(10 * math.log10(max(power, 1e-12)), 1)


def default_model_path():
    """silero_vad.onnx inside this interpreter's pipecat install, or None."""
    try:
        spec = importlib.util.find_spec('pipecat')
    except (ImportError, ValueError):
        return None
    for root in (spec.submodule_search_locations or []) if spec else []:
        p = Path(root) / 'audio' / 'vad' / 'data' / 'silero_vad.onnx'
        if p.is_file():
            return str(p)
    return None


class SileroVAD:
    """Silero VAD v5 ONNX: input [1, 64+512] float32, state [2,1,128], sr int64 -> prob."""

    def __init__(self, path, rate=16000):
        import onnxruntime
        o = onnxruntime.SessionOptions()
        o.intra_op_num_threads = o.inter_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(path), sess_options=o,
                                                    providers=['CPUExecutionProvider'])
        self.sr = np.array(rate, dtype=np.int64)
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros((1, CONTEXT), np.float32)

    def __call__(self, frame):
        x = np.concatenate((self.context, frame.reshape(1, -1)), axis=1)
        out, self.state = self.session.run(None, {'input': x, 'state': self.state, 'sr': self.sr})
        self.context = x[:, -CONTEXT:]
        return float(out[0, 0])

    def skip(self, frame):
        """Frame below the energy floor: not scored, but keeps the context continuous."""
        self.context = frame[-CONTEXT:].reshape(1, -1).astype(np.float32)


def _frames(ms, rate):
    return max(1, round(ms * rate / 1000 / FRAME))


class Listener:
    def __init__(self, model_path=None, *, rate=16000, start_prob=0.5, end_prob=0.35,
                 start_ms=320, end_ms=VAD_END_MS, preroll_ms=640, max_utterance_s=60.0,
                 floor_dbfs=-58.0, strict_prob=0.8, strict_ms=320, vad=None):
        if rate != 16000:
            raise ValueError('Listener expects 16 kHz audio (Silero frames are 512 samples)')
        if vad is None:
            model_path = model_path or default_model_path()
            if not model_path or not Path(model_path).is_file():
                raise RuntimeError('no Silero VAD model: install pipecat in this interpreter '
                                   'or pass model_path= or vad=')
            vad = SileroVAD(model_path, rate)
        self.vad, self.rate = vad, rate
        self.start_prob, self.end_prob = start_prob, end_prob
        self.start_n, self.end_n = _frames(start_ms, rate), _frames(end_ms, rate)
        self.preroll_n = round(preroll_ms * rate / 1000 / FRAME)
        self.max_n = max(1, int(max_utterance_s * rate / FRAME))
        self.floor = 10 ** (floor_dbfs / 20)  # RMS amplitude, full scale = 1.0
        # strict: barge-in while Cammy speaks (full duplex, after Echo): surer and longer
        self.strict_prob, self.strict_n = strict_prob, _frames(strict_ms, rate)
        self.ring = deque(maxlen=self.preroll_n + max(self.start_n, self.strict_n))
        self.buf = bytearray()
        self._stats = dict(frames_in=0, frames_speech=0, frames_sent=0, utterances=0,
                           frames_dropped_playback=0, model_calls=0)
        self._dropped_bytes = 0
        self.last_heard = None
        self.noise = None                     # mean power of frames scored as silence
        self._power = 0.0                     # mean power of the frame just scored
        self._speech_power, self._speech_n = 0.0, 0
        self._was_playing = False
        self._clear()

    @property
    def speaking(self):
        return self.open

    @property
    def stats(self):
        return dict(self._stats, frames_dropped_playback=self._dropped_bytes // FRAME_BYTES)

    def _clear(self):
        """Gate closed, no pre-roll, fresh model state."""
        self.open = False
        self.run = self.silence = self.utt_frames = 0
        self.ring.clear()
        self._since_reset = self._low = 0
        reset = getattr(self.vad, 'reset', None)
        if reset:
            reset()

    def _end(self, reason):
        ev = Event('end', reason=reason, seconds=self.utt_frames * FRAME / self.rate,
                   level=dbfs(self._speech_power / self._speech_n) if self._speech_n else None)
        self.open = False
        self.run = self.silence = self.utt_frames = 0
        self.ring.clear()  # the hangover was already sent; never resend it as pre-roll
        return ev

    def reset(self):
        events = [self._end('reset')] if self.open else []
        self.buf.clear()
        self._clear()
        return events

    def _prob(self, frame_bytes):
        x = np.frombuffer(frame_bytes, '<i2').astype(np.float32) / 32768.0
        self._power = float(np.dot(x, x)) / FRAME
        if math.sqrt(self._power) < self.floor:
            skip = getattr(self.vad, 'skip', None)
            if skip:
                skip(x)
            p = 0.0
        else:
            self._stats['model_calls'] += 1
            p = float(self.vad(x))
        # pipecat's periodic model reset: only while closed and clearly quiet
        self._since_reset += 1
        self._low = self._low + 1 if p < RESET_MAX_PROB else 0
        if (self._since_reset * FRAME >= RESET_EVERY_S * self.rate and not self.open
                and self.run == 0 and self._low >= RESET_LOW_FRAMES):
            reset = getattr(self.vad, 'reset', None)
            if reset:
                reset()
            self._since_reset = 0
        return p

    def feed(self, pcm, playing=False, strict=False):
        """strict=True: Cammy is speaking and Echo has converged (full duplex). The gate
        stays open to Adam, but needs strict_prob for strict_ms to open (barge-in)."""
        events = []
        start_prob, start_n = (self.strict_prob, self.strict_n) if strict else (self.start_prob, self.start_n)
        if playing:
            if self.open:
                events.append(self._end('playback'))
            # drop everything but a trailing odd byte, so later samples stay aligned
            self.buf += pcm
            keep = len(self.buf) % 2
            self._dropped_bytes += len(self.buf) - keep
            del self.buf[:len(self.buf) - keep]
            if not self._was_playing:
                self._clear()
                self._was_playing = True
            return events
        if self._was_playing:
            self._clear()  # Cammy's voice tail must not seed the model or the pre-roll
            self._was_playing = False
        self.buf += pcm
        audio = bytearray()
        n = len(self.buf) // FRAME_BYTES
        for i in range(n):
            f = bytes(self.buf[i * FRAME_BYTES:(i + 1) * FRAME_BYTES])
            self._stats['frames_in'] += 1
            p = self._prob(f)
            if p >= self.start_prob:
                self._stats['frames_speech'] += 1
            if not self.open:
                self.ring.append(f)
                if p < RESET_MAX_PROB:
                    self.noise = self._power if self.noise is None else 0.98 * self.noise + 0.02 * self._power
                self.run = self.run + 1 if p >= start_prob else 0
                if self.run >= start_n:
                    self.last_heard = time.monotonic()
                    keep = self.preroll_n + start_n
                    pre = b''.join(list(self.ring)[-keep:])
                    self.open, self.silence = True, 0
                    self.utt_frames = min(len(self.ring), keep)
                    self._stats['utterances'] += 1
                    self._stats['frames_sent'] += self.utt_frames
                    self.ring.clear()
                    trigger = np.frombuffer(pre[-start_n * FRAME_BYTES:], '<i2').astype(np.float32) / 32768.0
                    onset = float(np.dot(trigger, trigger)) / len(trigger)
                    self._speech_power, self._speech_n = onset * start_n, start_n
                    events.append(Event('start', pcm=pre, level=dbfs(onset),
                                        noise=None if self.noise is None else dbfs(self.noise)))
                    if self.utt_frames >= self.max_n:
                        events.append(self._end('max_length'))
                continue
            audio += f
            if p >= self.start_prob:
                self._speech_power += self._power
                self._speech_n += 1
            self.utt_frames += 1
            self._stats['frames_sent'] += 1
            self.silence = self.silence + 1 if p < self.end_prob else 0
            if not self.silence:
                self.last_heard = time.monotonic()
            reason = ('silence' if self.silence >= self.end_n else
                      'max_length' if self.utt_frames >= self.max_n else '')
            if reason:
                events.append(Event('audio', pcm=bytes(audio)))
                audio = bytearray()
                events.append(self._end(reason))
        if audio:
            events.append(Event('audio', pcm=bytes(audio)))
        del self.buf[:n * FRAME_BYTES]
        return events
