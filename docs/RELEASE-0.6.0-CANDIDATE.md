# CammyOS 0.6.0-rc1: integrated desk-companion candidate

Decision,2026-11-15: consolidate the demonstrated components into one repeatable
camera/desktop release. Stop using isolated one-shot screen demos as the normal
workflow. This document defines the next deliverable; VERSION is still0.5.0 and
no0.6 candidate has been installed or accepted.

## Outcome

Boot Cammy from USB power with its TF card, join home Wi-Fi, display an idle
character, then let the existing Reachy app/agent SDK open a bounded live session,
change/replay expressions, use supported head controls and return to idle.
No Wi-Fi-screen ritual, USB shell per action or app replacement during a session.
The desktop service reconnects to a matching camera release and reports actual
capabilities. Idle microphone/recognition are off; explicit user/agent sessions
have visible state and expiry. Speech can continue through the existing P15.

## Evidence and present blocker

The native-GUI static-face trial **did run** on boot a68e8ed8. P15 frames116–135
show the face, followed by the stock USB menu. A conservative8.873s interval
inside that display contains45 advancing decoded video frames and140,800 new
microphone samples. Entire session:148frames/448,000samples, expiry to idle,
owner null, preview off, workers/decoder joined and ALSA capture CLOSED. Original
S01 was restored and selector disabled. Immutable trial storage stayed resident
until exit. This proves finite drawing/coexistence, not live face commands,
animation, colour/detail fidelity, full driver ownership or endurance.

Subsequent boot IDs f219bdd9 and bdc7d9ca confirm later reboots; no agent reboot
command was issued after the trial request. Cause remains unresolved. The latest
read-only check on bdc7d9ca found stock PID779, original S01/native hashes and
458s uptime, with the prior boot's `startup.pending` blocking automatic runtime.
Current stability is therefore not acceptance of automatic media startup.
Do not clear that guard or replay/reboot the camera while preparing this RC.

The missing stock `rmdir` primitive was independently found/fixed: guarded empty
RAM directory removal passed on the actual camera. Updated launcher fixtures,
GUI-profile tests and exact package pins cover the correction. It does not
explain every later reset without further evidence.

## One release, four components

1. **Boot/recovery:** a reviewed versioned TF payload selected by one installed
   bootstrap; one native owner per boot. Replace the trial's automatic S01
   restore-after-every-demo with pending/healthy/fallback release state. Failed
   attempts select stock recovery with sensing off; no automatic reboot/retry
   loop. Preserve original executable and an exact rollback copy. Network loss
   degrades service rather than triggering an OS restart. Log named boot phases
   and failure reason before doing hardware work.
2. **Native face service:** retain the proven GUI hook/API path and exact ABI.
   Add bounded current-boot, owner/session-bound commands for show/hide, six
   moods, replay and an idle loop. Start with a finite immutable frame bank
   (including blink variants/effects) retained through process exit. GUI objects
   change only on the GUI thread. Do not free/reuse pixel buffers on an assumed
   draw/flush fence. Keep existing StackChan/ParamFace-facing desktop API.
3. **Camera runtime:** separate network-ready, GUI-ready, media-ready and active
   sensing. Resolve the reset regression before acceptance. Start media from an
   explicit session only after readiness; remove trial-specific RAM identity
   overrides by giving boot/runtime the same exact release manifest. One normal
   media lifecycle, bounded cleanup, idle microphone off, no AP/menu toggling as
   a user workflow. Keep stock media/motor drivers. Expose only verified input
   controls with a renewable hold deadline and independent release guardian.
4. **Desktop companion:** one matching service behind the actual Reachy app and
   current Cammy SDK. Reconnect, readiness, live video/mic, expression/replay,
   supported head controls, desktop PTT, Stop and diagnostics share ownership.
   Keep full Reachy body compatibility and physical red-button suppression
   labelled unavailable until implemented and tested; they are not implied by
   hosting an app in Reachy's simulator daemon.

## Build/update boundary

Produce one versioned local TF candidate bundle, component hashes, host companion
package, capability matrix and rollback instructions. This needs a TF update and
one reviewed startup/bootstrap update for the resident native hook. It does not
require replacing the kernel or writing a complete NOR firmware image. Later
assets/userspace updates should use versioned TF releases with a single planned
boot. Build a private native candidate from the saved stock binary locally;
keep proprietary bytes, credentials and captured media out of git/public files.

Do not relabel existing0.5 code as a completed0.6 release. The candidate version
and all required components must be bound by the manifest and actually built.
An unchanged component can remain pinned explicitly. Do not ship ad-hoc private
trial scripts as the production supervisor.

## Implementation order and acceptance

A. Fix/trace startup and recovery first: device-primitive checks, named phase
logging, exact owner identity, watchdog progress, startup budget and independent
network/media states. Preserve the current stable hardware state during local
work; hardware tests are one planned candidate update after local gates pass.

B. Build the reusable native face/GUI command service and host adapter. Test
replay repeatedly without a reboot, mood changes, idle loop, client loss,
expiry/Stop and screen changes. Match host/ARM pixels and test actual device ABI.

C. Integrate session media, bounded input and desktop PTT into the same versioned
runtime and app. Run meaningful local/ARM/fault tests, then build the disarmed
candidate and exact installation/rollback package for review.

D. Hardware acceptance is a single outcome: three deliberate cold boots without
reset or Wi-Fi-menu intervention; at least one hour of combined operation;
repeated face/replay and head-control/Stop operations with video+mic; idle capture
CLOSED; disconnect/expiry recovery and a stock rollback boot. Log fresh frame/PCM
provenance, command/GUI acknowledgements, actual LCD output and owner/cleanup
state. A boot counter or missing ready signal must not cause retry loops.

Full arbitrary-angle gimbal feedback, Cammy-speaker arbitrary speech/duplex,
physical record-button PTT and voice cloning are later milestones. Voice work
remains paused. These do not block a clearly labelled useful first companion
candidate, but they must not be advertised as implemented.
