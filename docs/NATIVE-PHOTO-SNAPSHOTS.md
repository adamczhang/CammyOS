# Experimental native-photo snapshots

Status: **real-camera capture and authenticated SDK delivery verified**.
This finite still-image path is separate from the unavailable RTSP stream.
The resident desktop service has explicit snapshots enabled in private config;
source defaults remain disabled. No automatic sensing loop is running.

## Evidence — 2026-11-14

The saved phone app's QZ backend (`p069n3/I.java`, `p063m3/d0.java`,
`V/e.java`) selects work mode with3034 and invokes the main shutter with1100.
Its transport is an HTTP POST with an empty body to
`/api/setdeviceinfo/?custom=1&cmd=…&par=…`. Its work-mode mapping is0 for
normal video and4 for normal photo. Exact stock executable analysis maps
these commands to its own task queue. These are static findings, not proof
of a successful capture or serialization against physical buttons.

Read-only live HTTP queries returned `current_mode:0`,
`curworkmodename:NormalRecordeMode` and `RecodStatus:0`.
GET `/mnt/extsd/DCIM/100MEDIA/` returns an HTML directory listing, empty when
first checked. HEAD returns404 even though GET works. Newly completed JPEGs
now provide the verified frame source. Raw evidence is
ignored under `private/vision-milestone/`.

Read-only preflight passed with stock boot/PID/watchdog, hashes and executable
file mappings preserved. Vendor `/proc/maps` inserts allocation/thread
annotations before pathname; those annotations change during ordinary HTTP
activity. The checker compares executable file mappings without the annotations,
and retains raw before/after maps privately. Device/DMA/IPC image buffers are
excluded from executable-file comparison. It does not assert identical heaps
or prove native media-resource cleanup.

Live command replies are `{"Cmd":3034,"Value":0}` for both mode parameters:
Value is a result code, not the parameter. Replies precede completion by about
1–2seconds in the calibration samples. An initial parser assumption failed,
and a premature restoration claim was found and corrected before accepting a
transaction. No shutter was sent in those first mode-only attempts.

The first actual photo decoded as5120×3840, with a16-byte vendor footer after
JPEG EOI. Its final little-endian word equals the JPEG length excluding the
footer. Only this length-bound framing is removed; encoded image bytes are not
re-encoded. Raw-file and delivered-JPEG hashes are reported separately.
The first footer-unaware transaction faulted rather than publishing an image;
the completed file was inspected and normal video mode restored explicitly.

## Desktop implementation

`host/cammy_vision.py` is optional and disabled by default. Stock remains the
media owner. No vendor replacement, loader, raw device, driver, firmware write
or restart is part of this path. A capture saves one ordinary photo on TF.

1. Require a matching authenticated lease with at least25seconds remaining,
   a real connected camera, idle preview and no competing control transaction.
2. Obtain boot identity through the existing CammyOS runtime probe. Check
   idle native mode and record the current photo filenames.
3. Select photo mode if necessary, confirm it, and send the shutter **once**.
   An uncertain action is never retried.
4. Accept exactly one new filename, normalize the evidenced footer, retrieve a bounded JPEG and decode it
   with Pillow. Existing files and incomplete JPEGs cannot satisfy a request.
5. Recheck lease history and boot identity. Restore and confirm the original
   mode before publishing an image.
6. Serve only to that controller with a valid lease, unchanged boot and image
   age at most10seconds. Connection changes and cancellation invalidate delivery.

Metadata carries boot ID, receipt time, dimensions, SHA256, synthetic label,
restoration result and `hardware_cleanup_confirmed:false`. Receipt time bounds
delivery age; it is not a sensor exposure timestamp. New-file completion is
capture evidence, not native cleanup proof. No other physical capability changes.

The native API lacks an atomic mode/capture operation and independent lease.
**Leave camera buttons and the phone app untouched during capture.** Only trusted
local clients should use this experimental backend. Unexpected owner/mode/boot
changes or uncertain capture/cleanup latch a fault requiring inspection. The
gateway never deletes camera files. Other clients using the body API directly
cannot be serialized by the desktop process; lease-history checks reject them
conservatively.

During capture, other mutating body requests receive409. Stop/owner release
cancel delivery and return409 saying cleanup is pending; retry when settled.
An in-progress shutter cannot be recalled. If capture completion is uncertain,
the worker leaves mode untouched and faults instead of retrying or claiming cleanup.

## API and SDK

These desktop-only routes require the gateway bearer token and `X-Agent-ID`.
Agent names coordinate trusted token holders, rather than authenticate them.

| Route | Result |
| --- | --- |
| `GET /v1/vision/capabilities` | Backend status and limits |
| `POST /v1/vision/capture` with `{}` |202 and one finite capture job |
| `GET /v1/vision/frame-N` | Progress and metadata |
| `GET /v1/vision/frame-N/image` | Fresh JPEG, current matching lease required |

SDK additions: `vision_capabilities`, `frame_start`, `frame_status`,
`frame_image`, `capture_frame`. The SDK remains standard-library-only;
the enabled host backend needs Pillow. The desktop page provides explicit
capture, synthetic labeling and image expiry. Refresh never takes a photo.

`--native-photo` or private config `"native_photo":true` enables the experiment.
Default startup makes no native photo actions. The resident service was restarted
with explicit capture enabled after hardware verification. The gateway remains usable without Pillow while
this optional feature is disabled.

## Prepared one-photo hardware test

The test has passed. One ephemeral-gateway capture and three consecutive resident
gateway captures delivered real5120×3840 JPEGs. The resident transactions took
6.050,7.340,8.519seconds, each with original mode restored, one new photo,
same boot/native/watchdog/hash identity and lease release. These dimensions are
decoded file dimensions, not a claim about optical sensor resolution.
See [hardware results](LIVE-VISION-2026-11-14.md).

`python3 -m tools.vision_trial` performs read-only readiness/preservation
checks. It writes a private PC report, without lease acquisition, mode change
or shutter. After approval of the following scope, run
`python3 -m tools.vision_trial --capture-one`:

1. Require exact stock init/app hashes, idle normal video mode0, native/watchdog
   identity, connected home gateway and no owner or preview.
2. Start an ephemeral loopback desktop gateway with the existing private token.
   Acquire60seconds as `vision-trial` through the SDK.
3. Select0→4, send one shutter, retrieve one new completed JPEG, restore4→0.
   Keep the photo on TF and save its SDK result privately on the PC.
4. Release the lease, stop the temporary desktop server and verify native mode,
   stock hashes, executable maps, boot/PID/watchdog and idle gateway.

For a bounded three-photo sequence through the resident service, add
`--count 3 --resident-gateway`. Neither option permits an unbounded loop.

No code/firmware deployment, app kill/restart, reboot, radio change, motor command
or raw-device access. A failed/ambiguous action ends automatic progress. Do not
resend the shutter; report unresolved restoration for inspection.

## Local verification

`tests/test_vision.py` uses fake native state and loopback HTTP/SDK fixtures,
covering fresh delivery, decoding, mode restoration, lease/boot changes,
cancellation, stale/ambiguous files, uncertain actions, strict path parsing,
exact POST parameters, redirect/bounds refusal and map comparison.
`tests/desktop_state.cjs` checks explicit capture, synthetic labeling and clearing
images on owner loss. These tests provide no real camera-frame evidence.
