"""ProfilePreviewStore: 250 MB of Eyes frames on disk in a Brain profile's work folder.

Offline: temporary directories only; no camera, no profile, no service.
"""
import errno
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from c3 import profile_preview_store as P  # noqa: E402
from c3.profile_preview_store import ProfilePreviewStore, StoreBusy  # noqa: E402

T0 = 1_800_000_000.0


def jpeg(tag, size=32):
    body = (b'%d|' % tag) * size
    return b'\xff\xd8' + body[:max(0, size - 4)] + b'\xff\xd9'


def frame(wall, tag=None, size=32):
    return SimpleNamespace(wall=wall, data=jpeg(int(wall) if tag is None else tag, size))


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.ws = self.root / 'profile-a'
        self.ws.mkdir()
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        for path in self.root.rglob('*'):            # undo chmods so cleanup works
            if not path.is_symlink():
                os.chmod(path, 0o700)
        self._tmp.cleanup()

    def open(self, ws=None, **kw):
        kw.setdefault('fsync', False)
        store = ProfilePreviewStore(ws or self.ws, **kw)
        self.stores.append(store)
        return store

    def reopen(self, store, **kw):
        store.close()
        return self.open(**kw)

    def files(self, ws=None):
        return sorted(os.listdir((ws or self.ws) / P.STORE_DIRNAME))


class BasicsTest(StoreCase):
    def test_append_query_nearest_stats(self):
        s = self.open()
        for i in range(10):
            self.assertTrue(s.append(frame(T0 + i)))
        got = s.query(seconds_ago=10, duration_s=10, limit=100, now_wall=T0 + 10)
        self.assertEqual([w for w, _ in got], [T0 + i for i in range(10)])
        self.assertEqual(got[3][1], jpeg(int(T0 + 3)))
        self.assertEqual(s.nearest(T0 + 4.4, 1.0), (T0 + 4, jpeg(int(T0 + 4))))
        st = s.stats()
        self.assertEqual((st['frames'], st['bytes'], st['oldest'], st['newest']), (10, 320, T0, T0 + 9))
        self.assertEqual(st['capacity_bytes'], P.HISTORY_BYTES)
        json.dumps(st)
        self.assertEqual(oct(os.stat(self.ws / P.STORE_DIRNAME).st_mode & 0o777), '0o700')

    def test_frame_cap_shrinks_existing_store_then_evicts_on_append(self):
        s = self.open(max_frames=5)
        for i in range(5):
            self.assertTrue(s.append(frame(T0 + i)))
        marker = self.ws / P.STORE_DIRNAME / 'unrelated.txt'
        marker.write_text('keep')
        with mock.patch.object(P.os, 'fsync') as sync:
            s = self.reopen(s, max_frames=3)
            self.assertEqual(s.stats()['max_frames'], 3)
            self.assertEqual(s.stats()['oldest'], T0 + 2)
            self.assertEqual(s.stats()['evicted'], 2)
            self.assertTrue(s.append(frame(T0 + 5)))
            self.assertEqual(s.stats()['oldest'], T0 + 3)
            s.close()
        sync.assert_not_called()
        self.assertEqual(len(self.files()), 4)
        self.assertEqual(marker.read_text(), 'keep')

    def test_default_profile_store_avoids_camera_frame_fsync(self):
        with mock.patch.object(P.os, 'fsync') as sync:
            with ProfilePreviewStore(self.ws) as store:
                self.assertTrue(store.append(frame(T0)))
                self.assertEqual(store.stats()['max_frames'], 14_400)
        sync.assert_not_called()

    def test_failed_eviction_blocks_publication_until_retry(self):
        s = self.open(max_frames=1)
        self.assertTrue(s.append(frame(T0)))
        old = self.files()
        with mock.patch.object(P.os, 'unlink', side_effect=OSError(errno.EIO, 'fault')):
            self.assertFalse(s.append(frame(T0 + 1)))
            self.assertFalse(s.append(frame(T0 + 2)))
        self.assertEqual(self.files(), old)
        self.assertTrue(s.append(frame(T0 + 3)))
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(s.stats()['newest'], T0 + 3)

    def test_query_window_limit_and_present_clamp(self):
        s = self.open()
        for i in range(100):
            s.append(frame(T0 + i))
        now = T0 + 100
        got = s.query(seconds_ago=50, duration_s=10, limit=100, now_wall=now)
        self.assertEqual([w for w, _ in got], [T0 + i for i in range(50, 61)])    # inclusive ends
        got = s.query(seconds_ago=100, duration_s=99, limit=5, now_wall=now)
        self.assertEqual([w for w, _ in got], [T0, T0 + 25, T0 + 49, T0 + 74, T0 + 99])   # 49.5 tie: earlier
        got = s.query(seconds_ago=5, duration_s=3600, limit=100, now_wall=now)    # never past now
        self.assertEqual([w for w, _ in got], [T0 + 95, T0 + 96, T0 + 97, T0 + 98, T0 + 99])
        self.assertEqual(len(s.query(seconds_ago=100, duration_s=100, limit=1, now_wall=now)), 1)
        self.assertEqual(s.query(seconds_ago=10, duration_s=10, limit=0, now_wall=now), [])
        for bad in ((-1, 1, 1), (1, float('nan'), 1), (1, 1, -1), (1, 1, 2.0), (True, 1, 1)):
            with self.assertRaises(ValueError):
                s.query(*bad, now_wall=now)

    def test_gap_vs_nearest(self):
        s = self.open()
        for i in range(5):
            s.append(frame(T0 + i))
        for i in range(5):
            s.append(frame(T0 + 1000 + i))                  # the camera was off for ~16 minutes
        self.assertEqual(s.query(seconds_ago=900, duration_s=600, limit=10, now_wall=T0 + 1005), [])
        self.assertIsNone(s.nearest(T0 + 500, 10))         # in the gap: nothing close enough
        self.assertEqual(s.nearest(T0 + 500, 600)[0], T0 + 4)
        self.assertEqual(s.nearest(T0 + 503, 600)[0], T0 + 1000)
        self.assertEqual(s.nearest(T0 + 503, 497)[0], T0 + 1000)   # exactly max_distance away
        self.assertIsNone(s.nearest(T0 + 503, 496.9))
        self.assertEqual(s.nearest(T0 + 0.5, 1)[0], T0)          # tie: the earlier
        self.assertIsNone(s.nearest(T0 + 2000, 5))
        self.assertIsNone(self.open(self._ws('b')).nearest(T0, 1e9))

    def _ws(self, name):
        path = self.root / name
        path.mkdir()
        return path

    def test_rejects_bad_frames(self):
        s = self.open(capacity_bytes=100)
        self.assertFalse(s.append(frame(T0, size=101)))       # larger than the whole budget
        self.assertFalse(s.append(SimpleNamespace(wall=T0, data=b'not a jpeg at all')))
        self.assertFalse(s.append(SimpleNamespace(wall=float('inf'), data=jpeg(1))))
        self.assertFalse(s.append(SimpleNamespace(wall=-1, data=jpeg(1))))
        self.assertFalse(s.append(SimpleNamespace(data=jpeg(1))))
        self.assertTrue(s.append(frame(T0, size=100)))
        self.assertEqual(s.stats()['rejected'], 5)
        self.assertEqual(len(self.files()), 1)

    def test_constructor_validation(self):
        for cap in (0, P.HISTORY_BYTES + 1, 1.5, True):
            with self.assertRaises(ValueError):
                ProfilePreviewStore(self.ws, capacity_bytes=cap)
        with self.assertRaises(ValueError):
            ProfilePreviewStore('relative/profile')
        with self.assertRaises(ValueError):
            ProfilePreviewStore(self.ws / '..' / 'profile-a')
        with self.assertRaises(FileNotFoundError):
            ProfilePreviewStore(self.root / 'missing')


class FifoTest(StoreCase):
    def test_strict_fifo_budget(self):
        s = self.open(capacity_bytes=1000)
        sizes = [100, 300, 200, 250, 150, 400, 100, 1000, 60, 60]
        kept = []
        for i, size in enumerate(sizes):
            self.assertTrue(s.append(frame(T0 + i, size=size)))
            kept.append((T0 + i, size))
            while sum(sz for _, sz in kept) > 1000:
                kept.pop(0)
            st = s.stats()
            self.assertLessEqual(st['bytes'], 1000)
            self.assertEqual(st['bytes'], sum(sz for _, sz in kept))
            self.assertEqual(st['oldest'], kept[0][0])
            on_disk = sum(os.path.getsize(self.ws / P.STORE_DIRNAME / n) for n in self.files())
            self.assertEqual(on_disk, st['bytes'])
        got = s.query(seconds_ago=100, duration_s=100, limit=100, now_wall=T0 + 50)
        self.assertEqual([(w, len(d)) for w, d in got], kept)

    def test_full_capacity_frame_evicts_everything(self):
        s = self.open(capacity_bytes=200)
        s.append(frame(T0, size=50))
        s.append(frame(T0 + 1, size=200))
        self.assertEqual(s.stats()['frames'], 1)
        self.assertEqual(s.stats()['evicted'], 1)


class PersistenceTest(StoreCase):
    def test_reopen_keeps_history_and_order(self):
        s = self.open(capacity_bytes=10_000, fsync=True)
        for i in range(20):
            s.append(frame(T0 + i * 1.25))
        before = s.stats()
        s = self.reopen(s, capacity_bytes=10_000)
        after = s.stats()
        for key in ('bytes', 'frames', 'oldest', 'newest'):
            self.assertEqual(before[key], after[key])
        self.assertTrue(s.append(frame(T0 + 100)))
        walls = [w for w, _ in s.query(1000, 1000, 1000, now_wall=T0 + 200)]
        self.assertEqual(walls, [T0 + i * 1.25 for i in range(20)] + [T0 + 100])
        seqs = [int(n.split('-')[1]) for n in self.files()]
        self.assertEqual(seqs, list(range(21)))

    def test_reopen_with_smaller_capacity_evicts_oldest(self):
        s = self.open(capacity_bytes=1000)
        for i in range(10):
            s.append(frame(T0 + i, size=100))
        s = self.reopen(s, capacity_bytes=450)
        st = s.stats()
        self.assertEqual((st['frames'], st['bytes'], st['oldest']), (4, 400, T0 + 6))
        self.assertEqual(len(self.files()), 4)

    def test_reopen_reads_no_jpeg_bytes(self):
        s = self.open()
        for i in range(5):
            s.append(frame(T0 + i))
        s.close()
        store_dir = self.ws / P.STORE_DIRNAME
        for name in os.listdir(store_dir):
            os.chmod(store_dir / name, 0)               # unreadable: open must not need the bytes
        real_read = os.read
        with mock.patch.object(P.os, 'read', side_effect=AssertionError('read on open')):
            s = self.open()
        self.assertEqual(s.stats()['frames'], 5)
        self.assertIs(P.os.read, real_read)

    def test_crash_leftovers_are_cleaned(self):
        s = self.open()
        for i in range(5):
            s.append(frame(T0 + i))
        s.close()
        d = self.ws / P.STORE_DIRNAME
        names = self.files()
        (d / '.tmp-000000000099-0123456789abcdef').write_bytes(b'\xff\xd8half')    # crash mid-write
        with open(d / names[1], 'r+b') as fh:                                      # truncated frame
            fh.truncate(10)
        (d / 'notes.txt').write_text('model scribble')                             # not ours
        (d / P._name(77, int((T0 + 2.5) * 1e6), 32)).write_bytes(jpeg(9))          # out of order
        (d / P._name(3, int((T0 + 3) * 1e6), 32)).write_bytes(jpeg(9))             # duplicate seq
        s = self.open()
        self.assertEqual([w for w, _ in s.query(100, 100, 100, now_wall=T0 + 50)], [T0, T0 + 2, T0 + 3, T0 + 4])
        left = self.files()
        self.assertIn('notes.txt', left)
        self.assertFalse(any(n.startswith('.tmp-') for n in left))
        self.assertNotIn(names[1], left)
        self.assertEqual(len(left), 5)
        self.assertTrue(s.append(frame(T0 + 5)))
        self.assertIn(P._name(78, int((T0 + 5) * 1e6), 32), self.files())   # seq never reused

    def test_missing_or_corrupt_file_at_read_is_skipped(self):
        s = self.open()
        for i in range(5):
            s.append(frame(T0 + i))
        d = self.ws / P.STORE_DIRNAME
        names = self.files()
        os.unlink(d / names[2])
        (d / names[3]).write_bytes(b'\x00' * 32)                          # same size, not a JPEG
        got = s.query(100, 100, 100, now_wall=T0 + 50)
        self.assertEqual([w for w, _ in got], [T0, T0 + 1, T0 + 4])
        self.assertEqual(s.nearest(T0 + 2.1, 5)[0], T0 + 1)              # falls back to the next nearest
        self.assertEqual(s.stats()['frames'], 3)
        self.assertEqual(s.stats()['bytes'], 96)


class ClockTest(StoreCase):
    def test_small_backward_step_rejected(self):
        s = self.open()
        self.assertTrue(s.append(frame(T0 + 10)))
        self.assertFalse(s.append(frame(T0 + 9.5)))
        self.assertTrue(s.append(frame(T0 + 10)))         # equal is fine
        self.assertEqual(s.stats()['rejected'], 1)

    def test_large_backward_step_discards_future_frames(self):
        s = self.open()
        for i in range(5):
            s.append(frame(T0 + 3600 + i))               # clock ran an hour fast
        s.append(frame(T0 - 2))
        s.append(frame(T0 - 1))
        self.assertTrue(s.append(frame(T0)))              # corrected
        st = s.stats()
        self.assertEqual((st['frames'], st['oldest'], st['newest'], st['discarded']), (3, T0 - 2, T0, 5))
        self.assertEqual(len(self.files()), 3)


class SafetyTest(StoreCase):
    def test_symlinked_workspace_refused(self):
        link = self.root / 'link'
        link.symlink_to(self.ws)
        with self.assertRaises(OSError):
            ProfilePreviewStore(link)

    def test_symlinked_store_dir_refused(self):
        other = self.root / 'profile-b'
        other.mkdir()
        b = self.open(other)
        b.append(frame(T0))
        b.close()
        (self.ws / P.STORE_DIRNAME).symlink_to(other / P.STORE_DIRNAME)
        with self.assertRaises(OSError):
            ProfilePreviewStore(self.ws)

    def test_planted_symlink_and_hardlink_not_read(self):
        other = self.root / 'profile-b'
        other.mkdir()
        b = self.open(other)
        b.append(frame(T0, tag=666))
        b.close()
        secret = other / P.STORE_DIRNAME / os.listdir(other / P.STORE_DIRNAME)[0]
        a = self.open()
        a.append(frame(T0 - 10))
        a.close()
        d = self.ws / P.STORE_DIRNAME
        (d / P._name(5, int(T0 * 1e6), 32)).symlink_to(secret)
        os.link(secret, d / P._name(6, int((T0 + 1) * 1e6), 32))
        a = self.open()
        self.assertEqual(a.stats()['frames'], 1)
        self.assertIsNone(a.nearest(T0, 5))
        # planted after opening: refused at read time too
        a.append(frame(T0 + 2))
        name = self.files()[-1]
        os.unlink(d / name)
        os.link(secret, d / name)
        self.assertIsNone(a.nearest(T0 + 2, 0.5))

    def test_profiles_are_isolated(self):
        other = self.root / 'profile-b'
        other.mkdir()
        a, b = self.open(), self.open(other)
        a.append(frame(T0, tag=1))
        b.append(frame(T0, tag=2))
        self.assertEqual(a.nearest(T0, 0)[1], jpeg(1))
        self.assertEqual(b.nearest(T0, 0)[1], jpeg(2))

    def test_second_open_is_busy(self):
        s = self.open()
        with self.assertRaises(StoreBusy):
            ProfilePreviewStore(self.ws)
        s.close()
        self.open().close()

    def test_foreign_owner_refused(self):
        with mock.patch.object(P.os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaises(PermissionError):
                ProfilePreviewStore(self.ws)


class FailureTest(StoreCase):
    def test_disk_full_returns_false_and_cleans_temp(self):
        s = self.open()
        self.assertTrue(s.append(frame(T0)))
        with mock.patch.object(P.os, 'write', side_effect=OSError(errno.ENOSPC, 'No space left on device')):
            self.assertFalse(s.append(frame(T0 + 1)))
        st = s.stats()
        self.assertEqual((st['frames'], st['errors']), (1, 1))
        self.assertIn('No space', st['last_error'])
        self.assertEqual(len(self.files()), 1)
        self.assertTrue(s.append(frame(T0 + 2)))

    def test_rename_failure_keeps_budget_consistent(self):
        s = self.open(capacity_bytes=64)
        s.append(frame(T0))
        s.append(frame(T0 + 1))
        with mock.patch.object(P.os, 'rename', side_effect=OSError(errno.EIO, 'I/O error')):
            self.assertFalse(s.append(frame(T0 + 2)))
        st = s.stats()
        self.assertEqual((st['frames'], st['bytes']), (1, 32))     # eviction happened first
        on_disk = sum(os.path.getsize(self.ws / P.STORE_DIRNAME / n) for n in self.files())
        self.assertEqual(on_disk, 32)

    def test_closed_store_is_inert(self):
        s = self.open()
        s.append(frame(T0))
        s.close()
        s.close()
        self.assertFalse(s.append(frame(T0 + 1)))
        self.assertFalse(s.submit(frame(T0 + 1)))
        self.assertEqual(s.query(10, 10, 10, now_wall=T0 + 1), [])
        self.assertIsNone(s.nearest(T0, 1))
        self.assertTrue(s.stats()['closed'])


class ThreadTest(StoreCase):
    def test_submit_writes_in_background(self):
        s = self.open()
        accepted = sum(s.submit(frame(T0 + i)) for i in range(5))
        s.flush()
        self.assertEqual(s.stats()['frames'], accepted)
        s = self.reopen(s)
        self.assertEqual(s.stats()['frames'], accepted)

    def test_submit_never_blocks(self):
        s = self.open()
        gate = threading.Event()
        real_append = s.append
        s.append = lambda f: (gate.wait(5), real_append(f))[1]
        results = [s.submit(frame(T0 + i)) for i in range(P.SUBMIT_QUEUE + 5)]
        self.assertFalse(all(results))
        self.assertGreater(s.stats()['dropped'], 0)
        gate.set()
        s.flush()

    def test_concurrent_appends_and_reads(self):
        s = self.open(capacity_bytes=3200)                      # 100 frames of 32 bytes
        errors = []

        def writer(base):
            for i in range(200):
                s.append(frame(T0 + base + i * 0.001, tag=i))

        def reader():
            try:
                for _ in range(300):
                    got = s.query(100, 100, 50, now_wall=T0 + 50)
                    walls = [w for w, _ in got]
                    assert walls == sorted(walls), walls
                    for _, data in got:
                        assert data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')
                    s.nearest(T0 + 0.1, 1)
                    st = s.stats()
                    assert st['bytes'] <= 3200, st
            except Exception as exc:     # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(0,))] + [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        st = s.stats()
        self.assertEqual((st['frames'], st['bytes']), (100, 3200))
        self.assertEqual(len(self.files()), 100)

    def test_close_waits_for_readers(self):
        s = self.open()
        for i in range(3):
            s.append(frame(T0 + i))
        started, release = threading.Event(), threading.Event()
        real_read = s._read

        def slow_read(entry):
            started.set()
            release.wait(5)
            return real_read(entry)

        s._read = slow_read
        out = []
        t = threading.Thread(target=lambda: out.append(s.query(10, 10, 10, now_wall=T0 + 5)))
        t.start()
        started.wait(5)
        closer = threading.Thread(target=s.close)
        closer.start()
        closer.join(0.2)
        self.assertTrue(closer.is_alive())              # still waiting on the reader
        release.set()
        t.join(5)
        closer.join(5)
        self.assertFalse(closer.is_alive())
        self.assertEqual(len(out[0]), 3)


if __name__ == '__main__':
    unittest.main()
