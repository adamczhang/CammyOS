# Agent-to-Cammy voice path: audit and target design (2026-11-16)

The single PC service is named **Cammy Control Center (C3)** (Adam, 2026-11-16).

Read-only audit of the code at 82e4c02. Adam's goals: fewer, more capable control
components; hours-long (always-on) use instead of the 5-minute default; dead air never
sent to the paid Gemini API; one speech gate serving everything.

## What exists today

- **Three stacks that each open their own lease, preview and mic:**
  - `host/cammy_gemini_live.py`, the Gemini voice, which also runs the speaker, face,
    capture, `look` and brain;
  - `host/cammy_live.py` with `cammy_agent_core/service/cli/client/mcp.py` (:8791) and
    `conversation_worker.py` (whisper/kokoro);
  - the `host/cammy_host.py` gateway (:8790), which has a duplicate audio stream opener.
- **Duplicates:**
  - `cammy_brain.py` and `cammy_t3.py` both wrap the T3 CLI;
  - `host/companion/` only runs against the simulator;
  - `cammy_conversation.py` is left over from Reachy.
- **No speech gate anywhere:**
  - Every 100 ms of mic audio goes to Gemini, including silence, and zeros are sent while
    Cammy speaks (`send_mic`).
  - About 25 tokens/s is billed whether anyone talks or not (rate from `docs/PLAN.md`).
  - `audio_stream_end` is never sent, and Gemini's default server-side detection is used.
- **Docs that do not match the code:** `docs/PLAN.md` describes Pipecat with Silero VAD, a
  wake gate and echo cancellation. None of that is in the code. The Silero and smart-turn
  ONNX models ship with pipecat in `.venv-live` but nothing uses them.
- **Single points of failure that end a session:**
  - one failed lease renewal (`keep_leases` has no error handling);
  - a mic read that stalls for more than 5 s;
  - the preview dropping (it is never restarted);
  - ffmpeg exiting (`look` stays dead for the rest of the session).

  Any task failing ends the whole program, and no watchdog restarts it.
- **Other findings:**
  - The brain transcript is sent only at exit (last 12,000 characters), so it is lost on a
    crash.
  - The brain polls the T3 CLI every second (~3,600 subprocesses an hour).
  - Several queues and the stdout log have no bound.
  - voicefx pads every 1-2 s chunk with 100 ms of silence on each side and normalizes each
    chunk separately, so long replies have ~200 ms gaps and their loudness jumps.
  - The PC pulls RTSP continuously at 1 fps for `look`, even when `look` is never called.
- **Limits that are fine:**
  - The portal's 600 s mic stream is reopened by the PC (gap under 1 s).
  - Gemini connection rotation uses go_away and session resumption.
  - Context uses a sliding window.

## Target: two control components

1. **Camera: the portal (unchanged).** Lease, mic stream, speaker, face, preview. The camera
   stays simple; all decisions run on the PC.
2. **PC: C3, one resident service** (systemd user unit, `Restart=always`).
   - **Body:**
     - holds the only lease, re-acquires it after loss and retries renewals;
     - supervises the preview and the mic, reopening each with backoff;
     - runs the speaker queue and the face;
     - grabs a `look` frame on demand instead of running ffmpeg all the time;
     - runs captures through the stock :8082 API.
   - **Listener:** the speech gate below.
   - **Voice:** the Gemini Live session manager with its tools.
   - **Brain link:** through `cammy_t3.py` only.
   - **One local API** (127.0.0.1) for T3, MCP and the desktop panel. They become clients;
     none of them opens its own lease.
   - Bounded logs, a rolling transcript file and a health endpoint.

   The other stacks migrate onto this API and are then retired; nothing is deleted before
   its users are moved.

## Echo cancellation and full duplex (Echo, inside C3)

Adam requires these in the plan (2026-11-16). They run inside C3, in front of the gate:
mic → Echo → Listener → consumers.

- **Reference.** C3 knows exactly what PCM it hands to Cammy's speaker. Mic and speaker
  share the camera's codec, so there is no clock drift, only a per-reply offset of roughly
  50-1200 ms (Wi-Fi, portal queue, playback start).
- **Alignment.** Echo finds that offset by cross-correlating (GCC-PHAT) the first second of
  each reply's echo with what was sent, then feeds the reference to the canceller already
  aligned.
- **Canceller.** WebRTC AEC3, from the system `libwebrtc-audio-processing-1` (the one
  PipeWire uses), through a small C shim. The fallback is SpeexDSP with residual echo
  suppression.
- **Full duplex.** Echo reports `echo_active` (Cammy's voice is in the mic) and `converged`
  (delay known, echo reduced by at least 15 dB).
  - While Cammy speaks and Echo is converged, the gate runs on the cleaned mic with stricter
    thresholds. If Adam talks over Cammy, C3 cancels the speaker job and sends
    `activity_start`, so Gemini stops its reply and listens: barge-in.
  - Until Echo is converged, C3 falls back to half duplex for that reply (the mic is ignored
    while Cammy speaks).
- **Calibration.** A one-time check on Cammy with a test clip (announced first) measures the
  real offset range and echo reduction before full duplex is the default.

## The speech gate (Listener)

It sits in one place, between Cammy's mic and every consumer. It works on 32 ms frames
(512 samples at 16 kHz, Silero's size).

1. **Playback awareness.** It reads the Echo state.
   - Half duplex, or Echo not converged: while Cammy speaks (plus a ~0.8 s tail), frames are
     dropped and nothing is sent. This replaces the zeros.
   - Full duplex: the gate stays open on the cleaned mic with stricter thresholds (barge-in).
2. **Noise floor.** A cheap adaptive RMS floor skips the model when the room is quiet.
3. **Silero VAD with hysteresis.**
   - Speech starts when p > 0.5 for about 200 ms.
   - Speech ends after about 700 ms below 0.35.
   - A 400 ms pre-roll ring buffer keeps the first syllable.
4. **Turn check (optional).** smart-turn v3, already installed, runs at each pause and
   decides whether the sentence is finished or Adam is only pausing. This avoids cutting
   him off mid-thought.

Outputs are events: `speech_start`, the speech frames, and `speech_end` with the utterance.

- **Gemini:**
  - Automatic activity detection is off.
  - `activity_start` plus the pre-roll is sent at `speech_start`, then frames, then
    `activity_end` at `speech_end`.
  - Silence sends nothing.
- **Idle:**
  - After a few minutes with no speech, the Gemini connection closes; the resumption handle
    is kept.
  - The next `speech_start` reconnects (about 1 s) while the utterance buffers, then flushes
    it.
  - If the handle has expired, a new session starts with a short summary taken from the
    rolling transcript.
  - Idle hours then cost nothing, and `--minutes` goes away (it stays as an optional cap).
- **Brain:** utterances and replies are appended to the rolling transcript as they happen,
  not only at exit.

Later: a wake word (an option if a TV or other people keep opening the gate).

## Phases

1. **PC only, no camera change.**
   - Listener module with fixture tests on recorded and synthetic audio.
   - Gemini on manual activity; no zeros.
   - No 5-minute default; idle disconnect and reconnect.
   - Lease renewal retries and re-acquire, mic-stall and ffmpeg recovery, on-demand `look`.
   - Bounded logs, rolling transcript, voicefx gap fix.
2. **C3 consolidation.** One service and one local API; T3, MCP and the panel move
   onto it; the duplicate stacks are retired.
Echo cancellation and full duplex are built with phase 1, inside C3. They are switched on
by default once the calibration on Cammy passes; until then C3 runs half duplex.
