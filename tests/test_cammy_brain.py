"""host/cammy_brain.py: which brain replies get spoken (no T3, no network).

.venv-live/bin/python -m unittest tests.test_cammy_brain
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import cammy_brain  # noqa: E402


def shown(active, *messages):
    return {'thread': {'activeRunId': active},
            'messages': [{'role': role, 'runId': run, 'text': text} for role, run, text in messages]}


class FakeBrain(cammy_brain.Brain):
    def __init__(self, pages):
        super().__init__('thread-1', cli='/bin/false')
        self.pages, self.sent = list(pages), []

    def _run(self, args, data=None, timeout=15):
        if args[1] == 'send':
            self.sent.append(data.decode())
            return {}
        return self.pages.pop(0)


class BrainTests(unittest.TestCase):
    def run_polls(self, brain, polls):
        async def go():
            for _ in range(polls):
                await brain.poll()
            return [brain.replies.get_nowait() for _ in range(brain.replies.qsize())]
        return asyncio.run(go())

    def test_only_final_reply_of_new_finished_runs_is_spoken(self):
        old = ('assistant', 'r1', 'An answer from before this session.')
        pages = [
            shown(None, old),                                            # baseline
            shown('r2', old, ('user', 'r2', 'q'), ('assistant', 'r2', 'Let me check.')),  # still running
            shown(None, old, ('assistant', 'r2', 'Let me check.'), ('assistant', 'r2', 'It is BetterBench.')),
            shown(None, old, ('assistant', 'r2', 'It is BetterBench.')),  # seen: not again
            shown(None, ('assistant', 'r3', 'noted'), ('assistant', 'r4', 'The note is done.')),
        ]
        self.assertEqual(self.run_polls(FakeBrain(pages), 5), ['It is BetterBench.', 'The note is done.'])

    def test_transcript_acknowledgement_does_not_wake_voice(self):
        ack = shown(None, ('user', 'sync', '[Cammy transcript]\nAdam: hello'),
                    ('assistant', 'sync', 'Noted. The camera heard Adam.'))
        self.assertEqual(self.run_polls(FakeBrain([shown(None), ack]), 2), [])

    def test_requests_and_transcripts_carry_their_headers(self):
        brain = FakeBrain([])
        asyncio.run(brain.ask('What is on my calendar?'))
        asyncio.run(brain.transcript(['Adam: hi', 'Cammy: hello']))
        asyncio.run(brain.transcript([]))  # nothing to send
        self.assertEqual(brain.sent, ['[Cammy voice request]\nWhat is on my calendar?',
                                      '[Cammy transcript]\nAdam: hi\nCammy: hello'])
        self.assertEqual(brain.pending, 1)


if __name__ == '__main__':
    unittest.main()
