"""Profile-gated camera history: the service gate, the viewer routes, look_back pinning, the
client and the Control Center tab.

Offline: no camera, no Gemini, no running service. Eyes is a fake honouring the agreed contract
(configure_profile_history(callback), history_status incl. profile, history_frame(timestamp,
max_distance_s=...) -> (Unix wall, jpeg) or None, history(seconds_ago, duration_s, limit)).
"""
import asyncio
import tempfile
import threading
import time
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from c3_client import C3Client, C3Error  # noqa: E402

try:                         # aiohttp and the Gemini SDK live in .venv-live; GTK in the system Python
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from google.genai import types
    from c3 import api, service
    from c3 import voice as V
    from c3.resident_brain import ResidentBrain
    from c3.transcript import Transcript
except ImportError:
    V = None
try:
    from c3 import gui
except (ImportError, ValueError):
    gui = None
needs_genai = unittest.skipUnless(V, 'google-genai/aiohttp not installed (use .venv-live)')
needs_gtk = unittest.skipUnless(gui, 'GTK bindings not installed (use the system python3)')

TOKEN = 'secret-token'
JPEG = b'\xff\xd8frame\xff\xd9'
T0 = 1_800_000_000.0


class Profiles:
    """The service's active_profile(): switchable resident truth."""

    def __init__(self, workdir):
        self.workdir = Path(workdir)
        self.current = ('desk', 'Desk helper', self.workdir / 'desk' / 'work')

    def select(self, profile_id):
        self.current = None if profile_id is None else (profile_id, profile_id.title(), self.workdir / profile_id / 'work')

    def __call__(self):
        return self.current


class FakeEyes:
    """Owner-aware history; `during` runs inside a lookup (to switch profiles mid-read)."""

    def __init__(self, profiles):
        self.profiles = profiles
        self.frames = {'desk': [(T0 + i, JPEG + bytes([i])) for i in range(5)], 'garage': [(T0 + 2, b'\xff\xd8other\xff\xd9')]}
        self.during = None
        self.threads = []
        self.calls = 0
        self.fail = False

    @property
    def owner(self):
        current = self.profiles()
        return current[0] if current else None

    def history_status(self):
        self.threads.append(threading.current_thread())
        frames = self.frames.get(self.owner, [])
        return {'profile': self.owner, 'bytes': sum(len(j) for _, j in frames), 'capacity_bytes': 250_000_000,
                'frames': len(frames), 'oldest_time': frames[0][0] if frames else None,
                'newest_time': frames[-1][0] if frames else None, 'decoder': {'running': True}}

    def history_frame(self, timestamp, max_distance_s):
        self.calls += 1
        self.threads.append(threading.current_thread())
        if self.fail:
            raise OSError('disk gone')
        frames = self.frames.get(self.owner, [])
        if self.during:
            self.during()
        best = min(frames, key=lambda f: abs(f[0] - timestamp), default=None)
        return best if best and abs(best[0] - timestamp) <= max_distance_s else None

    def history(self, seconds_ago, duration_s, limit):
        self.calls += 1
        self.threads.append(threading.current_thread())
        now = time.time()
        frames = [(now - seconds_ago + i, JPEG) for i in range(int(duration_s) + 1)][:limit]
        if self.during:
            self.during()
        return frames


@needs_genai
class PreviewRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profiles = Profiles(self.tmp.name)
        self.eyes = FakeEyes(self.profiles)
        c3 = SimpleNamespace(eyes=self.eyes, active_profile=self.profiles, brain_backend='off')
        self.client = TestClient(TestServer(api.make_app(c3, TOKEN)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def get(self, path, auth=True):
        return await self.client.get(path, headers={'Authorization': 'Bearer ' + TOKEN} if auth else {})

    async def test_auth_comes_first(self):
        for path in ('/v1/preview/history', '/v1/preview/frame?profile=desk&at=%r' % T0, '/v1/preview/frame?at=bad'):
            response = await self.get(path, auth=False)
            self.assertEqual(response.status, 401, path)
        self.assertEqual((self.eyes.calls, self.eyes.threads), (0, []))

    async def test_history_contract_reads_off_the_loop(self):
        response = await self.get('/v1/preview/history')
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {
            'profile': 'desk', 'profile_name': 'Desk helper', 'bytes': sum(len(j) for _, j in self.eyes.frames['desk']),
            'capacity_bytes': 250_000_000, 'recording_allowed': True,
            'frames': 5, 'oldest': T0, 'newest': T0 + 4})
        self.assertTrue(self.eyes.threads and all(t is not threading.main_thread() for t in self.eyes.threads))

    async def test_frame_is_raw_jpeg_with_time_and_profile(self):
        response = await self.get('/v1/preview/frame?profile=desk&at=%r' % (T0 + 2.4))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, 'image/jpeg')
        self.assertEqual(await response.read(), JPEG + bytes([2]))
        self.assertEqual(float(response.headers['X-Cammy-Frame-Time']), T0 + 2)
        self.assertEqual(response.headers['X-Cammy-Profile'], 'desk')
        self.assertNotIn(threading.main_thread(), self.eyes.threads)

    async def test_gap_or_overwritten_is_404(self):
        for at in (T0 - 60, T0 + 30):
            response = await self.get('/v1/preview/frame?profile=desk&at=%r' % at)
            self.assertEqual(response.status, 404, at)

    async def test_bad_query_is_400(self):
        for query in ('profile=desk', 'profile=desk&at=nan', 'profile=desk&at=-1', 'at=%r' % T0, 'profile=desk&at=inf'):
            response = await self.get('/v1/preview/frame?' + query)
            self.assertEqual(response.status, 400, query)
        self.assertEqual(self.eyes.calls, 0)

    async def test_other_or_stale_profile_is_409_and_never_read(self):
        response = await self.get('/v1/preview/frame?profile=garage&at=%r' % (T0 + 2))
        self.assertEqual(response.status, 409)
        self.assertEqual(self.eyes.calls, 0)

    async def test_switch_during_lookup_sends_nothing(self):
        self.eyes.during = lambda: self.profiles.select('garage')
        response = await self.get('/v1/preview/frame?profile=desk&at=%r' % (T0 + 2))
        self.assertEqual(response.status, 409)
        self.assertNotIn(b'\xff\xd8', await response.read())

    async def test_eyes_owner_must_match_the_selection(self):
        self.eyes.history_status = lambda: dict(FakeEyes.history_status(self.eyes), profile='garage')
        self.assertEqual((await self.get('/v1/preview/history')).status, 409)
        self.assertEqual((await self.get('/v1/preview/frame?profile=desk&at=%r' % T0)).status, 409)
        self.assertEqual(self.eyes.calls, 0)

    async def test_no_profile_is_503(self):
        self.profiles.select(None)
        self.assertEqual((await self.get('/v1/preview/history')).status, 503)
        self.assertEqual((await self.get('/v1/preview/frame?profile=desk&at=%r' % T0)).status, 503)
        self.assertEqual(self.eyes.calls, 0)

    async def test_store_fault_is_503(self):
        self.eyes.fail = True
        self.assertEqual((await self.get('/v1/preview/frame?profile=desk&at=%r' % T0)).status, 503)


@needs_genai
class ClientHeaders(unittest.TestCase):
    """C3Client.preview_frame against the real routes on a loopback test server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        token = Path(self.tmp.name) / 'api-token'
        token.write_text(TOKEN)
        self.profiles = Profiles(self.tmp.name)
        self.eyes = FakeEyes(self.profiles)
        app = api.make_app(SimpleNamespace(eyes=self.eyes, active_profile=self.profiles, brain_backend='off'), TOKEN)
        self.loop = asyncio.new_event_loop()
        self.runner = web.AppRunner(app)
        self.loop.run_until_complete(self.runner.setup())
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        self.loop.run_until_complete(site.start())
        port = site._server.sockets[0].getsockname()[1]
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.client = C3Client('http://127.0.0.1:%d' % port, token, timeout=5)

    def tearDown(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()
        self.tmp.cleanup()

    def test_history_and_frame(self):
        history = self.client.preview_history()
        self.assertEqual((history['profile'], history['frames']), ('desk', 5))
        frame = self.client.preview_frame('desk', T0 + 1.2)
        self.assertEqual(frame, {'jpeg': JPEG + bytes([1]), 'time': T0 + 1, 'profile': 'desk'})
        with self.assertRaises(C3Error) as gap:
            self.client.preview_frame('desk', T0 + 99)
        self.assertEqual(gap.exception.code, 404)
        self.profiles.select('garage')
        with self.assertRaises(C3Error) as moved:
            self.client.preview_frame('desk', T0 + 2)
        self.assertEqual(moved.exception.code, 409)


@needs_genai
class ServiceGate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name).absolute()

        async def runner(request):
            raise AssertionError('no model runs here')
        self.brain = ResidentBrain(self.data / 'brain', runner=runner)
        await self.brain.create_profile('desk', 'Desk helper', 'claude')
        await self.brain.create_profile('garage', 'Garage', 'codex')

    async def asyncTearDown(self):
        await self.brain.close()
        self.tmp.cleanup()

    def c3(self, backend='resident'):
        c3 = object.__new__(service.C3)
        c3.family = service.familymod.FamilyPolicy(None, deployment='adult-standalone')   # Gemini path
        c3.deployment, c3.cloud, c3.play, c3.local_voice, c3._teardowns = 'adult-standalone', True, None, None, set()
        c3.brain_backend, c3.brain, c3.data_dir, c3._brain_failure = backend, self.brain, self.data, None
        return c3

    async def test_active_profile_is_resident_truth_only(self):
        c3 = self.c3()
        self.assertIsNone(c3.active_profile())                         # nothing selected: no history
        await self.brain.select_profile('desk')
        self.assertEqual(c3.active_profile(), ('desk', 'Desk helper', self.data / 'brain/profiles/desk/work'))
        self.assertTrue(c3.active_profile()[2].is_dir())
        await self.brain.select_profile('garage')
        self.assertEqual(c3.active_profile()[0], 'garage')
        self.assertIsNone(self.c3('t3').active_profile())
        other = self.c3()
        other.data_dir = self.data / 'elsewhere'                       # Brain root is not DATA/brain
        self.assertIsNone(other.active_profile())
        self.brain._storage_failed = True
        try:
            self.assertIsNone(c3.active_profile())
        finally:
            self.brain._storage_failed = False

    async def test_eyes_gets_the_gate_and_status_names_the_profile(self):
        await self.brain.select_profile('desk')
        c3 = self.c3()
        got = []
        c3.eyes = SimpleNamespace(restarts=0, configure_profile_history=got.append,
                                  history_status=lambda: {'profile': 'desk', 'frames': 3, 'bytes': 9,
                                                          'capacity_bytes': 250_000_000})
        c3._configure_eyes_history()
        self.assertEqual(got, [c3.selected_profile])
        self.assertEqual(got[0](), c3.active_profile())
        history = c3.eyes_status()['history']
        self.assertEqual((history['profile'], history['profile_name']), ('desk', 'Desk helper'))
        c3.eyes.history_status = lambda: {'profile': 'garage', 'frames': 1}
        self.assertNotIn('profile_name', c3.eyes_status()['history'])


class Quiet:
    def __getattr__(self, name):
        return lambda *a, **k: None


@needs_genai
class VoicePinning(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profiles = Profiles(self.tmp.name)
        self.eyes = FakeEyes(self.profiles)
        self.voice = V.Voice(SimpleNamespace(aio=SimpleNamespace(live=None)), 'm', lambda: 'persona', 'v', Quiet(),
                             Quiet(), self.eyes, Quiet(), Transcript(self.tmp.name), idle_s=0.2)
        self.voice.active_profile = self.profiles

    async def asyncTearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def call(**args):
        return types.FunctionCall(id='c1', name='look_back', args=args)

    async def test_photos_under_a_held_profile(self):
        response = await self.voice._tool(self.call(seconds_ago=10, duration_s=4, count=3))
        self.assertTrue(response.parts)
        self.assertNotIn(threading.main_thread(), self.eyes.threads)
        sent = await self.voice._recheck_owners([response])
        self.assertIs(sent[0], response)
        self.assertEqual(self.voice._look_back_owners, {})

    async def test_no_profile_reads_nothing(self):
        self.profiles.select(None)
        response = await self.voice._tool(self.call(seconds_ago=10))
        self.assertIsNone(response.parts)
        self.assertIn('no brain profile', response.response['error'])
        self.assertEqual(self.eyes.calls, 0)

    async def test_switch_during_read_withholds(self):
        self.eyes.during = lambda: self.profiles.select('garage')
        response = await self.voice._tool(self.call(seconds_ago=10, duration_s=4))
        self.assertIsNone(response.parts)
        self.assertIn('profile changed', response.response['error'])

    async def test_switch_before_send_withholds(self):
        response = await self.voice._tool(self.call(seconds_ago=10, duration_s=4))
        self.assertTrue(response.parts)
        self.profiles.select('garage')
        sent = await self.voice._recheck_owners([response])
        self.assertIsNone(sent[0].parts)
        self.assertEqual((sent[0].id, sent[0].name), ('c1', 'look_back'))
        self.assertIn('profile changed', sent[0].response['error'])

    async def test_eyes_owner_disagreeing_reads_nothing(self):
        self.eyes.history_status = lambda: {'profile': 'garage'}
        response = await self.voice._tool(self.call(seconds_ago=10))
        self.assertIsNone(response.parts)
        self.assertEqual(self.eyes.calls, 0)

    def test_description_says_disk_not_memory(self):
        self.assertIn('selected brain profile', V.LOOK_BACK.description)
        self.assertNotIn('only in memory', V.LOOK_BACK.description)


if gui is not None:
    class FakePanel(gui.Gtk.Box):
        made = []

        def __init__(self, client):
            super().__init__()
            self.client = client
            FakePanel.made.append(self)

    class OfflineClient(C3Client):
        """No sockets: every request fails as if C3 were down, and is counted."""
        calls = []

        def exchange(self, method, path, body=None, raw=False):
            OfflineClient.calls.append(path)
            raise C3Error(503, 'offline test')


@needs_gtk
class ControlCenterTab(unittest.TestCase):
    def test_camera_memory_row_says_disk_and_profile(self):
        now = time.time()
        title, detail, pill, _ = gui.history_row({'profile': 'desk', 'profile_name': 'Desk helper', 'frames': 120,
                                                  'bytes': 5e6, 'capacity_bytes': 250e6, 'oldest_time': now - 200,
                                                  'newest_time': now - 80, 'span_s': 119.5, 'fresh': False}, 1)
        self.assertEqual((title, pill), ('Camera memory', 'kept'))
        self.assertEqual(detail, 'Desk helper · 120 frames · 2 min · 5 of 250 MB on disk · 1 look-backs')
        self.assertNotIn('RAM', detail)
        self.assertEqual(gui.history_row({'profile': None, 'frames': 0})[2:], ('off', 'grey'))

    def test_tab_installed_without_focus_or_demo_requests(self):
        gui.Adw.init()
        saved = gui.LookBackPanel
        gui.LookBackPanel = FakePanel
        try:
            app = gui.Adw.Application(flags=gui.Gio.ApplicationFlags.NON_UNIQUE)
            app.register(None)                                           # startup, so windows may join
            demo = gui.Source('asleep')
            demo.client = OfflineClient(token_file=Path('/nonexistent'))
            demo.start()
            win = gui.Window(app, demo)
            self.assertIsNone(win.channel.get_child_by_name('look-back'))
            self.assertEqual(FakePanel.made, [])
            self.assertEqual(OfflineClient.calls, [])                   # demo: no requests at all
            win.close()

            live = gui.Source()
            live.client = OfflineClient(token_file=Path('/nonexistent'))
            win = gui.Window(app, live)
            panel = win.channel.get_child_by_name('look-back')
            self.assertIs(panel, FakePanel.made[-1])
            self.assertIs(panel.client, live.client)
            self.assertEqual(win.channel.get_visible_child_name(), 'conversation')
            self.assertFalse(panel.has_focus())
            self.assertEqual(win.channel.get_page(panel).get_title(), 'Look back')
            win.close()
        finally:
            gui.LookBackPanel = saved


if __name__ == '__main__':
    unittest.main()
