"""Provider diagnostics against fake native CLIs. No provider network calls."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from c3 import provider_health as PH  # noqa: E402


def claude_status(**overrides):
    data = {
        'loggedIn': True,
        'authMethod': 'claude.ai',
        'apiProvider': 'firstParty',
        'subscriptionType': 'max',
        'email': 'private-account@example.test',
        'organizationName': 'private-organization',
        **overrides,
    }
    return 0, json.dumps(data), 'private diagnostic'


class ProviderHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / 'provider-data'
        self.health = PH.ProviderHealth(self.root)
        self.cli = {'codex': '/mock/codex', 'claude': '/mock/claude'}
        self.enterContext(patch.object(PH.shutil, 'which', side_effect=lambda name, **_: self.cli.get(name)))
        self.run_cli = self.enterContext(patch.object(PH, '_run', new_callable=AsyncMock))
        self.run_cli.side_effect = self.fake_cli
        self.responses = {
            'codex': (0, '', 'Logged in using ChatGPT\n'),
            'claude': claude_status(),
        }
        self.model_response = (0, PH._REPLY + '\n', 'private error stream')
        self.turn_workspaces = []

    async def fake_cli(self, argv, *, cwd, env, timeout):
        self.assertLessEqual(timeout, 45)
        name = Path(argv[0]).name
        if ('login', 'status') == argv[1:3] or argv[-3:] == ('auth', 'status', '--json'):
            self.assertEqual(timeout, PH._AUTH_TIMEOUT)
            self.assertFalse(cwd.is_relative_to(self.root))
            response = self.responses[name]
        else:
            self.assertEqual(timeout, PH._TEST_TIMEOUT)
            self.assertEqual(cwd.parent, self.root)
            self.assertNotIn(cwd, self.turn_workspaces)
            self.turn_workspaces.append(cwd)
            if os.name == 'posix':
                self.assertEqual(cwd.stat().st_mode & 0o077, 0)
            self.assertEqual(env['TMPDIR'], str(cwd))
            self.assertEqual(argv[-1], PH._PROMPT)
            if name == 'codex':
                self.assertEqual(argv[1], 'exec')
                self.assertEqual(env['CODEX_SQLITE_HOME'], str(cwd))
                for flag in ('--ephemeral', '--ignore-user-config', '--ignore-rules', '--strict-config'):
                    self.assertIn(flag, argv)
                for value in ('read-only', 'approval_policy="never"', 'mcp_servers={}',
                              'web_search="disabled"', 'project_doc_max_bytes=0',
                              'shell_tool', 'unified_exec', 'hooks', 'plugins', 'apps',
                              'openai_base_url="https://chatgpt.com/backend-api/codex"'):
                    self.assertIn(value, argv)
                self.assertFalse(any('forced_login_method' in arg for arg in argv))
                if isinstance(self.model_response, tuple):
                    reply_path = Path(argv[argv.index('--output-last-message') + 1])
                    self.assertEqual(reply_path.parent, cwd)
                    reply_path.write_text(self.model_response[1])
                    return self.model_response[0], 'untrusted transcript', self.model_response[2]
            else:
                self.assertEqual(env['ANTHROPIC_CONFIG_DIR'], str(cwd / 'anthropic'))
                self.assertFalse((cwd / 'anthropic').exists())
                for flag in ('--safe-mode', '--print', '--no-session-persistence', '--strict-mcp-config'):
                    self.assertIn(flag, argv)
                self.assertEqual(argv[argv.index('--tools') + 1], '')
                self.assertEqual(argv[argv.index('--setting-sources') + 1], '')
                self.assertEqual(argv[argv.index('--max-turns') + 1], '1')
                self.assertEqual(argv[argv.index('--model') + 1], 'haiku')
                self.assertNotIn('--bare', argv)
            self.assertFalse({'--resume', '--continue', '--profile', '--trust'} & set(argv))
            response = self.model_response
        if isinstance(response, Exception):
            raise response
        return response

    async def test_status_cache_refresh_and_expiry_are_auth_only(self):
        first = await self.health.status()
        codex, claude = first['providers']
        self.assertEqual(set(codex), {'id', 'name', 'installed', 'auth', 'check', 'detail', 'recovery'})
        self.assertEqual([row['id'] for row in (codex, claude)], ['codex', 'claude'])
        self.assertEqual([row['auth'] for row in (codex, claude)], ['subscription'] * 2)
        self.assertTrue(all(row['installed'] and row['check'] == 'untested' for row in (codex, claude)))
        self.assertIn('does not verify a paid plan', codex['detail'])
        self.assertIn('plan marker detected', claude['detail'])
        self.assertNotIn('private-', str(first))
        self.assertEqual(self.run_cli.await_count, 2)

        self.responses['codex'] = (1, 'Not logged in', '')
        first['providers'][0]['auth'] = 'tampered'
        cached = await self.health.status()
        self.assertEqual(cached['providers'][0]['auth'], 'subscription')
        self.assertEqual(self.run_cli.await_count, 2)
        refreshed = await self.health.status(refresh=True)
        self.assertEqual(refreshed['providers'][0]['auth'], 'signed_out')
        self.assertIn('codex login --device-auth', refreshed['providers'][0]['recovery'])
        self.assertEqual(self.run_cli.await_count, 4)
        self.health._checked_at['claude'] -= PH._CACHE_SECONDS
        await self.health.status()
        self.assertEqual(self.run_cli.await_count, 5)
        self.assertEqual(self.turn_workspaces, [])
        self.assertFalse(self.root.exists())

    async def test_both_tests_recheck_auth_use_private_workspaces_and_drop_overrides(self):
        unwanted = {
            'OPENAI_API_KEY': 'secret', 'CODEX_API_KEY': 'secret',
            'CODEX_ACCESS_TOKEN': 'secret', 'OPENAI_BASE_URL': 'https://other.example',
            'ANTHROPIC_API_KEY': 'secret', 'ANTHROPIC_AUTH_TOKEN': 'secret',
            'CLAUDE_CODE_OAUTH_TOKEN': 'secret', 'CLAUDE_CODE_USE_BEDROCK': '1',
            'ANTHROPIC_BASE_URL': 'https://other.example', 'CLAUDE_CODE_SIMPLE': '1',
            'ANTHROPIC_PROFILE': 'other-profile',
            'CLAUDECODE': '1', 'HTTPS_PROXY': 'https://other.example',
        }
        with patch.dict(os.environ, {
            **unwanted, 'CODEX_HOME': '/native/codex', 'CLAUDE_CONFIG_DIR': '/native/claude',
        }):
            for provider in ('codex', 'claude'):
                with self.subTest(provider=provider):
                    row = await self.health.test(provider)
                    self.assertEqual(row['check'], 'working')
                    self.assertEqual(row['auth'], 'subscription')
                    self.assertNotIn(PH._REPLY, str(row))
                    self.assertNotIn('private', str(row))
        self.assertEqual(self.run_cli.await_count, 4)
        self.assertEqual(len(self.turn_workspaces), 2)
        self.assertEqual(list(self.root.iterdir()), [])
        for call in self.run_cli.await_args_list:
            env = call.kwargs['env']
            self.assertTrue(unwanted.keys().isdisjoint(env))
            self.assertEqual(env['CODEX_HOME'], '/native/codex')
            self.assertEqual(env['CLAUDE_CONFIG_DIR'], '/native/claude')
        rows = (await self.health.status())['providers']
        self.assertTrue(all(row['check'] == 'working' for row in rows))
        self.assertEqual(self.run_cli.await_count, 4)

    async def test_non_subscription_and_unrecognized_auth_fail_closed(self):
        cases = [
            ('codex', (0, '', 'API key configured - private-key-mask'), 'api_key'),
            ('codex', (0, '', 'Logged in using an API key'), 'api_key'),
            ('codex', (0, 'Logged in using ChatGPT', 'Logged in using an API key'), 'api_key'),
            ('codex', (0, '', 'Logged in using access token'), 'unknown'),
            ('codex', (1, '', 'Error: private path'), 'unknown'),
            ('codex', (1, '', 'Not logged in'), 'signed_out'),
            ('codex', (1, '', 'Logged in using ChatGPT'), 'unknown'),
            ('claude', claude_status(authMethod='apiKey'), 'api_key'),
            ('claude', claude_status(authMethod='console'), 'api_key'),
            ('claude', claude_status(loggedIn=False), 'signed_out'),
            ('claude', claude_status(authMethod='oauth_token'), 'unknown'),
            ('claude', claude_status(subscriptionType=None), 'unknown'),
            ('claude', claude_status(apiProvider='bedrock'), 'unknown'),
            ('claude', claude_status(loggedIn='true'), 'unknown'),
            ('claude', (0, 'unstructured private response', ''), 'unknown'),
            ('claude', (0, '[]', ''), 'unknown'),
        ]
        for provider, response, auth in cases:
            with self.subTest(provider=provider, response=response):
                self.responses[provider] = response
                row = await self.health.test(provider)
                self.assertEqual(row['auth'], auth)
                self.assertEqual(row['check'], 'failed')
                self.assertNotIn('private', str(row))
                if provider == 'claude':
                    self.assertIn('claude auth login --claudeai', row['recovery'])
        self.assertEqual(self.run_cli.await_count, len(cases))
        self.assertEqual(self.turn_workspaces, [])
        self.assertFalse(self.root.exists())

    async def test_missing_cli_and_probe_failures(self):
        del self.cli['claude']
        self.responses['codex'] = TimeoutError('private auth timeout')
        codex, claude = (await self.health.status())['providers']
        self.assertTrue(codex['installed'])
        self.assertEqual(codex['auth'], 'unknown')
        self.assertIn('timed out', codex['detail'])
        self.assertNotIn('private', str(codex))
        self.assertFalse(claude['installed'])
        self.assertEqual(claude['auth'], 'unavailable')
        self.assertIn('Install', claude['recovery'])
        self.assertEqual((await self.health.test('claude'))['check'], 'failed')
        self.assertEqual(self.run_cli.await_count, 1)
        for error in (OSError('private path'), PH._OutputLimitError()):
            self.responses['codex'] = error
            row = await self.health.test('codex')
            self.assertEqual(row['auth'], 'unknown')
            self.assertEqual(row['check'], 'failed')
            self.assertNotIn('private', str(row))
        with self.assertRaisesRegex(ValueError, '^Unknown provider ID$'):
            await self.health.test('private malicious command')
        self.assertEqual(self.turn_workspaces, [])

    async def test_model_errors_are_redacted_and_temporary_files_are_cleaned(self):
        for provider in ('codex', 'claude'):
            for response, detail in (
                ((1, PH._REPLY, 'private token'), 'did not complete'),
                ((0, 'private model output', ''), 'unexpected test reply'),
                ((0, PH._REPLY + ' ' * 350 + 'extra', ''), 'unexpected test reply'),
                (TimeoutError('private data'), 'timed out'),
                (PH._OutputLimitError(), 'output limit'),
                (OSError('private path'), 'Could not run'),
            ):
                with self.subTest(provider=provider, response=response):
                    self.model_response = response
                    row = await self.health.test(provider)
                    self.assertEqual(row['check'], 'failed')
                    self.assertIn(detail, row['detail'])
                    self.assertNotIn('private token', str(row))
                    self.assertNotIn('private model output', str(row))
                    self.assertNotIn('private path', str(row))
                    self.assertEqual(list(self.root.iterdir()), [])

    async def test_usage_limit_and_http_429_offer_retry_guidance(self):
        private = 'request for private-account@example.test with sk-fake-token at /home/private/config'
        wait = 'Wait before running Test again. Check your account usage if the limit persists.'
        cases = (
            ('claude', (1, "You've hit your 5-hour limit · resets 3:50pm (America/Chicago)\n", private),
             'Run Test again after 3:50 PM (America/Chicago).'),
            ('claude', (1, private, f'HTTP 429: {private}'), wait),
            ('codex', (1, '', f'unexpected status 429 Too Many Requests: {private}'), wait),
            ('codex', (1, '', json.dumps({'error': {'code': 'insufficient_quota', 'message': private}})), wait),
        )
        for provider, response, recovery in cases:
            with self.subTest(provider=provider, response=response):
                self.model_response = response
                row = await self.health.test(provider)
                self.assertEqual((row['auth'], row['check']), ('subscription', 'failed'))
                self.assertIn('usage quota or rate limit blocked the model test', row['detail'])
                self.assertEqual(row['recovery'], recovery)
                self.assertNotIn('private', str(row))
                self.assertNotIn('sk-fake-token', str(row))
                self.assertNotIn("You've hit", str(row))
                self.assertIn(row, (await self.health.status())['providers'])

    async def test_quota_reset_uses_only_complete_typed_notice(self):
        for suffix in (
            '03:50pm (America/Chicago)',
            '13:50pm (America/Chicago)',
            '3:60pm (America/Chicago)',
            '3:50pm (/home/private/session)',
            '3:50pm (America/Chicago) private-key=sk-fake-token',
        ):
            with self.subTest(suffix=suffix):
                self.model_response = (1, f"You've hit your 5-hour limit · resets {suffix}", '')
                row = await self.health.test('claude')
                self.assertEqual(row['check'], 'failed')
                self.assertIn('usage quota or rate limit blocked the model test', row['detail'])
                self.assertEqual(
                    row['recovery'], 'Wait before running Test again. Check your account usage if the limit persists.',
                )
                self.assertNotIn(suffix, str(row))

    async def test_model_authentication_and_temporary_failures_offer_distinct_recovery(self):
        private = 'private-account@example.test sk-fake-token /home/private/cache/429'
        cases = (
            ('claude', (1, f'API Error: 401 {private}', ''), 'native account sign-in', 'claude auth login --claudeai'),
            ('codex', (1, '', f'Access token has expired: {private}'), 'native account sign-in', 'codex login'),
            ('claude', (1, private, f'HTTP 503: {private}'), 'did not complete', 'Check network access'),
            ('codex', (1, '', f'Could not open {private}'), 'did not complete', 'Check network access'),
        )
        for provider, response, detail, recovery in cases:
            with self.subTest(provider=provider, response=response):
                self.model_response = response
                row = await self.health.test(provider)
                self.assertEqual((row['auth'], row['check']), ('subscription', 'failed'))
                self.assertIn(detail, row['detail'])
                self.assertIn(recovery, row['recovery'])
                if recovery == 'Check network access':
                    self.assertNotIn('login', row['recovery'])
                self.assertNotIn('private', str(row))
                self.assertNotIn('sk-fake-token', str(row))

    async def test_fresh_auth_can_block_a_previous_success(self):
        self.assertEqual((await self.health.test('codex'))['check'], 'working')
        self.responses['codex'] = (0, '', 'API key configured - mask')
        row = await self.health.test('codex')
        self.assertEqual((row['auth'], row['check']), ('api_key', 'failed'))
        self.assertEqual(len(self.turn_workspaces), 1)
        self.responses['codex'] = (1, '', 'Not logged in')
        row = (await self.health.status(refresh=True))['providers'][0]
        self.assertEqual((row['auth'], row['check']), ('signed_out', 'untested'))

    async def test_overlapping_test_and_refresh_expose_running_without_second_turn(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(argv, **kwargs):
            result = await self.fake_cli(argv, **kwargs)
            if argv[1] == 'exec':
                started.set()
                await release.wait()
            return result

        self.run_cli.side_effect = blocked
        async with asyncio.TaskGroup() as tasks:
            first = tasks.create_task(self.health.test('codex'))
            await asyncio.wait_for(started.wait(), 1)
            try:
                self.assertEqual((await self.health.test('codex'))['check'], 'running')
                status = await self.health.status(refresh=True)
                self.assertEqual(status['providers'][0]['check'], 'running')
                self.assertEqual(len(self.turn_workspaces), 1)
                self.assertEqual(self.run_cli.await_count, 3)
            finally:
                release.set()
        self.assertEqual(first.result()['check'], 'working')

    async def test_cancellation_clears_running_state_and_workspace(self):
        started = asyncio.Event()

        async def blocked(argv, **kwargs):
            result = await self.fake_cli(argv, **kwargs)
            if argv[-1] == PH._PROMPT:
                started.set()
                await asyncio.Future()
            return result

        self.run_cli.side_effect = blocked
        task = asyncio.create_task(self.health.test('claude'))
        try:
            await asyncio.wait_for(started.wait(), 1)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual((await self.health.status())['providers'][1]['check'], 'untested')


class SubprocessBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_output_and_timeout_are_bounded(self):
        kwargs = {'cwd': ROOT, 'env': PH._native_env()}
        code, stdout, stderr = await PH._run(
            (sys.executable, '-c', 'import sys; print("out"); print("err", file=sys.stderr)'),
            **kwargs, timeout=2,
        )
        self.assertEqual((code, stdout.strip(), stderr.strip()), (0, 'out', 'err'))
        with self.assertRaises(TimeoutError):
            await PH._run((sys.executable, '-c', 'import time; time.sleep(30)'), **kwargs, timeout=0.1)
        with self.assertRaises(PH._OutputLimitError):
            await PH._run(
                (sys.executable, '-c', 'import time; print("x" * 200000, flush=True); time.sleep(30)'),
                **kwargs, timeout=2,
            )


if __name__ == '__main__':
    unittest.main()
