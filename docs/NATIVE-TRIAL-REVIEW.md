# Native Trial Review & Rollback Runbook — 2026-11-14

**Status: DISARMED OFFLINE REVIEW PACKAGE ONLY (`LOCAL_ONLY/BLOCKED_FOR_ACTIVATION`).**
- `review_only: true` | `activation_eligible: false` | `installation_authorized: false` | `deployed: false`
- Shipped selector gate: `SELECT_GATE=blocked`.
- **There is no currently approved live activation path.** This package is not an installer and cannot start the status-only candidate on hardware. This package provides no command to apply files to a camera, TF card, or system directory; the current task authorizes local preparation and review only.

See [Chunk 3 contract](work-packages/CHUNK3-BOOT-LAUNCH-CONTRACT.md), [Chunk 5 selection fixtures](work-packages/CHUNK5-ONE-SHOT-SELECTION.md), [Chunk 5 independent review](work-packages/CHUNK5-INDEPENDENT-REVIEW.md), [camera compatibility inventory](CAMERA-COMPATIBILITY-2026-11-14.md), and [loader proof](native-bridge/LOADER-PROOF.md).

---

## 1. Pinned Artifact Identities

All package members are staged on the host as inert mode `0644` files (no execute bits, no armed `arm` file in `tf-staging/`, and no camera root ownership claimed on the host).

| Artifact / Role | Package Member | SHA256 | MD5 | Size (B) | Lines |
| --- | --- | --- | --- | ---: | ---: |
| Stock `/etc/init.d/S01app` (build input & exact rollback copy) | `rollback/S01app.stock` | `3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136` | `daeb505e811834088f64cb07696c5cbe` | 4,246 | 156 |
| Helper proposal (`SELECT_GATE=blocked`) | `provenance/launch_selected_or_stock.proposal` | `c20efedcd528fd8fd6e84e55fbfaa880fe7b2f0a4d247a09205d8357d105e8d9` | `a372c0cdf596bc64e733ce9c07dff6b4` | 36,343 | 1,221 |
| Generated replacement `/etc/init.d/S01app` (`SELECT_GATE=blocked`) | `internal-bootstrap/S01app.proposal` | `8e79dd90c290d4678d7da08dfea980b67e63440c58b9a51d6b57d6f89770766f` | `502b58d52476d863aa58058c7606b70b` | 40,784 | 1,380 |
| Runtime stock `/usr/bin/sdvcam` (verified before any fallback launch; never replaced) | *(on camera / saved rootfs)* | `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec` | `8689ce53c4674563a7a6935f97c0d727` | 11,335,176 | — |
| Status-only candidate (`sdvcam-status-db34fa035a26`, `status-only`) | `tf-staging/CammyOS-native/candidate.bin` | `a9dedc985cc79d933601390df36b4458784e8dd4807b867cd53cfe0a8a9caba0` | `ef95bc8c0f9004cf54aa338ddfa7ad61` | 11,335,176 | — |
| Status-only bridge library (`libcammy_native_status.so`) | `tf-staging/CammyOS-native/libcammy_native_status.so` | `db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065` | `9249d39eb3755c4598ec15f10586ddfd` | 21,456 | — |

- **Allowlist binding without hash cycle:** `internal-bootstrap/native-select.allow` (`cammy-native-allow/2`) is derived *after* generating `S01app.proposal`. Its `s01app_md5` field is bound to the generated replacement digest (`502b58d52476d863aa58058c7606b70b`), **not** the pristine stock `S01app` digest (`daeb505e811834088f64cb07696c5cbe`).
- **On-camera digest boundary:** On-camera `md5sum` + byte size checks are weak mismatch checks for accidental corruption or wrong files, not cryptographic authentication. Full SHA256 identities are pinned and verified on the host.

---

## 2. Proposed Target Mapping & Directory Preconditions (Review Only)

Host package files are owned by the local build user with mode `0644`. They do **not** carry camera `root:root` (`0:0`) ownership or execute permissions on the host. Any future installation requires Adam’s approval of the exact internal change (Gate E2). Kernel and raw flash image replacement are out of scope.

### 2.1 Internal Writable Overlay (`JFFS2` via `/overlay` -> `/etc`)

| Camera Path | Source in Package | Intended UID:GID | Intended Mode | Role & Preconditions |
| --- | --- | --- | --- | --- |
| `/etc/init.d` | *(existing directory)* | `0:0` | `0755` | Existing init directory precondition (non-symlink). |
| `/etc/init.d/S01app` | `internal-bootstrap/S01app.proposal` | `0:0` | `0755` | Reviewed inactive replacement script (`SELECT_GATE=blocked`). Exact rollback source: `rollback/S01app.stock`. |
| `/etc/cammy` | *(directory precondition)* | `0:0` | `0755` | Parent configuration directory on internal overlay (non-symlink). |
| `/etc/cammy/native-select.allow` | `internal-bootstrap/native-select.allow` | `0:0` | `0600` | Strict 6-key allowlist (`cammy-native-allow/2`) binding `s01app_md5=502b58d52476d863aa58058c7606b70b`, stock `sdvcam`, status-only candidate, and library. |
| `/etc/cammy/native-select.enabled` | `internal-bootstrap/native-select.enabled.disabled-template` | `0:0` | `0600` | Packaged as `mode=disabled\n`. When absent or `mode=disabled`, skips TF probing and flash state writes. |
| `/etc/cammy/native-select` | *(directory precondition)* | `0:0` | `0700` | Private non-symlink state directory (`cammy_dir_private_ok` requires `drwx------` `0:0`). Holds intentional flash records (`inflight`, `claimed-<attempt>`, `observed-<attempt>`). |

### 2.2 TF Card (`vfat`, probed read-only at `/run/cammy-select/tf`)

| Camera / Card Path | Source in Package | Intended Mode | Status in Package |
| --- | --- | --- | --- |
| `CammyOS-native/candidate.bin` | `tf-staging/CammyOS-native/candidate.bin` | `0644` (vfat) | Staged in package as inert `0644` file. |
| `CammyOS-native/libcammy_native_status.so` | `tf-staging/CammyOS-native/libcammy_native_status.so` | `0644` (vfat) | Staged in package as inert `0644` file. |
| `CammyOS-native/arm` | `templates/arm.template` | `0644` (vfat) | **NOT staged in `tf-staging/`.** Non-consumed review template only (`cammy-native-arm/2`). |
| `CammyOS-native/ack-<attempt>` | `templates/ack.template` | `0644` (vfat) | **NOT staged in `tf-staging/`.** Non-consumed review template only (`cammy-native-ack/1`); requires a real post-attempt `observed_boot` UUID. |

### 2.3 Runtime RAM Targets (`tmpfs` on `/run` and `/tmp`, without `noexec`)

Created at boot by root init (`0:0`) only; never pre-staged on flash and never mounted over `/usr/bin/sdvcam`:

- `/run/cammy-select` (`0700`, `0:0`, non-symlink directory on `/run` `tmpfs`)
- `/run/cammy-select/tf` (`0700`, `0:0`, temporary private mount point for `mount -t vfat -o ro,nosuid,nodev,noexec`; detached and `rmdir`ed before claim/launch)
- `/run/cammy-select/sdvcam` (`0500`, `0:0`, staged candidate binary in RAM; cleaned on any preparation failure or `mode=observe`)
- `/run/cammy-select/status` (`0600`, `0:0`, immutable **PREPARATION-ONLY** record `cammy-boot-launch-status/1`)
- `/run/cammy-select/launch.stamp` (`0600`, `0:0`, exclusive per-boot **EXEC-ATTEMPT-ONLY** record `cammy-boot-launch-stamp/1`)
- `/tmp/cammy-native-status` (`0700`, `0:0`, non-symlink parent directory on `/tmp` `tmpfs`)
- `/tmp/cammy-native-status/db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065` (`0700`, `0:0`, full-SHA256 directory)
- `/tmp/cammy-native-status/db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065/libcammy_native_status.so` (`0444`, `0:0`, exact underscore path baked into candidate stub at `0x8e2f04`)
- `/tmp/cammy-bridge` (`0700`, `0:0`, non-symlink private socket directory on `/tmp` `tmpfs`)

---

## 3. Two-Stage Approval Distinction & Critical Frozen Semantics

### 3.1 Stage A vs Stage B (Do Not Conflate)

1. **Stage A — Separately Approved Stock-Fallback-Only Bootstrap Check (`SELECT_GATE=blocked`):**
   - If Adam separately approves the exact internal change under Gate E2 for an initial bootstrap check using `internal-bootstrap/S01app.proposal`, the script remains pinned to `SELECT_GATE=blocked`.
   - Under `SELECT_GATE=blocked`, the helper **never** calls `cammy_prepare_one_shot`, never probes or mounts the TF card, and never writes `/etc/cammy/native-select`. It verifies runtime stock `/usr/bin/sdvcam` (`md5=8689ce53c4674563a7a6935f97c0d727`, `size=11335176`), enforces the tightened `/mnt/extsd` vfat backup gate, writes `/run/cammy-select/status` (`decision=stock`) and `/run/cammy-select/launch.stamp` (`target=stock`), and launches stock `/usr/bin/sdvcam` once.
2. **Stage B — Future Selected Status-Only Trial (Currently Blocked; No Approved Live Path):**
   - **This package cannot start the candidate on hardware.**
   - `SELECT_GATE=chunk5-fixture-open` exists solely for synthetic host test relocation (`activation_eligible=false`). **NEVER** set `SELECT_GATE=chunk5-fixture-open` on a camera, **NEVER** edit or bypass `SELECT_GATE=blocked` in `S01app.proposal`, and **NEVER** emit an enabled helper from unapproved changes.
   - Any future live status-only trial requires closing open gates (`EA`, `E1`, `E2`, `E3`, `E4`, `E6`, `E7`) and reviewing/committing a separate live gate authorization.

### 3.2 Mandatory Semantics for Reviewers and Operators

- **`/run/cammy-select/status` is PREPARATION ONLY:** Written once at the end of `cammy_prepare_launch`. If a late pre-exec check in `cammy_launch_selected_or_stock` downgrades `candidate` to `stock` or `recovery-required` (e.g., `candidate-stage-invalid`, `core-limit-failed`, `launch-stamp-failed`), `status` remains immutable (`decision=candidate`) while `launch.stamp` records `target=stock` (or no stamp on refusal).
- **`/run/cammy-select/launch.stamp` is EXEC ATTEMPT ONLY:** Created immediately before `(cammy_disable_core_zero && exec "$RUNDIR/sdvcam") &` or `"/usr/bin/sdvcam" &`. Neither `status` nor `launch.stamp` proves that `sdvcam` is running or that the status bridge is ready.
- **Unverifiable stock means NO LAUNCH:** If `/usr/bin/sdvcam` fails the MD5 (`8689ce53c4674563a7a6935f97c0d727`) or size (`11335176`) check at boot or during fallback, the helper sets `decision=recovery-required` (`reason=stock-source-mismatch`) and launches **nothing**. Mismatched bytes are never executed as a fallback.
- **No second app or warm retry:** Once a candidate exec attempt occurs (`launch.stamp` written), no second `sdvcam` instance is ever started in that boot, regardless of exit status or crash. Recovery from a failed candidate startup is a cold reboot into verified stock.
- **Intentional vs unexpected durable flash writes:** Records inside `/etc/cammy/native-select/` (`inflight`, `claimed-<attempt>`, `observed-<attempt>`) are intentional flash writes required by the one-shot state machine. Unexpected durable side effects—such as a core dump landing in the internal overlay `/mnt/extsd/core.*` before `S10udev` mounts the card, unexpected `/data/app_md5` changes, or backup copies into an unmounted `/mnt/extsd/.sdvcam` overlay directory—are acceptance failures (`durable_side_effects != []`).

---

## 4. Open Prerequisites & Hardware Gates

Recent read-only USB compatibility checks (`docs/CAMERA-COMPATIBILITY-2026-11-14.md`, sampled at ~293s uptime) confirmed late-boot file hashes, `/run` and `/tmp` `tmpfs` without `noexec`, and BusyBox applet syntax parsing. **Late-boot metadata does not close early-boot or persistence gates, and no current camera connectivity is assumed.**

| Gate | Status | Unresolved Requirement Before Any Live Candidate Trial |
| --- | --- | --- |
| **EA** | `blocked` | Approved live activation gate design and reviewed replacement for `SELECT_GATE=blocked`. |
| **E1** | `open` | Manager closure of WP2A/WP5 status-only candidate & ABI review record (currently `evidence_class: "offline"`, `LOCAL_ONLY`). |
| **E2** | `open` | Explicit authorization from Adam for the exact internal `/etc/init.d/S01app` and `/etc/cammy/*` writes after rollback readiness is accepted. |
| **E3** | `partial` | S01-time early-boot environment: `/dev/mmcblk0` node availability before `S10udev`, free RAM at S01 time, and real `set -C` exclusive file creation on `/etc/cammy/native-select` (JFFS2 overlay) and `/run/cammy-select` (`tmpfs`). |
| **E4** | `open` | Early-boot TF mount + 11.3 MB copy + MD5 verification duration vs `/dev/watchdog` effective timeout and `nowayout` behavior (sysfs watchdog attributes were unreadable in late-boot inventory). |
| **E5** | `open` | Selected-boot hardware acceptance observations (single candidate PID, library mapping, 600s watchdog stability, LCD/Wi-Fi/portal normal, bridge status-only with all physical capabilities unavailable). |
| **E6** | `open` | Physical power-loss durability of `sync` on JFFS2/overlay flash (`cammy_write_record` read-back only proves page-cache presence) and verified post-attempt + card-removed stock restoration. |
| **E7** | `open` | On-camera verification that `(ulimit -c 0 && exec ...)` under stock BusyBox `/bin/sh` sets soft/hard core limit to `0` for the candidate process before `/mnt/extsd` is mounted. |
| **E8 / L1–L5** | `blocked` / `open` | Media-lease-gated execution (`E8`) and cooperative late process handoff (`L1–L5`) remain out of scope and blocked. |

---

## 5. Verification Checklist: Host vs Later Operator Camera Observations

### 5.1 Rootless Host Validation (Executable Now, Offline)

Run the package builder/validator and focused tests on the host (never touches a camera or TF card):

```sh
PYTHONPATH=.:tools python3 -m tools.trial_package validate <package-dir>
PYTHONPATH=.:tools python3 -m unittest -v tests.test_trial_package
```

Host validation confirms:
- Exact member whitelist (14 files), mode `0644`, no execute bits (`0111`), `st_nlink == 1`, no symlinks or path traversal.
- `S01app.proposal` matches `generate_proposal()` output from stock `S01app` + `launch_selected_or_stock.proposal` (`SELECT_GATE=blocked`).
- `native-select.allow` binds replacement `s01app_md5=502b58d52476d863aa58058c7606b70b`, stock `sdvcam`, candidate, and library.
- `rollback/S01app.stock` matches pristine stock `S01app` (`3327c3a9...`, `daeb505e...`, `4246` B).
- `candidate.bin` and `libcammy_native_status.so` pass `native_trial.bundle.classify(..., reproduce=True)` (`structural_class=status-only`, `connect_trigger_sites_modified=[]`, byte-identical to WP2A generator) and `native_trial.review.verify()`.
- Verdict returns `LOCAL_ONLY/BLOCKED_FOR_ACTIVATION` (`activation_eligible: false`, `hardware_validated: false`).

### 5.2 Later Operator Camera Observations (Required by `native_acceptance.spec` if Separately Approved)

Do **not** populate fake PIDs, timestamps, or boot UUIDs in `review/acceptance-checklist.json`. If a future trial is ever separately approved, the operator must record independent evidence across three distinct camera boots (`selected`, `post_attempt`, `card_removed`):

1. **Selected Boot (`stage: selected-boot`):**
   - **Boot ID:** Read `/proc/sys/kernel/random/boot_id` (`selected` UUID).
   - **Preparation & Launch Records (Necessary but Insufficient Alone):**
     - `/run/cammy-select/status` (`cammy-boot-launch-status/1`): `decision=candidate`, `reason=selected`, `mode=select`, `attempt=<attempt>`, `boot=<selected>`.
     - `/run/cammy-select/launch.stamp` (`cammy-boot-launch-stamp/1`): `target=candidate`, `attempt=<attempt>`, `app=/run/cammy-select/sdvcam`, `md5=ef95bc8c0f9004cf54aa338ddfa7ad61`, `size=11335176`.
   - **Independent Process Identity (`native_apps`):**
     - Exactly **one** `sdvcam` process in `/proc/[0-9]*/comm`; record `pid` and `/proc/<pid>/stat` `starttime`.
     - `/proc/<pid>/exe` resolves to `/run/cammy-select/sdvcam`, and hashing `/proc/<pid>/exe` yields SHA256 `a9dedc985cc79d933601390df36b4458784e8dd4807b867cd53cfe0a8a9caba0` (MD5 `ef95bc8c0f9004cf54aa338ddfa7ad61`, `11335176` B).
     - `/proc/<pid>/limits` confirms `Max core file size` is `0` (`Gate E7`).
   - **Independent Library Mapping (`library_mapping`):**
     - `/proc/<pid>/maps` shows `/tmp/cammy-native-status/db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065/libcammy_native_status.so` mapped into `<pid>`, with file SHA256 `db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065` (MD5 `9249d39eb3755c4598ec15f10586ddfd`, `21456` B).
   - **Watchdog Ownership & Stability (`watchdog`):**
     - `/proc/<pid>/fd/3` points to `/dev/watchdog` (`holder_pid == <pid>`).
     - Continuous observation for `>= 600` seconds (`WATCHDOG_WINDOW_S = 600`) with `resets == 0` and unchanged `boot_id_after == <selected>`.
   - **Camera Baseline & Status Bridge (`network`, `lcd`, `bridge`, `media`):**
     - `home_wifi_unchanged: true`, `portal_reachable: true`, `lcd.normal_view: true`.
     - Bridge socket under `/tmp/cammy-bridge` responds to `HELLO`/`STATUS` with `hello_ok: true`, `backend_kind: 2` (`native`), `boot_id: <selected>`, `hardware_actions_enabled: false`, `physical_capabilities: "all-unavailable"`, and `media.sessions_started: 0`.
2. **Cleanup & Disarm (`stage: cleanup`):**
   - `bridge_session_closed: true`, `orphan_processes: 0`, `unexpected_listeners: 0`, `media_sessions: 0`.
   - `disarm.method` (`arm-removed` or `enabled-removed`) with `disarm.verified: true`.
3. **Stock Restore Across Two Subsequent Cold Boots (`stage: stock-restore`):**
   - **Post-attempt boot (`post_attempt` UUID):**
     - `/run/cammy-select/status` reports `decision=stock`, `reason=post-attempt-boot`.
     - `/etc/cammy/native-select/observed-<attempt>` exists with `boot=<post_attempt>`.
     - Single `sdvcam` process `/proc/<pid>/exe` hashes to stock SHA256 `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec` (MD5 `8689ce53c4674563a7a6935f97c0d727`).
     - `lcd_normal: true`, `home_wifi: true`.
   - **Card-removed boot (`card_removed` UUID):**
     - Cold boot with TF card physically absent (`card_present: false`).
     - Single `sdvcam` process `/proc/<pid>/exe` hashes to stock SHA256 `8771961f...`, `selector_decision: "stock"`, `lcd_normal: true`, `home_wifi: true`.
   - **Operator Acknowledgment & Durable Side-Effect Check:**
     - `ack-<attempt>` quotes `observed_boot=<post_attempt>` and `outcome` (`stock-restored` or `recovered`).
     - `durable_side_effects: []` — verify no internal overlay `/mnt/extsd/core.*`, no unexpected `/data/app_md5` modification, and no internal overlay `/mnt/extsd/.sdvcam` file.

---

## 6. Rollback Procedure & Recovery Boundaries

### 6.1 Immediate Disarm (Without Removing Bootstrap Files)

- With `SELECT_GATE=blocked` (as shipped), candidate selection is already impossible.
- In any future configuration, setting `/etc/cammy/native-select.enabled` to `mode=disabled` (or deleting `/etc/cammy/native-select.enabled`, or removing the TF card / `CammyOS-native/arm`) forces the immediate stock path on the next cold boot with zero TF probing or selector flash writes.
- Any already-claimed attempt (`claimed-<attempt>`) automatically forces verified stock on the next cold boot (`reason=post-attempt-boot`) before any TF access.

### 6.2 Full Internal Rollback to Pristine Stock

1. **Do NOT signal `sdvcam` or run `/etc/init.d/S01app stop|restart`:** The exact saved stock `stop()` sends `kill -9` to matching `sdvcam` processes and sleeps one second; killing `sdvcam` leaves `/dev/watchdog` in an unproven state. Perform file restoration while the system is stable, then use a clean cold reboot.
2. **Restore Stock `/etc/init.d/S01app`:**
   - Copy `rollback/S01app.stock` to `/etc/init.d/S01app` owned by `0:0` with mode `0755`, then run `sync`.
   - Verify before rebooting:
     - Byte size (`wc -c </etc/init.d/S01app`): `4246`
     - Line count (`wc -l </etc/init.d/S01app`): `156`
     - MD5 (`md5sum /etc/init.d/S01app`): `daeb505e811834088f64cb07696c5cbe`
     - SHA256 (via runtime BusyBox `sha256sum`): `3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136`
     - Confirm `/usr/bin/sdvcam` remains untouched stock (`md5=8689ce53c4674563a7a6935f97c0d727`, `size=11335176`, `sha256=8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`).
3. **Remove Selector Config and State From Internal Overlay:**
   - Remove `/etc/cammy/native-select.enabled` and `/etc/cammy/native-select.allow`.
   - Remove `/etc/cammy/native-select/inflight`, `/etc/cammy/native-select/claimed-*`, and `/etc/cammy/native-select/observed-*`.
   - Remove `/etc/cammy/native-select` (and `/etc/cammy` if empty), then run `sync`.
4. **Clean TF Card Artifacts:**
   - Remove `CammyOS-native/arm`, `CammyOS-native/ack-*`, `CammyOS-native/candidate.bin`, and `CammyOS-native/libcammy_native_status.so` from the TF card.
5. **Cold Reboot & Post-Rollback Verification:**
   - Power-cycle / cold-boot the camera (`tmpfs` directories `/run/cammy-select`, `/tmp/cammy-native-status`, and `/tmp/cammy-bridge` vanish on reboot).
   - Confirm `/etc/init.d/S01app` still hashes to stock `3327c3a9...` (`daeb505e...`), `/etc/cammy/native-select` is absent, and a single stock `/usr/bin/sdvcam` process owns `/dev/watchdog`.

### 6.3 Limits if USB or Early-Boot Access is Lost

- **In-band rollback requires boot/USB reachability:** Restoring `/etc/init.d/S01app` over ADB requires the camera to complete early init and bring up the USB gadget.
- **TF removal boundary:** Powering off and removing the TF card bypasses card reads when `S01app.proposal` is intact, but cannot repair a corrupted `/etc/init.d/S01app` or damaged internal JFFS2 overlay.
- **Unverified low-level flash restore (FEL boundary):** Entering Allwinner `FEL` mode proves only maskrom USB enumeration. **FEL entry does NOT prove flash restoration;** full low-level SPI NOR flash dump and restore over FEL remains unverified on this camera. Do not write to `/etc/init.d/S01app` or `/etc/cammy` without Adam’s explicit Gate E2 sign-off accepting this boundary.
