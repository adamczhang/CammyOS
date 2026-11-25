# Red record button and speech-understanding check

Evidence: real M105, 2026-11-15. Adam operated the physical record button and
requested analysis of the resulting video. The agent observed input, read USB
metadata and retrieved an existing file; no injected input, grab, reboot,
firmware/code install or recording command was sent.

## Record input identified

All five stock input devices were observed concurrently. Only
`/dev/input/event3` (`gpio-keys`) reported button activity: EV_KEY code28,
value1 down, value2 repeat, value0 up, with SYN records. Two presses were seen.
Their down/up intervals were approximately0.360s and0.190s; this was not a
demonstration of a sustained two-second hold. Three repeat events accompanied
the first press. A prior expired capture produced no events and supplies no
mapping evidence.

Current event3 key capability mask is `100000 0 0 10000000`: on this32-bit
kernel it advertises code28 and KEY_POWER116. Thus grabbing this whole device
would also intercept power. Do not do so without verified essential-key
forwarding. Center play/pause remains event0/code7 and is a different control.

This completes passive identification, not robot-mode reassignment. Native
recording still runs. The next PTT gate is replacing that action while preserving
power and other controls; it is not enough to subscribe to the red button.

## Recorded media and understanding

`L0010009.mp4` was copied from TF to the PC through USB ADB, with matching source
and destination SHA256 (`5fbaffe9…fcbd083`,29,086,618bytes). Its6.966s video is
H.2643840×2160 with AAC16kHz mono audio. A sampled frame shows the current chat's
capture instructions, corroborating the interaction context. Camera wall-clock
timestamps are not used as a synchronized host clock.

Audio was decoded locally to16kHz mono PCM. Two pre-existing local whisper.cpp
models, small.en and large-v3-turbo Q5_0, agreed on the complete two-sentence
English test phrase. This is positive speech-recognition intelligibility
evidence for this short desk recording. It is not a measured word-error rate
against a supplied reference, human listening assessment, live transcription,
or a general quality benchmark. Models and libraries were reused from existing
host installations; no download or external transcription service was used.

Decoded audio is6.848s, RMS−28.46dBFS, peak−8.99dBFS, with zero samples at the
near-clipping threshold32760/32768. The recording is not silent or clipped by
that check. Whole-clip RMS does not measure noise, echo cancellation or SNR;
neither recognizer's agreement proves duplex or speaker cancellation.

The MP4's total-file rate is33.40Mb/s, above the15Mb/s design target. This clip
was4K, unlike the earlier accepted720p/30 Low test. Why the profile differs is
unproven; no settings were changed during this analysis. Reconfirm the intended
profile in the next media configuration test before drawing bandwidth conclusions.

## Preservation and evidence

Boot identity, native process/starttime, watchdog target/fdinfo, pinned stock
hashes, executable mappings and mount identity matched before/after. No body
lease or remote actuator was used. All finite observers completed.

Private evidence directory:
`private/stock-controls/record-button-1794751208618227326/` contains before/after
snapshots, raw traces, parsed `media-buttons.json`, video, retrieval receipt,
ffprobe metadata, decoded WAV, levels, sampled frame and the two local transcripts.
Raw media/transcripts remain ignored and are not committed.
