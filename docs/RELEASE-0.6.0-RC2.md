# CammyOS0.6.0-rc2: speaker integration candidate

2026-11-15. Successor to the unchanged0.6.0-rc1 artifact. This is a TF-staged,
disarmed userspace/native-face candidate, not a full-flash image or an accepted
hardware release. No automatic reboot or active camera installation is included.

## Added

- Opt-in authenticated `/v1/speaker/play`, `/v1/speaker/status` and
  `/v1/speaker/cancel`; SDK `play_pcm()` accepts mono16k S16_LE bytes.
- One finite50ms..5s job, peak<=4000, with current boot/lease/sequence binding.
  Playback requires enough remaining lease; overlapping or replayed jobs refuse.
- Device guardian checks current native shared playback format, LINEOUT0 mute,
  LINEOUT switch/select, tx-hub and the speaker-switch baseline. It refuses an
  already nonzero LINEOUT level rather than interrupting stock playback.
- The process-private ALSA route joins native dmix IPC1111 without replacing
  global microphone configuration. Gain temporarily becomes20, the level observed
  during audible stock playback, then returns to0. SPK returns to its observed
  OFF/ON baseline, independently of player success or cancellation.
- Stop, release, expiry and portal exit request bounded guardian cancellation.
  An abnormal/uncertain exit latches a RAM fault and blocks further jobs.
  HTTP completion and process status never claim physical audibility/cleanup.

## Evidence and limits

Adam confirmed a patterned PCM cue and local Kokoro “Hello world” from Cammy's
speaker through USB diagnostic trials. The new HTTP job and its on-device
preflight are locally tested; their actual Wi-Fi transport, hardware Stop,
simultaneous microphone/video and acoustic echo remain acceptance tasks. This
candidate adds transport for PC-generated PCM, not text-to-speech on the camera.
The desktop conversation defaults still use P15; switching them to camera output
requires acceptance of this API. No persistent cloud voice session or physical
record-button conversation mapping is added here. OpenAI credentials stay on PC.

## TF staging and next test

The versioned slot contains runtime, native face candidate, exact bootstrap
proposal, rollback files and checksums. Selection defaults to disabled. The
current card's CammyOS runtime and media files are preserved. Copying a versioned
slot does not replace the camera's installed S01/S99 startup files; a reviewed
bootstrap transaction is still needed before a one-shot candidate boot.

First test: install the exact disabled bootstrap with the TF/USB attached, verify
before/after hashes, authorize one candidate boot, then test SDK-generated speech
over home Wi-Fi. Keep the existing reset/fallback gates. Do not enable resident
mode before cold-boot and combined face/media/speaker acceptance. See
[installation review](RC06-INSTALL-REVIEW.md) and
[speaker investigation](SPEAKER-ROUTING-2026-11-15.md).
