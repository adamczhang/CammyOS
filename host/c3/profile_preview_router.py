"""Profile preview router: send Eyes frames to the selected Brain profile's preview store, and only there.

`ProfilePreviewRouter(active_profile)` wraps one `ProfilePreviewStore` at a time. `active_profile`
is a callback returning `(profile_id, profile_name, workspace)` for the profile selected right now,
or None when no profile is selected. The router opens the store in that workspace, closes it when
the selection changes (its frames stay on disk for when the profile is selected again) and opens
the next one.

Binding: a store is bound to `(profile_id, workspace)`. A new id, or the same id pointing at a
different workspace, is a different binding and gets a different store; a new display name alone
only updates the name. Frames go only to the store bound to the profile selected when they are
submitted, and only if they were captured after the router saw that profile selected (`frame.wall`
is not earlier than the moment the binding was first observed), so frames from before a switch
never land in the next profile. Each store's writer thread writes only into its own directory fd,
so frames still queued in an old store when a switch happens are written to that old profile, the
one they were submitted to, and never to the new one. Reads check the binding before and after
reading and return nothing if it changed in between, so a read never returns one profile's frames
while another is selected.

No profile: frames are not kept (dropped and counted), reads return nothing, and no store is open.

Errors (a failing callback, a bad workspace, a store already open elsewhere, disk errors on open or
close) never raise out of `submit` or `stats` and never stop the live preview: frames are dropped,
the error is recorded in `stats()['last_error']` as plain strings and numbers, and a failed open is
retried no sooner than `retry_s` later.

Threads and asyncio:
- `submit(frame)` never blocks on I/O: it calls the callback, takes a short lock and hands the frame
  to the store's own writer thread. Call it from the Eyes decoder thread (not while holding a lock
  the callback needs). When the selection has changed it drops the frame and wakes the router's
  switcher thread, which closes the old store and opens the new one.
- `query`, `nearest`, `refresh` and `close` block (they read files, and may open or close a store
  first). From asyncio, use `await asyncio.to_thread(router.query, ...)` or the `aquery`,
  `anearest`, `arefresh` and `aclose` helpers. `stats()` does no file I/O and is safe on the loop.
- The callback is called from any of these threads. It must be quick, must not block or await,
  and must not call back into the router; reading an immutable snapshot that the event loop
  replaces on each selection is the intended shape.
"""
import asyncio
import threading
import time
from pathlib import Path

from .profile_preview_store import HISTORY_BYTES, ProfilePreviewStore, _finite, _finite_nonneg

RETRY_S = 5.0
CLOSE_TIMEOUT_S = 5.0
_MAX_ID = 128
_MAX_NAME = 256
_MAX_ERROR = 300


def _error_text(exc):
    text = '%s: %s' % (type(exc).__name__, exc)
    return text if len(text) <= _MAX_ERROR else text[:_MAX_ERROR - 3] + '...'


def _check_binding(value):
    """(profile_id, profile_name, workspace Path) from the callback's value, or None; ValueError if malformed."""
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError('active_profile must return None or (profile_id, profile_name, workspace)')
    profile_id, name, workspace = value
    if not isinstance(profile_id, str) or not profile_id or len(profile_id) > _MAX_ID:
        raise ValueError('profile_id must be a non-empty str of at most %d characters' % _MAX_ID)
    if name is None:
        name = profile_id
    if not isinstance(name, str):
        raise ValueError('profile_name must be a str')
    if not isinstance(workspace, (str, Path)):
        raise ValueError('workspace must be a str or Path')
    workspace = Path(workspace)
    if not workspace.is_absolute() or '..' in workspace.parts:
        raise ValueError('workspace must be an absolute path without ..')
    return profile_id, name[:_MAX_NAME], workspace


class _Selected:
    """The selection as the router last saw it: binding key, display name, and since when (wall)."""
    __slots__ = ('key', 'name', 'since')

    def __init__(self, key, name, since):
        self.key, self.name, self.since = key, name, since


class ProfilePreviewRouter:
    """One `ProfilePreviewStore` at a time, always the selected profile's (see the module doc)."""

    def __init__(self, active_profile, capacity_bytes=HISTORY_BYTES, *, store_factory=None,
                 retry_s=RETRY_S, close_timeout_s=CLOSE_TIMEOUT_S, wall=time.time, clock=time.monotonic,
                 switcher=True):
        if not callable(active_profile):
            raise TypeError('active_profile must be callable')
        if isinstance(capacity_bytes, bool) or not isinstance(capacity_bytes, int) or not 0 < capacity_bytes <= HISTORY_BYTES:
            raise ValueError('capacity_bytes must be an int in 1..%d' % HISTORY_BYTES)
        self._active_profile = active_profile
        self.capacity_bytes = capacity_bytes
        self._factory = store_factory or ProfilePreviewStore
        self._retry_s = _finite_nonneg('retry_s', retry_s)
        self._close_timeout_s = _finite_nonneg('close_timeout_s', close_timeout_s)
        self._wall, self._clock = wall, clock
        self._lock = threading.Lock()          # selection, attachment, counters; no I/O under it
        self._switch_lock = threading.Lock()   # serialises opening and closing stores
        self._selected = None                  # _Selected, or None for no profile
        self._resolve_failed = False           # the last callback call failed
        self._store = None                     # the open store, bound to _bound
        self._bound = None                     # binding key (profile_id, workspace str) of _store
        self._epoch = 0                        # bumped whenever _store changes
        self._failed = None                    # (key, retry_after clock) after a failed open
        self._closed = False
        self.switches = 0
        self.submitted = self.dropped_no_profile = self.dropped_switching = 0
        self.dropped_stale = self.dropped_store = self.stale_reads = 0
        self.errors = 0
        self.last_error = None
        self._wake = threading.Event()
        self._switcher_enabled = bool(switcher)
        self._switcher = None

    # ---- selection -------------------------------------------------------------------------

    def _note_error(self, stage, profile_id, exc):
        """Record `exc` (caller holds `_lock`)."""
        self.errors += 1
        self.last_error = {'stage': stage, 'profile': profile_id, 'error': _error_text(exc),
                           'at': self._wall()}

    def _resolve(self):
        """Ask the callback; update `_selected`. Returns (ok, _Selected or None). Never raises."""
        try:
            binding = _check_binding(self._active_profile())
        except Exception as exc:
            with self._lock:
                if not self._resolve_failed:          # report once per run of failures
                    self._note_error('resolve', None, exc)
                self._resolve_failed = True
            return False, None
        with self._lock:
            self._resolve_failed = False
            if binding is None:
                self._selected = None
                return True, None
            profile_id, name, workspace = binding
            key = (profile_id, str(workspace))
            sel = self._selected
            if sel is None or sel.key != key:
                sel = self._selected = _Selected(key, name, self._wall())
            elif sel.name != name:
                sel.name = name
            return True, sel

    def _kick(self):
        """Wake the switcher thread (started on first use). Caller holds `_lock`."""
        if not self._switcher_enabled or self._closed:
            return
        if self._switcher is None:
            self._switcher = threading.Thread(target=self._switch_loop, name='preview-router', daemon=True)
            self._switcher.start()
        self._wake.set()

    def _switch_loop(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._closed:
                return
            try:
                self._refresh()
            except Exception as exc:             # never let the switcher die
                with self._lock:
                    self._note_error('switch', None, exc)

    def _detach(self):
        """Take the open store out of service (caller holds `_switch_lock`); returns it."""
        with self._lock:
            store, profile_id = self._store, (self._bound[0] if self._bound else None)
            self._store = self._bound = None
            self._epoch += 1
        return store, profile_id

    def _close_store(self, store, profile_id):
        if store is None:
            return
        try:
            store.close(self._close_timeout_s)
        except Exception as exc:
            with self._lock:
                self._note_error('close', profile_id, exc)

    def refresh(self):
        """Make the open store match the selected profile: close the old one, open the new one.
        Blocks on file I/O; returns `stats()`. Safe to call from any thread."""
        self._refresh()
        return self.stats()

    def _refresh(self):
        with self._switch_lock:
            for _ in range(4):                   # the selection may move while we open
                if self._closed:
                    break
                ok, sel = self._resolve()
                if not ok:
                    break                        # unknown selection: keep the store, write nothing
                want = sel.key if sel else None
                with self._lock:
                    if want == self._bound:
                        break
                    failed = self._failed
                if self._store is not None:
                    self._close_store(*self._detach())
                if sel is None:
                    break
                if failed is not None and failed[0] == want and self._clock() < failed[1]:
                    break                        # opened and failed recently: retry later
                try:
                    store = self._factory(Path(want[1]), self.capacity_bytes)
                except Exception as exc:
                    with self._lock:
                        self._failed = (want, self._clock() + self._retry_s)
                        self._note_error('open', want[0], exc)
                    break
                with self._lock:
                    if not self._closed:
                        self._store, self._bound = store, want
                        self._epoch += 1
                        self._failed = None
                        self.switches += 1
                        store = None
                if store is not None:            # closed while opening
                    self._close_store(store, want[0])

    # ---- writing ---------------------------------------------------------------------------

    def submit(self, frame):
        """Queue `frame` (.wall Unix seconds, .data JPEG) for the selected profile's store.
        Never blocks on I/O and never raises; True if the store accepted it into its queue."""
        ok, sel = self._resolve()
        try:
            wall = float(frame.wall)
        except Exception:
            wall = None
        with self._lock:
            if self._closed:
                return False
            if not ok:
                self.dropped_switching += 1
                return False
            if sel is None:
                self.dropped_no_profile += 1
                if self._store is not None:
                    self._kick()                 # close the old profile's store
                return False
            if self._bound != sel.key or self._store is None:
                self.dropped_switching += 1
                failed = self._failed
                if failed is None or failed[0] != sel.key or self._clock() >= failed[1]:
                    self._kick()
                return False
            if wall is None or wall < sel.since:
                self.dropped_stale += 1           # captured before this profile was selected
                return False
            store = self._store
            try:
                accepted = store.submit(frame)
            except Exception as exc:
                self._note_error('submit', sel.key[0], exc)
                accepted = False
            if accepted:
                self.submitted += 1
            else:
                self.dropped_store += 1
            return accepted

    # ---- reading ---------------------------------------------------------------------------

    def _read(self, fn, empty):
        """Run `fn(store)` on the selected profile's store, or return `empty`."""
        self._refresh()
        with self._lock:
            sel, store, epoch = self._selected, self._store, self._epoch
            if self._closed or store is None or sel is None or sel.key != self._bound:
                return empty
            key = sel.key
        try:
            out = fn(store)
        except Exception as exc:
            with self._lock:
                self._note_error('read', key[0], exc)
            return empty
        ok, sel = self._resolve()                # still the same profile, still the same store?
        with self._lock:
            if not ok or sel is None or sel.key != key or self._epoch != epoch:
                self.stale_reads += 1
                if sel is not None and sel.key != self._bound:
                    self._kick()
                return empty
        return out

    def query(self, seconds_ago, duration_s, limit, now_wall=None):
        """`ProfilePreviewStore.query` on the selected profile's store; [] with no profile.
        Blocks (file I/O, maybe opening the store); ValueError for bad arguments."""
        _finite_nonneg('seconds_ago', seconds_ago)
        _finite_nonneg('duration_s', duration_s)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError('limit must be an int >= 0')
        if now_wall is not None:
            _finite('now_wall', now_wall)
        return self._read(lambda s: s.query(seconds_ago, duration_s, limit, now_wall), [])

    def nearest(self, timestamp, max_distance_s):
        """`ProfilePreviewStore.nearest` on the selected profile's store; None with no profile.
        Blocks (file I/O, maybe opening the store); ValueError for bad arguments."""
        _finite('timestamp', timestamp)
        _finite_nonneg('max_distance_s', max_distance_s)
        return self._read(lambda s: s.nearest(timestamp, max_distance_s), None)

    def stats(self):
        """JSON-serialisable state. No file I/O. `profile`/`profile_name` are the selected
        profile; `coverage` is its stored history, or None until its store is open."""
        ok, sel = self._resolve()
        with self._lock:
            store = self._store if (sel is not None and sel.key == self._bound) else None
            if self._closed:
                state = 'closed'
            elif not ok:
                state = 'unknown'
            elif sel is None:
                state = 'no_profile'
            elif store is not None:
                state = 'open'
            elif self._failed is not None and self._failed[0] == sel.key:
                state = 'error'
            else:
                state = 'switching'
            if state in ('switching', 'error') and not self._closed:
                failed = self._failed
                if failed is None or failed[0] != sel.key or self._clock() >= failed[1]:
                    self._kick()
            out = {'state': state,
                   'profile': sel.key[0] if sel else None,
                   'profile_name': sel.name if sel else None,
                   'since': sel.since if sel else None,
                   'capacity_bytes': self.capacity_bytes,
                   'switches': self.switches, 'submitted': self.submitted,
                   'dropped_no_profile': self.dropped_no_profile,
                   'dropped_switching': self.dropped_switching,
                   'dropped_stale': self.dropped_stale, 'dropped_store': self.dropped_store,
                   'stale_reads': self.stale_reads, 'errors': self.errors,
                   'last_error': dict(self.last_error) if self.last_error else None}
        out['coverage'] = None
        out['store'] = None
        if store is not None:
            try:
                s = store.stats()
            except Exception as exc:
                with self._lock:
                    self._note_error('stats', out['profile'], exc)
            else:
                out['store'] = s
                out['coverage'] = {'oldest': s['oldest'], 'newest': s['newest'], 'frames': s['frames'],
                                   'bytes': s['bytes'], 'capacity_bytes': s['capacity_bytes']}
        return out

    # ---- closing ---------------------------------------------------------------------------

    def close(self):
        """Stop the switcher, write what was submitted and close the store. Blocks; idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            switcher = self._switcher
        self._wake.set()
        if switcher is not None and switcher is not threading.current_thread():
            switcher.join(self._close_timeout_s + 1.0)
        with self._switch_lock:
            self._close_store(*self._detach())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- asyncio helpers -------------------------------------------------------------------

    async def aquery(self, seconds_ago, duration_s, limit, now_wall=None):
        return await asyncio.to_thread(self.query, seconds_ago, duration_s, limit, now_wall)

    async def anearest(self, timestamp, max_distance_s):
        return await asyncio.to_thread(self.nearest, timestamp, max_distance_s)

    async def arefresh(self):
        return await asyncio.to_thread(self.refresh)

    async def aclose(self):
        await asyncio.to_thread(self.close)
