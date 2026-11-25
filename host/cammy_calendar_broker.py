"""Executor Cloud Calendar broker: per-profile scoped OAuth and fixed read-only calendar reads.

Runs as its own process (`python3 host/cammy_calendar_broker.py`), never inside C3 or a
resident provider. It is the only holder of Executor credentials. C3 reaches it over a private
Unix socket with fixed operations and never receives a token:

  health | status {profile} | connect {profile, connection} | cancel_connect {profile}
  test {profile} | disconnect {profile} | events {profile, range: today|tomorrow|next}

Connections are Executor *scoped connections* made on the Executor dashboard (Google Calendar
app, the chosen account, read-only tools). Each profile signs in through the MCP OAuth flow at
https://mcp.executor.sh/mcp?connection=<id>. Executor refuses personal access tokens at a
connection URL, so any token that works there is limited to that connection. There is no
plain /mcp sign-in, no personal access token and no shared login: every profile has its own
client registration and tokens under its own private directory.

Network: HTTPS to executor.sh hosts only, no proxies, no redirects, bounded bodies. Errors
are fixed codes and messages; nothing a server sends is echoed. The only MCP tool this
broker calls is Executor's `call`, and only with the fixed calendar tool below.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.server
import json
import logging
import os
import re
import secrets
import socket
import socketserver
import ssl
import stat
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger('cammy.calendar_broker')

VERSION = 1
EXECUTOR_MCP = 'https://mcp.executor.sh/mcp'
CALENDAR_TOOL = 'tools["google-calendar"].api.calendars.calendarEventsList'
CALENDAR_TOOL_PATHS = (CALENDAR_TOOL, 'tools.google.api.calendar.calendars.calendarEventsList')
CALENDAR_ID = 'primary'
RANGES = ('today', 'tomorrow', 'next')
NEXT_WINDOW = timedelta(days=7)
MAX_EVENTS = 20
FLOW_SECONDS = 300
HTTP_TIMEOUT = 20
MAX_BODY = 1 << 20
MAX_REQUEST = 4096
PROTOCOL_VERSION = '2025-06-18'
_PROFILE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_CONNECTION = re.compile(r'[A-Za-z0-9_-]{1,128}')
_CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f  ‪-‮⁦-⁩]+')

MESSAGES = {
    'bad_request': 'The broker rejected a malformed request.',
    'invalid_profile': 'The profile ID is not valid.',
    'invalid_connection': 'Enter the connection ID from the Executor dashboard (letters, digits, - and _).',
    'not_connected': 'This profile has no Executor calendar connection.',
    'connecting': 'A sign-in for this profile is already waiting in the browser.',
    'reconnect_required': 'The Executor sign-in expired or was revoked. Connect again.',
    'destination_refused': 'Executor sent an address outside executor.sh; nothing was sent there.',
    'redirect_refused': 'Executor answered with a redirect; the broker does not follow redirects.',
    'discovery_failed': 'Executor sign-in details could not be read. Try again later.',
    'registration_failed': 'Executor did not accept the broker as a sign-in client.',
    'pkce_unsupported': 'Executor did not offer PKCE (S256); the broker will not sign in without it.',
    'sign_in_failed': 'The Executor sign-in did not complete. Connect again.',
    'sign_in_expired': 'The browser sign-in took too long. Connect again.',
    'network': 'Executor could not be reached.',
    'unauthorized': 'Executor refused the stored sign-in. Connect again.',
    'forbidden': 'Executor refused this connection (membership or approval changed).',
    'server_error': 'Executor reported an error.',
    'protocol': 'Executor answered in an unexpected form.',
    'call_unsupported': 'Executor’s call tool has an unrecognised form; calendar reads stay off.',
    'tool_missing': 'The scoped connection does not expose the read-only calendar tool.',
    'account_ambiguous': 'Scope this connection to one Google Calendar account and one event-list tool.',
    'tool_error': 'The calendar tool reported an error.',
    'approval_required': 'Executor asked for an approval; read-only calendar tools must not need one.',
    'changed': 'The connection changed while the request ran; nothing was returned.',
    'storage': 'The broker could not use its private storage.',
    'busy': 'Another calendar request for this profile is running.',
    'invalid_range': 'Ask for today, tomorrow, or your next event.',
}


class BrokerError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code if code in MESSAGES else 'protocol'

    def reply(self) -> dict:
        return {'ok': False, 'code': self.code, 'message': MESSAGES[self.code]}


# ---- destinations and HTTP -----------------------------------------------------------------

def check_url(url: str) -> str:
    """Only https://executor.sh and its subdomains on the default port, without credentials."""
    if not isinstance(url, str) or len(url) > 2048 or _CONTROL.search(url):
        raise BrokerError('destination_refused')
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise BrokerError('destination_refused') from None
    host = (parts.hostname or '').lower()
    if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
            or port not in (None, 443) or parts.fragment
            or not (host == 'executor.sh' or host.endswith('.executor.sh'))
            or '..' in host or not re.fullmatch(r'[a-z0-9.-]+', host)):
        raise BrokerError('destination_refused')
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None    # urllib then raises HTTPError with the 3xx status


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class Https:
    """Strict HTTPS: no environment proxies, no redirects, verified TLS, bounded bodies."""

    def __init__(self):
        # Assembled by hand: no proxy, http, ftp, file or data handlers at all.
        opener = urllib.request.OpenerDirector()
        for handler in (urllib.request.UnknownHandler(), urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                        urllib.request.HTTPDefaultErrorHandler(), _NoRedirect(), urllib.request.HTTPErrorProcessor()):
            opener.add_handler(handler)
        self._opener = opener

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None) -> Response:
        check_url(url)
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=HTTP_TIMEOUT) as r:
                return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, _read(r))
        except urllib.error.HTTPError as exc:
            try:
                data = _read(exc)
            except OSError:
                data = b''
            return Response(exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, data)
        except (urllib.error.URLError, OSError, ValueError):
            raise BrokerError('network') from None


def _read(stream) -> bytes:
    data = stream.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise BrokerError('protocol')
    return data


def _json_body(response: Response) -> dict:
    try:
        value = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        raise BrokerError('protocol') from None
    if not isinstance(value, dict):
        raise BrokerError('protocol')
    return value


def _status_error(status: int) -> BrokerError:
    if 300 <= status < 400:
        return BrokerError('redirect_refused')
    if status == 401:
        return BrokerError('unauthorized')
    if status == 403:
        return BrokerError('forbidden')
    if status >= 500:
        return BrokerError('server_error')
    return BrokerError('protocol')


# ---- private storage -----------------------------------------------------------------------

def _private_dir(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise BrokerError('storage')
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)


class Store:
    """profiles/<profile>/connection.json, 0600 in 0700 directories, atomic writes."""

    def __init__(self, root: Path, forbidden: list[Path]):
        root = Path(root)
        if not root.is_absolute():
            raise ValueError('broker state directory must be absolute')
        resolved = root.parent.resolve() / root.name
        for blocked in forbidden:
            blocked = Path(blocked).resolve()
            if resolved == blocked or blocked in resolved.parents or resolved in blocked.parents:
                raise ValueError('broker state directory must be outside C3 and resident workspaces')
        self.root = resolved
        _private_dir(self.root)
        _private_dir(self.root / 'profiles')

    def _dir(self, profile: str) -> Path:
        return self.root / 'profiles' / profile

    def load(self, profile: str) -> dict | None:
        path = self._dir(profile) / 'connection.json'
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError:
            raise BrokerError('storage') from None
        try:
            with os.fdopen(fd, 'rb') as f:
                value = json.loads(f.read(MAX_BODY))
        except (OSError, ValueError):
            raise BrokerError('storage') from None
        if not isinstance(value, dict) or value.get('profile') != profile:
            raise BrokerError('storage')
        return value

    def save(self, profile: str, value: dict) -> None:
        try:
            folder = self._dir(profile)
            _private_dir(folder)
            data = json.dumps({**value, 'profile': profile}, sort_keys=True).encode()
            tmp = folder / ('.connection.%s.tmp' % secrets.token_hex(6))
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, folder / 'connection.json')
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            _fsync_dir(folder)
        except OSError:
            raise BrokerError('storage') from None

    def delete(self, profile: str) -> None:
        try:
            (self._dir(profile) / 'connection.json').unlink(missing_ok=True)
            if self._dir(profile).exists():
                _fsync_dir(self._dir(profile))
        except OSError:
            raise BrokerError('storage') from None


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---- OAuth (MCP authorization: RFC 9728, 8414, 7591, 7636, 8707) ---------------------------

def connection_url(connection: str) -> str:
    if not isinstance(connection, str) or not _CONNECTION.fullmatch(connection):
        raise BrokerError('invalid_connection')
    return EXECUTOR_MCP + '?' + urllib.parse.urlencode({'connection': connection})


def _well_known(url: str, name: str) -> list[str]:
    parts = urllib.parse.urlsplit(check_url(url))
    path = parts.path.rstrip('/')
    base = 'https://%s/.well-known/%s' % (parts.netloc, name)
    return [base + path, base] if path else [base]


def _endpoint(meta: dict, key: str, required: bool = True) -> str | None:
    value = meta.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise BrokerError('discovery_failed')
    return check_url(value)


class OAuth:
    def __init__(self, http):
        self.http = http

    def _get_json(self, urls: list[str]) -> dict:
        last = BrokerError('discovery_failed')
        for url in urls:
            response = self.http('GET', check_url(url), {'Accept': 'application/json'}, None)
            if response.status == 200:
                return _json_body(response)
            last = _status_error(response.status) if 300 <= response.status < 400 else BrokerError('discovery_failed')
            if response.status != 404:
                break
        raise last

    def discover(self, resource: str) -> dict:
        prm = self._get_json(_well_known(EXECUTOR_MCP, 'oauth-protected-resource'))
        if prm.get('resource') not in (EXECUTOR_MCP, resource):
            raise BrokerError('discovery_failed')
        servers = prm.get('authorization_servers')
        if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
            raise BrokerError('discovery_failed')
        issuer = check_url(servers[0])
        meta = None
        for name in ('oauth-authorization-server', 'openid-configuration'):
            try:
                meta = self._get_json(_well_known(issuer, name))
                break
            except BrokerError as exc:
                if exc.code != 'discovery_failed':
                    raise
        if meta is None or meta.get('issuer') != issuer:
            raise BrokerError('discovery_failed')
        methods = meta.get('code_challenge_methods_supported')
        if not isinstance(methods, list) or 'S256' not in methods:
            raise BrokerError('pkce_unsupported')
        scopes = prm.get('scopes_supported')
        scope = ' '.join(s for s in scopes if isinstance(s, str) and re.fullmatch(r'[\x21\x23-\x5b\x5d-\x7e]{1,64}', s)) \
            if isinstance(scopes, list) else ''
        return {
            'issuer': issuer,
            'authorization_endpoint': _endpoint(meta, 'authorization_endpoint'),
            'token_endpoint': _endpoint(meta, 'token_endpoint'),
            'registration_endpoint': _endpoint(meta, 'registration_endpoint'),
            'revocation_endpoint': _endpoint(meta, 'revocation_endpoint', required=False),
            'iss_parameter': meta.get('authorization_response_iss_parameter_supported') is True,
            'scope': scope,
        }

    def register(self, meta: dict, redirect_uri: str) -> dict:
        body = json.dumps({
            'client_name': 'Cammy Control Center calendar (read-only)',
            'redirect_uris': [redirect_uri],
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'],
            'token_endpoint_auth_method': 'none',
        }).encode()
        response = self.http('POST', meta['registration_endpoint'],
                             {'Content-Type': 'application/json', 'Accept': 'application/json'}, body)
        if response.status not in (200, 201):
            raise _status_error(response.status) if 300 <= response.status < 400 else BrokerError('registration_failed')
        value = _json_body(response)
        client_id = value.get('client_id')
        secret = value.get('client_secret')
        if not isinstance(client_id, str) or not 0 < len(client_id) <= 512:
            raise BrokerError('registration_failed')
        if secret is not None and (not isinstance(secret, str) or len(secret) > 1024):
            raise BrokerError('registration_failed')
        return {'client_id': client_id, 'client_secret': secret}

    def token(self, saved: dict, form: dict) -> dict:
        form = {**form, 'client_id': saved['client_id'], 'resource': saved['resource']}
        if saved.get('client_secret'):
            form['client_secret'] = saved['client_secret']
        response = self.http('POST', check_url(saved['token_endpoint']),
                             {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
                             urllib.parse.urlencode(form).encode())
        if response.status != 200:
            if 300 <= response.status < 400:
                raise BrokerError('redirect_refused')
            raise BrokerError('reconnect_required' if response.status in (400, 401) else 'server_error')
        value = _json_body(response)
        access = value.get('access_token')
        kind = value.get('token_type')
        if (not isinstance(access, str) or not 0 < len(access) <= 8192
                or not isinstance(kind, str) or kind.lower() != 'bearer'):
            raise BrokerError('protocol')
        expires = value.get('expires_in')
        expires = int(expires) if isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires > 0 else 3600
        refresh = value.get('refresh_token', saved.get('refresh_token'))
        if refresh is not None and (not isinstance(refresh, str) or len(refresh) > 8192):
            raise BrokerError('protocol')
        return {'access_token': access, 'refresh_token': refresh,
                'expires_at': time.time() + min(expires, 30 * 86400)}

    def revoke(self, saved: dict) -> bool:
        url = saved.get('revocation_endpoint')
        token = saved.get('refresh_token') or saved.get('access_token')
        if not url or not token:
            return False
        form = {'token': token, 'client_id': saved['client_id']}
        if saved.get('client_secret'):
            form['client_secret'] = saved['client_secret']
        try:
            response = self.http('POST', check_url(url), {'Content-Type': 'application/x-www-form-urlencoded'},
                                 urllib.parse.urlencode(form).encode())
        except BrokerError:
            return False
        return response.status == 200


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


class _Flow:
    """One browser sign-in waiting on a loopback redirect for one profile."""

    def __init__(self, broker: 'Broker', profile: str, connection: str, meta: dict):
        self.broker, self.profile, self.connection, self.meta = broker, profile, connection, meta
        self.state = secrets.token_urlsafe(32)
        self.verifier, challenge = _pkce()
        self.deadline = time.monotonic() + FLOW_SECONDS
        self.done = threading.Event()
        flow = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                flow._callback(self)

            def log_message(self, *args):
                pass

        self.generation = broker.generation(profile)
        self.server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.server.timeout = 1
        self.redirect_uri = 'http://127.0.0.1:%d/callback' % self.server.server_address[1]
        try:
            self.client = broker.oauth.register(meta, self.redirect_uri)
        except BaseException:
            self.server.server_close()
            raise
        query = {
            'response_type': 'code', 'client_id': self.client['client_id'], 'redirect_uri': self.redirect_uri,
            'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': self.state,
            'resource': connection_url(connection),
        }
        if meta['scope']:
            query['scope'] = meta['scope']
        endpoint = meta['authorization_endpoint']
        self.authorize_url = check_url(endpoint + ('&' if '?' in endpoint else '?') + urllib.parse.urlencode(query))
        self.thread = threading.Thread(target=self._serve, name='calendar-signin', daemon=True)

    def start(self):
        self.thread.start()

    def _serve(self):
        try:
            while not self.done.is_set() and time.monotonic() < self.deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()
            self.broker._flow_ended(self)

    def cancel(self):
        self.done.set()

    def _callback(self, request):
        parts = urllib.parse.urlsplit(request.path)
        params = urllib.parse.parse_qs(parts.query, max_num_fields=8)
        one = lambda key: (params.get(key) or [None])[0]  # noqa: E731
        if parts.path != '/callback' or self.done.is_set():
            request.send_error(404)
            return
        state, code, iss = one('state'), one('code'), one('iss')
        ok = (isinstance(state, str) and hmac.compare_digest(state, self.state) and isinstance(code, str)
              and 0 < len(code) <= 4096 and (iss == self.meta['issuer'] if (iss or self.meta['iss_parameter']) else True))
        if not (isinstance(state, str) and hmac.compare_digest(state, self.state)):
            request.send_error(400)      # a stray request; keep waiting for the real one
            return
        self.done.set()
        result = 'sign_in_failed'
        if ok:
            try:
                self.broker._finish_sign_in(self, code)
                result = None
            except BrokerError as exc:
                result = exc.code
        else:
            self.broker._record_failure(self.profile, 'sign_in_failed')
        page = (b'<!doctype html><meta charset="utf-8"><title>Cammy calendar</title><p>'
                + (b'Calendar connected. You can close this tab.' if result is None
                   else b'The calendar sign-in did not complete. Return to Cammy Control Center.'))
        request.send_response(200)
        request.send_header('Content-Type', 'text/html; charset=utf-8')
        request.send_header('Cache-Control', 'no-store')
        request.send_header('Referrer-Policy', 'no-referrer')
        request.send_header('Content-Length', str(len(page)))
        request.end_headers()
        request.wfile.write(page)


# ---- MCP client ------------------------------------------------------------------------------

class Mcp:
    """Streamable HTTP MCP at one connection URL: initialize, tools/list, tools/call(call)."""

    def __init__(self, http, url: str, token: str):
        self.http, self.url, self.token = http, check_url(url), token
        self.session = None
        self.ids = 0

    def _post(self, payload: dict, notify=False):
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': PROTOCOL_VERSION}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        response = self.http('POST', self.url, headers, json.dumps(payload).encode())
        if response.status not in (200, 202):
            raise _status_error(response.status)
        session = response.headers.get('mcp-session-id')
        if session and self.session is None:
            if not re.fullmatch(r'[\x21-\x7e]{1,256}', session):
                raise BrokerError('protocol')
            self.session = session
        if notify:
            return None
        return self._message(response, payload['id'])

    @staticmethod
    def _message(response: Response, id: int) -> dict:
        kind = response.headers.get('content-type', '').split(';')[0].strip().lower()
        candidates = []
        if kind == 'text/event-stream':
            data = []
            for line in response.body.decode('utf-8', 'replace').splitlines() + ['']:
                if line.startswith('data:'):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    candidates.append('\n'.join(data))
                    data = []
        else:
            candidates.append(response.body)
        for raw in candidates:
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(message, dict) and message.get('id') == id:
                if 'error' in message or not isinstance(message.get('result'), dict):
                    raise BrokerError('protocol')
                return message['result']
        raise BrokerError('protocol')

    def request(self, method: str, params: dict) -> dict:
        self.ids += 1
        return self._post({'jsonrpc': '2.0', 'id': self.ids, 'method': method, 'params': params})

    def open(self) -> None:
        self.request('initialize', {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {},
                                    'clientInfo': {'name': 'cammy-calendar-broker', 'version': str(VERSION)}})
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, notify=True)

    def calendar_tool(self) -> tuple[str, str | None]:
        result = self.request('tools/list', {})
        tools = result.get('tools')
        if not isinstance(tools, list):
            raise BrokerError('protocol')
        for tool in tools:
            if isinstance(tool, dict) and tool.get('name') == 'call':
                props = (tool.get('inputSchema') or {}).get('properties')
                if isinstance(props, dict) and 'calls' in props:
                    break
                raise BrokerError('call_unsupported')
        else:
            raise BrokerError('call_unsupported')
        found = self.tool('search', {'query': 'calendarEventsList', 'limit': 20})
        items = found.get('items') if isinstance(found, dict) else None
        if not isinstance(items, list):
            raise BrokerError('protocol')
        matches = [row for row in items if isinstance(row, dict) and row.get('path') in CALENDAR_TOOL_PATHS]
        if not matches:
            raise BrokerError('tool_missing')
        if len(matches) != 1:
            raise BrokerError('account_ambiguous')
        row, = matches
        signature = row.get('input')
        if not isinstance(signature, str):
            raise BrokerError('protocol')
        accounts = re.search(r'\baccountId\??\s*:\s*([^,}\n]+)', signature)
        if not accounts:
            return row['path'], None
        ids = re.fullmatch(r'\s*["\'](acc_[a-zA-Z0-9_-]{1,80})["\']\s*', accounts[1])
        if not ids:
            raise BrokerError('account_ambiguous')
        return row['path'], ids[1]

    def call_calendar(self, path: str, args: dict) -> object:
        if path not in CALENDAR_TOOL_PATHS:
            raise BrokerError('tool_missing')
        return self.tool('call', {'calls': [{'path': path, 'input': args}]})

    def tool(self, name: str, arguments: dict) -> object:
        result = self.request('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError') is True:
            text = json.dumps(result.get('content'))[:2000].lower()
            if 'not found' in text or 'unknown tool' in text or 'no such' in text:
                raise BrokerError('tool_missing')
            if 'approv' in text or 'elicit' in text or 'resume' in text:
                raise BrokerError('approval_required')
            raise BrokerError('tool_error')
        if isinstance(result.get('structuredContent'), (dict, list)):
            return result['structuredContent']
        for item in result.get('content') or []:
            if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str):
                try:
                    return json.loads(item['text'])
                except ValueError:
                    continue
        raise BrokerError('protocol')


# ---- calendar ranges and event masking -----------------------------------------------------

def local_range(name: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Bounded window in the host's local timezone (DST-correct midnights)."""
    if name not in RANGES:
        raise BrokerError('invalid_range')
    now = (now or datetime.now()).astimezone()
    today = now.date()
    if name == 'next':
        return now, now + NEXT_WINDOW
    day = today if name == 'today' else today + timedelta(days=1)
    start = datetime(day.year, day.month, day.day).astimezone()
    after = day + timedelta(days=1)
    return start, datetime(after.year, after.month, after.day).astimezone()


def local_zone() -> str | None:
    try:
        target = os.readlink('/etc/localtime')
    except OSError:
        return None
    match = re.search(r'zoneinfo/([A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*)$', target)
    return match.group(1) if match else None


def _items(value, depth=0):
    if isinstance(value, list) and len(value) == 1 and depth < 4:
        return _items(value[0], depth + 1)
    if isinstance(value, dict):
        if value.get('ok') is False:
            raise BrokerError('tool_error')
        if isinstance(value.get('items'), list):
            return value['items']
        if depth < 4:
            for key in ('result', 'results', 'data', 'body', 'response', 'value'):
                if key in value:
                    found = _items(value[key], depth + 1)
                    if found is not None:
                        return found
    return None


def _clean(text, limit=120) -> str:
    text = _CONTROL.sub(' ', text) if isinstance(text, str) else ''
    text = ' '.join(text.split())
    return text[:limit - 1] + '…' if len(text) > limit else text


def _when(value) -> tuple[str | None, bool]:
    if not isinstance(value, dict):
        return None, False
    if isinstance(value.get('dateTime'), str):
        try:
            return datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00')).astimezone().isoformat(), False
        except ValueError:
            return None, False
    if isinstance(value.get('date'), str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value['date']):
        return value['date'], True
    return None, False


def mask_events(raw, start: datetime, end: datetime, limit: int, upcoming: bool = False) -> tuple[list[dict], bool]:
    """Title and times only; private/confidential events become 'Private event'. No
    descriptions, attendees, links, locations or IDs leave the broker."""
    items = _items(raw)
    if items is None:
        raise BrokerError('protocol')
    events = []
    for item in items:
        if not isinstance(item, dict) or item.get('status') == 'cancelled':
            continue
        begin, all_day = _when(item.get('start'))
        finish, _ = _when(item.get('end'))
        if begin is None:
            continue
        if not all_day and datetime.fromisoformat(begin) >= end:
            continue
        if upcoming and (all_day or datetime.fromisoformat(begin) < start):
            continue     # 'next' means the next event that has not started yet
        private = item.get('visibility') in ('private', 'confidential')
        events.append({'title': 'Private event' if private else (_clean(item.get('summary')) or '(No title)'),
                       'start': begin, 'end': finish, 'all_day': all_day, 'private': private})
    events.sort(key=lambda e: (e['start'][:10], not e['all_day'], e['start']))
    return events[:limit], len(events) > limit


# ---- broker --------------------------------------------------------------------------------

class Broker:
    def __init__(self, store: Store, http=None, now=None):
        self.store = store
        self.http = http if http is not None else Https()
        self.oauth = OAuth(self.http)
        self.now = now or datetime.now
        self._lock = threading.Lock()
        self._state = threading.Lock()     # credential writes vs disconnect, with generations
        self._busy: set[str] = set()
        self._flows: dict[str, _Flow] = {}
        self._generation: dict[str, int] = {}
        self._failures: dict[str, str] = {}

    # Fixed operations, each with exactly these fields.
    OPS = {'health': set(), 'status': {'profile'}, 'connect': {'profile', 'connection'},
           'cancel_connect': {'profile'}, 'test': {'profile'}, 'disconnect': {'profile'},
           'events': {'profile', 'range'}}

    def handle(self, request) -> dict:
        try:
            if not isinstance(request, dict) or request.get('op') not in self.OPS:
                raise BrokerError('bad_request')
            op = request['op']
            if set(request) - {'op'} != self.OPS[op]:
                raise BrokerError('bad_request')
            if op == 'health':
                return {'ok': True, 'version': VERSION}
            profile = request['profile']
            if not isinstance(profile, str) or len(profile) > 64 or not _PROFILE.fullmatch(profile):
                raise BrokerError('invalid_profile')
            if op == 'status':
                return {'ok': True, **self.status(profile)}
            if op == 'connect':
                return {'ok': True, **self.connect(profile, request['connection'])}
            if op == 'cancel_connect':
                return {'ok': True, **self.cancel_connect(profile)}
            if op == 'disconnect':
                return {'ok': True, **self.disconnect(profile)}
            with self._exclusive(profile):
                if op == 'test':
                    return {'ok': True, **self.test(profile)}
                if not isinstance(request['range'], str):
                    raise BrokerError('invalid_range')
                return {'ok': True, **self.events(profile, request['range'])}
        except BrokerError as exc:
            return exc.reply()
        except Exception:          # noqa: BLE001 - never echo internals
            log.exception('calendar broker request failed')
            return BrokerError('protocol').reply()

    class _Exclusive:
        def __init__(self, broker, profile):
            self.broker, self.profile = broker, profile

        def __enter__(self):
            with self.broker._lock:
                if self.profile in self.broker._busy:
                    raise BrokerError('busy')
                self.broker._busy.add(self.profile)

        def __exit__(self, *exc):
            with self.broker._lock:
                self.broker._busy.discard(self.profile)

    def _exclusive(self, profile):
        return self._Exclusive(self, profile)

    def _bump(self, profile):
        with self._lock:
            self._generation[profile] = self._generation.get(profile, 0) + 1

    def generation(self, profile) -> int:
        with self._lock:
            return self._generation.get(profile, 0)

    def _save_if(self, profile: str, generation: int, value: dict, bump: bool = False) -> None:
        """Write credentials only if no connect/disconnect happened since `generation`."""
        with self._state:
            if self.generation(profile) != generation:
                raise BrokerError('changed')
            self.store.save(profile, value)
            if bump:
                self._bump(profile)

    def status(self, profile: str) -> dict:
        saved = self.store.load(profile)
        with self._lock:
            flow = self._flows.get(profile)
            failure = self._failures.get(profile)
        if flow is not None:
            state = 'connecting'
        elif saved is None:
            state = 'disconnected'
        elif saved.get('reconnect_required') or not saved.get('access_token'):
            state = 'reconnect_required'
        else:
            state = 'connected'
        result = {'profile': profile, 'state': state, 'generation': self.generation(profile),
                  'connection': (flow.connection if flow else (saved or {}).get('connection')),
                  'last_test': (saved or {}).get('last_test'), 'last_error': failure}
        if failure:
            result['last_error_message'] = MESSAGES[failure]
        return result

    def connect(self, profile: str, connection: str) -> dict:
        url = connection_url(connection)
        with self._lock:
            if profile in self._flows:
                raise BrokerError('connecting')
            self._failures.pop(profile, None)
        meta = self.oauth.discover(url)
        flow = _Flow(self, profile, connection, meta)
        with self._lock:
            if profile in self._flows:
                flow.server.server_close()
                raise BrokerError('connecting')
            self._flows[profile] = flow
        flow.start()
        return {'state': 'connecting', 'authorize_url': flow.authorize_url, 'expires_in': FLOW_SECONDS}

    def cancel_connect(self, profile: str) -> dict:
        with self._lock:
            flow = self._flows.get(profile)
        if flow is not None:
            flow.cancel()
        return {'cancelled': flow is not None}

    def _flow_ended(self, flow: _Flow):
        with self._lock:
            if self._flows.get(flow.profile) is flow:
                del self._flows[flow.profile]
                if not flow.done.is_set():
                    self._failures[flow.profile] = 'sign_in_expired'

    def _record_failure(self, profile, code):
        with self._lock:
            self._failures[profile] = code

    def _finish_sign_in(self, flow: _Flow, code: str) -> None:
        meta = flow.meta
        saved = {'connection': flow.connection, 'resource': connection_url(flow.connection),
                 'issuer': meta['issuer'], 'token_endpoint': meta['token_endpoint'],
                 'revocation_endpoint': meta['revocation_endpoint'], **flow.client}
        try:
            tokens = self.oauth.token(saved, {'grant_type': 'authorization_code', 'code': code,
                                              'redirect_uri': flow.redirect_uri, 'code_verifier': flow.verifier})
            saved.update(tokens)
            # A token that works at the connection URL is a connection grant: Executor refuses
            # personal access tokens there. Check before keeping it.
            Mcp(self.http, saved['resource'], saved['access_token']).open()
            saved['connected_at'] = time.time()
            self._save_if(flow.profile, flow.generation, saved, bump=True)
        except BrokerError as exc:
            self._record_failure(flow.profile, exc.code if exc.code != 'reconnect_required' else 'sign_in_failed')
            raise

    def disconnect(self, profile: str) -> dict:
        self.cancel_connect(profile)
        with self._state:
            saved = self.store.load(profile)
            self.store.delete(profile)
            self._bump(profile)
        with self._lock:
            self._failures.pop(profile, None)
        revoked = bool(saved) and self.oauth.revoke(saved)
        return {'state': 'disconnected', 'revoked': revoked}

    def _session(self, profile: str, generation: int) -> tuple[Mcp, dict]:
        saved = self.store.load(profile)
        if saved is None:
            raise BrokerError('not_connected')
        if saved.get('reconnect_required') or not saved.get('access_token'):
            raise BrokerError('reconnect_required')
        if saved['expires_at'] - 60 < time.time():
            saved = self._refresh(profile, generation, saved)
        mcp = Mcp(self.http, saved['resource'], saved['access_token'])
        try:
            mcp.open()
        except BrokerError as exc:
            if exc.code != 'unauthorized':
                raise
            saved = self._refresh(profile, generation, saved)
            mcp = Mcp(self.http, saved['resource'], saved['access_token'])
            mcp.open()
        return mcp, saved

    def _refresh(self, profile: str, generation: int, saved: dict) -> dict:
        if not saved.get('refresh_token'):
            self._save_if(profile, generation, {**saved, 'access_token': None, 'reconnect_required': True})
            raise BrokerError('reconnect_required')
        try:
            tokens = self.oauth.token(saved, {'grant_type': 'refresh_token', 'refresh_token': saved['refresh_token']})
        except BrokerError as exc:
            if exc.code == 'reconnect_required':
                self._save_if(profile, generation, {**saved, 'access_token': None, 'reconnect_required': True})
            raise
        saved = {**saved, **tokens}
        self._save_if(profile, generation, saved)
        return saved

    def _read(self, profile: str, name: str, limit: int) -> dict:
        generation = self.generation(profile)
        start, end = local_range(name, self.now())
        mcp, saved = self._session(profile, generation)
        path, account = mcp.calendar_tool()
        filters = {'timeMin': start.isoformat(), 'timeMax': end.isoformat(),
                   'singleEvents': True, 'orderBy': 'startTime', 'maxResults': limit + 1,
                   'fields': 'nextPageToken,items(summary,status,visibility,start,end)'}
        zone = local_zone()
        if zone:
            filters['timeZone'] = zone
        args = {'input': {'path': {'calendarId': CALENDAR_ID}, 'query': filters}}
        if account is not None:
            args['accountId'] = account
        raw = mcp.call_calendar(path, args)
        events, truncated = mask_events(raw, start, end, limit, upcoming=name == 'next')
        if self.generation(profile) != generation:
            raise BrokerError('changed')
        return {'range': name, 'start': start.isoformat(), 'end': end.isoformat(), 'timezone': zone,
                'events': events, 'truncated': truncated, 'generation': generation,
                'connection': saved['connection']}

    def test(self, profile: str) -> dict:
        """One fixed read of today (at most one masked event, returned to nobody): proves the
        sign-in, the connection URL, Executor's call tool and the calendar tool all work."""
        generation = self.generation(profile)
        try:
            result = self._read(profile, 'today', 1)
            outcome = {'ok': True, 'at': time.time()}
        except BrokerError as exc:
            outcome = {'ok': False, 'at': time.time(), 'code': exc.code}
            self._save_test(profile, generation, outcome)
            raise
        self._save_test(profile, generation, outcome)
        return {'test': 'working', 'connection': result['connection'], 'generation': result['generation']}

    def _save_test(self, profile, generation, outcome):
        try:
            with self._state:
                saved = self.store.load(profile)
                if saved is not None and self.generation(profile) == generation:
                    self.store.save(profile, {**saved, 'last_test': outcome})
        except BrokerError:
            pass

    def events(self, profile: str, name: str) -> dict:
        return self._read(profile, name, 1 if name == 'next' else MAX_EVENTS)


# ---- Unix socket server --------------------------------------------------------------------

def default_state_dir() -> Path:
    return Path(__file__).resolve().parents[1] / 'private' / 'calendar-broker'


def default_socket() -> Path:
    runtime = os.environ.get('XDG_RUNTIME_DIR') or '/run/user/%d' % os.getuid()
    return Path(runtime) / 'cammy-calendar' / 'broker.sock'


def _peer_uid(conn: socket.socket) -> int:
    data = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
    return struct.unpack('3i', data)[1]


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: Path, broker: Broker, client_uid: int):
        path = Path(path)
        _private_dir(path.parent)
        if path.exists() or path.is_symlink():
            info = os.lstat(path)
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError('refusing to replace a non-socket at the broker socket path')
            path.unlink()
        self.broker, self.client_uid = broker, client_uid

        class Handler(socketserver.StreamRequestHandler):
            def handle(handler):  # noqa: N805
                if _peer_uid(handler.connection) != self.client_uid:
                    return
                handler.connection.settimeout(10)
                try:
                    line = handler.rfile.readline(MAX_REQUEST + 1)
                    request = json.loads(line) if line.endswith(b'\n') and len(line) <= MAX_REQUEST else None
                except (ValueError, OSError):
                    request = None
                handler.connection.settimeout(None)
                reply = self.broker.handle(request)
                handler.wfile.write(json.dumps(reply).encode() + b'\n')

        old = os.umask(0o177)
        try:
            super().__init__(str(path), Handler)
        finally:
            os.umask(old)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='cammy-calendar-broker', description=__doc__.split('\n')[0])
    ap.add_argument('--state-dir', type=Path, default=default_state_dir())
    ap.add_argument('--socket', type=Path, default=default_socket())
    ap.add_argument('--forbid', type=Path, action='append', default=[],
                    help='an additional tree the state must stay out of (repeatable)')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    forbidden = [Path(__file__).resolve().parents[1] / 'private' / 'c3', *args.forbid]
    store = Store(args.state_dir.absolute(), forbidden)
    server = Server(args.socket, Broker(store), os.getuid())
    log.info('calendar broker ready on %s', args.socket)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        Path(args.socket).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
