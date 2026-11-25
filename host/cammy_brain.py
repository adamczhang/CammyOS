"""Bridge from the Gemini Live loop to Cammy's brain thread in T3 (docs/BRAIN.md).

One path, three uses, all through the installed T3 CLI (`t3 thread send|show`, ~0.2 s):
  ask(text)        Adam's request -> "[Cammy voice request]" in the brain thread; returns
                   at once, the reply arrives later through watch().
  watch()          polls the thread once a second; the final reply of every finished brain
                   run (direct answers and later delegated results alike) goes to
                   `replies` for Gemini to speak.
  transcript(..)   at session end, the conversation goes to the brain for memory.
brief() reads brain/context.md for the session's system instruction (no round trip).
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'private/brain/thread.json'
CONTEXT = ROOT / 'brain/context.md'
CLI = Path(os.environ.get('T3CODE_CLI_PATH') or Path.home() / '.t3/bin/t3')
QUIET = {'noted', 'noted.'}


def thread_id():
    """The brain thread: CAMMY_BRAIN_THREAD, else private/brain/thread.json."""
    if os.environ.get('CAMMY_BRAIN_THREAD'):
        return os.environ['CAMMY_BRAIN_THREAD']
    try:
        return json.loads(CONFIG.read_text())['thread_id']
    except (OSError, ValueError, KeyError):
        return None


def brief(limit=1500):
    try:
        text = CONTEXT.read_text().strip()
    except OSError:
        return ''
    return text[:limit]


class Brain:
    def __init__(self, thread, cli=CLI, poll_s=1.0, idle_poll_s=None):
        # C3 polls every idle_poll_s while no request is pending (a T3 CLI call each time)
        self.thread, self.cli, self.poll_s = thread, str(cli), poll_s
        self.idle_poll_s = idle_poll_s or poll_s
        self.replies = asyncio.Queue()
        self.spoken = set()        # run IDs whose reply was already delivered or predates us
        self.pending = 0           # requests sent and not yet answered
        self.baseline = False

    def _run(self, args, data=None, timeout=15):
        out = subprocess.run([self.cli, *args], input=data, capture_output=True, timeout=timeout)
        if out.returncode:
            raise RuntimeError('t3 %s failed: %s' % (args[1], out.stderr.decode(errors='replace')[-200:]))
        return json.loads(out.stdout)

    async def send(self, header, text):
        body = ('%s\n%s' % (header, text)).encode()
        await asyncio.to_thread(self._run, ['thread', 'send', self.thread, '-', '--json'], body)

    async def ask(self, text):
        await self.send('[Cammy voice request]', text)
        self.pending += 1

    async def transcript(self, lines):
        if lines:
            await self.send('[Cammy transcript]', '\n'.join(lines)[-12000:])

    def _finished(self, shown):
        """(run_id, final text) for each finished run in the latest messages."""
        active = shown['thread'].get('activeRunId')
        # Transcript sync is bookkeeping. A verbose acknowledgement must not wake Voice.
        transcript_runs = {m.get('runId') for m in shown.get('messages') or []
                           if m.get('role') == 'user' and (m.get('text') or '').startswith('[Cammy transcript]\n')}
        last = {}
        for m in shown.get('messages') or []:
            if m.get('role') == 'assistant' and m.get('runId') and m.get('runId') != active:
                last[m['runId']] = '' if m['runId'] in transcript_runs else (m.get('text') or '').strip()
        return last

    async def poll(self):
        shown = await asyncio.to_thread(self._run, ['thread', 'show', self.thread, '--messages', '8', '--json'])
        runs = self._finished(shown)
        if not self.baseline:  # replies from before this session are not spoken
            self.spoken.update(runs)
            self.baseline = True
            return
        for run, text in runs.items():
            if run in self.spoken:
                continue
            self.spoken.add(run)
            if len(self.spoken) > 500:   # hours-long services: keep the newest IDs only
                self.spoken = set(list(runs)) | {run}
            if text and text.lower() not in QUIET:
                self.pending = max(0, self.pending - 1)
                await self.replies.put(text)

    async def watch(self):
        failures = 0
        while True:
            try:
                await self.poll()
                failures = 0
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
                failures += 1
                if failures in (1, 10):
                    print('[brain] watch failed (%d): %s' % (failures, e), flush=True)
            await asyncio.sleep(self.poll_s if self.pending else self.idle_poll_s)
