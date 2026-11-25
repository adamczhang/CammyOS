"""Finite listening windows above CompanionService. No input device is opened.

The caller owns the tick loop and serializes all UI/button/agent controls through
this controller. Button events must already be authenticated and ordered by the
body adapter. This module does not intercept the stock record button or implement
streaming recognition, VAD, echo cancellation, or physical microphone cutoff.
"""
from dataclasses import dataclass
import math
import threading

from .service import RUNNING, PAUSED


@dataclass(frozen=True)
class EngagementConfig:
    idle_seconds: float = 60.0
    max_session_seconds: float = 600.0
    max_hold_seconds: float = 30.0

    def __post_init__(self):
        for name in ('idle_seconds', 'max_session_seconds', 'max_hold_seconds'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(name + ' must be finite and positive')
        if max(self.idle_seconds, self.max_hold_seconds) > self.max_session_seconds:
            raise ValueError('idle and hold limits must fit within the session limit')


class EngagementController:
    """Idle is silent; press or agent wake enables a bounded conversation window.

    Holding enables the existing finite-clip sensing pipeline. Release keeps it
    enabled for follow-up turns; it is not an end-of-utterance streaming RPC.
    At present this pipeline suppresses capture while thinking/speaking.
    """

    def __init__(self, service, config=None):
        self.service = service
        self.config = config or EngagementConfig()
        self.clock = service.clock
        self._lock = threading.RLock()
        self.state, self.reason = 'stopped', 'not-started'
        self.generation = 0
        self._service_generation = None
        self._idle_at = self._session_at = self._hold_at = None
        self._release_required = False

    def start(self):
        with self._lock:
            result = self.service.start(sensing=False)
            if result.get('started'):
                self._service_generation = self.service.status()['generation']
                self._clear('idle', 'started')
                self._release_required = False
            return result

    def stop(self, emergency=False):
        with self._lock:
            result = self.service.emergency_stop() if emergency else self.service.stop()
            self._clear('stopped', 'emergency-stop' if emergency else 'stop')
            self._release_required = True
            return result

    def _clear(self, state, reason):
        self.generation += 1
        self.state, self.reason = state, reason
        self._idle_at = self._session_at = self._hold_at = None

    def _ready(self):
        status = self.service.status()
        return (self.state in ('idle', 'held', 'open') and status['run_state'] == RUNNING
                and status['generation'] == self._service_generation)

    def _open(self, source, held=False):
        if not self._ready():
            return {'accepted': False, 'reason': 'not-ready'}
        sensing = self.service.enable_sensing()
        if not sensing.accepted:
            return {'accepted': False, 'reason': sensing.reason}
        now = self.clock()
        if self.state == 'idle':
            self.generation += 1
            self._session_at = now + self.config.max_session_seconds
        self.state, self.reason = ('held' if held else 'open'), source
        self._idle_at = now + self.config.idle_seconds
        self._hold_at = now + self.config.max_hold_seconds if held else None
        return {'accepted': True, 'generation': self.generation, 'source': source}

    def press(self):
        with self._lock:
            if self._release_required:
                return {'accepted': False, 'reason': 'release-required'}
            if self.state == 'held':
                return {'accepted': False, 'reason': 'already-held'}
            return self._open('push-to-talk', held=True)

    def release(self):
        with self._lock:
            self._release_required = False
            if self.state != 'held' or not self._ready():
                return {'accepted': False, 'reason': 'not-held'}
            self.state, self.reason, self._hold_at = 'open', 'button-released', None
            self._idle_at = self.clock() + self.config.idle_seconds
            return {'accepted': True, 'generation': self.generation}

    def agent_wake(self):
        with self._lock:
            if self.state == 'held':
                return {'accepted': False, 'reason': 'already-held'}
            return self._open('agent-wake')

    def mute(self):
        with self._lock:
            if self.state in ('stopped', 'fault'):
                return False
            self.service.disable_sensing()
            self.service.pause()
            self._clear('muted', 'muted')
            self._release_required = True
            return True

    def unmute(self):
        with self._lock:
            if self.state != 'muted' or self.service.run_state != PAUSED:
                return False
            if not self.service.resume():
                return False
            self._clear('idle', 'unmuted')
            # A button held across mute/unmute is not a fresh wake gesture.
            return True

    def submit_text(self, text):
        """Typed requests remain possible in idle without opening the microphone."""
        with self._lock:
            result = self.service.submit_text(text)
            if result.accepted and self.state in ('held', 'open'):
                self._idle_at = self.clock() + self.config.idle_seconds
            return result

    def _close(self, reason):
        # Disable first: pause/resume must not restore a previously enabled mic.
        self.service.disable_sensing()
        if self.service.run_state == RUNNING:
            self.service.pause()  # retires provider results and clears queued actions
            if self.service.run_state == PAUSED:
                self.service.resume()
        self._clear('idle', reason)

    def tick(self):
        with self._lock:
            now = self.clock()
            before = self.service.status()
            if self.state in ('held', 'open'):
                if now >= self._session_at:
                    self._release_required = self.state == 'held'
                    self._close('session-limit')
                elif self.state == 'held' and now >= self._hold_at:
                    self._release_required = True
                    self._close('hold-limit')
                elif self.state == 'open' and now >= self._idle_at and not before['work']['pending']:
                    self._close('idle-timeout')
            # Idle/muted cannot inherit an accidentally enabled sensing flag.
            if self.state not in ('held', 'open'):
                self.service.disable_sensing()
            after = self.service.tick()
            if self.state not in ('stopped', 'fault') and (
                    after['run_state'] not in (RUNNING, PAUSED)
                    or after['generation'] != self._service_generation):
                self.service.disable_sensing()
                self._clear('fault', after['halt_reason'] or 'service-session-changed')
                self._release_required = True
            elif self.state in ('held', 'open'):
                if not after['sensing']['enabled']:
                    self._close('sensing-unavailable')
                elif before['work']['pending'] or after['work']['pending'] or (
                        after['counters']['turns'] != before['counters']['turns']):
                    self._idle_at = now + self.config.idle_seconds
            return self.status()

    def status(self):
        with self._lock:
            now = self.clock()
            service = self.service.status()
            return {'engagement': self.state, 'reason': self.reason,
                    'generation': self.generation,
                    'listening_requested': self.state in ('held', 'open') and service['sensing']['enabled'],
                    'idle_remaining_seconds': max(0.0, self._idle_at - now) if self._idle_at else 0.0,
                    'session_remaining_seconds': max(0.0, self._session_at - now) if self._session_at else 0.0,
                    'release_required': self._release_required,
                    'physical_record_button_integrated': False,
                    'hardware_cleanup_confirmed': False,
                    'continuous_duplex': False, 'service': service}
