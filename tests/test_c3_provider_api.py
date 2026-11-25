"""Exercise the C3 provider routes without running a provider CLI or C3 service."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from aiohttp import ClientConnectionError
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import api, provider_health  # noqa: E402


class ProviderApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.health = Mock(spec=provider_health.ProviderHealth)
        self.c3 = SimpleNamespace(brain_backend='resident', brain=Mock(), provider_health=self.health,
                                  voice=Mock(state='asleep'), transcript=Mock())
        self.auth = {'Authorization': 'Bearer secret'}
        self.client = await self.open_client()

    async def open_client(self):
        client = TestClient(TestServer(api.make_app(self.c3, 'secret')))
        self.addAsyncCleanup(client.close)
        await client.start_server()
        return client

    async def test_auth_precedes_validation_and_provider_work(self):
        response = await self.client.get('/v1/providers?refresh=invalid')
        self.assertEqual(response.status, 401)
        response = await self.client.post('/v1/providers/test', data='invalid JSON',
                                          headers={'Authorization': 'Bearer wrong'})
        self.assertEqual(response.status, 401)
        self.health.status.assert_not_awaited()
        self.health.test.assert_not_awaited()

    async def test_status_passes_refresh_and_returns_provider_payload(self):
        result = {'providers': [{'id': 'codex'}, {'id': 'claude'}]}
        self.health.status.return_value = result
        for query in ('', '?refresh=1', '?refresh=0'):
            with self.subTest(query=query):
                response = await self.client.get('/v1/providers' + query, headers=self.auth)
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), result)
        self.health.status.assert_has_awaits([call(refresh=False), call(refresh=True), call(refresh=False)])
        self.health.test.assert_not_awaited()

    async def test_validation_and_exact_test_row(self):
        for query in ('?refresh=', '?refresh=true', '?refresh=1&refresh=0'):
            response = await self.client.get('/v1/providers' + query, headers=self.auth)
            self.assertEqual(response.status, 400)
        self.health.status.assert_not_awaited()

        path = '/v1/providers/test'
        response = await self.client.post(path, data='invalid JSON', headers=self.auth)
        self.assertEqual(response.status, 400)
        for payload in ([], {}, {'id': 3}, {'id': '   '}, {'id': 'codex', 'text': 'prompt'}):
            with self.subTest(payload=payload):
                response = await self.client.post(path, json=payload, headers=self.auth)
                self.assertEqual(response.status, 400)
        self.health.test.assert_not_awaited()

        self.health.test.side_effect = ValueError('raw CLI output')
        response = await self.client.post(path, json={'id': 'unknown'}, headers=self.auth)
        self.assertEqual(response.status, 400)
        self.assertEqual(await response.text(), 'Unknown provider id')

        row = {'id': 'claude', 'name': 'Claude Code', 'installed': True, 'auth': 'signed_out',
               'check': 'failed', 'detail': 'No native sign-in.', 'recovery': 'Sign in and refresh.'}
        self.health.test.side_effect = None
        self.health.test.return_value = row
        response = await self.client.post(path, json={'id': 'claude'}, headers=self.auth)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), row)
        self.health.test.assert_awaited_with('claude')

    async def test_unexpected_runtime_errors_are_redacted(self):
        self.health.status.side_effect = self.health.test.side_effect = RuntimeError('raw CLI output')
        with self.assertLogs('c3.api', level='WARNING') as logs:
            status = await self.client.get('/v1/providers', headers=self.auth)
            test = await self.client.post('/v1/providers/test', json={'id': 'codex'}, headers=self.auth)
        for response in (status, test):
            self.assertEqual(response.status, 503)
            self.assertNotIn('raw CLI output', await response.text())
        self.assertNotIn('raw CLI output', '\n'.join(logs.output))

    async def test_get_only_probes_auth_and_signed_out_provider_leaves_voice_available(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.c3.provider_health = provider_health.ProviderHealth(Path(folder))
        client = await self.open_client()

        def auth_only(argv, **_):
            if argv[1:3] == ('login', 'status'):
                return 0, 'Logged in using ChatGPT', ''
            if argv[-3:] == ('auth', 'status', '--json'):
                return 1, '{"loggedIn":false}', ''
            self.fail(f'Unexpected provider model turn: {argv}')

        with patch.object(provider_health.shutil, 'which', side_effect=lambda name, **_: '/fake/' + name), \
             patch.object(provider_health, '_run', side_effect=auth_only) as run:
            response = await client.get('/v1/providers?refresh=1', headers=self.auth)
            rows = await response.json()
            self.assertEqual([(row['auth'], row['check']) for row in rows['providers']],
                             [('subscription', 'untested'), ('signed_out', 'untested')])
            self.assertEqual(run.await_count, 2)
            response = await client.post('/v1/message', json={'text': 'Hello'}, headers=self.auth)
            self.assertEqual(await response.json(), {'queued': True, 'voice': 'asleep'})
            self.assertEqual(run.await_count, 2)
        self.c3.voice.add_turn.assert_called_once()

    async def test_shutdown_cancels_active_provider_check(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def long_test(_):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        self.health.test.side_effect = long_test
        request = asyncio.create_task(self.client.post('/v1/providers/test', json={'id': 'codex'}, headers=self.auth))
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.wait_for(self.client.server.close(), timeout=2)
        with self.assertRaises(ClientConnectionError):
            await asyncio.wait_for(request, timeout=2)
        self.assertTrue(cancelled.is_set())


if __name__ == '__main__':
    unittest.main()
