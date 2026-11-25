# Hardware ownership execution plan — 2026-11-14

Adam accepted the hardware-ownership direction after the
[go-forward assessment](GO-FORWARD-OPTIONS-2026-11-14.md). This selects the
architecture below; it does not approve a live native restart, internal bootstrap
write or firmware deployment. This document is a plan, not implemented features.

## Target architecture

Keep the stock kernel, drivers and camera tuning. Initially retain `sdvcam` as
the sole owner of camera/ISP/encoder, watchdog, GUI, audio and motor interfaces.
Add a small cooperative native bridge to an exact offline copy. The body portal
validates authentication/leases and sends bounded requests over local IPC; the
bridge dispatches supported operations onto the proper native thread and reports
completion/readiness. The PC service owns agent orchestration and media consumers.

Agents must not independently open the camera's hardware nodes or compete with
the vendor app. Native ownership is an implementation boundary, not permission
for multiple controllers. Keep the existing HTTP/Python interface and simulator.

## Stages and concrete deliverables

| Stage | Deliverable | Completion gate |
| --- | --- | --- |
| 1. Native ownership contract | Small address/ABI/thread/lifetime map for media, watchdog and native GUI/task dispatch; launch-context and error-path inventory | Every planned call has an identified object, valid lifetime, required thread and completion/error rule. Unknown adapters stay unavailable. |
| 2. Status-only bridge | Local IPC specification, bridge source, fake native backend and exact-build offline integration tooling | Hello/status/capabilities work in host and ARM fixtures; no hardware action, shell command or arbitrary-address interface. |
| 3. Owned video slice | Media start/stop/status requests through the portal and native dispatch, with one owned event/stream session | Fake/ARM fault cases cover duplicate start, missing recorder, blocked call, failure, Stop, expiry and disconnect. Frame readiness stays separate from event/listener readiness. |
| 4. Activation and rollback | Concrete one-shot boot-selection or proven cooperative-handoff bundle, manifests, disarm behavior and recovery runbook | Exact activation route is implemented and locally tested; source/candidate identity and fallback are reviewable. Current S99 payload copying alone is insufficient. |
| 5. Hardware acceptance | First a status-only native build, then one video-enabled attempt through the selected guarded workflow | Original hash retained, one app, stable watchdog/home Wi-Fi/LCD, fresh frames and measured cleanup; disarmed next boot and stock recovery verified. |
| 6. Body adapters | Microphone/speaker, bounded gimbal and GUI-owned face, added separately | Audible/intelligible audio; real movement/stop/limits; persistent face without breaking media. No inferred capability from simulated behavior. |
| 7. Companion service | Real media ingestion, speech/agent adapters and resident behavior above the gateway | Natural conversation/interruption, coordinated face/gaze and recovery with USB power only, followed by endurance testing. |

The first code milestone is the **status-only bridge and fake backend**. The first
physical milestone is **a fresh home-Wi-Fi frame with owned stop/recovery**.
Do not bundle audio, motors, a face and a new OS into that first hardware trial.

## Bridge contract

Use a private local Unix socket or an equivalently bounded native IPC mechanism,
selected after mapping the native task queue. Define protocol version, request ID,
boot identity, owner/lease generation, deadline, typed arguments and typed result.
Never expose raw addresses, generic native messages, device writes or shell text.

The initial command set is hello/status/capabilities, followed by media start,
stop and status. The native bridge must reject stale generations and expired
requests, serialize ownership changes and stop work on native deadlines even if
the host disappears. Put a fixed limit on request sizes and queued work. A queued
Stop acknowledgement is distinct from confirmed hardware cleanup; report both.

Use the current private media candidate as call-path evidence, not as a production
authorization mechanism. A connection to the unauthenticated vendor 9999 listener
must not itself authorize media. Integrate actual lease ownership and decide how
the native RTSP/event listeners are isolated or mediated before daily-use release.

Native-thread dispatch and cancellation need explicit tests. An HTTP timeout is
not proof that a native operation stopped, and cancelling an IPC request must not
leave a delayed command that acts after lease expiry. Do not move a blocking native
call onto an arbitrary thread merely to make a timeout test pass.

## Activation preference and workflow boundary

Prefer starting the selected app **once during boot**, retaining its normal
watchdog startup, over killing a running hardware owner. Build a status-only
native candidate first. Determine whether a minimal one-time bootstrap selector
can choose a verified TF copy and fall back to stock on the next boot.

This preference is conditional: no early selector exists today, and installing
one may require changing an internal writable-overlay init hook. That is a
separate action from a TF runtime update and must be presented concretely before
installation. Do not silently modify internal init from a TF installer. Keep
kernel/NOR image replacement outside this slice.

If early selection is unsuitable, a late handoff needs the unresolved cooperative
shutdown/watchdog/storage/USB/radio contract from the
[lifecycle report](../NATIVE-LIFECYCLE-2026-11-14.md). Do not substitute SIGTERM or
force-kill. If neither activation route passes its gates, keep native hardware
actions disabled and evaluate the matching-SDK backend; do not guess a live test.

## Verification and working rhythm

First use host fixtures and ARM userspace tests with inert callbacks, then stage a
disabled private TF bundle. Keep vendor bytes/private evidence outside git. For
each stage, update architecture/status with the verified result, commit a coherent
slice and record the next gate. Do not change the resident release number merely
because an offline prototype builds.

Hardware acceptance begins with status-only identity/ownership checks. The video
trial then requires changing-target frames with timestamps, measured Stop/expiry/
disconnect behavior, and return to a normal stock boot. Extend a passing bounded
trial to sustained media operation before adding other adapters. Keep boot
acceptance, usable media, conversation quality and daily endurance as separate
results.

The immediate local work is to locate the native GUI/task dispatch boundary and
write the bridge's request/completion contract, then implement its fake backend
and status-only ARM slice. No card movement or camera code edit is needed for
those initial steps.
