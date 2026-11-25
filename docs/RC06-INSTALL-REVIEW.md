# CammyOS0.6.0-rc1 installation and acceptance review

This is a private integrated candidate, not an accepted release. No installation
or reboot is performed by its builder or validator. Proprietary stock-derived
bytes stay in ignored output. The camera remains on its last stable stock boot
while local assembly runs. The previous unexplained resets remain unresolved.

## Exact package

`python3 -m tools.rc06_build --out private/rc06/candidate-N` builds one new directory.
`python3 -m tools.rc06_validate private/rc06/candidate-N` verifies its members,
exact native patch, saved-libc ABI, derived launch sources and disabled default.
The package manifest records every member's SHA256/size. It is an integrity and
local provenance record, not a signature or physical acceptance certificate.

The payload binds the portal0.6.0-rc1, reusable GUI service/frame bank, fixed native
media client, shared-PCM profile, bounded input guardian, networking, media worker,
health policy, original rollback init and corresponding desktop sources/artifacts.
No token, network password, model cache or captured media is included. The native
DSO and candidate are generated locally from the saved exact stock executable.

The desktop directory includes the Linux x86-64 ParamFace renderer, presets,
editor distribution and MIT license. Use the existing Reachy reference venv on
this machine (Python>=3.10, reachy-mini1.14.0, Pillow12.0.0 and the existing FastAPI
stack). FFmpeg and the existing configured gateway remain prerequisites. Local
speech additionally needs the separate prepared voice environment/models; none
is silently downloaded. The official daemon remains a reference body, not a
calibrated mapping to Cammy angles. No new service is started by the builder.

## Local evidence

`make test-rc06`:185tests passed, no skips, using the prepared Reachy environment.
A separate69test startup/selection/face-launch/rollback suite passed. Evidence is
local loopback, confined relocated shell, synthetic GUI/input and ARM emulation.
It includes the actual device-compatible DSO, not execution of the vendor app.
Repeated builds matched release identities and every member byte; tampered/native,
armed, linked and altered-bootstrap packages were refused. These results do not
close any physical boot, animation, motion, durability or cleanup gate.

## One planned update

1. Validate the package on the PC and confirm the current camera is idle. Record
   current boot UUID, native PID/start, original S01 hash, existing S99 hash,
   `/proc/mounts`, card identity and available memory. Abort on any mismatch with
   `installation-plan.json`; it contains the exact before/after identities.
2. Copy `tf/CammyOS-releases/<release_id>` to the TF card as a new version slot.
   Verify hashes after copying. Preserve the existing CammyOS directory and
   private network/token configuration. Never reuse the recovery boot stub.
3. Stage internal files beside their targets, verify hashes/modes, then install
   the exact proposed S01, S99 and allowlist with selection **disabled**. Keep the
   original S01/S99 rollback copies off-device. Preserve old startup/claim evidence
   in the private PC checkpoint before clearing it for an explicitly planned trial.
   A partial installation must remain guarded by `startup.pending`; do not arm it.
4. Recheck all target identities. Enable only the reviewed `mode=select` template
   for the first one-shot trial, with the package's exact arm record on TF. This
   is an explicit internal userspace/init change, not a kernel or full-flash image.
5. Ask Adam for one deliberate Power off/on. Leave USB connected, choose Charging
   Mode, and leave Wi-Fi controls untouched. Neither builder nor runtime issues a
   reboot, warm native restart or automatic retry.

Do not improvise shell writes from this prose. The next hardware operation starts
with a concrete checked transaction against the actual before hashes and backed-up
state. A changed camera must be re-inspected, not forced to match stale assumptions.
No automatic installer or resident opt-in is supplied/enabled by this candidate.

## Acceptance and fallback

The early selector runs the RAM candidate once. Failed/interrupted/pending prior
runtime state forces stock before TF probing. S01 retains stock power/watchdog
setup and launches one owner. Runtime waits for normal startup, brings networking
up, and leaves media uninitialized. An authenticated session explicitly requests
media preparation once per boot; failures retain the fault latch and never retry.
It binds the exact native owner, boot and deadline. Stop cancels queued work; a
media/native fault may require a deliberate recovery boot.

Native face service idles/blinks and supports repeated show/hide/replay without a
reboot. Screen changes yield to stock until explicit replay. GUI acknowledgement
is not a physical LCD observation. Input holds require renewal; the independent
guardian releases on expiry/parent loss. No angle calibration or autonomous
tracking-stop guarantee is claimed.

Health requires120continuous seconds of exact owner/GUI/portal/network survival.
Ordinary network loss resets this timer without a reboot or native takeover.
Health alone never rearms. Resident/one-boot-advance records are separate opt-ins
and remain absent during initial acceptance. A failed trial leaves its claim,
so the next boot returns to stock. Flash durability is still a hardware gate.

Accept only after three deliberate cold boots, at least one hour of combined
face+video+mic, repeated head/replay/Stop/disconnect tests and a stock rollback boot.
Observe LCD via P15 and Adam, fresh frame/PCM identities, ADC CLOSED when idle,
no owner leakage and no reset. Leave all hardware fields pending until observed.

Rollback: disable selection, restore and hash-check `rollback/S01app.stock` and
`rollback/S99cammy-tf.previous`, preserve fault/claim evidence, remove the candidate
arm or TF card, then request one manual Power cycle and verify stock live view.
Do not kill/restart sdvcam or hand off its watchdog while running. Full SPI-NOR/FEL
restoration remains unverified and is not the rollback claim here.
