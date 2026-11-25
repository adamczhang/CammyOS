# Push-to-talk and temporary conversation windows

Decision: Adam, 2026-11-14. In robot mode the rear red record button should
wake the companion for voice interaction. Holding it opens listening; releasing
it leaves a temporary conversation channel open, then Cammy returns to idle.
Authorized agents may also wake it. Idle should not continuously acquire audio.

The2026-11-15 [live A/V prototype](../LIVE-AV-2026-11-15.md) demonstrates shared
microphone input during live video. However, native preview itself acquires the
physical microphone even when RTSP sends no audio. The host policy's sensing-off
state is not proof of electrical mute or closed hardware while preview runs.
Runtime routing/mute ownership must be verified before showing a physical-mute
indicator. Current tests stop preview and confirm capture closed on cleanup.

## Local implementation

`host/companion/engagement.py` wraps the existing `CompanionService`, body adapter,
providers and lease handling. Its caller owns the tick loop and routes controls
through this controller; it is not a new daemon or a second control plane.
Import and construction perform no I/O. `start()` acquires the body lease with
sensing disabled. Typed requests can still work in idle without enabling audio.

| Event | Result |
| --- | --- |
| Fresh press in idle | Request sensing, enter held state if the body supports it |
| Release | Keep sensing enabled for follow-up conversation |
| Recognized/typed turn or outstanding response | Restart the inactivity tail |
| Agent wake | Open the same bounded window, subject to mute and the body lease |
| Quiet window expires | Retire sensing and response jobs, clear queued actions, idle |
| Mute | Disable sensing and pause; agent wake cannot override it |
| Unmute | Return to idle; a held button must first be released |
| Stop, disconnect, lease loss | Invalidate work; require explicit restart after halt |

Proposed configurable defaults are **60 seconds of inactivity**, **30 seconds
maximum continuous hold**, and **10 minutes maximum per conversation window**.
The hard window bound also limits a provider that never completes. Duplicate
presses do not extend a hold, and repeated wakes do not extend the hard window.
The tick loop must run frequently; these are local monotonic deadlines, not
camera-enforced guarantees. Raw audio is not written by this controller.

This implementation gates the service's existing finite-clip capture/transcribe
pipeline. It does **not** assemble one continuous held utterance or implement
release-to-commit streaming recognition. Recognition can complete while held.
Capture currently pauses during thinking/speaking, so this is an interim
conversation policy, not duplex audio or VAD. The SDK body still refuses sensing
where its real audio-capture capability is unavailable; synthetic test data is
never promoted to microphone evidence. Status explicitly reports physical
record-button integration, duplex and hardware cleanup as false.

Run the complete policy scenario without any camera or gateway connection:

```sh
PYTHONPATH=host python3 -m companion --engagement
PYTHONPATH=host python3 -m companion --engagement --profile stock --quiet
python3 -m unittest tests.test_engagement tests.test_companion
```

The second scenario deliberately uses unavailable body capabilities, so it must
remain idle instead of claiming to listen. Both commands use a manual clock,
scripted providers and the in-process simulator. No model or system service is
installed. A resident caller constructs `EngagementController(service)`, calls
`start()`, routes `press()`/`release()`/`agent_wake()`/`mute()`/`unmute()` through it,
regularly calls `tick()`, and finishes with `stop()`.

Verification, 2026-11-14: **48 focused tests pass** (17 engagement and31 existing
companion checks). The full repository suite passed **526 tests in120.007s**;
three additional engagement boundary tests were then added and passed in the
focused48-test run. Evidence is simulator/fake-provider and local fixture only.
The added boundaries cover post-open capture refusal, mute/disconnect with a
late response, and an externally restarted service invalidating the window.

## Physical input gate

The red record button was passively mapped on2026-11-15 to event3/code28,
including down/repeat/up. The same bank advertises power116, so whole-device
grabbing would also intercept power. Center play/pause is event0/code7, a
different key. See [real button and audio evidence](../RECORD-BUTTON-AUDIO-2026-11-15.md).

Offline inspection of the saved rootfs found `etc/MiniGUI.cfg` selecting
`sunxikeytslib`, with `mdev0=event0`, `mdev1=event1`, `mdev3=event2` and
`mdev5=event3`. The saved `etc/sunxi-keyboard.kl` names code115 `TIME` and102
`PLAY`, although actual holds of those codes moved left/right. This proves those
names are insufficient to identify the red key. It does not prove which config
the current app uses or establish a supported runtime remapping API. The saved
configuration SHA256s are respectively `a63145c1…31155600` and `9d7cf2a4…ba3a0d3296`.

Observing a button does not consume it: the stock app may simultaneously start
recording video onto TF. Robot mode must replace that action, not quietly record
video as a voice workaround. Do not use `EVIOCGRAB` on an entire button bank
without proving essential button forwarding, especially power. No input grab,
record-key injection, native input filter, or kernel modification is included
in the local implementation.

Investigate stock input dispatch/configuration first. If no supported remapping
exists, compare a narrow cooperative native filter with an input proxy that
preserves every other control; the latter requires checking uinput support and
how the stock GUI selects its input devices. Either needs a separately reviewed
integration, recovery plan and physical acceptance. Mapping alone is insufficient.

## Next combined acceptance

1. **Passed2026-11-15:** passively identify record down/repeat/up and the shared
   power key. Longer-held behavior remains unmeasured.
2. Demonstrate robot-mode suppression of stock recording while other buttons
   still work, and normal recording after leaving robot mode.
3. Start idle with no audio acquisition or forwarding. Hold/release the record
   button for an intelligible initial turn and several follow-ups, then verify
   inactivity closes capture. Distinguish requested listening from actual capture.
4. Verify mute, Stop, stuck hold, Wi-Fi loss, lease takeover and late provider
   results cannot reopen the microphone or dispatch queued responses.
5. Measure whether native playback permits camera/microphone capture at the same
   time. Existing audible file playback is not arbitrary speech or duplex proof.

VAD can later provide turn boundaries **inside the active window**; idle need
not listen to support it. [Silero VAD](https://github.com/snakers4/silero-vad)
is one existing implementation to evaluate, but real microphone
routing, speaker output and echo handling remain separate hardware work. Keep
the [Wi-Fi control milestone](NEXT-CONTROL-JUMP.md) focused on usable motion,
media context, listening state and Stop in one console. No TF runtime update is
needed for this host-only policy, and no camera release is claimed here.
