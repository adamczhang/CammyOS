"""Deterministic fake providers and a manual clock for tests and the offline demo.

Everything here is synthetic. The scripted transcriber ignores the audio it is
given; it is not speech recognition. Text speech only paces the speaking phase
by a character-rate estimate; nothing here renders or plays audio.
SPDX-License-Identifier: MIT
"""
from .contracts import AgentReply, CANCELLED, Outcome, SpeechClip, Transcript


class ManualClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        if seconds < 0:
            raise ValueError('monotonic clock cannot go backwards')
        self.now += seconds
        return self.now


class ClockJob:
    """Finishes at ``ready_at``. ``ignore_cancel`` models a provider that keeps working."""
    def __init__(self, clock, ready_at, outcome, ignore_cancel=False):
        self.clock, self.ready_at, self.outcome = clock, ready_at, outcome
        self.ignore_cancel, self.cancelled = ignore_cancel, False

    def poll(self):
        if self.cancelled and not self.ignore_cancel:
            return Outcome(error=CANCELLED)
        return self.outcome if self.clock() >= self.ready_at else None

    def cancel(self):
        self.cancelled = True


class ScriptedAgent:
    name, synthetic = 'scripted-fake-agent', True

    def __init__(self, clock, replies=(), delay=0.5, ignore_cancel=False):
        self.clock, self.delay, self.ignore_cancel = clock, delay, ignore_cancel
        self.replies = replies if callable(replies) else list(replies)
        self.requests, self.jobs = [], []

    def start(self, request):
        self.requests.append(request)
        if callable(self.replies):
            reply = self.replies(request)
        elif self.replies:
            reply = self.replies.pop(0)
        else:
            reply = '[synthetic reply] received: ' + request.text[:80]
        outcome = reply if isinstance(reply, Outcome) else Outcome(AgentReply(reply) if isinstance(reply, str) else reply)
        job = ClockJob(self.clock, self.clock() + self.delay, outcome, self.ignore_cancel)
        self.jobs.append(job)
        return job


class ScriptedTranscriber:
    name, synthetic = 'scripted-fake-transcriber', True

    def __init__(self, clock, texts=(), delay=0.2, ignore_cancel=False):
        self.clock, self.delay, self.ignore_cancel = clock, delay, ignore_cancel
        self.texts, self.clips, self.jobs = list(texts), [], []

    def start(self, clip):
        self.clips.append(clip)
        text = self.texts.pop(0) if self.texts else None
        job = ClockJob(self.clock, self.clock() + self.delay, Outcome(Transcript(text, True)), self.ignore_cancel)
        self.jobs.append(job)
        return job


class EstimatedTextSpeech:
    """Asks the body to speak text (/v1/speak); duration is an estimate, not playback."""
    name, synthetic = 'estimated-text-speech', True

    def __init__(self, clock, chars_per_second=15.0, delay=0.0):
        self.clock, self.cps, self.delay = clock, chars_per_second, delay
        self.texts = []

    def start(self, text):
        self.texts.append(text)
        clip = SpeechClip(text, round(0.3 + len(text) / self.cps, 3))
        return ClockJob(self.clock, self.clock() + self.delay, Outcome(clip))


class FixturePcmSpeech:
    """Returns silent fixture PCM. The v1 body has no PCM playback, so it is refused."""
    name, synthetic = 'fixture-pcm-speech', True

    def __init__(self, clock):
        self.clock = clock

    def start(self, text):
        clip = SpeechClip(text, 0.1, pcm=b'\0\0' * 1600, sample_rate=16000)
        return ClockJob(self.clock, self.clock(), Outcome(clip))
