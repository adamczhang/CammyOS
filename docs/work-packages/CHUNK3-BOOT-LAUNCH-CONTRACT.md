# Chunk 3: S01app boot-launch contract — 2026-11-14

**Offline design only; manager-reviewed corrections included.** This replaces the
EA-blocked bind-overlay direction with explicit launch selection. It closes no
hardware gate. See [WP5 findings](WP5-ACCEPTANCE.md) and the
[execution plan](../archive/HARDWARE-OWNERSHIP-EXECUTION.md).

## 1. Exact inputs

The saved stock `S01app` is **156 lines, 4,246 bytes**, SHA256
`3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136`.
Its five launch sites are lines **91, 99, 101, 114, 118**. The generator must
refuse other input bytes before emitting any proposal.

The [loader proof](../native-bridge/LOADER-PROOF.md) pins stock `sdvcam`
(`8771961f…62b0ec`), status candidate (`a9dedc98…a9caba0`, 11,335,176 bytes)
and library (`db34fa03…1e55065`, 21,456 bytes). Use full digests from that proof,
not these abbreviations. No media redirects or physical capabilities are enabled.

## 2. Transformation and one launch authority

Preserve stock VM settings, environment, `/tmp/data`, power/MMIO setup and
`stop()` text. Stop/restart are not trial recovery mechanisms and are never
invoked by this workflow. Replace all five launch sites with one helper using a
single preparation decision, made after startup setup and before the backup
branch. An exclusive per-boot RAM launch stamp prevents repeated helper/start
invocations from executing another app. Stamp failure means no launch.

Default `SELECT_GATE=blocked`, missing/disabled config and unsupported modes
skip TF I/O and selector flash writes. They select verified stock. Chunk 4 never
selects a candidate. Backup behavior is intentionally tightened below; do not
claim equivalence for the unsafe legacy condition.

Build-time input identity and runtime replacement identity are different. The
reviewed replacement hash belongs in an external allowlist/manifest. Before any
launch, validate runtime stock identity. If stock cannot be verified, launch
nothing and report recovery-required; never execute mismatched bytes as fallback.
Invalid candidate configuration falls back only when stock remains verified.

## 3. TF and filesystem boundaries

Never mount over `/usr/bin/sdvcam`. Reject trial probing during `.firstboot`.
Allow legacy backup/firstboot writes only when `/mnt/extsd` itself is a verified
normal TF/vfat mount and no private staging mount remains. Any-vfat detection is
removed: a failed detach at `/run/cammy-select/tf` must not authorize backup writes
into the internal overlay. Firstboot behavior on a valid normal card remains
stock. Private mounts never qualify. Do not assume `unshare`, namespaces or
`stat` support.

Stage candidate at `/run/cammy-select/sdvcam` (0500), library at
`/tmp/cammy-native-status/<full-library-sha256>/libcammy_native_status.so` (0444),
and socket directory `/tmp/cammy-bridge`; directories are root-owned 0700,
non-symlink paths on executable tmpfs. Preparation failure cleans staged payloads
and selects verified stock. Shell verification followed by exec is not atomic;
the design trusts root init as sole writer of these private RAM paths. On-camera
MD5+size is weak mismatch detection, not authenticated SHA256. The loader does
not verify the digest in its path.

## 4. Attempts and interrupted preparation

Before device probing, mount or card reads, exclusively write and sync an
internal `inflight` record bound to this boot. Failure refuses TF work. Old or
malformed state forces stock without another probe until maintenance/ack.
Before candidate launch, persist `claimed-<attempt>` and remove `inflight` only
after the claim is confirmed. The next boot records `observed-<attempt>` and
selects stock; rearm requires a new bounded attempt and ack quoting that boot.

A surviving breadcrumb prevents repeated risky I/O. **Power-loss durability is
unproven:** sync/read-back reaches page cache; do not promise one-boot hang
recovery until persistence is measured. No hard card-I/O timeout or bootloader
watchdog budget is established. State mutations are intentional flash writes;
"no unexpected durable side effects" excludes those documented records.

## 5. Exec, readiness and core policy

Preserve stock core settings. Candidate execution uses a subshell with
`ulimit -c 0 && exec ...`; failure to disable cores prevents candidate exec.
A consumed claim is selection, not proof of process start. Record launch
attempt, observed PID/identity and bridge readiness separately. After any
candidate exec attempt, never start a second app from exit status or a timeout;
failed/uncertain startup needs a stock next boot. Stub attach failure stays within
the same candidate process and follows its original call. No warm recovery is
claimed.

## 6. Transition/fault contract

| Condition | App action | State / effects |
| --- | --- | --- |
| Disabled, missing/invalid config; stock verified | Stock once | No TF probe or selector flash writes; tightened legacy backup gate applies |
| Wrong generator input | Emit nothing | No target execution or changes |
| Runtime stock mismatch/unverifiable, or launch stamp failure | No launch | Recovery-required; no unverified fallback |
| Firstboot active | Verified stock once | No private TF probe; legitimate normal-card firstboot behavior retained |
| Prior/malformed inflight or claim | Verified stock once | No repeated probe; maintenance/ack or observed-boot bookkeeping |
| Missing/corrupt TF, invalid arm, copy/hash failure | Verified stock once | Document inflight create/removal; clean RAM payloads |
| Private TF detach fails | Verified stock once | Recovery-required; backup suppressed while residue remains |
| Observe succeeds | Verified stock once | Clean payloads; no candidate execution |
| Select succeeds | Candidate exec attempted once | Claim consumed; core limit zero; PID/readiness still need observation |
| Candidate exec/core-limit failure, crash or uncertain startup | No second app | Claim remains consumed; next boot stock; no automatic retry |
| Next boot after attempt, including card removed | Verified stock once | Record observed boot before any rearm |
| Repeated invocation in same boot | No additional exec | Preserve launch stamp; do not overwrite accepted launch evidence |

## 7. Remaining gates and next chunks

Internal changes require a reviewed replacement `S01app`, allowlist/manifest,
enable file and state directory plus restore procedure (E2). Early shell/tools,
mount identity/options, card timing, available RAM and watchdog budget remain E3/E4;
persistence, normal view/Wi-Fi, one-owner operation and stock restore require
hardware evidence (E5/E6). Candidate core policy needs verification (E7).
EA/E1 remain review gates; E8/media and late-handoff L1–L5 remain blocked/open.

Next: **chunk 4** generates an inactive proposal and proves stock fallback locally;
**chunk 5** adds one-shot selection fixtures; **chunk 6** prepares exact trial and
rollback artifacts for review. Installation remains outside these local chunks.
