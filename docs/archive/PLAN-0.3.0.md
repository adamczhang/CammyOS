# CammyOS 0.3.0 plan: internals mapping and preview readiness

Date:2026-11-14. Status: **probe/readiness/desktop increments locally tested; TF-updated and camera boot/probes verified; media unavailable**. The camera still
runs TF runtime0.3.0 after the verified update. This plan follows [current architecture](../../architecture.md)
and [targeted findings](../TARGETED-PROBES-2026-11-14.md).

## Release outcome

Give the agent enough structured evidence to distinguish an unavailable native
media service, a closed audio route, and an ownership conflict without repeatedly
using a USB shell. Fix the meaning of preview success. Preserve normal home Wi-Fi,
stock kernel/app, existing authentication/leases and the TF update/rollback path.

A working home-network frame is the next feature target. It is not promised merely
by adding probes: the safe native initialization interface is still unresolved.
Useful mapping/readiness work can ship independently of that unresolved bridge.
Gimbal motion, a real face and full conversation remain separate milestones.

## Known problem and hypothesis

The v0.2 preview hook opens native event9999 and sends4016, then returns success.
That establishes an event session, not a listening RTSP server or a decodable
frame. Stock code creates RTSP/stream senders in a MSG_SOFTAP_ENABLE presenter
branch, while CammyOS starts the radio in managed mode independently of that UI
lifecycle. This suggests missing native media initialization; it remains a
hypothesis. Charging Mode restores native PCM playback but does not fix RTSP.

The UART motor protocol and a separate I2C motor path are partly understood, but
physical function mapping and stop semantics are not. MIC1 was off, and a native
PCM capability utility timed out. These facts determine the probes below.

## 1. Named, read-only mapping probes

Extend the body with fixed-allowlist collectors; no endpoint takes shell commands,
arbitrary paths/URLs, SQL, memory addresses, raw device bytes or native event IDs.
Use camera monotonic timestamps and boot ID to correlate results. Absence, access
failure and truncation must be explicit, with source paths/collection method.

| Probe ID | What it reads | Question it answers |
|---|---|---|
| `runtime` | Release/state marker, uptime/boot ID, startup phase/latch presence, portal/native process liveness | Did the runtime settle and does the native app still run? |
| `network-bindings` | Existing interfaces/addresses/mode metadata and bounded proc TCP socket inventory | Is RTSP absent, loopback-only, AP-bound or home-bound? |
| `native-ownership` | Allowlisted native process descriptors and socket inodes for display, audio, motor and IPC resources | Which stock process owns the resource we need? |
| `audio-routing` | ALSA cards/PCM state and read-only mixer controls for MIC1, ADC/DAC, capture route/gain | Is capture routed, closed, or already owned? |
| `display-layout` | fb sysfs geometry/stride and supported read-only framebuffer metadata query, if bounded | What format and physical/virtual geometry must a future renderer support? |
| `ipc-inventory` | Existing Unix sockets, owner and available queue metadata | What narrow native bridge candidates exist? |

Do not read pixels, UART streams or I2C registers as part of metadata collection.
UART reads would consume bytes intended for the stock app. Do not scan I2C, reopen
busy PCM devices, repeat tinypcminfo, attach a debugger or enable kernel tracing.
No collector may read credential files, arbitrary process memory or unfiltered
command environments. Existing ownership does not establish a supported API.

USB connection/gadget state can be observed, but it is not a reliable substitute
for the native window state. Report an unknown UI state rather than inventing a
Charging/live/menu classification. Operator-confirmed state may be annotated on
the PC with its source and timestamp.

## USB webcam and mass-storage investigation

These modes are explicit early investigation tracks, not assumed working features.
The menu offers both, and earlier normal USB enumeration showed a storage-class
interface. That does not establish end-to-end webcam capture, useful audio,
which volume is exported, or a safe update path through the camera. Run these
trials separately from native Wi-Fi initialization so transport-specific success
and failure remain distinguishable.

### Common mode-switch protocol

Record a Charging Mode baseline: physical USB port chain, descriptors/serial,
kernel bindings, block/video/audio nodes, Wi-Fi address, CammyOS identity/boot ID,
resource ownership and media sockets. Release any lease and stop observation
before changing mode. Have the operator select a native menu option; do not force
gadget configurations through ad-hoc sysfs writes. Enumerate the changed device
before opening any interface. Track serial plus physical USB topology; VID/PID
can change with mode, and the PC has other cameras/audio devices.

Compare native app/media state before/during/after each mode, including whether
ADB and the home portal survive, the stock preview/audio devices are released,
RTSP appears, or the camera reboots. Restore Charging Mode afterward and verify
normal live view, HOME_STATION and no unexpected active CammyOS session. Treat a
failed transition or lost diagnostics as an observation, not permission for a
reboot, firmware update or a raw device workaround.

### Webcam mode: potential local vision and audio backend

1. Identify video-class and any audio-class interfaces, device nodes, native driver
   bindings and advertised formats/resolutions/frame intervals. Query capabilities
   first. Do not assume a USB microphone, speaker, H264, MJPEG or a particular rate.
2. Add a narrowly scoped **host CammyOS USB-media adapter/probe** before active
   capture. It must select the verified M105 only, use the authoritative lease and
   stop path, and reject conflicting body/host ownership. A disconnected body portal
   must not cause silent ownership bypass. All sensing is an explicit hook operation;
   no opening the first `/dev/video*`, unrelated desk camera or default microphone.
3. Through that hook, attempt one fresh frame, then a bounded10second stream if
   the first frame succeeds. Measure timestamps, negotiated format, frame delivery,
   drop/timeout behavior and cancellation. Store samples privately on the PC.
4. If USB Audio is actually present, separately test bounded capture and/or low-volume
   playback through the corresponding host hook after checking direction and ownership.
   USB video alone proves neither two-way audio nor microphone quality.
5. Compare USB observations with native Wi-Fi: does webcam selection initialize the
   encoder, expose another stream, or suspend network media? Do not attribute a
   network improvement to a mode change without before/after evidence.

An existing exploratory `Cammy Bot/tools/uvc_raw_probe.py` is a research lead,
not validated release tooling. It hard-codes a candidate VID/PID, detaches kernel
interfaces and sends UVC PROBE/COMMIT requests. Those are stream-state changes,
even though its docstring says read-only. Review/rework it with explicit device
selection, bounded operation and reliable driver/interface cleanup before any use.
Prefer the standard host video driver first. Raw USB escalation needs a specific
observed driver/protocol problem, not just failure of native RTSP.

A working USB-media backend would be useful for local development and perhaps
wired operation. It does not complete the USB-power-only, Wi-Fi companion goal.
Advertise transport and source explicitly; never silently substitute USB for a
claimed working home-Wi-Fi stream.

### Mass-storage mode: identify what the camera exports

1. Prevent automatic read-write mounting of the identified camera LUNs before the
   trial. If that cannot be ensured, stop before filesystem access. Inventory USB
   storage interfaces, LUN count, read-only flags, capacities and filesystem UUIDs.
2. Apply a host-side read-only block-device policy to the verified target where
   available; inspect known filesystems using a read-only mount. No repair,
   formatting, boot-sector writes, arbitrary SCSI/vendor commands or payload copy.
3. Establish whether each LUN is the32GB TF filesystem (compare known UUID and
   release-file hashes), a small internal data volume, or something else. Size
   and label alone are insufficient. A storage menu does not imply root-filesystem
   or full-flash access. Avoid reading private configs or unrelated recordings.
4. Observe whether the camera actually releases its own filesystem mount while
   the PC has access. Never allow simultaneous camera and PC writers. Safely unmount
   on the PC before leaving storage mode, then verify native card access recovers.
5. Only after exclusive ownership, flush/eject and reconnection behavior are proved,
   consider a later host-managed runtime updater. Keep the external card-reader
   workflow as the current accepted update route; v0.3 storage probes remain read-only.

Storage success means we know what is exposed and how it is owned, not permission
to update or flash it. If the card remains mounted by the camera, record that
blocker and end the test without writes.

### USB-mode acceptance report

Record each mode independently: device identity, negotiated transport, portal/ADB
availability, native resource changes, supported and actually tested media, exposed
volumes/ownership, cleanup and recovery. A successful trial includes returning to
the known Charging/home-Wi-Fi state without unexpected sensing or storage writes.
The first PCCAM trial now confirms descriptors and a standard USB-audio binding;
UVC initialization fails and a leased host capture returned silence. A fresh-boot
return to Charging/home runtime was observed. Webcam acceptance is incomplete;
mass-storage trials remain pending. See [results](../PCCAM-AND-APP-2026-11-14.md).

## 2. Probe supervisor and API

The current C portal handles requests synchronously. Keep new work out of that
control loop. Use one supervised probe worker, explicit deadlines, a bounded
result buffer and one active job at a time. Do not add a general-purpose remote
execution facility. The helper must invoke collector code directly, not shell
strings supplied over HTTP. The existing synchronous HTTP parser can also wait on
a slow client; address that scheduling path or explicitly isolate the stop/lease
loop. A background probe worker alone does not establish responsive stop behavior.

API additions below are still absent from the installed v0.2 camera. Catalogue/job
and preview-status routes are implemented locally:

| Operation | Behavior |
|---|---|
| `GET /v1/probes` | Authenticated catalogue: supported IDs, effects, limits and availability |
| `POST /v1/probes` | Start one named probe; validate arguments and return202 with job ID |
| `GET /v1/probes/{id}` | Bounded status/result, timing, availability, truncation and failure reason |
| `POST /v1/probes/{id}/cancel` | Cancel an owned active experiment; authorised stop can cancel all owned actuation |
| `GET /v1/preview/status` | Separate event-session, listener, SDP and host frame-readiness evidence |

Read-only collection requires authentication and serialized execution; state-changing
experiments additionally require the existing body lease and a per-boot enable
flag. Probe job ownership uses the same cooperative agent labels, not a claim of
independent per-agent authentication. Fixed catalogue identifiers must be distinct
from generated job IDs in the implementation/schema.

Initial proposed budgets: metadata deadline1second, explicit preview experiment
maximum5seconds, one worker, one retained current result plus a small bounded RAM
history (maximum64KiB total). Measure these targets; do not quietly increase them
when a collector blocks. Return busy, timeout, cancelled and unavailable honestly.
The host request body/reply limits must remain compatible with the probe schemas.

A timeout must not block the portal while waiting for an unkillable kernel task.
If a worker cannot be reaped, mark probing degraded and refuse replacements rather
than leaking processes. For state-changing probes, avoid opening interfaces with
unproven cancellation behavior. Stop/lease-expiry responsiveness is an acceptance
requirement, not satisfied merely by moving a helper into the background.

Results/logs stay in bounded tmpfs buffers. The PC may export a redacted report
under private project storage. Do not write observation logs to internal flash or
automatically fill TF. No probes or sensing start automatically at boot. Normal
runtime networking and existing startup-latch behavior remain in place.

## 3. Instrument the existing preview hook

Represent the existing event socket and stream readiness as different states:
`idle`, `event-connected`, `waiting-for-listener`, `sdp-ready`, `failed`.
A decodable fresh frame is a separate host-observed state, not a body inference.
Keep compatibility with existing SDK lease/stop operations; document the new
response schema and capability flags. Do not advertise a working stream solely
because stock-preview support was compiled in.

Additional app evidence now identifies an AP-specific9999 client and an
approximately30second4016 repeat loop. Include a bounded, lease-aware keepalive
scheduler on the same retained event socket in the compatibility tests. Stop,
expiry and cancellation must stop it; no request may silently renew a lease.
Trace the rest of the Android app's initialization/callers before introducing any
new native command. See [PCCAM/app findings](../PCCAM-AND-APP-2026-11-14.md).

For an explicitly requested preview experiment:

1. Require the control lease and record the native-process/socket baseline.
2. Start the existing4016 session once; record time, connection outcome and a
   tightly limited parsed event response summary if available. Do not log arbitrary
   native payloads: they can contain names, paths or settings.
3. Observe listener changes for up to5seconds. Classify absent/bound-wrong-interface
   states separately from an RTSP protocol error.
4. If reachable, issue only bounded RTSP OPTIONS/DESCRIBE against the fixed native
   endpoint. SDP success does not prove frame delivery. Decode on the PC after an
   explicit observation request and attach freshness/timing evidence.
5. Stop/cancel/expiry must close the event session and experiment-owned connections.
   Do not alter another existing preview session. Reject conflicting sessions
   rather than reconstructing an ownership state that was not observed.

The owner may run the ordinary preview session independently of the bounded probe;
the probe API should not silently close a session it did not create.

## 4. Conditional native-media bootstrap experiment

First inspect the known local binary, event receiver and native app protocol.
The Unix socket is currently identified as a udev/hotplug byte channel; it is not
an approved generic event-injection API. Static function addresses are evidence
locations, never callable control parameters.

An active bootstrap candidate is eligible only after we understand its message,
preconditions, effects and cleanup, and can test its parser/state machine locally.
Package one narrow named operation through CammyOS. It must preserve managed
home Wi-Fi and avoid changing persistent settings, triggering recording, calibration,
updates, native-app restarts or unrelated hardware events. Keep it disabled by
default and require explicit enable plus the control lease for a bounded trial.
Do not replay SOFTAP_ENABLE blindly: the full event can have side effects beyond
starting RTSP, and a presenter message may differ from its network-control event.

If no safe existing bridge is established, ship read-only probes/readiness and
report this dependency as blocked. Do not substitute raw memory patching,
LD_PRELOAD/ptrace injection, guessed udev bytes or forced Wi-Fi-mode changes. A
separate media backend or deeper vendor integration is a later design decision.

## 5. Audio, motor and face work in this version

**Audio:** collect routing and ownership through the new probe only. Prepare the
capture adapter design around explicit MIC1 routing ownership, bounded PCM buffers,
measured formats, mixer restoration and stop/expiry behavior. Actual capture is
optional follow-on work after routing/driver cancellation is understood; it must
be its own declared sensing operation, not a side effect of a diagnostic probe.
Speaker playback and duplex are not implied by successful metadata collection.

**Motor:** keep `/v1/look` unavailable. Preserve the recovered UART structure and
separate I2C findings as static evidence. Build offline decoders/fixtures if useful;
do not emit commands or infer degrees from480/270 coordinate checks. Enable real
motion only after identifying axes/units, arbitration, limits and a local stop
mechanism independent of host connectivity.

**Face:** collect geometry and display ownership. Keep `/v1/face` unavailable until
a rendering/ownership contract exists. Continue local face simulation, without
claiming it controls the camera screen. No direct VRAM edits.

## 6. Host, simulator and architecture updates

- Add probe catalogue/results and a media-readiness timeline to the desktop.
- Show actual versus simulated observations, timestamps, unknowns and timeout
  reasons; clear stale evidence on boot-ID change or disconnect.
- Add simulator cases for missing native bootstrap, wrong listener binding,
  event-connected/no-server, valid SDP/no-frames, a slow/hung collector and resource
  contention. Inject webcam enumeration/stream and mass-storage LUN/ownership fixtures,
  plus USB menu and normal-live metadata; label them
  simulated instead of claiming automatic camera-window detection.
- Keep raw probe/sensor evidence private. Export concise redacted evidence with
  source/runtime versions and stable artifact hashes.
- Update architecture.md when findings land, not only at the end of the release.
  Keep this document's proposed APIs out of the implemented-capabilities tables
  until source and hardware validation justify promoting them.

## 7. Implementation order and acceptance gates

1. Prepare the USB mode identity/baseline procedure and host fixture tests. Plan
   read-only storage enumeration and capability-only webcam checks early, then
   bounded capture only through a host CammyOS hook. These tracks can proceed
   independently of the unresolved native Wi-Fi media bridge.
2. Define catalogue/job/readiness schemas; implement the worker supervisor and
   read-only collectors against fixtures. Add OpenAPI/SDK bindings.
3. Extend simulator and desktop; test cancellation, auth, concurrency, buffer limits,
   timeouts, boot changes, partial/slow HTTP clients and stale-result handling on PC
   loopback only. Set and measure an initial stop-response target below500ms during
   slow metadata collection; do not claim a hardware stop guarantee from that test.
4. Build static ARM binaries and run native plus Cortex-A7 user-mode tests. Retain
   the v0.2 TF release and rollback copies; do not increment current VERSION until
   the implementation is ready for an actual new build.
5. Package a credential-free TF runtime with source/license notices, checksums and
   release notes. Verify reproducibility and hostile-archive rejection. Preserve
   existing startup-hook/private-config compatibility; no internal flashing.
6. Update the powered-off camera's TF card using the verified backup/copy/eject
   procedure. Boot and validate read-only probes first; confirm home networking,
   latch clearance and normal native camera operation.
7. Verify results against a limited USB read-only reference, then test the bounded
   existing-preview experiment. Prove stop and lease expiry still respond during
   slow/failed probing. Do not knowingly wedge a real kernel driver to test failure.
8. Only if the native bootstrap candidate meets the separate evidence gate, run
   its explicit hook trial. Success means a fresh decoded home-network frame with
   no radio-mode switch, persistent-setting change or native-app failure. Otherwise
   retain the probe findings and leave video unavailable.

The release can be accepted as an internals-mapping release when the probe and
readiness gates pass on hardware. Working video is a separately reported feature
acceptance. Full-duplex audio, real gaze, face rendering, full OS replacement and
24-hour endurance are not completion criteria for0.3 and remain on the broader
[milestones](milestones.md).


## Implementation checkpoint — 2026-11-14

Locally implemented: four read-only collectors, catalogue/job/cancel APIs, one
supervised worker, one-second deadlines, two retained public jobs and separate
legacy diagnostic buffer; nonblocking HTTP request assembly and deferred legacy
reads; nonblocking event connection,30second4016 keepalive and five-second fixed
RTSP DESCRIBE readiness check with status/recheck endpoints; simulator and desktop
probe/readiness views. Stop/release/expiry semantics and evidence limits are in
[preview readiness](../PREVIEW-READINESS.md) and the [probe contract](../PROBE-SUPERVISOR.md).
No automatic probe starts at boot or silent lease renewal was introduced.

Validated:65native/host tests,15ARM protocol tests and9ARM preview fault tests.
A reproducible, credential-free [v0.3 TF candidate](RELEASE-0.3.0.md) is prepared
and strictly staged locally, then copied/remount-verified on the TF card with
LAB/on-card0.2 backups. The actual0.3 boot, cleared latch, HOME_STATION and all four probes are verified
through the restarted gateway. No internal flash write occurred. The bounded
preview result is rtsp-unreachable; Stop and short-lease cleanup passed. Full
physical media acceptance has not occurred.

The saved [QZ app trace](../MEDIA-STARTUP-TRACE-2026-11-14.md) narrowed the backend
and lifecycle paths but found no eligible narrow native bootstrap. Therefore the
candidate deliberately ships read-only probes/readiness with that dependency
unresolved, as permitted by section4. Metadata mapping remains narrower than the
full table: native ownership, mixer routing and IPC inventory are future work.
