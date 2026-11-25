"""Preview history: the last few minutes of Eyes frames, in memory only.

- `JpegStream` cuts complete JPEGs out of an ffmpeg `image2pipe` byte stream. It walks the
  marker segments (so an FFD9 inside a header segment cannot end a frame early), scans the
  entropy-coded data for the real EOI, discards a partial frame at a restart and resyncs on
  the next SOI after garbage or an oversized frame.
- `FrameRing` keeps (wall, monotonic, JPEG bytes) frames, oldest first, with the JPEG bytes
  strictly capped (250,000,000 at most) and the oldest evicted first. Nothing is written to
  disk; the ring ends with the process.
- `sample_evenly` picks up to `limit` frames spread evenly in time across a window.

Times: intervals are measured on the monotonic clock (immune to wall-clock steps); each frame
also carries its wall time (time.time()), which is what callers get back.
"""
import bisect
import math
import threading
from collections import deque
from dataclasses import dataclass

HISTORY_BYTES = 250_000_000      # the hard ceiling on resident JPEG bytes
MAX_FRAME_BYTES = 16_000_000     # a single frame larger than this is garbage; resync

SOI = b'\xff\xd8'
EOI = 0xD9
SOS = 0xDA
_STANDALONE = frozenset(range(0xD0, 0xD8)) | {0x01}   # RSTn, TEM: no length field


class JpegStream:
    """Feed bytes in, get complete JPEG frames out (each from SOI to EOI inclusive)."""

    def __init__(self, max_frame=MAX_FRAME_BYTES):
        self.max_frame = max_frame
        self.buf = bytearray()
        self.discarded = 0       # bytes thrown away (garbage, corrupt or oversized frames)
        self._reset()

    def _reset(self):
        self._pos = None         # None: hunting for SOI; else the next index to parse
        self._entropy = False    # inside entropy-coded data after an SOS header

    def _drop(self, count):
        count = min(count, len(self.buf))
        del self.buf[:count]
        self.discarded += count
        self._reset()

    def _resync(self):
        """Drop the current (corrupt) start and hunt for the next SOI after it."""
        nxt = self.buf.find(SOI, 1)
        self._drop(nxt if nxt >= 0 else max(0, len(self.buf) - 1))

    def feed(self, data):
        frames = []
        if data:
            self.buf += data
        while True:
            frame = self._step()
            if frame is None:
                break
            if frame is not False:
                frames.append(frame)
        if self._pos is not None and len(self.buf) > self.max_frame:
            self._resync()
        return frames

    def _step(self):
        """One parse step: bytes for a finished frame, False to continue, None for more data."""
        buf = self.buf
        if self._pos is None:
            start = buf.find(SOI)
            if start < 0:
                # keep a trailing 0xFF: it may be the first half of the next SOI
                self._drop(len(buf) - 1 if buf.endswith(b'\xff') else len(buf))
                return None
            if start:
                self._drop(start)
            self._pos = 2
            return False
        i = self._pos
        if i > len(buf):
            return None          # the rest of a segment has not arrived
        if self._entropy:
            while True:
                j = buf.find(b'\xff', i)
                if j < 0 or j + 1 >= len(buf):
                    self._pos = j if j >= 0 else len(buf)
                    return None
                nxt = buf[j + 1]
                if nxt == 0x00 or nxt in _STANDALONE or nxt == 0xFF:
                    i = j + 1 if nxt == 0xFF else j + 2   # stuffed byte, restart marker or fill
                    continue
                self._entropy = False
                self._pos = j
                return False
        if i + 1 >= len(buf):
            return None
        if buf[i] != 0xFF:
            self._resync()
            return False
        marker = buf[i + 1]
        if marker == 0xFF:            # fill byte before a marker
            self._pos = i + 1
            return False
        if marker == EOI:
            end = i + 2
            if end > self.max_frame:      # completed within one chunk, but still too big
                self._drop(end)
                return False
            frame = bytes(buf[:end])
            del buf[:end]
            self._reset()
            return frame
        if marker in _STANDALONE:
            self._pos = i + 2
            return False
        if marker == 0xD8 or marker == 0x00:   # a new SOI (truncated frame) or a stray byte
            self._resync()
            return False
        if i + 3 >= len(buf):
            return None
        length = (buf[i + 2] << 8) | buf[i + 3]
        if length < 2:
            self._resync()
            return False
        self._pos = i + 2 + length
        if marker == SOS:
            self._entropy = True
        if self._pos > len(buf):
            return None      # the segment is not all here yet; parsing resumes at _pos
        return False

    def reset(self):
        """Forget a partial frame (the producer restarted)."""
        self._drop(len(self.buf))


@dataclass(frozen=True)
class Frame:
    wall: float
    mono: float
    data: bytes
    generation: int = 0


def sample_evenly(frames, limit):
    """Up to `limit` of the chronological `frames`, spread evenly in (monotonic) time: the
    first and last frames, and the frames nearest equally spaced instants between them. One
    frame asked: the one nearest the middle. Chronological, no repeats."""
    n = len(frames)
    if limit <= 0 or n == 0:
        return []
    if n <= limit:
        return list(frames)
    times = [f.mono for f in frames]
    first, last = times[0], times[-1]
    if limit == 1:
        targets = [(first + last) / 2]
    else:
        targets = [first + (last - first) * k / (limit - 1) for k in range(limit)]
    picked, lo = [], 0
    for k, target in enumerate(targets):
        hi = n - (limit - k)               # leave a frame for every later target
        j = bisect.bisect_left(times, target, lo, hi + 1)
        best = None
        for cand in (j - 1, j):
            if lo <= cand <= hi and (best is None or abs(times[cand] - target) < abs(times[best] - target)):
                best = cand
        picked.append(frames[best])
        lo = best + 1
    return picked


def _finite_nonneg(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('%s must be a finite number >= 0' % name)
    return float(value)


class FrameRing:
    """Chronological frames with the JPEG bytes capped at `capacity_bytes`; oldest out first."""

    def __init__(self, capacity_bytes=HISTORY_BYTES):
        if isinstance(capacity_bytes, bool) or not isinstance(capacity_bytes, int) or not 0 < capacity_bytes <= HISTORY_BYTES:
            raise ValueError('capacity_bytes must be an int in 1..%d' % HISTORY_BYTES)
        self.capacity_bytes = capacity_bytes
        self._frames = deque()
        self._bytes = 0
        self._lock = threading.Lock()
        self.added = 0
        self.evicted = 0
        self.rejected = 0

    def append(self, frame):
        size = len(frame.data)
        with self._lock:
            if size > self.capacity_bytes or (self._frames and frame.mono < self._frames[-1].mono):
                self.rejected += 1
                return False
            while self._frames and self._bytes + size > self.capacity_bytes:
                self._bytes -= len(self._frames.popleft().data)
                self.evicted += 1
            self._frames.append(frame)
            self._bytes += size
            self.added += 1
            return True

    def clear(self):
        with self._lock:
            self._frames.clear()
            self._bytes = 0

    def newest(self):
        with self._lock:
            return self._frames[-1] if self._frames else None

    def window(self, start_mono, end_mono):
        """Frames with start <= mono <= end, oldest first."""
        with self._lock:
            frames = list(self._frames)
        times = [f.mono for f in frames]
        return frames[bisect.bisect_left(times, start_mono):bisect.bisect_right(times, end_mono)]

    def query(self, seconds_ago, duration_s, limit, now_mono):
        """Frames from the inclusive interval that starts `seconds_ago` before `now_mono` and
        runs `duration_s` toward the present (never past it), evenly sampled down to `limit`."""
        seconds_ago = _finite_nonneg('seconds_ago', seconds_ago)
        duration_s = _finite_nonneg('duration_s', duration_s)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError('limit must be an int >= 0')
        start = now_mono - seconds_ago
        end = min(start + duration_s, now_mono)
        return sample_evenly(self.window(start, end), limit)

    def stats(self):
        with self._lock:
            oldest = self._frames[0] if self._frames else None
            newest = self._frames[-1] if self._frames else None
            return {'bytes': self._bytes, 'capacity_bytes': self.capacity_bytes, 'frames': len(self._frames),
                    'oldest': oldest, 'newest': newest, 'added': self.added, 'evicted': self.evicted,
                    'rejected': self.rejected}

    def __len__(self):
        with self._lock:
            return len(self._frames)

    @property
    def bytes(self):
        with self._lock:
            return self._bytes
