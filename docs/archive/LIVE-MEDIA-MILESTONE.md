# Current milestone: sustained live media to the PC

Adam approved this priority correction on2026-11-15. It supersedes the sequencing
in NEXT-CONTROL-JUMP.md: the Wi-Fi control console remains useful supporting
work, but live video and session microphone audio are now the delivery priority.

## Product goal

Cammy is a live robot terminal: sensor video reaches the PC continuously, enabled
microphone audio reaches speech recognition with low latency, and incoming agent
speech reaches its speaker. The PC runs vision, recognition, agent and synthesis.
The gimbal and screen supply embodiment. Home Wi-Fi with USB power is the final
transport; a working USB media connection is an acceptable development stage.

Push-to-talk opens a finite live conversation window, with optional agent wake;
idle agent microphone forwarding remains disabled. Native preview currently
acquires the physical microphone independently; physical idle mute with video
is still an unmet requirement. It must not require recording a
TF video, finishing it, downloading it and then recognizing speech. Native clips
and photographs are diagnostics and optional tools, not a substitute for this
live connection. The agent may sample frames from a continuous stream without
processing every frame with a model.

## Delivery and acceptance

1. **Live receive:** capture video and enabled microphone audio on the PC as
   they are produced. Demonstrate fresh changing frames and intelligible speech
   before stopping the source. No TF clip or native record action as the transport.
2. **Measurement:** report actual resolution/frame cadence, encoded bitrate,
   missing/stalled data, transport and recognition delay. A screen/cue observed
   at both ends is needed for end-to-end latency; packet arrival alone is not it.
3. **Sustained operation:** run30minutes with bounded buffers, Stop, lease loss
   and reconnect behavior. Silence or a frozen image must be reported as failure.
4. **Conversation:** add arbitrary streamed speaker audio while microphone and
   camera remain usable; prove overlap/interruption and then tune echo handling.
5. **Final transport:** reproduce the useful session on home Wi-Fi with PC USB
   data removed. Aim for720p and a combined15Mb/s video/two-audio/face budget;
   use measured encoded rates rather than nominal resolution as proof.

An initial ten-second decoded live sample is a useful entry proof; it does not
close sustained operation or two-way conversation. Face polish, more button
mapping, framework integrations and internal OS replacement do not gate it.

## Route selection from current evidence

- **Native network preview:** the phone app expects RTSP8554/ch00 plus the9999
  event connection/4016 heartbeat. Managed home Wi-Fi had no RTSP listener.
  Verify the normal stock Wi-Fi lifecycle before changing native code. A standard
  stock-AP session is a legitimate diagnostic transport, not final home-LAN proof.
- **USB webcam:** standard descriptors advertise MJPEG720p30, but earlier UVC
  control negotiation failed and USB audio returned exact zeros. Descriptor
  advertisement is not working live media. Revisit only with a targeted cause
  and controlled test, not repeated silent captures.
- **Native integration:** if normal stock routes fail, identify the exact missing
  application lifecycle/codec bridge and prepare a reversible runtime integration.
  Drivers already operate the stock sensor/display/recording pipeline. An unowned
  parallel MPP process or guessed native event is not an established solution.

Preserve stock app/init and boot while using supported controls. Stop on an
unexpected boot, loss of native/watchdog identity, or ambiguous command result.
Any persistent install, candidate launch or reboot remains a distinct reviewed
action. Record what each test proves and the actual next dependency; do not let
fixture counts or another recorded clip stand in for a live-media pass.

## Current execution checkpoint —2026-11-15

**First concurrent live-media proof passed.** Normal stock Wi-Fi initialization,
followed by renewing the existing home DHCP lease, started native RTSP on home
Wi-Fi. A35.079s video window overlapped20s of live microphone PCM and recognition
of Adam's speech. Video uses Wi-Fi; microphone still uses USB and an experimental
shared ALSA configuration. The native RTSP audio track sends no audio packets.
All temporary configuration mounts were restored, staging removed, microphone
closed and stock identities preserved. See [evidence and limits](../LIVE-AV-2026-11-15.md).

`host/live_media_probe.py` is the experimental host receiver. It authenticates
to the selected Cammy body, refuses another owner, opens the known native preview
event path only if RTSP listens, reads bounded video/audio SDP, and consumes
decoded frames/PCM while the source is still running. It renews its short lease,
checks ownership/preview/identity, terminates and reaps its decoder on failure,
and releases only its own session. No shutter, recorder, mode setter, default
webcam/microphone fallback or on-camera executable is used.

Run only when the selected stock route is ready:

```sh
python3 host/live_media_probe.py --host <camera-ip> --seconds 10
```

For the stock AP the host is192.168.10.1. The CLI reads the existing private host
token file; it does not print credentials or store raw audio/video. Gray160×90
frames sampled at5fps are bounded **monitoring output**, not a camera profile
change, source frame-rate measurement or network bandwidth reduction. Nonzero
PCM and changing frame hashes are delivery evidence, not recognized speech,
fresh-scene cue acceptance, acoustic quality or end-to-end latency.
Exit2 means partial delivery without both video and nonzero audio (the current
native RTSP route); exit1 is a transport/control error. Exit0 is delivery evidence,
not speech-quality or physical-cleanup acceptance.

Eight local tests pass, including real-time synthetic video/audio pipes, silent
audio refusal, video-only honesty, decoder cancellation/reaping, lease takeover,
absent listener without side effects, fixed-source restrictions and bounded SDP.
Together with existing USB and companion tests,45 checks pass in8.844s. These
are local fixture results. The complete repository suite passes537 tests in
125.025s. Next: a bounded shared-capture runtime profile and authenticated Wi-Fi
PCM endpoint, short-window recognition, then latency/cue and sustained-session
acceptance above. No new deployable TF release is claimed by the entry proof.

## TF transport candidate,2026-11-15

[CammyOS0.4.0](RELEASE-0.4.0.md) packages owner-scoped Wi-Fi PCM and the RAM-only
shared-capture profile. This removes the planned USB microphone dependency in
code; real-camera Wi-Fi transport, sustained video/audio and measured latency
remain pending. A TF update is being staged with0.3.0 rollback retained.

## First all-Wi-Fi transport proof,2026-11-15

The [25s/20s concurrent trial](../WIFI-AV-2026-11-15.md) received changing video and
nonzero microphone PCM over home Wi-Fi, without USB media or TF recording.
Client close and lease release ended the owned session. This closes the initial
Wi-Fi-transport question, not intelligibility/latency/sustained/duplex acceptance.
The subsequent35s video/20s PCM human trial passed through the desktop gateway:
an8s prefix was recognized while video continued, and the full sample was
intelligible. Stop/expiry and native capture-closed checks passed. Complete
rolling transcription, sustained/latency, physical PTT and speaker duplex remain open.
