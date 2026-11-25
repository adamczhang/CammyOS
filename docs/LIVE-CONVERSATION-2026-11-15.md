# Finite live agent turns — 2026-11-15

The official Cammy Reachy app now closes a real acoustic turn: a known phrase
played on the P15 was captured by Cammy, transcribed locally, delivered through
the agent inbox, answered through the bound reply API and spoken on the P15.
Cammy's microphone then captured that reply and local recognition recovered it
while video continued. Stop returned the system to idle with ALSA capture CLOSED.

This is a finite turn service for an attached agent, not an autonomous model
provider. The manager supplied the test reply through the public agent client.
**Output is the PC's P15 speaker, not Cammy's speaker.** The face changes are
on the PC preview; native LCD display remains unverified. No camera firmware,
init, TF installation, native app restart or camera reboot occurred.

## User and agent flow

The existing official Reachy app at127.0.0.1:8801 now has a conversation panel.
It starts idle. Open a5–90second session, default60; speech output requires the
explicit P15 checkbox. Hold Listen while speaking, then release to recognize
one utterance. A press is capped at8seconds; short clips are discarded. No audio
before the press is submitted for recognition. No VAD or physical button mapping
is claimed, and there is no automatic follow-up capture in this increment.

Opening the session activates the existing live video/microphone transport.
Holding gates recognition, not physical microphone power. Stop or expiry
releases the app's media session. The existing native-preview/microphone
relationship remains; software release is not proof of an instantaneous
acoustic/physical mute.

An agent polls the recognized turn, optionally inspects current frames through
the existing Reachy MediaManager extension, and supplies one reply. Text-only
replies are supported with output disabled. No hosted speech/model API is
selected or called. Recognized turns, microphone PCM and reply PCM stay in RAM;
they are not written to media files or added to model prompts automatically.

```python
# PYTHONPATH=sdk; requires the already-running official Cammy Reachy app.
from cammy_conversation_client import CammyConversation

cammy = CammyConversation()  # no sensing or playback
cammy.start(seconds=60, speaker=True)  # explicit P15 output
# Wait until cammy.status()['phase'] == 'ready'.
cammy.listen(seconds=8)
# Speak, then:
cammy.release()
# Poll cammy.turn(); None means no current recognized turn.
turn = cammy.turn()
if turn is not None:
    cammy.reply(turn['turn'], 'Hello. I can hear you through Cammy.')
# Always stop the owned session in the caller's finally block:
cammy.stop()
```

This SDK client is standard-library-only. The service is an app-specific
extension within the existing authenticated Reachy app, not a replacement
robot protocol or a full Cammy-backed ReachyMini body daemon. The accepted
[official SDK media extension](archive/REACHY-MEDIA-2026-11-15.md) can attach to the
same explicit session for fresh video/audio.

## Implementation and boundaries

`host/cammy_conversation.py` is a serialized finite state machine:

`idle → starting → ready → listening → recognizing → awaiting-agent →
synthesizing → playing → ready`, with Stop/expiry returning to idle.

Readiness requires a current boot/session and both fresh video and audio.
While active, missing/stale media, a history gap, sequence reset, boot change,
session change or session expiry ends the conversation. Capture consumes only
new sequences from the existing mono16k rolling PCM ring and is capped at256kB.
Only one job and one current inbox turn are admitted. Recognition older than
20seconds is discarded; a recognized turn expires after at most20seconds and
always by the session deadline. A reply is bound to exact session+turn, accepted
once, capped at160 plain-text characters and checked against current media.

Stop clears the capture buffer, transcript inbox, queued reply and current job.
Canceled jobs are retained only until reaped; late results cannot be accepted.
There is no global body Stop fallback when another app session has replaced
this one. A status-check/stop binding race still invalidates local work and
reports that media stop was unconfirmed. Pending speech, decoder, stream and
WebRTC cleanup block starting another session; the UI shows that wait.

`host/conversation_worker.py` uses the already-installed voice-keys environment:
local whisper.cpp small.en on CPU and Kokoro/af_heart for synthesis. Models are
read from existing SERVE/models paths, offline flags are set and no download or
system installation is performed. Each job is an owned subprocess with bounded
input/output, a deadline, cancellation and kill/reap. The existing parent-death
exec shim also covers speech workers and PipeWire playback. Replies go only to
the explicitly identified P15 sink, without a default-device fallback. PCM
output is mono24k, capped at15seconds; synthesis and capture run off the GUI/API
thread. The ParamFace preview uses neutral/doubt/happy as turn feedback, without
claiming lip synchronization or physical LCD output.

The legacy `host/companion/service.py` provider/engagement prototype remains a
separate locally tested engine. This increment provides real live turns for
external agents in the official app; it does not silently connect the legacy
fake providers or engage a general language model.

## Authenticated app API

All endpoints reuse the app's session token, strict loopback Host/Origin checks,
bounded JSON requests and no-store responses. They are not exposed on the LAN.

| Method / path | Request / result |
| --- | --- |
|GET `/api/conversation/status`|Phase, current inbox, boot/session, output destination and cleanup flags|
|POST `/api/conversation/start`|`seconds`, `speaker`; starts a new finite media session|
|POST `/api/conversation/listen`|`session`, optional `seconds`1–8; starts recognition capture|
|POST `/api/conversation/finish`|`session`; releases the desktop utterance gate|
|POST `/api/conversation/reply`|`session`, `turn`, `text`; consumes one current agent turn|
|POST `/api/conversation/stop`|`session`; invalidates work and stops only that media session|

Unknown fields/invalid limits return400. Wrong, replayed, expired or unavailable
session/turn returns409. Physical PTT, VAD, native speaker and LCD flags remain
false. Existing direct live-media Stop also causes conversation invalidation.

## Hardware evidence

The final controlled round used a known generated cue and a manager-authored
reply. The microphone input was real acoustic P15→Cammy, not PCM injection.
Cammy's return audio independently recovered the reply's content, including
“the live video is still arriving.” Recognition misheard the name Cammy, so
this is not a perfect voice-quality claim.

| Observation | Result |
| --- | --- |
|Fresh video/audio ready|1.852s after session start, one sample|
|Utterance release → inbox|About1.316s, including local recognition startup|
|Bound reply → playback phase|About3.442s, including local synthesis startup|
|Video frames before recognition / after recognition / after spoken reply|6 /33 /71|
|Final received media|78frames;251,200 mono audio samples|
|Acoustic reply recovery|Reply content recognized from Cammy's rolling microphone buffer|
|Final Stop cleanup observed|1.050s after PC Stop; owner null, preview off, workers/decoder/WebRTC clean, ALSA CLOSED|

These are PC-receipt/control observations from one round, not measured sensor
exposure latency, a broad latency benchmark or acoustic cutoff latency. No new
bandwidth/cap claim follows. The earlier15Mb/s target remains an acceptance goal.

Two further real control trials passed: Stop during recognition canceled/reaped
the worker and refused its stale reply with409; automatic expiry of a7second
session also returned to idle. Complete cleanup, including driver CLOSED and
WebRTC completion, was observed1.057s after Stop and1.079s after observing expiry
idle respectively. Original boot, stock PID/start, watchdog and file hashes
stayed unchanged; the acoustic round also preserved file-backed mappings.

An initial cleanup check was premature: PC release completed before ALSA CLOSED.
A follow-up read showed closure, and the final tests explicitly polled both
transport/worker cleanup and the driver. An early-start test also exposed a
ready-before-first-audio bug; freshness gating now has a regression. No failed
or premature check is treated as hardware success.

Private evidence: `private/conversation/acoustic-trial.json`,
`control-trials.json`, generated cue, bounded trial scripts and focused-test logs.
Raw microphone PCM was not saved by this implementation/trial.

## Verification and remaining work

Focused tests cover construction remaining idle, capture boundaries, bounded
jobs/pipes, cancellation and late results, media freshness/gaps/boot/session
changes, one-shot reply admission, expiry, replacement-owner isolation,
cleanup gating, authenticated schemas and an actual client/app-route round trip.
The full app/media/WebRTC profile must run with real sensing idle because its
upstream SDK fixtures use the same fixed signalling port8443.

`make test-reachy-media` passed70 tests with no skips, including22 conversation
tests. The updated JavaScript also passed the Node ES-module syntax check.

The next jumps are: connect a chosen resident agent/model to the inbox; improve
model residency/response timing; add an in-session speech detector and follow-up
policy; then map the physical record button without stock recording effects.
Cammy speaker streaming/duplex, Wi-Fi motor ownership, the native LCD trial and
full Reachy body compatibility remain separate unfinished hardware outcomes.
No permanent background listening or new system service was enabled.
