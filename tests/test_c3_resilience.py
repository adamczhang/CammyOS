"""Offline fault injection for the C3 voice, microphone and speaker supervisors."""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.test_c3 import B, V, CammyError, FakeClient, FakeLive, FakePortal, FakeSession, FakeStream
from tests.test_c3 import FastClock, Quiet, Thing, Transcript, msg
from tests.test_c3_calendar import fake_gi
from c3 import listener, service
from google.genai import errors, types


def audio_message(pcm, **kwargs):
    audio = types.Part(inline_data=types.Blob(mime_type='audio/pcm', data=pcm))
    return msg(server_content=types.LiveServerContent(model_turn=types.Content(parts=[audio]), **kwargs))


class Scenario(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.data = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clock = FastClock()
        self.portal = FakePortal()
        self.body = B.Body('http://unused', 'fixture', client=self.portal, clock=self.clock)
        self.speaker = Quiet()
        self.listener = Quiet()
        self.transcript = Transcript(self.data / 'transcript')

    def voice(self, live):
        return V.Voice(FakeClient(live), 'm', lambda: 'persona', 'v', self.speaker, Thing(), Thing(),
                       self.listener, self.transcript, clock=self.clock, handle_file=self.data / 'session.json')

    def start(self, coro):
        task = asyncio.create_task(coro)
        self.addAsyncCleanup(self.stop, task)
        return task

    @staticmethod
    async def stop(task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def until(self, predicate, timeout=3):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.005)


class RecoveryTests(Scenario):
    async def test_second_silent_session_clears_resume_on_disk_and_reconnects_with_summary(self):
        first, second = FakeSession([]), FakeSession([])
        live = FakeLive([first, second, FakeSession([])])
        voice = self.voice(live)
        voice.handle = 'old'
        voice._retry = AsyncMock()
        self.transcript.add('adam', 'Remember the red mug.')
        with self.assertLogs('c3.voice', level='WARNING') as log:
            self.start(voice.run())
            voice.add_turn([types.Part(text='first')])
            await self.until(lambda: first.sent)
            self.clock.t += V.WAITING_MAX_S
            await self.until(lambda: voice.status()['reply'] == 'timed-out')
            first.script.append(msg(go_away=types.LiveServerGoAway()))
            await self.until(lambda: voice.stats['connections'] == 2)
            voice.add_turn([types.Part(text='second')])
            await self.until(lambda: second.sent)
            self.clock.t += V.WAITING_MAX_S
            await self.until(lambda: voice.stats['connections'] == 3)
        self.assertIn('2 consecutive silent replies', '\n'.join(log.output))
        self.assertEqual([cfg.session_resumption.handle for cfg in live.configs], ['old', 'old', None])
        self.assertIn('red mug', live.configs[-1].system_instruction)
        self.assertIsNone(json.loads((self.data / 'session.json').read_text())['handle'])
        self.assertEqual(voice.status()['silent_replies'], 2)

    async def test_empty_completion_counts_as_silent_and_pcm_resets_the_streak(self):
        session = FakeSession([])
        voice = self.voice(FakeLive([]))
        voice._expect_reply()
        self.start(voice._receive(session))
        session.script.append(audio_message(b'', turn_complete=True))
        await self.until(lambda: voice.last_reply == 'completed-without-audio')
        self.assertEqual(voice.status()['silent_replies'], 1)
        self.assertIsNone(voice.last_pcm)
        self.assertEqual(self.speaker.said, [])
        voice._expect_reply()
        session.script.append(audio_message(b'\x01\x00', turn_complete=True))
        await self.until(lambda: voice.last_reply == 'received-audio')
        self.assertEqual(voice.status()['silent_replies'], 0)
        self.assertEqual(self.speaker.said, [(2, True)])

    async def test_completed_speech_without_an_answer_counts_as_ignored(self):
        session = FakeSession([])
        voice = self.voice(FakeLive([]))
        voice.handle = 'resume'
        self.start(voice._receive(session))
        for i, heard in enumerate(('', 'Background television'), 1):
            voice._expect_reply('speech')
            session.script.append(msg(server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text=heard), turn_complete=True)))
            await self.until(lambda: voice.stats['ignored_speech_turns'] == i)
        self.assertEqual(voice.last_reply, 'ignored-speech')
        self.assertEqual(voice.stats['silent_replies'], 0)
        self.assertEqual(voice.handle, 'resume')
        self.assertFalse(voice._restart_for_silence)

        voice._expect_reply('speech')
        session.script.append(msg(server_content=types.LiveServerContent(
            output_transcription=types.Transcription(text='I am answering'), turn_complete=True)))
        await self.until(lambda: voice.last_reply == 'completed-without-audio')
        self.assertEqual(voice.stats['silent_replies'], 1)

    async def test_session_that_goes_mute_after_first_reply_reconnects(self):
        session, fresh = FakeSession([]), FakeSession([])
        live = FakeLive([session, fresh])
        voice = self.voice(live)
        voice.handle = 'resume'
        voice._retry = AsyncMock()
        self.start(voice.run())

        for n in range(1, 4):
            voice.on_speech(listener.Event('start', bytes(B.CHUNK)))
            voice.on_speech(listener.Event('end'))
            await self.until(lambda: session.sent.count(('activity_end', None)) == n)
            if n == 1:
                session.script.append(audio_message(bytes(100), turn_complete=True))
                await self.until(lambda: voice.last_reply == 'received-audio')
            else:
                self.clock.t += V.WAITING_MAX_S
                await self.until(lambda: voice.stats['silent_replies'] == n - 1)
        await self.until(lambda: voice.stats['connections'] == 2)
        self.assertIsNone(voice.handle)
        self.assertEqual(voice.stats['ignored_speech_turns'], 0)

    async def test_tool_pauses_stall_watchdog_and_restarts_its_clock(self):
        session = FakeSession([])
        voice = self.voice(FakeLive([]))
        release = asyncio.Event()

        async def tool(call):
            await release.wait()
            return types.FunctionResponse(name=call.name, response={})

        voice._tool = tool
        voice._expect_reply()
        self.start(voice._receive(session))
        watch = self.start(voice._watch())
        session.script.extend([audio_message(bytes(200)), msg(tool_call=types.LiveServerToolCall(
            function_calls=[types.FunctionCall(name='slow')]))])
        await self.until(lambda: voice.in_turn and voice._handling_tools)
        arrived = voice.last_pcm
        self.clock.t += V.REPLY_STALL_S + 1
        await asyncio.sleep(0.3)
        self.assertFalse(watch.done())
        release.set()
        await self.until(lambda: not voice._handling_tools)
        self.assertEqual(voice.last_pcm, arrived)
        await asyncio.sleep(0.3)
        self.assertFalse(watch.done())
        self.clock.t += V.REPLY_STALL_S
        self.assertEqual(await asyncio.wait_for(watch, 1), 'stalled')

    async def test_stalled_partial_audio_flushes_tail_reopens_mic_and_reconnects(self):
        session = FakeSession([])
        live = FakeLive([session, FakeSession([])])
        voice = self.voice(live)
        voice._retry = AsyncMock()
        c3 = SimpleNamespace(speaker=self.speaker, voice=voice)
        self.start(voice.run())
        voice.add_turn([types.Part(text='hello')])
        await self.until(lambda: session.sent)
        first_block = int(V.FIRST_CHUNK_S * V.RATE_OUT * 2)
        self.assertEqual(first_block, 24_000)
        session.script.extend([audio_message(bytes(first_block - 200)), audio_message(bytes(200)),
                               audio_message(bytes(200)), msg(server_content=types.LiveServerContent(
                                   output_transcription=types.Transcription(text='hi')))])
        await self.until(lambda: voice.in_turn and len(voice._reply_audio) == 200 and voice._since_audio)
        self.assertTrue(service.C3.mic_closed(c3))
        self.clock.t += V.REPLY_STALL_S
        await self.until(lambda: voice.stats['connections'] == 2)
        self.assertEqual(self.speaker.said, [(first_block, False), (200, True)])
        self.assertFalse(service.C3.mic_closed(c3))
        self.assertEqual(voice.status()['reply'], 'stalled')
        self.assertEqual(voice.status()['last_stall'], {'idle_s': V.REPLY_STALL_S, 'since_audio': [(0.0, 'said')]})

    async def test_cancel_while_deliver_is_busy_preserves_fifo_head(self):
        voice = self.voice(FakeLive([]))
        first, second = FakeSession([]), FakeSession([])
        self.listener.speaking = True
        voice.add_turn([types.Part(text='first')])
        voice.add_turn([types.Part(text='second')])
        deliver = asyncio.create_task(voice._deliver(first))
        await self.until(lambda: voice._pending_turn is not None)
        await self.stop(deliver)
        self.assertFalse(first.sent)

        self.listener.speaking = False
        self.start(voice._deliver(second))
        await self.until(lambda: second.sent)
        voice.reply_due = None
        await self.until(lambda: len(second.sent) == 2)
        self.assertEqual(second.sent, [('turn', 'first'), ('turn', 'second')])

    async def test_camera_down_holds_turns_and_discards_audio_from_the_lost_epoch(self):
        session, fresh = FakeSession([]), FakeSession([])
        live = FakeLive([session, fresh])
        voice = self.voice(live)
        voice.body = self.body
        voice._retry = AsyncMock()
        self.start(voice.run())
        voice.add_turn([types.Part(text='hold')])
        await self.until(lambda: voice.state == 'waiting-camera')
        self.assertEqual(live.configs, [])
        self.body.up.set()
        await self.until(lambda: session.sent)
        self.assertEqual(session.sent, [('turn', 'hold')])
        session.script.append(audio_message(bytes(200)))
        await self.until(lambda: voice.in_turn)
        voice.add_turn([types.Part(text='after the drop')])
        await self.until(lambda: voice._pending_turn is not None)
        self.body._clear_up()
        session.script.append(msg(server_content=types.LiveServerContent(
            turn_complete=True, output_transcription=types.Transcription(text='discard this'))))
        await self.until(lambda: voice.status()['discarded_replies'] == 1)
        self.assertEqual(self.speaker.said, [])
        self.assertEqual(self.transcript.count, 0)
        self.assertEqual(session.sent, [('turn', 'hold')])
        self.body.epoch += 1
        self.body.up.set()
        await self.until(lambda: fresh.sent)
        self.assertEqual(fresh.sent, [('turn', 'after the drop')])

    async def test_supervised_restart_wakes_from_existing_speech_or_turn_queue(self):
        for queue in ('speech', 'turns'):
            with self.subTest(queue=queue):
                session = FakeSession([])
                voice = self.voice(FakeLive([session]))
                if queue == 'speech':
                    voice.speech.put_nowait((self.clock(), listener.Event('start', bytes(3200))))
                else:
                    voice.turns.put_nowait(('brain', [types.Part(text='pending')]))
                task = self.start(voice.run())
                await self.until(lambda: session.sent)
                await self.stop(task)
                self.assertEqual(voice.stats['wakes'], 1)

    async def test_provider_error_backoff_keeps_handle_and_fatal_recovers(self):
        class FailingLive(FakeLive):
            failures = [
                errors.APIError(1008, 'Quota exceeded', None),
                errors.ServerError(503, {'message': 'offline'}, None),
                errors.APIError(1007, 'Invalid payload', None),
                errors.APIError(401, 'Invalid credentials', None),
            ]

            @asynccontextmanager
            async def connect(self, model, config):
                self.configs.append(config)
                if self.failures:
                    raise self.failures.pop(0)
                yield session

        session = FakeSession([])
        live = FailingLive([])
        voice = self.voice(live)
        voice.handle = 'resume'
        sleeps = []
        sleeping = asyncio.Event()
        release = asyncio.Event()
        sleep = asyncio.sleep

        async def pause(seconds):
            if voice.next_retry and seconds >= 2:
                sleeps.append((seconds, voice.status()['last_error']['kind'], voice.status()['next_retry']))
                if seconds == V.FATAL_RETRY_S:
                    sleeping.set()
                    await release.wait()
                await sleep(0)
            else:
                await sleep(seconds)

        with patch.object(V.asyncio, 'sleep', new=pause):
            self.start(voice.run())
            voice.add_turn([types.Part(text='wait')])
            await asyncio.wait_for(sleeping.wait(), 2)
            self.assertEqual(voice.status()['last_error'], {'kind': 'fatal', 'detail': 'code 401'})
            self.assertEqual(voice.state, 'backoff')
            release.set()
            await self.until(lambda: session.sent)
        self.assertEqual([cfg.session_resumption.handle for cfg in live.configs], ['resume'] * 5)
        self.assertEqual([(delay, kind) for delay, kind, _ in sleeps],
                         [(30, 'transient'), (4, 'transient'), (20, 'protocol'), (600, 'fatal')])
        self.assertTrue(all(stamp > time.time() for _, _, stamp in sleeps))
        self.assertIsNone(voice.status()['next_retry'])
        self.assertIsNone(voice.status()['last_error'])

    def test_protocol_class_is_distinct_from_auth_and_model_errors(self):
        for code in (400, 1007, 1008):
            with self.subTest(code=code):
                self.assertEqual(V.connection_error(errors.APIError(code, 'bad payload', None), False)[0], 'protocol')
        for code, detail in [(401, 'bad configuration'), (403, 'denied'), (404, 'models/test is not found'),
                             (400, 'API key not valid')]:
            with self.subTest(code=code, detail=detail):
                self.assertEqual(V.connection_error(errors.APIError(code, detail, None), False)[0], 'fatal')
        self.assertEqual(V.connection_error(errors.APIError(404, 'endpoint missing', None), False)[0], 'transient')
        self.assertEqual(V.connection_error(errors.APIError(1008, 'resume handle invalid', None), True)[0],
                         'resume-refused')

    async def test_setup_timeout_cancels_setup_and_leaves_established_session_unbounded(self):
        closed = asyncio.Event()

        class HangingLive:
            @asynccontextmanager
            async def connect(self, **kwargs):
                try:
                    await asyncio.Future()
                    yield
                finally:
                    closed.set()

        voice = self.voice(HangingLive())
        config = V.live_config('p', 'v', [])
        with patch.object(V, 'SETUP_TIMEOUT_S', 0.01):
            with self.assertRaisesRegex(V.SetupTimeout, 'setup timed out'):
                async with voice._connect(config):
                    self.fail('incomplete setup yielded a session')
            self.assertTrue(closed.is_set())
            voice.client = FakeClient(FakeLive([FakeSession([])]))
            async with voice._connect(config):
                await asyncio.sleep(0.02)


class HearingTests(Scenario):
    def wire(self):
        self.listener = listener.Listener(start_ms=32, vad=lambda frame: 1.0)
        self.voice_loop = self.voice(FakeLive([]))
        self.voice_loop.body = self.body
        c3 = service.C3.__new__(service.C3)
        c3.listener, c3.voice, c3.body = self.listener, self.voice_loop, self.body
        self.body.on_mic_reset = c3._reset_hearing
        self.body.up.set()
        self.pcm = b'\xff\x3f' * listener.FRAME
        self.session = FakeSession([])
        self.start(self.voice_loop._send_speech(self.session))

    async def open_gate(self):
        for event in self.listener.feed(self.pcm):
            self.voice_loop.on_speech(event)
        await self.until(lambda: self.voice_loop.utterance_open)
        self.assertTrue(self.listener.speaking)
        self.assertIsNotNone(self.listener.last_heard)

    async def test_physical_mute_forwards_end_to_the_active_session(self):
        self.wire()
        self.body._capture_status({'capture_sequence': 2})
        await self.open_gate()
        self.body._capture_status({'capture_sequence': 3})
        await self.until(lambda: ('activity_end', None) in self.session.sent)
        self.assertFalse(self.listener.speaking)
        self.assertFalse(self.voice_loop.utterance_open)

    async def test_camera_loss_clears_gate_and_does_not_resend_activity_start(self):
        self.wire()
        await self.open_gate()
        self.body._clear_up()
        self.assertFalse(self.listener.speaking)
        self.assertFalse(self.voice_loop.utterance_open)
        self.assertTrue(self.voice_loop._quiet())
        self.body.epoch += 1
        self.body.up.set()
        fresh = FakeSession([])
        self.start(self.voice_loop._send_speech(fresh, self.body.epoch))
        await asyncio.sleep(0.02)
        self.assertEqual(fresh.sent, [])

    async def test_mic_stream_rollover_resets_gate_with_lease_still_up(self):
        self.wire()
        self.portal.streams = [FakeStream([bytes(B.CHUNK)]), FakeStream([bytes(B.CHUNK)])]
        async with asyncio.timeout(2):
            stream = self.body.mic()
            self.addAsyncCleanup(stream.aclose)
            await anext(stream)
            await self.open_gate()
            self.clock.t += B.MIC_STREAM_CAP_S
            await anext(stream)
            await self.until(lambda: ('activity_end', None) in self.session.sent)
        self.assertFalse(self.listener.speaking)
        self.assertTrue(self.body.up.is_set())


class SpeakerTests(Scenario):
    async def test_finish_preserves_one_echo_window_for_either_guardian(self):
        self.body.up.set()
        speaker = B.Speaker(self.body)
        self.portal.speaker_status = Mock(return_value={'phase': 'finished'})
        self.start(speaker.watch())
        for guardian in (0.6, 1.0):
            with self.subTest(guardian=guardian):
                end = self.clock()
                speaker.active = True
                speaker.busy_until = end + B.SPEAKER_TAIL_S
                self.clock.t = end + guardian
                await self.until(lambda: not speaker.active)
                self.assertEqual(speaker.busy_until, end + B.SPEAKER_TAIL_S)
                self.assertTrue(speaker.muted())
                self.clock.t = speaker.busy_until
                self.assertFalse(speaker.muted())

    async def test_playing_and_cancelling_jobs_get_a_deadline(self):
        self.body.up.set()
        for phase in ('playing', 'cancelling'):
            with self.subTest(phase=phase):
                self.portal.speaker_status = Mock(return_value={'phase': phase})
                speaker = B.Speaker(self.body)
                speaker.active = True
                speaker.busy_until = self.clock()
                task = self.start(speaker.watch())
                await self.until(lambda: self.portal.speaker_status.called)
                self.assertTrue(speaker.muted())
                self.clock.t += B.SPEAKER_JOB_GRACE_S + 1
                await self.until(lambda: speaker.failure_reason == 'job-stuck')
                self.assertFalse(speaker.muted())
                await self.stop(task)
        self.assertEqual(self.portal.log, ['speaker_cancel', 'speaker_cancel'])

    async def test_repeated_refusals_release_and_reacquire_the_lease(self):
        speaker = B.Speaker(self.body, tail=0)
        self.portal.play_pcm = Mock(side_effect=[
            CammyError(503, 'speaker endpoint refused playback'), None, None, None,
        ])
        self.portal.speaker_status = Mock(return_value={'phase': 'preflight-refused', 'reason': 'stock-pcm-format'})
        passthrough = SimpleNamespace(feed=lambda pcm, end: pcm)
        # The frozen test clock never reaches the first status read's delay.
        with patch.object(B.cammy_voicefx, 'Stream', return_value=passthrough), patch.object(B, 'BACKOFF', (0,)), \
                patch.object(B, 'SPEAKER_FIRST_CHECK_S', 0):
            with self.assertLogs('c3.body', level='INFO') as log:
                self.start(self.body.run())
                self.start(speaker.run())
                self.start(speaker.watch())
                await self.body.up.wait()
                for n in range(1, B.SPEAKER_FAILURE_LIMIT + 1):
                    speaker.say(bytes(B.CHUNK), end=True)
                    await self.until(lambda: speaker.consecutive_failures == n)
                self.assertTrue(speaker.failing)
                self.assertEqual(speaker.failure_reason, 'stock-pcm-format')
                await self.until(lambda: self.body.epoch == 2)
                self.assertEqual(speaker.lease_recycles, 1)
                self.assertIn('release', self.portal.log)
                self.assertEqual(self.portal.log.count('acquire'), 2)

                self.portal.speaker_status.return_value = {'phase': 'finished'}
                speaker.say(bytes(B.CHUNK), end=True)
                await self.until(lambda: speaker.last_finished is not None)
                self.assertFalse(speaker.failing)
        logs = '\n'.join(log.output)
        self.assertIn('speaker endpoint refused playback', logs)
        self.assertIn('down: RuntimeError: speaker jobs repeatedly failed', logs)
        self.assertIn('up (epoch 2)', logs)


class BodyPowerTests(Scenario):
    def test_unusual_mode_and_reboot_wait_for_normal_mode_to_stay_stable(self):
        for rebind in (False, True):
            with self.subTest(rebind=rebind):
                self.clock.t = 0
                self.body = B.Body('http://unused', 'fixture', client=self.portal, clock=self.clock)
                # Mode first returns to normal, regresses just before the hold ends,
                # and settles again afterward.
                settled_at = B.MEDIA_GUARD_S + 2

                def stock_state():
                    now = self.clock()
                    normal = 5 <= now < B.MEDIA_GUARD_S - 1 or now >= settled_at
                    return (B.VIDEO if normal else 6), False

                prepared = []
                self.body.stock = Mock()
                self.body.stock.mode_lock = None
                self.body.stock.state.side_effect = stock_state
                low = {'level': 1, 'external_power': False, 'warning': 'Low battery on battery power'}
                self.body.stock.battery.return_value = low
                self.body._pause = lambda seconds=0: setattr(self.clock, 't', self.clock() + seconds)
                self.portal.media_status = lambda: {'phase': 'ready' if prepared else 'unprepared'}
                self.portal.prepare_media = lambda: prepared.append(self.clock())
                if rebind:
                    self.body.rebind()
                with self.assertLogs('c3.body', level='INFO') as logs:
                    self.body._bring_up()
                self.assertEqual(prepared, [settled_at + B.STOCK_MODE_STABLE_S])
                self.assertIn('stock mode 6 outside video/photo', '\n'.join(logs.output))
                self.body.stock.set_mode.assert_not_called()
                self.body.stock.battery.assert_called_once_with()
                self.assertEqual(self.body.state['battery']['warning'], low['warning'])
                self.assertIsNone(self.body.state['media_hold'])

    def test_an_odd_mode_that_stays_is_switched_to_video(self):
        modes = {'now': 1}
        self.body.stock = Mock()
        self.body.stock.mode_lock = None
        self.body.stock.state.side_effect = lambda: (modes['now'], False)
        self.body.stock.set_mode.side_effect = lambda m: modes.update(now=m)
        self.body.stock.battery.return_value = {'level': 5, 'external_power': True, 'warning': None}
        self.body._pause = lambda seconds=0: setattr(self.clock, 't', self.clock() + seconds)
        self.portal.state = lambda: {'owner': None}
        prepared = []
        self.portal.media_status = lambda: {'phase': 'ready' if prepared else 'unprepared'}
        self.portal.prepare_media = lambda: prepared.append(self.clock())
        start = self.clock()
        self.body._prepare_media()
        self.body.stock.set_mode.assert_called_once_with(B.VIDEO)
        self.assertEqual(len(prepared), 1)
        self.assertGreaterEqual(prepared[0] - start, B.ODD_MODE_SWITCH_S)
        self.assertLess(prepared[0] - start, B.ODD_MODE_SWITCH_S + B.MODE_SETTLE_S + 5)

    def test_rebind_holds_even_when_mode_is_already_normal(self):
        prepared = []
        self.body.stock = SimpleNamespace(state=lambda: (B.VIDEO, False))
        self.body._pause = lambda seconds=0: setattr(self.clock, 't', self.clock() + seconds)
        self.portal.media_status = lambda: {'phase': 'ready' if prepared else 'unprepared'}
        self.portal.prepare_media = lambda: prepared.append(self.clock())
        start = self.clock()
        self.body.rebind()
        self.assertIn('camera rebooted', self.body.state['media_hold'])
        self.body._prepare_media()
        self.assertEqual(prepared, [start + B.MEDIA_GUARD_S])

    async def test_idle_poll_backoff_keeps_lease_and_detects_stale_boot(self):
        origin = self.clock()
        hits = []
        battery = Mock(return_value={'level': 5, 'external_power': True, 'warning': None})
        self.body.stock = SimpleNamespace(battery=battery)
        self.body._refresh_battery()
        self.body._renewed = origin
        self.body.conversation_active = lambda: self.clock() - origin >= B.RENEW_S
        self.body.capture = (2, True)
        self.portal.renew = Mock()

        def state():
            elapsed = self.clock() - origin
            hits.append(elapsed)
            if elapsed >= B.RENEW_S + 2 * B.CAPTURE_POLL_ACTIVE_S:
                raise CammyError(409, 'stale camera boot binding')
            return {'capture_sequence': 2}

        async def advance(seconds):
            self.clock.t += seconds

        self.portal.state = state
        self.body._battery_due = origin + B.RENEW_S - B.CAPTURE_POLL_IDLE_S
        with patch.object(B.asyncio, 'sleep', new=advance):
            with self.assertRaisesRegex(CammyError, 'stale camera boot'):
                await self.body._watch()
        self.assertEqual(hits[:7], [0, 2, 4, 6, 8, 10, 12])
        self.assertEqual(hits[-3:], [15, 15.5, 16])
        self.portal.renew.assert_called_once_with(B.LEASE_TTL)
        self.assertEqual(battery.call_count, 2)
        self.assertTrue(self.body.state['battery']['external_power'])


class LinkLogTests(Scenario):
    def test_shutdown_during_requested_recycle_still_releases_lease(self):
        self.body.up.set()
        self.body._owns_lease = True
        self.body.recycle_lease(self.body.epoch)
        self.body.release()
        self.assertEqual(self.portal.log, ['release'])

    async def test_down_logs_new_reasons_once_and_reports_recovery(self):
        reasons = iter(['unreachable', 'unreachable', 'preview failed', 'preview failed', None])

        def bring_up():
            reason = next(reasons)
            if reason is not None:
                raise RuntimeError(reason)

        sleep = asyncio.sleep

        async def advance(seconds):
            self.clock.t += seconds
            await sleep(0)

        async def watch_until_cancelled():
            await asyncio.Future()

        with (patch.object(self.body, '_bring_up', side_effect=bring_up),
              patch.object(self.body, '_watch', side_effect=watch_until_cancelled) as watch,
              patch.object(B.asyncio, 'sleep', new=advance),
              patch.object(B, 'BACKOFF', (B.LINK_LOG_INTERVAL_S,)),
              self.assertLogs('c3.body', level='INFO') as log):
            task = self.start(self.body.run())
            await self.body.up.wait()
            await self.stop(task)
        watch.assert_awaited_once()
        self.assertEqual(log.output, [
            'WARNING:c3.body:down: RuntimeError: unreachable',
            'WARNING:c3.body:down: RuntimeError: preview failed',
            'INFO:c3.body:up (epoch 1)',
        ])


class HealthTests(Scenario):
    async def test_canary_runs_on_lease_and_periodically_without_playback_or_resume(self):
        self.portal.speaker_status = Mock(return_value={'phase': 'idle', 'configured': True})
        self.portal.play_pcm = Mock(side_effect=AssertionError('the canary must never play'))
        sessions = [
            FakeSession([audio_message(bytes(200), turn_complete=True)]),
            FakeSession([audio_message(b'', turn_complete=True)]),
            FakeSession([audio_message(bytes(200), turn_complete=True)]),
        ]
        live = FakeLive(list(sessions))
        voice = self.voice(live)
        voice.body = self.body
        voice.handle = 'conversation'
        self.body.epoch = 1
        self.body.up.set()
        self.start(voice.canary_watch())
        await self.until(lambda: voice.canary and voice.canary['state'] == 'ok')
        self.clock.t += V.CANARY_INTERVAL_S + 1
        await self.until(lambda: voice.canary['state'] == 'degraded')
        self.assertEqual(voice.canary['reasons'], ['Gemini produced no audio'])
        self.body.epoch += 1
        await self.until(lambda: voice.canary['epoch'] == 2)
        self.assertEqual(voice.canary['state'], 'ok')
        self.assertEqual(voice.handle, 'conversation')
        self.assertIsNone(voice.last_pcm)
        self.assertEqual(self.transcript.count, 0)
        self.assertEqual(self.speaker.said, [])
        self.portal.play_pcm.assert_not_called()
        self.assertEqual(self.portal.speaker_status.call_count, 3)
        for session, config in zip(sessions, live.configs, strict=True):
            self.assertIsNone(config.session_resumption)
            self.assertIsNone(config.tools)
            self.assertEqual(session.sent, [('turn', 'Say ready.')])

    async def test_canary_skips_a_new_bring_up_soon_after_a_passing_check(self):
        self.portal.speaker_status = Mock(return_value={'phase': 'idle', 'configured': True})
        live = FakeLive([FakeSession([audio_message(bytes(200), turn_complete=True)]) for _ in range(2)])
        voice = self.voice(live)
        voice.body = self.body
        self.body.epoch = 1
        self.body.up.set()
        self.start(voice.canary_watch())
        await self.until(lambda: voice.canary and voice.canary['state'] == 'ok')
        for _ in range(3):                                   # three quick reboots
            self.body.epoch += 1
            self.clock.t += 60
            await asyncio.sleep(1.2)
        self.assertEqual(len(live.configs), 1)
        self.assertEqual(voice.canary['epoch'], 1)
        self.clock.t += V.CANARY_EPOCH_MIN_S
        self.body.epoch += 1
        await self.until(lambda: voice.canary['epoch'] == self.body.epoch)
        self.assertEqual(len(live.configs), 2)

    def test_health_uses_path_timings_and_is_shown_in_the_panel(self):
        body = {'up': True, 'epoch': 1, 'mic_chunk_age_s': 0.1}
        hearing = {'last_heard_age_s': 21}
        voice = {'pcm_age_s': 20}
        speaker = {'last_finished_age_s': 19}
        self.assertEqual(service.voice_health(body, hearing, voice, speaker), ('ok', []))
        verdict, reasons = service.voice_health(body, hearing, voice, {'last_finished_age_s': 25})
        self.assertEqual(verdict, 'degraded')
        self.assertIn('recent model audio without finished playback', reasons)
        ignored = {'pcm_age_s': 23, 'reply': 'ignored-speech'}
        self.assertEqual(service.voice_health(body, hearing, ignored, speaker), ('ok', []))
        verdict, reasons = service.voice_health(body, hearing, dict(ignored, reply='timed-out'), speaker)
        self.assertIn('last reply timed out', reasons)
        self.assertEqual(verdict, 'degraded')
        verdict, reasons = service.voice_health(
            dict(body, mic_chunk_age_s=6), hearing,
            dict(voice, last_error={'kind': 'fatal', 'detail': 'code 1008'}),
            dict(speaker, state='failing', failure_reason='stock-pcm-closed'))
        self.assertEqual(verdict, 'failing')
        self.assertIn('microphone chunks missing', reasons)
        self.assertIn('Gemini: code 1008', reasons)
        self.assertIn('speaker: stock-pcm-closed', reasons)
        modules = fake_gi()
        modules['gi.repository'].GObject = SimpleNamespace(Object=object, SignalFlags=SimpleNamespace(RUN_FIRST=0))
        with patch.dict(sys.modules, modules):
            gui = import_module('c3.gui')
        panel = gui.rows({'body': body, 'voice': voice, 'voice_health': verdict, 'voice_health_reasons': reasons}, None)
        self.assertIn(('Voice health', ' · '.join(reasons), 'failing', 'red'), panel)
        battery = {'level': 1, 'external_power': False, 'warning': 'Low battery on battery power'}
        panel = gui.rows({'body': dict(body, battery=battery, media_hold='stock mode 6')}, None)
        self.assertIn(('Battery', battery['warning'], 'level 1', 'amber'), panel)
        self.assertIn(('Media preparation', 'stock mode 6', 'waiting', 'amber'), panel)

        connected = []
        win = SimpleNamespace(
            row_widgets={}, source=SimpleNamespace(client=SimpleNamespace(endpoint='fixture')),
            connections=SimpleNamespace(add=connected.append), activity=Mock(), diagnostics=Mock(),
            duplex=Mock(), wake=Mock(), _colour=Mock(),
        )
        gi = modules['gi.repository']
        with patch.multiple(gui, Adw=gi.Adw, Gtk=gi.Gtk, GLib=gi.GLib):
            gui.Window._status_rows(win, {'body': dict(body, battery=battery, media_hold='stock mode 6')}, None)
        for name in ('Battery', 'Media preparation'):
            self.assertIn(win.row_widgets[name][0], connected)


class Stop(Exception):
    pass


class LinkProbeTests(Scenario):
    """A failed state poll, a symptom probe, the cached state and the speaker's status schedule."""

    async def watch_for(self, seconds):
        """Run Body._watch on the test clock until `seconds` have passed (Stop) or it raises."""
        end = self.clock() + seconds

        async def advance(step):
            self.clock.t += step
            if self.clock() >= end:
                raise Stop

        with patch.object(B.asyncio, 'sleep', new=advance):
            await self.body._watch()

    async def test_failed_state_poll_keeps_capture_and_mic(self):
        resets = []
        self.body.on_mic_reset = lambda: resets.append(1)
        self.body.up.set()
        self.body._renewed = self.clock()
        self.body.capture = (2, True)
        self.body.conversation_active = lambda: True
        self.portal.state = Mock(side_effect=OSError('timed out'))
        with self.assertRaises(Stop):
            await self.watch_for(5)
        self.assertGreater(self.portal.state.call_count, 3)
        self.assertEqual(self.body.capture, (2, True))
        self.assertEqual(resets, [])
        # Real status still moves it.
        self.body._capture_status({'capture_sequence': 3})
        self.assertEqual(self.body.capture, (3, False))
        self.assertEqual(resets, [1])

    async def test_symptom_renews_at_once_and_a_dead_camera_is_lost_in_seconds(self):
        self.body.up.set()
        start = self.body._renewed = self.clock()
        self.portal.renew = Mock(side_effect=OSError('no route to host'))
        self.body.suspect('mic stalled')
        with self.assertRaisesRegex(B.LeaseLost, 'mic stalled; renew failed') as lost:
            await self.watch_for(B.LEASE_STALE_S)
        self.assertFalse(lost.exception.binding_lost)
        self.assertLess(self.clock() - start, 2)
        self.portal.renew.assert_called_once_with(B.LEASE_TTL)

    async def test_symptom_probe_is_rate_limited(self):
        self.body.up.set()
        self.body._renewed = self.clock()
        self.portal.renew = Mock()
        self.body.suspect('eyes: decoder exited with 1')
        with self.assertRaises(Stop):
            await self.watch_for(2)
        self.assertEqual(self.portal.renew.call_count, 1)          # the camera answered: no loss
        self.body.suspect('mic stalled')                           # too soon after the last probe
        self.assertIsNone(self.body._suspect)
        self.clock.t += B.SUSPECT_PROBE_MIN_S
        self.body.suspect('mic stalled')
        self.assertEqual(self.body._suspect, 'mic stalled')
        self.body.up.clear()
        self.body._suspect = None
        self.body.suspect('mic stalled')                           # nothing to probe while down
        self.assertIsNone(self.body._suspect)

    async def test_eyes_failure_asks_the_body_to_probe(self):
        self.body.suspect = Mock()
        eyes = B.Eyes(self.body, frame_dir=self.data / 'frames')
        eyes.last_error = 'decoder exited with 1'
        eyes._suspect_body()
        self.body.suspect.assert_called_once_with('eyes: decoder exited with 1')

    def test_decoder_uses_one_thread(self):
        cmd = B.Eyes.ffmpeg_command('rtsp://cammy/live')
        self.assertLess(cmd.index('-threads'), cmd.index('-i'))
        self.assertEqual(cmd[cmd.index('-threads') + 1], '1')
        self.assertNotIn('-skip_frame', cmd)

    def test_bring_up_and_polls_cache_the_state(self):
        self.body._bring_up()
        when, state = self.body.last_state
        self.assertEqual(when, self.clock())
        self.assertEqual(state['face_pack'], 'pikachu')
        self.body.rebind()
        self.assertIsNone(self.body.last_state)

    async def test_speaker_reads_status_after_start_then_near_the_end(self):
        self.body.up.set()
        speaker = B.Speaker(self.body)
        self.portal.speaker_status = Mock(return_value={'phase': 'playing'})
        start = self.clock()
        speaker.active, speaker.jobs, speaker.job_started = True, 1, start
        audio_end = start + 4.0
        speaker.busy_until = audio_end + speaker.tail
        self.start(speaker.watch())
        await asyncio.sleep(0.5)
        self.assertEqual(self.portal.speaker_status.call_count, 0)    # not before 0.5 s
        self.clock.t = start + B.SPEAKER_FIRST_CHECK_S
        await self.until(lambda: self.portal.speaker_status.call_count == 1)
        self.clock.t = audio_end - 1.0
        await asyncio.sleep(0.6)
        self.assertEqual(self.portal.speaker_status.call_count, 1)    # mid-reply: no polls
        self.clock.t = audio_end - B.SPEAKER_END_EARLY_S
        await self.until(lambda: self.portal.speaker_status.call_count >= 3)   # every tick near the end
        self.portal.speaker_status.return_value = {'phase': 'finished'}
        await self.until(lambda: not speaker.active)
        self.assertEqual(speaker.consecutive_failures, 0)
