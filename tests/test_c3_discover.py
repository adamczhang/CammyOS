"""Finding Cammy's portal when the router gives the camera a new address."""
import asyncio
import ipaddress
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import discover as D  # noqa: E402


class Health(BaseHTTPRequestHandler):
    body = b'{"service":"cammy-portal","status":"ok"}'

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(('127.0.0.1', 0), Health)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_port
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / 'portal-address'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_portal_is_recognised_by_its_health_reply(self):
        self.assertTrue(D.is_portal(self.url))
        self.assertFalse(D.is_portal('http://127.0.0.1:1'))

    def test_explicit_portal_wins_and_a_live_cache_is_used(self):
        self.assertEqual(D.resolve('http://x:1', cache=self.cache), 'http://x:1')
        D.remember(self.url, self.cache)
        with mock.patch.object(D, 'sweep', side_effect=AssertionError('no sweep needed')):
            self.assertEqual(D.resolve(cache=self.cache), self.url)

    def test_a_dead_cache_sweeps_and_saves_the_new_address(self):
        D.remember('http://127.0.0.1:1', self.cache)
        with mock.patch.object(D, 'sweep', return_value=self.url) as sweep:
            self.assertEqual(D.resolve(cache=self.cache), self.url)
        self.assertEqual(sweep.call_args.kwargs['prefer'], '127.0.0.1')
        self.assertEqual(D.cached(self.cache), self.url)
        with mock.patch.object(D, 'sweep', return_value=None):
            D.remember('http://127.0.0.1:1', self.cache)
            self.assertEqual(D.resolve(cache=self.cache), 'http://127.0.0.1:1')   # nothing found: keep it

    def test_sweep_skips_networks_too_large_and_finds_the_portal(self):
        port = self.server.server_port
        with mock.patch.object(D, 'PORT', port):
            found = D.sweep([ipaddress.ip_interface('127.0.0.2/30')], prefer='127.0.0.1')
            self.assertEqual(found, 'http://127.0.0.1:%d' % port)
            self.assertIsNone(D.sweep([ipaddress.ip_interface('10.0.0.1/8')]))


class PortalWatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_lost_camera_found_elsewhere_restarts_c3(self):
        from c3 import service as S
        body = SimpleNamespace(up=asyncio.Event())
        clock = SimpleNamespace(t=0.0)
        restarted, swept = [], []
        real_sleep = asyncio.sleep

        async def fast_sleep(_):
            clock.t += S.PORTAL_WATCH_S
            await real_sleep(0)

        def sweep(networks, prefer):
            swept.append(prefer)
            return 'http://192.0.2.56:8787'

        with mock.patch.object(S.asyncio, 'sleep', fast_sleep), \
             mock.patch('c3.discover.remember') as remember:
            await asyncio.wait_for(S.portal_watch(body, 'http://192.0.2.53:8787', sweep=sweep,
                                                  restart=lambda: restarted.append(1), clock=lambda: clock.t), 5)
        self.assertEqual(swept, ['192.0.2.53'])
        self.assertEqual(restarted, [1])
        remember.assert_called_once_with('http://192.0.2.56:8787')
        self.assertGreaterEqual(clock.t, S.PORTAL_LOST_S)


if __name__ == '__main__':
    unittest.main()
