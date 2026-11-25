"""Local, in-memory provider diagnostics for C3.

Pass a private C3 data directory as root. status() returns {"providers": [row, ...]}
in Codex, Claude Code order. Each row has id, name, installed (bool), auth, check,
detail and recovery. Auth is subscription, api_key, signed_out, unknown or
unavailable. Check is untested, running, working or failed. Detail and recovery
contain fixed messages and may include a validated quota reset time.

Auth reflects the diagnostic CLI configuration with environment overrides removed.
Status caches native authentication checks for 30 seconds. refresh=True bypasses
the cache for idle providers. test(id) rechecks auth and runs one small prompt
in an ephemeral workspace when eligible. It returns that provider's updated
row. An overlapping test returns the current running row. An unknown ID raises
ValueError. A failed auth preflight records a skipped test as failed.

Both use the native CLI's credential storage. C3 neither opens credential files
nor signs in, signs out, or resumes a conversation. The CLI can refresh its own
sign-in during a turn. A test uses the account's native usage allowance and is
subject to any extra usage or credits already enabled on that account.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_CACHE_SECONDS = 30
_AUTH_TIMEOUT = 5
_TEST_TIMEOUT = 45
_OUTPUT_LIMIT = 128 * 1024
_REPLY = 'CAMMY_PROVIDER_OK'
_PROMPT = f'Reply with exactly {_REPLY}. Do not use tools.'
_PROVIDERS = {
    'codex': ('Codex', 'Run codex login to sign in with ChatGPT. For a headless host, run codex login --device-auth.'),
    'claude': ('Claude Code', 'Run claude auth login --claudeai to sign in with Claude.'),
}
# Keep only native CLI lookup, home/config and OS credential-store discovery.
# API keys, access tokens, cloud gateways, config injection, proxy overrides and
# parent agent/session flags never reach either subprocess.
_ENV_KEYS = (
    'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL',
    'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'SystemRoot', 'WINDIR',
    'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
    'CODEX_HOME', 'CLAUDE_CONFIG_DIR',
)
_CODEX_BACKEND = 'https://chatgpt.com/backend-api/codex'
_CODEX_CONFIG = (
    '-c', 'model_provider="openai"',
    # Match both invocations to the native keyring with file fallback, even
    # when exec omits the user configuration.
    '-c', 'cli_auth_credentials_store="auto"',
    # An API-key auth change between status and exec must not send a request
    # to the separately billed platform API. Do not force_login_method, which
    # can remove credentials when the login method changes.
    '-c', f'openai_base_url="{_CODEX_BACKEND}"',
)
_CLAUDE_FLAGS = (
    '--safe-mode', '--setting-sources', '',
    '--settings', '{"disableAllHooks":true,"disableClaudeAiConnectors":true,"forceLoginMethod":"claudeai"}',
)
_CLAUDE_PLANS = {'free', 'pro', 'max', 'ultra', 'team', 'enterprise'}
_CLAUDE_API_AUTH = {'apiKey', 'api_key', 'console', 'anthropicConsole', 'apiKeyHelper'}
_USAGE_LIMIT_NOTICE = r"You['’]ve hit your (?:[1-9][0-9]*-hour |daily |weekly |monthly |usage )?limit"
_CLAUDE_RESET = re.compile(
    rf'{_USAGE_LIMIT_NOTICE} · resets ([1-9]|1[0-2]):([0-5][0-9])(am|pm) \(America/Chicago\)',
    re.IGNORECASE | re.ASCII,
)
_HTTP_STATUS = r'\b(?:http(?:/[0-9]+(?:\.[0-9]+)?)?|status(?: code)?|api error)[: \t]+'
_RATE_LIMIT = re.compile(
    rf'{_HTTP_STATUS}429\b|\b(?:{_USAGE_LIMIT_NOTICE}|too many requests|'
    r'rate[-_ ]limit(?:ed|_error|_exceeded)?|usage_limit_reached|insufficient_quota|'
    r'quota (?:exceeded|exhausted)|(?:exceeded|exhausted) your (?:current )?quota)\b',
    re.IGNORECASE | re.ASCII,
)
_AUTH_FAILURE = re.compile(
    rf'{_HTTP_STATUS}(?:401|403)\b|\b(?:authentication(?:_error| (?:failed|required))|'
    r'invalid authentication|unauthorized|not logged in|please run /login|'
    r'(?:access|oauth) token (?:has )?expired|invalid (?:access|oauth) token)\b',
    re.IGNORECASE | re.ASCII,
)


@dataclass(frozen=True)
class _Auth:
    cli: str | None
    kind: str
    detail: str
    recovery: str


class _OutputLimitError(Exception):
    pass


async def _read_output(stream: asyncio.StreamReader) -> str:
    output = bytearray()
    while chunk := await stream.read(8192):
        output.extend(chunk)
        if len(output) > _OUTPUT_LIMIT:
            raise _OutputLimitError
    return output.decode('utf-8', errors='replace')


async def _run(
    argv: tuple[str, ...], *, cwd: Path, env: dict[str, str], timeout: float,
) -> tuple[int, str, str]:
    """Bound time and captured output. Kill the CLI and its local process group on POSIX."""
    grouped = os.name == 'posix'
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, start_new_session=grouped,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    readers = [asyncio.create_task(_read_output(pipe)) for pipe in (process.stdout, process.stderr)]
    try:
        stdout, stderr, returncode = await asyncio.wait_for(
            asyncio.gather(*readers, process.wait()), timeout,
        )
        return returncode, stdout, stderr
    except (TimeoutError, _OutputLimitError, asyncio.CancelledError):
        try:
            if grouped:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # Drain the pipes so asyncio can complete wait() even after the read
        # coroutines have been cancelled with buffered output still pending.
        await process.communicate()
        raise


def _native_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR'):
        if env.get(key):
            env[key] = os.path.abspath(env[key])
    env.update(
        LC_ALL='C',
        NO_COLOR='1',
        DISABLE_AUTOUPDATER='1',
        DISABLE_ERROR_REPORTING='1',
        DISABLE_TELEMETRY='1',
        ENABLE_CLAUDEAI_MCP_SERVERS='false',
    )
    return env


def _auth_kind(provider: str, code: int, stdout: str, stderr: str) -> str:
    if provider == 'codex':
        lines = {line.strip() for line in (stdout + '\n' + stderr).splitlines()}
        api_key = 'Logged in using an API key' in lines or any(
            line.startswith('API key configured - ') for line in lines
        )
        if code == 0 and api_key:
            return 'api_key'
        if code == 0 and 'Logged in using ChatGPT' in lines:
            return 'subscription'
        return 'signed_out' if code == 1 and 'Not logged in' in lines else 'unknown'

    try:
        data = json.loads(stdout)
    except (ValueError, RecursionError):
        return 'unknown'
    if not isinstance(data, dict):
        return 'unknown'
    if code in (0, 1) and data.get('loggedIn') is False:
        return 'signed_out'
    if code != 0 or data.get('loggedIn') is not True:
        return 'unknown'
    method = data.get('authMethod')
    if isinstance(method, str) and method in _CLAUDE_API_AUTH:
        return 'api_key'
    plan = data.get('subscriptionType')
    if (method == 'claude.ai' and data.get('apiProvider') == 'firstParty'
            and isinstance(plan, str) and plan.lower() in _CLAUDE_PLANS):
        return 'subscription'
    return 'unknown'


def _cli_failure(provider: str, stdout: str, stderr: str, retry: str) -> tuple[str, str, str]:
    # Keep CLI output private. Only the clock fields in a complete reset notice
    # can reach the row, and the time zone in the message is fixed.
    output = stdout + '\n' + stderr
    if _RATE_LIMIT.search(output):
        detail = 'The provider’s usage quota or rate limit blocked the model test.'
        if provider == 'claude':
            for line in output.splitlines():
                reset = _CLAUDE_RESET.fullmatch(line)
                if reset:
                    hour, minute, period = reset.groups()
                    reset_time = f'{hour}:{minute} {period.upper()} (America/Chicago)'
                    return 'failed', detail, f'Run Test again after {reset_time}.'
        return 'failed', detail, 'Wait before running Test again. Check your account usage if the limit persists.'
    if _AUTH_FAILURE.search(output):
        return 'failed', 'The provider rejected the native account sign-in or access.', _PROVIDERS[provider][1]
    return 'failed', 'The CLI did not complete the model test.', retry


def _test_args(provider: str, cli: str, workspace: Path) -> tuple[str, ...]:
    if provider == 'codex':
        return (
            cli, 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules', '--strict-config',
            '--skip-git-repo-check', '--sandbox', 'read-only', '--color', 'never',
            '-m', 'gpt-6-luna', *_CODEX_CONFIG,
            '-c', 'approval_policy="never"',
            '-c', 'project_root_markers=[]', '-c', 'project_doc_max_bytes=0',
            '-c', 'mcp_servers={}', '-c', 'web_search="disabled"',
            '-c', 'history.persistence="none"',
            '-c', 'model_reasoning_effort="low"',
            '-c', f'log_dir={json.dumps(str(workspace))}',
            '--disable', 'shell_tool', '--disable', 'unified_exec', '--disable', 'hooks',
            '--disable', 'plugins', '--disable', 'apps', '--disable', 'memories', '--disable', 'skill_search',
            '--enable', 'skip_host_skill_discovery',
            '--output-last-message', str(workspace / 'reply.txt'), _PROMPT,
        )
    return (
        cli, *_CLAUDE_FLAGS, '--print', '--model', 'haiku', '--effort', 'low',
        '--output-format', 'text', '--max-turns', '1', '--no-session-persistence',
        '--tools', '', '--permission-mode', 'dontAsk',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
        '--disable-slash-commands', '--no-chrome', '--system-prompt', 'You are a connectivity check.',
        _PROMPT,
    )


class ProviderHealth:
    """Project-independent status and explicitly requested provider checks."""

    def __init__(self, root: Path):
        self._root = root.resolve()
        self._auth: dict[str, _Auth] = {}
        self._checked_at: dict[str, float] = {}
        self._locks = {provider: asyncio.Lock() for provider in _PROVIDERS}
        self._running: set[str] = set()
        self._results: dict[str, tuple[str, str, str]] = {}

    async def _probe(self, provider: str, env: dict[str, str]) -> _Auth:
        name, login = _PROVIDERS[provider]
        cli = shutil.which(provider, path=env.get('PATH'))
        if cli is None:
            return _Auth(None, 'unavailable', f'{name} CLI is missing.', f'Install {name} and make it available on C3’s PATH. {login}')
        cli = os.path.abspath(cli)
        if provider == 'codex':
            args = (cli, 'login', 'status', *_CODEX_CONFIG)
        else:
            args = (cli, *_CLAUDE_FLAGS, 'auth', 'status', '--json')
        retry = f'Check or update the {name} CLI, then refresh. {login}'
        try:
            code, stdout, stderr = await _run(args, cwd=Path(self._root.anchor), env=env, timeout=_AUTH_TIMEOUT)
        except TimeoutError:
            return _Auth(cli, 'unknown', 'The native sign-in check timed out.', retry)
        except (OSError, _OutputLimitError):
            return _Auth(cli, 'unknown', 'The native sign-in check could not run reliably.', retry)
        kind = _auth_kind(provider, code, stdout, stderr)
        if kind == 'subscription':
            if provider == 'codex':
                detail = 'ChatGPT sign-in detected. Codex login status does not verify a paid plan or usage limits.'
            else:
                detail = 'Claude sign-in and plan marker detected. Local status does not verify live entitlement or usage limits.'
            return _Auth(
                cli, kind, detail,
                'Run Test to check model access. The test can use account allowance or enabled extra credits.',
            )
        if kind == 'signed_out':
            return _Auth(cli, kind, 'No native sign-in was detected.', login)
        if kind == 'api_key':
            return _Auth(cli, kind, 'API billing credentials detected. Model tests require native account sign-in.', login)
        return _Auth(cli, kind, 'The CLI did not confirm a supported sign-in method and plan.', retry)

    async def _refresh(self, provider: str, refresh: bool, env: dict[str, str]) -> _Auth:
        async with self._locks[provider]:
            previous = self._auth.get(provider)
            if previous is not None and not refresh and time.monotonic() - self._checked_at[provider] < _CACHE_SECONDS:
                return previous
            auth = await self._probe(provider, env)
            if auth != previous:
                self._results.pop(provider, None)
            self._auth[provider] = auth
            self._checked_at[provider] = time.monotonic()
            return auth

    def _row(self, provider: str) -> dict:
        auth = self._auth.get(provider)
        if auth is None:
            auth = _Auth(shutil.which(provider), 'unknown', 'Checking native sign-in.', '')
        check, detail, recovery = self._results.get(provider, ('untested', '', ''))
        if provider in self._running:
            check, detail, recovery = 'running', 'Provider test in progress.', 'Wait for the test to finish.'
        return {
            'id': provider, 'name': _PROVIDERS[provider][0], 'installed': auth.cli is not None,
            'auth': auth.kind, 'check': check,
            'detail': ' '.join(part for part in (auth.detail, detail) if part),
            'recovery': recovery or auth.recovery,
        }

    async def status(self, refresh: bool = False) -> dict:
        env = _native_env()
        await asyncio.gather(*(
            self._refresh(provider, refresh, env)
            for provider in _PROVIDERS if provider not in self._running
        ))
        return {'providers': [self._row(provider) for provider in _PROVIDERS]}

    async def test(self, provider_id: str) -> dict:
        if provider_id not in _PROVIDERS:
            raise ValueError('Unknown provider ID')
        if provider_id in self._running:
            return self._row(provider_id)
        self._running.add(provider_id)
        self._results.pop(provider_id, None)
        try:
            env = _native_env()
            auth = await self._refresh(provider_id, True, env)
            if auth.cli is not None and auth.kind == 'subscription':
                result = await self._test_turn(provider_id, auth.cli, env)
            else:
                result = ('failed', 'Model test skipped.', auth.recovery)
            self._results[provider_id] = result
        finally:
            self._running.remove(provider_id)
        return self._row(provider_id)

    async def _test_turn(self, provider: str, cli: str, env: dict[str, str]) -> tuple[str, str, str]:
        name, _ = _PROVIDERS[provider]
        retry = f'Check network access and {name} availability, then run Test again.'
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='provider-check-', dir=self._root) as folder:
                workspace = Path(folder)
                env = dict(env, TMPDIR=folder, TEMP=folder, TMP=folder)
                if provider == 'codex':
                    env['CODEX_SQLITE_HOME'] = folder
                else:
                    # An expired Claude sign-in can otherwise fall back to an
                    # Anthropic Platform profile billed to its organization.
                    env['ANTHROPIC_CONFIG_DIR'] = str(workspace / 'anthropic')
                argv = _test_args(provider, cli, workspace)
                code, stdout, stderr = await _run(argv, cwd=workspace, env=env, timeout=_TEST_TIMEOUT)
                if code != 0:
                    return _cli_failure(provider, stdout, stderr, retry)
                if provider == 'codex':
                    with (workspace / 'reply.txt').open('rb') as reply:
                        stdout = reply.read(257).decode('utf-8', errors='replace')
                if len(stdout) <= 256 and stdout.strip() == _REPLY:
                    return 'working', 'The model returned the expected test reply.', ''
                return 'failed', 'The model returned an unexpected test reply.', retry
        except TimeoutError:
            return 'failed', 'The model test timed out.', retry
        except _OutputLimitError:
            return 'failed', 'The model test exceeded its output limit.', retry
        except OSError:
            return 'failed', 'Could not run the CLI or read its test reply in the private workspace.', retry
