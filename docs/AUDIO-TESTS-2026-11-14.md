# Native microphone and USB duplex tests — 2026-11-14

**The M105 microphone captured a known acoustic cue through native AAC recording.**
USB capture and playback could run concurrently, but did not deliver physical
microphone/speaker signals. The evidence points to an unconnected gadget/codec
route; it does not establish a need for a new driver.

Later stock-control baseline: Adam played existing clips through Cammy's own
media viewer and heard the recorded beep cues from its speaker. Onboard native
file playback therefore works. This does not establish USB speaker routing,
remote CammyOS speech or conversational duplex. See
[operator-observed baseline](STOCK-CONTROL-BASELINE-2026-11-14.md).

The later leased USB button trial also remotely opened the viewer and pressed
OK/center; Adam confirmed that video1/9 played and its beeps sounded. This is
verified remote speaker output via native file playback, not a new PCM/TTS or
USB-audio-gadget stream. The normal app already supplies the functioning route.

## Accepted working profile

Adam accepted720p/30 Low as the working video preset. It was applied through
the native menu API and read back as resolution5,quality2. It survived the
operator's subsequent mode changes/reboots and remains selected. The previous
4K/30 High values0/0 are retained privately for restoration if requested.
The15Mb/s combined media target remains unchanged; no hard traffic limiter or
continuous media service was installed in these tests.

## Real microphone evidence

One bounded P15 cue contains700,1100 and1700Hz tones in that order. Each lasts
0.24s with silence between tones. The P15 output uses an explicit physical sink
at stream volume0.85; global volume/mute settings were not changed. A separate
P15 microphone reference is labelled separately from the camera recording.

The accepted native test used known recording start/stop commands and returned
AAC from the M105. Both PipeWire playback/reference routes were verified via
process→client→node→physical endpoint links. Adam also confirmed hearing the P15.

| Tone | Peak time in decoded camera audio | Fraction of window energy at that tone |
| --- | ---: | ---: |
|700Hz |0.66s |0.99083 |
|1100Hz |1.04s |0.96889 |
|1700Hz |1.48s |0.86260 |

The decoded capture has2.624s of mono16kHz PCM, peak2247/32768 and RMS445.047.
Detection requires all three tones in order, frequency fraction≥0.2 and tone RMS
at least twice the initial noise baseline. Local tests reject silence, noise,
one carrier and a reversed sequence. These results establish an acoustic input
path, not word intelligibility, speech recognition or end-to-end voice latency.

During this capture, the existing CammyOS `audio-state` probe reported physical
card0 capture RUNNING, mono16kHz S16_LE. Read-only USB metadata showed playback
also RUNNING/configured; its `appl_ptr` was0, so that status is not speaker-output
proof. No PCM device was opened directly on Cammy and no mixer was changed.

The initial harness incorrectly treated PipeWire Client IDs as Node IDs and
therefore rejected a genuine cue despite positive camera analysis. The graph
verifier was corrected and a second native test passed independently. The P15
reference was nearly silent during its own speaker cue; no suppression mechanism
is inferred from that observation alone.

## Standard-driver USB duplex evidence

Following Adam's webcam-mode lead, he selected PCCAM Mode. The host matched
VID:PID18d1:100d,serial20080411 and the exact USB ancestry of ALSA card6 (dynamic,
not hardcoded). Descriptors advertise mono16kHz S16_LE in both directions.

`host/usb_duplex_probe.py --run` holds a CammyOS body lease, validates the exact
device throughout and opens ordinary host `arecord`/`aplay` streams. It first
sends one finite cue to Cammy's USB speaker, then a later cue to the P15. It
captures five seconds from M105 USB plus a separate P15 reference. It stops owned
processes on expiry, Stop, disconnect or error and never chooses a default device.

Two five-second runs completed both driver streams. Each USB microphone file
contains80,000 exact-zero samples. No camera-speaker signal was observed in the
external reference; Adam reported hearing only the later P15 cue. A previously
reported faint chime after a native test remains source-unconfirmed and is not
counted as a speaker success.

The second run collected metadata while both host USB directions were RUNNING:

- The host showed both USB interfaces active at16kHz.
- Cammy exposed a third sound card, `UAC1Gadget` (card2), alongside its physical
  `audiocodec` (card0) and `snddaudio0` (card1).
- Physical card0 microphone `hw_params` and `status` remained **closed** during
  the active USB capture. In the successful native acoustic test, that same
  physical capture path was RUNNING.

This is positive evidence that USB duplex transport exists, with no observed
bridge feeding it from the proven microphone path. Speaker playback routing is
also unproven. Exact zeros must not be interpreted as successful echo cancellation.

## Existing echo-cancellation components

The saved exact stock executable exports `WebRtcAecProcess` at0x25BDCC. Its
ALSA config defines `Capture1MicPlusAec`, `Capture2MicPlusAec` and an I2SRX echo
reference route. These are static component-presence findings only. No direct
ARM BL/PLT caller was established by the narrow scan; indirect or other call
forms were not ruled out. Neither active AEC nor noise suppression was proved.
The saved USB audio descriptor graph has terminal/streaming descriptors but no
advertised feature/processing unit establishing a host AEC control.

## Restoration and reproducibility

Adam returned Cammy to Charging Mode and confirmed normal live view. The final
read-only check verified original stock init/app/library hashes, native/watchdog
identity, normal video mode0,recording0,owner null and preview off. The accepted
720p/30 Low preset remains active. The operator's mode changes included new boot
IDs; seamless hot switching is not claimed. The resident gateway reconnected.

After USB re-enumeration, the existing PC ADB server5037 held Cammy; dedicated
server5038 had no device. No server was restarted. Preservation tools now accept
an explicit `--adb-port 5037` option (default5038 unchanged).

No firmware/code was deployed to Cammy, no native app was signalled, and no raw
USB override, driver detach, camera PCM write, mixer edit or always-on recorder
was used. Only finite native recordings/USB streams and read-only probes ran.
All owned PC playback/capture processes stopped and leases were released.

Raw media and exact receipts remain ignored under `private/vision-milestone/`
(`20261115T015903Z-clip`, `20261115T020641Z-clip`) and
`private/audio-milestone/` (`20261114T201658-usb`, `20261114T202213-usb`,
`working-profile.json`, `final-state.json`, `checkpoint.json`).
The full local suite passed455tests;44 focused audio/USB/vision tests passed. These
fixture results are separate from the live observations above.

## Next outcome

Build a reviewed CammyOS audio bridge that reuses existing native input/output
ownership and exposes bounded speaker/microphone jobs. First add constrained
read-only gadget-card routing/ownership metadata, then identify the supported
native dispatch/stop path. Do not blindly open Cammy's occupied PCM devices or
start `alsaloop`. Acceptance is a clearly audible Cammy phrase while its microphone
returns a known near-end cue, followed by latency, echo/double-talk and combined
15Mb/s tests. USB is a diagnostic route; Wi-Fi operation remains the end goal.
