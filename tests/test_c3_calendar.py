"""Executor calendar: broker (fake Executor, real loopback sign-in), C3 routing and guards,
API routes and the Calendar tab (fake GTK). No network, accounts or display."""
import asyncio
import base64
import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import types as pytypes
import unittest
import urllib.parse
import urllib.request
from datetime import datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
import cammy_calendar_broker as cb  # noqa: E402
from c3 import calendar as cal  # noqa: E402
from c3 import resident_brain as brainmod  # noqa: E402

CONN = 'conn_cal-1'
CONN_URL = cb.EXECUTOR_MCP + '?connection=' + CONN
AS = 'https://auth.executor.sh'


def ok_json(value, status=200, headers=None):
    return cb.Response(status, {'content-type': 'application/json', **(headers or {})}, json.dumps(value).encode())


class FakeExecutor:
    """Executor Cloud as the broker sees it: discovery, registration, tokens and MCP."""

    def __init__(self):
        self.requests = []
        self.prm = {'resource': cb.EXECUTOR_MCP, 'authorization_servers': [AS], 'scopes_supported': ['mcp']}
        self.asm = {'issuer': AS, 'authorization_endpoint': AS + '/authorize', 'token_endpoint': AS + '/token',
                    'registration_endpoint': AS + '/register', 'revocation_endpoint': AS + '/revoke',
                    'code_challenge_methods_supported': ['S256']}
        self.items = []
        self.override = {}
        self.tokens = {}
        self.refresh = {}
        self.challenges = {}
        self.call_schema = {'calls': {'type': 'array'}}
        self.calendar_matches = [{'path': cb.CALENDAR_TOOL, 'input': '{ accountId: "acc_fixture", input: object }'}]
        self.tool_calls = []
        self.during_call = None
        self.expires_in = 3600
        self.token_status = 200
        self.sse = False
        self.n = 0

    def __call__(self, method, url, headers, body):
        cb.check_url(url)        # the broker never hands a disallowed URL to the transport
        self.requests.append((method, url))
        for prefix, response in self.override.items():
            if url.startswith(prefix):
                return response
        if url == 'https://mcp.executor.sh/.well-known/oauth-protected-resource/mcp':
            return ok_json(self.prm)
        if url == AS + '/.well-known/oauth-authorization-server':
            return ok_json(self.asm)
        if url == AS + '/register':
            meta = json.loads(body)
            assert meta['token_endpoint_auth_method'] == 'none'
            return ok_json({'client_id': 'client-1'}, 201)
        if url == AS + '/token':
            form = dict(urllib.parse.parse_qsl(body.decode()))
            assert form['resource'] == CONN_URL and form['client_id'] == 'client-1'
            if self.token_status != 200:
                return cb.Response(self.token_status, {}, b'{"error":"invalid_grant","detail":"SECRET-DETAIL"}')
            if form['grant_type'] == 'authorization_code':
                verifier = form['code_verifier']
                digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                if self.challenges.get(form['code']) != digest:
                    return cb.Response(400, {}, b'{}')
            elif form['grant_type'] == 'refresh_token':
                if form['refresh_token'] not in self.refresh:
                    return cb.Response(400, {}, b'{}')
            self.n += 1
            access, refresh = 'at-%d' % self.n, 'rt-%d' % self.n
            self.tokens[access] = True
            self.refresh[refresh] = True
            return ok_json({'access_token': access, 'token_type': 'Bearer', 'expires_in': self.expires_in,
                            'refresh_token': refresh})
        if url == AS + '/revoke':
            return cb.Response(200, {}, b'')
        if url == CONN_URL:
            token = headers.get('Authorization', '')[7:]
            if token not in self.tokens:
                return cb.Response(401, {}, b'{"error":"SECRET-401"}')
            message = json.loads(body)
            if message['method'] == 'notifications/initialized':
                return cb.Response(202, {}, b'')
            if message['method'] == 'initialize':
                result = {'protocolVersion': cb.PROTOCOL_VERSION, 'capabilities': {}, 'serverInfo': {'name': 'x'}}
                return self._reply(message, result, {'mcp-session-id': 'session-1'})
            if message['method'] == 'tools/list':
                tools = [{'name': name, 'inputSchema': {'type': 'object', 'properties': {}}}
                         for name in ('skills', 'search', 'describe', 'execute', 'resume')]
                tools.append({'name': 'call', 'inputSchema': {'type': 'object', 'properties': self.call_schema}})
                return self._reply(message, {'tools': tools})
            if message['method'] == 'tools/call':
                if message['params']['name'] == 'search':
                    return self._reply(message, {'structuredContent': {'items': self.calendar_matches}})
                self.tool_calls.append(message['params'])
                if self.during_call:
                    self.during_call()
                return self._reply(message, {'content': [{'type': 'text', 'text': json.dumps(
                    {'results': [{'ok': True, 'value': {'kind': 'calendar#events', 'items': self.items}}]})}]})
        return cb.Response(404, {}, b'')

    def _reply(self, message, result, headers=None):
        payload = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
        if self.sse:
            body = ('event: message\ndata: %s\n\n' % json.dumps(payload)).encode()
            return cb.Response(200, {'content-type': 'text/event-stream', **(headers or {})}, body)
        return ok_json(payload, headers=headers)


def browser_approves(fake, authorize_url, *, state=None, code='code-1', iss=AS):
    """What the user's browser does after signing in on Executor: hit the loopback redirect."""
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(authorize_url).query))
    fake.challenges[code] = query['code_challenge']
    params = {'code': code, 'state': state or query['state'], 'iss': iss}
    url = query['redirect_uri'] + '?' + urllib.parse.urlencode(params)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b''


class BrokerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeExecutor()
        self.store = cb.Store(Path(self.tmp.name) / 'state', [ROOT])
        self.now = datetime(2026, 11, 17, 10, 0).astimezone()
        self.broker = cb.Broker(self.store, http=self.fake, now=lambda: self.now)

    def connect(self, profile='adam'):
        reply = self.broker.handle({'op': 'connect', 'profile': profile, 'connection': CONN})
        self.assertTrue(reply['ok'], reply)
        status, page = browser_approves(self.fake, reply['authorize_url'])
        self.assertEqual(status, 200)
        self.assertIn(b'Calendar connected', page)
        for _ in range(50):
            if self.broker.status(profile)['state'] == 'connected':
                break
            time.sleep(0.05)
        return reply


class DestinationTests(unittest.TestCase):
    def test_only_https_executor_hosts(self):
        for url in ('https://mcp.executor.sh/mcp', 'https://executor.sh/x', 'https://auth.executor.sh:443/t'):
            self.assertEqual(cb.check_url(url), url)
        for url in ('http://mcp.executor.sh/mcp', 'https://executor.sh.evil.com/', 'https://evilexecutor.sh/',
                    'https://user:pw@mcp.executor.sh/', 'https://mcp.executor.sh:8443/', 'https://mcp.executor.sh/#f',
                    'file:///etc/passwd', 'https://127.0.0.1/', 'https://mcp.executor.sh\n.evil/', None):
            with self.subTest(url=url), self.assertRaises(cb.BrokerError):
                cb.check_url(url)

    def test_transport_has_only_https_and_no_proxy_or_redirects(self):
        opener = cb.Https()._opener
        self.assertEqual(set(opener.handle_open), {'https', 'unknown'})
        self.assertFalse(any(isinstance(h, urllib.request.ProxyHandler) for h in opener.handlers))
        self.assertIsNone(cb._NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://evil.com/'))
        with self.assertRaises(cb.BrokerError):
            cb.Https()('GET', 'https://evil.com/', {}, None)

    def test_connection_ids_are_strict(self):
        self.assertEqual(cb.connection_url(CONN), CONN_URL)
        for bad in ('', 'a/b', 'a&connection=b', 'x' * 129, 'ünï', None):
            with self.subTest(bad=bad), self.assertRaises(cb.BrokerError):
                cb.connection_url(bad)


class SignInTests(BrokerCase):
    def test_scoped_oauth_flow_stores_private_per_profile_tokens(self):
        reply = self.connect()
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(reply['authorize_url']).query))
        self.assertTrue(reply['authorize_url'].startswith(AS + '/authorize?'))
        self.assertEqual(query['code_challenge_method'], 'S256')
        self.assertEqual(query['resource'], CONN_URL)
        self.assertRegex(query['redirect_uri'], r'^http://127\.0\.0\.1:\d+/callback$')
        status = self.broker.handle({'op': 'status', 'profile': 'adam'})
        self.assertEqual((status['state'], status['connection']), ('connected', CONN))
        self.assertNotIn('at-1', json.dumps(status))
        folder = self.store.root / 'profiles' / 'adam'
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(folder / 'connection.json').st_mode), 0o600)
        self.assertEqual(self.broker.status('other')['state'], 'disconnected')    # no shared login
        hosts = {urllib.parse.urlsplit(u).hostname for _, u in self.fake.requests}
        self.assertEqual(hosts, {'mcp.executor.sh', 'auth.executor.sh'})

    def test_wrong_state_is_ignored_and_bad_issuer_fails(self):
        reply = self.broker.handle({'op': 'connect', 'profile': 'adam', 'connection': CONN})
        status, _ = browser_approves(self.fake, reply['authorize_url'], state='forged')
        self.assertEqual(status, 400)
        self.assertEqual(self.broker.status('adam')['state'], 'connecting')
        status, page = browser_approves(self.fake, reply['authorize_url'], iss='https://evil.executor.sh')
        self.assertIn(b'did not complete', page)
        time.sleep(0.1)
        self.assertEqual(self.broker.status('adam')['state'], 'disconnected')
        self.assertEqual(self.broker.status('adam')['last_error'], 'sign_in_failed')

    def test_discovery_refuses_redirects_foreign_servers_and_missing_pkce(self):
        cases = [
            ({'https://mcp.executor.sh/.well-known': cb.Response(302, {'location': 'https://evil.com'}, b'')},
             None, 'redirect_refused'),
            ({}, lambda f: f.prm.update(authorization_servers=['https://evil.com']), 'destination_refused'),
            ({}, lambda f: f.asm.update(token_endpoint='https://evil.com/token'), 'destination_refused'),
            ({}, lambda f: f.asm.pop('code_challenge_methods_supported'), 'pkce_unsupported'),
            ({}, lambda f: f.prm.update(resource='https://mcp.executor.sh/other'), 'discovery_failed'),
        ]
        for override, change, code in cases:
            with self.subTest(code=code):
                self.fake = FakeExecutor()
                self.fake.override = override
                if change:
                    change(self.fake)
                self.broker = cb.Broker(self.store, http=self.fake)
                reply = self.broker.handle({'op': 'connect', 'profile': 'adam', 'connection': CONN})
                self.assertEqual(reply['code'], code)
                self.assertNotIn('evil', json.dumps(self.fake.requests))

    def test_disconnect_deletes_and_revokes(self):
        self.connect()
        reply = self.broker.handle({'op': 'disconnect', 'profile': 'adam'})
        self.assertEqual(reply, {'ok': True, 'state': 'disconnected', 'revoked': True})
        self.assertFalse((self.store.root / 'profiles' / 'adam' / 'connection.json').exists())
        self.assertEqual(self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})['code'],
                         'not_connected')


class ReadTests(BrokerCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.fake.items = [
            {'summary': 'Standup', 'start': {'dateTime': '2026-11-17T09:00:00-06:00'},
             'end': {'dateTime': '2026-11-17T09:15:00-06:00'}, 'description': 'SECRET-DESC',
             'location': 'Room 4', 'attendees': [{'email': 'x@example.com'}], 'htmlLink': 'https://x'},
            {'summary': 'Therapy', 'visibility': 'private', 'start': {'dateTime': '2026-11-17T13:00:00-06:00'},
             'end': {'dateTime': '2026-11-17T14:00:00-06:00'}},
            {'summary': 'Dropped', 'status': 'cancelled', 'start': {'dateTime': '2026-11-17T15:00:00-06:00'}},
            {'summary': 'Holiday‮\x07', 'start': {'date': '2026-11-17'}, 'end': {'date': '2026-11-18'}},
        ]

    def test_events_are_masked_and_fixed(self):
        reply = self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})
        self.assertTrue(reply['ok'], reply)
        text = json.dumps(reply)
        for secret in ('SECRET-DESC', 'Room 4', 'x@example.com', 'Therapy', 'Dropped', 'htmlLink'):
            self.assertNotIn(secret, text)
        self.assertEqual([e['title'] for e in reply['events']], ['Holiday', 'Standup', 'Private event'])
        self.assertEqual(set(reply['events'][0]), {'title', 'start', 'end', 'all_day', 'private'})
        (params,) = self.fake.tool_calls
        self.assertEqual(params['name'], 'call')
        call, = params['arguments']['calls']
        self.assertEqual(call['path'], cb.CALENDAR_TOOL)
        self.assertEqual(call['input']['accountId'], 'acc_fixture')
        args = call['input']['input']
        self.assertEqual((args['path']['calendarId'], args['query']['singleEvents'], args['query']['maxResults']),
                         ('primary', True, 21))
        self.assertEqual(args['query']['fields'], 'nextPageToken,items(summary,status,visibility,start,end)')

    def test_ranges_are_local_and_bounded(self):
        with patch.dict(os.environ, {'TZ': 'America/Chicago'}):
            time.tzset()
            try:
                start, end = cb.local_range('tomorrow', datetime(2026, 10, 31, 23, 30))
                self.assertEqual((start.isoformat(), end.isoformat()),
                                 ('2026-11-01T00:00:00-05:00', '2026-11-02T00:00:00-06:00'))   # DST ends
                start, end = cb.local_range('next', datetime(2026, 11, 17, 10, 0))
                self.assertEqual(end - start, cb.NEXT_WINDOW)
            finally:
                os.environ.pop('TZ', None)
            time.tzset()
        reply = self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'next week'})
        self.assertEqual(reply['code'], 'invalid_range')

    def test_next_is_the_first_upcoming_timed_event(self):
        self.now = datetime.fromisoformat('2026-11-17T10:00:00-06:00')
        reply = self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'next'})
        self.assertEqual([e['title'] for e in reply['events']], ['Private event'])

    def test_sse_responses_and_refresh_on_expiry_and_401(self):
        self.fake.sse = True
        saved = self.store.load('adam')
        self.store.save('adam', {**saved, 'expires_at': time.time() - 5})
        self.assertTrue(self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})['ok'])
        self.assertEqual(self.store.load('adam')['access_token'], 'at-2')
        self.fake.tokens.clear()         # server revoked the access token: refresh once, retry
        self.assertTrue(self.broker.handle({'op': 'test', 'profile': 'adam'})['ok'])
        self.assertTrue(self.store.load('adam')['last_test']['ok'])

    def test_refresh_refused_requires_reconnect_without_leaking_details(self):
        self.fake.tokens.clear()
        self.fake.token_status = 400
        reply = self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})
        self.assertEqual(reply['code'], 'reconnect_required')
        self.assertNotIn('SECRET', json.dumps(reply))
        self.assertIsNone(self.store.load('adam')['access_token'])
        self.assertEqual(self.broker.status('adam')['state'], 'reconnect_required')

    def test_server_errors_are_redacted(self):
        self.fake.override = {CONN_URL: cb.Response(500, {}, b'SECRET-STACKTRACE token=at-1')}
        reply = self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})
        self.assertEqual(reply, {'ok': False, 'code': 'server_error', 'message': cb.MESSAGES['server_error']})

    def test_unknown_call_shape_and_missing_tool_fail_closed(self):
        self.fake.call_schema = {'program': {'type': 'string'}}
        self.assertEqual(self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})['code'],
                         'call_unsupported')
        self.assertEqual(self.fake.tool_calls, [])
        self.fake.call_schema = {'calls': {}}
        self.fake._reply_orig = self.fake._reply
        self.fake.override = {}
        original = self.fake.__call__

        def missing(method, url, headers, body):
            if url == CONN_URL and json.loads(body).get('params', {}).get('name') == 'call':
                message = json.loads(body)
                return ok_json({'jsonrpc': '2.0', 'id': message['id'], 'result': {
                    'isError': True, 'content': [{'type': 'text', 'text': 'Tool not found: x'}]}})
            return original(method, url, headers, body)
        self.broker.http = self.broker.oauth.http = missing
        self.assertEqual(self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})['code'],
                         'tool_missing')

    def test_scoped_search_must_offer_one_calendar_account(self):
        self.fake.calendar_matches[0]['input'] = '{ accountId: "acc_first" | "acc_second", input: object }'
        reply = self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})
        self.assertEqual(reply['code'], 'account_ambiguous')
        self.assertEqual(self.fake.tool_calls, [])

    def test_disconnect_mid_read_returns_nothing_and_keeps_no_credentials(self):
        saved = self.store.load('adam')
        self.store.save('adam', {**saved, 'expires_at': time.time() + 3600})
        self.fake.during_call = lambda: self.broker.disconnect('adam')
        reply = self.broker.handle({'op': 'events', 'profile': 'adam', 'range': 'today'})
        self.assertEqual(reply['code'], 'changed')
        self.assertIsNone(self.store.load('adam'))

    def test_only_fixed_operations(self):
        for request in ({'op': 'call', 'profile': 'adam'}, {'op': 'execute', 'profile': 'adam', 'code': 'x'},
                        {'op': 'resume', 'profile': 'adam'}, {'op': 'events', 'profile': 'adam', 'range': 'today',
                                                               'tool': 'gmail.send'},
                        {'op': 'status', 'profile': '../x'}, [], None):
            with self.subTest(request=request):
                self.assertFalse(self.broker.handle(request)['ok'])
        self.assertEqual(len(self.fake.tool_calls), 0)


class StorageAndSocketTests(unittest.TestCase):
    def test_state_must_stay_outside_c3_and_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            c3_data = Path(tmp) / 'private' / 'c3'
            c3_data.mkdir(parents=True)
            for inside in (c3_data / 'calendar', c3_data / 'brain' / 'profiles' / 'adam' / 'work' / 'x', ROOT / 'x'):
                with self.subTest(inside=inside), self.assertRaises(ValueError):
                    cb.Store(inside, [ROOT, c3_data])
            cb.Store(Path(tmp) / 'outside', [ROOT, c3_data])

    def test_socket_round_trip_and_private_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = cb.Store(Path(tmp) / 'state', [ROOT])
            path = Path(tmp) / 'run' / 'broker.sock'
            server = cb.Server(path, cb.Broker(store, http=FakeExecutor()), os.getuid())
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
            reply = asyncio.run(cal.BrokerClient(path).call('status', profile='adam'))
            self.assertEqual(reply['state'], 'disconnected')
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(str(path))
                s.sendall(b'x' * 5000 + b'\n')
                self.assertEqual(json.loads(s.makefile().readline())['code'], 'bad_request')
            other = cb.Server(Path(tmp) / 'run2' / 'b.sock', cb.Broker(store, http=FakeExecutor()), os.getuid() + 1)
            threading.Thread(target=other.serve_forever, daemon=True).start()
            self.addCleanup(other.server_close)
            self.addCleanup(other.shutdown)
            with self.assertRaises(cal.CalendarBlocked):
                asyncio.run(cal.BrokerClient(Path(tmp) / 'run2' / 'b.sock').call('health'))


# ---- C3 side ------------------------------------------------------------------------------

class IntentTests(unittest.TestCase):
    def test_paraphrases(self):
        reads = {
            "What's on my calendar today?": 'today', 'whats on my calendar': 'today',
            'Do I have any meetings tomorrow?': 'tomorrow', "What's my next meeting?": 'next',
            'When is my next appointment': 'next', 'Am I free this afternoon?': 'today',
            "What's my schedule for tomorrow": 'tomorrow', 'anything on my agenda today?': 'today',
            'Do I have anything coming up?': 'next', 'Read me tomorrow’s schedule': 'tomorrow',
            'What is on the calendar tonight': 'today', 'Do I have any meetings?': 'today',
        }
        for text, rng in reads.items():
            with self.subTest(text=text):
                self.assertEqual(cal.parse_intent(text), cal.Intent('read', range=rng))
        blocked = {
            'Add lunch with Sam to my calendar tomorrow': 'read_only',
            'Cancel my next meeting': 'read_only', 'Schedule a meeting with Bob tomorrow': 'read_only',
            "What's on my calendar on Friday?": 'unsupported_range',
            'Do I have meetings next week?': 'unsupported_range',
            'What is on my calendar today and tomorrow': 'unsupported_range',
        }
        for text, reason in blocked.items():
            with self.subTest(text=text):
                self.assertEqual(cal.parse_intent(text), cal.Intent('blocked', reason=reason))
        for text in ('Summarize my meeting notes from today', 'Update the calendar module code in the repo',
                     'Schedule the backup job for tomorrow', 'What events happened in the C3 log?',
                     'Tell me a joke', 'What is the next step for the build?', 'Plan my week of meals',
                     'x' * 500):
            with self.subTest(text=text):
                self.assertIsNone(cal.parse_intent(text))


class FakeBroker:
    def __init__(self):
        self.calls = []
        self.during = None
        self.reply = {'ok': True, 'range': 'today', 'timezone': 'America/Chicago', 'truncated': False,
                      'events': [{'title': 'Standup', 'start': '2026-11-17T09:00:00-06:00',
                                  'end': '2026-11-17T09:15:00-06:00', 'all_day': False, 'private': False},
                                 {'title': 'Private event', 'start': '2026-11-17T13:00:00-06:00',
                                  'end': '2026-11-17T14:00:00-06:00', 'all_day': False, 'private': True}]}

    async def call(self, op, **fields):
        self.calls.append((op, fields))
        if self.during and op in ('events', 'test', 'connect'):
            await self.during()
        if op == 'events':
            return {**self.reply, 'range': fields['range']}
        if op == 'connect':
            return {'ok': True, 'state': 'connecting', 'authorize_url': AS + '/authorize?x=1', 'expires_in': 300}
        if op == 'status':
            return {'ok': True, 'state': 'connected', 'connection': CONN, 'last_test': None, 'last_error': None}
        return {'ok': True}


class FakeC3:
    def __init__(self, brain, policy=True, grants=False):
        self.brain = brain
        if policy:
            self.allow = True
            self.calendar_access_allows = lambda profile: self.allow
        if grants:
            self.access_generation = 7
            self.calendar_access_grant = lambda profile: self.access_generation if self.allow else None
            self.calendar_access_current = lambda profile, g: self.allow and g == self.access_generation

    adult = True

    def selected_profile(self):
        id = self.brain._active
        return (id, self.brain._profiles[id]['name'], Path('/unused')) if id else None

    def active_profile(self):
        return self.selected_profile() if self.adult else None


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.provider_turns = []

        async def runner(request):
            self.provider_turns.append(request.text)
            return brainmod.RunResult('11111111-1111-4111-8111-111111111111', 'provider answer')
        self.brain = brainmod.ResidentBrain(Path(self.tmp.name) / 'brain', runner=runner)
        self.addAsyncCleanup(self.brain.close)
        await self.brain.create_profile('adam', 'Adam', 'claude')
        await self.brain.create_profile('kid', 'Kid', 'codex')
        await self.brain.select_profile('adam')
        self.c3 = FakeC3(self.brain)
        self.broker = FakeBroker()
        self.spoken = []

        async def speak(text):
            self.assertTrue(self.service.mic_hold())
            self.spoken.append(text)
        self.service = cal.CalendarService(self.c3, self.broker, speak=speak, mic_tail_s=0.05)
        self.brain.local_router = self.service.route

    async def test_calendar_request_stays_local(self):
        outcome = await self.brain.ask("What's on my calendar today?")
        self.assertEqual((outcome['status'], outcome['capability'], outcome['profile_id']), ('local', 'calendar', 'adam'))
        self.assertNotIn('Standup', json.dumps(outcome))
        self.assertEqual(self.provider_turns, [])
        self.assertEqual(self.brain.history('adam'), [])
        self.assertTrue(self.brain.replies.empty())          # nothing queued for Gemini
        self.assertEqual(self.service.last('adam')['lines'], ['9:00 AM – 9:15 AM, Standup',
                                                              '1:00 PM – 2:00 PM, Private event'])
        self.assertEqual(len(self.spoken), 1)
        self.assertIn('Standup', self.spoken[0])
        await asyncio.sleep(0.08)
        self.assertFalse(self.service.mic_hold())

    async def test_local_voice_needs_address_and_current_grant(self):
        scope = self.service.voice_context()
        self.assertIsNotNone(scope)
        self.assertIsNone(await self.service.voice_request('What is on my calendar tomorrow?', scope))
        self.assertEqual(self.broker.calls, [])
        self.assertEqual(await self.service.voice_request('Hey Cammy, check my schedule tomorrow.', scope),
                         'I put your schedule in the Calendar tab.')
        self.assertEqual(self.service.last('adam')['range'], 'tomorrow')
        self.assertEqual(self.spoken, [])
        self.c3.allow = False
        self.assertIsNone(await self.service.voice_request('Cammy, next event?', scope))
        self.assertIsNone(self.service.last('adam'))

    async def test_typed_chat_is_not_spoken_and_others_go_to_provider(self):
        await self.brain.submit('do I have any meetings tomorrow')
        self.assertEqual(self.spoken, [])
        await self.brain.submit('Tell me a joke')
        await self.brain._job.task
        self.assertEqual(self.provider_turns, ['Tell me a joke'])

    async def test_refusals_are_not_forwarded(self):
        cases = [('Add dinner to my calendar', 'read_only', None),
                 ("What's on my calendar Friday", 'unsupported_range', None),
                 ("What's on my calendar today?", 'calendar_locked', lambda: setattr(self.c3, 'allow', False)),
                 ("What's on my calendar today?", 'calendar_access_error',
                  lambda: setattr(self.c3, 'calendar_access_allows', lambda p: 1 / 0))]
        for text, code, setup in cases:
            with self.subTest(code=code):
                if setup:
                    setup()
                outcome = await self.brain.submit(text)
                self.assertEqual((outcome['status'], outcome['code']), ('blocked', code))
        self.assertEqual(self.provider_turns, [])
        self.assertNotIn(('events', {'profile': 'adam', 'range': 'today'}), self.broker.calls)

    async def test_fails_closed_without_calendar_access(self):
        service = cal.CalendarService(FakeC3(self.brain, policy=False), self.broker)
        with self.assertRaises(cal.CalendarBlocked) as caught:
            await service.query('today')
        self.assertEqual(caught.exception.code, 'calendar_access_missing')
        self.assertEqual(self.broker.calls, [])
        status = await service.status()
        self.assertEqual(status['access'], {'integrated': False, 'allowed': False})
        self.assertFalse(status['ready'])

    async def test_aba_profile_switch_discards_result(self):
        async def aba():
            await self.brain.select_profile('kid')
            await self.brain.select_profile('adam')
        self.broker.during = aba
        outcome = await self.brain.submit("What's on my calendar today?")
        self.assertEqual(outcome['code'], 'profile_changed')
        self.assertIsNone(self.service.last('adam'))

    async def test_access_locked_mid_call_and_stale_results_hidden(self):
        async def revoke():
            self.c3.allow = False
        self.broker.during = revoke
        with self.assertRaises(cal.CalendarBlocked) as caught:
            await self.service.query('today', profile='adam')
        self.assertEqual(caught.exception.code, 'calendar_locked')
        self.broker.during = None
        self.c3.allow = True
        await self.service.query('today', profile='adam')
        self.assertIsNotNone(self.service.last('adam'))
        await self.brain.select_profile('kid')
        await self.brain.select_profile('adam')
        self.assertIsNone(self.service.last('adam'))

    async def test_access_grant_generation_is_rechecked(self):
        self.c3 = FakeC3(self.brain, grants=True)
        self.service = cal.CalendarService(self.c3, self.broker)

        async def relock():          # Calendar access locked and reopened during the call
            self.c3.access_generation += 1
        self.broker.during = relock
        with self.assertRaises(cal.CalendarBlocked) as caught:
            await self.service.query('today', profile='adam')
        self.assertEqual(caught.exception.code, 'calendar_locked')
        self.broker.during = None
        await self.service.query('today', profile='adam')
        self.assertIsNotNone(self.service.last('adam'))
        self.c3.access_generation += 1
        self.assertIsNone(self.service.last('adam'))
        self.c3.allow = False
        with self.assertRaises(cal.CalendarBlocked):
            await self.service.query('today', profile='adam')

    async def test_account_actions_are_pinned(self):
        with self.assertRaises(cal.CalendarBlocked):
            await self.service.connect('kid', CONN)
        with self.assertRaises(cal.CalendarBlocked) as caught:
            await self.service.connect('adam', 'bad/id')
        self.assertEqual(caught.exception.code, 'invalid_connection')

        async def switch():
            await self.brain.select_profile('kid')
        self.broker.during = switch
        with self.assertRaises(cal.CalendarBlocked):
            await self.service.connect('adam', CONN)
        self.assertEqual(self.broker.calls[-1], ('cancel_connect', {'profile': 'adam'}))
        self.broker.during = None
        await self.brain.select_profile('adam')
        self.assertEqual((await self.service.connect('adam', CONN))['authorize_url'], AS + '/authorize?x=1')
        self.c3.allow = False
        self.assertEqual((await self.service.disconnect('adam'))['state'], 'disconnected')   # always allowed

    async def test_kids_mode_can_still_remove_access_but_sees_no_account_data(self):
        self.c3.adult = False                    # Kids: active_profile is closed, the selection is not
        self.c3.allow = False
        with self.assertRaises(cal.CalendarBlocked) as caught:
            await self.service.query('today', profile='adam')
        self.assertEqual(caught.exception.code, 'no_profile')
        public = await self.service.status()
        self.assertEqual((public['profile'], public['connection'], public['adult']), (None, None, False))
        window = await self.service.status(panel=True)
        self.assertEqual((window['profile'], window['connection']['state']), ('adam', 'connected'))
        self.assertFalse(window['access']['allowed'])
        self.assertFalse(window['ready'])
        self.assertEqual((await self.service.cancel_connect('adam'))['cancelled'], True)
        self.assertEqual((await self.service.disconnect('adam'))['state'], 'disconnected')
        with self.assertRaises(cal.CalendarBlocked):
            await self.service.disconnect('kid')                # only the selected profile
        self.assertNotIn('events', [op for op, _ in self.broker.calls])

    async def test_locked_access_still_shows_setup_state_for_disconnect(self):
        self.c3.allow = False
        status = await self.service.status()
        self.assertEqual((status['profile'], status['connection']['state']), ('adam', 'connected'))
        self.assertEqual(status['access'], {'integrated': True, 'allowed': False, 'code': 'calendar_locked'})

    async def test_broker_down_is_reported(self):
        service = cal.CalendarService(self.c3, cal.BrokerClient(Path(self.tmp.name) / 'missing.sock'))
        self.brain.local_router = service.route
        outcome = await self.brain.submit("what's on my calendar")
        self.assertEqual(outcome['code'], 'broker_unavailable')
        self.assertFalse((await service.status())['broker']['running'])
        self.assertEqual(self.provider_turns, [])

    async def test_generation_counts_every_selection(self):
        before = self.brain.generation
        await self.brain.select_profile('adam')
        self.assertEqual(self.brain.generation, before + 1)


class VoiceToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_gets_no_calendar_data(self):
        try:
            from c3 import voice as voicemod
        except ImportError as exc:
            self.skipTest('voice dependencies missing: %s' % exc)

        class Brain:
            async def ask(self, text):
                return self.outcome
        brain = Brain()
        lines = []
        stub = SimpleNamespace(brain=brain, transcript=SimpleNamespace(add=lambda *a: lines.append(a)))
        call = SimpleNamespace(name='ask_brain', args={'request': 'what is on my calendar'}, id='1')
        brain.outcome = {'capability': 'calendar', 'status': 'local', 'message': 'Shown in Cammy Control Center.'}
        response = await voicemod.Voice._tool(stub, call)
        self.assertIn("Answered on Adam's PC", response.response['result'])
        brain.outcome = {'capability': 'calendar', 'status': 'blocked', 'message': cal.BLOCKED['read_only']}
        response = await voicemod.Voice._tool(stub, call)
        self.assertEqual(response.response, {'error': 'not done: ' + cal.BLOCKED['read_only']})
        brain.outcome = {'id': 'x', 'profile_id': 'adam', 'status': 'pending'}
        response = await voicemod.Voice._tool(stub, call)
        self.assertIn('Passed to your brain', response.response['result'])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            from aiohttp.test_utils import TestClient, TestServer
            from c3 import api
        except ImportError as exc:
            self.skipTest('aiohttp missing: %s' % exc)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.provider_turns = []

        async def runner(request):
            self.provider_turns.append(request.text)
            return brainmod.RunResult('11111111-1111-4111-8111-111111111111', 'ok')
        brain = brainmod.ResidentBrain(Path(self.tmp.name) / 'brain', runner=runner)
        self.addAsyncCleanup(brain.close)
        await brain.create_profile('adam', 'Adam', 'claude')
        await brain.select_profile('adam')
        fake = FakeC3(brain)
        self.service = cal.CalendarService(fake, FakeBroker())
        brain.local_router = self.service.route
        c3 = SimpleNamespace(brain_backend='resident', brain=brain, provider_health=None, calendar=self.service,
                             calendar_panel_token=cal.panel_token(Path(self.tmp.name)),
                             voice=SimpleNamespace(state='asleep'), transcript=None)
        self.panel = {'Authorization': 'Bearer secret', 'X-Cammy-Panel-Token': c3.calendar_panel_token}
        self.auth = {'Authorization': 'Bearer secret'}
        self.client = TestClient(TestServer(api.make_app(c3, 'secret')))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()

    async def test_brain_message_reply_has_no_calendar_data(self):
        response = await self.client.post('/v1/brain/profiles/adam/message', headers=self.auth,
                                          json={'text': "What's on my calendar today?"})
        body = await response.json()
        self.assertEqual((response.status, body['queued'], body['local']['status']), (200, False, 'local'))
        self.assertNotIn('Standup', json.dumps(body))
        self.assertEqual(self.provider_turns, [])

    async def test_calendar_data_and_actions_need_the_panel_token(self):
        for method, path, payload in (('post', '/v1/calendar/query', {'profile': 'adam', 'range': 'today'}),
                                      ('get', '/v1/calendar/result?profile=adam', None),
                                      ('post', '/v1/calendar/connect', {'profile': 'adam', 'connection': CONN}),
                                      ('post', '/v1/calendar/disconnect', {'profile': 'adam'})):
            response = await getattr(self.client, method)(path, headers=self.auth, json=payload)
            self.assertEqual(response.status, 403, path)
        response = await self.client.get('/v1/calendar', headers=self.auth)
        status = await response.json()
        self.assertEqual((status['profile'], status['ready'], status['connection']['state']), ('adam', True, 'connected'))
        response = await self.client.post('/v1/calendar/query', headers=self.panel, json={'profile': 'adam', 'range': 'today'})
        self.assertEqual((await response.json())['lines'][0], '9:00 AM – 9:15 AM, Standup')
        response = await self.client.get('/v1/calendar/result?profile=adam', headers=self.panel)
        self.assertEqual((await response.json())['result']['range'], 'today')
        response = await self.client.post('/v1/calendar/query', headers=self.panel, json={'profile': 'kid', 'range': 'today'})
        self.assertEqual(response.status, 409)
        response = await self.client.post('/v1/calendar/query', headers=self.panel, json={'profile': 'adam', 'range': 'week'})
        self.assertEqual(response.status, 400)
        self.assertEqual(stat.S_IMODE(os.stat(Path(self.tmp.name) / 'calendar-panel-token').st_mode), 0o600)


# ---- Calendar tab with a fake GTK -------------------------------------------------------------

class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return name


class _Widget(metaclass=_Meta):
    def __init__(self, *args, **props):
        self.props = dict(props)
        self.children = []
        self.signals = {}
        self.mapped = False

    def connect(self, signal, callback, *args):
        self.signals.setdefault(signal, []).append((callback, args))

    def emit(self, signal):
        for callback, args in self.signals.get(signal, []):
            callback(self, *args)

    def append(self, widget):
        self.children.append(widget)

    def remove(self, widget):
        self.children.remove(widget)

    def get_mapped(self):
        return self.mapped

    def get_text(self):
        return self.props.get('text', '')

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        if name.startswith('set_'):
            return lambda *v: self.props.__setitem__(name[4:], v[0] if len(v) == 1 else v)
        if name.startswith('get_'):
            return lambda: self.props.get(name[4:])
        return lambda *a, **k: None


class _Module(pytypes.ModuleType):
    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        cls = type(name, (_Widget,), {})
        setattr(self, name, cls)
        return cls


def fake_gi():
    gi = pytypes.ModuleType('gi')
    gi.require_version = lambda *a: None
    repo = pytypes.ModuleType('gi.repository')
    for name in ('Adw', 'Gio', 'GLib', 'Gtk', 'Pango'):
        setattr(repo, name, _Module(name))
    gi.repository = repo
    return {'gi': gi, 'gi.repository': repo}


class FakeApi:
    def __init__(self, status):
        self.status_value = status
        self.calls = []
        self.authorize = AS + '/authorize?x=1'

    def status(self):
        return self.status_value

    def result(self, profile):
        return None

    def connect(self, profile, connection):
        self.calls.append(('connect', profile, connection))
        return {'profile': profile, 'state': 'connecting', 'authorize_url': self.authorize}

    def test(self, profile):
        self.calls.append(('test', profile))
        return {'test': 'working'}

    def query(self, profile, range_name):
        self.calls.append(('query', profile, range_name))
        return {'profile': profile, 'profile_name': 'Adam', 'label': 'Today', 'lines': ['9:00 AM, Standup']}


def status(access=True, integrated=True, running=True, state='connected', test=None, adult=True):
    return {'profile': 'adam', 'profile_name': 'Adam', 'adult': adult,
            'access': {'integrated': integrated, 'allowed': access},
            'broker': {'running': running}, 'ready': access and state == 'connected',
            'connection': {'state': state, 'connection': CONN if state != 'disconnected' else None,
                           'last_test': test, 'last_error': None}}


class PanelTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(sys.modules, fake_gi()):
            sys.modules.pop('c3.calendar_panel', None)
            self.panel_mod = import_module('c3.calendar_panel')

    def make(self, value):
        api = FakeApi(value)
        launched = []
        run = lambda work, done: done(*((work(), None) if True else ()))   # noqa: E731 - synchronous
        panel = self.panel_mod.CalendarPanel(None, api=api, run=run, launch=launched.append)
        panel.refresh()
        return panel, api, launched

    def test_dependencies_are_honest(self):
        rows = dict((t, (ok, d)) for t, ok, d in self.panel_mod.dependencies(status(access=False, integrated=False)))
        self.assertEqual(rows['Calendar access'], (False, 'This C3 has no Calendar access gate. Calendar stays off.'))
        rows = dict((t, (ok, d)) for t, ok, d in self.panel_mod.dependencies(status(access=False)))
        self.assertEqual(rows['Calendar access'],
                         (False, 'Locked. Unlock it with the Calendar access PIN in the Family tab.'))
        rows = dict((t, (ok, d)) for t, ok, d in self.panel_mod.dependencies(status(access=False, adult=False)))
        self.assertEqual(rows['Calendar access'], (False, 'Kids mode: Calendar stays off. Disconnect still works.'))
        self.assertIn('C3 cannot see which account', rows['Executor scoped connection'][1])
        rows = dict((t, ok) for t, ok, _ in self.panel_mod.dependencies(status(running=False)))
        self.assertFalse(rows['Calendar broker'])
        self.assertEqual(self.panel_mod.dependencies(None)[0][1], False)

    def test_buttons_follow_state(self):
        actions = self.panel_mod.actions
        self.assertEqual(actions(status()), {'connect': False, 'cancel': False, 'test': True,
                                             'disconnect': True, 'read': True})
        self.assertFalse(any(actions(status(), busy=True).values()))
        off = actions(status(access=False, state='disconnected'))
        self.assertFalse(off['connect'] or off['read'] or off['test'])
        self.assertTrue(actions(status(state='disconnected'))['connect'])
        self.assertTrue(actions(status(access=False, state='connected'))['disconnect'])
        self.assertTrue(actions(status(access=False, adult=False, state='connected'))['disconnect'])
        self.assertTrue(actions(status(state='connecting'))['cancel'])

    def test_connect_opens_only_executor_sign_in(self):
        panel, api, launched = self.make(status(state='disconnected'))
        self.assertTrue(panel.buttons['connect'].props['sensitive'])
        self.assertFalse(panel.buttons['read:today'].props['sensitive'])
        panel.connection.set_text(CONN)
        panel.buttons['connect'].emit('clicked')
        self.assertEqual(api.calls, [('connect', 'adam', CONN)])
        self.assertEqual(launched, [AS + '/authorize?x=1'])
        api.authorize = 'https://evil.com/authorize'
        panel.buttons['connect'].emit('clicked')
        self.assertEqual(len(launched), 1)

    def test_reads_show_locally(self):
        panel, api, _ = self.make(status())
        panel.buttons['read:tomorrow'].emit('clicked')
        self.assertEqual(api.calls, [('query', 'adam', 'tomorrow')])
        self.assertEqual(panel.result.props['label'], '9:00 AM, Standup')
        self.assertEqual(panel.result_title.props['label'], 'Today · Adam')
        rows = [r.props['title'] for r in panel.check_rows]
        self.assertEqual(rows[0], 'Brain profile')


if __name__ == '__main__':
    unittest.main()
