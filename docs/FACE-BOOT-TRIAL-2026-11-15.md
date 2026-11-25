# First physical face: staged one-shot trial

State: **revision2 physically displayed the static face with simultaneous
Wi-Fi A/V; release acceptance is blocked by later resets.**

Boot a68e8ed8 selected the exact candidate in4.91s of selector work. The observer
restored original S01 and disabled selection before media startup, observed the
exact library/watchdog owner, and applied the scoped RAM media profile. P15
frames116–135 show the face for approximately ten seconds; frame136 is a display
transition and later frames show the stock USB menu. A conservative8.873s inside
the visible interval contains45 advancing decoded frames and140,800 new PCM
samples. Full session148frames/448,000samples expired to idle with owner null,
preview off, workers joined and ALSA capture CLOSED. P15 exposure hides fine
features/colour. Adam missed the display; no human fine-detail approval claimed.

Later boot IDs f219bdd9/bdc7d9ca changed without another agent reboot request.
Current stock boot is stable with automatic startup blocked by the prior failed
runtime guard. Cause remains unresolved. Native LCD animation/live face commands
and stable automatic startup are not accepted. No further camera changes while
building [the integrated0.6 candidate](RELEASE-0.6.0-CANDIDATE.md).

Private evidence: `r2-physical-result.json`, `r2-cleanup-identity.json`,
`boot-observer-checkpoint.json`, `lcd-boot-*.jpg` under private/character-face.
The current process is now stock after the later reboot; trial RAM library gone.

## Exact trial

The status-only launcher remains unchanged/blocked. A separate constant-only
GUI profile uses `gui-face-trial-v1` and `face-trial-authorized`. Local relocated
shell tests cover one selection, next-boot stock fallback, disabled/old-class
refusal, corrupt copy, sync error and TF detach failure.The launcher suites contain50 tests; six GUI-profile tests and13 package tests
pass, plus two narrow media-profile tests. The earlier native suite also passed. These are not hardware boot proofs.

The candidate preserves original media sites/watchdog and all stock bytes
outside the documented main/GUI hook, stubs and program-header ranges. It loads
an immutable356×400 happy image through native canvas APIs on the native GUI
thread. It waits60s, shows for10s and removes on expiry/Stop/screen change. A GUI
hang can delay removal. No raw framebuffer/VRAM access, stock process kill,
late owner handoff or full firmware flash is involved.

Actual staged identities:

- Attempt: `5d8207781569c5d2fa9679cc4c0ae77c`.
- Candidate/library and source hashes: ignored
  `private/native-face/pikachu-av-boot-review-r2/review-manifest.json`.
- Installed S01 MD5: `4666b305ebf0931a2e5e3be6180ea0c1`.
- Original S01 MD5: `daeb505e811834088f64cb07696c5cbe`.
- Original `/usr/bin/sdvcam` MD5 remains
  `8689ce53c4674563a7a6935f97c0d727`.
- Before reboot, stock PID942/start559/boot
  `884962c8-58ed-48f3-a57c-8033f51692b3` and watchdog fd3 were unchanged.

Staged camera paths: one-shot payload/arm under `/mnt/extsd/CammyOS-native/`;
allowlist/mode under `/etc/cammy/native-select.*`; exact replacement
`/etc/init.d/S01app`. No accepted `/mnt/extsd/CammyOS/` runtime file was changed.
This is an internal startup-file change and TF trial staging, not a claim of
zero persistent writes. Local staging receipt/log are private.

## Observer sequence

`private/character-face/observe_face_boot.py` is finite trial instrumentation,
not a shipped desktop service. It waits up to ten minutes for a different USB
boot ID, checks a single exact candidate executable/library mapping, records
cropped P15 frames for up to110s and collects identity/launch status.

After seeing a current-boot completed selector status, even without a native
owner, it transactionally restores original S01 from the
review kit and sets `mode=disabled`, leaving the current app running. It does
not reboot the camera. The selected copy lives in RAM for that boot only.

The accepted media initializer pins stock executable identity. A trial-scoped
RAM derivation changes exactly its two native executable checks, preserves the
stock-file hash check, and adds exact current-boot plus candidate-SHA guards.
It replaces the RAM initializer only if its source hash matches the accepted
script. TF payload stays untouched. Identity or timing failure stops the
combined-media proof; it never restarts native ownership or retries native init.

If native media reaches `sdp-ready-idle`, the observer requests one30s Wi-Fi A/V
session in the running Reachy app. Receipt timestamps, camera uptime and P15
frames must establish overlap with the physical image; successful independent
runs do not count as simultaneous LCD+A/V evidence. Finally it stops its owned
capture and media session and saves cleanup receipts. It does not play audio.

## Operator action and recovery

Power off/on once with TF and PC USB connected. Choose Charging Mode promptly
if prompted; leave Wi-Fi/buttons untouched and keep the P15 view clear. A happy
face should appear for ten seconds about a minute after native startup. The
host loop's six moods are not yet a native LCD loop.

If the candidate crashes or hangs, its durable claim makes the next boot stock;
flash durability remains an unclosed assumption. The observer restores S01 only
while USB is reachable. If it fails, inspect its checkpoint before doing anything
else. Original S01 is also in the host review kit and the current staging kit
(`/tmp/cammy-face-install-kit`, lost on reboot). Use the exact reviewed restore
transaction with verified files and target identity; never execute S01 as a
recovery command or warm-restart sdvcam. A second manual boot is required to
prove restored stock startup. TF removal alone does not restore S01. Low-level
FEL/SPI restoration remains unverified.

No automatic retries or permanent face capability flag follow this trial. Even
if it passes, native animation, live face commands and arbitrary Cammy-speaker
output remain separate work.

## Missing primitive and correction

The stock device has neither a `rmdir` executable nor a BusyBox `rmdir` applet.
The old helper used it when removing the private TF mount directory and library
folder. After a successful unmount, that command necessarily fails and blocks
launch with recovery required. This is a verified defect that can explain the
first failure; the first boot's final selector status was not captured, so its
exact path remains unproved.

The generic helper now checks that a private directory is real and entirely
empty, including hidden/dangling-symlink entries, then removes it using the
available `rm -r`. The primitive is included in the RAM self-test. Actual camera
RAM checks passed empty removal and preservation of hidden files, dangling links
and directory symlinks, with original native/init/watchdog unchanged. A relocated
selection also passes with `rmdir` deliberately unavailable. Package pins were
updated to the exact corrected blocked-helper revision, not loosened.

The old TF payload, allowlist, disabled mode and inflight record were archived
under explicit `*.failed-33b5bd08` names; old RAM status was archived too. No claim
existed. Current original startup/stock owner were verified before revision2
staging. An exact stale runtime guard from the failed boot was archived and the
accepted S99 runtime started normally: home station, `sdp-ready-idle`, capture
CLOSED were observed before the next trial request. Accepted TF payload unchanged.

The revised observer records selector status while no native PID exists and
restores stock startup as soon as selection completes. Its wider P15 crop covers
the current camera position; the first boot images showed Cammy being moved out
of the narrower crop, so they do not prove LCD output during that attempt.
