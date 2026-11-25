"""Finite listening policy: simulator/fake providers only, no camera or audio I/O."""
from unittest import mock
import contextlib
import io
import json
import unittest

from tests.test_companion import Harness, SimulatorBody, ScriptedTranscriber
from companion import BodyError, EngagementConfig, EngagementController, RUNNING, STOPPED
from companion.demo import main


class EngagementTests(Harness):
    def build(self, **kwargs):
        service = self.make(heard=kwargs.pop('heard', [None] * 100), **kwargs)
        controller = EngagementController(service, EngagementConfig(idle_seconds=3,
                                           max_session_seconds=20, max_hold_seconds=5))
        return service, controller

    def step(self, controller, seconds, step=0.25):
        for _ in range(round(seconds / step)):
            self.clock.advance(step)
            controller.tick()
        return controller.status()

    def test_default_config_and_invalid_bounds(self):
        self.assertEqual(EngagementConfig().idle_seconds, 60)
        for bad in (True, 0, -1, float('nan'), float('inf'), '3'):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                EngagementConfig(idle_seconds=bad)
        with self.assertRaises(ValueError):
            EngagementConfig(max_session_seconds=10)

    def test_construction_stopped_and_idle_never_capture(self):
        service, controller = self.build()
        sequence = self.sim.sequence
        controller.tick()
        self.assertFalse(controller.press()['accepted'])
        self.assertFalse(controller.agent_wake()['accepted'])
        self.assertEqual(self.sim.sequence, sequence)
        self.assertTrue(controller.start()['started'])
        self.step(controller, 25)
        self.assertEqual(service.counters['captures'], 0)
        self.assertEqual(controller.state, 'idle')
        self.assertEqual(service.run_state, RUNNING)
        self.assertFalse(service.sensing_enabled)

    def test_press_release_keeps_followup_open_then_silence_closes(self):
        service, controller = self.build()
        controller.start()
        self.assertTrue(controller.press()['accepted'])
        self.step(controller, 1)
        self.assertGreater(service.counters['captures'], 0)
        self.assertTrue(controller.release()['accepted'])
        count = service.counters['captures']
        self.step(controller, 2)
        self.assertEqual(controller.state, 'open')
        self.assertGreater(service.counters['captures'], count)
        self.step(controller, 1)
        self.assertEqual((controller.state, controller.reason), ('idle', 'idle-timeout'))
        count = service.counters['captures']
        self.step(controller, 5)
        self.assertEqual(service.counters['captures'], count)

    def test_repeat_press_does_not_extend_stuck_hold(self):
        service, controller = self.build()
        controller.start(); controller.press()
        self.step(controller, 4)
        self.assertEqual(controller.press()['reason'], 'already-held')
        self.step(controller, 1)
        self.assertEqual(controller.reason, 'hold-limit')
        self.assertFalse(service.sensing_enabled)
        self.assertEqual(controller.press()['reason'], 'release-required')
        controller.release()
        self.assertTrue(controller.press()['accepted'])

    def test_agent_wake_bounded_and_hard_limit_cannot_be_extended(self):
        service, controller = self.build()
        controller.start()
        self.assertTrue(controller.agent_wake()['accepted'])
        for _ in range(10):
            self.step(controller, 1.75)
            self.assertTrue(controller.agent_wake()['accepted'])
        self.step(controller, 2.5)
        self.assertEqual(controller.reason, 'session-limit')
        self.assertFalse(service.sensing_enabled)

    def test_mute_blocks_agent_and_button_unmute_returns_idle(self):
        service, controller = self.build()
        controller.start(); controller.press()
        self.assertTrue(controller.mute())
        self.assertFalse(controller.agent_wake()['accepted'])
        self.assertFalse(controller.press()['accepted'])
        self.step(controller, 4)
        self.assertTrue(controller.unmute())
        self.assertFalse(service.sensing_enabled)
        self.assertEqual(controller.state, 'idle')
        self.assertEqual(controller.press()['reason'], 'release-required')
        controller.release()
        self.assertTrue(controller.press()['accepted'])

    def test_hard_close_rejects_delayed_provider_and_discards_queued_actions(self):
        service, controller = self.build(replies=['must never be spoken'], delay=25, ignore_cancel=True)
        controller.start(); controller.agent_wake(); controller.submit_text('hello')
        controller.tick()
        self.step(controller, 20)
        self.assertEqual(controller.reason, 'session-limit')
        self.step(controller, 6)
        self.assertEqual(list(service.spoken), [])
        self.assertGreater(service.counters['stale_results_rejected'], 0)

    def test_idle_close_rejects_delayed_transcriber_even_after_rewake(self):
        service, controller = self.build(heard=['old voice'], heard_delay=4)
        service.transcriber = ScriptedTranscriber(self.clock, ['old voice'], delay=4, ignore_cancel=True)
        controller.start(); controller.agent_wake(); controller.tick()
        self.step(controller, 3)
        self.assertEqual(controller.state, 'idle')
        controller.agent_wake()
        self.step(controller, 1.5)
        self.assertEqual(service.agent.requests, [])
        self.assertGreater(service.counters['stale_results_rejected'], 0)

    def test_useful_work_finishes_before_idle_tail_expires(self):
        service, controller = self.build(replies=['short reply'], delay=4)
        controller.start(); controller.agent_wake(); controller.submit_text('hi')
        controller.tick()
        self.step(controller, 5)
        self.assertEqual(controller.state, 'open')
        self.assertEqual(len(service.spoken), 1)
        self.step(controller, 5)
        self.assertEqual(controller.state, 'idle')

    def test_stop_and_emergency_stop_do_not_reopen_without_start(self):
        for emergency in (False, True):
            with self.subTest(emergency=emergency):
                service, controller = self.build()
                controller.start(); controller.press()
                report = controller.stop(emergency=emergency)
                self.assertFalse(report['hardware_cleanup_confirmed'])
                self.assertEqual(service.run_state, STOPPED)
                self.assertFalse(controller.agent_wake()['accepted'])
                self.step(controller, 5)
                self.assertTrue(controller.start()['started'])
                self.assertFalse(service.sensing_enabled)

    def test_lease_takeover_halts_and_never_stops_other_owner(self):
        service, controller = self.build()
        controller.start(); controller.press()
        service.body.state = mock.Mock(return_value={'owner': 'someone-else'})
        service.body.stop = mock.Mock()
        self.step(controller, 2)
        self.assertEqual(controller.state, 'fault')
        self.assertEqual(controller.reason, 'lease-lost')
        self.assertFalse(service.sensing_enabled)
        service.body.stop.assert_not_called()

    def test_unavailable_real_style_body_never_claims_listening(self):
        service, controller = self.build(profile='stock')
        controller.start()
        self.assertEqual(controller.press()['reason'], 'sensing-unavailable')
        self.assertEqual(controller.state, 'idle')
        status = controller.status()
        self.assertFalse(status['listening_requested'])
        self.assertFalse(status['physical_record_button_integrated'])
        self.assertFalse(status['continuous_duplex'])

    def test_capture_refusal_after_open_closes_requested_window(self):
        service, controller = self.build()
        controller.start(); controller.press()
        service.body.capture_audio = mock.Mock(side_effect=BodyError('unsupported', 501, 'fixture unavailable'))
        controller.tick()
        self.assertEqual((controller.state, controller.reason), ('idle', 'sensing-unavailable'))
        self.assertFalse(controller.status()['listening_requested'])

    def test_mute_discards_late_response_and_disconnect_requires_restart(self):
        service, controller = self.build(replies=['discard me'], delay=5, ignore_cancel=True)
        controller.start(); controller.agent_wake(); controller.submit_text('hi'); controller.tick()
        controller.mute(); self.step(controller, 6)
        self.assertEqual(list(service.spoken), [])
        controller.unmute(); controller.release(); controller.press()
        service.body.state = mock.Mock(side_effect=BodyError('unavailable', 503, 'fixture disconnected'))
        self.step(controller, 2)
        self.assertEqual((controller.state, controller.reason), ('fault', 'body-disconnected'))
        self.assertFalse(controller.agent_wake()['accepted'])

    def test_external_service_restart_does_not_inherit_listening_window(self):
        service, controller = self.build()
        controller.start(); controller.press()
        service.stop(); service.start(sensing=True)
        controller.tick()
        self.assertEqual((controller.state, controller.reason), ('fault', 'service-session-changed'))
        self.assertFalse(service.sensing_enabled)

    def test_typed_idle_request_does_not_enable_microphone(self):
        service, controller = self.build(replies=['typed response'])
        controller.start(); controller.submit_text('typed input')
        self.step(controller, 3)
        self.assertEqual(len(service.spoken), 1)
        self.assertEqual(service.counters['captures'], 0)
        self.assertFalse(service.sensing_enabled)

    def test_cli_exercises_same_controller_without_network_or_devices(self):
        for profile in ('companion', 'stock'):
            with self.subTest(profile=profile), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--engagement', '--profile', profile, '--quiet']), 0)
            result = json.loads(output.getvalue())
            self.assertFalse(result['muted_wake_accepted'])
            self.assertEqual(result['status']['engagement'], 'stopped')
            self.assertFalse(result['status']['physical_record_button_integrated'])
            self.assertEqual(result['trace'][0]['captures'], 0)
            self.assertEqual(result['trace'][2]['engagement'], 'idle')


if __name__ == '__main__':
    unittest.main()
