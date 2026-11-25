# Chunk 5: one-shot selection fixtures — 2026-11-14

Base: `289d5b4`. **Local prototype; independent review complete under the declared trust model.** The public
helper remains mode 0644 with `SELECT_GATE=blocked`; no camera/card/init change,
installer, vendor executable execution or hardware gate closure occurred.
The [chunk 3 contract](CHUNK3-BOOT-LAUNCH-CONTRACT.md) and
[compatibility inventory](../CAMERA-COMPATIBILITY-2026-11-14.md) remain authoritative.

## Implemented slice

The existing `tools/boot_launch/` generator, model and relocated dash harness now
exercise one-shot status-only selection. The same proposed shell logic is tested
with `chunk5-fixture-open` only in synthetic, ineligible copies. The public
emitter refuses an enabled helper and records logic-present/live-routing-disabled
separately. The old S00 bind-overlay route remains EA-blocked.

The helper uses `/etc/cammy/native-select` for persistent records, private
`/run/cammy-select/sdvcam` for the staged app, and the SHA256-scoped library path
from the [loader proof](../native-bridge/LOADER-PROOF.md). It reuses the strict
`native_trial.records` formats. No `status-only-v1` class or alternative state
path is introduced; the supported class is `status-only`.

- Inspect persistent inflight/claim/observed state before card access.
- Write inflight before probing/mounting, validate arm/allowlist/ack and payloads,
  stage into checked private RAM directories, then detach the temporary TF mount.
- Persist the claim before clearing inflight and attempting candidate execution.
- Keep candidate selection, exec attempt and observed readiness separate. Core
  limit zero and the per-boot stamp guard execution; never start a second app
  following a candidate exec attempt or uncertain failure.
- A subsequent modeled boot selects verified stock and records its observation
  before rearm. Malformed/replayed state and wrong acknowledgments refuse selection.
- Failure to detach refuses launch and suppresses stock backup writes. Other
  pre-exec failures can fall back only to independently verified stock.

The shipped blocked gate still takes the stock-only path: no TF probe or durable
selector records for missing/disabled/invalid or attempt-to-enable configuration.

## Manager findings and fixes

The original 47 tests passed, but review found two untested gaps:

1. Record write/removal ignored failed `sync` return codes. Those failures now
   refuse further selection, with focused failures at inflight, claim and clear
   boundaries. A successful sync is still not proof of flash persistence.
2. The allowlist parsed `s01app_md5` without comparing it to the running script.
   It now checks `INITFILE=/etc/init.d/S01app` before TF access. This field binds
   the reviewed **replacement**, not the pristine generator input. Fixtures use a
   separate synthetic init file; packaging must calculate the replacement digest
   after generating its final bytes and put that digest in the external allowlist.

The manager also restored the existing cleanup assertions alongside the new
sync-failure cases; intentional retained records are checked explicitly.

## Verification

```sh
python3 -m unittest tests.test_boot_launch tests.test_boot_selection
```

The pre-review manager run passed 48 tests in 52.080s. Three focused regression
methods also passed after the independent-review follow-ups; final full-suite
verification passed **367 tests**, including 50 boot-launch/selection tests. Three
pre-existing HTTPError ResourceWarnings remain, with no test failures. Evidence consists
of synthetic app/library files, intercepted mount/umount/sync/copy/chmod operations,
relocated dash execution, a Python reference model and parse-only shell checks.
Stock power/MMIO startup and original/full patched sdvcam were never executed.
Private classifier tests read saved vendor artifacts only. Public metadata/hashes
must be derived from the final generated manifest, not copied from intermediate
outputs; earlier delegate summary paths/line counts are not acceptance evidence.

Coverage includes blocked routing, selection/observe, same-boot repeat refusal,
next-boot stock with/without a card, rearm/ack binding, payload/record/class faults,
private directory/mount hazards, cleanup, core/claim failures, sync errors and
modeled interruption at critical boundaries. The fixtures assume explicitly
recorded state survives their modeled boot; they do not prove physical power-loss
behavior.

## Remaining gates and next step

Early-boot exclusive-create semantics, card-I/O/watchdog timing, core-limit setter
behavior, persistence and stock restore remain unproved on hardware. The USB
inventory accepted `set -C` without file creation and queried core limits; it did
not test those write/setter behaviors. Effective watchdog timeout/nowayout is
unknown. No physical adapter or media lease is enabled.

The [independent review](CHUNK5-INDEPENDENT-REVIEW.md) found no additional blocking
bug under the stated model. Manager follow-ups clarify preparation versus launch
evidence, observe all prior claims, validate private state permissions and cover
read-back corruption. Freeze the verified integration commit for chunk 6's **offline review package** and rollback runbook. That package
must retain blocked activation, distinguish pending hardware facts from local
proofs, and include no automatic install or gate-opening action.
