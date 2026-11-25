# Chunk 6: Disarmed offline status-only review package — 2026-11-14

Base: `b9045f8` (`b9045f88e2c4fcd24683a473d27f71fda42f61e4`). **Offline review package only; no installer, no camera/card/system writes, no hardware gate closed.**

See [Chunk 3 contract](CHUNK3-BOOT-LAUNCH-CONTRACT.md), [Chunk 5 selection fixtures](CHUNK5-ONE-SHOT-SELECTION.md), [Chunk 5 independent review](CHUNK5-INDEPENDENT-REVIEW.md), [camera compatibility inventory](../CAMERA-COMPATIBILITY-2026-11-14.md), [loader proof](../native-bridge/LOADER-PROOF.md), and the [operator review & rollback runbook](../NATIVE-TRIAL-REVIEW.md).

---

## 1. Deliverable Scope & Frozen Boundaries

Chunk 6 packages the reviewed Chunk 5 `S01app` proposal (`SELECT_GATE=blocked`), the exact stock `S01app` rollback copy, the WP2A status-only candidate (`sdvcam-status-db34fa035a26`) and bridge library (`libcammy_native_status.so`), the strict external allowlist, a disabled mode template, non-consumed `arm`/`ack` templates, a stage-5 acceptance checklist, and the operator rollback runbook into one deterministic, hash-bound, mode-`0644` review package.

- **Disarmed review-only status:** `review_only: true`, `activation_eligible: false`, `installation_authorized: false`, `deployed: false`, `hardware_validated: false`, `local_only: true`, `select_gate: "blocked"`.
- **Verdict:** `LOCAL_ONLY/BLOCKED_FOR_ACTIVATION` (never hardware or install `PASS`).
- **No live activation path exists:** `SELECT_GATE=blocked` is pinned in `native/activation/launch_selected_or_stock.proposal` and in the generated `S01app.proposal`. The fixture token `SELECT_GATE=chunk5-fixture-open` is for synthetic host test relocation only and must never be recommended for a camera, used to edit/bypass `SELECT_GATE=blocked`, or emitted by any tool.
- **No installer or auto-run layout:** All 14 package members are written with mode `0644` (no execute bits, `st_nlink == 1`, no symlinks). `tf-staging/CammyOS-native/` contains only `candidate.bin` and `libcammy_native_status.so`; `arm.template` and `ack.template` live under `templates/` as non-consumed review templates. No `--apply`, `--install`, `--flash`, `--adb`, or `--enable` mode exists.

---

## 2. Owned Paths

- `tools/trial_package/__init__.py`
- `tools/trial_package/package.py`
- `tools/trial_package/cli.py`
- `tools/trial_package/__main__.py`
- `tests/test_trial_package.py`
- `docs/NATIVE-TRIAL-REVIEW.md`
- `docs/work-packages/CHUNK6-REVIEW-PACKAGE.md`
- Ignored private output: `private/ownership-program/chunk6/review-package/` and `private/ownership-program/chunk6/checkpoint.json`

No frozen files in `tools/boot_launch/`, `native/activation/`, `tools/native_trial/`, `tools/native_acceptance/`, `native/loader/`, `native/bridge/`, `companion/`, `adapters/`, `Makefile`, or earlier reports were modified.

---

## 3. Pinned Identities & Allowlist Binding

All digests and sizes were re-derived from current bytes at `HEAD` (`b9045f8`):

| Artifact | Role | SHA256 | MD5 | Size (B) | Lines |
| --- | --- | --- | --- | ---: | ---: |
| Stock `S01app` | Build input & `rollback/S01app.stock` | `3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136` | `daeb505e811834088f64cb07696c5cbe` | 4,246 | 156 |
| Helper proposal | `provenance/launch_selected_or_stock.proposal` (`SELECT_GATE=blocked`) | `c20efedcd528fd8fd6e84e55fbfaa880fe7b2f0a4d247a09205d8357d105e8d9` | `a372c0cdf596bc64e733ce9c07dff6b4` | 36,343 | 1,221 |
| Generated `S01app.proposal` | `internal-bootstrap/S01app.proposal` (`SELECT_GATE=blocked`) | `8e79dd90c290d4678d7da08dfea980b67e63440c58b9a51d6b57d6f89770766f` | `502b58d52476d863aa58058c7606b70b` | 40,784 | 1,380 |
| Stock `sdvcam` | Runtime stock executable verified before any fallback launch | `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec` | `8689ce53c4674563a7a6935f97c0d727` | 11,335,176 | — |
| Status candidate | `tf-staging/CammyOS-native/candidate.bin` (`status-only`) | `a9dedc985cc79d933601390df36b4458784e8dd4807b867cd53cfe0a8a9caba0` | `ef95bc8c0f9004cf54aa338ddfa7ad61` | 11,335,176 | — |
| Status library | `tf-staging/CammyOS-native/libcammy_native_status.so` | `db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065` | `9249d39eb3755c4598ec15f10586ddfd` | 21,456 | — |
| External allowlist | `internal-bootstrap/native-select.allow` (`cammy-native-allow/2`) | `ecdc50687e183b744d23b3f0e6e830788a256ef663136fb93d1ab89bb162e7b2` | `ce4bb9b2fbb2f4c08209fdc767e5430c` | 313 | 6 |

- **Allowlist binding:** `tools/trial_package/package.py` invokes `tools.boot_launch.generator.generate_proposal` first, then builds `internal-bootstrap/native-select.allow` via `native_trial.records.allow_record` with `s01app_md5=502b58d52476d863aa58058c7606b70b` (the generated replacement digest), `source_md5=8689ce53c4674563a7a6935f97c0d727`, `source_size=11335176`, `candidate=ef95bc8c0f9004cf54aa338ddfa7ad61:11335176:status-only`, and `library=db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065:9249d39eb3755c4598ec15f10586ddfd:21456`.
- **Runtime RAM paths:** Candidate stages to `/run/cammy-select/sdvcam` (`0500`, `0:0`); library stages to `/tmp/cammy-native-status/db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065/libcammy_native_status.so` (`0444`, `0:0`); bridge socket dir is `/tmp/cammy-bridge` (`0700`, `0:0`); persistent state dir is `/etc/cammy/native-select` (`0700`, `0:0`).

---

## 4. Package Contents & Public Identity Summary

Initial delegate snapshot (before manager corrections): `private/ownership-program/chunk6/review-package`. The identities in this section describe that snapshot, not the final manager package; its manifest/checklist/runbook hashes are superseded. Final provenance is recorded in ignored `private/ownership-program/chunk6/manager-final-manifest.json`.
- `contents_count`: `14` members (`package-manifest.json` + 13 hash-bound artifacts)
- `package_tree_sha256`: `b0748b78aaf57b4b84362f6ea3c778927ba8a0315a559f2e870146e04cfbdc5d`
- `manifest_sha256`: `a83d700c69f8e02fa1cfc66061977d02a136b5d08063db8e18ef220d3f5b531b` (`30,387` B)

| Package Member | Target Plane | Intended Camera Path | Intended Mode / Owner | SHA256 | Size (B) |
| --- | --- | --- | --- | --- | ---: |
| `package-manifest.json` | `host-review-only` | `None` | — | `a83d700c69f8e02fa1cfc66061977d02a136b5d08063db8e18ef220d3f5b531b` | 30,387 |
| `internal-bootstrap/S01app.proposal` | `internal-bootstrap` | `/etc/init.d/S01app` | `0755` `0:0` | `8e79dd90c290d4678d7da08dfea980b67e63440c58b9a51d6b57d6f89770766f` | 40,784 |
| `internal-bootstrap/S01app.proposal.diff` | `host-review-only` | `None` | — | `be71454ca2aaff437e4262712aeec172df38b0f8fe86b8c4fe16f7673ca52518` | 39,334 |
| `internal-bootstrap/native-select.allow` | `internal-bootstrap` | `/etc/cammy/native-select.allow` | `0600` `0:0` | `ecdc50687e183b744d23b3f0e6e830788a256ef663136fb93d1ab89bb162e7b2` | 313 |
| `internal-bootstrap/native-select.enabled.disabled-template` | `internal-bootstrap` | `/etc/cammy/native-select.enabled` | `0600` `0:0` | `e72ecc5df027b9aea134754baa7d73e0cddc4bad0875b65ac7d686fdf5fffb14` | 14 |
| `rollback/S01app.stock` | `internal-rollback` | `/etc/init.d/S01app` | `0755` `0:0` | `3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136` | 4,246 |
| `tf-staging/CammyOS-native/candidate.bin` | `tf-card` | `/mnt/extsd/CammyOS-native/candidate.bin` | `0644` `vfat-mount` | `a9dedc985cc79d933601390df36b4458784e8dd4807b867cd53cfe0a8a9caba0` | 11,335,176 |
| `tf-staging/CammyOS-native/libcammy_native_status.so` | `tf-card` | `/mnt/extsd/CammyOS-native/libcammy_native_status.so` | `0644` `vfat-mount` | `db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065` | 21,456 |
| `templates/arm.template` | `tf-template-not-staged` | `/mnt/extsd/CammyOS-native/arm` | `0644` `vfat-mount` | `35be66f59c7bf86f9fd34c3dcdee76d807bf53149ccee3a249b1aa49e3684f09` | 292 |
| `templates/ack.template` | `tf-template-not-staged` | `/mnt/extsd/CammyOS-native/ack-00000000000000000000000000000001` | `0644` `vfat-mount` | `7420d76048e2075c82646f074e6661ef0020095bb0d3d94688eadf54a8c9d9cb` | 141 |
| `provenance/launch_selected_or_stock.proposal` | `host-review-only` | `None` | — | `c20efedcd528fd8fd6e84e55fbfaa880fe7b2f0a4d247a09205d8357d105e8d9` | 36,343 |
| `provenance/review.json` | `host-review-only` | `None` | — | `54ece0f5e35848e6849de7b6f80b63870d4d0136c25f6c1f75f0a03334e263bd` | 1,954 |
| `review/acceptance-checklist.json` | `host-review-only` | `None` | — | `144b80e2a4fe71b22500933e316103bda82be9b25e1f995c9126bba2f9911d1d` | 11,074 |
| `review/NATIVE-TRIAL-REVIEW.md` | `host-review-only` | `None` | — | `cdbbd9cfd1ef8bac0f75af81b1f8aaadac7c95e19c4b989216fc34b9275f80b2` | 21,227 |

---

## 5. Verification Executed

```sh
PYTHONPATH=.:tools python3 -m tools.trial_package build --out-dir private/ownership-program/chunk6/review-package
PYTHONPATH=.:tools python3 -m tools.trial_package validate private/ownership-program/chunk6/review-package
PYTHONPATH=.:tools python3 -m tools.trial_package summary private/ownership-program/chunk6/review-package
PYTHONPATH=.:tools python3 -m unittest -v tests.test_trial_package
```

- **Focused test suite (`tests.test_trial_package`):** 12 tests passed (`OK`).
  - Covers deterministic real-byte build/validation, mode `0644` / no-exec / `st_nlink == 1` enforcement, `SELECT_GATE=blocked` and `mode=disabled` defaults, replacement `s01app_md5` allowlist binding and rejection of stock `S01app` allowlist binding, stock `S01app` rollback verification, output path confinement and overwrite/symlink/traversal rejection, tamper/extra-file/armed-file/exec-bit/symlink/hardlink rejection, unpopulated hardware checklist guards (`PENDING_HARDWARE_NOT_RUN`), preservation of `offline`/`fixture` review classes without promotion, rejection of synthetic or media-connect candidates in the production builder, and CLI rejection of `--apply`/`--install`/`--flash`/`--adb`/`--enable` modes.
- **No hardware or side effects:** No camera/ADB/network/TF access, mount, process signal, or vendor binary execution occurred.

---

## 6. Next Human Review Boundary

1. **Manager / human review of this disarmed package:** Inspect `docs/NATIVE-TRIAL-REVIEW.md`, `private/ownership-program/chunk6/review-package/package-manifest.json`, and `internal-bootstrap/S01app.proposal.diff`.
2. **Stage A (Stock-Fallback Bootstrap Check only, if separately approved under Gate E2):** Even if internal bootstrap files are later approved for installation, `S01app.proposal` ships with `SELECT_GATE=blocked` and can only exercise verified stock fallback (`decision=stock`) and the tightened `/mnt/extsd` backup gate.
3. **Stage B (Future Status-Only Candidate Trial — Currently Blocked):** Requires closing open prerequisite gates (`EA`, `E1`, `E2`, `E3`, `E4`, `E6`, `E7`) and approving a separate live gate change before any candidate can ever be selected on hardware.

## Manager integration

The manager reproduced the initial 12 package tests and a full 379-test repository
run (118.221s, no failures; three existing HTTPError ResourceWarnings). Review of
the generated summary found inherited WP4 gate descriptions pointing to the
rejected S00 bind overlay. The package now describes the revised S01app route,
keeps all gate statuses open/blocked/partial, and validates those descriptions in
both manifest and checklist. A regression rejects the old policy snapshot; the
updated focused suite passes 13 tests (13.287s). Frozen launcher/loader inputs are
unchanged. Runbook wording scopes current authorization to this task and reserves
approval of the exact internal change to Adam. Final package generation occurs
from the integration commit, with its identity saved in the private checkpoint.
