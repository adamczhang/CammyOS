"""WP7 companion orchestration tests.

Evidence class: in-process simulator, scripted fake providers and one ephemeral
127.0.0.1 gateway fixture started and stopped by the test. No camera, ADB,
device, microphone, speaker or external network is touched.
"""
import contextlib
import io
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sdk'))
sys.path.insert(0, str(ROOT / 'host'))
import cammy_simulator  # noqa: E402
from cammy_simulator import Simulator  # noqa: E402
from cammy_host import Gateway, make_server  # noqa: E402
from companion import (AgentReply, BodyError, CompanionConfig, CompanionService, HALTED, IDLE,  # noqa: E402
                       LISTENING, PAUSED, RUNNING, SPEAKING, STOPPED, SdkBody, SimulatorBody, THINKING,
                       describe_capabilities, split_speech)
from companion import demo  # noqa: E402
from companion.contracts import Outcome  # noqa: E402
from companion.fakes import (EstimatedTextSpeech, FixturePcmSpeech, ManualClock, ScriptedAgent,  # noqa: E402
                             ScriptedTranscriber)

AGENT = 'companion-test'


class Harness(unittest.TestCase):
    def make(self, profile='companion', replies=(), delay=1.0, ignore_cancel=False, heard=None, heard_delay=0.3,
             synthesizer=None, config=None, body_type=SimulatorBody):
        self.clock = ManualClock()
        self.sim = Simulator(profile, clock=self.clock)
        self.body = body_type(self.sim, AGENT)
        self.agent = ScriptedAgent(self.clock, replies, delay=delay, ignore_cancel=ignore_cancel)
        self.speech = (synthesizer or EstimatedTextSpeech)(self.clock)
        self.transcriber = None if heard is None else ScriptedTranscriber(self.clock, heard, delay=heard_delay)
        self.service = CompanionService(self.body, self.agent, self.speech, self.transcriber,
                                        config or CompanionConfig(lease_ttl=10, renew_margin=4), self.clock)
        return self.service

    def run_for(self, seconds, step=0.25):
        status = None
        for _ in range(round(seconds / step)):
            self.clock.advance(step)
            status = self.service.tick()
        return status

    def sim_state(self):
        return self.sim.request('GET', '/v1/state', None, 'observer')[1]

    def mark(self):
        return self.sim.sequence

    def body_events(self, after):
        return [e['type'] for e in self.sim.events if e['sequence'] > after]

    def speak_turn(self, text='hello'):
        self.assertTrue(self.service.submit_text(text).accepted)
        self.service.tick()
        self.assertEqual(self.service.phase, THINKING)


class LifecycleTests(Harness):
    def test_construction_and_stopped_service_do_no_body_io(self):
        body = mock.Mock(spec=SimulatorBody)
        body.agent = AGENT
        clock = ManualClock()
        service = CompanionService(body, ScriptedAgent(clock), EstimatedTextSpeech(clock), None, None, clock)
        service.tick(); service.status()
        self.assertFalse(service.submit_text('hi').accepted)
        self.assertEqual(service.enable_sensing().reason, 'not-running')
        self.assertFalse(service.interrupt())
        self.assertEqual(body.method_calls, [])
        status = service.status()
        self.assertEqual((status['run_state'], status['sensing']['enabled']), (STOPPED, False))
        self.assertFalse(status['physical_capabilities_verified'])

    def test_typed_turn_coordinates_face_gaze_and_text_speech(self):
        service = self.make(replies=[AgentReply('Hello there.', gaze_pan_degrees=20)])
        started = service.start()
        self.assertTrue(started['started'])
        self.assertEqual({c['evidence'] for c in started['capabilities'].values()}, {'simulated'})
        self.assertFalse(any(c['physical_verified'] for c in started['capabilities'].values()))
        self.assertEqual(self.sim_state()['owner'], AGENT)
        self.assertEqual(self.sim_state()['face'], 'idle')
        self.speak_turn('hi')
        self.assertEqual(self.sim_state()['face'], 'thinking')
        self.clock.advance(1.0); service.tick()
        self.assertEqual(service.phase, SPEAKING)
        state = self.sim_state()
        self.assertEqual((state['face'], state['commanded_pan_degrees'], state['last_speech_text']),
                         ('speaking', 20, 'Hello there.'))
        self.assertEqual(service.gaze, {'commanded_pan_degrees': 20.0, 'measured_pan_degrees': None,
                                        'body_reported_measured': False, 'simulated': True})
        self.assertEqual(service.face['measured'], None)
        spoken = service.spoken[-1]
        self.assertEqual((spoken['audio_played'], spoken['body_simulated'], spoken['duration_evidence']),
                         (False, True, 'provider-estimate'))
        self.run_for(2)
        self.assertEqual((service.phase, self.sim_state()['face']), (IDLE, 'idle'))
        user, reply = list(service.captions)
        self.assertEqual((user['evidence'], user['provenance']['heard_audio']), ('typed', False))
        self.assertEqual(reply['evidence'], 'synthetic')
        request = self.agent.requests[0]
        self.assertEqual(request.input_evidence, 'typed')
        self.assertEqual(request.capabilities, {'speech': True, 'face': True, 'gaze': True, 'sensing': True})
        self.assertTrue(service.status()['synthetic_session'])

    def test_stop_releases_lease_and_does_not_claim_hardware_cleanup(self):
        service = self.make(delay=2, ignore_cancel=True)
        service.start(); self.speak_turn()
        before = self.mark()
        report = service.stop()
        self.assertEqual(report, {'stopped': True, 'release_sent': True, 'acknowledged': True,
                                  'hardware_cleanup_confirmed': False})
        self.assertIsNone(self.sim_state()['owner'])
        self.assertIn('session.released', self.body_events(before))
        self.assertNotIn('stop.requested', self.body_events(before))
        self.clock.advance(3); service.tick()
        self.assertEqual(service.counters['stale_results_rejected'], 1)
        self.assertEqual(self.sim_state()['last_speech_text'], '')
        self.assertTrue(service.stop()['already_stopped'])
        emergency = service.emergency_stop()
        self.assertEqual((emergency['body_stop_sent'], emergency['acknowledged'],
                          emergency['hardware_cleanup_confirmed']), (True, True, False))
        self.assertIn('stop.requested', self.body_events(before))

    def test_emergency_stop_and_delayed_synthesizer_and_queued_work_cleared(self):
        def make_speech(clock):
            speech = EstimatedTextSpeech(clock, delay=2.0)

            def start_ignoring_cancel(text):
                speech.texts.append(text)
                from companion.contracts import SpeechClip
                from companion.fakes import ClockJob
                clip = SpeechClip(text, round(0.3 + len(text) / speech.cps, 3))
                return ClockJob(clock, clock() + speech.delay, Outcome(clip), ignore_cancel=True)

            speech.start = start_ignoring_cancel
            return speech

        service = self.make(
            replies=[AgentReply('First chunk. ' + 'word ' * 40, gaze_pan_degrees=15),
                     AgentReply('Second turn. ' + 'word ' * 40, gaze_pan_degrees=-10)],
            delay=0.2,
            ignore_cancel=True,
            heard=['queued voice'],
            heard_delay=2.0,
            synthesizer=make_speech,
            config=CompanionConfig(lease_ttl=10, renew_margin=4, barge_in=False),
        )
        service.transcriber.ignore_cancel = True
        service.start(sensing=True)
        service.tick()  # capture audio clip and start _sense_job
        self.assertIsNotNone(service._sense_job)
        self.speak_turn('turn one')
        self.assertTrue(service.submit_text('queued typed').accepted)
        self.clock.advance(0.25)
        service.tick()  # agent finishes; look(15) runs; synth_job starts for chunk 1; chunk 2 remains in _actions
        self.assertIsNotNone(service._synth_job)
        self.assertIsNotNone(service._sense_job)
        self.assertGreater(service.status()['queues']['actions'][0], 0)
        self.assertEqual(service.status()['queues']['inputs'][0], 1)

        before = self.mark()
        report = service.emergency_stop()
        self.assertEqual(
            report,
            {'body_stop_sent': True, 'acknowledged': True, 'hardware_cleanup_confirmed': False},
        )
        self.assertEqual(service.run_state, STOPPED)
        self.assertEqual(service.status()['queues']['inputs'][0], 0)
        self.assertEqual(service.status()['queues']['actions'][0], 0)
        self.assertIsNone(service.face)
        self.assertIsNone(service.gaze)
        self.assertEqual(self.body_events(before), ['stop.requested'])

        after_stop = self.mark()
        self.run_for(3.0)  # stubborn synth and transcriber finish while STOPPED
        self.assertEqual(self.body_events(after_stop), [])
        self.assertEqual(service.counters['stale_results_rejected'], 2)
        self.assertEqual(len(service.spoken), 0)

        # Restart/reacquire and verify a stubborn synthesizer from before stop+restart cannot act
        self.assertTrue(service.start()['started'])
        self.speak_turn('turn two')
        self.clock.advance(0.25)
        service.tick()  # synth_job in flight again
        self.assertIsNotNone(service._synth_job)
        service.stop()
        self.assertTrue(service.start()['started'])
        after_restart = self.mark()
        self.run_for(3.0)
        for kind in ('speech.simulated', 'gimbal.commanded', 'face.changed'):
            self.assertNotIn(kind, self.body_events(after_restart))
        self.assertEqual(service.counters['stale_results_rejected'], 3)
        self.assertEqual(len(service.spoken), 0)

    def test_lease_conflict_and_expiry_while_speaking_or_pending_reject_delayed_results(self):
        service = self.make(
            replies=[
                AgentReply('Chunk one. ' + 'word ' * 40, gaze_pan_degrees=10),
                AgentReply('Should never arrive.', gaze_pan_degrees=-20),
            ],
            delay=0.2,
            ignore_cancel=True,
            config=CompanionConfig(lease_ttl=4, renew_margin=1, barge_in=False),
        )
        service.start()
        self.speak_turn('first')
        self.clock.advance(0.25)
        service.tick()  # chunk 1 spoken; chunk 2 queued in _actions
        self.assertEqual(service.phase, SPEAKING)
        self.assertEqual(len(service.spoken), 1)
        self.assertGreater(service.status()['queues']['actions'][0], 0)
        self.assertTrue(service.submit_text('queued while speaking').accepted)

        # Another owner takes the lease while chunk 1 is in flight and chunk 2 + input are queued
        self.sim.request('POST', '/v1/stop', b'{}', 'other-owner')
        self.sim.request('POST', '/v1/session', b'{"agent": "other-owner", "ttl": 60}', 'other-owner')
        before = self.mark()
        self.run_for(2.5)  # health check or next chunk hits lease-lost (409 / owner mismatch)
        self.assertEqual((service.run_state, service.halt_reason), (HALTED, 'lease-lost'))
        self.assertEqual(service.status()['queues']['actions'][0], 0)
        self.assertEqual(service.status()['queues']['inputs'][0], 0)
        self.assertTrue(service.spoken[-1]['interrupted'])
        self.assertEqual(self.body_events(before), [])
        self.assertEqual(self.sim_state()['owner'], 'other-owner')

        # Release other-owner, reacquire, start a slow turn, then let local lease expire mid-thinking
        self.sim.request('POST', '/v1/session/release', b'{}', 'other-owner')
        self.agent.delay = 5.0
        self.assertTrue(service.start()['started'])
        self.speak_turn('slow turn before expiry')
        before_expiry = self.mark()
        self.clock.advance(4.5)  # past lease_ttl=4
        service.tick()
        self.assertEqual((service.run_state, service.halt_reason), (HALTED, 'lease-expired'))
        self.assertEqual(self.body_events(before_expiry), [])
        self.clock.advance(1.0)
        service.tick()  # stubborn agent finishes at t+5.5
        self.assertEqual(self.body_events(before_expiry), [])
        self.assertEqual(service.counters['stale_results_rejected'], 1)
        self.assertEqual(len(service.spoken), 1)

    def test_pause_mutes_stops_capture_keeps_lease_and_resume_restores_opt_in(self):
        service = self.make(heard=[], replies=['One two three four five six.'])
        service.start()
        self.assertTrue(service.enable_sensing().accepted)
        self.run_for(1)
        captures = service.counters['captures']
        self.assertGreater(captures, 0)
        self.speak_turn(); self.clock.advance(1); service.tick()
        self.assertEqual(service.phase, SPEAKING)
        self.assertTrue(service.pause())
        status = service.status()
        self.assertEqual((status['run_state'], status['queues']['actions'][0], status['sensing']['enabled']),
                         (PAUSED, 0, False))
        self.assertEqual(self.sim_state()['face'], 'muted')
        self.assertFalse(service.submit_text('ignored while paused').accepted)
        self.run_for(25)   # spans two lease renewals (ttl 10, margin 4)
        self.assertEqual(service.counters['captures'], captures)
        self.assertGreaterEqual(service.counters['renewals'], 2)
        self.assertEqual(self.sim_state()['owner'], AGENT)
        self.assertTrue(service.resume())
        self.assertEqual((service.phase, service.sensing_enabled), (LISTENING, True))
        self.run_for(1)
        self.assertGreater(service.counters['captures'], captures)
        self.assertEqual(self.sim_state()['face'], 'listening')


class LeaseAndFaultTests(Harness):
    def test_renewal_keeps_lease_and_no_renewal_after_local_deadline(self):
        service = self.make(config=CompanionConfig(lease_ttl=3, renew_margin=1))
        service.start()
        self.run_for(10, step=0.5)
        self.assertGreaterEqual(service.counters['renewals'], 4)
        self.assertEqual((service.run_state, self.sim_state()['owner']), (RUNNING, AGENT))
        before = self.mark()
        self.clock.advance(5)   # the host stalled past its local lease deadline
        service.tick()
        self.assertEqual((service.run_state, service.halt_reason), (HALTED, 'lease-expired'))
        events = self.body_events(before)
        self.assertNotIn('session.acquired', events); self.assertNotIn('session.renewed', events)
        self.assertIsNone(self.sim_state()['owner'])

    def test_latency_renewal_landing_after_deadline_is_released_not_trusted(self):
        service = self.make(delay=4, ignore_cancel=True, config=CompanionConfig(lease_ttl=3, renew_margin=1))
        service.start(); self.speak_turn()
        self.sim.settings({'latency_ms': 1500})
        before = self.mark()   # after the fault-change event
        with mock.patch.object(cammy_simulator.time, 'sleep', side_effect=self.clock.advance):
            self.clock.advance(2.2)
            service.tick()
        self.assertEqual((service.run_state, service.halt_reason), (HALTED, 'lease-expired'))
        self.assertEqual(self.body_events(before), ['session.expired', 'session.acquired', 'session.released'])
        self.assertEqual((service.body_stop['release_sent'], service.body_stop['acknowledged']), (True, True))
        self.sim.settings({'latency_ms': 0})
        self.run_for(3)
        self.assertEqual(service.counters['stale_results_rejected'], 1)
        self.assertEqual(self.sim_state()['last_speech_text'], '')

    def test_moderate_latency_still_completes_a_turn(self):
        service = self.make(replies=['Short answer.'])
        service.start()
        self.sim.settings({'latency_ms': 200})
        with mock.patch.object(cammy_simulator.time, 'sleep', side_effect=self.clock.advance):
            self.speak_turn()
            self.run_for(4)
        self.assertEqual((service.run_state, self.sim_state()['last_speech_text']), (RUNNING, 'Short answer.'))

    def test_lease_taken_by_other_halts_without_body_stop_and_no_takeover(self):
        service = self.make(replies=['first', 'second'])
        service.start()
        before = self.mark()
        self.sim.request('POST', '/v1/stop', b'{}', 'other')
        self.sim.request('POST', '/v1/session', b'{"agent": "other", "ttl": 60}', 'other')
        self.assertTrue(service.submit_text('hi').accepted)
        service.tick()   # the thinking-face command meets 409
        self.assertEqual((service.run_state, service.halt_reason), (HALTED, 'lease-lost'))
        self.assertEqual(self.agent.jobs[0].cancelled, True)
        self.assertIn('not owner-scoped', service.body_stop['note'])
        self.assertEqual(self.body_events(before).count('stop.requested'), 1)   # only the other agent's
        self.assertEqual(self.sim_state()['owner'], 'other')
        refused = service.start()
        self.assertEqual(refused, {'started': False, 'reason': 'lease-held-by-other'})
        self.assertEqual((service.run_state, self.sim_state()['owner']), (HALTED, 'other'))

    def test_disconnect_mid_turn_then_reconnect_rejects_stale_result(self):
        service = self.make(delay=5, ignore_cancel=True)
        service.start(); self.speak_turn()
        generation = service.status()['generation']
        self.sim.settings({'disconnected': True})
        self.run_for(2.25)
        self.assertEqual((service.run_state, service.halt_reason), (HALTED, 'body-disconnected'))
        self.assertEqual((service.body_stop['body_stop_sent'], service.body_stop['hardware_cleanup_confirmed']),
                         (False, False))
        self.assertIn('unconfirmed', service.body_stop['note'])
        self.assertEqual(service.start(), {'started': False, 'reason': 'body-unavailable'})
        self.sim.settings({'disconnected': False})
        restarted = service.start()
        self.assertTrue(restarted['started']); self.assertFalse(restarted['identity_changed'])
        self.assertGreater(service.status()['generation'], generation)
        before = self.mark()
        self.run_for(4)   # the stubborn provider finishes the old turn now
        self.assertEqual(service.counters['stale_results_rejected'], 1)
        self.assertNotIn('speech.simulated', self.body_events(before))
        self.assertEqual(service.phase, IDLE)

    def test_body_restart_and_identity_change_are_detected(self):
        service = self.make()
        service.start(); self.run_for(1)
        self.body.simulator = Simulator('companion', clock=self.clock)   # body rebooted: sequence restarts
        self.run_for(2.25)
        self.assertEqual((service.run_state, service.halt_reason), (HALTED, 'body-restarted'))
        self.assertFalse(service.start()['identity_changed'])
        self.run_for(1)
        self.body.simulator = Simulator('stock', clock=self.clock)
        self.run_for(2.25)
        self.assertEqual(service.halt_reason, 'body-restarted')
        changed = service.start()
        self.assertTrue(changed['identity_changed'])
        self.assertFalse(any(c['available'] for c in changed['capabilities'].values()))


class InterruptTests(Harness):
    def test_explicit_interrupt_during_thinking_rejects_late_result(self):
        service = self.make(delay=2, ignore_cancel=True)
        service.start(); self.speak_turn()
        self.assertTrue(service.interrupt())
        self.assertEqual((service.phase, self.agent.jobs[0].cancelled), (IDLE, True))
        before = self.mark()
        self.run_for(3)
        self.assertEqual(service.counters['stale_results_rejected'], 1)
        self.assertNotIn('speech.simulated', self.body_events(before))
        self.assertEqual(self.sim_state()['face'], 'idle')

    def test_token_layer_alone_blocks_work_that_escaped_retirement(self):
        """White-box: retiring handles is the primary guard; tokens are the second layer."""
        service = self.make(replies=['must not be spoken'], delay=1)
        service.start(); self.speak_turn()
        service._epoch += 1          # as if a handle escaped retirement during a cancel
        service._actions.append(('speak', 'stale queued chunk', (service._generation, service._epoch - 1)))
        before = self.mark()
        self.run_for(2)
        self.assertEqual((service.counters['stale_results_rejected'], service.counters['stale_actions_dropped']),
                         (1, 1))
        self.assertNotIn('speech.simulated', self.body_events(before))

    def test_honoured_cancellation_is_acknowledged_not_counted_stale(self):
        service = self.make(delay=2)
        service.start(); self.speak_turn()
        service.interrupt(); self.run_for(3)
        self.assertEqual((service.counters['cancellations_acknowledged'],
                          service.counters['stale_results_rejected']), (1, 0))

    def test_barge_in_while_speaking_drops_queued_chunks(self):
        long = ' '.join('word%03d' % n for n in range(60))   # > 2 speech chunks
        service = self.make(replies=[long, 'Newer answer.'])
        service.start(); self.speak_turn('first'); self.clock.advance(1); service.tick()
        self.assertEqual(service.phase, SPEAKING)
        self.assertEqual(len(service.spoken), 1)
        self.assertGreater(service.status()['queues']['actions'][0], 0)
        self.assertTrue(service.submit_text('second').accepted)
        self.assertEqual(service.status()['queues']['actions'][0], 0)
        self.assertTrue(service.spoken[-1]['interrupted'])
        self.assertEqual(service.counters['interrupts'], 1)
        self.run_for(5)
        self.assertEqual([s['text'] for s in service.spoken][1:], ['Newer answer.'])
        self.assertEqual(self.sim_state()['last_speech_text'], 'Newer answer.')
        interrupted = [e for e in service.events if e['type'] == 'interrupted']
        self.assertFalse(interrupted[0]['dispatched_chunk_recalled'])


class CapabilityTests(Harness):
    def test_stock_profile_refuses_body_actions_but_keeps_captions(self):
        service = self.make(profile='stock', replies=[AgentReply('Caption only.', gaze_pan_degrees=5)], heard=[])
        before = self.mark()
        self.assertTrue(service.start()['started'])
        self.assertEqual(service.enable_sensing().reason, 'sensing-unavailable')
        self.speak_turn(); self.run_for(3)
        events = self.body_events(before)
        for kind in ('face.changed', 'gimbal.commanded', 'speech.simulated', 'audio.synthetic_capture'):
            self.assertNotIn(kind, events)
        self.assertEqual(service.captions[-1]['text'], 'Caption only.')
        self.assertEqual(len(service.spoken), 0)
        self.assertGreaterEqual(service.counters['actions_refused_unavailable'], 3)

    def test_capability_evidence_never_upgrades_to_physical(self):
        # Shape of the real portal (src/cammy_portal.c) today: adapters unavailable.
        portal = {'api_version': '1', 'control_leases': True, 'preview': {'available': True},
                  **{k: {'available': False} for k in ('audio_capture', 'speech', 'face', 'gimbal')}}
        self.assertEqual({c['evidence'] for c in describe_capabilities({'os': 'CammyOS'}, portal).values()},
                         {'unavailable'})
        claimed = describe_capabilities({'os': 'CammyOS'}, {'speech': {'available': True}})['speech']
        self.assertEqual((claimed['evidence'], claimed['physical_verified']), ('body-reported-unverified', False))
        simulated = describe_capabilities({'simulation': True}, {'speech': {'available': True}})['speech']
        self.assertEqual((simulated['evidence'], simulated['physical_verified']), ('simulated', False))

    def test_body_501_marks_capability_unavailable_for_the_session(self):
        class NoFaceBody(SimulatorBody):
            calls = 0

            def face(self, expression):
                NoFaceBody.calls += 1
                raise BodyError('unsupported', 501, 'face adapter missing')
        service = self.make(replies=['ok'], body_type=NoFaceBody)
        service.start(); self.speak_turn(); self.run_for(3)
        self.assertEqual(NoFaceBody.calls, 1)
        face = service.capabilities()['face']
        self.assertEqual((face['available'], face['evidence']), (False, 'unavailable'))
        self.assertEqual(self.sim_state()['last_speech_text'], 'ok')

    def test_fixture_pcm_is_refused_not_played(self):
        service = self.make(replies=['pcm please'], synthesizer=FixturePcmSpeech)
        before = self.mark()
        service.start(); self.speak_turn(); self.run_for(2)
        self.assertNotIn('speech.simulated', self.body_events(before))
        self.assertEqual(len(service.spoken), 0)
        refusals = [e for e in service.events if e['type'] == 'action-refused']
        self.assertIn('fixture PCM not played', refusals[0]['reason'])


class SensingTests(Harness):
    def test_sensing_is_opt_in_half_duplex_and_labelled_synthetic(self):
        service = self.make(heard=['hello cammy'], replies=['Hello, synthetic listener.'])
        transcriber = self.transcriber
        before = self.mark()
        service.start(); self.run_for(3)
        self.assertNotIn('audio.synthetic_capture', self.body_events(before))
        self.assertEqual(transcriber.clips, [])
        self.assertTrue(service.enable_sensing().accepted)
        self.assertEqual(service.phase, LISTENING)
        self.run_for(0.5)
        clip = transcriber.clips[0]
        self.assertTrue(clip.synthetic); self.assertIn('synthetic', clip.source)
        self.run_for(1.25)
        self.assertEqual(service.phase, SPEAKING)
        heard = [c for c in service.captions if c['role'] == 'user'][0]
        self.assertEqual(heard['evidence'], 'synthetic')
        self.assertEqual((heard['provenance']['audio_synthetic'], heard['provenance']['heard_audio']), (True, False))
        captures = service.counters['captures']
        while service.phase == SPEAKING:
            self.run_for(0.25)
            if service.phase == SPEAKING:
                self.assertEqual(service.counters['captures'], captures)   # half-duplex
        self.run_for(1.5)
        self.assertGreater(service.counters['captures'], captures)
        service.disable_sensing()
        captures = service.counters['captures']
        self.run_for(3)
        self.assertEqual((service.counters['captures'], service.phase), (captures, IDLE))

    def test_sensing_needs_a_transcriber(self):
        service = self.make()
        service.start()
        self.assertEqual(service.enable_sensing().reason, 'no-transcriber')

    def test_stale_observations_and_generation_changes_are_rejected(self):
        service = self.make(heard=['too late'], heard_delay=6)
        service.start(); service.enable_sensing(); self.run_for(6.5)
        self.assertEqual(service.counters['observations_rejected_stale'], 1)
        self.assertEqual(self.agent.requests, [])
        stubborn = ScriptedTranscriber(self.clock, ['from the old session'], delay=1, ignore_cancel=True)
        service.disable_sensing()
        service.transcriber = stubborn
        service.enable_sensing(); service.tick()
        self.assertEqual(len(stubborn.clips), 1)
        service.stop(); service.start()
        self.run_for(2)
        self.assertEqual(service.counters['stale_results_rejected'], 1)
        self.assertEqual(self.agent.requests, [])


class InputAndProviderTests(Harness):
    def test_input_validation_queue_bound_and_age(self):
        service = self.make(config=CompanionConfig(lease_ttl=10, renew_margin=4, max_inputs=2,
                                                   max_input_chars=20, max_input_age=5))
        service.start()
        reasons = [service.submit_text(value).reason for value in (123, '   ', 'x' * 21, 'a\x00b')]
        self.assertEqual(reasons, ['not-text', 'empty', 'too-long', 'control-characters'])
        self.assertTrue(service.submit_text('one').accepted)
        self.assertTrue(service.submit_text('two').accepted)
        self.assertEqual(service.submit_text('three').reason, 'input-queue-full')
        self.assertEqual(service.counters['inputs_rejected'], 5)
        self.clock.advance(6); service.tick()
        self.assertEqual(service.counters['inputs_rejected_stale'], 2)
        self.assertEqual(self.agent.requests, [])

    def test_reply_is_chunked_bounded_and_truncated(self):
        self.assertTrue(all(len(c) <= 160 for c in split_speech('a' * 400 + ' tail ' + 'b ' * 200, 160)))
        self.assertEqual(split_speech('one two three', 7), ['one two', 'three'])
        service = self.make(replies=['word ' * 400], config=CompanionConfig(lease_ttl=10, renew_margin=4,
                                                                             max_actions=2))
        service.start(); self.speak_turn(); self.clock.advance(1); service.tick()
        self.assertEqual(service.counters['output_truncated'], 2)   # reply chars and action count
        self.assertEqual(len(service.captions[-1]['text']), 640)
        self.run_for(30)
        self.assertEqual(len(service.spoken), 2)
        self.assertTrue(all(len(s['text']) <= 160 for s in service.spoken))

    def test_provider_failures_and_invalid_output_are_contained(self):
        service = self.make(replies=[Outcome(error='boom'), AgentReply('Still speaks.', gaze_pan_degrees=float('nan')),
                                     AgentReply('Out of range gaze.', gaze_pan_degrees=90)])
        service.start()
        for _ in range(3):
            self.speak_turn(); self.run_for(4)
        counters = service.counters
        self.assertEqual((counters['provider_errors'], counters['invalid_provider_output'],
                          counters['actions_rejected_by_body']), (1, 1, 1))
        self.assertEqual([s['text'] for s in service.spoken], ['Still speaks.', 'Out of range gaze.'])
        self.assertEqual(service.run_state, RUNNING)

        def explode(_request):
            raise RuntimeError('provider crashed')
        self.agent.replies = explode
        self.assertTrue(service.submit_text('again').accepted); service.tick()
        self.assertEqual((service.counters['provider_errors'], service.phase), (2, IDLE))

    def test_config_rejects_unsafe_values(self):
        for bad in ({'lease_ttl': 0}, {'lease_ttl': 121}, {'renew_margin': 30}, {'max_inputs': 0},
                    {'speech_chunk_chars': 161}, {'health_interval': 0}, {'max_reply_chars': 10}):
            with self.assertRaises(ValueError):
                CompanionConfig(**bad)
        with self.assertRaises(ValueError):
            SimulatorBody(Simulator(), 'bad agent!')


class LoopbackGatewayTests(unittest.TestCase):
    """SdkBody through the existing gateway on an ephemeral loopback port, simulator-backed."""
    def setUp(self):
        self.clock = ManualClock()
        self.sim = Simulator(clock=self.clock)
        self.gateway = Gateway('test-companion-token', ['http://192.0.2.1:8787'], simulator=self.sim)
        self.gateway.opener = mock.Mock()
        self.gateway.opener.open.side_effect = AssertionError('simulator attempted external I/O')
        self.gateway.refresh()
        self.server = make_server(self.gateway, ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.endpoint = 'http://127.0.0.1:%d' % self.server.server_port

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.gateway.opener.open.assert_not_called()

    def test_http_body_runs_a_turn_and_halts_on_disconnect(self):
        body = SdkBody(self.endpoint, 'test-companion-token', 'companion-http', timeout=5)
        self.assertNotIn('test-companion-token', repr(body))
        agent = ScriptedAgent(self.clock, [AgentReply('Over HTTP.', gaze_pan_degrees=-5)], delay=0.5)
        service = CompanionService(body, agent, EstimatedTextSpeech(self.clock), None,
                                   CompanionConfig(lease_ttl=10, renew_margin=4), self.clock)
        self.assertTrue(service.start()['started'])
        service.submit_text('hello over loopback')
        for _ in range(4):
            self.clock.advance(0.25); service.tick()
        self.assertEqual(service.phase, SPEAKING)
        state = self.sim.request('GET', '/v1/state', None, 'observer')[1]
        self.assertEqual((state['last_speech_text'], state['commanded_pan_degrees'], state['owner']),
                         ('Over HTTP.', -5, 'companion-http'))
        self.sim.settings({'disconnected': True}); self.gateway.refresh()
        for _ in range(10):
            self.clock.advance(0.25); service.tick()
        self.assertEqual((service.run_state, service.halt_reason), (HALTED, 'body-disconnected'))
        self.assertNotIn('test-companion-token', json.dumps(service.status()))
        self.assertNotIn('test-companion-token', json.dumps(list(service.events)))

    def test_wrong_token_is_refused_at_start(self):
        service = CompanionService(SdkBody(self.endpoint, 'wrong', 'companion-http'), ScriptedAgent(self.clock),
                                   EstimatedTextSpeech(self.clock), None, None, self.clock)
        self.assertEqual(service.start(), {'started': False, 'reason': 'body-auth'})


class SdkBodyGuardTests(unittest.TestCase):
    def test_endpoint_and_token_are_explicit_loopback_only_and_construction_is_silent(self):
        with mock.patch.object(socket, 'create_connection', side_effect=AssertionError('network')):
            for endpoint in ('http://192.0.2.53:8787', 'https://127.0.0.1:1', 'http://127.0.0.1',
                             'http://user:pw@127.0.0.1:1', 'http://127.0.0.1:1/v1', 'http://example.com:1', None):
                with self.assertRaises(ValueError):
                    SdkBody(endpoint, 'token')
            with self.assertRaises(ValueError):
                SdkBody('http://127.0.0.1:1', '  ')
            SdkBody('http://127.0.0.1:1', 'token')   # constructing performs no request


class DemoTests(unittest.TestCase):
    def run_demo(self, *argv):
        out = io.StringIO()
        with mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('network')), \
                mock.patch.object(socket, 'create_connection', side_effect=AssertionError('network')), \
                contextlib.redirect_stdout(out):
            self.assertEqual(demo.main(list(argv)), 0)
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertTrue(all(row['synthetic'] is True for row in rows))
        return rows

    def test_demo_is_finite_offline_and_truthful(self):
        rows = self.run_demo()
        summary = rows[-1]
        self.assertEqual(summary['event'], 'summary')
        self.assertFalse(summary['physical_capabilities_verified'])
        self.assertTrue(summary['stopped']['acknowledged'])
        self.assertFalse(summary['stopped']['hardware_cleanup_confirmed'])
        self.assertEqual(summary['counters']['interrupts'], 1)
        self.assertTrue(all(s['audio_played'] is False for s in summary['spoken']))
        faulted = self.run_demo('--fault', 'disconnect', '--quiet')
        self.assertEqual(len(faulted), 1)
        rows = self.run_demo('--fault', 'disconnect')
        fault = [r for r in rows if r['event'] == 'fault'][0]
        self.assertEqual(fault['halt_reason'], 'body-disconnected')
        stock = self.run_demo('--profile', 'stock', '--quiet')[-1]
        self.assertEqual(stock['spoken'], [])
        self.assertGreater(stock['counters']['actions_refused_unavailable'], 0)


if __name__ == '__main__':
    unittest.main()
