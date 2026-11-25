"""ProfilePreviewRouter: Eyes frames go only to the selected Brain profile's preview store.

Offline: temporary directories only; no camera, no real profile, no service.
"""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from c3.profile_preview_router import ProfilePreviewRouter  # noqa: E402
from c3.profile_preview_store import STORE_DIRNAME, ProfilePreviewStore  # noqa: E402

T0 = 1_800_000_000.0


def jpeg(tag):
    return b'\xff\xd8' + tag.encode() + b'|' * 16 + b'\xff\xd9'


def frame(wall, tag):
    return SimpleNamespace(wall=wall, data=jpeg(tag))


def tag_of(data):
    return data[2:].split(b'|', 1)[0].decode()


def fast_store(ws, cap):
    return ProfilePreviewStore(ws, cap, fsync=False)


class RouterCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.ws = {}
        for pid in ('a', 'b'):
            self.ws[pid] = self.root / pid / 'work'
            self.ws[pid].mkdir(parents=True)
        self.selected = None                  # what the fake resident brain has selected
        self.names = {'a': 'Alpha', 'b': 'Bravo'}
        self.now = T0
        self.mono = 100.0
        self.routers = []

    def tearDown(self):
        for r in self.routers:
            r.close()
        self._tmp.cleanup()

    def active(self):
        if self.selected is None:
            return None
        return self.selected, self.names[self.selected], self.ws[self.selected]

    def router(self, **kw):
        kw.setdefault('store_factory', fast_store)
        kw.setdefault('wall', lambda: self.now)
        kw.setdefault('clock', lambda: self.mono)
        kw.setdefault('switcher', False)
        r = ProfilePreviewRouter(kw.pop('active', self.active), **kw)
        self.routers.append(r)
        return r

    def select(self, r, pid):
        self.selected = pid
        r.refresh()

    def flush(self, r):
        store = r._store
        if store is not None:
            store.flush()

    def tags_on_disk(self, pid):
        d = self.ws[pid] / STORE_DIRNAME
        if not d.exists():
            return []
        return sorted(tag_of(p.read_bytes()) for p in d.iterdir() if p.name.startswith('f-'))

    def put(self, r, tag, dt=1.0):
        self.now += dt
        return r.submit(frame(self.now, tag))


class NoProfileTest(RouterCase):
    def test_no_profile_keeps_nothing(self):
        r = self.router()
        self.assertFalse(self.put(r, 'x'))
        self.assertEqual(r.query(60, 60, 10, now_wall=self.now), [])
        self.assertIsNone(r.nearest(self.now, 5))
        s = r.stats()
        self.assertEqual((s['state'], s['profile'], s['coverage']), ('no_profile', None, None))
        self.assertEqual(s['dropped_no_profile'], 1)
        self.assertFalse((self.ws['a'] / STORE_DIRNAME).exists())
        json.dumps(s)

    def test_deselect_closes_store_and_keeps_files(self):
        r = self.router()
        self.select(r, 'a')
        self.assertTrue(self.put(r, 'a1'))
        self.flush(r)
        self.select(r, None)
        self.assertIsNone(r._store)
        self.assertFalse(self.put(r, 'lost'))
        self.assertEqual(self.tags_on_disk('a'), ['a1'])
        with ProfilePreviewStore(self.ws['a'], fsync=False) as s:   # lock released
            self.assertEqual(len(s), 1)


class SwitchTest(RouterCase):
    def test_frames_follow_selection_and_reopen_persists(self):
        r = self.router()
        self.select(r, 'a')
        self.assertTrue(self.put(r, 'a1'))
        self.assertTrue(self.put(r, 'a2'))
        self.flush(r)
        s = r.stats()
        self.assertEqual((s['state'], s['profile'], s['profile_name']), ('open', 'a', 'Alpha'))
        self.assertEqual(s['coverage']['frames'], 2)

        self.select(r, 'b')
        self.assertTrue(self.put(r, 'b1'))
        self.flush(r)
        got = r.query(60, 60, 10, now_wall=self.now)
        self.assertEqual([tag_of(d) for _, d in got], ['b1'])
        self.assertEqual(r.stats()['coverage']['frames'], 1)

        self.select(r, 'a')                     # reopened from disk
        self.assertEqual([tag_of(d) for _, d in r.query(60, 60, 10, now_wall=self.now)], ['a1', 'a2'])
        wall, data = r.nearest(T0 + 2, 0.5)
        self.assertEqual((wall, tag_of(data)), (T0 + 2, 'a2'))
        self.assertEqual(self.tags_on_disk('a'), ['a1', 'a2'])
        self.assertEqual(self.tags_on_disk('b'), ['b1'])
        self.assertEqual(r.stats()['switches'], 3)

    def test_submit_after_selection_change_before_switch_is_dropped(self):
        r = self.router()
        self.select(r, 'a')
        self.selected = 'b'                     # brain switched; router has not reopened yet
        self.assertFalse(self.put(r, 'b-early'))
        self.assertEqual(r.stats()['state'], 'switching')
        self.assertEqual(self.tags_on_disk('a'), [])
        r.refresh()
        self.assertTrue(self.put(r, 'b1'))
        self.flush(r)
        self.assertEqual(self.tags_on_disk('b'), ['b1'])
        self.assertEqual(r.stats()['dropped_switching'], 1)

    def test_frame_captured_before_selection_is_stale(self):
        r = self.router()
        self.select(r, 'a')
        self.assertFalse(r.submit(frame(self.now - 0.5, 'old')))
        self.assertEqual(r.stats()['dropped_stale'], 1)

    def test_name_change_keeps_store_but_new_workspace_reopens(self):
        r = self.router()
        self.select(r, 'a')
        store = r._store
        self.names['a'] = 'Alpha 2'
        self.assertEqual(r.refresh()['profile_name'], 'Alpha 2')
        self.assertIs(r._store, store)
        self.ws['a'] = self.root / 'a' / 'moved'
        self.ws['a'].mkdir()
        r.refresh()
        self.assertIsNot(r._store, store)
        self.assertEqual(r.stats()['switches'], 2)


class RaceTest(RouterCase):
    def test_delayed_writer_finishes_into_old_profile_only(self):
        gate, entered = threading.Event(), threading.Event()

        class SlowStore(ProfilePreviewStore):
            def append(self, f):
                entered.set()
                gate.wait(5)
                return super().append(f)

        def factory(ws, cap):
            return (SlowStore if ws == self.ws['a'] else ProfilePreviewStore)(ws, cap, fsync=False)

        r = self.router(store_factory=factory)
        self.select(r, 'a')
        self.assertTrue(self.put(r, 'a1'))
        self.assertTrue(self.put(r, 'a2'))
        self.assertTrue(entered.wait(5))
        self.selected = 'b'
        t = threading.Thread(target=r.refresh)
        t.start()                               # blocks closing A until its writer drains
        deadline = time.monotonic() + 5
        while r._bound is not None and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(self.put(r, 'b-during'))   # A detached, B not open: dropped
        gate.set()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertTrue(self.put(r, 'b1'))
        self.flush(r)
        self.assertEqual(self.tags_on_disk('a'), ['a1', 'a2'])
        self.assertEqual(self.tags_on_disk('b'), ['b1'])

    def test_read_overlapping_switch_returns_nothing(self):
        release, reading = threading.Event(), threading.Event()

        class SlowRead(ProfilePreviewStore):
            def query(self, *a, **kw):
                out = super().query(*a, **kw)
                reading.set()
                release.wait(5)
                return out

        r = self.router(store_factory=lambda ws, cap: SlowRead(ws, cap, fsync=False))
        self.select(r, 'a')
        self.put(r, 'a1')
        self.flush(r)
        result = []
        t = threading.Thread(target=lambda: result.append(r.query(60, 60, 10, now_wall=self.now)))
        t.start()
        self.assertTrue(reading.wait(5))
        self.selected = 'b'                     # switched while A's frames were in hand
        release.set()
        t.join(5)
        self.assertEqual(result, [[]])
        self.assertEqual(r.stats()['stale_reads'], 1)

    def test_concurrent_switching_never_mixes_profiles(self):
        sel_lock = threading.Lock()
        r = self.router(wall=time.time, switcher=True)
        self.selected = 'a'
        r.refresh()
        stop = threading.Event()
        sent = {'a': 0, 'b': 0}

        def camera():
            n = 0
            while not stop.is_set():
                with sel_lock:                  # the tag names the profile selected at submit
                    pid = self.selected
                    if r.submit(frame(time.time(), '%s%d' % (pid, n))):
                        sent[pid] += 1
                n += 1
                time.sleep(0.0005)

        cam = threading.Thread(target=camera)
        cam.start()
        for i in range(30):
            with sel_lock:
                self.selected = 'ab'[i % 2]
            r.refresh() if i % 3 else None      # sometimes leave it to the switcher thread
            time.sleep(0.01)
        stop.set()
        cam.join(5)
        r.close()
        a, b = self.tags_on_disk('a'), self.tags_on_disk('b')
        self.assertTrue(all(t.startswith('a') for t in a), a[:5])
        self.assertTrue(all(t.startswith('b') for t in b), b[:5])
        self.assertEqual((len(a), len(b)), (sent['a'], sent['b']))
        self.assertGreater(len(a) + len(b), 0)


class ErrorTest(RouterCase):
    def test_busy_store_reports_error_and_retries_after_backoff(self):
        blocker = ProfilePreviewStore(self.ws['b'], fsync=False)
        r = self.router(retry_s=5.0)
        self.select(r, 'a')
        self.select(r, 'b')
        s = r.stats()
        self.assertEqual((s['state'], s['profile']), ('error', 'b'))
        self.assertEqual(s['last_error']['stage'], 'open')
        self.assertIn('StoreBusy', s['last_error']['error'])
        json.dumps(s)
        self.assertIsNone(r._store)             # A was closed, not kept for B's frames
        self.assertFalse(self.put(r, 'b-lost'))
        self.assertEqual(self.tags_on_disk('a'), [])
        blocker.close()
        r.refresh()                             # within backoff: not retried
        self.assertEqual(r.stats()['state'], 'error')
        self.mono += 5.0
        self.assertEqual(r.refresh()['state'], 'open')
        self.assertTrue(self.put(r, 'b1'))

    def test_callback_errors_are_reported_and_nothing_written(self):
        boom = {'on': False}

        def active():
            if boom['on']:
                raise RuntimeError('brain state unavailable')
            return self.active()

        r = self.router(active=active)
        self.select(r, 'a')
        boom['on'] = True
        self.assertFalse(self.put(r, 'x'))
        self.assertEqual(r.query(60, 60, 10, now_wall=self.now), [])
        s = r.stats()
        self.assertEqual(s['state'], 'unknown')
        self.assertEqual(s['last_error']['stage'], 'resolve')
        self.assertEqual(s['errors'], 1)        # reported once per run of failures
        json.dumps(s)
        boom['on'] = False
        self.assertEqual(r.stats()['state'], 'open')

    def test_malformed_binding_is_an_error(self):
        for bad in [('a', 'A', 'relative/path'), ('', 'A', self.ws['a']), ('a', 'A'),
                    ('a', 'A', self.ws['a'] / '..' / 'b')]:
            r = self.router(active=lambda bad=bad: bad)
            self.assertFalse(r.submit(frame(self.now, 'x')))
            s = r.refresh()
            self.assertEqual(s['state'], 'unknown', bad)
            self.assertEqual(s['last_error']['stage'], 'resolve')

    def test_bad_read_arguments_raise_value_error(self):
        r = self.router()
        with self.assertRaises(ValueError):
            r.query(-1, 10, 5)
        with self.assertRaises(ValueError):
            r.query(1, 10, True)
        with self.assertRaises(ValueError):
            r.nearest(float('nan'), 1)

    def test_close_is_idempotent_and_final(self):
        r = self.router()
        self.select(r, 'a')
        self.put(r, 'a1')
        r.close()
        r.close()
        self.assertFalse(self.put(r, 'late'))
        self.assertEqual(r.query(60, 60, 10, now_wall=self.now), [])
        self.assertEqual(r.stats()['state'], 'closed')
        self.assertEqual(self.tags_on_disk('a'), ['a1'])
        ProfilePreviewStore(self.ws['a'], fsync=False).close()   # lock released


class AsyncTest(RouterCase):
    def test_async_helpers(self):
        import asyncio
        r = self.router()
        self.selected = 'a'

        async def main():
            s = await r.arefresh()
            self.put(r, 'a1')
            self.flush(r)
            got = await r.aquery(60, 60, 10, self.now)
            near = await r.anearest(self.now, 1)
            await r.aclose()
            return s, got, near

        s, got, near = asyncio.run(main())
        self.assertEqual(s['state'], 'open')
        self.assertEqual([tag_of(d) for _, d in got], ['a1'])
        self.assertEqual(tag_of(near[1]), 'a1')


if __name__ == '__main__':
    unittest.main()
