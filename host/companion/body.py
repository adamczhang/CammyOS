"""Body adapters: the only route from the companion to Cammy's v1 control plane.

``SimulatorBody`` calls the in-process ``host/cammy_simulator.Simulator`` and
performs no I/O. ``SdkBody`` reuses ``sdk/cammy.py`` against an explicitly given
loopback gateway endpoint and token; it makes no request until the service asks.
Neither adapter discovers endpoints, reads token files or opens native sockets.
SPDX-License-Identifier: MIT
"""
import base64
import binascii
import hashlib
import http.client
import io
import json
import re
from pathlib import Path
import sys
from urllib.parse import urlsplit
import wave

from .contracts import AudioClip

AGENT_ID = re.compile(r'[A-Za-z0-9_-]{1,48}')
# Companion feature -> v1 capability key. The real portal currently reports all
# four as {"available": false}; the companion simulator reports them simulated.
FEATURES = {'speech': 'speech', 'face': 'face', 'gaze': 'gimbal', 'sensing': 'audio_capture'}
EXPRESSIONS = ('idle', 'listening', 'thinking', 'speaking', 'muted', 'sleep')
MAX_CAPTURE_BYTES = 1 << 20
LOOPBACK_HOSTS = ('127.0.0.1', '::1', 'localhost')


class BodyError(RuntimeError):
    """kind: invalid | auth | conflict | unsupported | unavailable | protocol."""
    def __init__(self, kind, status, message):
        self.kind, self.status = kind, status
        super().__init__(message)


def error_kind(status):
    known = {400: 'invalid', 401: 'auth', 403: 'auth', 404: 'unsupported', 409: 'conflict',
             501: 'unsupported', 503: 'unavailable'}
    return known.get(status, 'unavailable' if status >= 500 else 'invalid')


def describe_capabilities(identity, caps):
    """Translate body capability claims; never upgrades simulated to physical."""
    simulated_body = identity.get('simulation') is True or caps.get('simulation') is True
    out = {}
    for feature, key in FEATURES.items():
        entry = caps.get(key) if isinstance(caps.get(key), dict) else {}
        available = entry.get('available') is True
        simulated = simulated_body or entry.get('simulated') is True
        out[feature] = {'body_capability': key, 'available': available, 'physical_verified': False,
                        'evidence': ('simulated' if simulated else 'body-reported-unverified')
                        if available else 'unavailable'}
    return out


def identity_fingerprint(identity):
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]


def parse_capture(payload, captured_at):
    """Validate a bounded mono/stereo 16-bit WAV capture reply."""
    data = payload.get('wav_base64')
    if not isinstance(data, str) or len(data) > MAX_CAPTURE_BYTES * 4 // 3 + 4:
        raise BodyError('protocol', 200, 'capture reply lacks a bounded wav_base64 field')
    try:
        raw = base64.b64decode(data, validate=True)
        with wave.open(io.BytesIO(raw), 'rb') as audio:
            channels, width, rate, frames = (audio.getnchannels(), audio.getsampwidth(),
                                             audio.getframerate(), audio.getnframes())
            if width != 2 or channels not in (1, 2) or not 8000 <= rate <= 48000:
                raise BodyError('protocol', 200, 'unsupported capture format')
            pcm = audio.readframes(frames)
    except (binascii.Error, wave.Error, EOFError) as exc:
        raise BodyError('protocol', 200, 'malformed capture WAV') from exc
    source = str(payload.get('source', 'unspecified'))[:120]
    synthetic = payload.get('simulation') is True or 'synthetic' in source.lower()
    return AudioClip(pcm, rate, channels, frames / rate, synthetic, source, captured_at)


class BodyAdapter:
    def __init__(self, agent):
        if not isinstance(agent, str) or not AGENT_ID.fullmatch(agent):
            raise ValueError('agent must be 1..48 characters [A-Za-z0-9_-]')
        self.agent = agent

    def _call(self, method, path, body=None):
        raise NotImplementedError

    def identity(self): return self._call('GET', '/v1/identity')
    def capabilities(self): return self._call('GET', '/v1/capabilities')
    def state(self): return self._call('GET', '/v1/state')
    def acquire(self, ttl): return self._call('POST', '/v1/session', {'agent': self.agent, 'ttl': ttl})
    def renew(self, ttl): return self.acquire(ttl)
    def release(self): return self._call('POST', '/v1/session/release', {})
    def stop(self): return self._call('POST', '/v1/stop', {})
    def face(self, expression): return self._call('POST', '/v1/face', {'expression': expression})
    def look(self, pan_degrees): return self._call('POST', '/v1/look', {'pan_degrees': pan_degrees})
    def speak(self, text): return self._call('POST', '/v1/speak', {'text': text})

    def play_pcm(self, clip):
        raise BodyError('unsupported', 501, 'no body PCM playback endpoint exists (blocked on WP6 audio adapter)')

    def capture_audio(self, captured_at):
        return parse_capture(self._call('POST', '/v1/audio/capture', {}), captured_at)


class SimulatorBody(BodyAdapter):
    """In-process adapter over ``Simulator.request``; no sockets, files or devices."""
    def __init__(self, simulator, agent='cammy-companion'):
        super().__init__(agent)
        self.simulator = simulator

    def _call(self, method, path, body=None):
        raw = None if body is None else json.dumps(body).encode()
        status, payload = self.simulator.request(method, path, raw, self.agent)
        if status >= 400:
            raise BodyError(error_kind(status), status, str(payload.get('error', 'request failed'))[:200])
        return payload


class SdkBody(BodyAdapter):
    """HTTP adapter over the existing SDK. Loopback gateway endpoints only.

    The companion sits above the PC gateway; it never addresses the body or a
    native bridge directly. The token is held only by the SDK client in memory.
    """
    def __init__(self, endpoint, token, agent='cammy-companion', timeout=5):
        super().__init__(agent)
        parts = urlsplit(endpoint) if isinstance(endpoint, str) else None
        try:
            port = parts.port if parts else None
        except ValueError:
            port = None
        if (not parts or parts.scheme != 'http' or parts.hostname not in LOOPBACK_HOSTS or not port
                or parts.username or parts.password or parts.path not in ('', '/') or parts.query or parts.fragment):
            raise ValueError('endpoint must be an explicit http://127.0.0.1:<port> gateway address')
        if not isinstance(token, str) or not token.strip():
            raise ValueError('an explicit non-empty token is required')
        if not (isinstance(timeout, (int, float)) and 0 < timeout <= 30):
            raise ValueError('timeout must be within (0, 30] seconds')
        sdk = str(Path(__file__).resolve().parents[2] / 'sdk')
        if sdk not in sys.path:
            sys.path.insert(0, sdk)
        from cammy import Cammy, CammyError
        self._error_type = CammyError
        self._client = Cammy(endpoint, token, agent, timeout)
        self.endpoint = self._client.endpoint

    def __repr__(self):
        return 'SdkBody(endpoint=%r, agent=%r)' % (self.endpoint, self.agent)

    def renew(self, ttl):
        try:
            return self._client.renew(ttl)
        except self._error_type as exc:
            raise BodyError(error_kind(exc.status), exc.status, str(exc)[:200]) from None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise BodyError('unavailable', 0, type(exc).__name__) from None

    def _call(self, method, path, body=None):
        try:
            if method=='GET' and path=='/v1/state':reply=self._client.state()
            elif method=='POST' and path=='/v1/session':reply=self._client.acquire(body['ttl'])
            elif method=='POST' and path=='/v1/session/release':reply=self._client.release()
            elif method=='POST' and path=='/v1/stop':reply=self._client.stop()
            elif method=='POST' and path=='/v1/face' and getattr(self,'_native_face',False):
                moods={'idle':('neutral','idle'),'listening':('neutral','show'),'thinking':('doubt','show'),
                       'speaking':('happy','show'),'muted':('sad','show'),'sleep':('sleepy','show')}
                mood,action=moods[body['expression']];reply=self._client.face(mood,action=action)
            else:reply = self._client.request(method, path, body)
            if method=='GET' and path=='/v1/capabilities':
                self._native_face=reply.get('face',{}).get('backend')=='native-gui-ipc' and reply.get('face',{}).get('available') is True
        except self._error_type as exc:
            raise BodyError(error_kind(exc.status), exc.status, str(exc)[:200]) from None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise BodyError('unavailable', 0, type(exc).__name__) from None
        if not isinstance(reply, dict):
            raise BodyError('protocol', 200, 'expected JSON object')
        return reply
