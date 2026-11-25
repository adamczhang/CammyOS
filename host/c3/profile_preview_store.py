"""Profile preview store: the last 250 MB of Eyes frames, on disk, in the Brain profile's work folder.

`ProfilePreviewStore(workspace)` keeps JPEG frames (about one a second) in
`<workspace>/.cammy-preview/`, oldest evicted first at the frame count or byte cap
(14,400 frames and 250,000,000 JPEG bytes by default), and finds them again after the process or the host
restarts.

Layout: one file per frame, named `f-<seq 12>-<wall µs 17>-<size 9>.jpg`. The directory listing
*is* the index: there is no separate index file to lose or corrupt, and reopening reads names and
`lstat` results only, never JPEG bytes. Sequence numbers give the strict FIFO order; wall times
(Unix microseconds) are non-decreasing in that order.

Safety:
- The workspace must be an absolute path with no `..`; it and `.cammy-preview` are opened with
  O_NOFOLLOW|O_DIRECTORY, and every later file operation is relative to that directory fd with
  O_NOFOLLOW, so a symlink (or a renamed path) cannot redirect reads or writes into another
  profile. A frame file must be a regular file with one link, the size its name states, and start
  with SOI and end with EOI, or it is dropped.
- The store directory is owned by this user and set to 0700. An exclusive flock on it stops a
  second process from opening the same store (`StoreBusy`).
- Files in the store that do not match the frame or temp name patterns are left alone and
  ignored; nothing outside `.cammy-preview` is ever read, written or listed.

Durability: a frame is written to a temp file and atomically renamed into place. Per-frame
fsync is optional. By default recent frames can be lost on abrupt power loss. Eviction happens first, so payload plus the temp
file stays within the budget. A crash leaves at worst a stray temp file (removed on reopen), a
truncated file (dropped on reopen by its size check) or an unlink that did not reach the disk
(the budget is re-enforced on reopen). Disk full or I/O errors make `append` return False and
are recorded in `stats()`; they never raise.

Clock: frames must arrive with non-decreasing wall time. A frame up to `clock_step_s` (300 s)
earlier than the newest is rejected (NTP jitter); a larger backward step is taken as a clock
correction: stored frames newer than the new frame are discarded and the frame is accepted.

Threads: every method is thread-safe. File I/O happens in the caller's thread, so async callers
use `asyncio.to_thread` for `append`/`query`/`nearest`, or `submit()`, which hands the frame to
the store's own writer thread and returns at once.
"""
import bisect
import errno
import fcntl
import math
import os
import queue
import re
import stat
import threading
import time
from pathlib import Path

HISTORY_BYTES = 250_000_000
HISTORY_MAX_FRAMES = 14_400
STORE_DIRNAME = '.cammy-preview'
CLOCK_STEP_S = 300.0
DIR_FSYNC_EVERY = 10
SUBMIT_QUEUE = 8

_FRAME_RE = re.compile(r'^f-(\d{12})-(\d{17})-(\d{9})\.jpg$')
_TEMP_RE = re.compile(r'^\.tmp-\d{12}-[0-9a-f]{16}$')
_MAX_SEQ = 10 ** 12 - 1
_MAX_WALL_US = 10 ** 17 - 1
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_SOI = b'\xff\xd8'
_EOI = b'\xff\xd9'


class StoreBusy(RuntimeError):
    """Another ProfilePreviewStore (this process or another) has this workspace's store open."""


def _name(seq, wall_us, size):
    return 'f-%012d-%017d-%09d.jpg' % (seq, wall_us, size)


def _finite_nonneg(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('%s must be a finite number >= 0' % name)
    return float(value)


def _finite(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('%s must be a finite number' % name)
    return float(value)


def _wall_key(entry):
    return entry[1]


def _sample_evenly(entries, limit):
    """Up to `limit` of the chronological `entries` spread evenly in wall time (first and last
    included; one asked: the one nearest the middle). Chronological, no repeats."""
    n = len(entries)
    if limit <= 0 or n == 0:
        return []
    if n <= limit:
        return list(entries)
    times = [e[1] for e in entries]
    first, last = times[0], times[-1]
    if limit == 1:
        targets = [(first + last) / 2]
    else:
        targets = [first + (last - first) * k / (limit - 1) for k in range(limit)]
    picked, lo = [], 0
    for k, target in enumerate(targets):
        hi = n - (limit - k)
        j = bisect.bisect_left(times, target, lo, hi + 1)
        best = None
        for cand in (j - 1, j):
            if lo <= cand <= hi and (best is None or abs(times[cand] - target) < abs(times[best] - target)):
                best = cand
        picked.append(entries[best])
        lo = best + 1
    return picked


def _validate_workspace(workspace):
    path = Path(workspace)
    if not path.is_absolute():
        raise ValueError('workspace must be an absolute path')
    if '..' in path.parts:
        raise ValueError('workspace must not contain ..')
    return path


class ProfilePreviewStore:
    """Persistent FIFO of JPEG frames under `<workspace>/.cammy-preview`, capped at `capacity_bytes`."""

    def __init__(self, workspace, capacity_bytes=HISTORY_BYTES, *, max_frames=HISTORY_MAX_FRAMES,
                 fsync=False, clock_step_s=CLOCK_STEP_S):
        if isinstance(capacity_bytes, bool) or not isinstance(capacity_bytes, int) or not 0 < capacity_bytes <= HISTORY_BYTES:
            raise ValueError('capacity_bytes must be an int in 1..%d' % HISTORY_BYTES)
        if type(max_frames) is not int or not 0 < max_frames <= HISTORY_MAX_FRAMES:
            raise ValueError('max_frames must be an int in 1..%d' % HISTORY_MAX_FRAMES)
        self.workspace = _validate_workspace(workspace)
        self.max_frames = max_frames
        self.capacity_bytes = capacity_bytes
        self._fsync = bool(fsync)
        self._clock_step_us = int(_finite_nonneg('clock_step_s', clock_step_s) * 1_000_000)
        self._lock = threading.Lock()          # guards the index and counters (held briefly, no I/O)
        self._idle = threading.Condition(self._lock)   # signalled when the last reader finishes
        self._readers = 0                      # queries using the directory fd right now
        self._write_lock = threading.Lock()    # serialises appends (I/O happens under this one)
        self._pending_unlinks = []  # failed evictions must clear before publishing again
        self._entries = []                     # (seq, wall_us, size), FIFO order from _head
        self._head = 0
        self._bytes = 0
        self._next_seq = 0
        self._since_dir_sync = 0
        self._closed = False
        self.added = self.evicted = self.rejected = self.discarded = self.dropped = self.errors = 0
        self.last_error = None
        self._queue = None
        self._writer = None
        self._dfd = self._open_store_dir()
        try:
            self._load()
        except BaseException:
            os.close(self._dfd)
            raise

    # ---- opening -------------------------------------------------------------------------

    def _open_store_dir(self):
        wfd = os.open(self.workspace, _DIR_FLAGS)
        try:
            try:
                os.mkdir(STORE_DIRNAME, 0o700, dir_fd=wfd)
            except FileExistsError:
                pass
            dfd = os.open(STORE_DIRNAME, _DIR_FLAGS, dir_fd=wfd)
        finally:
            os.close(wfd)
        try:
            st = os.fstat(dfd)
            if st.st_uid != os.geteuid():
                raise PermissionError('%s is not owned by this user' % STORE_DIRNAME)
            if stat.S_IMODE(st.st_mode) != 0o700:
                os.fchmod(dfd, 0o700)
            try:
                fcntl.flock(dfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreBusy('preview store already open: %s' % (self.workspace / STORE_DIRNAME)) from None
        except BaseException:
            os.close(dfd)
            raise
        return dfd

    def _unlink(self, name):
        try:
            os.unlink(name, dir_fd=self._dfd)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            self._note_error(exc)
            return False

    def _load(self):
        """Rebuild the index from names and lstat only; drop temp files and invalid frames."""
        found = []
        with os.scandir(self._dfd) as it:
            for entry in it:
                name = entry.name
                if _TEMP_RE.match(name):
                    self._unlink(name)
                    continue
                m = _FRAME_RE.match(name)
                if not m:
                    continue
                seq, wall_us, size = int(m.group(1)), int(m.group(2)), int(m.group(3))
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != size
                        or size < 4 or size > self.capacity_bytes):
                    self._unlink(name)
                    continue
                found.append((seq, wall_us, size))
        found.sort()
        kept, last_seq, last_wall = [], -1, -1
        for seq, wall_us, size in found:
            if seq == last_seq or wall_us < last_wall:   # duplicate seq or out of order: corrupt
                self._unlink(_name(seq, wall_us, size))
                self.discarded += 1
                continue
            kept.append((seq, wall_us, size))
            last_seq, last_wall = seq, wall_us
        self._entries = kept
        self._bytes = sum(e[2] for e in kept)
        self._next_seq = (kept[-1][0] + 1) if kept else 0
        if found:
            self._next_seq = max(self._next_seq, found[-1][0] + 1)
        victims = []
        while self._live() > self.max_frames or self._bytes > self.capacity_bytes:
            victims.append(self._pop_oldest())
        for victim in victims:
            if not self._unlink(_name(*victim)):
                raise OSError(errno.EIO, 'could not shrink preview history')
        if victims:
            self._sync_dir()

    # ---- index helpers (call with self._lock held) ---------------------------------------

    def _live(self):
        return len(self._entries) - self._head

    def _pop_oldest(self):
        entry = self._entries[self._head]
        self._head += 1
        self._bytes -= entry[2]
        self.evicted += 1
        if self._head > 1024 and self._head * 2 > len(self._entries):
            del self._entries[:self._head]
            self._head = 0
        return entry

    def _forget(self, entry):
        """Drop one entry found missing or corrupt on read (if still indexed)."""
        i = bisect.bisect_left(self._entries, entry, self._head)
        if i < len(self._entries) and self._entries[i] == entry:
            del self._entries[i]
            self._bytes -= entry[2]
            self.discarded += 1

    def _note_error(self, exc):
        self.errors += 1
        self.last_error = '%s: %s' % (type(exc).__name__, exc)

    def _sync_dir(self):
        if not self._fsync:
            return
        try:
            os.fsync(self._dfd)
        except OSError as exc:
            self._note_error(exc)
        self._since_dir_sync = 0

    # ---- writing -------------------------------------------------------------------------

    def append(self, frame):
        """Store `frame` (.wall: Unix seconds, .data: one JPEG). True if it was published."""
        try:
            wall = frame.wall
            data = bytes(frame.data)
            wall_us = round(_finite_nonneg('wall', wall) * 1_000_000)
        except (AttributeError, TypeError, ValueError):
            with self._lock:
                self.rejected += 1
            return False
        size = len(data)
        if (size < 4 or size > self.capacity_bytes or wall_us > _MAX_WALL_US
                or not data.startswith(_SOI) or not data.endswith(_EOI)):
            with self._lock:
                self.rejected += 1
            return False
        with self._write_lock:
            if self._closed:
                with self._lock:
                    self.rejected += 1
                return False
            self._pending_unlinks = [name for name in self._pending_unlinks if not self._unlink(name)]
            if self._pending_unlinks:
                return False
            with self._lock:
                if self._next_seq > _MAX_SEQ:
                    self.rejected += 1
                    return False
                victims = []
                if self._live():
                    newest = self._entries[-1][1]
                    if wall_us < newest:
                        if newest - wall_us <= self._clock_step_us:
                            self.rejected += 1
                            return False
                        while self._live() and self._entries[-1][1] > wall_us:   # clock corrected
                            entry = self._entries.pop()
                            self._bytes -= entry[2]
                            self.discarded += 1
                            victims.append(entry)
                while self._live() >= self.max_frames or self._bytes + size > self.capacity_bytes:
                    victims.append(self._pop_oldest())
                seq = self._next_seq
                self._next_seq += 1
            # Out of the index first so readers skip these files. Failed unlinks still
            # count against the on-disk budget until a later append can remove them.
            for victim in victims:
                name = _name(*victim)
                if not self._unlink(name):
                    self._pending_unlinks.append(name)
            if self._pending_unlinks:
                return False
            final = _name(seq, wall_us, size)
            if not self._write_file(seq, final, data):
                return False
            with self._lock:
                self._entries.append((seq, wall_us, size))
                self._bytes += size
                self.added += 1
                if self._fsync:
                    self._since_dir_sync += 1
                sync = self._fsync and (self._since_dir_sync >= DIR_FSYNC_EVERY or bool(victims))
            if sync:
                self._sync_dir()
            return True

    def _write_file(self, seq, final, data):
        temp = '.tmp-%012d-%s' % (seq, os.urandom(8).hex())
        fd = None
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=self._dfd)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, 'short write')
                view = view[written:]
            if self._fsync:
                os.fsync(fd)
            os.close(fd)
            fd = None
            os.rename(temp, final, src_dir_fd=self._dfd, dst_dir_fd=self._dfd)
            return True
        except OSError as exc:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._unlink(temp)
            with self._lock:
                self._note_error(exc)
            return False

    def submit(self, frame):
        """Queue `frame` for the store's writer thread; never blocks. False if dropped."""
        with self._lock:
            if self._closed:
                self.dropped += 1
                return False
            if self._writer is None:
                self._queue = queue.Queue(SUBMIT_QUEUE)
                self._writer = threading.Thread(target=self._write_loop, name='preview-store-writer', daemon=True)
                self._writer.start()
            q = self._queue
        try:
            q.put_nowait(frame)
            return True
        except queue.Full:
            with self._lock:
                self.dropped += 1
            return False

    def _write_loop(self):
        q = self._queue
        while True:
            frame = q.get()
            try:
                if frame is None:
                    return
                self.append(frame)
            except Exception as exc:          # never let the writer thread die
                with self._lock:
                    self._note_error(exc)
            finally:
                q.task_done()

    def flush(self):
        """Wait until every submitted frame has been written (or failed)."""
        q = self._queue
        if q is not None:
            q.join()

    # ---- reading -------------------------------------------------------------------------

    def _begin_read(self):
        """A snapshot of the index, with the directory fd held open until `_end_read`; None if closed."""
        with self._lock:
            if self._closed:
                return None
            self._readers += 1
            return self._entries[self._head:]

    def _end_read(self):
        with self._lock:
            self._readers -= 1
            if not self._readers:
                self._idle.notify_all()

    def _read(self, entry):
        """The JPEG bytes of `entry`, or None (and the entry dropped) if missing or corrupt."""
        seq, wall_us, size = entry
        fd = None
        try:
            fd = os.open(_name(seq, wall_us, size), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._dfd)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != size:
                raise ValueError('not the frame indexed')
            chunks, remaining = [], size
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    raise ValueError('truncated')
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b''.join(chunks)
            if os.read(fd, 1) or not data.startswith(_SOI) or not data.endswith(_EOI):
                raise ValueError('not a JPEG')
            return data
        except FileNotFoundError:
            with self._lock:                    # evicted under us is normal; else it vanished
                self._forget(entry)
            return None
        except (OSError, ValueError) as exc:
            with self._lock:
                self._forget(entry)
                self._note_error(exc)
            if not isinstance(exc, OSError) or exc.errno in (errno.ELOOP, errno.EMLINK):
                self._unlink(_name(seq, wall_us, size))
            return None
        finally:
            if fd is not None:
                os.close(fd)

    def query(self, seconds_ago, duration_s, limit, now_wall=None):
        """Frames from the inclusive interval that starts `seconds_ago` before `now_wall`
        (default: now) and runs `duration_s` toward it (never past it), evenly sampled down to
        `limit`: a chronological list of (wall, jpeg). A gap in the history is simply empty."""
        seconds_ago = _finite_nonneg('seconds_ago', seconds_ago)
        duration_s = _finite_nonneg('duration_s', duration_s)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError('limit must be an int >= 0')
        now = time.time() if now_wall is None else _finite('now_wall', now_wall)
        start = now - seconds_ago
        end = min(start + duration_s, now)
        if limit == 0 or end < start:
            return []
        entries = self._begin_read()
        if entries is None:
            return []
        try:
            lo = bisect.bisect_left(entries, math.ceil(start * 1_000_000 - 1e-6), key=_wall_key)
            hi = bisect.bisect_right(entries, math.floor(end * 1_000_000 + 1e-6), key=_wall_key)
            out = []
            for entry in _sample_evenly(entries[lo:hi], limit):
                data = self._read(entry)
                if data is not None:
                    out.append((entry[1] / 1_000_000, data))
            return out
        finally:
            self._end_read()

    def nearest(self, timestamp, max_distance_s):
        """The stored frame closest in wall time to `timestamp` (ties: the earlier), as
        (wall, jpeg), or None when nothing lies within `max_distance_s` (a gap)."""
        target = _finite('timestamp', timestamp) * 1_000_000
        max_us = _finite_nonneg('max_distance_s', max_distance_s) * 1_000_000
        entries = self._begin_read()
        if entries is None:
            return None
        try:
            j = bisect.bisect_left(entries, target, key=_wall_key)
            before, after = j - 1, j
            while before >= 0 or after < len(entries):
                d_before = target - entries[before][1] if before >= 0 else math.inf
                d_after = entries[after][1] - target if after < len(entries) else math.inf
                if d_before <= d_after:
                    cand, dist = before, d_before
                    before -= 1
                else:
                    cand, dist = after, d_after
                    after += 1
                if dist > max_us + 0.5:
                    return None
                data = self._read(entries[cand])
                if data is not None:
                    return entries[cand][1] / 1_000_000, data
            return None
        finally:
            self._end_read()

    def stats(self):
        """JSON-serialisable counts; `oldest`/`newest` are Unix seconds or None."""
        with self._lock:
            live = self._live()
            return {'bytes': self._bytes, 'capacity_bytes': self.capacity_bytes, 'frames': live,
                    'max_frames': self.max_frames,
                    'oldest': self._entries[self._head][1] / 1_000_000 if live else None,
                    'newest': self._entries[-1][1] / 1_000_000 if live else None,
                    'added': self.added, 'evicted': self.evicted, 'rejected': self.rejected,
                    'discarded': self.discarded, 'dropped': self.dropped, 'errors': self.errors,
                    'last_error': self.last_error, 'closed': self._closed}

    def __len__(self):
        with self._lock:
            return self._live()

    # ---- closing -------------------------------------------------------------------------

    def close(self, timeout=5.0):
        """Write what was submitted, sync, release the lock and the directory. Idempotent."""
        with self._lock:
            if self._closed:
                return
            writer, q = self._writer, self._queue
        if writer is not None:
            q.put(None)
            writer.join(timeout)
        with self._write_lock:
            with self._lock:
                self._closed = True
                while self._readers:
                    self._idle.wait()
            self._sync_dir()
            try:
                fcntl.flock(self._dfd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self._dfd)
            self._dfd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
