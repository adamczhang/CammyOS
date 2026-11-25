# Real camera frames and finite video — 2026-11-14

The desktop can now deliver a new camera JPEG through the authenticated SDK.
Finite native H.264/AAC recording also works. Continuous streaming, independent
microphone/speaker control and face rendering are still open.

## Hardware results

| Test | Verified result |
| --- | --- |
| First recovered photo | Native shutter created ANMP0000.jpg;5120×3840 decoded on PC |
| Ephemeral gateway + SDK | New ANMP0001.jpg delivered after mode restoration; real/synthetic=false |
| Three resident gateway + SDK captures | ANMP0002–0004.jpg;6.050,7.340,8.519s per transaction; each restored mode0 |
| Native three-second recording request | ANMR0005.mp4;12,160,375bytes;82 decoded H.264 frames,3840×2160, nominal30fps;2.699s video |
| Temporary720p/30 Low recording | ANMR0006.mp4;2,905,545bytes;84 decoded frames,1280×720, nominal30fps;2.766s video |
| Audio in both clips | AAC,16kHz,mono; all packets decoded; simultaneous speaker playback was not tested |

These are file dimensions and reported frame rates, not proof of optical
sensor resolution or independent sensor exposures at that rate.
The high-resolution clip's decoded PCM had41,984samples (2.624s), peak337/32768,
RMS106.762/32768 (−49.74dBFS). It is quiet, nonzero audio. No known speech/cue
or echo-cancellation test was performed, so intelligibility remains unproven.

Across accepted transactions, the original stock init/app hashes, boot ID,
native PID/start time and watchdog owner were preserved. The camera returned
to normal video mode0, recording0; leases were released and preview stayed off.
Resolution/quality were read back and restored after the temporary720p test.
Read-only inspection found1MiB reserved MP4 header space, mostly `free` padding;
an HTTP byte-range read returned206 with exactly the requested8bytes. This is
a transport-optimization lead, not a streaming implementation.
No firmware/code was installed on Cammy, no app was killed/restarted, and no
reboot, radio change, raw media device or framebuffer access was used.
Five ordinary photos and two clips remain on TF; no user files were deleted.

## What made the path work

Use the existing native application as media owner. The phone app's known
HTTP controls select mode3034 and trigger shutter/start/stop1100. Native files
are available through the existing HTTP photo directory. The desktop supplies
authentication, lease/history/boot checks, file validation and delivery.

Two protocol assumptions were corrected from live evidence:

- Replies identify the command with `Cmd` and carry result code `Value:0`.
  Value is not the parameter. Mode changes finish after the HTTP reply. Two
  mode-only attempts failed before any shutter, and were explicitly restored.
  A premature restoration claim was discovered and fixed; an uncertain mode
  dispatch now faults rather than treating an old mode sample as completion.
- JPEG files append a16byte native footer. Its last little-endian word is the
  JPEG length excluding that footer. The first shutter transaction faulted
  under the old strict-EOI check; that completed file was inspected and mode0
  explicitly restored. The backend now strips only that length-bound footer,
  decodes the JPEG and reports both raw-file and delivered-image hashes.

Vendor `/proc/maps` adds mutable thread-accounting annotations and executable
permissions to DMA/device/shared-image buffers. Preservation checks compare
actual executable-file mappings and retain full raw maps privately; they do
not pretend image-buffer allocation is fixed or prove resource cleanup.

## Host software and limits

The resident `cammy-host` service was restarted with `native_photo:true` in
private config after the first accepted SDK transaction. Its default source
configuration remains disabled. Explicit authenticated capture is available
at `/v1/vision/capture`; there is no automatic sensing loop. The desktop page
has an explicit photo button and10second image expiry. SDK `capture_frame()`
returns metadata and JPEG bytes. See [contract](NATIVE-PHOTO-SNAPSHOTS.md).

`tools/vision_trial.py` defaults to read-only preflight. Its explicit capture
mode permits at most three photos and can test the resident gateway.
`tools/native_clip_trial.py --record-three-seconds` is a one-off operator
experiment, not a resident clip API. Add `--720p-low` for temporary native
resolution/quality selection with restoration. The tool requests its own stop
in `finally`; this does not survive a host crash and does not establish an
independent camera-side stop guarantee. Never expose it as autonomous recording
until that ownership/lifetime problem is solved.

Both native video tests stopped and returned idle. Physical cleanup remains
explicitly unconfirmed; no in-process native bridge or candidate-boot gate closes.

## Evidence and verification

Ignored private evidence is under `private/vision-milestone/`:
`20261115T011320Z-capture`, `20261115T011507Z-capture`,
`20261115T011803Z-clip`, `20261115T012753Z-clip`,
`mode-protocol-calibration.json`, `native-jpeg-footer.json`, and the compact
`checkpoint.json`. Sensor media, tokens, raw vendor files and private host
config remain outside git.

The final full suite passed446tests in119.597s. The focused suite passed38tests,
including refusal before dispatch when the optional JPEG decoder is missing.
Snapshot, gateway/SDK, HTTP transport,
browser-state and finite-video fixtures are local/fake evidence, separate from
the hardware results above. Existing HTTPError ResourceWarnings remain; no
tests failed in the final run. One earlier broad run exposed a pre-existing
bridge fixture race when a BUSY rejection closed before the client sent; the
fixture now verifies that exact BUSY response and retains all connection counters.
Native bridge code was unchanged. See STATUS for the current handoff.

Bandwidth findings and the user's combined15Mb/s target are in
[MEDIA-BUDGET.md](MEDIA-BUDGET.md).
