"""PC-only desktop/offline acceptance. No camera addresses are contacted."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('desktop_host', ROOT / 'host/cammy_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.gateway = host.Gateway('desktop-test-token', ['http://192.0.2.1:8787'], offline=True)
        self.gateway.opener = Mock()
        self.gateway.opener.open.side_effect = AssertionError('Offline mode attempted network egress')
        self.server = host.make_server(self.gateway, ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None, token=None):
        headers = {} if token is None else {'Authorization': 'Bearer ' + token}
        return self.opener.open(urllib.request.Request(self.base + path,
            data=body, headers=headers), timeout=2)

    def test_offline_guard_blocks_monitor_and_direct_requests(self):
        self.assertFalse(self.gateway.refresh()['connected'])
        with self.assertRaisesRegex(OSError, 'Offline mode'):
            self.gateway.request('http://192.0.2.1:8787', 'POST', '/v1/session', b'{}')
        self.gateway.opener.open.assert_not_called()

    def test_offline_api_and_auth(self):
        with self.request('/health') as reply:
            health = json.load(reply)
        self.assertTrue(health['offline'])
        self.assertFalse(health['connected'])
        for path, body, token, status in [
            ('/v1/gateway', None, None, 401),
            ('/v1/simulation', b'{}', 'desktop-test-token', 404),
            ('/v1/simulation/frame', None, 'desktop-test-token', 404),
            ('/v1/state', None, 'desktop-test-token', 503),
            ('/v1/stop', b'{}', 'desktop-test-token', 503),
        ]:
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.request(path, body, token)
            self.assertEqual(failure.exception.code, status)
            failure.exception.close()
        with self.request('/v1/gateway', token='desktop-test-token') as reply:
            self.assertTrue(json.load(reply)['offline'])
        self.gateway.opener.open.assert_not_called()

    def test_panel_assets_are_local_and_contain_no_credentials(self):
        for path, mime in [('/', 'text/html'), ('/app.js', 'text/javascript'), ('/style.css', 'text/css')]:
            with self.request(path) as reply:
                self.assertIn(mime, reply.headers['Content-Type'])
                self.assertIn("frame-ancestors 'none'", reply.headers['Content-Security-Policy'])
                self.assertEqual(reply.headers['Cache-Control'], 'no-store')
                self.assertNotIn(b'desktop-test-token', reply.read())
        for path in ['/../cammy_host.py', '/web/../../private/config.json', '/index.html']:
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.request(path, token='desktop-test-token')
            failure.exception.close()
        self.gateway.opener.open.assert_not_called()

    def test_offline_cli_starts_without_device_config(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / 'token'
            token.write_text('offline-cli-test-token')
            process = subprocess.Popen([sys.executable, str(ROOT / 'host/cammy_host.py'),
                '--offline', '--token-file', str(token), '--port', '0'],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                # The already tested HTTP server binds port 0; confirm CLI startup is stable.
                time.sleep(.25)
                self.assertIsNone(process.poll(), 'Offline CLI failed to start')
            finally:
                process.terminate()
                _, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, error.decode())

    @unittest.skipUnless(shutil.which('node'), 'Node is used only to check the browser script')
    def test_browser_state_and_controls(self):
        subprocess.run(['node', str(ROOT / 'tests/desktop_state.cjs')], check=True, timeout=10)
