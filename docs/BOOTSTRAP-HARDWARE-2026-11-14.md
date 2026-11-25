# Stock-only bootstrap hardware test — 2026-11-14

**PASS: both normal boot checks completed; exact stock startup is restored.**

Adam approved the [one-file test and stock restoration](STOCK-BOOTSTRAP-TEST.md).
The test retained the vendor kernel, native app and TF runtime 0.3.0. Candidate
selection stayed blocked; no media or motor operation was requested.

## Modified boot

The installed S01app was the exact blocked proposal, SHA256
`8e79dd90c290d4678d7da08dfea980b67e63440c58b9a51d6b57d6f89770766f`.
After a user-operated power cycle:

- The boot ID changed. Launch records selected stock, with gate blocked, mode
  absent, reason not-enabled and no candidate attempt.
- Preparation timestamps were 1.68 to 4.75 seconds after boot: approximately
  3.07 seconds for this single preparation. This is not a worst-case budget or
  candidate library-load timing measurement.
- One stock sdvcam PID/start time stayed unchanged across 21 samples over
  600.086 seconds. Its executable was `/usr/bin/sdvcam`, fd3 referenced the watchdog,
  and the native executable SHA256 matched at both ends.
- The desktop gateway was connected to `<camera-ip>:8787` at every sample.
  Its identity remained real CammyOS 0.3.0, not a simulator. No controller owned
  a lease; no sensing was started by the gateway.
- Adam confirmed normal live camera view. An earlier black-screen report was
  followed by “On now,” USB enumeration and this successful live-view confirmation;
  no repeated reset was observed during the measured window.
- Selector state/config, candidate mapping and private TF staging were absent.
  `/data/app_md5` remained absent, matching baseline; no backup/core path appeared
  in the bounded metadata checks. This is not a whole-filesystem write audit.

Stock native-app SHA256 throughout:
`8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`.

## Restoration

The already approved guarded restore completed successfully while the native app
was left running. Independent read-back confirmed original S01app SHA256
`3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136`,
4,246 bytes and mode 0755. No native process was signalled or restarted for either
file transaction.

After a second user power cycle, a different boot ID, original init/app SHA256s,
absence of `/run/cammy-select` and normal live LCD were confirmed. Twenty-one
samples over 600.066 seconds then showed the same stock PID/start time and boot
ID, with the gateway connected at every sample. Original init/app SHA256s matched
at both ends. Final checks found no temporary staging/transaction/selector files,
no active controller lease and no active preview. No test process remains running.
Cammy is left on the exact original stock startup with TF runtime 0.3.0.

## What this establishes

This is hardware evidence for one normal boot through the blocked launch wrapper
and an in-band return to the exact stock init file. It verifies early-boot shell
execution, the successful stock-launch branch and that branch's RAM records on
this boot. It does not validate one-shot candidate selection, media ownership,
early TF staging, bridge attach timing, candidate core suppression, effective
watchdog timeout/nowayout or recovery after arbitrary flash corruption.

Normal power-cycle persistence of this init file does not prove power-loss
durability of future selector claims on JFFS2. No flash erase/program operation,
full-image replacement, candidate activation, native code modification or TF
payload update was performed. No candidate/media gate is promoted by this test.

## Reproducible evidence and corrections

Private index: `private/bootstrap-hardware-test/current.json`; exact execution
directory: `private/bootstrap-hardware-test/20261114T223315Z/`.
Its `acceptance.json` separates modified boot, file restoration and restored boot.
Raw boot/process/network samples are private; no credentials or sensor data are
included in this report.

Target inspection found chown/rmdir/df unavailable. The file transaction uses
root-created ownership checks and rm -r only for its own exclusive private lock;
13 local focused tests cover this compatibility profile. The actual install and
restore each returned zero and passed independent on-camera SHA256 read-back.

An earlier documentation claim about stock S01app stop was corrected: this exact
script runs hwclock, sends kill -9 to matching sdvcam processes, then sleeps one
second; it does not invoke poweroff. Stop/restart remains excluded because it
force-kills the watchdog-owning app without a proven handoff.
