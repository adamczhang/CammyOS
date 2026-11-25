# WP5 independent review and stage-5 acceptance harness — 2026-11-14

**Local only.** This package reviewed the WP4 activation proposal and built an
offline acceptance-evidence validator. No camera, card, ADB, device node, ioctl,
signal to a camera process, real mount, TF update, internal init write, vendor
executable or resident service was used. Nothing is installed or approved. The
shell proposal was parsed (not run) under busybox `sh`; it ran only as a
relocated copy under `dash` with every mutating command intercepted. **No
hardware gate is closed by anything here.**

Outcome in one line: the WP4 bind-overlay early selector is **activation
ineligible** (new gate EA, blocked). It now ships `SELECT_GATE=blocked`, and no
tool emits an installable allowlist. A revised bootstrap design is proposed in §3
for review. The WP2A status-only build is aligned and classifies as structurally
status-only and byte-identical to its reviewed generator, but E1 stays open
(manager closure), and the result is LOCAL_ONLY.

**Manager integration:** reproduced both suites, then fixed a mixed-evidence
aggregation bug: a passing hardware-labelled item could hide a fixture item for
the same stage. The added regression covers both item orders and host/hardware
stages. Current counts are 94 activation + 32 acceptance = **126 tests**, all
passing. No hardware evidence or deployment was added. The next local package
will develop alternative (b), a reviewed S01app launch substitution; this is an
implementation direction, not authorization to install the replacement.

## 1. Findings

The severity scale is Critical (unverified bytes can run), High (a durable or
unsafe side effect, or a trust bypass), Medium (a fail-open edge or missing
evidence counted as success) and Low. "Pre" lines refer to the WP4 files as
delivered on top of `c81d295`. "Now" lines refer to this revision.

| ID | Sev | Where | Trigger / fault | Fix or gating | State |
| --- | --- | --- | --- | --- | --- |
| F2a | Critical | proposal pre 291–301, now 356–373 | The bind succeeds but verification fails, and `umount` fails. The selector prints `recovery-required` and exits 0. Stock S01app then runs `/usr/bin/sdvcam &`, as it does at all 5 launch sites. Pre-review, the copy was `chmod 0555` **before** the bind, so the unverified mount was executable. | **Exec-bit commit:** bind a 0444 copy, verify through the mount, then `chmod 0555` the shared inode. On any later failure, drop the exec bits first, then unmount. `bind-unrestorable` now leaves a non-executable file: no app runs, a power cycle follows, and the post-attempt boot is stock. Residual: if the mount shows some *other* executable file, the result is `bind-unrestorable-executable` and S01app runs it. | Mitigated in a design that remains blocked (EA). |
| F2b | High | proposal 346, 161; S01app backup branch | Card detach fails, giving `recovery-required tf-detach-failed` with exit 0. The private vfat mount matches S01app's `grep '\<vfat\>' /proc/mounts`. Its backup branch writes `/data/app_md5` and copies `/usr/bin/sdvcam` to `/mnt/extsd/.sdvcam`. At S01, `/mnt/extsd` is not yet a mount, so the copy goes to the **internal overlay** (subject to its du/df check). The same happens in observe mode. | Modelled in `tools/native_trial/s01app_model.py`. The sweep test shows exactly two unsafe outcomes: `tf-detach-failed` and `bind-unrestorable-executable`. The fix is design R1.1 (§3). | Open → EA |
| F2c | High | WP4 §1; `GUARDED-TF-LAUNCH-PLAN.md:31-33` | Bind-mounting over `/usr/bin/sdvcam` deviates from the plan, which explicitly avoided mounting over the stock executable. WP4 recorded it as "selected" by assertion. | It is now recorded as a deviation (`policy.DECISION['design_deviation']`) with no selected route. Alternatives are in §3. | Recorded; blocked |
| F2d | High | proposal 284–346 (card mount, reads, MD5) before the first durable write (claim, 351) | A hanging vfat mount or read (no `timeout` applet), or a bootloader-armed watchdog firing during S00, happens **before** the claim. The next boot repeats it. The result is a hang or a reset loop with no record. | Design R1.3 (in-flight breadcrumb) plus E4 timing. Not implemented. | Open → EA, E4 |
| F1 | High | records pre 120–125; proposal pre 51, 213 | The library path used MD5 with a hyphenated name: `/tmp/cammy-native-status/<md5>/libcammy-native-status.so`. The manager/WP2A contract is SHA256 with an underscore. | `records.library_path(sha256)` now gives `/tmp/cammy-native-status/<SHA256>/libcammy_native_status.so`. The arm has `lib_sha256` (`cammy-native-arm/2`). The allowlist has `library=<sha256>:<md5>:<size>` (`cammy-native-allow/2`). The selector derives `LIBDIR` from the reviewed allowlist SHA256 (now 270–277). On the camera, MD5 + size remain a **weak mismatch check** that does not authenticate the SHA256. The SHA256 identities are pinned in the host manifest. | Fixed |
| F3a | High | bundle pre 116–178 | `eligible: true` followed from the changed-range scope, an ELF header and one path string. Arbitrary code in the cave, or any ARM `.so`, passed. | `classify` now separates `structural_ok` from `trust`. `eligible` needs a SHA256-bound review/ABI record (`native_trial/review.py`): NATIVE-ABI §7 proofs 1–5 as hashed artifacts, plus provenance. A `fixture` or `offline` record is LOCAL_ONLY. Even `reviewed` means only "E1 evidence complete, manager closure pending". `activation_eligible` is always false. An optional `--reproduce` compares the candidate byte-for-byte with the reviewed WP2A generator. | Fixed |
| F3b | Medium | bundle pre 239–284; cli pre 113–118 | `validate_bundle(out)` without a source returned `ok` for any self-consistent file set. `proposal-patch --bundle` used it to emit a **real allowlist**, giving an installable patch. | `ok` now needs the stock source (reclassification) and the evidence root (proof re-hash). `proposal-patch` refuses every bundle while activation is blocked. Tamper tests cover the manifest's `activation_eligible` and `local_only` fields and `review.json`. | Fixed |
| F3c | Medium | policy pre 26–30; bundle | The scope allowed the whole cave, including the media candidate's range. The bl target and LOAD sizes were unchecked. | WP2A reviewed constants now apply: stub start VA 0x8E2E40 (bl must target it), stub end ≤ file 0x8D3000 (last mapped text page), and LOAD grown equally from 0x8D2E1C to the stub end. The media range is outside scope. There is a stray-path check in the cave. A test cross-checks these against `tools/build_native_status_candidate.py` (read-only). | Fixed |
| F5a | Medium | proposal pre 82 | `record_ok` used `[ -z "$(tail -c 1 f)" ]`, which **passes when `tail` fails or lacks `-c`**. | `tail -c 1 \| wc -l` must be 1 (98–99). `tools_ok` self-tests md5sum, `wc -c`, sed, `tail -c`, awk and `grep \<\>` on a known file. Any difference gives `tools-unverified`, then stock (122–137, 207). A broken awk already gives `run-not-tmpfs`. | Fixed (tested with broken PATH stubs) |
| F5b | Low | proposal pre 96 | `(set -C; printf …)`: if a shell rejects `set -C`, the write proceeds non-exclusively. | `set -C && printf` (114). | Fixed |
| F5c | Medium | proposal pre 147, 212 | **Hidden mount flags.** "Any /run line is tmpfs" accepted a `noexec` or stacked mount. A bind inherits the source mount's flags. A noexec `/run` would make S01app's exec fail (no app), and a noexec `/tmp` breaks the library mmap. | Topmost-mount check (`top_mount`): `run-noexec`, `lib-dir-noexec`, and `bound_ok` rejects a noexec bind (87, 150–154, 193–197, 272–276). | Fixed |
| F5d | Medium | proposal (no guard) | **Stale or late invocation.** A manual `S00cammy-select start` after boot overwrote the selected boot's status (acceptance evidence). Without a claim from this boot it could proceed while an app runs. | If any `/proc/*/comm` is `sdvcam`, it exits with `app-already-running`, writing no status and doing nothing else (199–202). | Fixed |
| F5e | Medium | handoff pre 309 | `verify_selected_boot(..., library_loaded=None)` passed: missing evidence counted as success. | `bridge-library-mapping-unproven`. | Fixed |
| F5f | Medium | proposal 111–118; WP4 §3 | Sync plus read-back was presented as a durable claim. Read-back reads the page cache, not JFFS2 flash. | The wording now says so. Real power-loss durability is a hardware gate (EA/E6). | Documented |
| F5g | Medium | S01app `ulimit -c unlimited`, `core_pattern /mnt/extsd/core.%e.%p` | A crash before S10udev mounts the card writes the core into the **internal overlay** directory `/mnt/extsd`. | E7 updated. Acceptance requires `durable_side_effects == []`. | Open → E7 |
| F4 | Medium | policy pre 57–59; WP4 §2 | E3 facts were not recorded. WP4 said live `/tmp` was unknown. | `policy.E3_LIVE_FACTS` holds the confirmed late-boot facts. E3 is `partial`. See §5. | Fixed (record) |
| F5h | Low | proposal; tests | Dash ⇄ Python equivalence is not stock-shell proof. The stock `/bin/sh` has no `command` builtin (E3), so it may be ash without `command` support, or hush. The host busybox has no hush to parse with. | Recorded as an E3 open item. The `tools-broken` model fault mirrors the stubs; it is not derived from them. | Open → E3 |
| F5i | Low | state rules | A partial or malformed `observed-*` blocks all later selection until maintenance. This fails closed. MD5 is the only on-camera integrity check. | Accepted and documented. | Accepted |

## 2. What changed (owned paths only)

- `native/activation/S00cammy-select.proposal`: `SELECT_GATE=blocked` (`select-blocked`
  happens before payload/card/claim/mount actions; RAM scratch, self-test and
  diagnostic status writes may still occur), the SHA256 path, `tools_ok`,
  the top-mount and noexec checks, the late-invocation guard, `set -C &&`, the tail
  fail-closed check and the exec-bit commit. Exit status is still 0. It parses under
  dash, busybox sh, bash and bash `--posix`.
- `tools/native_trial/`:
  - The Python model mirrors every change, including the shipped gate.
  - The fake bind now shares the source inode (hard link), so `chmod` through the
    mount behaves like a real file bind. New faults: `bind-noexec`,
    `bind-foreign-exec`, `tools-broken` and `awk-broken`.
  - New `review.py` and `s01app_model.py`.
  - `policy.py`: EA blocked, E3 partial with live facts, E1/E7 wording, WP2A constants.
  - The CLI adds `review-template`, `classify --review/--evidence-root`,
    `make-bundle --review` and `validate-bundle --source --evidence-root`.
    `proposal-patch` produces a blocked header with a placeholder allowlist only.
- `tools/native_acceptance/` (new): `spec`, `validate`, `fixtures`, `cli`.
- `tests/test_native_trial.py` was adapted and extended. `tests/test_native_acceptance.py` is new.
- Private: `private/ownership-program/wp5/` holds the checkpoint and
  `real-review/review.json`.
  - That record is an `offline` review of the real WP2A artifacts.
  - It binds the candidate, library and source SHA256s.
  - Its proofs are WP2A's candidate JSON, `audit-release.json` and `evidence-fixtures.json`.
  - Its provenance is the generator SHA256 `f48330d6…`, which matches the WP2A checkpoint.
  - Switching it to `reviewed` is the manager's E1 closure act; WP5 does not do it.

## 3. Activation mechanism: assessment and revised bootstrap design (for review)

**Why the bind overlay is ineligible now.** S01app runs whatever `/usr/bin/sdvcam`
shows, and S00 cannot stop S01app. Two selector outcomes, both reachable by fault,
break fail-closed behaviour. The sweep test proves this on the model:
`tf-detach-failed` (S01app's backup branch makes durable internal writes) and
`bind-unrestorable-executable` (unverified bytes run). F2d (the pre-claim hang or
reset loop) is a third. None of these can be excluded without hardware facts, so
the route stays blocked and is not "selected".

**Revised design R1 (bind overlay, hardened; not installed).**

1. **No init-namespace vfat mount.** Do all card I/O inside `unshare -m`, so that
   the namespace and the mount die with the process. S01app never sees a vfat line,
   and `tf-detach-failed` disappears. Needs `CONFIG_MNT_NS` and an `unshare` applet
   (E3b).
   - **Fallback:** mount with `-t msdos` if `/proc/filesystems` lists it (8.3 names
     on the card); `\<vfat\>` then cannot match.
   - **Otherwise:** no card access at S00.
2. **Exec-bit commit.** Implemented in the blocked proposal and the model, and
   tested. The residual is a mount that shows a foreign executable. Because a real
   file bind shares the inode, this needs a kernel/mount anomaly. Gate it with a
   post-bind `stat` inode check (E3b: `stat` applet).
3. **In-flight breadcrumb.** Before the first card access, write
   `inflight` (format, boot id) exclusively to internal state, and remove it after
   the decision. If a breadcrumb from another boot is found, the result is stock
   `selector-interrupted` until an operator ack. This bounds hang or reset loops to
   one boot, at a cost of 2 JFFS2 writes per enabled boot.
4. **Time budget.** Measure the S00 duration in observe mode (E4) against the
   bootloader watchdog. A self-timeout (a background `sleep; kill`) waits until E3b
   proves the applets. Real signals were not used here.
5. **Media and power side effects** stay with the stock app. The WP2A stub falls
   back to the original `bl 0xA4294` if `dlopen`, `dlsym` or attach fails. A
   missing or bad library therefore degrades to stock-equivalent behaviour, but the
   candidate bytes still run.

**Alternatives compared.**

| Route | Unverified exec possible | Internal change | Blocker |
| --- | --- | --- | --- |
| (a) Late handoff (WP4 handoff) | No (preflight refuses) | None | L1–L5 open; backend unavailable |
| (b) Reviewed S01app replacement with verify-at-exec: one `exec` of the RAM copy after verification, else stock | No: the decision and the exec are one script | Replaces stock S01app text (larger E2) | Changes the stock init text the plan wanted unchanged |
| (c) Bind overlay + R1 | Only via a mount anomaly | 3 files (E2) | EA, E3b, E4 |
| (d) Matching-SDK backend, no native patch | n/a | None | Separate evaluation (execution plan) |

Recommendation: keep EA blocked. Collect the E3b read-only facts first:
`/proc/filesystems`, presence of `unshare`/`stat`, the stock shell's identity, and
whether it supports `set -C`, `${#v}` and `$(( ))`. Then choose between (b) and
(c)+R1 in a reviewed change. The current proposal remains the review artifact.

## 4. Classification of the real WP2A build (local, read-only)

Command: `bundle.classify(<wp2a candidate>, <stock>, status-only, <wp2a library>, reproduce=True)`.

| Item | Value |
| --- | --- |
| Source (stock sdvcam) | SHA256 `8771961f…62b0ec`, MD5 `8689ce53…`, 11335176 B |
| Stock S01app | SHA256 `3327c3a9…a72136`, MD5 `daeb505e…` |
| Candidate | SHA256 `a9dedc98…a9caba0`, MD5 `ef95bc8c0f9004cf54aa338ddfa7ad61`, 11335176 B |
| Library | SHA256 `db34fa03…1e55065`, MD5 `9249d39eb3755c4598ec15f10586ddfd`, 21456 B, ARM EABI5 32-bit shared object |
| RAM path | `/tmp/cammy-native-status/db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065/libcammy_native_status.so`, embedded exactly once |
| Structural | `status-only`, `structural_ok`. Changes only at the bl (0x21DB0), stub bytes 0x8D2E40–0x8D2FA8 and LOAD sizes (0x8D2FA8). Connect sites untouched. No stray cave path. |
| Generator reproduction | byte-identical (`expected_sha256 == candidate_sha256`) |
| Without a review record | `trust: structural-only`, `eligible: false` |
| With `wp5/real-review/review.json` (offline) | `review-bound`, `eligible_for_staging: true`, `local_only: true`, `eligible: false`, `e1: open`, `activation_eligible: false` |
| Old cfee media candidate | `media-connect-triggered`, ineligible, even with a forged `reviewed` record |

## 5. Live facts (E3 partial; current late-boot metadata only)

Source: `private/ownership-program/manager-e3-live.json` (read-only, uptime about
9120 s). Confirmed:

- S01app SHA256 `3327c3a9…` and sdvcam `8771961f…` equal stock.
- `/tmp` and `/run` are tmpfs `rw,relatime`, without noexec.
- The card is `/dev/mmcblk0`, with **no** `mmcblk0p1`, mounted vfat **rw** at `/mnt/extsd` after boot.
- Direct `md5sum` works (S01app `daeb505e…`).
- MemAvailable is 421304 kB.

**Not verified:**

- The busybox applet list: self-dispatch printed "applet not found".
- Shell command discovery: `command` is not a builtin, so the shell's identity is unknown.
- `mount -o bind`, `umount -l`, `tail -c`, `awk -v`, and `grep \<\>`.
- S00-time card and mount availability (MMC probing is asynchronous).
- Selector duration, bootloader watchdog and nowayout.
- MemAvailable at S00.

These remain hardware gates and are not marked verified.

## 6. Acceptance evidence harness (`tools/native_acceptance`)

`python3 tools/native_acceptance spec | validate REPORT [--evidence-root D]
[--prerequisite R] [--now T] [--out F] | template --profile P --out F`. It has no
hardware backend: a test asserts that its source contains no subprocess, socket,
adb, ioctl, kill, system or urllib use. Writes go only under
`build/native-acceptance/` or the temp dir.

**Stages, in order:** planned, staged and structurally-classified, then
abi-proved (all host evidence), then selected-boot, working-frame (video only),
cleanup and stock-restore (hardware evidence).

**Evidence classes:**

| Class | Counts as |
| --- | --- |
| `hardware` | Real evidence for a hardware stage |
| `host`, `static`, `offline-arm` | Real evidence for a host stage |
| `simulated`, `fixture` | LOCAL only |
| A host class on a hardware stage | LOCAL |

**Per-stage status:** PASS, PASS_LOCAL, MISSING, UNKNOWN, FAIL, MISMATCH
(identity), WRONG_BOOT, STALE (outside the window, older than 14 days, or in the
future) and INVALID. When several items cover one stage, a failure outranks any
number of passes.

**Verdicts:**

| Verdict | When |
| --- | --- |
| REJECTED | Any error or blocking stage |
| INCOMPLETE | A stage is missing or unknown, or the video prerequisite is unverified |
| LOCAL_ONLY | A fixture report, any local item, a local-only bundle, a fixture or offline review, or artifacts not re-hashed |
| CANCELLED_CLEAN / CANCELLED_CLEANUP_PENDING | The trial was cancelled |
| HARDWARE_EVIDENCE_COMPLETE | Only when everything is consistent, current and hardware-class with re-hashed artifacts |

HARDWARE_EVIDENCE_COMPLETE is still `approval: none`, because the manager reviews
and closes gates. The CLI also caps any report outside `private/` at LOCAL_ONLY,
so a relabelled fixture in `build/` or `/tmp` cannot present as hardware.

**Identities required:**

| Identity | Rule |
| --- | --- |
| source | Equals stock (SHA256, MD5, size) |
| s01app | Equals stock (SHA256, MD5) |
| candidate | SHA256, MD5 and size; differs from stock |
| library | SHA256, MD5 and size; `ram_path` must be the manager SHA256 path |
| Every evidence item | Binds the candidate and library SHA256 |

The selected app is identified by `exe_sha256` of `/proc/<pid>/exe`. A bind keeps
the `/usr/bin/sdvcam` path and comm, so the name and path are ignored, and
"name/path only" is INVALID.

**Selection authorization versus media lease:**

- `selection_authorization` has exactly attempt, allowlist SHA256, approval ref and
  scope `select-one-boot`. Any extra key, such as a lease, token or generation,
  causes rejection.
- `media_lease` must be null for status-only.
- For video, it must be issued in the **selected boot**, with its own `lease_ref`
  (not the selection approval), a generation that matches the frames, and frames
  inside its window.

**Boots:** the selected, post-attempt and card-removed boots must be distinct UUIDs.
Each stage checks its own boot.

**Cancellation:**

| Situation | Result |
| --- | --- |
| Cancelled before any hardware stage | CANCELLED_CLEAN |
| Cancelled after selected-boot, with cleanup and stock-restore passing | CANCELLED_CLEAN |
| Cancelled after selected-boot, without them | CANCELLED_CLEANUP_PENDING |
| Evidence other than cleanup or restore captured after cancellation | REJECTED |

A cancelled trial never yields a hardware pass.

## 7. Status-only acceptance spec (first hardware attempt, after EA/E1/E2/E3/E4)

1. **selected-boot** (selected boot ID):
   - The selector status is `candidate/selected/select`, with the authorized attempt and this boot.
   - **Exactly one** native app, whose executable bytes equal the candidate SHA256 (not stock).
   - The library is mapped in that PID at the manager path, and its bytes match.
   - `/dev/watchdog` is held by that PID for at least 600 s with 0 resets and an unchanged boot ID.
   - Home Wi-Fi is unchanged and the portal is reachable. The LCD shows its normal view.
   - Bridge hello comes from `backend_kind=2` (native) with this boot ID,
     `hardware_actions_enabled=false` and every physical capability unavailable.
   - `media.sessions_started == 0`.
2. **cleanup** (selected boot): the bridge session is closed, there are no orphan
   processes or unexpected listeners, media sessions are 0, and the disarm (arm
   removed or `enabled` removed) is verified.
3. **stock-restore:**
   - **Post-attempt boot:** stock/`post-attempt-boot`, with an `observed-<id>` record
     naming that boot and exactly one stock app; LCD and Wi-Fi are confirmed.
   - **Card-removed boot:** stock or silent, exactly one stock app, LCD and Wi-Fi confirmed.
   - **Ack:** it quotes the post-attempt boot.
   - `durable_side_effects == []`: no internal core, no `/data/app_md5` change, no internal `.sdvcam`.
4. **Host stages:**
   - planned: plan ref, runbook SHA256 and rollback ref.
   - staged: the bundle manifest; not installed; `local_only` false.
   - structural: status-only, no connect sites.
   - abi: a `reviewed` review with NATIVE-ABI §7 proofs 1–5.

## 8. Later video acceptance spec (one attempt; needs E8 lease enforcement)

- **Prerequisite:** a status-only report from this same flow that validated as
  HARDWARE_EVIDENCE_COMPLETE, bound by its SHA256.
- **Build:**
  - structural class `media-lease-gated`;
  - ABI proofs 1–5 plus `dispatch_fault_suite` and `lease_enforcement`;
  - connect-trigger sites untouched.
- **working-frame (selected boot, current-boot lease):**
  - Distinct, ordered milestones: event-connected < listener-ready < SDP-ready < first
    decoded frame. Frame readiness is separate from listener readiness.
  - At least 2 decoded frames of a **changing known target**, with distinct content
    hashes, strictly increasing timestamps and dimensions.
  - Every frame is inside the lease window, and the latest frame is no older than 2 s.
  - The stock source hash is retained.
- **cleanup:**
  - Stop: the queued `ack_ms` and the confirmed hardware cleanup are reported separately.
  - Stop, lease expiry and controller disconnect each reach confirmed cleanup
    within 5 s, with the encoder released and no late command after the lease ends.
  - No orphan session or listener remains, and the disarm is verified.
- **stock-restore:** as in §7. Separately armed attempts for Stop, expiry and
  disconnect are separate reports.

## 9. Verification performed (local)

| Command | Result |
| --- | --- |
| `python3 -m unittest tests.test_native_trial` | **94 tests OK**, 0 skipped. The 75 WP4 tests were adapted to the SHA256 contract and blocked gate. The 8 bundle tests became 13. Added: 10 review-fix tests (shell ⇄ model) and 4 WP2A integration tests, which use the real private artifacts read-only. |
| `python3 -m unittest tests.test_native_acceptance` | **31 tests OK** |
| Both together | 125 tests OK in about 12.6 s |
| Validator mutation check (scratch copy, 8 mutants) | 8/8 killed |
| `dash -n`, `busybox sh -n` (BusyBox 1.37.0, parse only), `bash -n`, `bash --posix -n` on the proposal | All OK. No shellcheck available (not installed). No hush on the host. |
| `native_trial status/gates/simulate/proposal-patch` | Activation blocked. Simulate: shipped `select-blocked`; design gate `selected, post-attempt-boot, attempt-replayed`. Gates: EA blocked, E3 partial, others open, E8 blocked. Patch written to `build/native-activation/wp5-blocked-bootstrap-proposal.patch` (placeholder allowlist). The stale pre-review `bootstrap-proposal.patch` was removed. |
| `native_acceptance template` + `validate --now` | Fixture → LOCAL_ONLY, exit 4. Without `--now`, the future-dated fixture correctly gives STALE → REJECTED. |

**Not run:** the other packages' suites (WP2/WP2A/WP3 and others), because they may
write their own build outputs while the manager integrates. **No full-suite claim
is made.** The legacy status-only bridge protocol and `native/bridge` were not
touched. There is still no native hardware backend: `UnavailableNativeControl` is
unchanged.

## 10. Remaining gates and the next integration dependency

| Gate | Status | Notes |
| --- | --- | --- |
| EA | blocked | Fail-closed activation mechanism; §3 |
| E1 | open | Build proof available; manager closure needed. The `offline` review is LOCAL_ONLY. |
| E2 | open | — |
| E3 | partial | Plus the E3b items in §3 |
| E4 | open | — |
| E5 | open | — |
| E6 | open | — |
| E7 | open | Internal-overlay core path |
| E8 | blocked | — |
| L1–L5 | open | — |

**Next integration dependency:**

- The manager integrates these owned paths.
- Then a reviewed decision on the activation route, (b) or (c)+R1, informed by the
  E3b read-only facts.
- Only after that may E1 closure (`reviewed` evidence class) and an E2 internal
  change be considered.
- The acceptance harness is ready to receive real evidence under
  `private/ownership-program/`.
