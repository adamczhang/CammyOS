"""Profile-scoped voice Markdown: work/AGENT.md and work/skills/<id>.md (c3/instructions.py),
the /v1/instructions routes, and how a save reaches Gemini (the next connection only).

Offline: a real ResidentBrain on a temp folder (no model runs), a fake Gemini Live client.
"""
import asyncio
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
sys.path.insert(0, str(ROOT / 'tests'))

try:                         # aiohttp and the Gemini SDK live in .venv-live
    from aiohttp.test_utils import TestClient, TestServer
    from c3 import api, instructions as I, service
    from c3 import voice as V
    from c3.resident_brain import ResidentBrain
    from c3.transcript import Transcript
    import cammy_persona
    from test_c3 import Ev, FakeClient, FakeLive, FakeSession, Quiet, Thing, msg
except ImportError:
    V = None
needs_genai = unittest.skipUnless(V, 'google-genai/aiohttp not installed (use .venv-live)')
TOKEN = 'secret-token'
AUTH = {'Authorization': 'Bearer ' + TOKEN}


async def no_model(request):
    raise AssertionError('no model runs here')


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        I._last_good.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name).absolute()
        self.brain = ResidentBrain(self.data / 'brain', runner=no_model)
        await self.brain.create_profile('desk', 'Desk helper', 'claude', 'resident-only rules')
        await self.brain.create_profile('garage', 'Garage', 'codex')
        await self.brain.select_profile('desk')
        self.c3 = self.make_c3()
        self.voice = None

    async def asyncTearDown(self):
        task = getattr(self, 'voice_task', None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if getattr(self, 'client', None):
            await self.client.close()
        await self.brain.close()
        self.tmp.cleanup()

    def make_c3(self, camera_down=False):
        c3 = object.__new__(service.C3)
        c3.family = service.familymod.FamilyPolicy(None, deployment='adult-standalone')   # Gemini path
        c3.deployment, c3.cloud, c3.play, c3.local_voice, c3._teardowns = 'adult-standalone', True, None, None, set()
        c3.brain_backend, c3.brain, c3.data_dir, c3._brain_failure = 'resident', self.brain, self.data, None
        c3._face_pack = cammy_persona.DEFAULT_PACK

        def state():
            if camera_down:
                raise ConnectionError('camera away')
            return {'face_pack': cammy_persona.DEFAULT_PACK}
        c3.body = SimpleNamespace(cammy=SimpleNamespace(state=state))
        c3.voice = None
        c3.eyes = SimpleNamespace()
        return c3

    def work(self, profile='desk'):
        return self.data / 'brain/profiles' / profile / 'work'

    async def api(self):
        self.client = TestClient(TestServer(api.make_app(self.c3, TOKEN)))
        await self.client.start_server()
        return self.client

    async def put(self, path, body):
        response = await self.client.put(path, json=body, headers=AUTH)
        return response.status, (await response.json() if response.content_type == 'application/json' else await response.text())


@needs_genai
class Store(Base):
    async def test_seeded_skills_are_compact_editable_markdown(self):
        view = I.snapshot(self.c3.active_profile())
        ids = [s['id'] for s in view['skills']]
        self.assertEqual(ids, sorted(I.SEED_SKILLS))
        self.assertIn('look-around', ids)
        look_around = next(s for s in view['skills'] if s['id'] == 'look-around')
        self.assertIn('[Look around] photos', look_around['text'])
        self.assertLess(sum(len(s['text'].encode()) for s in view['skills']), 1500)
        self.assertEqual(view['agent'], {'text': '', 'revision': None})
        skills = self.work() / 'skills'
        self.assertEqual(stat.S_IMODE(skills.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((skills / 'look-around.md').stat().st_mode), 0o600)
        # Deleted by hand: never reseeded, the rest stay.
        (skills / 'look.md').unlink()
        self.assertNotIn('look', [s['id'] for s in I.snapshot(self.c3.active_profile())['skills']])
        self.assertEqual([p.name for p in self.work().iterdir() if p.name.startswith('.')], [])

    async def test_legacy_seed_is_compacted_only_for_voice_and_only_if_unedited(self):
        I.snapshot(self.c3.active_profile())
        skill = self.work() / 'skills' / 'look.md'
        original = """# Look

Use `look` for one photo straight ahead whenever Adam asks what you see, or about an object,
a person or the room in front of you. Describe what is really in the photo, briefly. If the
photo is dark or blurry, say so instead of guessing.

## Examples
- Adam: "What am I holding?" You call `look`, then: "A blue coffee mug."
- Adam: "How do I look today?" You call `look`, then say something kind and honest about
  what you can see.
- Adam: "Can you read this?" You call `look` and read the text aloud; if it is too small,
  ask him to hold it closer.
"""
        skill.write_text(original)
        self.assertIn(I.SEED_SKILLS['look'].strip(), self.c3.profile_guidance())
        self.assertEqual(skill.read_text(), original)
        skill.write_text(original + '\nDescribe the foreground first.\n')
        self.assertIn(original.strip(), self.c3.profile_guidance())
        self.assertIn('Describe the foreground first.', self.c3.profile_guidance())

    async def test_revisions_create_update_conflict_and_persist(self):
        profile = self.c3.active_profile()
        view = I.save_agent(profile, 'Call Adam "boss".', None)
        rev = view['agent']['revision']
        self.assertEqual(view['agent']['text'], 'Call Adam "boss".\n')
        self.assertEqual((self.work() / 'AGENT.md').read_bytes(), b'Call Adam "boss".\n')
        self.assertEqual(stat.S_IMODE((self.work() / 'AGENT.md').stat().st_mode), 0o600)
        with self.assertRaises(I.Conflict) as caught:
            I.save_agent(profile, 'again', None)               # null when it exists
        self.assertEqual(caught.exception.revision, rev)
        with self.assertRaises(I.Conflict):
            I.save_agent(profile, 'again', 'f' * 64)           # stale
        view = I.save_agent(profile, 'Be brief.', rev)
        self.assertNotEqual(view['agent']['revision'], rev)
        with self.assertRaises(I.Conflict):
            I.save_skill(profile, 'cooking', 'x', 'a' * 64)    # a revision for a missing file
        view = I.save_skill(profile, 'cooking', '# Cooking\nUse look for the pan.', None)
        self.assertIn({'id': 'cooking', 'text': '# Cooking\nUse look for the pan.\n',
                       'revision': I.revision_of(b'# Cooking\nUse look for the pan.\n')}, view['skills'])
        # A new process sees the same files and revisions.
        I._last_good.clear()
        again = I.snapshot(self.c3.active_profile())
        self.assertEqual(again, view)
        self.assertEqual([p.name for p in self.work().rglob('.*')], [])   # no temp files left

    async def test_ids_sizes_and_counts_are_bounded(self):
        profile = self.c3.active_profile()
        for bad in ('../escape', 'A', 'x/y', '.hidden', 'x.md', '-x', 'x-', '', 'a' * 49, 'skills/../x'):
            with self.assertRaises(I.Invalid, msg=bad):
                I.save_skill(profile, bad, 'text', None)
        with self.assertRaises(I.Invalid):
            I.save_agent(profile, 'x' * (I.AGENT_MAX_BYTES + 1), None)
        with self.assertRaises(I.Invalid):
            I.save_skill(profile, 'big', 'é' * (I.SKILL_MAX_BYTES // 2 + 1), None)
        with self.assertRaises(I.Invalid):
            I.save_agent(profile, 'bell\x07', None)
        for n in range(I.MAX_SKILLS - len(I.SEED_SKILLS)):
            I.save_skill(profile, 's%d' % n, 'skill %d' % n, None)
        with self.assertRaises(I.Invalid):
            I.save_skill(profile, 'one-too-many', 'x', None)
        self.assertFalse((self.work().parent / 'escape').exists())

    async def test_symlinks_are_refused_never_followed(self):
        outside = self.data / 'outside.md'
        outside.write_text('outside\n')
        (self.work() / 'AGENT.md').symlink_to(outside)
        profile = self.c3.active_profile()
        with self.assertRaises(I.Conflict):
            I.save_agent(profile, 'hijack', None)
        with self.assertRaises(I.Conflict):
            I.snapshot(profile)
        self.assertEqual(outside.read_text(), 'outside\n')
        (self.work() / 'AGENT.md').unlink()
        I.snapshot(profile)                                   # seeds skills
        (self.work() / 'skills' / 'evil.md').symlink_to(outside)
        view = I.snapshot(profile)
        self.assertNotIn('evil', [s['id'] for s in view['skills']])
        self.assertEqual([p['id'] for p in view['problems']], ['evil'])
        with self.assertRaises(I.Conflict):
            I.save_skill(profile, 'evil', 'hijack', None)
        self.assertEqual(outside.read_text(), 'outside\n')
        # A symlinked skills folder or workspace is refused.
        garage = self.work('garage')
        os.symlink(self.data, garage / 'skills')
        with self.assertRaises(I.Unsafe):
            I.save_skill(('garage', 'Garage', garage), 'x', 'y', None)
        link = self.data / 'linked-work'
        link.symlink_to(self.work('garage'))
        with self.assertRaises(I.Unsafe):
            I.snapshot(('garage', 'Garage', link))
        self.assertEqual(I.compose(('garage', 'Garage', link)), '')

    async def test_profile_switch_before_rename_writes_nothing(self):
        with self.assertRaises(I.Conflict):
            I.save_agent(self.c3.active_profile(), 'late', None, still_active=lambda: False)
        self.assertFalse((self.work() / 'AGENT.md').exists())
        self.assertEqual([p.name for p in self.work().iterdir() if p.name.startswith('.')], [])

    async def test_profiles_are_isolated_and_old_profiles_stay_loadable(self):
        manifest = (self.work().parent / 'profile.json').read_bytes()
        selection = (self.data / 'brain/selection.json').read_bytes()
        desk = self.c3.active_profile()
        I.save_agent(desk, 'Desk only note.', None)
        I.save_skill(desk, 'desk-skill', 'Desk skill.', None)
        await self.brain.select_profile('garage')
        garage = self.c3.active_profile()
        self.assertEqual(garage[0], 'garage')
        view = I.snapshot(garage)
        self.assertEqual(view['agent'], {'text': '', 'revision': None})
        self.assertNotIn('desk-skill', [s['id'] for s in view['skills']])
        self.assertNotIn('Desk only note', I.compose(garage))
        self.assertIn('Desk only note', I.compose(desk))
        # The resident brain's own files are untouched and the checked loader still accepts them.
        self.assertEqual((self.work().parent / 'profile.json').read_bytes(), manifest)
        await self.brain.close()
        self.assertNotEqual((self.data / 'brain/selection.json').read_bytes(), selection)   # garage now
        self.brain = service._load_resident_brain(self.data / 'brain')
        self.assertEqual(self.brain.state['active_profile'], 'garage')
        self.assertEqual({p['id'] for p in self.brain.state['profiles']}, {'desk', 'garage'})
        self.assertEqual(self.brain._profiles['desk']['instructions'], 'resident-only rules')

    async def test_compose_survives_a_disk_blip_with_the_last_good_text(self):
        desk = self.c3.active_profile()
        I.save_agent(desk, 'Keep me.', None)
        good = I.compose(desk)
        self.assertIn('Keep me.', good)
        os.chmod(self.work(), 0o000)
        try:
            self.assertEqual(I.compose(desk), good)
        finally:
            os.chmod(self.work(), 0o700)
        self.assertEqual(I.compose(None), '')


@needs_genai
class Routes(Base):
    async def test_auth_shape_and_no_profile(self):
        await self.api()
        for method, path in (('get', '/v1/instructions'), ('put', '/v1/instructions/agent'),
                             ('put', '/v1/instructions/skills/look')):
            response = await getattr(self.client, method)(path, json={})
            self.assertEqual(response.status, 401, path)
        self.assertFalse((self.work() / 'skills').exists())
        self.c3.voice_tools = lambda: [{'name': 'look', 'description': 'Take one photo'}]
        response = await self.client.get('/v1/instructions', headers=AUTH)
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(sorted(data), ['agent', 'profile', 'profile_name', 'skills', 'tools'])
        self.assertEqual((data['profile'], data['profile_name']), ('desk', 'Desk helper'))
        self.assertEqual(data['tools'], [{'name': 'look', 'description': 'Take one photo'}])
        self.assertEqual(sorted(data['skills'][0]), ['id', 'revision', 'text'])
        self.brain._active = None                              # nothing selected
        data = await (await self.client.get('/v1/instructions', headers=AUTH)).json()
        self.assertEqual((data['profile'], data['profile_name'], data['agent'], data['skills']), (None, None, None, []))
        self.assertIn('No resident brain profile is selected', data['message'])
        status, body = await self.put('/v1/instructions/agent', {'profile': 'desk', 'text': 'x', 'revision': None})
        self.assertEqual(status, 409)
        self.assertFalse((self.work() / 'AGENT.md').exists())

    async def test_put_agent_and_skill_return_the_snapshot_and_409s(self):
        await self.api()
        status, view = await self.put('/v1/instructions/agent', {'profile': 'desk', 'text': 'Hi', 'revision': None})
        self.assertEqual(status, 200)
        self.assertEqual(view['agent']['text'], 'Hi\n')
        self.assertIn('tools', view)
        status, body = await self.put('/v1/instructions/agent', {'profile': 'desk', 'text': 'Hey', 'revision': None})
        self.assertEqual((status, body['revision']), (409, view['agent']['revision']))
        status, body = await self.put('/v1/instructions/agent', {'profile': 'garage', 'text': 'Hey',
                                                                 'revision': view['agent']['revision']})
        self.assertEqual((status, body['active_profile']), (409, 'desk'))
        self.assertFalse((self.work('garage') / 'AGENT.md').exists())
        look = next(s for s in view['skills'] if s['id'] == 'look-around')
        status, view = await self.put('/v1/instructions/skills/look-around',
                                      {'profile': 'desk', 'text': '# Look Around\nAlways count the chairs.',
                                       'revision': look['revision']})
        self.assertEqual(status, 200)
        self.assertIn('count the chairs', next(s for s in view['skills'] if s['id'] == 'look-around')['text'])
        status, _ = await self.put('/v1/instructions/skills/new-one', {'profile': 'desk', 'text': 'New', 'revision': None})
        self.assertEqual(status, 200)
        for path, body in (('/v1/instructions/skills/Bad..id', {'profile': 'desk', 'text': 'x', 'revision': None}),
                           ('/v1/instructions/agent', {'profile': 'desk', 'text': 'x'}),
                           ('/v1/instructions/agent', {'profile': 'desk', 'text': 'x', 'revision': 3}),
                           ('/v1/instructions/agent', {'profile': 'desk', 'text': 'x' * 20000, 'revision': None})):
            status, _ = await self.put(path, body)
            self.assertEqual(status, 400, (path, body))

    async def test_switch_during_a_save_is_409(self):
        await self.api()
        real, calls = self.c3.active_profile, []

        def flaky():                                           # desk for the pin, garage after
            calls.append(1)
            return real() if len(calls) == 1 else ('garage', 'Garage', self.work('garage'))
        self.c3.active_profile = flaky
        status, body = await self.put('/v1/instructions/agent', {'profile': 'desk', 'text': 'x', 'revision': None})
        self.assertEqual(status, 409)
        self.assertNotIn('saved', body)
        self.assertFalse((self.work() / 'AGENT.md').exists())
        self.assertFalse((self.work('garage') / 'AGENT.md').exists())


@needs_genai
class GeminiConfig(Base):
    """What Gemini gets, and that a save waits for the next connection."""

    def make_voice(self, live, handle_file=None):
        self.transcript = Transcript(self.data / 'transcript')
        self.speaker, self.listener = Quiet(), Quiet()
        voice = service.ResidentVoice(FakeClient(live), 'm', self.c3.persona, 'Leda', self.speaker, Thing(),
                                      Thing(), self.listener, self.transcript, brain=self.brain,
                                      idle_s=60, handle_file=handle_file)
        self.c3.voice = voice
        return voice

    async def until(self, check, s=3.0):
        for _ in range(int(s / 0.01)):
            if check():
                return
            await asyncio.sleep(0.01)
        self.fail('timed out')

    def expected_persona(self, agent=None, skills=None):
        base = cammy_persona.persona(cammy_persona.DEFAULT_PACK) + service.RESIDENT_BRAIN_PERSONA
        skills = dict(I.SEED_SKILLS if skills is None else skills)
        text = I.GUIDANCE_HEAD.format(name='Desk helper')
        if agent:
            text += '\n\n## Notes\n' + agent
        text += '\n\n## Skills' + ''.join('\n\n### Skill: %s\n%s' % (k, v.strip()) for k, v in sorted(skills.items()))
        return base + text

    async def test_exact_config_and_a_save_waits_for_the_next_connection(self):
        I.save_agent(self.c3.active_profile(), 'Call Adam "boss".', None)
        first = FakeSession([msg(session_resumption_update=V.types.LiveServerSessionResumptionUpdate(
            resumable=True, new_handle='h1'))])
        live = FakeLive([first, FakeSession([]), FakeSession([])])
        voice = self.make_voice(live)
        self.voice_task = asyncio.create_task(voice.run())
        voice.wake.set()
        await self.until(lambda: voice.handle == 'h1')
        cfg = live.configs[0]
        self.assertEqual(cfg.system_instruction, self.expected_persona('Call Adam "boss".'))
        self.assertEqual([t.name for t in cfg.tools[0].function_declarations], ['look', 'ask_brain'])
        self.assertEqual(cfg.tools[0].function_declarations[1].description, service.RESIDENT_ASK_BRAIN.description)
        self.assertEqual(cfg.response_modalities, ['AUDIO'])
        self.assertEqual(cfg.speech_config.voice_config.prebuilt_voice_config.voice_name, 'Leda')
        self.assertTrue(cfg.realtime_input_config.automatic_activity_detection.disabled)
        self.assertIsNone(cfg.session_resumption.handle)
        self.assertEqual(self.c3.voice_tools(), [{'name': 'look', 'description': V.LOOK.description},
                                                 {'name': 'ask_brain', 'description': service.RESIDENT_ASK_BRAIN.description}])

        # Saving over the API while Cammy is connected: nothing is sent, nothing reconnects.
        await self.api()
        view = I.snapshot(self.c3.active_profile())
        status, _ = await self.put('/v1/instructions/agent', {'profile': 'desk', 'text': 'Say "chief".',
                                                              'revision': view['agent']['revision']})
        self.assertEqual(status, 200)
        await asyncio.sleep(0.3)
        self.assertEqual(first.sent, [])
        self.assertEqual((voice.state, voice.stats['connections'], voice.stats['turns']), ('awake', 1, 0))
        self.assertFalse(voice.wake.is_set())
        self.assertEqual(self.speaker.flushed, 0)

        # The next connection: fresh (handle dropped), with the new text and a summary.
        self.transcript.add('adam', 'my cat is called Miso')
        voice.idle_now()
        await self.until(lambda: voice.state == 'asleep')
        voice.wake.set()
        await self.until(lambda: len(live.configs) == 2 and voice.state == 'awake')
        cfg = live.configs[1]
        self.assertIsNone(cfg.session_resumption.handle)
        self.assertTrue(cfg.system_instruction.startswith(self.expected_persona('Say "chief".')))
        self.assertIn('Miso', cfg.system_instruction)
        self.assertEqual(voice.stats['persona_changes'], 1)

    async def test_unchanged_instructions_resume_and_a_profile_switch_does_not(self):
        live = FakeLive([FakeSession([msg(session_resumption_update=V.types.LiveServerSessionResumptionUpdate(
            resumable=True, new_handle='h1'))]), FakeSession([]), FakeSession([])])
        voice = self.make_voice(live)
        self.voice_task = asyncio.create_task(voice.run())
        voice.wake.set()
        await self.until(lambda: voice.handle == 'h1')
        voice.idle_now()
        await self.until(lambda: voice.state == 'asleep')
        voice.wake.set()
        await self.until(lambda: len(live.configs) == 2 and voice.state == 'awake')
        self.assertEqual(live.configs[1].session_resumption.handle, 'h1')
        self.assertEqual(live.configs[1].system_instruction, self.expected_persona())
        voice.idle_now()
        await self.until(lambda: voice.state == 'asleep')
        await self.brain.select_profile('garage')
        voice.wake.set()
        await self.until(lambda: len(live.configs) == 3 and voice.state == 'awake')
        cfg = live.configs[2]
        self.assertIsNone(cfg.session_resumption.handle)
        self.assertIn('notes for this brain profile (Garage)', cfg.system_instruction)
        self.assertNotIn('Desk helper', cfg.system_instruction)

    async def test_saved_handle_from_before_a_restart_is_dropped_when_instructions_changed(self):
        handle_file = self.data / 'session.json'
        voice = self.make_voice(FakeLive([]), handle_file=handle_file)
        voice.base = self.c3.persona()
        voice.handle = 'h1'
        voice._save_handle()
        I.save_agent(self.c3.active_profile(), 'New rule.', None)
        live = FakeLive([FakeSession([])])
        voice = self.make_voice(live, handle_file=handle_file)          # C3 restarted
        self.assertEqual(voice.handle, 'h1')
        self.voice_task = asyncio.create_task(voice.run())
        voice.wake.set()
        await self.until(lambda: voice.state == 'awake')
        self.assertIsNone(live.configs[0].session_resumption.handle)
        self.assertIn('New rule.', live.configs[0].system_instruction)

    async def test_camera_down_or_brain_gone_still_connects(self):
        I.save_agent(self.c3.active_profile(), 'Rule.', None)
        self.c3 = self.make_c3(camera_down=True)
        self.assertEqual(self.c3.persona(), self.expected_persona('Rule.'))
        self.c3.active_profile = lambda: (_ for _ in ()).throw(RuntimeError('brain blip'))
        self.assertEqual(self.c3.persona(), cammy_persona.persona(cammy_persona.DEFAULT_PACK) + service.RESIDENT_BRAIN_PERSONA)


if __name__ == '__main__':
    unittest.main()
