"""C3 resident API and voice routing without hardware or provider calls."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientTimeout
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import api, listener, provider_health, resident_brain, service, voice  # noqa: E402
from c3.resident_brain import ResidentBrain, RunResult  # noqa: E402


class BrainApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.provider = AsyncMock()
        self.brain = ResidentBrain(Path(folder) / 'brain', runner=self.provider)
        self.addAsyncCleanup(self.brain.close)
        self.c3 = SimpleNamespace(brain_backend='resident', brain=self.brain, voice=Mock(state='asleep'), transcript=Mock())
        self.client = TestClient(TestServer(api.make_app(self.c3, 'secret')))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()
        self.auth = {'Authorization': 'Bearer secret'}

    async def post(self, path, data):
        return await self.client.post('/v1/brain/' + path, headers=self.auth, json=data)

    async def create_and_select(self):
        payload = {'id': 'desk', 'name': 'Desk', 'provider': 'codex'}
        response = await self.post('profiles', payload)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['profiles'], [payload])
        response = await self.post('select', {'id': 'desk'})
        self.assertEqual((await response.json())['active_profile'], 'desk')

    async def test_resident_routes_require_auth_and_keep_text_chat_private(self):
        self.assertEqual((await self.client.get('/v1/brain')).status, 401)
        response = await self.client.get('/v1/brain', headers=self.auth)
        self.assertEqual(await response.json(), self.brain.state)
        self.assertEqual((await self.post('message', {'text': 'no profile yet'})).status, 400)
        await self.create_and_select()
        self.provider.assert_not_awaited()

        self.provider.return_value = RunResult(str(uuid4()), 'private reply')
        response = await self.post('message', {'text': 'private question'})
        self.assertEqual(await response.json(), {'queued': True})
        response = await self.client.get('/v1/brain/history?profile=desk', headers=self.auth)
        self.assertEqual([entry['text'] for entry in (await response.json())['entries']],
                         ['private question', 'private reply'])
        self.assertTrue(self.brain.replies.empty())
        self.c3.voice.add_turn.assert_not_called()
        self.c3.transcript.add.assert_not_called()

    async def test_named_message_only_queues_to_the_selected_profile(self):
        await self.create_and_select()
        await self.post('profiles', {'id': 'other', 'name': 'Other', 'provider': 'claude'})
        path = '/v1/brain/profiles/desk/message'
        self.assertEqual((await self.client.post(path, json={'text': 'unauthenticated'})).status, 401)
        await self.post('select', {'id': 'other'})
        response = await self.client.post(path, headers=self.auth, json={'text': 'stale selection'})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.brain.history('desk'), [])
        self.assertEqual(self.brain.history('other'), [])
        self.provider.assert_not_awaited()

        await self.post('select', {'id': 'desk'})
        self.provider.return_value = RunResult(str(uuid4()), 'private reply')
        response = await self.client.post(path, headers=self.auth, json={'text': 'question for desk'})
        self.assertEqual(await response.json(), {'queued': True, 'profile': 'desk'})
        self.assertEqual(self.brain.history('desk')[0]['text'], 'question for desk')
        self.assertEqual(self.brain.history('other'), [])
        self.assertTrue(self.brain.replies.empty())

    async def test_provider_failure_stays_in_brain_history_and_gemini_accepts_turns(self):
        await self.create_and_select()
        self.provider.side_effect = OSError('provider unavailable')
        self.assertEqual((await self.post('message', {'text': 'provider question'})).status, 200)
        self.assertEqual(self.brain.history()[-1]['status'], 'failed')
        self.assertTrue(self.brain.replies.empty())
        self.c3.voice.add_turn.assert_not_called()

        response = await self.client.post('/v1/message', headers=self.auth, json={'text': 'Gemini question'})
        self.assertEqual(await response.json(), {'queued': True, 'voice': 'asleep'})
        self.assertEqual(self.c3.voice.add_turn.call_args.args[0][0].text, 'Gemini question')

    async def test_validation_busy_cancel_and_disabled_routes(self):
        self.assertEqual((await self.post('profiles', {'instructions': 13})).status, 400)
        self.assertEqual((await self.post('select', {'id': 'unknown'})).status, 400)
        self.assertEqual((await self.post('message', {'text': ' '})).status, 400)
        self.assertEqual((await self.post('cancel', {'extra': True})).status, 400)

        async def wait_for_cancel(_):
            await asyncio.Event().wait()

        self.provider.side_effect = wait_for_cancel
        await self.create_and_select()
        self.assertEqual((await self.post('message', {'text': 'first'})).status, 200)
        self.assertEqual((await self.post('message', {'text': 'second'})).status, 409)
        response = await self.post('cancel', {})
        self.assertEqual((await response.json())['pending'], 0)
        self.assertEqual(self.brain.history()[0]['status'], 'cancelled')

        for backend in ('t3', None):
            app = api.make_app(SimpleNamespace(brain_backend=backend), 'secret')
            async with TestClient(TestServer(app)) as client:
                self.assertEqual((await client.get('/v1/brain', headers=self.auth)).status, 404)


class BrainVoiceTests(unittest.IsolatedAsyncioTestCase):
    def test_persona_survives_temporary_camera_outage(self):
        c3 = service.C3.__new__(service.C3)
        c3.family = service.familymod.FamilyPolicy(None, deployment='adult-standalone')   # Gemini path
        c3.deployment, c3.cloud, c3.play, c3.local_voice, c3._teardowns = 'adult-standalone', True, None, None, set()
        c3.brain_backend = None
        state = Mock(side_effect=AssertionError('persona must use the cached state'))
        c3.body = SimpleNamespace(cammy=SimpleNamespace(state=state), last_state=None)

        def persona(cached):
            c3.body.last_state = None if cached is None else (0.0, cached)
            return c3.persona()

        first = persona({'face_pack': 'pikachu', 'face_pack_shown': 'fin-fur'})
        self.assertIn('Fin and Fur', first)
        self.assertEqual(persona(None), first)          # an outage keeps the selected pack
        self.assertIn('Pikachu', persona({'face_pack': 'pikachu'}))
        self.assertIn("I'm Cammy on CammyOS!", persona({'face_pack': 'pikachu', 'face_pack_shown': 'unknown'}))
        state.assert_not_called()

    def test_status_counts_facts_again_only_when_the_file_changes(self):
        c3 = service.C3.__new__(service.C3)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'facts.jsonl'
            path.write_text('{"id": "a", "text": "likes tea"}\n')
            facts = Mock(side_effect=lambda: [None] * len(path.read_text().splitlines()))
            c3.memory = SimpleNamespace(path=path, facts=facts)
            self.assertEqual([c3._fact_count() for _ in range(3)], [1, 1, 1])
            self.assertEqual(facts.call_count, 1)
            with path.open('a') as f:
                f.write('{"id": "b", "text": "has a cat"}\n')
            self.assertEqual(c3._fact_count(), 2)
            self.assertEqual(facts.call_count, 2)

    async def test_resident_run_starts_only_reply_worker_and_closes_brain(self):
        c3 = service.C3.__new__(service.C3)
        c3.family = service.familymod.FamilyPolicy(None, deployment='adult-standalone')   # Gemini path
        c3.deployment, c3.cloud, c3.play, c3.local_voice, c3._teardowns = 'adult-standalone', True, None, None, set()
        c3.args = SimpleNamespace(
            level='normal', tail=1.6, model='model', voice='voice', idle=45,
            api_port=0, greet=False, portal='test', minutes=0.0001,
        )
        c3.duplex, c3.restarts = 'half', {}
        c3.brain_backend = 'resident'
        c3.brain = SimpleNamespace(replies=asyncio.Queue(), close=AsyncMock(), ask=AsyncMock())
        c3.echo = c3.native = c3.camera = c3.client = c3.memory = None
        c3.moves = Mock()
        c3.body, c3.face, c3.eyes, c3.listener = Mock(), Mock(), Mock(), Mock()
        c3.transcript = Mock(earlier=False)
        c3.status = Mock(return_value={'voice': None})
        runner = SimpleNamespace(cleanup=AsyncMock())
        with patch.object(service.bodymod, 'Speaker'), \
             patch.object(service.api, 'api_token', return_value='test'), \
             patch.object(service.api, 'serve', new=AsyncMock(return_value=runner)), \
             patch.object(service, 'supervise', new_callable=AsyncMock) as supervise, \
             patch.object(asyncio.get_running_loop(), 'add_signal_handler'):
            await c3.run()

        brain_workers = {call.args[0] for call in supervise.await_args_list if call.args[0].startswith('brain-')}
        self.assertEqual(brain_workers, {'brain-in'})
        c3.transcript.since.assert_not_called()
        c3.brain.ask.assert_not_awaited()
        c3.brain.close.assert_awaited_once_with()
        runner.cleanup.assert_awaited_once_with()

    async def test_resident_sync_is_inert_and_explicit_reply_reaches_gemini(self):
        c3 = service.C3.__new__(service.C3)
        c3.family = service.familymod.FamilyPolicy(None, deployment='adult-standalone')   # Gemini path
        c3.deployment, c3.cloud, c3.play, c3.local_voice, c3._teardowns = 'adult-standalone', True, None, None, set()
        c3.brain_backend = 'resident'
        c3.brain = SimpleNamespace(replies=asyncio.Queue())
        c3.body = SimpleNamespace(cammy=SimpleNamespace(state=lambda: {'face_pack': 'pikachu'}))
        c3.transcript = Mock()
        c3.voice = service.ResidentVoice(None, 'model', c3.persona, 'voice', None, None, None,
                                         None, c3.transcript, c3.brain)
        with patch.object(service.cammy_brain, 'thread_id', side_effect=AssertionError), \
             patch.object(service.cammy_brain, 'brief', side_effect=AssertionError):
            self.assertTrue(c3.label().startswith('C3 on '))
            self.assertIn('selected brain profile', c3.persona())
        self.assertNotIn(voice.ASK_BRAIN, c3.voice.tools())
        await c3.sync_brain()
        c3.transcript.since.assert_not_called()
        self.assertFalse(c3.voice.wake.is_set())

        worker = asyncio.create_task(c3.brain_in())
        try:
            await c3.brain.replies.put('requested answer')
            label, parts = await asyncio.wait_for(c3.voice.turns.get(), timeout=1)
            self.assertEqual((label, parts[0].text), ('brain', '[Brain] requested answer'))
            self.assertTrue(c3.voice.wake.is_set())
        finally:
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker


class ResidentStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.data / 'brain'
        self.auth = {'Authorization': 'Bearer secret'}
        self.enterContext(patch.object(service, 'DATA', self.data))
        self.enterContext(patch.object(service, 'credentials', return_value=('test', 'test')))
        self.gemini = self.enterContext(patch.object(service.genai, 'Client'))
        self.camera = self.enterContext(patch.object(service.bodymod, 'Body', return_value=Mock(
            up=asyncio.Event(), state={'lease': 'none'}, epoch=0)))
        self.enterContext(patch.object(service.cammy_capture, 'Native', return_value=None))
        self.enterContext(patch.object(service.bodymod, 'Face'))
        self.enterContext(patch.object(service.bodymod, 'Eyes', return_value=Mock(wanted=False, restarts=0)))
        self.enterContext(patch.object(listener, 'Listener', return_value=Mock(stats={}, speaking=False)))
        self.health = Mock(spec=provider_health.ProviderHealth)
        self.health.status.return_value = {'providers': [{'id': 'codex'}]}
        self.health_factory = self.enterContext(patch.object(
            provider_health, 'ProviderHealth', return_value=self.health))
        self.model_call = self.enterContext(patch.object(resident_brain._NativeRunner, '__call__', new_callable=AsyncMock))
        self.enterContext(patch.object(service.cammy_brain, 'thread_id', side_effect=AssertionError))

    def start(self):
        args = SimpleNamespace(brain='resident', deployment='adult-standalone', duplex='off', portal='test', sweep=1,
                               level='normal', tail=1.6, model='model', voice='voice', idle=45,
                               api_port=0, greet=False, minutes=0)
        c3 = service.C3(args)
        if c3.brain is not None:
            self.addAsyncCleanup(c3.brain.close)
        return c3

    async def open_client(self, c3):
        client = TestClient(TestServer(api.make_app(c3, 'secret')), timeout=ClientTimeout(total=2))
        self.addAsyncCleanup(client.close)
        await asyncio.wait_for(client.start_server(), timeout=2)
        return client

    async def test_locked_store_keeps_camera_voice_and_authenticated_diagnostics(self):
        owner = ResidentBrain(self.root)
        self.addAsyncCleanup(owner.close)
        await owner.create_profile('desk', 'Desk', 'codex')
        await owner.select_profile('desk')
        c3 = self.start()
        self.assertIsNone(c3.brain)
        self.gemini.assert_called_once()
        self.camera.assert_called_once()
        self.health.status.assert_not_awaited()
        self.health.test.assert_not_awaited()

        runner = SimpleNamespace(cleanup=AsyncMock())
        loop = asyncio.get_running_loop()
        handlers = {}

        async def start_worker(name, *_):
            if name == 'moves':
                handlers[service.signal.SIGINT]()

        speaker = Mock(active=False, chunks=0, seconds=0, last_outcome=None, failure_reason=None,
                       failing=False, consecutive_failures=0, lease_recycles=0, last_finished=None)
        with patch.object(service.bodymod, 'Speaker', return_value=speaker), \
             patch.object(service.api, 'serve', new=AsyncMock(return_value=runner)), \
             patch.object(service, 'supervise', side_effect=start_worker) as supervise, \
             patch.object(loop, 'add_signal_handler', side_effect=handlers.__setitem__):
            await asyncio.wait_for(c3.run(), timeout=2)

        workers = {call.args[0] for call in supervise.await_args_list}
        self.assertTrue({'body', 'hear', 'voice', 'voice-canary', 'speaker-watch', 'moves'} <= workers)
        self.assertFalse(any(name.startswith('brain-') for name in workers))
        self.assertIs(type(c3.voice), voice.Voice)
        self.assertNotIn(voice.ASK_BRAIN, c3.voice.tools())
        c3.eyes.stop.assert_called_once_with()
        c3.body.release.assert_called_once_with()
        runner.cleanup.assert_awaited_once_with()

        c3.eyes.look = AsyncMock(return_value=b'fake JPEG')
        client = await self.open_client(c3)
        for path in ('/v1/status', '/v1/brain', '/v1/brain/history', '/v1/providers'):
            self.assertEqual((await client.get(path)).status, 401)
        self.health.status.assert_not_awaited()
        self.assertEqual((await client.post('/v1/providers/test', json={'id': 'codex'})).status, 401)
        for method, path in (
            ('GET', '/v1/brain'), ('GET', '/v1/brain/history?profile=desk'),
            ('POST', '/v1/brain/profiles'), ('POST', '/v1/brain/select'),
            ('POST', '/v1/brain/message'), ('POST', '/v1/brain/cancel'),
        ):
            with self.subTest(path=path):
                reply = await client.request(method, path, headers=self.auth)
                self.assertEqual(reply.status, 503)
                problem = await reply.json()
                self.assertEqual(problem['code'], 'storage_busy')
                self.assertIn('restart C3', problem['recovery'])
                self.assertNotIn('profiles', problem)
        reply = await client.get('/v1/status', headers=self.auth)
        self.assertEqual((await reply.json())['brain'], {'backend': 'resident', **problem})
        reply = await client.get('/v1/providers?refresh=1', headers=self.auth)
        self.assertEqual(await reply.json(), {'providers': [{'id': 'codex'}], 'brain': problem})
        self.health.status.assert_awaited_once_with(refresh=True)
        reply = await client.post('/v1/message', headers=self.auth, json={'text': 'Gemini question'})
        self.assertEqual(await reply.json(), {'queued': True, 'voice': 'asleep'})
        label, parts = c3.voice.turns.get_nowait()
        self.assertEqual((label, parts[0].text), ('api', 'Gemini question'))
        reply = await client.post('/v1/look', headers=self.auth)
        self.assertEqual((reply.status, reply.content_type, await reply.read()), (200, 'image/jpeg', b'fake JPEG'))
        self.assertEqual(owner.state['active_profile'], 'desk')
        self.assertEqual(owner.history(), [])
        self.model_call.assert_not_awaited()
        self.health.test.assert_not_awaited()

    async def test_corrupt_profile_or_selection_does_not_rewrite_saved_state(self):
        seed = ResidentBrain(self.root)
        try:
            await seed.create_profile('desk', 'Desk', 'codex')
            await seed.create_profile('later', 'Later', 'claude')
            await seed.select_profile('desk')
        finally:
            await seed.close()
        valid = self.root / 'profiles/desk/profile.json'
        later = self.root / 'profiles/later/profile.json'
        selection = self.root / 'selection.json'
        saved = json.loads(valid.read_bytes())
        saved['history'] = [{'role': 'user', 'id': 'interrupted-turn', 'text': 'recover me', 'status': 'pending'}]
        pending = json.dumps(saved).encode()
        valid.write_bytes(pending)
        for path, corrupt in (
            (later, b'{invalid json'),
            (later, b'{"id":"later","provider":"claude","session_id":null,"history":[]}'),
            (selection, b'{"active_profile":"missing"}'),
        ):
            with self.subTest(path=path, corrupt=corrupt):
                original = path.read_bytes()
                path.write_bytes(corrupt)
                c3 = self.start()
                self.assertIsNone(c3.brain)
                self.assertEqual(c3.status()['brain']['code'], 'storage_invalid')
                self.assertEqual(path.read_bytes(), corrupt)
                self.assertEqual(valid.read_bytes(), pending)
                path.write_bytes(original)
        restored = self.start()
        self.assertEqual(restored.brain.state['active_profile'], 'desk')
        self.assertEqual(restored.brain.history()[0]['status'], 'interrupted')
        self.model_call.assert_not_awaited()

    async def test_unreadable_store_and_unexpected_init_error_are_redacted(self):
        seed = ResidentBrain(self.root)
        await seed.create_profile('desk', 'Desk', 'codex')
        await seed.close()
        for target, name, failure, code in (
            (resident_brain, '_read_json', PermissionError('private diagnostic'), 'storage_error'),
            (ResidentBrain, '__init__', RuntimeError('private diagnostic'), 'initialization_failed'),
        ):
            with self.subTest(code=code):
                with patch.object(target, name, side_effect=failure):
                    c3 = self.start()
                client = await self.open_client(c3)
                reply = await client.get('/v1/brain', headers=self.auth)
                self.assertEqual(reply.status, 503)
                problem = await reply.json()
                self.assertEqual(problem['code'], code)
                self.assertNotIn('private diagnostic', json.dumps(problem))

    async def test_provider_initialization_failure_leaves_brain_usable(self):
        self.health_factory.side_effect = RuntimeError('private diagnostic')
        c3 = self.start()
        client = await self.open_client(c3)
        payload = {'id': 'desk', 'name': 'Desk', 'provider': 'codex'}
        reply = await client.post('/v1/brain/profiles', headers=self.auth, json=payload)
        self.assertEqual((await reply.json())['profiles'], [payload])
        for reply in (await client.get('/v1/providers', headers=self.auth),
                      await client.post('/v1/providers/test', headers=self.auth, json={'id': 'codex'})):
            self.assertEqual(reply.status, 503)
            problem = await reply.json()
            self.assertIn('refresh provider status', problem['recovery'])
            self.assertNotIn('private diagnostic', json.dumps(problem))
        self.model_call.assert_not_awaited()

    async def test_storage_write_failure_blocks_brain_calls_and_keeps_private_history_out_of_status(self):
        c3 = self.start()
        await c3.brain.create_profile('desk', 'Desk', 'codex')
        await c3.brain.select_profile('desk')
        client = await self.open_client(c3)
        with patch.object(resident_brain, '_write_json', side_effect=OSError('private diagnostic')):
            reply = await client.post('/v1/brain/message', headers=self.auth, json={'text': 'question'})
        self.assertEqual(reply.status, 503)
        problem = await reply.json()
        self.assertEqual(problem['code'], 'storage_error')
        self.assertNotIn('private diagnostic', json.dumps(problem))
        self.assertEqual(c3.status()['brain'], {'backend': 'resident', **problem})
        reply = await client.get('/v1/brain/history?profile=desk', headers=self.auth)
        self.assertEqual(reply.status, 503)
        self.model_call.assert_not_awaited()

    async def test_cancellation_remains_available_after_storage_write_failure(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hold_turn(_):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        self.model_call.side_effect = hold_turn
        c3 = self.start()
        await c3.brain.create_profile('desk', 'Desk', 'codex')
        await c3.brain.select_profile('desk')
        await c3.brain.submit('explicit turn')
        await asyncio.wait_for(started.wait(), timeout=2)

        client = await self.open_client(c3)
        profile = {'id': 'other', 'name': 'Other', 'provider': 'claude'}
        with patch.object(resident_brain, '_write_json', side_effect=OSError('write failed')):
            reply = await client.post('/v1/brain/profiles', headers=self.auth, json=profile)
        self.assertEqual(reply.status, 503)
        reply = await client.post('/v1/brain/cancel', headers=self.auth, json={})
        self.assertEqual(reply.status, 200)
        self.assertEqual((await reply.json())['pending'], 0)
        self.assertTrue(cancelled.is_set())
        self.model_call.assert_awaited_once()

    async def test_api_startup_failure_releases_resident_lock_and_camera_resources(self):
        c3 = self.start()
        with patch.object(service.bodymod, 'Speaker'), \
             patch.object(service.api, 'serve', new=AsyncMock(side_effect=OSError('bind failed'))):
            with self.assertRaisesRegex(OSError, 'bind failed'):
                await asyncio.wait_for(c3.run(), timeout=2)

        c3.eyes.stop.assert_called_once_with()
        c3.body.release.assert_called_once_with()
        owner = ResidentBrain(self.root)
        await owner.close()


if __name__ == '__main__':
    unittest.main()
