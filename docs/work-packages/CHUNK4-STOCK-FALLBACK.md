# Chunk 4: Inactive Exact-Source `S01app` Launch Proposal and Stock Fallback Verification — 2026-11-14

Base commit: `5fa2915`
Authoritative contract: [`docs/work-packages/CHUNK3-BOOT-LAUNCH-CONTRACT.md`](CHUNK3-BOOT-LAUNCH-CONTRACT.md)
Evidence class: Offline local exact-source generator, inert shell helper proposal (`0644`), parse-only shell checks (`dash`, `busybox sh`, `bash`, `bash --posix`), and relocated `dash` + Python reference model equivalence on isolated temporary fixtures. No vendor binary execution, no candidate selection, no TF staging, no durable selector state writes, no installer, and no camera/hardware interaction.

## Manager review

The manager reproduced the original23 tests, then added four regressions and
fixes: root-owned0700 RAM-directory validation before preparation/launch,
TF source identity as well as vfat type, truthful exec-attempt logging, and
exclusive output creation that refuses existing files/symlinks before writing.
**27/27 tests pass** after these changes. Synthetic fixtures substitute their
own UID/GID in a marked test-only identity block; production IDs stay0. Early
camera support for numeric `ls -ldn` remains E3 until read-only verification.
No candidate path or activation authority was added.

Current artifacts are under `build/boot-launch/manager-reviewed-chunk4/`.
Choose a fresh output directory for each emission; overwriting is refused.
The identities and line ranges below describe this reviewed revision.

## 1. Source vs. Generated Identities

Build-time input identity and generated replacement identity are strictly separated; the runtime helper verifies only the stock `/usr/bin/sdvcam` executable (`MD5` + size) and never compares the modified `S01app` against the stock `S01app` input hash.

| Artifact | Role | SHA-256 | MD5 | Size (bytes) | Lines |
| --- | --- | --- | --- | --- | --- |
| Saved stock `S01app` (read-only input) | Exact build-time input verified before generation | `3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136` | `daeb505e811834088f64cb07696c5cbe` | `4,246` | `156` |
| [`native/activation/launch_selected_or_stock.proposal`](../../native/activation/launch_selected_or_stock.proposal) | Inert stock-only helper source (`0644`, `SELECT_GATE=blocked`) | `9f7848c24ab96a2be240e6bc1dcf51b5bec52ace2c6f34e6742a36516b281bc0` | `fb7aeb64db21fedae412421c8ff27799` | `11,012` | `350` |
| Generated `S01app.proposal` (ignored output only) | Inactive replacement proposal (`0644`, `deployed: false`, `activation_eligible: false`) | `a766649dc21a54554e91de272165019842b0cf4b0d7bc322b0d8e3774230f523` | `654ab1acb1a42d946c0c137d5f5d5411` | `15,451` | `509` |
| Stock `/usr/bin/sdvcam` (never executed) | Runtime stock identity verified before fallback launch | `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec` | `8689ce53c4674563a7a6935f97c0d727` | `11,335,176` | — |

Complete vendor-derived `S01app.proposal`, `S01app.proposal.diff`, and `manifest.json` are emitted only into git-ignored directories (`build/boot-launch/`, `private/ownership-program/chunk4/proposal/`, or temporary test directories) with mode `0644` and `deployed: false`, `activation_eligible: false`. Public git tracks only our generator, inert helper proposal, tests, and this report.

## 2. Exact Transformation and Preserved Ranges

`tools/boot_launch/generator.py` verifies the exact 156-line / 4,246-byte stock `S01app` input before emitting any output and refuses any modified or truncated input:

1. **Helper insertion:** Embeds `launch_selected_or_stock.proposal` (without duplicate shebang) immediately before `start()` (stock line 24).
2. **Single preparation & tightened backup gate (stock line 78):** Replaces the single active legacy `if [ ! -z "$(grep '\<vfat\>' /proc/mounts)" ]; then` check with:
   ```sh
   cammy_prepare_launch
   if [ "$LAUNCH_BACKUP_ELIGIBLE" = "1" ]; then
   ```
   `LAUNCH_BACKUP_ELIGIBLE` is `1` **only** when `LAUNCH_DECISION=stock`, `/mnt/extsd` itself has topmost mount type `vfat` from `/dev/mmcblk0` or `/dev/mmcblk0p1` in `/proc/mounts`, and no private staging mount or file residue (`/run/cammy-select/tf`, submounts under `/run/cammy-select/`, or `/run/cammy-select/sdvcam`) exists. Unrelated `vfat` mounts and failed private TF detaches never authorize backup writes into the internal overlay.
3. **Five launch sites routed through one authority:** Stock lines **91, 99, 101, 114, 118** (`/usr/bin/sdvcam &`) are replaced with `cammy_launch_selected_or_stock`, preserving indentation.
4. **Mechanically preserved stock ranges (150 of 156 lines byte-for-byte identical):**
   - Stock lines `1..23` (`proposal 1..23`): shebang, VM sysctls, environment exports, `/tmp/data` symlink.
   - Stock lines `24..77` (`proposal 376..429`): `start()` DRAM/`mbus_pmu` QoS, AXP register writes, `ulimit -c unlimited`, `core_pattern`, commented audio/mount lines.
   - Stock lines `79..90` (`proposal 432..443`): `gettimems` and `.firstboot` size/space check and `.sdvcam` backup copy.
   - Stock lines `92..98` (`proposal 445..451`): `.firstboot` cleanup (`rm /overlay/.firstboot`, `rm /mnt/extsd/isp*.bin`) and existing `.sdvcam` MD5 comparison.
   - Stock line `100` (`proposal 428`): `else` branch for matching `.sdvcam` MD5.
   - Stock lines `102..113` (`proposal 455..466`): normal-boot missing-`.sdvcam` size/space check and backup copy.
   - Stock lines `115..117` (`proposal 468..470`): closing `fi` / `else` framing before launch site 118.
   - Stock lines `119..156` (`proposal 472..509`): closing `fi`, commented `webapp`/`hwclock`, `stop()`, and `start|stop|restart` `case` dispatch.

## 3. Stock-Only Helper & Fail-Closed Invariants

- **Chunk 4 narrow hook (`SELECT_GATE=blocked`):** `SELECT_GATE` is pinned to `blocked`. Missing (`not-enabled`), `mode=disabled` (`disabled`), invalid (`config-invalid`), and `mode=observe` / `mode=select` (`select-gate-blocked`) configurations all skip TF/card probing and durable state writes and select verified stock. If a fixture mutates `SELECT_GATE` away from `blocked`, `cammy_prepare_launch` refuses launch with `decision=recovery-required reason=select-gate-tampered`. No candidate staging or candidate `exec` code path exists in Chunk 4.
- **No unverified fallback:** Before preparing or launching stock, `cammy_verify_stock_app` checks that `/usr/bin/sdvcam` is a regular non-symlink executable matching `STOCK_SDVCAM_SIZE=11335176` and `STOCK_SDVCAM_MD5=8689ce53c4674563a7a6935f97c0d727`. If `/usr/bin/sdvcam` is missing, non-executable, mismatched, or if `cammy_tools_ok` (`md5sum`, `wc -c`, `sed`, `tail -c`, `awk`, `grep \<\>`), `/run` tmpfs/exec checks, or `boot_id` UUID validation fail, the helper sets `decision=recovery-required` and **launches nothing**.
- **Exclusive per-boot RAM launch stamp:** `cammy_launch_selected_or_stock` creates `/run/cammy-select/launch.stamp` exclusively via `(set -C && printf ...)` and verifies read-back before starting `sdvcam`. If stamp creation fails (`launch-stamp-failed`), nothing is launched. Repeat invocations of `S01app start`, `cammy_prepare_launch`, or `cammy_launch_selected_or_stock` in the same boot detect the existing stamp (`launch-stamp-exists`) or running native owner (`/proc/[0-9]*/comm == sdvcam`, `app-already-running`), refuse a second launch, and preserve existing `/run/cammy-select/status` and `/run/cammy-select/launch.stamp` evidence untouched.
- **Old `S00cammy-select` route:** Left untouched and `EA`-blocked in `tools/native_trial/policy.py`.

## 4. Safe Test Substrate and Equivalence Scope

- **What was executed and proved locally:**
  - `dash -n`, `busybox sh -n`, `bash -n`, and `bash --posix -n` parse-only syntax checks pass on both `native/activation/launch_selected_or_stock.proposal` and the full 509-line generated `S01app.proposal`.
  - The embedded helper and transformed `S01app` branch block (stock lines 78–119) are extracted into a temporary fixture, relocated via explicit test-only header rewriting (`relocate_helper_for_test` / `extract_and_relocate_s01app_branch_harness`), and executed under `/usr/bin/dash` (never `busybox ash`) with PATH stubs (`cp`, `rm`, `du`, `df`, `gettimems`, `md5sum`, `awk`, `tail`) confined to the temporary root (`BootLaunchFakeTree`).
  - A synthetic 53-byte shell stub stands in for `/usr/bin/sdvcam` inside the temporary fixture (`synthetic_fixture: true`, `activation_eligible: false`). No production environment variable override exists in the helper or generator.
  - Every scenario runs against both the relocated `dash` harness and the Python reference model (`run_s01app_branch_model`) and asserts identical snapshots across all 5 launch branches (`91`, `99`, `101`, `114`, `118`), config states, residue/mount checks, tool/hash faults, duplicate invocations, and stamp failures.
- **Known limits / not proven by Chunk 4:**
  - Stock lines `1..77` and `120..156` (DRAM QoS, AXP power registers, sysctls, `stop()`) are proved by exact byte-range preservation, not host execution.
  - `dash` + PATH-stub equivalence does not prove the camera's early-boot `/bin/sh` identity or applet options (Gate E3), JFFS2 power-loss durability, watchdog timing, or hardware readiness (Gates E2–E7).
  - Candidate selection, TF mounting/staging, and `inflight`/`claimed`/`observed` state records remain unimplemented hooks for Chunk 5.

## 5. Changed Paths and Reproducible Verification

### Owned paths created
- [`native/activation/launch_selected_or_stock.proposal`](../../native/activation/launch_selected_or_stock.proposal) (mode `0644`)
- [`tools/boot_launch/__init__.py`](../../tools/boot_launch/__init__.py)
- [`tools/boot_launch/generator.py`](../../tools/boot_launch/generator.py)
- [`tools/boot_launch/model.py`](../../tools/boot_launch/model.py)
- [`tools/boot_launch/cli.py`](../../tools/boot_launch/cli.py)
- [`tools/boot_launch/__main__.py`](../../tools/boot_launch/__main__.py)
- [`tests/test_boot_launch.py`](../../tests/test_boot_launch.py)
- [`docs/work-packages/CHUNK4-STOCK-FALLBACK.md`](CHUNK4-STOCK-FALLBACK.md)
- Ignored outputs: `build/boot-launch/{S01app.proposal,S01app.proposal.diff,manifest.json}` and `private/ownership-program/chunk4/{checkpoint.json,proposal/*}`

### Verification commands and outcomes
```sh
cd <projects>/Cammy-OS
PYTHONPATH=.:tools python3 -m unittest -v tests.test_boot_launch
PYTHONPATH=.:tools python3 -m boot_launch verify-source
PYTHONPATH=.:tools python3 -m boot_launch emit-proposal --out-dir build/boot-launch/FRESH-DIRECTORY
dash -n native/activation/launch_selected_or_stock.proposal build/boot-launch/FRESH-DIRECTORY/S01app.proposal
busybox sh -n native/activation/launch_selected_or_stock.proposal build/boot-launch/FRESH-DIRECTORY/S01app.proposal
bash -n native/activation/launch_selected_or_stock.proposal build/boot-launch/FRESH-DIRECTORY/S01app.proposal
bash --posix -n native/activation/launch_selected_or_stock.proposal build/boot-launch/FRESH-DIRECTORY/S01app.proposal
```
Outcome after manager review: `Ran 27 tests in 8.087s — OK`; `verify-source` and `emit-proposal` exit `0`; all four shell syntax checks exit `0`.
