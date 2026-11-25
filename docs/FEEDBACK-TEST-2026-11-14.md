# Monitor/P15 feedback test — 2026-11-14

Adam placed the M105 facing the monitor and authorized P15 speakers/microphone and
bounded generated speech for testing. He requested screen sleep disabled for now.
These permissions apply to this test setup; the standing camera firmware/VRAM and
custom-hook constraints remain unchanged.

## What was tested

- A high-contrast monitor target with code A7F3/B9C2, orientation markers and colour
  blocks was generated locally and displayed in chat. Adam confirmed it was visible
  in the M105's own LCD live view in Charging Mode. This is **operator-confirmed
  optical/display evidence**, not an image retrieved through CammyOS.
- P15 output/input were selected by exact PipeWire node names; active graph links
  confirmed the playback targeted the P15 sink and recording used its real input
  source, rather than the sink monitor or another default microphone.
- The first twice-attenuated cue was not audible to Adam. He separately verified
  speakers with YouTube. A second local Flite voice at stream volume0.6 was audible
  but quiet; the next short cue used0.85 without changing global device volume.
- The first two P15 captures were nearly silent. A later native48kHz stereo capture
  around the spoken/clap cue contained peak6162/RMS387.95 on both channels over
  approximately14seconds. Adam reported that he talked when prompted. These are
  physical input-signal observations, **not verified word transcription**.

All recordings, generated speech, tone cues, target images and detailed results are
private under `private/feedback-test/`. Tests were bounded; playback/capture processes
ended, and no looping or always-listening voice service was installed or left running.
The earlier M105 USB-UAC capture remains all-zero; P15 results do not supersede it.

## Important naming and behavior limits

The generated phrase said “Cammy speaker check,” which was ambiguous: **the P15
played that cue, not the M105**. The camera's speech adapter is still unavailable.
No microphone-to-transcription-to-model-to-spoken-reply pipeline was connected, so
the test did not automatically answer Adam's spoken response. Future cues should
name their actual device explicitly, such as “P15 reference test.”

Current conclusions: user-confirmed M105 optical/LCD target visibility; audible P15
playback; non-silent P15 microphone input. M105-to-PC live imagery, M105 microphone
samples/intelligibility, M105 speaker output and autonomous dialogue remain open.

## Display sleep and restoration

GNOME desktop idle-delay is now0 and automatic AC sleep type is nothing. Original
values were saved before changes in `private/feedback-test/sleep-settings-before.json`.
Screen-lock configuration and global audio volume/mute were not changed. These
idle settings remain disabled until restoration is requested; no background audio
loop is needed to keep the display awake.

To restore the saved values later:

```sh
python3 tools/restore_feedback_settings.py
```

## Next useful closed-loop checks

After a real CammyOS video hook returns fresh frames, change the monitor target
and verify its actual code/orientation from those frames with source/freshness
metadata. Knowing the expected target or seeing it in a user-uploaded setup photo
does not establish camera-stream access.

After a working M105 capture hook exists, begin capture before a known PC-speaker
cue and detect its timing/frequency sequence in the camera's returned samples.
Use P15 recordings as separately labelled reference data, and account for possible
echo suppression and device clock/latency differences. A temporary PC conversation
prototype may use P15 audio with explicit labelling, but cannot count as completed
camera audio. Live speech recognition and automatic replies require separate
implementation and validation.
