# Chunk 5 independent safety review — 2026-11-14

Base: `289d5b4` + uncommitted Chunk 5 selection prototype and manager fixes.
**Review only; no implementation files modified, no hardware gate closed, no activation approved.**

## Manager integration after this review

The hashes below identify the reviewed snapshot, before these small follow-ups:

- F1: `status` is explicitly documented as immutable **preparation** evidence.
  `launch.stamp` records only an exec attempt. Neither proves a running app or
  bridge readiness; the chunk 6 runbook must require independent observations.
- F2: the shell/model now finish observing every earlier claim before returning
  stock, even if an earlier observation already names this boot. Regression added.
- F3: added real read-back corruption at inflight and claim sync boundaries,
  checking retained records, cleanup and verified stock fallback.
- F4: persistent state now requires the same owner/0700 checks as RAM directories;
  fixture directory modes and the matching model were updated, with a regression.
  Empty bridge-directory retention is accepted. Fixture-gate comments corrected.

The three focused regression methods passed after these changes. Final hashes
are derived from the integration commit/package manifest, not the snapshot table.
No hardware gate was closed by review or integration.

## Reviewed source hashes (SHA256)

| File | SHA256 |
| --- | --- |
| `native/activation/launch_selected_or_stock.proposal` | `815fe928b3759034e2623b5af6019fc1ac7302a59591b40e7913fbe752489597` |
| `tools/boot_launch/model.py` | `6a29129d4dc1e3ac703e590f1473b0f8c8a72bb875201c349d58a0af2ef4cc55` |
| `tools/boot_launch/generator.py` | `a61128ae0e390ece9789ef822eaccb736eed21e03db00c9fd8189ee012c2e2ab` |
| `tools/boot_launch/__init__.py` | `4caad32f79ea032b89eb4e55af41da99ea51fdb9c7c8638c3517b7e47db27eb0` |
| `tests/test_boot_selection.py` | `42198f4ed547004d3dc94e4cfe42c1922ac198eb4f2f5c8b0de399eb3d2fdf47` |
| `tests/test_boot_launch.py` | `81c147720cbdfcc277bd53d3ef1607a601b0c9bb39a1b54f448cc6da8593d548` |
| `docs/work-packages/CHUNK5-ONE-SHOT-SELECTION.md` | `c353fad22dd7b840aed6ab2ca45006c0bfcd03a5f90c3773baa42b7c9b544b15` |
| `docs/work-packages/CHUNK3-BOOT-LAUNCH-CONTRACT.md` | `a94dc296ff91ed5b1e4f1a0021a1e5347d246759485ee379e5bf6740a2910533` |

Generated replacement proposal from `SAVED_STOCK_S01APP` (`3327c3a9…72136`, MD5 `daeb505e811834088f64cb07696c5cbe`, 4,246 bytes, 156 lines):
- SHA256: `4bb9ff59668dd8d680fb98c1fc1d1f7a7532f8165c43bb3c101f8d13bcddcfcf`
- MD5: `db67a4d004d9d364acf9832acf70f1cd` (40,531 bytes, 1,374 lines)

---

## Findings (ordered by severity)

### No blocking safety bugs found in the core invariants

- **Invariant A (Blocked gate & public generator):** Verified. `SELECT_GATE=blocked` is pinned in `native/activation/launch_selected_or_stock.proposal` (line 56, mode `0644`) and enforced by `verify_helper_proposal()` (`tools/boot_launch/generator.py:213-230`). Hostile/enabled configs under `SELECT_GATE=blocked` never call `cammy_prepare_one_shot`, never probe/mount TF, and never write `/etc/cammy/native-select`. No environment-variable overrides exist in the shell helper or generator.
- **Invariant B (One-shot ordering & manager fixes):** Verified. Persistent state (`/etc/cammy/native-select`) is inspected before any TF probe/mount (`proposal:367-521`); `inflight` is exclusively written, synced, and read back before TF mount (`proposal:610-615`); TF is detached (`proposal:915-918`) before `claimed-<attempt>` is persisted and `inflight` removed (`proposal:941-955`). Manager fixes for `sync` return-code checking (`proposal:271-282`) and `INITFILE=/etc/init.d/S01app` replacement MD5 verification before TF access (`proposal:570-573`) are present and verified.
- **Invariant C (Filesystem trust & backup gate):** Verified. Never mounts over `/usr/bin/sdvcam` (`already-bound` check at `proposal:362-365`, private mount at `/run/cammy-select/tf` with `-t vfat -o ro,nosuid,nodev,noexec`). Failed TF detach sets `recovery-required` and suppresses backup writes (`proposal:328-344`).
- **Invariant D (Launch ownership & core suppression):** Verified. Every fallback path verifies runtime stock `sdvcam` MD5+size (`proposal:336-339`, `1149-1168`, `1192-1197`). Candidate exec runs in `(cammy_disable_core_zero && exec "$RUNDIR/sdvcam") &` after writing `launch.stamp` (`proposal:1179-1188`). No second app is ever started after a candidate exec attempt or crash.

---

### Low / Non-blocking findings & cleanup items for manager integration

#### F1 (Low — Diagnostic consistency): Launch-time fallback from `candidate` to `stock` updates `LAUNCH_DECISION`/`LAUNCH_REASON` in memory and `launch.stamp`, but leaves `/run/cammy-select/status` saying `decision=candidate` / `reason=selected`
- **Location:** `native/activation/launch_selected_or_stock.proposal:315-322, 1131-1169, 1179-1185, 1192-1211`; `tools/boot_launch/model.py:1564-1755`.
- **Trigger:** `cammy_prepare_launch` succeeds with `decision=candidate` (`reason=selected`) and writes `/run/cammy-select/status` once (`cammy_write_status` refuses to overwrite an existing status file at line 317). If `cammy_launch_selected_or_stock` subsequently detects a late fault before candidate exec (`cammy_verify_staged_candidate` failure at line 1147, `cammy_disable_core_zero` failure at line 1158, `rundir-not-private` at line 1131, or `launch-stamp-failed` at line 1179), it falls back to stock (or `recovery-required`) and writes `target=stock` / `reason=candidate-stage-invalid` to `launch.stamp`, while `/run/cammy-select/status` still records `decision=candidate` and `reason=selected`.
- **Observable consequence:** Safety is preserved (staged RAM is cleaned and only verified stock or nothing is launched), and `launch.stamp` + stdout log reflect the actual target, but `/run/cammy-select/status` disagrees with `launch.stamp`. (In `model.py:1533-1538`, `_invoke_launch_after_prepare` also reads `boot=` from `status` rather than `HelperDecision`.)
- **Reproducer:** `private/ownership-program/chunk5-review/repro_all.py` (`F1_launch_fallback_stale_status`).
- **Proposed minimal fix:** Either document that `/run/cammy-select/status` is strictly the preparation-phase record while `launch.stamp` is the authoritative launch-phase record, or allow a helper `cammy_update_status` to refresh `status` when `cammy_launch_selected_or_stock` downgrades `candidate` to `stock` or `recovery-required` before `launch.stamp` is created (while still refusing to touch `status` once `launch.stamp` exists or `sdvcam` is running).

#### F2 (Low — Multi-claim edge case): Observed-boot recording loop exits early if an earlier `claimed-*` was already observed in the current boot, skipping any later unobserved `claimed-*` in the same boot
- **Location:** `native/activation/launch_selected_or_stock.proposal:485-507`; `tools/boot_launch/model.py:1038-1050`.
- **Trigger:** When two `claimed-<id>` files exist and `claimed-<id1>` already has `observed-<id1>` with `boot=$LAUNCH_BOOT` while `claimed-<id2>` has no `observed-<id2>` (reachable if an earlier run in the same boot wrote `observed-<id1>`, failed on `observed-<id2>` due to a transient sync error before `launch.stamp` was created, and `S01app` was invoked again in the same boot).
- **Observable consequence:** Lines 489-492 immediately return `stock` (`post-attempt-boot` for `id1`) before reaching `claimed-<id2>`, leaving `observed-<id2>` unwritten until the next reboot. Fail-closed behavior is preserved (stock is selected without TF access), so there is no safety breach.
- **Reproducer:** `private/ownership-program/chunk5-review/repro_all.py` (`F2_multi_claim_observed_short_circuit`).
- **Proposed minimal fix:** In the `for sf in "$STATE"/claimed-*` loop (`proposal:485-503` and `model.py:1038-1049`), if `observed-$cid` already has `boot=$LAUNCH_BOOT`, set `new_obs=1; last_obs_id=$cid; continue` instead of returning immediately, so every unobserved claim is recorded before returning `post-attempt-boot`.

#### F3 (Low — Test/harness fidelity): Shell fault overrides for `*-write-fail` and `inflight-clear-fail` bypass `printf`/`rm` before returning `1`, whereas real `sync` or read-back failures leave the newly created/unlinked file on disk
- **Location:** `tools/boot_launch/model.py:761-795, 1844-1851`; `tests/test_boot_selection.py:603-609, 622-628`.
- **Trigger:** `extract_and_relocate_s01app_branch_harness` injects shell function overrides for `inflight-write-fail`, `claim-write-fail`, `observe-write-fail`, and `inflight-clear-fail` that `return 1` before `printf` or `rm -f` runs (`model.py:1844-1851`). By contrast, the manager's `sync-fail-1/2/3` and `corrupt-on-sync` faults exercise the real shell `cammy_write_record` / `cammy_remove_inflight` functions, where `printf` (or `rm -f`) executes first and then `sync` or read-back fails—leaving `inflight` or `claimed-<attempt>` present in `STATE`.
- **Observable consequence:** Both paths fail closed to verified stock, and `sync-fail-1/2/3` already test the real shell functions (`test_boot_selection.py:607-609, 623-626`). However, someone reading the `claim-write-fail` / `inflight-write-fail` subcases may not realize those specific fault names test `set -C` open failure (no file created), whereas `corrupt-on-sync` (implemented in `model.py:260-265`) is not yet wired into a test case in `test_boot_selection.py`.
- **Reproducer:** `private/ownership-program/chunk5-review/repro_all.py` (`F3_harness_override_vs_real_write_failure`).
- **Proposed minimal fix:** Add a `corrupt-on-sync-1` / `corrupt-on-sync-2` subcase to `test_payload_mismatches_memory_low_and_copy_or_staging_faults_clean_up_and_select_stock` so read-back mismatch in the real shell `cammy_write_record` is directly asserted alongside `sync-fail-1/2/3`.

#### F4 (Informational — Trust boundary / cleanup): Pre-existing `/etc/cammy/native-select` directory permissions and `/tmp/cammy-bridge` residue in `observe` mode
- **Location:** `native/activation/launch_selected_or_stock.proposal:368-377, 895-928`; `tools/boot_launch/generator.py:106-111`.
- **Trigger & observation:**
  1. `STATE` (`/etc/cammy/native-select`) is checked for `[ ! -L "$STATE" ]` and `-d`, and created under `umask 077` if absent (`proposal:368-377`), but if `/etc/cammy/native-select` already exists prior to boot, `cammy_dir_private_ok "$STATE"` is not called (unlike `/run/cammy-select`, `/tmp/cammy-native-status`, and `/tmp/cammy-bridge`). Within the declared sole-writer root `/etc` model this is acceptable, since `/etc/cammy` is internal root-controlled storage rather than `1777` `/tmp` or `/run`, but checking `cammy_dir_private_ok "$STATE"` would make directory hygiene uniform.
  2. In `mode=observe`, step 8 creates `/tmp/cammy-bridge` (`0700`) and step 10 (`cammy_clean_staged_ram`) removes `/run/cammy-select/sdvcam` and `/tmp/cammy-native-status/<sha256>` while leaving the empty `0700` `/tmp/cammy-bridge` directory in place (`proposal:895-928`). Next-boot or same-boot checks accept an existing `0700` `/tmp/cammy-bridge` (`proposal:598-603`), so this is harmless, or `cammy_clean_staged_ram` can `rmdir "$BRIDGEDIR" 2>/dev/null` when created during that run.
  3. Minor doc string note: `tools/boot_launch/generator.py:110` and `native/activation/launch_selected_or_stock.proposal:14,350` comments mention `SELECT_GATE=open` in prose while the actual code and tests use `SELECT_GATE=chunk5-fixture-open`.

---

## Hardware gates (cannot be proven offline; remain open for E2–E7)

1. **Gate E6 (Physical power-loss durability on JFFS2/overlay):** `cammy_write_record` and `cammy_remove_inflight` check `sync` exit codes and read back from the kernel page cache, which does not prove physical flash persistence across an abrupt reset or brownout.
2. **Gate E3/E4 (Early-boot S01app environment, exclusive file creation, and watchdog budget):** Late-boot USB inventory (`~293s` uptime) confirmed applets and `set -C` syntax parsing, but actual `set -C` exclusive creation on `/etc/cammy/native-select` and `/run/cammy-select`, early-boot `/dev/mmcblk0` availability at S01 time, and card I/O timing against `/dev/watchdog` remain unproven on hardware.
3. **Gate E7 (Candidate core-limit enforcement):** `(ulimit -c 0 && exec ...)` is verified under `dash` on the host, but actual BusyBox `/bin/sh` `ulimit -c 0` enforcement before `sdvcam` exec on the camera remains a hardware check.

---

## Verification executed during this review

1. **Full focused test suite:**
   ```sh
   python3 -m unittest tests.test_boot_launch tests.test_boot_selection
   ```
   Result: **48 tests passed in 52.709s** (`OK`).
2. **Bounded local synthetic reproducer (`private/ownership-program/chunk5-review/repro_all.py`):**
   Verified F1–F4 and generated manifest digests (`replacement_sha256=4bb9ff59668dd8d680fb98c1fc1d1f7a7532f8165c43bb3c101f8d13bcddcfcf`, `replacement_md5=db67a4d004d9d364acf9832acf70f1cd`) under the confined `BootLaunchFakeTree` + `dash` harness with zero violations.
3. **Drive temperatures:** NVMe composite temperatures remained `45.9°C–51.9°C` (well below the `81°C` pause threshold).
