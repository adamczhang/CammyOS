"""Small stdlib client for the C3 local API (host/c3/api.py), for MCP tools and scripts.

C3 holds Cammy's one control lease, so agents ask C3 instead of opening their own session.
Endpoint 127.0.0.1:8792, bearer token from private/c3/api-token.
"""
import json
import os
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = os.environ.get('CAMMY_C3_ENDPOINT', 'http://127.0.0.1:8792')
TOKEN_FILE = Path(os.environ.get('CAMMY_C3_TOKEN_FILE', ROOT / 'private/c3/api-token'))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class C3Error(Exception):
    def __init__(self, status, message, *, reason_code=None):
        super().__init__(message)
        self.code = status
        self.reason_code = reason_code


class C3Client:
    def __init__(self, endpoint=ENDPOINT, token_file=TOKEN_FILE, timeout=30, *, desktop_token_file=None):
        self.endpoint, self.token_file, self.timeout = endpoint.rstrip('/'), Path(token_file), timeout
        self.desktop_token_file = Path(desktop_token_file) if desktop_token_file is not None else None

    def desktop_headers(self):
        endpoint = urllib.parse.urlsplit(self.endpoint)
        if self.desktop_token_file is not None and endpoint.hostname in ('127.0.0.1', 'localhost', '::1'):
            try:
                value = self.desktop_token_file.read_text().strip()
            except OSError:
                return {}
            if len(value) == 64 and set(value) <= set('0123456789abcdef'):
                return {'X-Cammy-Desktop-Token': value}
        return {}

    def _token(self):
        try:
            return self.token_file.read_text().strip()
        except OSError:
            raise C3Error(503, 'C3 is not set up (no API token); start it: cd host && ../.venv-live/bin/python -m c3')

    def request(self, method, path, body=None, raw=False):
        return self.exchange(method, path, body, raw)[0]

    def exchange(self, method, path, body=None, raw=False, headers=None):
        """Like request(), but returns (payload, response headers) for routes whose headers matter."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.endpoint + path, data=data, method=method,
                                     headers={**(headers or {}), **self.desktop_headers(),
                                              'Authorization': 'Bearer ' + self._token(),
                                              **({'Content-Type': 'application/json'} if data else {})})
        try:
            # Loopback only: never through a proxy from the environment.
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(req, timeout=self.timeout) as r:
                payload = r.read()
                return (payload if raw else json.loads(payload or b'{}')), dict(r.headers.items())
        except urllib.error.HTTPError as e:
            with e:
                text = e.read(8192).decode(errors='replace')
            try:
                body = json.loads(text)
            except ValueError:
                body = None
            reason = None
            if isinstance(body, dict):
                if isinstance(body.get('error'), str):
                    text = body['error']
                if isinstance(body.get('code'), str):
                    reason = body['code']
            raise C3Error(e.code, text[:300], reason_code=reason) from None
        except (urllib.error.URLError, OSError):
            raise C3Error(503, 'C3 is not running; start it: cd host && ../.venv-live/bin/python -m c3')

    # ---- the API ------------------------------------------------------------------------
    def status(self):
        return self.request('GET', '/v1/status')

    def summary(self):
        """The few fields an agent needs, not the counters."""
        s = self.status()
        body, voice, moves = s.get('body') or {}, s.get('voice') or {}, s.get('moves') or {}
        return {'cammy_connected': bool(body.get('up')), 'voice': voice.get('state'), 'duplex': s.get('duplex'),
                'following': moves.get('following'), 'camera_view': moves.get('camera_view'),
                'sweeping': moves.get('sweeping'), 'capture_busy': (s.get('capture') or {}).get('busy'),
                'last_error': body.get('last_error') or None}

    def transcript(self, n=20):
        return self.request('GET', '/v1/transcript?n=%d' % max(1, min(400, int(n))))

    def look(self):
        return self.request('POST', '/v1/look', {}, raw=True)

    def say(self, text):
        return self.request('POST', '/v1/say', {'text': text})

    def message(self, text):
        return self.request('POST', '/v1/message', {'text': text})

    def follow(self, on):
        return self.request('POST', '/v1/follow', {'on': bool(on)})

    def camera_view(self, on):
        return self.request('POST', '/v1/camera-view', {'on': bool(on)})

    def look_around(self):
        return self.request('POST', '/v1/look-around', {})

    def turn_around(self):
        return self.request('POST', '/v1/turn-around', {})

    def capture(self, kind):
        return self.request('POST', '/v1/capture', {'kind': kind})

    def providers(self):
        """Read-only resident provider status: {'providers': [{id, name, installed, auth, check,
        detail, recovery}], 'brain'?}. Never refresh=1 and never /v1/providers/test: those run
        native CLI checks; this only reads what C3 already knows."""
        return self.request('GET', '/v1/providers')

    def brain(self):
        """Resident brain state: {backend, active_profile, profiles: [{id, name, provider}], pending}."""
        return self.request('GET', '/v1/brain')

    def brain_history(self, profile):
        """Resident brain turns of one named profile: {'entries': [{id, role, text, status, ...}]}."""
        if not isinstance(profile, str) or not profile.strip():
            raise C3Error(400, 'profile: nonempty string required')
        return self.request('GET', '/v1/brain/history?' + urllib.parse.urlencode({'profile': profile}))

    def brain_message(self, profile, text):
        """Queue a private turn only if the named profile is still selected."""
        if not isinstance(profile, str) or not profile.strip():
            raise C3Error(400, 'profile: nonempty string required')
        path = '/v1/brain/profiles/' + urllib.parse.quote(profile, safe='') + '/message'
        return self.request('POST', path, {'text': text})

    def preview_history(self):
        """The selected profile's camera history: {profile, profile_name, bytes, capacity_bytes,
        frames, oldest, newest} (Unix seconds or None). 503 when no profile is selected."""
        return self.request('GET', '/v1/preview/history')

    def preview_frame(self, profile, at):
        """The stored JPEG nearest Unix time `at` for `profile` (the active id from
        preview_history): {'jpeg', 'time', 'profile'}. C3Error 404 = a gap or overwritten,
        409 = the active profile changed (reload the history)."""
        query = urllib.parse.urlencode({'profile': profile, 'at': repr(float(at))})
        jpeg, headers = self.exchange('GET', '/v1/preview/frame?' + query, raw=True)
        headers = {k.lower(): v for k, v in headers.items()}
        try:
            when = float(headers['x-cammy-frame-time'])
        except (KeyError, ValueError):
            raise C3Error(502, 'C3 sent a frame without its time')
        if headers.get('x-cammy-profile') != profile:
            raise C3Error(409, 'the frame belongs to another profile')
        return {'jpeg': jpeg, 'time': when, 'profile': profile}
