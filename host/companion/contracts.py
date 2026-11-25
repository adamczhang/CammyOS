"""Provider contracts and value types for the Cammy desktop companion.

Every provider call returns a Job: ``poll()`` is non-blocking and returns None
while pending or an Outcome when finished; ``cancel()`` is a request only. The
service never trusts a provider to honour cancellation: it checks a work token
before accepting any result and again before any body action is dispatched.
SPDX-License-Identifier: MIT
"""
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Tuple

# Evidence labels. None of these means "physically verified"; this layer cannot
# verify physical audio, motion or display behaviour.
TYPED = 'typed'                    # text a desktop user submitted; not heard audio
SYNTHETIC = 'synthetic'            # produced by a fake/scripted provider or simulator
LIVE_UNVERIFIED = 'live-unverified'  # a non-fake provider; quality/behaviour unmeasured


CANCELLED = 'cancelled'   # Outcome.error a job may return to acknowledge cancel()


@dataclass(frozen=True)
class Outcome:
    value: Any = None
    error: Optional[str] = None


class Job(Protocol):
    def poll(self) -> Optional[Outcome]: ...
    def cancel(self) -> None: ...


@dataclass(frozen=True)
class AudioClip:
    """PCM returned by the body adapter. ``synthetic`` clips are not microphone audio."""
    pcm: bytes
    sample_rate: int
    channels: int
    duration_s: float
    synthetic: bool
    source: str
    captured_at: float


@dataclass(frozen=True)
class Transcript:
    text: Optional[str]            # None: nothing recognised
    synthetic: bool


@dataclass(frozen=True)
class AgentRequest:
    text: str
    input_evidence: str
    history: Tuple[Tuple[str, str], ...]   # bounded (role, text) pairs, memory only
    capabilities: dict                     # what the body can do this session


@dataclass(frozen=True)
class AgentReply:
    text: str
    gaze_pan_degrees: Optional[float] = None


@dataclass(frozen=True)
class SpeechClip:
    """A synthesizer result. ``pcm`` None means body-side text speech (/v1/speak).

    ``duration_s`` is the provider's estimate used to pace the speaking phase; it
    is not a measurement of playback.
    """
    text: str
    duration_s: float
    pcm: Optional[bytes] = None
    sample_rate: int = 0
    synthetic: bool = True


class Transcriber(Protocol):
    name: str
    synthetic: bool
    def start(self, clip: AudioClip) -> Job: ...      # Outcome.value: Transcript


class AgentProvider(Protocol):
    name: str
    synthetic: bool
    def start(self, request: AgentRequest) -> Job: ...  # Outcome.value: AgentReply


class SpeechSynthesizer(Protocol):
    name: str
    synthetic: bool
    def start(self, text: str) -> Job: ...             # Outcome.value: SpeechClip


@dataclass
class Submission:
    accepted: bool
    reason: str = 'queued'
    details: dict = field(default_factory=dict)
