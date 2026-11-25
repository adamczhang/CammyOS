# Legacy desktop companion core: offline checkpoint

The official Reachy app now has a separate [real finite agent-turn service](../LIVE-CONVERSATION-2026-11-15.md)
using live Cammy PCM, local recognition, a bound agent inbox and opt-in P15
replies. Its acoustic/control tests passed. The provider/engagement engine
described below remains separate; its fake providers were not promoted to live
hardware adapters or silently connected to that new service.

The [push-to-talk engagement controller](PUSH-TO-TALK.md) now provides a local
idle/held/follow-up-window/mute policy above this service. It keeps sensing off
in idle and reuses existing cancellation and lease handling. Physical record-key
mapping/suppression and live audio remain pending; no camera runtime was installed.

As of 2026-11-14, `host/companion/` contains a tick-driven orchestration prototype
above the existing v1 control plane. Development stopped at Adam's request before
the delegate delivered a final review/report. The manager preserved the files and
reproduced 29 passing local tests. A subsequent bounded Stop/lease-loss review
found no code defect and added two regression tests; the manager reproduced
**31 passing companion tests**. See the [chunk 1 report](../work-packages/CHUNK1-COMPANION-STOP.md).
This is not a resident release or hardware acceptance.

The caller constructs `CompanionService`, calls `start()`, supplies inputs and
calls `tick()`, then `stop()`. The service has running, paused, halted and stopped
states with idle, listening, thinking and speaking phases. Construction performs
no body I/O; sensing requires explicit opt-in. Providers implement nonblocking
job polling and cancellation. Bounded queues and generation/epoch checks reject
stale results and actions after interruption, stop or reconnect.

`SimulatorBody` reuses the existing in-process simulator. `SdkBody` uses the
existing SDK with an explicit loopback gateway endpoint and token; using a real
gateway may reach the camera. It performs no discovery or automatic startup.
The tests use an ephemeral loopback fixture, never the resident gateway or camera.

The finite demo is entirely in-process, with a manual clock and scripted fake
providers:

```sh
PYTHONPATH=host python3 -m companion --quiet
PYTHONPATH=host python3 -m companion --profile stock --quiet
PYTHONPATH=host python3 -m companion --fault disconnect --quiet
python3 -m unittest tests.test_companion
```

Tests cover ownership and lease loss, interruption, delayed provider results,
input limits, capability refusal, synthetic sensing, unsupported PCM playback,
HTTP authentication and simulated disconnect/reconnect. Synthetic data remains
labeled synthetic; speech duration is a provider estimate, and gaze/face state
is commanded rather than measured. Local cancellation or a release response
does not prove hardware cleanup. The normal release path is owner-scoped;
global Stop is not sent after another controller takes the lease.

Chunk 1 specifically covers pending jobs that ignore cancellation, queued work
at emergency Stop, lease takeover during multi-chunk speech, local expiry during
a delayed agent turn, and stale synthesis after restart/reacquisition. Already
dispatched speech has no per-utterance recall RPC in v1; rejecting future work
does not prove that physical playback stopped.

Remaining work includes a completed integration review, real fresh media input,
STT/agent/TTS adapters, measured playback completion and interruption, real body
adapters, streaming/full-duplex audio, resident installation and endurance tests.
No system service was installed, model downloaded, microphone recorded or speaker
played by this package. The camera runtime and release number remain unchanged.
