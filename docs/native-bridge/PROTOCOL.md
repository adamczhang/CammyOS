# Native status bridge protocol 1.0 (WP2, 2026-11-14)

**Status-only local IPC.** Version 1.0 carries hello, status and capabilities, and
nothing else. It defines no media, audio, motor, display, watchdog or lifecycle
action. A working bridge with the fake backend proves only that the protocol and
socket lifecycle work. It says nothing about camera, native-app or hardware
readiness. Implementation: [`native/bridge/`](../../native/bridge/). Wire constants:
[`cammy_bridge_proto.h`](../../native/bridge/cammy_bridge_proto.h). Native-side
contract (WP1): [NATIVE-ABI.md](NATIVE-ABI.md). Results so far:
[WP2 report](../work-packages/WP2-STATUS-BRIDGE.md).

## 1. Transport and socket lifecycle

| Item | Rule |
| --- | --- |
| Socket | `AF_UNIX`, `SOCK_SEQPACKET`. One request or reply per packet; the kernel preserves boundaries. Stream and datagram clients cannot connect (`EPROTOTYPE`). |
| Path | Absolute, shorter than `sun_path` (108 bytes), not directly under `/`. Proposed camera path: `/tmp/cammy-bridge/bridge.sock`. That `/tmp` is RAM-backed on the camera is **not yet verified**. |
| Directory | The bridge creates the parent if it is missing (one level, mode 0700). It refuses unless the parent is a real directory (not a symlink), owned by the bridge's effective UID, with mode `& 0077 == 0`. Ancestors are trusted to the operator. |
| Socket mode | 0600, set immediately after bind. The private parent closes the bind-to-chmod window. |
| Instance lock | `<path>.lock`, a regular 0600 file created with `O_NOFOLLOW`, held with `flock(LOCK_EX|LOCK_NB)` for the bridge's lifetime. If another holder has it, start-up fails with `already_running`. The file is never unlinked, so lock identity cannot race. |
| Stale socket | Under the lock, an existing socket owned by the same UID that refuses connections is removed. A regular file, a foreign-owned node, or a live listener is **never** removed (`path_occupied`). |
| Exit | The socket path is unlinked only if its device and inode still match the socket this instance bound. A replacement is left alone. A crash leaves a stale path that the next start reclaims. |
| Peer check | `SO_PEERCRED` UID must equal the configured UID (default: the bridge's EUID). Otherwise the bridge sends one `peer_denied` reply with instance, boot ID, backend kind and clock zeroed, then closes. |
| Connections | 1–8 concurrent, default 4. Listen backlog = that limit. Extra accepted peers get `busy` and are closed. A non-blocking client `connect()` may also fail with `EAGAIN` while the backlog is full. Callers treat both as busy and back off; neither means the bridge is down. |
| Fairness | At most 4 packets per connection per loop step. Requests are answered in order, one reply each. No request is queued inside the bridge beyond the one being processed. Unread packets stay in the kernel's bounded socket queue. |
| Idle | A connection with no packet for the idle timeout (100–60000 ms, default 10000) is closed. |
| Slow reader | If a reply cannot be queued without blocking, the connection is closed (`slow_disconnects`). The bridge never waits on a peer. |
| Error budget | After 8 rejected (well-framed) requests on one connection, the eighth reply is marked closing and the connection closes. |
| Empty packet | Treated as end of stream: closed with no reply. |
| Ancillary data | Any control data (e.g. passed descriptors) gets `bad_ancillary`, then close. The bridge provides no control buffer, so the kernel discards passed descriptors and none are retained. |

All descriptors are `O_CLOEXEC`/`SOCK_CLOEXEC`. Replies use `MSG_NOSIGNAL|MSG_DONTWAIT`.

## 2. Frames

All integers are little-endian. Offsets are in bytes. A request is **exactly 64 bytes**.

**Request (64 bytes)**

| Off | Size | Field | Protocol 1.0 rule |
| --- | --- | --- | --- |
| 0 | 4 | magic | `CBRQ` |
| 4 | 2 | major | 1 |
| 6 | 2 | minor | ≤ 0 (bridge's minor) |
| 8 | 2 | header_size | 64 |
| 10 | 2 | command | 1 hello, 2 status, 3 capabilities |
| 12 | 4 | request_id | non-zero; strictly increasing per connection |
| 16 | 4 | flags | 0 |
| 20 | 4 | reserved | 0 |
| 24 | 8 | expect_instance | 0 = any; else must equal the bridge instance |
| 32 | 16 | boot_id | zero = unspecified; else must equal the bridge's boot ID |
| 48 | 8 | deadline_mono_ms | 0 = none; else absolute `CLOCK_MONOTONIC` ms (§4) |
| 56 | 8 | owner_generation | 0 (stage-3 lease generation) |

**Response header (64 bytes), then `payload_size` bytes. Total ≤ 256.**

| Off | Size | Field | Notes |
| --- | --- | --- | --- |
| 0 | 4 | magic | `CBRS` |
| 4 | 2+2 | major, minor | 1, 0 |
| 8 | 2 | header_size | 64 |
| 10 | 2 | command | echoed when the request had valid magic and ≥ 16 bytes, else 0 |
| 12 | 4 | request_id | same echo rule |
| 16 | 2 | status | §5 |
| 18 | 2 | backend_kind | 0 none (identity withheld), 1 fake, 2 native |
| 20 | 4 | payload_size | exact length of what follows |
| 24 | 8 | instance | random per bridge start (`getrandom`, with a time/PID fallback); never 0 |
| 32 | 16 | boot_id | `/proc/sys/kernel/random/boot_id`; zero if unavailable |
| 48 | 8 | mono_now_ms | bridge `CLOCK_MONOTONIC` ms |
| 56 | 8 | owner_generation | always 0 in 1.0 (no owner exists) |

**Error payload (16 bytes; every status ≠ 0):** `u16 supported_major`, `u16
supported_minor`, `u16 field_offset` (first offending request byte, `0xFFFF` = none),
`u16 disposition` (0 keep, 1 closing), `u32 request_size` (64), `u32
errors_remaining` (0 when closing).

**Hello payload (48 bytes):** `u16 proto_major`, `u16 proto_minor_max`, `u16
max_clients`, `u16 max_burst`, `u32 request_size`, `u32 max_frame`, `u32
idle_timeout_ms`, `u32 max_deadline_horizon_ms`, `u32 command_mask` (bit *n* =
command *n*; 1.0 = `0x0E`), `u32 error_budget`, `char bridge_version[16]`
(NUL-padded ASCII).

**Status payload (64 bytes):** `u16 backend_kind`, `u16 backend_state`, `u16
backend_reason`, `u16 flags` (bit0 boot ID available, bit1 hardware actions enabled
(never set by 1.0), bit2 owner present (never set by 1.0)), `u64 start_mono_ms`,
`u64 uptime_ms`, then u32 counters: `active_clients`, `connections_accepted`,
`rejected_busy`, `rejected_peer`, `requests_ok`, `requests_rejected`,
`framing_errors`, `idle_disconnects`, `slow_disconnects`, `peer_disconnects`.

**Capabilities payload (8 + 7×8 bytes):** `u16 count` (7), `u16 entry_size` (8),
`u32 reserved`, then entries: `u16 id`, `u8 state`, `u8 reason`, `u8 flags` (bit0
physical), 3 reserved bytes.

## 3. Validation order and disposition

The bridge checks a packet in this order and returns the **first** failure:

| # | Check | Status | Connection |
| --- | --- | --- | --- |
| 1 | Control data present | `bad_ancillary` (18) | closes |
| 2 | Longer than 256 bytes (truncated) | `too_large` (3) | closes |
| 3 | Shorter than 8 bytes | `bad_length` (2) | closes |
| 4 | Magic ≠ `CBRQ` | `bad_magic` (1), no echo | closes |
| 5 | major ≠ 1, then minor > 0 | `unsupported_version` (4), offset 4 / 6 | kept |
| 6 | Length ≠ 64, then header_size ≠ 64 | `bad_length` (2), offset — / 8 | closes |
| 7 | request_id 0 or ≤ last on this connection | `bad_request_id` (6), offset 12 | kept |
| 8 | flags ≠ 0, then reserved ≠ 0 | `unknown_field` (5), offset 16 / 20 | kept |
| 9 | command in 0x10–0x1F | `mutation_disabled` (8), offset 10 | kept |
| 10 | command not 1–3 | `unknown_command` (7), offset 10 | kept |
| 11 | owner_generation ≠ 0 | `owner_unsupported` (9), offset 56 | kept |
| 12 | expect_instance ≠ 0 and ≠ instance | `stale_instance` (10), offset 24 | kept |
| 13 | boot_id non-zero and (unknown or different) | `boot_mismatch` (11), offset 32 | kept |
| 14 | deadline set without a request boot_id, or bridge boot ID unknown | `clock_domain` (12), offset 48 | kept |
| 15 | now ≥ deadline | `expired` (13), offset 48 | kept |
| 16 | deadline − now > 60000 ms | `bad_deadline` (14), offset 48 | kept |
| 17 | Dispatch finished at or after the deadline | `expired` (13); the result is discarded | kept |

Version is judged before length, so a future layout is reported as unsupported, not
malformed. A request that passes check 7 consumes its ID even if a later check
rejects it. "Kept" rejections count toward the error budget.

## 4. Field semantics

- **request_id:** the replay and duplicate guard, scoped to one connection. When
  `0xFFFFFFFF` has been used, the caller reconnects. Stage 3 is expected to build
  idempotent mutating requests on this rule.
- **instance:** changes on every bridge start. A caller that saw instance A and now
  sees B must treat all earlier state as void. `expect_instance` lets a request fail
  instead of acting on a restarted bridge.
- **boot_id + deadline:** deadlines are absolute `CLOCK_MONOTONIC` milliseconds,
  which are comparable only within one boot. A deadline is therefore accepted only
  when the request also carries the matching boot ID. The horizon is 60 s. A result
  produced after its deadline is never delivered as success.
- **owner_generation:** reserved for the stage-3 lease. In 1.0 it must be 0, the
  bridge always reports 0, and the owner-present flag is never set.

## 5. Enumerations

Status: 0 ok, 1 bad_magic, 2 bad_length, 3 too_large, 4 unsupported_version, 5
unknown_field, 6 bad_request_id, 7 unknown_command, 8 mutation_disabled, 9
owner_unsupported, 10 stale_instance, 11 boot_mismatch, 12 clock_domain, 13
expired, 14 bad_deadline, 15 busy, 16 peer_denied, 17 internal (reserved), 18
bad_ancillary.

Backend state: 0 unavailable, 1 ready, 2 error. Backend reason: 0 none, 1
fake_backend, 2 native_table_absent, 3 native_table_rejected, 4 init_failed, 5
query_failed, 6 native_exiting, 7 native_identity_mismatch.

Capability IDs: 1 status_ipc (not physical), 2 video_stream, 3 audio_capture, 4
audio_playback, 5 gimbal, 6 display, 7 lifecycle_handoff. State: 0 unavailable, 1
available, 2 simulated (reserved; never sent by 1.0). Reason: 0 none, 1
fake_backend, 2 native_table_absent, 3 not_in_protocol_v1, 4 backend_error, 5
native_table_rejected.

## 6. Capability honesty rules (enforced by the core, not by backends)

1. `status_ipc` is the only capability reported available, because the bridge is
   answering. It is flagged non-physical.
2. Protocol 1.0 reports **no physical capability as available, whatever any backend
   table says**. With a valid native table the reason is `not_in_protocol_v1`. A
   failed, out-of-range or `backend_error` answer gives `backend_error`.
3. Fake capability claims are never consulted. Every physical entry is unavailable
   with reason `fake_backend`. Fake status always carries reason `fake_backend`, and
   every response header carries `backend_kind`.
4. The operator chooses the backend kind explicitly (`--backend fake|native`; there
   is no default). A table whose own kind differs is rejected, never relabeled.
5. A native `ready` must carry reason `none`. Any other combination, or a value out
   of range, is reported as `error`/`query_failed`.

## 7. Versioning and unknown-field policy

- **Requests are must-understand.** A request with a newer minor, any non-zero
  flag, reserved or owner bits, or a different major is rejected with the offending
  offset. It is never partly honored. A safety field silently ignored (lease
  generation, deadline) would be worse than a refusal.
- The request layout is frozen for major 1. A later minor may give meaning to
  `flags`/`reserved` bits and new command codes. A bridge accepts requests up to its
  own `proto_minor_max`, which it announces in hello.
- **Responses are append-only within major 1.** Clients use `payload_size`. They
  treat an unknown capability state or reason, or an unknown backend state, as
  *unavailable*. They treat an unknown status code as a failure. New enum values may
  be added in a minor release; existing meanings never change.

## 8. Stage-3 migration (reserved, not implemented)

| Need | Hook already in 1.0 |
| --- | --- |
| Lease ownership | `owner_generation` in request and response; stale generations will be rejected like `stale_instance`. |
| Native deadlines | Absolute same-boot `deadline_mono_ms`; the post-dispatch expiry check is already enforced. |
| Restart safety | `instance` + `expect_instance`. |
| Duplicate start/stop | Per-connection strictly increasing `request_id`. |
| Mutating commands | 0x10–0x1F reserved and rejected today. Proposed: 0x10 media_start, 0x11 media_stop, 0x12 media_status. |
| Stop semantics | Must report "stop queued/acknowledged" and "cleanup confirmed" as separate results. 1.0 defines neither. |

## 9. Backend table and embedding contract

`struct cammy_bridge_backend` (ABI 1, [header](../../native/bridge/cammy_bridge_backend.h))
has these fields: `abi_version`, `struct_size`, `kind`, `reserved` = 0, `name`,
`ctx`, and the callbacks `init`, `query_status`, `query_capabilities`, `fini`. The
core rejects a table with a wrong ABI or size, a kind mismatch, non-zero reserved,
or any null member. It calls `init` only after the listener is up, and `fini` only
if `init` succeeded. Callbacks run on the stepping thread and must not block or
touch hardware. In WP2, `cammy_bridge_native_backend()` returns NULL, so a
`--backend native` bridge reports `unavailable`/`native_table_absent`.

The core is embeddable. It installs no signal handlers, never changes umask, does
no stdio and never calls exit. `cammy_bridge_step()` performs no allocation (the
only allocation is in `open`). `cammy_bridge_wake()` is thread-safe and
async-signal-safe, and bounds how long a detach join waits. Call
`cammy_bridge_close()` only after the stepping thread stops; if the thread was
abandoned, leak the handle instead.

**Not an in-process build.** The existing ARM toolchain is musl 1.2.5 (time64). The
camera's saved libc is musl 1.1.24 (time32). The static standalone fixture is valid
on its own, but a core object built with this toolchain references
`__clock_gettime64`, `__fstat_time64` and `__lstat_time64`, which 1.1.24 does not
export. An in-process build needs a time32 musl-1.1.24 sysroot and the separate
ABI/loader proof.
