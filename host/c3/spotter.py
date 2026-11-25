"""Name spotter for the talk gate: a small local Whisper in its own process.

The gate holds quiet utterances and asks this whether they name Cammy. Whisper runs
in a child process (python -m c3.spotter MODEL) so its CPU time never blocks the
event loop that streams audio, and a crash costs one utterance, not the service.

Model: whisper.cpp tiny.en (78 MB; ~/cammy-models or $CAMMY_SPOTTER_MODEL). Measured
2026-11-23 on the PC: 30-40 ms for a 3 s clip with audio_ctx 512 and 8 threads, and
with 0.5 s of lead-in silence it wrote "Cammy" for all six test clips that said it
(Kokoro and two Piper voices) and for none of those that did not. The prompt "Cammy"
teaches it the spelling.

Wire format: the parent writes a 4-byte little-endian length and that many bytes of
S16LE 16 kHz mono PCM; the child answers one JSON line {"text": ..., "ms": ...}.
"""
import asyncio
import json
import logging
import os
import struct
import sys
import time
from pathlib import Path

log = logging.getLogger('c3.spotter')

MODEL = Path(os.environ.get('CAMMY_SPOTTER_MODEL', str(Path.home() / 'cammy-models' / 'ggml-tiny.en.bin')))
TIMEOUT_S = 3.0
START_TIMEOUT_S = 20.0
RETRY_S = 60.0           # after a failure, wait this long before starting a new child


class Spotter:
    def __init__(self, model=MODEL, python=sys.executable, clock=time.monotonic):
        self.model, self.python, self.clock = Path(model), python, clock
        self.proc = None
        self.lock = asyncio.Lock()
        self.calls = self.failures = 0
        self.last_ms = None
        self.last_error = None
        self.retry_at = None

    @property
    def state(self):
        return {'model': self.model.name, 'running': self.proc is not None and self.proc.returncode is None,
                'calls': self.calls, 'failures': self.failures, 'last_ms': self.last_ms, 'last_error': self.last_error}

    async def _start(self):
        if not self.model.is_file():
            raise RuntimeError(f'no spotter model at {self.model}')
        host = Path(__file__).resolve().parent.parent
        self.proc = await asyncio.create_subprocess_exec(
            self.python, '-m', 'c3.spotter', str(self.model), cwd=str(host),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        line = await asyncio.wait_for(self.proc.stdout.readline(), START_TIMEOUT_S)
        if json.loads(line or b'{}').get('ready') is not True:
            raise RuntimeError('spotter did not start')

    async def transcribe(self, pcm):
        """Text Whisper heard in S16LE 16 kHz PCM. Raises when the spotter is unavailable."""
        async with self.lock:
            if self.retry_at is not None and self.clock() < self.retry_at:
                raise RuntimeError(f'spotter resting after: {self.last_error}')
            self.calls += 1
            try:
                if self.proc is None or self.proc.returncode is not None:
                    await self._start()
                self.proc.stdin.write(struct.pack('<I', len(pcm)) + pcm)
                await self.proc.stdin.drain()
                reply = json.loads(await asyncio.wait_for(self.proc.stdout.readline(), TIMEOUT_S))
            except Exception as exc:
                self.failures += 1
                self.last_error = f'{type(exc).__name__}: {exc}'[:200]
                self.retry_at = self.clock() + RETRY_S
                self.close()
                raise
            self.retry_at = None
            self.last_ms = reply.get('ms')
            return reply.get('text', '')

    def close(self):
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()
        self.proc = None


def serve(model):
    import numpy as np
    from pywhispercpp.model import Model
    m = Model(model, n_threads=min(8, os.cpu_count() or 4), print_progress=False, print_realtime=False,
              redirect_whispercpp_logs_to=None)
    out = sys.stdout
    out.write('{"ready": true}\n')
    out.flush()
    inp = sys.stdin.buffer
    while True:
        head = inp.read(4)
        if len(head) < 4:
            return
        (n,) = struct.unpack('<I', head)
        pcm = inp.read(n)
        t = time.monotonic()
        audio = np.frombuffer(pcm, '<i2').astype(np.float32) / 32768.0
        # audio_ctx 1500 is Whisper's 30 s window; a shorter one runs the encoder on
        # less padding. Keep a margin so the end of the clip is inside it.
        ctx = min(1500, max(512, int(len(audio) / 16000 / 30 * 1500) + 128))
        # temperature_inc 0: no retries at higher temperature, which took 0.6-0.9 s
        segments = m.transcribe(audio, initial_prompt='Cammy', no_context=True, single_segment=True,
                                audio_ctx=ctx, temperature_inc=0.0)
        text = ' '.join(s.text for s in segments).strip()
        out.write(json.dumps({'text': text, 'ms': round((time.monotonic() - t) * 1000)}) + '\n')
        out.flush()


if __name__ == '__main__':
    serve(sys.argv[1])
