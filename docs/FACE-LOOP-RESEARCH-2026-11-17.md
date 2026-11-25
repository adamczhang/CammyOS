# Desktop robot face and expression loops · 2026-11-17

The strongest fit for Cammy is a local face controller that combines conversation
state, subtle idle animation and short reactions. Real playback should drive
the speaking loop. Each face pack can express those signals with its own poses.

## Comparable implementations

| Project | What it does | Implication for Cammy |
|---|---|---|
| StackChan Realtime | A separate render task draws spring-driven expressions, blinking and small gaze shifts. Mouth opening follows speaker amplitude. The model can request one of five expressions. [Firmware and design notes](https://github.com/huydx/stackchan-firmware) | Speech energy can drive a convincing mouth on a small display. The face keeps its own clock and a small vocabulary. |
| Reachy Mini | Local voice activity detection quiets idle movement while the user speaks. Explicit reactions use recorded clips. Its maintainers distinguish the end of model audio generation from the end of speaker playback. [Conversation releases](https://github.com/pollen-robotics/reachy_mini_conversation_app/releases), [emotion tools](https://github.com/huggingface/blog/blob/main/adding-mcp-tools-to-reachy-mini.md#built-in-tools), [playback timing discussion](https://github.com/pollen-robotics/reachy_mini_conversation_app/issues/526) | The listener can acknowledge a person quickly, even during a slow model turn. Start and stop speaking animation with the speaker. |
| Furhat | A face combines ongoing blinks and small eye/mouth movement with event-driven gestures. Gestures have duration and intensity. Phoneme timing is available for richer lip sync. [Gestures](https://docs.furhat.io/skill-development/reference/gestures), [speech and lip sync](https://docs.furhat.io/skill-development/reference/speech) | Idle animation, a deliberate reaction and mouth movement can run as independent parts of one face. A first version can work from speech energy and leave room for later visemes. |

## Baseline before the prototype (0a1613f)

- CCC sends a face command when the speaker becomes active, voice is idle, a look is
  in progress, or the view should be hidden. The face task checks every 300 ms.
  Its idle and neutral branches have no separate state for the local listener's
  speech gate or for waiting on a reply.
  [CCC face and speaker code](../host/c3/body.py), [listener](../host/c3/listener.py)
- The camera plays a two-frame speaking loop at one frame per 180 ms while the
  speaking command is active. Both face packs have their own speaking art. The mouth
  timing comes from the loop clock. The idle command clears the native sleep flag and
  cycles the mood bank every four seconds. This can show a mood with no corresponding
  conversational event. Native sleep is entered at boot or when the face lease ends.
  [Native face engine](../native/face_service/face_service.c)

## Speaker timing audit

CCC sends final filtered 16 kHz PCM to the camera. HTTP 202 confirms queue
acceptance. The guardian feeder copies chunks through a pipe and asks the
kernel for a 4 KiB pipe capacity, equivalent to 128 ms of PCM. It currently
ignores failure of that request. The ALSA configuration requests a 960-frame
period and a 7,680-frame buffer, equivalent to 60 ms and 480 ms. Device queue
depth, startup delay and audible drain still need measurement.
[Portal speaker path](../src/cammy_speaker.h),
[guardian feeder](../native/speaker_probe/guardian.c)

An offline guardian test reproduced a late append that was accepted by the
file handoff after the feeder closed and before the guardian exited. That
chunk never reached the player. The speaker path needs an explicit handoff
between the portal's append acceptance and the feeder's closing state.
Verification must cover appends just before, during and after the close,
as well as cancellation.

A device-side monotonic clock and a few smoothed mouth levels can reuse the
existing two mouth frames for both packs. Prefer a feeder or ALSA progress
signal as the timeline anchor. A model based on queue depth and a calibrated
startup delay remains an estimate, especially on short replies, underflow,
and cancel. Check playout timing against a recording before changing the
face IPC layout or treating that estimate as lip sync.

## Prototype direction

The first offline implementation uses the existing pose commands for listening,
waiting and sleepy. The native renderer adds variable blink timing and an
animated sleepy pose, and holds a settled neutral expression during idle. These
changes keep the speaking art and two-frame cadence. [Current status](STATUS.md)

1. **Use the interaction state to choose a base pose.** Idle should look settled, with
   occasional blinking and small glances. Use the local speech gate for listening.
   Show an attentive waiting pose when the user finishes and the reply is pending.
   Keep asleep visibly distinct. All four states should work in either face pack.
2. **Drive the mouth from playout.** Derive two or three smoothed mouth levels from
   the final PCM, using the device playout position where available. Keep the empty
   speech bubble low and clear of the face. End the visual cue when sound drains or
   playback is cancelled. First check the timing against an actual speaker recording.
3. **Add brief, intentional reactions.** Map a few intents, such as delight, curiosity
   and acknowledgement, to original per-pack transitions with a finite duration.
   Let the local renderer blend them back into the current base pose. Keep the
   gimbal out of the first experiment so the LCD cues can be judged in isolation.

Useful acceptance cases include a neutral quiet minute, a short question and answer,
a multi-part reply with a pause, interruption, a late network block, switching face
packs and opening Settings during speech. Watch for a mouth still moving after audio
ends, missed listener cues, and repeat patterns that look mechanical.
