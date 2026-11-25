"""Tick-driven desktop companion orchestration above Cammy's v1 control plane.

The caller owns the loop: construct (no I/O), ``start()``, call ``tick()`` at a
chosen cadence, then ``stop()``. Nothing senses until ``enable_sensing()``.
Work tokens are (generation, epoch): a stop, halt or reconnect bumps the
generation; an interrupt or pause bumps the epoch. Results and queued body
actions whose token is no longer current are discarded, so cancelled provider
work can never later cause speech, gaze or face actions.
SPDX-License-Identifier: MIT
"""
from collections import deque
from dataclasses import dataclass
import math
import threading
import time
import unicodedata

from .body import BodyError, describe_capabilities, identity_fingerprint
from .contracts import (AgentReply, AgentRequest, CANCELLED, LIVE_UNVERIFIED, Outcome, SpeechClip, Submission,
                        SYNTHETIC, Transcript, TYPED)

STOPPED, RUNNING, PAUSED, HALTED = 'stopped', 'running', 'paused', 'halted'
IDLE, LISTENING, THINKING, SPEAKING = 'idle', 'listening', 'thinking', 'speaking'
HALT_REASONS = {'conflict': 'lease-lost', 'auth': 'body-auth-failed', 'unavailable': 'body-disconnected',
                'protocol': 'body-protocol-error'}
NO_BODY_STOP = {
    'lease-lost': '/v1/stop is not owner-scoped; sending it would stop the agent now holding the lease',
    'lease-expired': 'the body expires its own lease; any renewal that may have re-granted it was released',
    'body-disconnected': 'body unreachable; body-side cleanup is unconfirmed',
    'body-restarted': 'body event sequence went backwards; any lease a renewal re-granted was released',
}
COUNTERS = ('renewals', 'captures', 'turns', 'interrupts', 'inputs_rejected', 'inputs_rejected_stale',
            'observations_rejected_stale', 'stale_results_rejected', 'stale_actions_dropped',
            'actions_refused_unavailable', 'actions_rejected_by_body', 'provider_errors',
            'invalid_provider_output', 'output_truncated', 'retired_dropped', 'cancellations_acknowledged')


@dataclass(frozen=True)
class CompanionConfig:
    lease_ttl: int = 30             # seconds requested per lease grant (body allows 1..120)
    renew_margin: float = 10.0      # renew when this little local lease time remains
    health_interval: float = 2.0    # GET /v1/state at least this often (disconnect/restart/owner)
    max_inputs: int = 4             # bounded pending user inputs
    max_actions: int = 8            # bounded queued body actions per reply
    max_input_chars: int = 500
    max_reply_chars: int = 640
    speech_chunk_chars: int = 160   # v1 /v1/speak accepts 1..160 characters
    max_input_age: float = 30.0     # drop typed/heard input older than this when dequeued
    max_observation_age: float = 5.0  # reject a transcript of audio captured longer ago
    capture_interval: float = 1.0
    history_turns: int = 8
    max_retired: int = 8
    max_speech_seconds: float = 60.0
    barge_in: bool = True           # new input interrupts thinking/speaking

    def __post_init__(self):
        if type(self.lease_ttl) is not int or not 1 <= self.lease_ttl <= 120:
            raise ValueError('lease_ttl must be an integer 1..120')
        if not 0 < self.renew_margin < self.lease_ttl:
            raise ValueError('renew_margin must be within (0, lease_ttl)')
        for name, low, high in (('max_inputs', 1, 64), ('max_actions', 1, 64), ('max_input_chars', 1, 4000),
                                ('speech_chunk_chars', 1, 160), ('history_turns', 0, 64), ('max_retired', 1, 64)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('%s must be an integer %d..%d' % (name, low, high))
        if type(self.max_reply_chars) is not int or not self.speech_chunk_chars <= self.max_reply_chars <= 4000:
            raise ValueError('max_reply_chars must be speech_chunk_chars..4000')
        for name in ('max_input_age', 'max_observation_age', 'capture_interval', 'max_speech_seconds',
                     'health_interval'):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not 0 < value <= 3600:
                raise ValueError(name + ' must be within (0, 3600]')


def split_speech(text, limit):
    chunks, current = [], ''
    for word in text.split():
        while len(word) > limit:
            if current:
                chunks.append(current); current = ''
            chunks.append(word[:limit]); word = word[limit:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= limit:
            current += ' ' + word
        else:
            chunks.append(current); current = word
    if current:
        chunks.append(current)
    return chunks


class _Pending:
    __slots__ = ('kind', 'job', 'token', 'meta')

    def __init__(self, kind, job, token, meta=None):
        self.kind, self.job, self.token, self.meta = kind, job, token, meta or {}


class CompanionService:
    def __init__(self, body, agent, synthesizer, transcriber=None, config=None, clock=time.monotonic):
        self.body, self.agent, self.synthesizer, self.transcriber = body, agent, synthesizer, transcriber
        self.config = config or CompanionConfig()
        self.clock = clock
        self._lock = threading.RLock()
        self.run_state, self.phase, self.halt_reason = STOPPED, IDLE, None
        self._generation = self._epoch = self._sense_epoch = 0
        self._inputs, self._actions = deque(), deque()
        self._agent_job = self._synth_job = self._sense_job = None
        self._retired = deque()
        self._speech_until = None
        self._lease_deadline = self._last_sequence = None
        self._next_health = 0.0
        self._identity = self._fingerprint = None
        self._capabilities = describe_capabilities({}, {})
        self.sensing_enabled = self._sensing_on_resume = False
        self._next_capture = 0.0
        self._face_wanted = None
        self.face = self.gaze = self.body_stop = None
        self._history = deque(maxlen=2 * self.config.history_turns)
        self.captions = deque(maxlen=20)   # memory only; never written to disk
        self.spoken = deque(maxlen=20)
        self.events = deque(maxlen=64)
        self.counters = dict.fromkeys(COUNTERS, 0)

    # ----- lifecycle -------------------------------------------------------
    def start(self, sensing=False):
        """Explicit (re)connect: read identity/capabilities, acquire the lease."""
        with self._lock:
            if self.run_state in (RUNNING, PAUSED):
                return {'started': False, 'reason': 'already-running'}
            self._generation += 1
            self._reset_work()
            self.halt_reason = self.body_stop = None
            self.face = self.gaze = self._face_wanted = None
            try:
                identity, caps = self.body.identity(), self.body.capabilities()
            except BodyError as exc:
                return self._refuse_start('body-' + exc.kind)
            fingerprint = identity_fingerprint(identity)
            changed = self._fingerprint is not None and fingerprint != self._fingerprint
            if changed:
                self._event('identity-changed', previous=self._fingerprint, current=fingerprint)
            self._identity, self._fingerprint = identity, fingerprint
            self._capabilities = describe_capabilities(identity, caps)
            sent = self.clock()
            try:
                self.body.acquire(self.config.lease_ttl)
            except BodyError as exc:
                return self._refuse_start('lease-held-by-other' if exc.kind == 'conflict' else 'body-' + exc.kind)
            self._lease_deadline = sent + self.config.lease_ttl
            try:
                state = self.body.state()
            except BodyError as exc:
                return self._refuse_start('body-' + exc.kind, release=True)
            if state.get('owner') != self.body.agent:
                return self._refuse_start('lease-not-confirmed')
            self._last_sequence = state.get('event_sequence')
            self._next_health = sent + self.config.health_interval
            self.run_state, self.phase = RUNNING, IDLE
            self._event('started', generation=self._generation, identity_changed=changed)
            self._sync_face()
            result = {'started': True, 'generation': self._generation, 'identity_changed': changed,
                      'capabilities': self.capabilities()}
            if sensing:
                result['sensing'] = self.enable_sensing().reason
            return result

    def pause(self):
        with self._lock:
            if self.run_state != RUNNING:
                return False
            self._interrupt('pause')
            resume_sensing = self.sensing_enabled
            self._retire_sensing()   # pausing genuinely stops acquisition
            self._sensing_on_resume = resume_sensing
            self._inputs.clear()
            self.run_state, self.phase = PAUSED, IDLE
            self._event('paused')
            self._guard_body(self._sync_face)
            return True

    def resume(self):
        with self._lock:
            if self.run_state != PAUSED:
                return False
            self.sensing_enabled = self._sensing_on_resume and self._capabilities['sensing']['available']
            self._sensing_on_resume = False
            self.run_state, self.phase = RUNNING, self._rest_phase()
            self._next_capture = self.clock()
            self._event('resumed')
            self._guard_body(self._sync_face)
            return True

    def stop(self):
        """Cancel local work and release the lease we hold (owner-scoped)."""
        with self._lock:
            if self.run_state == STOPPED:
                return {'stopped': True, 'already_stopped': True}
            active = self.run_state in (RUNNING, PAUSED)
            self._generation += 1
            self._reset_work()
            report = {'release_sent': active, 'acknowledged': False, 'hardware_cleanup_confirmed': False}
            if active:
                try:
                    report['acknowledged'] = self.body.release().get('released') is True
                except BodyError as exc:
                    report['error'] = exc.kind
            self._settle(STOPPED, None, report)
            return {'stopped': True, **report}

    def emergency_stop(self):
        """Explicit body-wide /v1/stop, even without the lease. Not owner-scoped."""
        with self._lock:
            self._generation += 1
            self._reset_work()
            report = {'body_stop_sent': True, 'acknowledged': False, 'hardware_cleanup_confirmed': False}
            try:
                report['acknowledged'] = self.body.stop().get('stopped') is True
            except BodyError as exc:
                report['error'] = exc.kind
            self._settle(STOPPED, None, report)
            return report

    # ----- inputs and controls --------------------------------------------
    def enable_sensing(self):
        with self._lock:
            if self.run_state not in (RUNNING, PAUSED):
                return Submission(False, 'not-running')
            if self.transcriber is None:
                return Submission(False, 'no-transcriber')
            if not self._capabilities['sensing']['available']:
                return Submission(False, 'sensing-unavailable')
            self.sensing_enabled, self._next_capture = True, self.clock()
            if self.phase == IDLE:
                self.phase = LISTENING
            self._event('sensing-enabled', evidence=self._capabilities['sensing']['evidence'])
            return Submission(True, 'sensing-enabled', {'evidence': self._capabilities['sensing']['evidence']})

    def disable_sensing(self):
        with self._lock:
            was = self.sensing_enabled
            self._retire_sensing()
            if self.phase == LISTENING:
                self.phase = IDLE
            if was:
                self._event('sensing-disabled')
            return was

    def submit_text(self, text):
        with self._lock:
            if self.run_state != RUNNING:
                self.counters['inputs_rejected'] += 1
                return Submission(False, self.run_state)
            reason = self._invalid_text(text)
            if reason:
                self.counters['inputs_rejected'] += 1
                return Submission(False, reason)
            return self._enqueue_input(text.strip(), TYPED, self.clock(),
                                       {'source': 'desktop-typed', 'heard_audio': False})

    def interrupt(self, reason='requested'):
        with self._lock:
            if self.run_state not in (RUNNING, PAUSED):
                return False
            return self._interrupt(reason)

    # ----- the step --------------------------------------------------------
    def tick(self):
        with self._lock:
            now = self.clock()
            self._drain_retired()
            if self.run_state in (RUNNING, PAUSED):
                def step():
                    if not self._maintain_lease(now):
                        return
                    if self.run_state == RUNNING:
                        self._step_transcript(now)
                        self._step_agent(now)
                        self._step_inputs(now)
                        self._step_output()
                        self._step_capture()
                    self._sync_face()
                self._guard_body(step)
            return self.status()

    # ----- observation -----------------------------------------------------
    def capabilities(self):
        return {name: dict(value) for name, value in self._capabilities.items()}

    def status(self):
        with self._lock:
            now = self.clock()
            remaining = max(0.0, self._lease_deadline - now) if self._lease_deadline is not None else 0.0
            synthetic = (any(c['evidence'] == 'simulated' for c in self._capabilities.values())
                         or any(getattr(p, 'synthetic', False) for p in (self.agent, self.synthesizer, self.transcriber)))
            return {
                'run_state': self.run_state, 'phase': self.phase, 'halt_reason': self.halt_reason,
                'generation': self._generation, 'epoch': self._epoch,
                'lease': {'agent': self.body.agent, 'held': self._lease_deadline is not None,
                          'local_remaining_seconds': round(remaining, 3)},
                'sensing': {'enabled': self.sensing_enabled, 'opt_in': True,
                            'evidence': self._capabilities['sensing']['evidence']},
                'capabilities': self.capabilities(), 'physical_capabilities_verified': False,
                'face': self.face, 'gaze': self.gaze,
                'queues': {'inputs': [len(self._inputs), self.config.max_inputs],
                           'actions': [len(self._actions), self.config.max_actions],
                           'retired_jobs': len(self._retired)},
                'work': {'pending': bool(self._inputs or self._agent_job or self._synth_job
                                         or self._actions or self._speech_until is not None)},
                'counters': dict(self.counters), 'body_stop': self.body_stop,
                'identity_fingerprint': self._fingerprint, 'synthetic_session': synthetic,
            }

    # ----- internals -------------------------------------------------------
    def _event(self, event_type, **detail):
        self.events.append({**detail, 't': round(self.clock(), 3), 'type': event_type})

    def _rest_phase(self):
        return LISTENING if self.sensing_enabled else IDLE

    def _token(self):
        return (self._generation, self._epoch)

    def _invalid_text(self, text):
        if not isinstance(text, str):
            return 'not-text'
        text = text.strip()
        if not text:
            return 'empty'
        if len(text) > self.config.max_input_chars:
            return 'too-long'
        if any(unicodedata.category(ch) == 'Cc' and ch not in '\n\t' for ch in text):
            return 'control-characters'
        return None

    def _enqueue_input(self, text, evidence, at, provenance):
        if len(self._inputs) >= self.config.max_inputs:
            self.counters['inputs_rejected'] += 1
            return Submission(False, 'input-queue-full')
        if self.config.barge_in and self.phase in (THINKING, SPEAKING):
            self._interrupt('barge-in')
        self._inputs.append({'text': text, 'evidence': evidence, 'at': at, 'provenance': provenance,
                             'generation': self._generation})
        return Submission(True, 'queued', {'evidence': evidence})

    def _poll(self, pending):
        try:
            return pending.job.poll()
        except Exception as exc:  # injected provider; never let it break the loop
            return Outcome(error=type(exc).__name__)

    def _start_job(self, kind, starter, arg, token, meta=None):
        try:
            return _Pending(kind, starter(arg), token, meta)
        except Exception as exc:
            self.counters['provider_errors'] += 1
            self._event('provider-error', kind=kind, error=type(exc).__name__)
            return None

    def _retire(self, pending):
        if pending is None:
            return
        try:
            pending.job.cancel()
        except Exception:
            self.counters['provider_errors'] += 1
        if len(self._retired) >= self.config.max_retired:
            self._retired.popleft()
            self.counters['retired_dropped'] += 1
        self._retired.append(pending)

    def _drain_retired(self):
        for pending in list(self._retired):
            outcome = self._poll(pending)
            if outcome is None:
                continue
            self._retired.remove(pending)
            if outcome.error == CANCELLED:
                self.counters['cancellations_acknowledged'] += 1
            else:
                self.counters['stale_results_rejected'] += 1
                self._event('stale-result-rejected', kind=pending.kind, token=list(pending.token))

    def _retire_sensing(self):
        self.sensing_enabled = self._sensing_on_resume = False
        self._sense_epoch += 1
        self._retire(self._sense_job)
        self._sense_job = None

    def _interrupt(self, reason):
        busy = bool(self._agent_job or self._synth_job or self._actions or self._speech_until is not None)
        self._epoch += 1
        self._retire(self._agent_job); self._retire(self._synth_job)
        self._agent_job = self._synth_job = None
        dropped = len(self._actions)
        self._actions.clear()
        in_flight = self._speech_until is not None
        self._speech_until = None
        if in_flight and self.spoken:
            self.spoken[-1]['interrupted'] = True
        if busy:
            self.counters['interrupts'] += 1
            # v1 has no per-utterance cancel: an already dispatched chunk is not recalled.
            self._event('interrupted', reason=reason, dropped_actions=dropped,
                        dispatched_chunk_recalled=False if in_flight else None)
        if self.phase in (THINKING, SPEAKING):
            self.phase = self._rest_phase()
        return busy

    def _reset_work(self):
        self._interrupt('reset')
        self._retire_sensing()
        self._inputs.clear()
        self.phase = IDLE

    def _settle(self, run_state, reason, report):
        self.run_state, self.halt_reason, self.body_stop = run_state, reason, report
        self._lease_deadline = None
        self.face = self.gaze = self._face_wanted = None   # body state now unknown/released
        self._event(run_state, reason=reason)

    def _refuse_start(self, reason, release=False):
        self._halt(reason, release=release)
        return {'started': False, 'reason': reason}

    def _halt(self, reason, release=False):
        self._generation += 1
        self._reset_work()
        report = {'release_sent': release, 'acknowledged': False, 'body_stop_sent': False,
                  'hardware_cleanup_confirmed': False, 'note': NO_BODY_STOP.get(reason)}
        if release:
            try:
                report['acknowledged'] = self.body.release().get('released') is True
            except BodyError as exc:
                report['error'] = exc.kind
        self._settle(HALTED, reason, report)

    def _guard_body(self, step):
        try:
            step()
        except BodyError as exc:
            self._halt(HALT_REASONS.get(exc.kind, 'body-error'))

    def _maintain_lease(self, now):
        deadline = self._lease_deadline
        if now >= deadline:
            # Never renew after the local deadline: the body may have expired the
            # lease and stopped, and a renewal would silently re-acquire it.
            self._halt('lease-expired')
            return False
        renew = deadline - now <= self.config.renew_margin
        if not renew and now < self._next_health:
            return True
        if renew:
            self.body.renew(self.config.lease_ttl)
            if self.clock() >= deadline:
                # The body may have expired the lease first and granted this as a
                # fresh one after stopping; do not trust it, give it back.
                self._halt('lease-expired', release=True)
                return False
            self._lease_deadline = now + self.config.lease_ttl
            self.counters['renewals'] += 1
        self._next_health = now + self.config.health_interval
        state = self.body.state()
        sequence, last = state.get('event_sequence'), self._last_sequence
        if type(sequence) is int and type(last) is int and sequence < last:
            self._halt('body-restarted', release=renew)
            return False
        if state.get('owner') != self.body.agent:
            self._halt('lease-lost')
            return False
        self._last_sequence = sequence
        return True

    def _refuse(self, feature, reason='capability-unavailable'):
        self.counters['actions_refused_unavailable'] += 1
        self._event('action-refused', feature=feature, reason=reason)
        return None

    def _act(self, feature, call, *args):
        """Gate a body action on its capability; 501/404 marks it unavailable."""
        if not self._capabilities[feature]['available']:
            return self._refuse(feature)
        try:
            return call(*args)
        except BodyError as exc:
            if exc.kind == 'unsupported':
                self._capabilities[feature].update(available=False, evidence='unavailable',
                                                   note='body refused with %s this session' % exc.status)
                return self._refuse(feature, 'body-unsupported')
            if exc.kind == 'invalid':
                self.counters['actions_rejected_by_body'] += 1
                self._event('action-rejected', feature=feature, status=exc.status)
                return None
            raise

    def _step_transcript(self, now):
        pending = self._sense_job
        outcome = self._poll(pending) if pending else None
        if outcome is None:
            return
        self._sense_job = None
        if pending.token != (self._generation, self._sense_epoch):
            self.counters['stale_results_rejected'] += 1
            return
        clip = pending.meta['clip']
        transcript = outcome.value
        if outcome.error or not isinstance(transcript, Transcript):
            self.counters['provider_errors' if outcome.error else 'invalid_provider_output'] += 1
            return
        if not transcript.text:
            return
        if now - clip.captured_at > self.config.max_observation_age:
            self.counters['observations_rejected_stale'] += 1
            self._event('observation-rejected', reason='stale', age=round(now - clip.captured_at, 3))
            return
        if self._invalid_text(transcript.text):
            self.counters['invalid_provider_output'] += 1
            return
        synthetic = clip.synthetic or transcript.synthetic or getattr(self.transcriber, 'synthetic', True)
        self._enqueue_input(transcript.text.strip(), SYNTHETIC if synthetic else LIVE_UNVERIFIED, clip.captured_at,
                            {'source': 'body:/v1/audio/capture', 'audio_synthetic': clip.synthetic,
                             'audio_source': clip.source, 'transcriber': getattr(self.transcriber, 'name', '?'),
                             'heard_audio': False})

    def _step_agent(self, now):
        pending = self._agent_job
        outcome = self._poll(pending) if pending else None
        if outcome is None:
            return
        self._agent_job = None
        self.phase = self._rest_phase()
        if pending.token != self._token():
            self.counters['stale_results_rejected'] += 1
            return
        reply = outcome.value
        if outcome.error or not isinstance(reply, AgentReply) or not isinstance(reply.text, str):
            self.counters['provider_errors' if outcome.error else 'invalid_provider_output'] += 1
            self._event('agent-failed', error=outcome.error)
            return
        text = ' '.join(reply.text.split())
        truncated = len(text) > self.config.max_reply_chars
        if truncated:
            text = text[:self.config.max_reply_chars]
            self.counters['output_truncated'] += 1
        evidence = SYNTHETIC if getattr(self.agent, 'synthetic', True) else LIVE_UNVERIFIED
        if text:
            self.captions.append({'role': 'companion', 'text': text, 'evidence': evidence, 'truncated': truncated})
            self._history.append(('companion', text))
        actions = []
        pan = reply.gaze_pan_degrees
        if pan is not None:
            if isinstance(pan, (int, float)) and not isinstance(pan, bool) and math.isfinite(pan):
                actions.append(('look', float(pan)))
            else:
                self.counters['invalid_provider_output'] += 1
        actions += [('speak', chunk) for chunk in split_speech(text, self.config.speech_chunk_chars)]
        if len(actions) > self.config.max_actions:
            self.counters['output_truncated'] += 1
            actions = actions[:self.config.max_actions]
        token = self._token()
        self._actions.extend((kind, arg, token) for kind, arg in actions)
        if actions:
            self.phase = SPEAKING

    def _step_inputs(self, now):
        if self._agent_job or self.phase in (THINKING, SPEAKING):
            return
        while self._inputs:
            item = self._inputs.popleft()
            if item['generation'] != self._generation or now - item['at'] > self.config.max_input_age:
                self.counters['inputs_rejected_stale'] += 1
                self._event('input-rejected', reason='stale')
                continue
            self._epoch += 1
            request = AgentRequest(item['text'], item['evidence'], tuple(self._history),
                                   {name: c['available'] for name, c in self._capabilities.items()})
            self._history.append(('user', item['text']))
            self.captions.append({'role': 'user', 'text': item['text'], 'evidence': item['evidence'],
                                  'provenance': item['provenance']})
            pending = self._start_job('agent', self.agent.start, request, self._token())
            if pending:
                self._agent_job, self.phase = pending, THINKING
                self.counters['turns'] += 1
            return

    def _step_output(self):
        if self._speech_until is not None:
            if self.clock() < self._speech_until:
                return
            self._speech_until = None
        for _ in range(2 * self.config.max_actions + 2):
            if self._synth_job:
                pending = self._synth_job
                outcome = self._poll(pending)
                if outcome is None:
                    return
                self._synth_job = None
                if pending.token != self._token():
                    self.counters['stale_results_rejected'] += 1
                elif outcome.error or not isinstance(outcome.value, SpeechClip):
                    self.counters['provider_errors' if outcome.error else 'invalid_provider_output'] += 1
                elif self._dispatch_speech(outcome.value):
                    return
                continue
            if not self._actions:
                if self.phase == SPEAKING:
                    self.phase = self._rest_phase()
                return
            kind, arg, token = self._actions.popleft()
            if token != self._token():
                self.counters['stale_actions_dropped'] += 1
            elif kind == 'look':
                reply = self._act('gaze', self.body.look, arg)
                if reply is not None:
                    self.gaze = {'commanded_pan_degrees': arg, 'measured_pan_degrees': None,
                                 'body_reported_measured': reply.get('measured'),
                                 'simulated': reply.get('simulation') is True}
            elif not self._capabilities['speech']['available']:
                self._refuse('speech')
            else:
                self._synth_job = self._start_job('speech', self.synthesizer.start, arg, token, {'text': arg})

    def _dispatch_speech(self, clip):
        if clip.pcm is not None:
            try:
                self.body.play_pcm(clip)
                reply = {}
            except BodyError as exc:
                if exc.kind != 'unsupported':
                    raise
                return bool(self._refuse('speech', 'pcm-playback-unavailable; fixture PCM not played'))
        else:
            reply = self._act('speech', self.body.speak, clip.text)
            if reply is None:
                return False
        duration = clip.duration_s
        if not isinstance(duration, (int, float)) or not 0 <= duration <= self.config.max_speech_seconds:
            self.counters['invalid_provider_output'] += 1
            duration = self.config.max_speech_seconds
        self.spoken.append({'text': clip.text, 'audio_played': reply.get('audio_played'),
                            'body_simulated': reply.get('simulation') is True, 'synthetic': clip.synthetic,
                            'duration_evidence': 'provider-estimate', 'interrupted': False})
        self._speech_until = self.clock() + duration
        return True

    def _step_capture(self):
        now = self.clock()
        if (not self.sensing_enabled or self.phase not in (IDLE, LISTENING) or self._sense_job
                or self._agent_job or self._inputs or now < self._next_capture):
            return
        self._next_capture = now + self.config.capture_interval
        clip = self._act('sensing', self.body.capture_audio, now)
        if clip is None:
            if not self._capabilities['sensing']['available']:
                self._retire_sensing()
                self.phase = IDLE
            return
        self.counters['captures'] += 1
        self.phase = LISTENING
        self._sense_job = self._start_job('transcript', self.transcriber.start, clip,
                                          (self._generation, self._sense_epoch), {'clip': clip})

    def _sync_face(self):
        wanted = 'muted' if self.run_state == PAUSED else self.phase
        if wanted == self._face_wanted:
            return
        self._face_wanted = wanted
        reply = self._act('face', self.body.face, wanted)
        if reply is not None:
            self.face = {'commanded': wanted, 'body_reported': reply.get('expression'),
                         'measured': None, 'simulated': reply.get('simulation') is True}
