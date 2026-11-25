# Guarded TF launch plan — 2026-11-14

**Design only. No launcher is implemented, card updated or camera changed by
this plan.** The stock-derived native media candidate remains offline. This is
the next integration stage for home-network vision, not a new OS or a release
announcement. The resident TF runtime remains 0.3.0.

Stage 1 now has [native lifecycle evidence](../NATIVE-LIFECYCLE-2026-11-14.md).
Read-only mapping confirms native watchdog ownership and SIGTERM crash/re-raise
behavior. No safe external process-only shutdown/restore contract is established.
An active stop backend remains ineligible; compare the [broader go-forward routes](GO-FORWARD-OPTIONS-2026-11-14.md)
before assuming the late handoff is the preferred implementation.

## Starting point and chosen approach

The [native candidate report](../NATIVE-MEDIA-CANDIDATE-2026-11-14.md) records the
exact source/candidate hashes, modified ranges and synthetic validation from
commit `dcc11b5`. It routes native client connect/disconnect through stock media
initialization/destruction without publishing an AP event. Real frames, native
callbacks, teardown and exception behavior remain unverified.

Saved read-only boot evidence shows that S01app starts `/usr/bin/sdvcam` before
S99 starts the TF runtime. Its `.sdvcam` handling maintains a backup and still
launches `/usr/bin/sdvcam`. Its start routine also writes power/MMIO settings;
its stop routine uses `kill -9` and updates the hardware clock. Neither routine
is a suitable trial handoff to replay. Evidence is private at
`private/native-start/live-init-scripts.txt`.

The first design is therefore a **late, controlled stock-app handoff**, once the
normal camera boot and managed home Wi-Fi have settled. A separate supervisor
would copy a verified private candidate from TF to RAM, stop the exact original
app through a proven shutdown path, and launch the RAM copy. This avoids changing
internal init scripts, replacing `/usr/bin/sdvcam`, mounting over it or modifying
process memory. It does require stopping the native app: that operation has not
yet been validated or performed for this candidate.

The existing [TF startup](../../scripts/tf-runtime-startup.sh) owns Wi-Fi/DHCP and the
portal separately from `sdvcam`; it does not implement this trial supervisor.
Separate processes alone do not prove the native app will leave that network
alone during shutdown/reinitialization. Establish that before hardware eligibility.

If a safe late handoff cannot be established, stop this route and document why.
An early-boot selector would be a separate design requiring new boot integration;
do not quietly turn this plan into an internal-flash or init-hook modification.

## Work sequence and exit gates

| Stage | Work | Required result before advancing |
| --- | --- | --- |
| 1. Lifecycle mapping | Trace native startup/exit, radio setup, watchdog, IPC and child ownership; map launch arguments, working directory, environment and expected settings. | A concrete shutdown/restore contract, with unresolved effects called out. No guessed event or signal sequence. |
| 2. Local supervisor | Implement a separate supervisor, boot-bound control contract and inert process/resource fixtures. Default to preflight-only; exclude native payload from public packages. | Failure paths tested without camera access; no automatic attempt on insertion or reboot. |
| 3. Private TF bundle | Package the exact candidate, original reference hash, supervisor and manifest; stage and verify locally. | Reproducible checks, bounded logs, rollback instructions and disabled-by-default configuration. No version or deployment claim from staging. |
| 4. Hardware preflight | Through a later TF update, run inventory/preflight only while the original app stays running. | Authenticated portal, managed Wi-Fi, correct identity and resources; then a separately accepted bounded handoff test. |
| 5. Handoff and recovery | First prove the original-app shutdown and restoration path, before enabling the patched app. | Original app returns with normal LCD view and managed networking, without replaying S01app or retrying repeatedly. |
| 6. One candidate trial | Start one owned native session, establish media and test cleanup. | Fresh frames plus the acceptance evidence below, or a recorded failure and bounded recovery. |

Stages 1–3 are local development. Stages 4–6 require a later, concrete hardware
trial through the powered-off TF workflow. This document does not authorize an
ad hoc restart over USB or a direct code upload to the running camera.

### Lifecycle mapping is the first implementation task

Resolve these before implementing a real process-stop action:

- Identify whether a supported graceful shutdown exists and what it does to the
  screen, capture, encoder, event socket, Unix IPC, settings and radio. A signal's
  default behavior or S01app's force-kill is not evidence of orderly release.
- Identify watchdog ownership, timeouts and any respawn service. Do not disable,
  feed or reconfigure a watchdog by guesswork. Two concurrent native apps are
  forbidden; a stock respawn must make the handoff fail closed.
- Determine whether a fresh native startup forces AP mode, writes relevant
  settings, requires inherited descriptors, moves the gimbal or expects USB/UI
  state. The media-only patch does not change the rest of native startup.
- Define how resource release and readiness can be observed without opening
  capture devices or issuing speculative ioctls. `/proc` identities, descriptor
  owners and listeners can contribute; disappearance of a PID alone is insufficient.
- Define the exact standalone original-app launch environment and a bounded
  readiness check. Normal LCD view needs a user observation until an approved
  custom hook can establish it. A process being alive is not restoration success.

If static evidence is insufficient, specify a narrow read-only probe for a later
session. Any shutdown/restart experiment is a separate hardware gate. Neither
local stubs nor this document establishes that warm restart is recoverable.

## Proposed payload and control contract

Keep the ordinary `CammyOS/` runtime and its verified rollback copy intact. A
separate private trial directory would contain the candidate, supervisor, manifest
and bounded trial-state files. Exact names/API routes are implementation work;
the existing runtime has no native-trial endpoints. Vendor bytes remain excluded
from git, public archives and the ordinary MIT runtime package.

At preflight, verify board/kernel identity, original and candidate hashes,
manifest, available RAM/storage, TF mount identity, private config readability,
one native app, no competing owner/trial, cleared ordinary startup latch and
stable HOME_STATION. Copy the candidate to a new private RAM directory; verify
again before making the RAM copy executable. Check `/tmp` really is tmpfs. A
hash manifest detects mismatch, not an authenticated software supply chain.

The proposed authenticated hooks expose status/preflight, one-shot start and
abort. Start requires the current exclusive lease, current boot ID, a fresh
supervisor nonce and the exact accepted candidate hash. The host must not retry
start automatically after a timeout or reconnect. Existing Stop, lease expiry,
controller loss and an independent deadline all reach the trial supervisor;
trial management must not block the portal's HTTP worker.

A TF arm record is necessary but insufficient: every boot still requires a
fresh authenticated start request. Consume the arm record and durably record
the attempt **before stopping the original app**. Failure to confirm state writes
means refusal. On FAT, rename alone is not proof of power-loss durability; test
write/flush/reopen failures and ambiguous state. A later boot always returns to
disarmed/preflight-only, regardless of an old arm file. Re-arming requires an
explicit new attempt record through the reviewed workflow.

Trial state is separate from `/etc/cammy/startup.pending`: do not repurpose that
ordinary network-startup latch or create a new internal persistent boot hook.
Keep secrets in their existing private configuration; trial reports contain
hashes, phase, timings, identities and failure reasons, not credentials or media.

## State machine and failure behavior

Normal path:

`DISARMED → PREFLIGHT → ARMED → CLAIMED → STOCK_STOPPING → STOCK_RELEASED
→ CANDIDATE_STARTING → READY → RESTORING_STOCK → FINISHED`

ARMED only means preflight and a one-shot record are ready. CLAIMED consumes that
record after the fresh host request; it is not entered automatically at boot.
READY requires native readiness checks, not merely a living candidate PID. Media
acceptance is a separate result recorded during READY.

Before CLAIMED, errors return to disarmed with the original app untouched. After
CLAIMED, no automatic second attempt is allowed. The independent supervisor must
survive controller loss and candidate failure. Record exact boot ID, PID, process
start time, executable identity/hash and any known child identities; never use
`killall sdvcam`, a name match or an unverified PID file as process ownership.

| Failure | Required response |
| --- | --- |
| Hash, mount, startup-latch, owner, network or identity mismatch | Refuse before stock shutdown; retain a reason. |
| Arm consumption/write durability uncertain | Refuse; no stock shutdown and no implicit re-arm. |
| Original app does not stop or ownership remains ambiguous | Never launch a second app. Stop the trial; recover only through the verified contract. |
| Candidate exits, readiness times out, Stop/expiry/host loss occurs | Close the owned event session, stop only the verified trial process tree, confirm release, and attempt original restoration once if safe. |
| Native app changes radio state or disrupts portal/network | Abort; independent local deadline still applies. Do not chase AP modes or restart the Wi-Fi supervisor as an improvised fix. |
| Candidate cannot be stopped or resource ownership is uncertain | Do not start another native app. Mark recovery-required; preserve any surviving portal and request physical recovery. |
| Original restoration fails | No respawn loop, automatic reboot or internal write. Mark recovery-required and stop attempts. |
| Power loss, new boot or ambiguous previous attempt | Ordinary stock boot, trial disabled; prior state never triggers a candidate launch. |

Use monotonic phase deadlines plus an independent overall deadline. Proposed
initial ceiling: 120 seconds per hardware attempt, including recovery; use a
shorter limit if lifecycle/watchdog evidence requires it. Specific stop, readiness
and restore budgets must be fixed and tested before hardware eligibility, with
time reserved for recovery. A missing or expired budget is a refusal, not an
unbounded wait. Do not clear a failure merely because the process is alive.

The native app requests a ten-second watchdog timeout; actual timeout and
close/nowayout behavior remain unverified. The 120-second overall ceiling supplies
no watchdog handoff allowance and must not be used as a native-stop timeout.

No trial should rerun S01app, alter internal firmware, change radio/driver/mixer
settings, inject process memory or publish guessed native events. This does not
promise the unmodified vendor app itself makes no ordinary settings writes; map
and account for its startup effects first.

## Local verification before the TF update

Keep the exact-image guard tests and synthetic ARM lifecycle fixture from
`dcc11b5`. Their 69-test suite and six synthetic ARM cases are baseline results,
not validation of the proposed supervisor. Add meaningful supervisor tests for:

- Default/disabled boot, missing host request and replayed nonce/boot/attempt.
- Every identity/hash/mount guard, PID reuse, stock respawn and competing owners.
- Arm-write/flush failure, partial bundle, read-only/removed card and power loss
  at each phase; recovery boots cannot auto-launch a candidate.
- Stock shutdown/release failure, candidate startup failure, stuck child and
  restore failure, with exactly one attempt and no concurrent native apps.
- Stop, lease expiry and controller loss in every active phase; deadline expiry
  still works with the gateway unavailable and leaves portal/network supervision
  independent. Exercise blocked native calls in isolated fake processes.
- Environment/argument fidelity, log bounds, disk-full behavior and immutable
  source files; hash mismatches prevent executing the RAM copy.

Use a fake process backend for exhaustive local fault injection, then ARM
userspace checks for the supervisor's real timeout/IPC behavior. Neither is a
full-board emulation. Only after these pass should a separate TF trial archive
and machine-readable manifest be created; do not silently add it to release 0.3.0.

## Hardware sequence and acceptance evidence

When the earlier gates are met, use the external reader with the camera powered
off. Confirm the current reader/card identity rather than assuming `/dev/sda`.
Back up the current TF runtime and trial metadata, preserve DCIM/.data and older
rollback directories, verify new files after remount, then safely eject. No raw
sector write, FEL stub or filesystem format belongs to this plan.

First boot is **preflight-only**, with USB data available for bounded read-only
reference and physical recovery. Select Charging Mode if needed; leave native
Wi-Fi controls alone. Confirm the portal and managed network are stable before
any attempt. First prove the accepted original-app handoff/restore path; then
stage a separately armed candidate attempt. Keep the camera stationary and do
not test USB modes, gimbal commands or native UI mode changes during that trial.

For the candidate attempt, obtain one lease and make one fresh start request.
Open one owned native event session through the CammyOS preview hook. Record:

1. Original source hash unchanged, running executable is the intended RAM copy,
   exactly one native app, unchanged managed mode/address and reachable portal.
2. Distinct event-connected, listener-ready, SDP-ready and fresh-decoded-frame
   milestones. Require at least two different frames of a changing known target;
   record elapsed time and dimensions without claiming success from an open port.
3. Explicit preview stop, lease expiry and controller-loss cleanup. Test these as
   separately armed attempts if the one-shot budget cannot cover them honestly.
   No unattended event session, orphan trial app or unexpected listener remains.
4. Return to the original app, normal LCD view and managed networking. Later
   verify one disarmed cold boot and a card-removed stock boot. These are acceptance
   checks to perform, not guarantees already established by the offline candidate.

Keep raw frame evidence and bounded logs private on the host. Record native RTSP
exposure/authentication: portal bearer auth does not protect the vendor RTSP
listener. The initial trial must be confined to the intended trusted LAN; do not
add port forwarding or call this an agent-secured stream. A persistent release
needs an explicit media access-control decision and ownership enforcement.

Publish a short result with passed/failed gates, hashes, phase timings, frame
freshness, cleanup and recovery evidence. Failed video means the media milestone
stays open even if handoff/rollback works. Acceptance of this experiment does not
enable automatic daily startup, audio, gimbal or face capabilities.

## Immediate next action

Use the lifecycle report and go-forward assessment to choose the launch/ownership
approach. Resolve the watchdog and native shutdown/restore gaps for a late handoff,
or separately design the bootstrap needed for early selection. Then implement
and fault-test the selected integration. A local preflight-only/fake-process
supervisor can progress without enabling real native stop. Hardware launch remains
gated; no card handling is needed for the assessment stage.
