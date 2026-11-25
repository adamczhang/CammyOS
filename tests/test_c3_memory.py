"""C3 lasting memory (2026-11-16; vault copy 2026-11-23): local facts, shared vault copy, tools,
resume handle on disk. No network: a fake shared copy, plus the real VaultCopy on a temp folder.
    .venv-live/bin/python -m unittest tests.test_c3_memory
"""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
from c3 import memory as MM  # noqa: E402
from c3 import transcript as T  # noqa: E402
from c3 import voice as V  # noqa: E402


class FakeShared:
    def __init__(self):
        self.docs, self.deleted = {}, []

    def retain(self, doc_id, text, when):
        self.docs[doc_id] = text

    def delete(self, doc_id):
        self.deleted.append(doc_id)
        self.docs.pop(doc_id, None)

    def recall(self, query, max_tokens=700):
        return [t for t in self.docs.values() if any(w in t.lower() for w in query.lower().split())]


def settle():
    time.sleep(0.05)   # background vault-copy threads


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.hs = FakeShared()
        self.m = MM.Memory(self.dir.name, shared=self.hs)

    def test_remember_keeps_a_local_and_a_shared_copy(self):
        self.assertEqual(self.m.remember("Adam's favourite colour is green"), (True, 'remembered'))
        settle()
        self.assertEqual([f['text'] for f in self.m.facts()], ["Adam's favourite colour is green"])
        self.assertEqual(len(self.hs.docs), 1)
        self.assertTrue(next(iter(self.hs.docs)).startswith('cammy-fact-'))
        self.assertIn('green', self.m.brief())
        self.assertEqual(self.m.remember("adam's favourite colour is green")[1], 'already remembered')

    def test_secrets_are_refused(self):
        for text in ['The wifi password is hunter2', 'Bank PIN 4821', 'my api key is abc', 'code 123456']:
            self.assertFalse(self.m.remember(text)[0], text)
        self.assertEqual(self.m.facts(), [])
        self.assertTrue(self.m.remember('Adam was born in 1990')[0])     # years are fine

    def test_forget_removes_both_copies_and_survives_a_restart(self):
        self.m.remember('Mia likes dinosaurs')
        self.m.remember('Adam drinks oat milk')
        settle()
        count, gone = self.m.forget('dinosaurs')
        settle()
        self.assertEqual((count, gone), (1, ['Mia likes dinosaurs']))
        self.assertEqual(len(self.hs.deleted), 1)
        again = MM.Memory(self.dir.name, shared=FakeShared())     # a C3 restart
        self.assertEqual([f['text'] for f in again.facts()], ['Adam drinks oat milk'])

    def test_recall_merges_local_and_shared(self):
        self.m.remember('Mia likes dinosaurs')
        settle()
        self.hs.docs['other'] = 'Mia goes to school on Mondays'
        found = self.m.recall('Mia')
        self.assertEqual(found[0], 'Mia likes dinosaurs')
        self.assertTrue(any('school' in t for t in found))

    def test_shared_copy_down_keeps_local_memory(self):
        class Down:
            def retain(self, *a):
                raise OSError('refused')

            def recall(self, *a, **k):
                raise OSError('refused')
        m = MM.Memory(self.dir.name, shared=Down())
        self.assertTrue(m.remember('Adam likes tea')[0])
        settle()
        self.assertEqual(m.recall('tea'), ['Adam likes tea'])
        self.assertGreaterEqual(m.stats['shared_errors'], 2)


class VaultCopyTests(unittest.TestCase):
    def test_notes_are_written_searched_and_deleted(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'bots').mkdir()
            v = MM.VaultCopy(Path(root) / 'bots' / 'cammy')
            v.retain('cammy-fact-1', 'Mia likes dinosaurs (Adam told Cammy on 2026-11-23)', '2026-11-23T09:00:00-0600')
            v.retain('cammy-fact-2', 'Adam drinks oat milk (Adam told Cammy on 2026-11-23)', '2026-11-23T09:01:00-0600')
            note = (Path(root) / 'bots/cammy/cammy-fact-1.md').read_text()
            self.assertIn('type: cammy-fact', note)
            self.assertIn('date: 2026-11-23', note)
            self.assertEqual(v.recall('what does Mia like'), ['Mia likes dinosaurs (Adam told Cammy on 2026-11-23)'])
            v.delete('cammy-fact-1')
            v.delete('cammy-fact-1')                      # deleting twice is fine
            self.assertEqual(v.recall('Mia dinosaurs'), [])
            m = MM.Memory(Path(root) / 'local', shared=v)
            m.remember('Adam likes tea')
            settle()
            self.assertEqual(m.recall('tea'), ['Adam likes tea'])

    def test_missing_vault_means_local_only(self):
        with self.assertRaises(OSError):
            MM.VaultCopy(Path('/nonexistent/vault/bots/cammy'))

class Call:
    def __init__(self, name, args):
        self.name, self.args, self.id = name, args, 'c1'


class Transcript:
    def add(self, *a):
        pass


class VoiceMemoryTests(unittest.TestCase):
    def voice(self, folder, **kw):
        v = V.Voice.__new__(V.Voice)
        v.memory = MM.Memory(folder, shared=FakeShared())
        v.transcript, v.moves, v.camera, v.brain, v.turn = Transcript(), None, None, None, None
        v.handle_file, v.handle, v.base = kw.get('handle_file'), None, None
        return v

    def test_tools_offered_and_answered(self):
        with tempfile.TemporaryDirectory() as d:
            v = self.voice(d)
            self.assertTrue({'remember', 'recall_memory', 'forget'} <= {t.name for t in v.tools()})
            r = asyncio.run(v._tool(Call('remember', {'fact': 'Adam has a cat called Fur'})))
            self.assertEqual(r.response, {'result': 'remembered'})
            r = asyncio.run(v._tool(Call('recall_memory', {'query': 'cat'})))
            self.assertEqual(r.response['result'], ['Adam has a cat called Fur'])
            self.assertIn('Adam has a cat called Fur', v._memory_text())
            r = asyncio.run(v._tool(Call('forget', {'about': 'cat'})))
            self.assertIn('forgot 1 fact', r.response['result'])

    def test_resume_handle_survives_a_restart_unless_the_persona_changed(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'session.json'
            v = self.voice(d, handle_file=f)
            v.handle, v.base = 'h1', 'persona A'
            v._save_handle()
            w = self.voice(d, handle_file=f)
            w._load_handle()
            self.assertEqual(w.handle, 'h1')
            data = json.loads(f.read_text())
            data['time'] -= 3 * 3600                                 # too old to resume
            f.write_text(json.dumps(data))
            x = self.voice(d, handle_file=f)
            x._load_handle()
            self.assertIsNone(x.handle)


class TranscriptRestartTests(unittest.TestCase):
    def test_the_last_conversation_is_summarised_after_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            t = T.Transcript(d)
            t.add('adam', 'My cat is called Fur')
            t.add('cammy', 'Fur is a lovely name!')
            t2 = T.Transcript(d)
            self.assertIn('My cat is called Fur', t2.recent_text())
            self.assertEqual(t2.since(0)[0], [])                     # not sent to the brain again


class HalfDuplexGateTests(unittest.TestCase):
    def test_the_mic_stays_closed_while_a_reply_is_on_its_way(self):
        from c3 import service as SV
        c3 = SV.C3.__new__(SV.C3)

        class Speaker:
            def muted(self):
                return False

        class Voice_:
            in_turn = True
        c3.speaker, c3.voice = Speaker(), Voice_()
        self.assertTrue(c3.mic_closed())
        c3.voice.in_turn = False
        self.assertFalse(c3.mic_closed())


if __name__ == '__main__':
    unittest.main()
