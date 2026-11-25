"""Eyes' single decoder and the preview history: no camera, no service, fake clocks."""
import asyncio
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
from c3 import body as B  # noqa: E402
from c3 import preview_history as H  # noqa: E402
from c3 import profile_preview_router as R  # noqa: E402
from c3 import profile_preview_store as S  # noqa: E402


def jpeg(tag, size=200):
    """A structurally valid JPEG: an APP segment holding a fake FFD9, stuffed FFs and restart
    markers in the entropy data, and a second scan (progressive style)."""
    app = b'JFIF\x00' + b'\xff\xd9\xff\xd8' + tag
    def scan(seed):
        data = bytes((i * seed + len(tag)) % 255 for i in range(size)).replace(b'\xff', b'\xfe')
        return data[:size // 3] + b'\xff\x00' + data[size // 3:2 * size // 3] + b'\xff\xd3' + data[2 * size // 3:]
    sos = b'\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00'
    seg = lambda m, body: b'\xff' + bytes([m]) + (len(body) + 2).to_bytes(2, 'big') + body  # noqa: E731
    return (b'\xff\xd8' + seg(0xE0, app) + seg(0xDB, bytes(65)) + b'\xff\xff' + seg(0xC0, bytes(9))
            + sos + scan(37) + sos + scan(53) + b'\xff\xd9')


class StartupHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_eyes_startup_reopens_and_trims_selected_store_with_camera_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with S.ProfilePreviewStore(workspace, max_frames=4) as store:
                for i in range(4):
                    self.assertTrue(store.append(H.Frame(1_800_000_000 + i, i, jpeg(b'frame'), 1)))
            eyes = B.Eyes(SimpleNamespace(up=asyncio.Event(), epoch=0), frame_dir=workspace / 'frames')

            def make_store(path, budget):
                return S.ProfilePreviewStore(path, budget, max_frames=2)

            def make_router(profile, capacity):
                return R.ProfilePreviewRouter(profile, capacity, store_factory=make_store)

            eyes.configure_profile_history(lambda: ('desk', 'Desk', workspace), router_factory=make_router)
            task = asyncio.create_task(eyes.run())
            directory = workspace / S.STORE_DIRNAME
            try:
                # No status read or new frame is needed to open and shrink the store.
                async with asyncio.timeout(2):
                    while len(list(directory.glob('*.jpg'))) > 2:
                        await asyncio.sleep(0.01)
                async with asyncio.timeout(2):
                    while eyes.history_status['frames'] != 2:
                        await asyncio.sleep(0.01)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await asyncio.to_thread(eyes.stop)


class JpegStreamTest(unittest.TestCase):
    maxDiff = 200
    def frames(self, chunks, **kw):
        stream, out = H.JpegStream(**kw), []
        for chunk in chunks:
            out += stream.feed(chunk)
        return stream, out

    def test_random_chunking_gives_exact_frames(self):
        frames = [jpeg(b'%d' % i, 150 + i * 13) for i in range(6)]
        data = b''.join(frames)
        rng = random.Random(7)
        for _ in range(40):
            cuts = sorted(rng.sample(range(1, len(data)), rng.randint(1, 60)))
            chunks = [data[a:b] for a, b in zip([0] + cuts, cuts + [len(data)])]
            _, out = self.frames(chunks)
            self.assertEqual(out, frames)
        _, out = self.frames([bytes([b]) for b in data])     # byte by byte
        self.assertEqual(out, frames)

    def test_garbage_and_truncated_frames_are_dropped(self):
        a, b, c = jpeg(b'a'), jpeg(b'b'), jpeg(b'c')
        stream, out = self.frames([b'noise\xff\x00', a, b'\x00\x01', b[:len(b) // 2], c, b'\xff'])
        self.assertEqual(out, [a, c])               # the cut-off b is never emitted
        self.assertGreater(stream.discarded, 0)
        stream, out = self.frames([a[:-1]])          # missing EOI at the end of a stream
        self.assertEqual(out, [])

    def test_oversized_frame_resyncs(self):
        big, small = jpeg(b'big', 5000), jpeg(b's', 50)
        _, out = self.frames([big, small], max_frame=1000)
        self.assertEqual(out, [small])

    def test_reset_drops_partial(self):
        a = jpeg(b'a')
        stream = H.JpegStream()
        self.assertEqual(stream.feed(a[:40]), [])
        stream.reset()
        self.assertEqual(stream.feed(a[40:]), [])
        self.assertEqual(stream.feed(a), [a])


def frame(mono, size=10, wall_offset=1_000_000.0, gen=1):
    return H.Frame(mono + wall_offset, mono, bytes([int(mono) % 256]) * size, gen)


class FrameRingTest(unittest.TestCase):
    maxDiff = 200
    def test_capacity_is_capped_and_eviction_is_fifo(self):
        self.assertEqual(H.FrameRing().capacity_bytes, 250_000_000)
        for bad in (250_000_001, 0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                H.FrameRing(bad)
        ring = H.FrameRing(1000)
        for i in range(10):
            self.assertTrue(ring.append(frame(i, 300)))
            self.assertLessEqual(ring.bytes, 1000)
        self.assertEqual(len(ring), 3)
        self.assertEqual([f.mono for f in ring.window(0, 99)], [7, 8, 9])
        stats = ring.stats()
        self.assertEqual((stats['bytes'], stats['evicted'], stats['added']), (900, 7, 10))
        self.assertFalse(ring.append(frame(20, 1001)))       # can never fit
        self.assertFalse(ring.append(frame(5, 10)))          # out of order
        self.assertEqual(ring.stats()['rejected'], 2)
        self.assertEqual(ring.bytes, 900)

    def test_exact_budget_fill(self):
        ring = H.FrameRing(1000)
        for i in range(4):
            ring.append(frame(i, 250))
        self.assertEqual((ring.bytes, len(ring)), (1000, 4))
        ring.append(frame(4, 1))
        self.assertEqual((ring.bytes, len(ring)), (751, 4))

    def test_query_interval_and_sampling(self):
        ring = H.FrameRing()
        for i in range(100):
            ring.append(frame(float(i)))
        now = 100.0
        got = ring.query(30, 10, 100, now)                   # inclusive [70, 80]
        self.assertEqual([f.mono for f in got], list(range(70, 81)))
        self.assertEqual([f.mono for f in ring.query(30, 10, 3, now)], [70, 75, 80])
        self.assertEqual([f.mono for f in ring.query(30, 10, 1, now)], [75])
        self.assertEqual([f.mono for f in ring.query(30, 10, 11, now)], list(range(70, 81)))
        self.assertEqual(ring.query(30, 10, 0, now), [])
        self.assertEqual([f.mono for f in ring.query(5, 60, 100, now)], [95, 96, 97, 98, 99])  # clipped at now
        self.assertEqual([f.mono for f in ring.query(30, 0, 5, now)], [70])
        self.assertEqual([f.mono for f in ring.query(30.5, 0, 5, now)], [])
        self.assertEqual(ring.query(500, 10, 5, now), [])
        for args in ((-1, 1, 1), (1, -1, 1), (float('nan'), 1, 1), (1, float('inf'), 1), (1, 1, -1),
                     (1, 1, 2.0), (True, 1, 1), (1, 1, True), ('1', 1, 1)):
            with self.assertRaises(ValueError, msg=args):
                ring.query(*args, now)

    def test_sampling_is_even_in_time_across_a_gap(self):
        frames = [frame(t) for t in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 60]]
        got = [f.mono for f in H.sample_evenly(frames, 3)]
        self.assertEqual(got, [0, 10, 60])                   # nearest to 0, 30, 60
        got = [f.mono for f in H.sample_evenly(frames, 11)]
        self.assertEqual(len(set(got)), 11)
        self.assertEqual(got, sorted(got))
        self.assertEqual((got[0], got[-1]), (0, 60))


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def wall(self):
        return self.t + 1_700_000_000.0


class FakeProc:
    """A decoder child: a real pipe for stdout, a scripted lifetime."""
    count = 0

    def __init__(self, cmd, **kw):
        FakeProc.count += 1
        self.cmd, self.pid, self.returncode = cmd, 900000 + FakeProc.count, None
        r, self.w = os.pipe()
        self.stdout = os.fdopen(r, 'rb', buffering=0)
        self.terminated = False

    def poll(self):
        return self.returncode

    def write(self, data):
        os.write(self.w, data)

    def exit(self, code=1):
        self.returncode = code
        if self.w is not None:
            os.close(self.w)
            self.w = None

    def terminate(self):
        self.terminated = True
        self.exit(-15)

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class _EyesFixture(unittest.TestCase):
    """An Eyes on a fake decoder process (real pipe and reader thread) and a fake clock."""
    maxDiff = 200

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        up = threading.Event()
        up.set()
        self.body = SimpleNamespace(up=up, rtsp='rtsp://cammy/preview', epoch=1)
        self.procs = []

        def popen(cmd, **kw):
            proc = FakeProc(cmd, **kw)
            self.procs.append(proc)
            return proc
        p = patch.object(B.subprocess, 'Popen', side_effect=popen)
        p.start()
        self.addCleanup(p.stop)
        self.eyes = B.Eyes(self.body, capacity_bytes=10_000, clock=self.clock, wall=self.clock.wall,
                           frame_dir=self.tmp.name)
        self.addCleanup(self.eyes.stop)

    def step(self):
        return asyncio.run(self.eyes.step())

    def feed(self, proc, *frames, gap=1.0):
        """Write frames through the real pipe and reader thread, one clock tick apart."""
        for data in frames:
            self.clock.t += gap
            before = self.eyes.stats['accepted'] + self.eyes.stats['burst_drops']
            proc.write(data)
            deadline = time.monotonic() + 3
            while self.eyes.stats['accepted'] + self.eyes.stats['burst_drops'] == before:
                self.assertLess(time.monotonic(), deadline, 'reader did not ingest')
                time.sleep(0.002)



class EyesTest(_EyesFixture):
    """Memory mode: no profile gate configured."""

    def test_physical_mute_pauses_sampling_and_keeps_existing_history(self):
        self.body.capture = (2, True)
        self.assertEqual(self.step(), 'started')
        first = jpeg(b'before')
        self.feed(self.procs[0], first)
        old_generation = self.eyes._live[0]
        self.body.capture = (3, False)
        self.assertIsNone(self.eyes.newest())
        self.assertFalse(self.eyes._ingest(old_generation, jpeg(b'during')))
        self.assertEqual(self.step(), 'halted')
        self.assertEqual(self.step(), 'idle')
        self.assertEqual(len(self.eyes.ring), 1)
        self.body.capture = (4, True)
        self.assertEqual(self.step(), 'started')
        self.assertFalse(self.eyes._ingest(old_generation, jpeg(b'late')))
        after = jpeg(b'after')
        self.feed(self.procs[1], after)
        self.assertEqual(self.eyes.newest(), after)
        self.assertEqual(len(self.eyes.ring), 2)

    def test_own_capture_pauses_the_decoder_without_failures(self):
        busy = [False]
        self.eyes.paused = lambda: busy[0]
        self.assertEqual(self.step(), 'started')
        busy[0] = True
        self.procs[0].exit(0)                                # the stock photo mode ends RTSP
        self.assertEqual(self.step(), 'paused')
        self.assertEqual(self.step(), 'paused')
        self.assertEqual((self.eyes.stats['exits'], self.eyes._failures, self.eyes.restarts), (0, 0, 0))
        busy[0] = False
        self.assertEqual(self.step(), 'backoff')              # the camera settles into video mode
        self.clock.t += B.CAPTURE_SETTLE_S
        self.assertEqual(self.step(), 'started')
        self.assertEqual(len(self.procs), 2)

    def test_one_native_decoder_without_scale_or_duplicates(self):
        self.assertEqual(self.step(), 'started')
        self.assertEqual(self.step(), 'running')
        self.assertEqual(len(self.procs), 1)
        cmd = ' '.join(self.procs[0].cmd)
        self.assertNotIn('scale', cmd)
        self.assertNotIn('fps=', cmd)                        # fps would duplicate frames on a stall
        self.assertIn('select=', cmd)
        self.assertIn('passthrough', cmd)
        self.assertIn('pipe:1', cmd)
        self.assertIn(B.EYES_MARK, cmd)
        self.assertEqual(Path(self.tmp.name, 'grabber.pid').read_text(), str(self.procs[0].pid))
        self.eyes.wanted = False                             # Voice asleep: decoder unaffected
        self.assertEqual(self.step(), 'running')

    def test_frames_timestamps_and_freshness(self):
        self.step()
        proc = self.procs[0]
        self.assertIsNone(self.eyes.newest())
        a, b = jpeg(b'a'), jpeg(b'b')
        self.feed(proc, a)
        t_a = self.clock.t
        self.assertEqual(self.eyes.newest(), a)
        self.feed(proc, b)
        self.assertEqual(self.eyes.newest(), b)
        got = self.eyes.history(1.0, 1.0, 10)
        self.assertEqual(got, [(t_a + 1_700_000_000.0, a), (self.clock.t + 1_700_000_000.0, b)])
        self.assertEqual(self.eyes.newest(after=self.clock.wall() + 0.01), None)
        self.clock.t += 4.0
        self.assertEqual(self.eyes.newest(), b)              # exactly max_age old: still fresh
        self.clock.t += 0.001
        self.assertIsNone(self.eyes.newest())
        self.assertFalse(self.eyes.history_status['fresh'])
        self.assertTrue(self.eyes.history_status['live'])
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ['grabber.pid'])  # no frames on disk

    def test_burst_after_stall_is_not_stored_with_false_times(self):
        self.step()
        proc = self.procs[0]
        self.feed(proc, jpeg(b'1'))
        self.feed(proc, jpeg(b'2'), jpeg(b'3'), gap=0.1)    # catch-up burst
        self.assertEqual(len(self.eyes.ring), 1)             # both burst frames came < 0.5 s after it
        self.assertEqual(self.eyes.stats['burst_drops'], 2)
        self.feed(self.procs[0], jpeg(b'4'), gap=0.31)       # 0.5 s after the last stored frame
        self.assertEqual(len(self.eyes.ring), 2)

    def test_disconnect_and_epoch_end_freshness_at_once_but_keep_history(self):
        self.step()
        a = jpeg(b'a')
        self.feed(self.procs[0], a)
        self.assertEqual(self.eyes.newest(), a)
        self.body.epoch = 2                                 # a bring-up, before the supervisor sees it
        self.assertIsNone(self.eyes.newest())
        self.assertFalse(self.eyes.history_status['live'])
        self.assertEqual(self.step(), 'started')            # restart without backoff
        self.assertTrue(self.procs[0].terminated)
        self.assertEqual(len(self.procs), 2)
        self.assertIsNone(self.eyes.newest())               # the old frame is not live in epoch 2
        self.assertEqual([d for _, d in self.eyes.history(5, 5, 10)], [a])
        b = jpeg(b'b')
        self.feed(self.procs[1], b)
        self.assertEqual(self.eyes.newest(), b)
        self.body.up.clear()                                # lease or preview lost
        self.assertIsNone(self.eyes.newest())
        self.assertEqual(self.step(), 'halted')
        self.assertEqual(self.step(), 'idle')
        self.assertIsNone(self.eyes.proc)
        self.assertFalse(Path(self.tmp.name, 'grabber.pid').exists())
        self.assertEqual([d for _, d in self.eyes.history(10, 10, 10)], [a, b])
        self.body.up.set()
        self.body.epoch = 3
        self.assertEqual(self.step(), 'started')
        self.assertIsNone(self.eyes.newest())
        self.eyes.stop()                                    # service exit drops the history
        self.assertEqual(self.eyes.history_status['frames'], 0)

    def test_superseded_decoder_frames_are_ignored(self):
        self.step()
        old = self.procs[0]
        self.body.epoch = 2
        self.step()
        self.assertFalse(self.eyes._ingest(1, jpeg(b'late')))
        self.assertEqual(len(self.eyes.ring), 0)
        self.assertTrue(old.terminated)

    def test_stall_and_exit_restart_with_bounded_backoff(self):
        self.assertEqual(self.step(), 'started')
        self.clock.t += B.EYES_FIRST_FRAME_S
        self.assertEqual(self.step(), 'running')            # exactly the limit: not yet
        self.clock.t += 0.01
        self.assertEqual(self.step(), 'stalled')
        self.assertEqual(self.eyes.stats['stalls'], 1)
        delays = []
        for _ in range(7):
            self.assertEqual(self.step(), 'backoff')
            delay = self.eyes._next_start - self.clock.t
            delays.append(round(delay, 2))
            self.clock.t += delay
            self.assertEqual(self.step(), 'started')
            self.procs[-1].exit(1)
            self.assertEqual(self.step(), 'exited')
        self.assertEqual(delays, [1, 2, 5, 10, 30, 30, 30])
        self.assertEqual(self.eyes.restarts, 8)
        self.assertEqual(sum(1 for p in self.procs if p.poll() is None), 0)
        # a healthy decoder resets the backoff; a running one that stops sending frames stalls
        self.clock.t = self.eyes._next_start
        self.assertEqual(self.step(), 'started')
        self.feed(self.procs[-1], *[jpeg(b'%d' % i) for i in range(B.EYES_HEALTHY_FRAMES)])
        self.assertEqual(self.eyes._failures, 0)
        self.clock.t += B.EYES_STALL_S + 0.01
        self.assertEqual(self.step(), 'stalled')
        self.assertEqual(self.eyes._next_start - self.clock.t, 1)
        self.assertEqual(len(self.eyes.ring), B.EYES_HEALTHY_FRAMES)   # kept across the restart
        self.assertEqual(sum(1 for p in self.procs if p.poll() is None), 0)

    def test_start_error_backs_off(self):
        self.body.rtsp = None
        self.assertEqual(self.step(), 'idle')
        self.body.rtsp = 'rtsp://cammy/preview'
        with patch.object(B.subprocess, 'Popen', side_effect=OSError('no ffmpeg')):
            self.assertEqual(self.step(), 'error')
        self.assertEqual(self.step(), 'backoff')
        self.clock.t += 1
        self.assertEqual(self.step(), 'started')

    def test_ring_turns_over_within_budget(self):
        self.step()
        frames = [jpeg(b'%03d' % i, 900) for i in range(40)]
        size = len(frames[0])
        self.feed(self.procs[0], *frames)
        ring = self.eyes.ring
        self.assertLessEqual(ring.bytes, 10_000)
        self.assertEqual(len(ring), 10_000 // size)
        self.assertEqual(ring.newest().data, frames[-1])
        status = self.eyes.history_status
        json.dumps(status)
        self.assertEqual(status['capacity_bytes'], 10_000)
        self.assertEqual(status['frames'], 10_000 // size)
        self.assertEqual(status['evicted'], 40 - 10_000 // size)
        self.assertEqual(status['newest_age_s'], 0.0)
        self.assertEqual(status['span_s'], float(len(ring) - 1))
        self.assertTrue(status['fresh'] and status['decoder']['running'])
        n = len(ring)                                         # frames 1 s apart, newest at now
        got = self.eyes.history(n - 1, n - 1, 4)
        kept = frames[-n:]
        want = [kept[round(k * (n - 1) / 3)] for k in range(4)]
        self.assertEqual([d for _, d in got], want)
        self.assertEqual([w for w, _ in got], sorted(w for w, _ in got))
        self.assertEqual(self.eyes.history(n, 0, 4), [])     # evicted: before the oldest kept

    def test_look_waits_for_a_frame_after_the_given_time(self):
        self.step()
        a = jpeg(b'a')

        async def scenario():
            waiter = asyncio.create_task(self.eyes.look(wait=2.0, after=self.clock.wall() + 0.5))
            await asyncio.sleep(0.05)
            await asyncio.to_thread(self.feed, self.procs[0], a)
            return await waiter
        self.assertEqual(asyncio.run(scenario()), a)
        self.assertIsNone(asyncio.run(self.eyes.look(wait=0.0, after=self.clock.wall() + 1)))


class Profiles:
    """The service's active_profile(): (id, name, workspace) of the selected profile, or None."""

    def __init__(self, root):
        self.root = Path(root)
        self.selected = None

    def select(self, profile_id):
        if profile_id is None:
            self.selected = None
            return
        work = self.root / 'profiles' / profile_id / 'work'
        work.mkdir(parents=True, exist_ok=True)
        self.selected = (profile_id, 'Profile ' + profile_id.upper(), work)

    def __call__(self):
        return self.selected

    def store_dir(self, profile_id):
        return self.root / 'profiles' / profile_id / 'work' / '.cammy-preview'


class SlowStore(S.ProfilePreviewStore):
    """A real store whose reads can be held mid-read, to switch profiles under them."""
    hold = entered = None

    def _delay(self, out):
        if SlowStore.hold is not None:
            SlowStore.entered.set()
            SlowStore.hold.wait(5)
        return out

    def query(self, *args, **kw):
        return self._delay(super().query(*args, **kw))

    def nearest(self, *args, **kw):
        return self._delay(super().nearest(*args, **kw))


class ProfileEyesTest(_EyesFixture):
    """Profile mode: frames persist only on disk, in the selected profile's workspace."""

    def setUp(self):
        super().setUp()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.profiles = Profiles(self.data.name)
        self.routers = []
        SlowStore.hold = None
        self.addCleanup(setattr, SlowStore, 'hold', None)

    def router(self, active_profile, capacity_bytes):
        router = R.ProfilePreviewRouter(
            active_profile, capacity_bytes, wall=self.clock.wall,
            store_factory=lambda ws, cap: SlowStore(ws, cap, fsync=False))
        self.routers.append(router)
        return router

    def configure(self, eyes=None):
        (eyes or self.eyes).configure_profile_history(self.profiles, router_factory=self.router)

    def select(self, profile_id):
        """Select a profile and let the router bind it (the service selects on the loop; the
        router's switcher opens the store a moment later)."""
        self.profiles.select(profile_id)
        self.eyes._router.refresh()

    def stored(self, count, eyes=None):
        eyes = eyes or self.eyes
        deadline = time.monotonic() + 3
        while eyes.history_status['frames'] < count:
            self.assertLess(time.monotonic(), deadline, 'writer did not store the frames')
            time.sleep(0.002)

    def test_frames_go_only_to_the_selected_profile_on_disk(self):
        self.configure()
        self.assertIsNone(self.eyes.ring)                    # no RAM history besides the live frame
        self.select('alpha')
        self.step()
        a, b = jpeg(b'a1'), jpeg(b'a2')
        self.feed(self.procs[0], a, b)
        self.assertEqual(self.eyes.newest(), b)              # the live look is unchanged
        self.stored(2)
        self.assertEqual([d for _, d in self.eyes.history(5, 5, 10)], [a, b])
        wall_b = self.clock.wall()
        self.assertEqual(self.eyes.history_frame(wall_b + 0.4), (wall_b, b))
        self.assertIsNone(self.eyes.history_frame(wall_b + 10))
        status = self.eyes.history_status
        self.assertEqual((status['mode'], status['profile'], status['profile_name']), ('profile', 'alpha', 'Profile ALPHA'))
        self.assertEqual(status['frames'], 2)
        self.assertEqual(status['bytes'], len(a) + len(b))
        self.assertEqual((status['oldest_time'], status['newest_time']), (wall_b - 1, wall_b))
        self.assertEqual(status['span_s'], 1.0)
        self.assertTrue(status['live'] and status['fresh'])
        json.dumps(status)
        self.assertEqual(len([n for n in os.listdir(self.profiles.store_dir('alpha')) if n.endswith('.jpg')]), 2)

    def test_no_profile_keeps_nothing_and_has_no_memory_fallback(self):
        self.configure()
        self.step()
        a = jpeg(b'n1')
        self.feed(self.procs[0], a, jpeg(b'n2'))
        self.assertIsNotNone(self.eyes.newest())             # Voice and the API still see live frames
        self.assertEqual(self.eyes.history(10, 10, 10), [])
        self.assertIsNone(self.eyes.history_frame(self.clock.wall()))
        status = self.eyes.history_status
        self.assertEqual((status['profile'], status['frames'], status['state']), (None, 0, 'no_profile'))
        self.assertGreaterEqual(status['dropped_no_profile'], 2)
        self.assertFalse((Path(self.data.name) / 'profiles').exists())

    def test_pausing_recording_keeps_saved_frames_available(self):
        self.configure()
        self.select('alpha')
        self.step()
        saved, paused = jpeg(b'saved'), jpeg(b'paused')
        self.feed(self.procs[0], saved)
        self.stored(1)
        self.eyes.history_recording = lambda: False
        self.feed(self.procs[0], paused)
        self.assertEqual(self.eyes.newest(), paused)
        self.assertEqual([data for _, data in self.eyes.history(10, 10, 10)], [saved])

    def test_router_failure_still_has_no_memory_fallback(self):
        def broken(*_):
            raise OSError('disk gone')
        self.eyes.configure_profile_history(self.profiles, router_factory=broken)
        self.step()
        self.feed(self.procs[0], jpeg(b'f1'))
        self.assertIsNotNone(self.eyes.newest())
        self.assertIsNone(self.eyes.ring)
        self.assertEqual(self.eyes.history(10, 10, 10), [])
        self.assertIsNone(self.eyes.history_frame(self.clock.wall()))
        status = self.eyes.history_status
        self.assertEqual((status['profile'], status['frames']), (None, 0))
        self.assertIn('OSError', status['error'])

    def test_separate_profiles_never_share_frames(self):
        self.configure()
        self.select('alpha')
        self.step()
        a = jpeg(b'alpha')
        self.feed(self.procs[0], a)
        self.stored(1)
        wall_a = self.clock.wall()
        self.select('beta')
        self.assertEqual(self.eyes.history(10, 10, 10), [])   # beta has nothing yet
        self.assertIsNone(self.eyes.history_frame(wall_a))
        b = jpeg(b'beta')
        self.feed(self.procs[0], b)
        self.stored(1)
        self.assertEqual([d for _, d in self.eyes.history(10, 10, 10)], [b])
        self.assertEqual(self.eyes.history_status['profile'], 'beta')
        self.select('alpha')                                  # alpha's frames are still on disk
        self.assertEqual([d for _, d in self.eyes.history(10, 10, 10)], [a])
        self.assertEqual(self.eyes.history_frame(wall_a), (wall_a, a))
        names = lambda p: sorted(n for n in os.listdir(self.profiles.store_dir(p)) if n.endswith('.jpg'))  # noqa: E731
        self.assertEqual(len(names('alpha')), 1)
        self.assertEqual(len(names('beta')), 1)

    def test_a_read_delayed_across_a_switch_returns_nothing(self):
        self.configure()
        self.select('alpha')
        self.step()
        a = jpeg(b'slow')
        self.feed(self.procs[0], a)
        self.stored(1)
        wall_a = self.clock.wall()
        Path(self.data.name, 'profiles', 'beta', 'work').mkdir(parents=True)
        for read, empty in ((lambda: self.eyes.history(10, 10, 10), []),
                            (lambda: self.eyes.history_frame(wall_a), None)):
            SlowStore.hold, SlowStore.entered = threading.Event(), threading.Event()
            out = []
            reader = threading.Thread(target=lambda: out.append(read()))
            reader.start()
            self.assertTrue(SlowStore.entered.wait(5))        # the read has alpha's frame in hand
            self.profiles.select('beta')                      # the selection changes under it
            SlowStore.hold.set()
            reader.join(5)
            self.assertEqual(out, [empty])                    # alpha's frame is never returned for beta
            SlowStore.hold = None
            self.profiles.select('alpha')
            self.eyes._router.refresh()
            self.assertNotEqual(read(), empty)                # undisturbed, the same read finds it

    def test_history_outlives_disconnect_and_service_restart(self):
        self.configure()
        self.select('alpha')
        self.step()
        a, b = jpeg(b'r1'), jpeg(b'r2')
        self.feed(self.procs[0], a, b)
        self.stored(2)
        self.body.up.clear()                                  # disconnect: live ends, history stays
        self.assertEqual(self.step(), 'halted')
        self.assertIsNone(self.eyes.newest())
        self.assertFalse(self.eyes.history_status['live'])
        self.assertEqual([d for _, d in self.eyes.history(5, 5, 10)], [a, b])
        self.body.up.set()
        self.body.epoch = 2
        self.step()
        self.assertIsNone(self.eyes.newest())                 # the old frame is not live in epoch 2
        writers = lambda: [t for t in threading.enumerate() if t.name.startswith('preview-store')]  # noqa: E731
        self.assertTrue(writers())
        self.eyes.stop()                                      # service exit
        self.eyes.stop()                                      # idempotent
        self.assertTrue(self.routers[0].stats()['state'] == 'closed')
        deadline = time.monotonic() + 3
        while writers():
            self.assertLess(time.monotonic(), deadline, 'store writer still running')
            time.sleep(0.01)
        self.assertEqual(self.eyes.history(5, 5, 10), [])     # stopped: no router, no memory fallback
        self.assertEqual(self.eyes.history_status['error'], 'stopped')
        # A new service process: a new Eyes opens the same profile store (its lock was released).
        again = B.Eyes(self.body, capacity_bytes=10_000, clock=self.clock, wall=self.clock.wall,
                       frame_dir=self.tmp.name)
        self.addCleanup(again.stop)
        self.configure(again)
        again._router.refresh()
        self.assertEqual([d for _, d in again.history(10, 10, 10)], [a, b])
        self.assertEqual(again.history_status['frames'], 2)

    def test_status_reads_no_files(self):
        self.configure()
        self.select('alpha')
        self.step()
        self.feed(self.procs[0], jpeg(b's1'))
        self.stored(1)

        def no_io(*_, **__):
            raise AssertionError('history_status touched the disk')
        with patch.object(os, 'open', no_io), patch.object(os, 'read', no_io), \
                patch.object(os, 'listdir', no_io), patch.object(os, 'scandir', no_io), \
                patch.object(os, 'stat', no_io), patch.object(os, 'lstat', no_io), \
                patch('builtins.open', no_io):
            status = self.eyes.history_status
        self.assertEqual((status['profile'], status['frames']), ('alpha', 1))

    def test_live_gates_match_memory_mode(self):
        self.configure()
        self.select('alpha')
        self.step()
        old = self.procs[0]
        a = jpeg(b'g1')
        self.feed(old, a)
        self.clock.t += B.EYES_FRESH_S + 0.01
        self.assertIsNone(self.eyes.newest())                 # stale by age
        self.assertFalse(self.eyes.history_status['fresh'])
        self.body.epoch = 2                                   # a new bring-up supersedes the decoder
        self.step()
        self.assertTrue(self.eyes._ingest(1, jpeg(b'late')) is False)  # old generation ignored
        self.assertIsNone(self.eyes.newest())
        b = jpeg(b'g2')
        self.feed(self.procs[-1], b)
        self.assertEqual(self.eyes.newest(), b)
        self.assertEqual(asyncio.run(self.eyes.look(wait=0.0)), b)


class RealChildTest(unittest.TestCase):
    maxDiff = 200
    """The real Popen path: a child writing JPEGs in odd chunks to the pipe, and the stale-PID
    owner check."""

    def test_child_pipe_and_stale_kill(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = [jpeg(b'%d' % i, 3000) for i in range(3)]
            Path(tmp, 'frames.bin').write_bytes(b''.join(frames))
            child = ('import sys, time; d = open(sys.argv[1], "rb").read(); n = len(d) // 3\n'
                     'for i in range(0, len(d), 777):\n'
                     '    sys.stdout.buffer.write(d[i:i + 777]); sys.stdout.flush(); time.sleep(0.12)\n'
                     'time.sleep(30)\n')
            up = threading.Event()
            up.set()
            body = SimpleNamespace(up=up, rtsp='rtsp://x', epoch=1)
            eyes = B.Eyes(body, frame_dir=tmp,
                          command=lambda rtsp: [sys.executable, '-c', child, str(Path(tmp, 'frames.bin')),
                                                'ffmpeg', B.EYES_MARK])
            try:
                self.assertEqual(asyncio.run(eyes.step()), 'started')
                deadline = time.monotonic() + 10
                while len(eyes.ring) < 3 and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual([d for _, d in eyes.history(30, 30, 10)], frames)
                pid = eyes.proc.pid
                # a restarted service finds the recorded PID and kills the leaked decoder
                leaked = B.Eyes(body, frame_dir=tmp)
                leaked._kill_stale()
                eyes.proc.wait(5)
                self.assertIsNotNone(eyes.proc.poll())
                self.assertEqual(eyes.proc.pid, pid)
            finally:
                eyes.stop()
            self.assertEqual(len(eyes.ring), 0)
            self.assertFalse(Path(tmp, 'grabber.pid').exists())

    def test_stale_kill_spares_unrelated_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', 'ffmpeg'])
            try:
                Path(tmp, 'grabber.pid').write_text(str(other.pid))
                B.Eyes(SimpleNamespace(up=threading.Event(), rtsp=None, epoch=0), frame_dir=tmp)._kill_stale()
                time.sleep(0.1)
                self.assertIsNone(other.poll())
            finally:
                other.kill()
                other.wait()


if __name__ == '__main__':
    unittest.main()
