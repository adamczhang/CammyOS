# Next hardware test: stock-only bootstrap

Prepared 2026-11-14; the approved modified boot passed and the exact stock file is restored. **Both 600-second boot checks passed; original startup is left installed.**
This test checks the revised launch wrapper with `SELECT_GATE=blocked`. It runs
the unchanged stock application. It cannot select the native status candidate.

## Concrete change

Replace only `/etc/init.d/S01app`, root `0:0`, mode `0755`:

| Role | SHA256 | Bytes |
| --- | --- | ---: |
| Before / rollback | `3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136` | 4,246 |
| After / blocked proposal | `8e79dd90c290d4678d7da08dfea980b67e63440c58b9a51d6b57d6f89770766f` | 40,784 |

The replacement preserves 150 of the 156 stock lines byte-for-byte, routes all five
launch sites through the reviewed helper, and tightens the TF backup condition.
Review the exact diff in the private chunk-6 package. The 11,335,176-byte
`/usr/bin/sdvcam` stays at SHA256
`8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`.

No `/etc/cammy` configuration/state or TF files are installed. The later one-shot
candidate logic stays unreachable. The install/restore transaction briefly creates
the private `/etc/init.d/S01app.cammy-transaction/` directory and its `next` file,
then renames the verified file over S01app and removes the directory. A crash can
leave that directory; a subsequent transaction refuses to reuse it.

The prepared kit contains four inert `0644` files: `S01app.stock`,
`S01app.proposal`, `transaction.review.sh`, and `readiness.json`. The first three
are staged into a root-owned `0700` directory on camera `/tmp` after approval.
The receipt stays on the PC. This RAM staging is separate from the one internal
file replacement. The transaction does not reboot, signal or launch any app.

## Preparation completed

Fresh read-only USB evidence is under `private/bootstrap-readiness/`; the final
kit location and hashes are recorded in `private/bootstrap-readiness/checkpoint.json`.

- Live stock init and native-app SHA256s match the exact saved inputs.
- The camera is root; init file/directory ownership and modes match expectations.
- `/run` is executable tmpfs and one stock `sdvcam` process has fd3 at `/dev/watchdog`.
- Native `/bin/sh -n` returns `0` for `exit 73` and `2` for invalid syntax.
  After these controls, it parses the exact proposal, transaction and rollback
  bytes successfully. Every streamed input is SHA256 checked on the camera before
  parsing. No startup code was executed.
- Boot ID is unchanged across the preflight. No camera files were written during preflight.

Transport findings: stock ADB rejects `shell -T` and `exec-out`; its PTY shell
has a short command limit. Runtime BusyBox has SHA256 but lacks base64/gzip/gunzip,
and `stty` is absent. The preflight streams into a shell variable through `cat`,
preserves trailing newlines with a sentinel, verifies the hash, and pipes to
`/bin/sh -n`. It uses no temporary camera file or heredoc. ANSI colour in `ls`
output is removed on the PC before checking ownership metadata.

Local transaction fixtures cover install/restore, corrupt payload/app refusal,
existing lock and symlink refusal, private directory permissions, and sync failures
on either side of rename. The final test results are in [STATUS](STATUS.md).
Local execution uses synthetic scripts and a relocated filesystem. It does not
prove flash durability or successful boot of the replacement.

## Immediately before an approved test

Keep the current TF runtime inserted and use PC USB data/power. No TF reader work
is needed. Do not arm or copy a native candidate. Confirm normal operation first.
Recheck source/kit hashes and fresh live stock identities; an old passing receipt
is a snapshot, not a continuing connectivity or authorization claim.

Host verification (no device access):

```sh
python3 -m tools.bootstrap_readiness --verify private/bootstrap-readiness/FINAL_KIT
```

Replace `FINAL_KIT` with the checkpoint's exact directory. This CLI has no install,
restore, reboot or enable action. Optional `--adb` with `--out-dir` collects only
the named read-only preflight. Use the existing ADB transport, port 5038 and serial
20080411. This host's downloaded ADB needs its build-deps Android library path.

Before staging, review the exact [transaction script](../native/activation/stock_bootstrap_transaction.review.sh).
Stage only the three reviewed files into `/tmp/cammy-stock-bootstrap`, root
`0:0`, directory `0700`, files `0644`, after verifying `/tmp` is tmpfs. Refuse an
existing/symlink staging directory rather than overwriting it. Verify all three
SHA256s against the PC receipt after transfer. Parse the staged transaction with
`/bin/sh -n` before executing it. Save stock init/app hashes and boot ID privately.

After exact internal-write approval, the proposed device command is:

```sh
/bin/sh /tmp/cammy-stock-bootstrap/transaction.review.sh install /tmp/cammy-stock-bootstrap
```

It checks root identity, private staging, original target type/mode/owner, both
payloads and stock app MD5/size; creates its exclusive private transaction directory;
copies/parses/checks the proposal; sets `0:0`/`0755`; syncs; rechecks the original;
renames on the same filesystem; syncs and verifies installed bytes/mode. The PC
then independently reads the SHA256 of the installed init and unchanged app.
MD5/size in the script are mismatch guards, not authentication. Root init/operator
is assumed to be the sole writer. A failed check means stop, preserve diagnostics,
and inspect; do not automatically retry, start an app, or reboot.

## Boot check and acceptance

After successful installation/read-back, ask Adam for one normal power cycle.
Leave TF and PC USB connected. Choose Charging Mode if prompted; leave Wi-Fi
controls untouched. Record the new boot ID as soon as USB returns.

Read-only observations at startup, approximately 90 seconds, and through a
600-second stability window:

1. New boot ID, then no additional boot-ID changes or spontaneous shutdown.
2. S01app has the proposal SHA256; stock app SHA256 remains unchanged.
3. `/run/cammy-select/status`: `decision=stock`, `mode=absent` and
   `reason=not-enabled` when no config exists. If config already exists, record its
   classification; blocked selection still requires `decision=stock`.
4. `launch.stamp`: `target=stock`, `app=/usr/bin/sdvcam`, correct MD5/size and
   current boot ID. These records alone prove only preparation/exec attempt.
5. Independent `/proc` evidence: exactly one `sdvcam` PID, executable path
   `/usr/bin/sdvcam`, same PID/start time throughout the window, fd3 watchdog.
6. Adam confirms normal live LCD. Home Wi-Fi and existing authenticated gateway
   diagnostics recover; record any missed connection without assuming media works.
7. No private TF staging mount, candidate/library mapping, selector flash record,
   unexpected internal backup or core dump. Compare `/data/app_md5` and normal
   backup metadata to the pre-test baseline; record legitimate stock TF backups
   separately from writes into an unmounted internal `/mnt/extsd` directory.

Read known files with the legacy `adb shell` transport; do not open the watchdog,
device nodes, audio/video pipelines or debug FIFO. No media or movement test is
part of this acceptance. Stop at any reset, unexpected PID, hash drift, refusal,
failed staging detach, or unexpected durable write. Never call S01app stop/restart
or kill sdvcam; stock stop sends kill -9, and watchdog close semantics are unproven.

## Restore before closing this test

While USB is reachable and the running app is stable, verify/stage the same kit
again if a cold boot cleared `/tmp`. With the already approved restore scope:

```sh
/bin/sh /tmp/cammy-stock-bootstrap/transaction.review.sh restore /tmp/cammy-stock-bootstrap
```

The transaction accepts only the exact known proposal as the current file. It
restores exact stock ownership/mode/bytes, without touching the running app.
Independently verify stock SHA256, ask for a second normal power cycle, and confirm
new boot ID, one unchanged stock app/watchdog owner, normal LCD/Wi-Fi and a
600-second stable boot. `/run/cammy-select` should be absent after this stock boot.
Keep baseline, modified-boot and restored-boot reports separate. Restore stock even
after a successful wrapper boot; retaining the wrapper is a later decision.

If a transaction reports a post-rename failure, the target may already contain
the complete proposal: read its hash and do not reboot blindly. Unknown target
bytes, a stale lock, or a corrupt payload require inspection, not forced retries.

## Recovery boundary and later gates

USB rollback depends on the camera booting far enough to expose ADB. Removing TF
does not repair broken internal init. FEL enumeration was seen earlier, but SPI
NOR dump/restore is not verified. This test therefore requires Adam's approval of
the exact one-file change and this recovery limitation under existing gate E2.

This stock-only test does not need or close candidate power-loss durability,
early TF copy timing, candidate core-limit or media-lease gates. It leaves EA,
candidate activation and all physical capabilities unchanged. A stable wrapper
boot plus verified stock restoration supplies evidence for the next decision;
it is not a full OS, media or daily reliability acceptance.

## Initial installation evidence — 2026-11-14

Adam approved the exact stock-only test and restoration. Fresh preflight passed.
The first RAM-directory preparation stopped because native `chown` is absent;
inspection also found no `rmdir` or `df` (including runtime BusyBox df). No internal
file had changed at that point. The transaction now verifies root-created file
ownership rather than calling chown, and cleans only its own private lock with
available rm -r. Preflight checks every required command explicitly. Thirteen
focused tests passed (1.278s), including fixtures that make chown/rmdir fail.
The S01app payload itself is unchanged from the reviewed SHA256 above.

The refreshed transaction and all three staged files passed exact SHA256 and
target-shell syntax checks. The guarded installation returned zero and independently
read-back S01app is `8e79dd90...`; stock app SHA256 and the pre-test boot ID remain
unchanged. No app was signalled/restarted or candidate enabled. The modified cold boot subsequently passed its 600-second observation, with
normal live LCD confirmed. The exact stock file was restored and a second stock
boot passed its own 600-second stability window with correct hashes, live view
and home connection. Original stock startup remains installed; temporary files
and controller leases are absent.
See the [hardware report](BOOTSTRAP-HARDWARE-2026-11-14.md).
Evidence: `private/bootstrap-hardware-test/current.json` and its execution directory.
Do not interpret installation success as boot or recovery acceptance.
