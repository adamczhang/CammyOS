# WP4 activation and rollback — 2026-11-14

**Local only.** Everything here was built and tested against fake trees, fake
process tables and a relocated copy of the selector script run under `dash`. No
camera, card, ADB, device node, signal, mount of a real card, internal init
write or vendor executable was used. Nothing is installed or authorized to be.
The real native stop/exec/install backend does not exist.

> **WP5 review (2026-11-14) supersedes parts of this report.** See
> `docs/work-packages/WP5-ACCEPTANCE.md`. In summary:
> - **Activation is blocked (gate EA).** The proposal ships
>   `SELECT_GATE=blocked`, and select mode is refused before payload/card/claim/mount actions.
>   RAM scratch/self-test and diagnostic status writes may still occur. The
>   bind overlay deviates from `GUARDED-TF-LAUNCH-PLAN.md:31-33`, and two
>   fault paths are not fail-closed under stock S01app: `tf-detach-failed`
>   triggers its backup branch, and `bind-unrestorable-executable` can run
>   unverified bytes. "Decision" in §1 is now a design proposal, not a
>   selected route.
> - **The library path is SHA256-scoped:**
>   `/tmp/cammy-native-status/<library-SHA256>/libcammy_native_status.so`
>   (arm `cammy-native-arm/2`, allowlist `cammy-native-allow/2`). The
>   `<lib-md5>`/hyphen path below is obsolete. On the camera, MD5 + size is a
>   weak mismatch check only.
> - **Added:** a primitive self-test, top-mount noexec checks, a
>   late-invocation guard and the exec-bit commit.
> - **Classification is structural only.** Eligibility needs a SHA256-bound
>   review record (`tools/native_trial/review.py`).
> - **Sync + read-back** proves the page cache, not flash durability.
> - `tests.test_native_trial` now has 94 tests.

## 1. Decision: early one-shot selector, late handoff stays ineligible

| Question | Early selector (proposed) | Late handoff (current plan, ineligible) |
| --- | --- | --- |
| Who starts the app | Stock `S01app`, unchanged, once, with its own power/MMIO setup and `&` launch | A supervisor after boot settles, which must first stop the running app |
| Watchdog | The selected app opens and feeds it exactly as stock does. No transfer between apps | Needs proven native disable/close/nowayout semantics (gate L2). The 120 s ceiling gives no handoff allowance |
| Stop of stock app | None | Needs a cooperative process-only shutdown (L1). SIGTERM goes to the glog crash path, SIGINT is ignored, and `S01app stop` is `kill -9` |
| Continuity of radio, USB and TF | Not exercised. The native app starts as at a normal boot | Native teardown is broad (L3) |
| Restore | The next boot is stock by construction (RAM bind mount) and by internal state | Needs one warm restore with a proven launch profile (L4) |
| Install cost | One internal-overlay change: 3 files. Needs approval (E2) | None internal, but blocked on L1–L5 |
| Failure while running | Watchdog reset or power cycle, then a stock boot that records the observed boot | Needs a recovery-required state with no second app |

**Selected route: the early selector, as a proposal.** It is implemented as an
inert script and a matching Python model, and both are fault-tested. It removes
the warm-transfer problem and does not touch the vendor shutdown paths. It does
not remove the risk of bad native startup, radio effects or a crash. Those are
bounded by one attempt, a stock next boot, and gates E4–E7. The late handoff
keeps a fail-closed state machine (§6) so its failure policy stays testable.
It refuses at preflight until L1–L5 close.

### Selecting an app is not authorizing media

Early selection decides **which binary `S01app` runs this boot**. It grants no
authority:

- The TF arm, internal allowlist and claim records hold only hashes, sizes, a
  class, an attempt id and boot ids. The strict parsers reject any extra key, such
  as `lease=…`, so a lease, token, nonce or media request cannot be smuggled in.
- Only class `status-only` can be selected: hello, status and capabilities, with
  every physical capability unavailable. `media-lease-gated` is a known class
  but stays unselectable. It needs gate E8: the bridge enforces a fresh
  **current-boot** lease and generation from the portal before any media call.
- The current offline candidate is **`media-connect-triggered`**. Its patched
  9999 connect/disconnect sites start media without a lease, so it can never be
  allowlisted by the tool or the selector. The classifier detects those sites
  from bytes, whatever class is declared. Run read-only against the private
  candidate (SHA256 `cfee212a…`), it reports both sites changed and `eligible: false`.
- Portal leases live in RAM, so they are inherently per-boot. A boot-time arm
  cannot satisfy them. Selection therefore never weakens the media rule: actual
  media still needs a lease issued in the selected boot. Portal capabilities stay
  unavailable unless the bridge reports otherwise (WP2/WP3).

## 2. Boot facts (static, from the extracted stock rootfs)

The extracted image's `sdvcam` SHA256 `8771961f…` equals the live read-only hash.
The image's `S01app` (SHA256 `3327c3a9…`, MD5 `daeb505e…`) matches the saved live
text, ignoring trailing whitespace. Live bytes are gate E3.

- **Order.** `inittab` runs `rcS boot`. `rcS` mounts UDISK, runs `mdev -s`, then
  checks charge mode (it loops forever in charge mode, before any `S??`). It runs
  `load_script.conf` and then `rc.final`, which runs `/etc/init.d/S??* start` in
  glob order. That order is `S00cammy-power`, **`S00cammy-select`**, `S00mpp`
  (insmod MPP/sensor modules), `S01app`, …, `S10udev`, …, `S99cammy-tf`.
- **TF availability is not implied by S01-before-S99.** The `mdev` hotplug script
  in the image is a no-op (`exit 0`). The udev TF hook mounts `mmcblk0p1` on
  `/mnt/extsd` only after `S10udev`, which runs after `S01app`. The vfat mount in
  `rc.final` is commented out. `S99cammy-tf` has its own wait/mount fallback.
  Whether the card is mounted at S00 time is therefore unproven. **The selector
  establishes it explicitly:**
  1. Any vfat mount present at S00 is unexpected, and the boot stays stock.
  2. Otherwise the selector probes `/dev/mmcblk0p1`, then `/dev/mmcblk0`.
  3. It mounts the card `ro,nosuid,nodev,noexec` at a private `/run` point.
  4. It detaches the card before `S01app`, falling back to `umount -l`.
  5. No device, mount, arm, candidate or library means an ordinary stock boot.
- **Why the card must be detached before `S01app`.** `S01app`'s backup branch
  keys on *any* vfat mount. With a bind-mounted candidate, that branch could copy
  the candidate to `.sdvcam` and write `/data/app_md5`. If the card cannot be
  detached, the result is `recovery-required` and nothing is selected.
- `/run` is tmpfs per `inittab`. In the image, **`/tmp` is a plain rootfs
  directory**: `fstab` only has devpts, and `/var` is a symlink to `/tmp`. Whether
  `/tmp` is tmpfs live is unknown (E3). The selector refuses
  (`lib-dir-not-tmpfs`) unless `/proc/mounts` shows tmpfs at exactly `/tmp`. If
  live `/tmp` turns out not to be tmpfs, WP1/WP2 should move the library root
  under `/run`.
- Stock busybox provides `md5sum`, `sync`, `tail`, `awk`, `sed`, `cut`, `rmdir`,
  `mount` (the `bind` option string is present) and `umount`. It has **no
  `sha256sum` and no `timeout`**, so on-camera integrity is MD5 plus size against
  an internally installed allowlist. SHA256 is checked on the host.
- The stock text LOAD maps file offset 0 to VA 0x10000. WP1's status-only scope
  in file offsets is:
  - bl at 0x21DB0 (`0xeb01c937` → 0xA4294)
  - zero cave 0x8D2E1C–0x8D3030
  - LOAD filesz/memsz at 0xA4–0xAB

  All three were verified on the stock ELF.

## 3. Mechanism (`native/activation/S00cammy-select.proposal`)

The bootstrap is never installed by a TF update and has no installer. Its
inputs:

- **Internal, set at install (E2).** `/etc/cammy/native-select.enabled`, whose
  content must be exactly `mode=observe` or `mode=select`; anything else means
  disabled, and a missing file exits silently with zero side effects.
  `/etc/cammy/native-select.allow` holds exactly one reviewed candidate and
  library, with the stock `S01app` and `sdvcam` identities.
- **TF card.** `CammyOS-native/arm`, `candidate.bin` and
  `libcammy-native-status.so`, plus `ack-<attempt>` for each prior attempt.

The checks run in order; any failure means stock, with one reason code:

1. The mode is valid.
2. `/run` is tmpfs.
3. The boot id is valid.
4. The internal state contains only `claimed-<id>` and `observed-<id>`.
5. A claim from this boot is a no-op. Any claim without an `observed` file makes
   this the **post-attempt boot**: the selector records the observed boot and
   stays stock.
6. The attempt limit is 4.
7. The allowlist is valid, and its class is `status-only` or `media-lease-gated`.
8. The `S01app` MD5 matches.
9. `/usr/bin/sdvcam` is not already a mount point.
10. The stock size and MD5 match.
11. `/tmp` is tmpfs, and the hash-scoped library directory is absent.
12. There is no vfat mount, and a private read-only card mount succeeds.
13. The arm is strict and its attempt is not already claimed.
14. Every earlier claim has a TF ack that quotes its observed boot id.
15. The arm equals the allowlist, and the class is `status-only`.
16. The candidate and library bytes on the card match.
17. MemAvailable is at least 3× the payload plus 1 MiB.

The commit steps follow:

1. Copy the candidate to `/run/cammy-select/sdvcam` and the library to
   `/tmp/cammy-native-status/<lib-sha256>/libcammy_native_status.so` (WP5: the
   directory is the allowlisted library SHA256), verifying both
   copies.
2. Detach the card.
3. In `observe` mode, stop here: report `observe-only` and remove the copies.
4. **Claim**: exclusive-create `claimed-<attempt>` on the internal overlay, `sync`,
   and require an exact read-back. Any failure means stock, and the attempt is
   consumed.
5. `chmod`, then bind-mount the copy over `/usr/bin/sdvcam`.
6. Verify the candidate and library hashes through the mount. On a mismatch,
   unmount and re-verify stock (`bind-verify-failed`). If stock cannot be
   restored, the result is `recovery-required` (`bind-unrestorable`).

The selector writes `/run/cammy-select/status`: mode, decision, reason, attempt,
boot and uptime timings. The exit status is always 0.

Records are tiny `key=value` files with fixed keys in fixed order, at most 512
bytes, newline-terminated. Their formats are in `tools/native_trial/records.py`.
The library path is fixed at build time and scoped by hash. No path comes from
IPC or the card. The classifier requires the candidate to embed exactly
`/tmp/cammy-native-status/<lib-sha256>/libcammy_native_status.so` (WP5 alignment
with the manager/WP2A contract; WP2A builds to it).

**Rollback and disarm.** To disarm, delete `native-select.enabled`; the hook then
exits silently. To uninstall, delete the three files and
`/etc/cammy/native-select/`. To stop an armed card, remove the card or its
`arm`. Any boot without a valid arm, card or library is stock.

## 4. Tooling (`tools/native_trial/`, stdlib only)

The tool is run with `python3 tools/native_trial <command>`. It writes only under
`build/native-activation/` or the temp directory. There is **no** install,
select, stop, launch or card-write command; the tests assert that such names are
rejected.

| Command | Purpose |
| --- | --- |
| `status` (default), `gates` | Backends unavailable; decision; open gates E1–E8, L1–L5 |
| `classify` | Effective class from bytes: patch scope, bl site, connect sites, embedded library path, library ELF/ARM/EABI5-hf header. Exits 3 if ineligible |
| `make-bundle` | Stages `tf/CammyOS-native/{arm,candidate.bin,lib}`, `internal-proposal/native-select.allow` and the manifest. Refuses ineligible candidates and non-staging destinations |
| `validate-bundle` | Strict file set, symlinks, arm/allowlist/manifest/hash consistency, optional reclassification |
| `make-ack` | Ack record for a reviewed prior attempt and its observed boot |
| `simulate` | FAKE three-boot selector run |
| `proposal-patch` | Inert camera-root patch, with a placeholder allowlist unless a validated bundle is given |

The modules are:

- `selector`: the reference model of the shell.
- `faketree`: fake root, emulated mount/umount/sync/cp, and power-loss reboot.
- `handoff`: the gated late-handoff machine, plus `verify_selected_boot`.
- `fakeproc`: fake processes, clock, control and attempt store.
- `bundle`, `records` and `policy`.

## 5. Verification performed (local; fake/relocated-shell/static only)

`python3 -m unittest tests.test_native_trial` ran **75 tests, all passing** at WP4
hand-off (WP5 revision: 94, see WP5-ACCEPTANCE.md §9). The
full `unittest discover` run was 173 tests, all passing, without running `make native`.

- **Shell and model equivalence (44).** The proposal is relocated by rewriting
  only its path header; a test asserts that no other system path exists. It runs
  under `dash` with PATH stubs. The Python model runs on an identical tree. The
  stdout line, state files, status, RAM files, app hash, mount table and
  primitive call log must be equal. Cases cover:
  - every reason code
  - replay, ack and observed-boot binding, and the attempt limit
  - observe mode
  - the classes
  - premounted vfat, missing device and whole-disk fallback
  - malformed arm (including an injected `lease=` key)
  - candidate and library mismatch or absence
  - `/tmp` not tmpfs, an existing or symlinked library directory, low memory
  - copy failure and corruption, lazy-detach fallback and detach failure
  - claim refusal and partial claim
  - bind fail, bind mismatch and unrestorable bind
  - a SIGKILL power loss at the claim `sync`, with the unsynced claim lost, kept
    or truncated

  `busybox sh -n` parses the proposal. The host busybox prefers applets over PATH
  stubs, so ash itself was not executed.
- **Power-loss sweep (2).** Power loss was injected at every primitive of every boot
  of a five-boot script (arm A1, post-boot, ack plus A2, …), with unsynced writes
  lost, kept or truncated. Invariants: never selected without a synced claim;
  each attempt selected at most once; no two consecutive candidate boots; stock
  app and no card mount whenever not selected.
- **Bundle.** Synthetic 9 MB images with stock constants patched in-test cover:
  - eligibility, staging, validation and tamper detection
  - the destination guard
  - out-of-scope bytes, an unchanged bl site, and wrong or duplicate embedded paths
  - non-ARM, soft-float and non-DYN libraries
  - the connect-site override
  - a bundle driving the fake selector end to end

  The real private candidate was classified read-only, with the outcome described
  in §1.
- **Handoff.** The default contract and control refuse before any claim or call.
  Further cases cover:
  - budgets
  - stale boot, nonce, attempt, lease and candidate binding
  - claim write failure, partial write and read-back failure (attempt consumed,
    stock untouched)
  - a power loss leaving the next boot disarmed
  - competing apps and resource owners, an unexpected executable
  - stock stop timeout, respawn, resource leak and missing release ack (never a
    second app)
  - PID reuse not mistaken for stock
  - candidate crash, not-ready or wrong identity (one restore)
  - a stuck candidate (no restore)
  - restore failure (single attempt)
  - abort and overall deadline
  - selected-boot verification

**Not established by any of this:** real busybox/ash behavior, bind-mount
semantics on the camera, TF timing, the effect of selector duration on boot or a
pre-armed watchdog, native startup with the status-only build, watchdog
stability, or stock restore. Those are hardware results.

## 6. Remaining gates and the precise next hardware gate

Open gates for the early selector (WP5: plus **EA, blocked**; E3 is now
`partial`; E1 additionally needs a SHA256-bound review record):

- **E1.** A status-only build from pristine stock, built to the hash-scoped
  library path, passes `classify`.
- **E2.** Approval of the 3-file internal change.
- **E3.** Read-only live facts:
  - `/etc/init.d` listing
  - `S01app` hash
  - `/proc/mounts`, including whether `/run` and `/tmp` are tmpfs
  - busybox applets and options
  - `/proc/uptime`
  - MemAvailable
  - card device naming
- **E4.** Observe-mode boot.
- **E5.** Selected status-only boot acceptance.
- **E6.** Stock restore and card-removed boot.
- **E7.** Policy for core dumps (`S01app`: `ulimit -c unlimited`, cores on the card).
- **E8.** Bridge lease enforcement; blocked.

Late handoff: L1–L5 remain open.

**Next hardware-facing gate: E3.** It is a bounded read-only reference that needs
no code on the camera; it was not performed here. **Operator steps are
deliberately not written yet.** The bundle is not concrete because no status-only
build exists (E1). After E1, `make-bundle` and `validate-bundle` produce the exact
files, and `proposal-patch --bundle` produces the exact internal change for E2
review. The runbook follows from those.

## 7. Residual risks

- On the camera, MD5 is the only integrity check. Collision resistance relies on
  the reviewed allowlist, not the card.
- `bind-unrestorable` cannot stop stock `S01app` from running whatever the mount
  then shows. It is recorded, not prevented.
- Selector time adds to boot before the app opens the watchdog. A watchdog
  pre-armed by the bootloader is unproven, so E4 measures it.
- A candidate crash produces an unlimited core dump on the card (inherited stock
  behavior).
- The `observed` and `claimed` files add one JFFS2 write per selected boot and
  per post-attempt boot. A malformed state file blocks all further selection
  until maintenance removes the state directory.

## 8. Artifacts

- Proposal: `native/activation/S00cammy-select.proposal`, no exec bits, never
  executable in the repository; ships `SELECT_GATE=blocked` (WP5).
- Generated, gitignored: `build/native-activation/wp5-blocked-bootstrap-proposal.patch`
  (WP5; the pre-review `bootstrap-proposal.patch` was removed as stale).
  `proposal-patch` refuses every `--bundle` while activation is blocked. Its
  allowlist is a placeholder, so if installed by mistake the selector reports
  `allowlist-invalid` and the boot stays stock (tested). Regenerate it with
  `python3 tools/native_trial proposal-patch --out <new file>`.
- Private: `private/ownership-program/wp4/` holds the checkpoint and the
  real-candidate classification JSON.
