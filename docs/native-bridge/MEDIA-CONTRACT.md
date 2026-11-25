# Owned-media contract (WP3 proposal, 2026-11-14)

**Proposal plus a local supervision core. No native media backend exists.** The
core in [`native/media/`](../../native/media/) implements the ownership, admission,
queue, dispatch and cleanup rules below against an explicit fake backend. The
native backend is **absent**, and the core refuses any native-kind table
(`serialization_unproven`). The [WP3 report](../work-packages/WP3-OWNED-VIDEO.md)
explains why: static analysis of the exact `sdvcam` did not establish a lock or
thread that owns every RTSP/media entrypoint. WP2 protocol 1.0
([PROTOCOL.md](PROTOCOL.md)) is unchanged and stays status-only.

## 1. Roles

| Party | Owns | Never does |
| --- | --- | --- |
| PC / agent | Requests through the portal; consumes media | Opens camera nodes; talks to the bridge directly |
| Body portal | HTTP authentication; maps one authenticated controller to one media connection | Treats an HTTP timeout as a native stop; forwards 9999 traffic |
| Bridge media core (`cm_core`) | Lease generations, admission, bounded queue, dispatch revalidation, completion/cleanup accounting | Calls native media functions itself; trusts frame or capability claims |
| Native backend (absent) | Would enqueue start/cleanup onto a *proven* owner context and post completions | Must not exist until gate G1 passes |

The bridge keeps WP2's peer-UID check as its only peer authorization. Authentication
and lease policy for people and agents stay in the portal. A connection to the
vendor's unauthenticated 9999 listener never authorizes media, and no 9999 message
is passed through.

## 2. Ownership model

- **One owner, one session.** At most one lease holder (`owner_generation`) and one
  owned session exist per bridge instance.
- **Generations.** The core assigns a new 64-bit generation per grant. It never
  reuses one within an instance. A new instance makes every old generation stale
  (`expect_instance`).
- **Controller identity** is assigned by the transport (a per-connection nonce),
  never taken from the payload. Connection close or idle drop calls
  `cm_controller_lost()`.
- **Leases** are 1–60 s. `renew` extends one. Expiry, loss or `release` moves the
  owner to `draining`: queued starts are cancelled at once, and anything dispatched
  needs cleanup.
- **No new owner while effects are unconfirmed.** `acquire` returns
  `cleanup_pending` until the session is confirmed clean (or was never dispatched).
  After the cleanup budget is spent, the owner is `faulted` until the instance
  restarts.

## 3. Request binding and admission order

Every mutating request carries:
- `controller_id`
- a strictly increasing `request_id` (per controller)
- the exact `instance` (no wildcard)
- the exact `boot_id` (no wildcard)
- an absolute same-boot `deadline_mono_ms` at most 60 s ahead
- `owner_generation` (0 only for `acquire`)

`status` may use wildcards (instance 0, boot zero, no deadline). A deadline
without a boot ID is refused (clock domain).

Checks run in order and return the first failure:
1. kind
2. reserved
3. controller
4. profile and lease field placement
5. instance
6. boot
7. deadline: present, not expired, within the horizon
8. request ID (consumed from here on)
9. backend availability
10. faulted owner
11. ownership: `acquire` → owned_by_other / already_owner / cleanup_pending; others →
    stale_generation, not_owner, lease_expired
12. kind-specific state

The **profile** is an enum (`1 = preview_main`). There are no paths, pointers,
methods or native message values.

## 4. Session phases and readiness (all reported separately)

| Phase | Meaning |
| --- | --- |
| `queued` | Admitted (`accepted` reply), waiting in the bounded queue; **not** dispatched |
| `start_dispatched` | Backend `submit_start` returned; completion pending |
| `active` | Start completed OK. Readiness is separate flags: `listener`, `sdp`, `frame_fresh` (frame within `frame_fresh_ms`) |
| `stop_queued` | Cleanup required and waiting (also during retry delay) |
| `cleanup_dispatched` | Cleanup submitted; not confirmed |
| `cleanup_uncertain` | Cleanup timed out, **or** cleanup acknowledged while the start is still unresolved |
| `faulted` | Cleanup failed past its budget |

Readiness flags are reported only in `active` without a stop request. Frames
from a fake backend are always flagged `synthetic`, by the core, whatever the
backend claims. `video` is **unavailable in every state** in core v1. Fixture
success never enables a physical capability.

## 5. Dispatch, deadlines and late completions

- The request deadline is the **dispatch deadline**. It is checked at admission and
  again immediately before `submit_start`. The second check also covers: same
  session, owner held, same generation, lease unexpired, no stop requested. An
  expired or stopped queued start is removed and **never** executed later.
- `cm_step()` runs on one thread. It drains at most 16 completions, applies lease
  expiry and op timeouts, and dispatches **at most one** item.
- `submit_*` must return immediately. A refusal (< 0) means "no effect". Completions
  arrive through `cm_post()`: lock-free, bounded (16), callable from any thread. A
  full mailbox is counted. The op then resolves by timeout (uncertain), never by
  assumption. The CAS loop is lock-free, not wait-free: an individual producer
  has no hard latency bound under contention. Before `cm_core_fini()` or freeing
  caller-owned storage, quiesce and join every completion producer. Local teardown
  is not an acknowledgement of physical cleanup.
- **Start timeout:** no completion within `start_timeout_ms` → `start_unresolved`,
  cleanup required, and the start token is kept.
  - A late start success **cannot be undone by dropping it**. If cleanup was already
    acknowledged, a second cleanup is issued.
  - A cleanup ack while the start is unresolved is **not final**.
- **Start failure** (or an unknown result) is treated as possibly partial: cleanup is
  required. Only an explicit `no_effect` result ends a session without cleanup.
- **Cleanup timeout:** `cleanup_uncertain`. No second cleanup is sent while one is
  unresolved. A late ack is applied normally.
- **Cleanup failure:** retry after `cleanup_retry_ms`, up to `cleanup_max_attempts`,
  then `faulted`.
- **Stale effects:** completions and events carry 64-bit op tokens. A token that does
  not match is counted and ignored, and cannot change state. Events after a stop
  request are ignored.
- **Backend ordering assumption (for a future native table):** cleanup is
  FIFO-ordered after any start for the same session on the backend's owner context.
  The core does not rely on it for safety (see `cleanup_uncertain`), only for
  needing one cleanup instead of two.

## 6. Stop semantics

`stop` returns one of:
- `stop_not_started`: a queued start was removed. Nothing ran, and cleanup is
  trivially confirmed.
- `stop_queued`: acknowledged only.
- `already_stopping`
- `no_session`

Cleanup is confirmed only by the `last_cleanup_done` flag with phase `none`. An
HTTP or native deadline passing never reports Stop complete and never frees the
owner.

## 7. Proposed wire protocol (separate endpoint; not implemented)

WP2 1.0 is frozen and keeps rejecting 0x10–0x1F (`mutation_disabled`) and physical
availability. The media protocol is a **separate SEQPACKET socket**
(`/tmp/cammy-bridge/media.sock`, same directory and lock rules). It is bound only
when the media backend is `ready`. It reuses WP2's framing so the portal client
shares one codec.

**Request (96 bytes):**
- Bytes 0–63: the WP2 request header with magic `CBMQ` and major 1. Fields at
  offsets 12 (request_id), 24 (expect_instance), 32 (boot_id), 48 (deadline) and
  56 (owner_generation) keep their WP2 meaning, now **mandatory** for mutating
  commands.
- Body, offsets 64–95:

  | Off | Size | Field |
  | --- | --- | --- |
  | 64 | 2 | profile |
  | 66 | 2 | reserved, 0 |
  | 68 | 4 | lease_ms |
  | 72 | 8 | session_id (0 = current) |
  | 80 | 16 | reserved, 0 |

**Commands:** 0x10 media_start, 0x11 media_stop, 0x12 media_status (the code points
WP2 §8 reserved), 0x13 lease_acquire, 0x14 lease_renew, 0x15 lease_release.

**Response:** the WP2 response header with magic `CBMS`. `owner_generation` at
offset 56 is real, but returned only to the holder. The payload is the `cm_status`
fields:
- backend kind/state/reason
- video state/reason
- owner state/reason
- phase, end reason, flags, cleanup attempts
- session/last session
- lease expiry, last frame time
- counters

**Status codes:** WP2 framing codes 1–18 keep their meaning. Core codes map to
`32 + code` (accepted = 33, stop_queued = 34, …). Unknown codes are failures.
Must-understand rules are as in WP2 §7.

**Compatibility plan for WP2 fields:**
- 1.0's `owner_generation` must stay 0 and status flag bits 1–2 are never set.
- A later 1.x may add only a read-only, append-only media summary to its status
  payload. It must never flip a physical capability to available without gate G1
  and a reviewed protocol change.
- Clients that know only 1.0 see no change.

## 8. Never exposed

- raw or source-chosen addresses, vtable slots or method names
- generic `MSG_TYPE`/`Notify`/`PostMsg`
- WindowManager or debug-FIFO messages
- 9999/HTTP passthrough
- shell text, device writes
- watchdog, radio, storage or power controls

A native backend may only post typed, generation-checked tasks to a proven owner
context.

## 9. Native gates (all open)

| Gate | Requirement |
| --- | --- |
| G1 Serialization owner | One context that executes **every** RTSP/media entrypoint (Update cases 0x8F8/0x9B8/0x9B9, GUI message handlers, work-mode window load/detach, `DestroyRtspServer` 0xADD60). WP3 did not establish such a lock or thread. Needs a reviewed design that removes or mediates the competing paths, or the matching-SDK route. |
| G2 Completion source | How start success, listener, SDP and fresh frames are observed without the debug FIFO, 9999 or polling native objects without a lock |
| G3 Cleanup meaning | What `DestroyRtspServer` and the sender teardown release, from which context, and how failure is detected |
| G4 In-process ABI | time32 musl 1.1.24 sysroot and loader proof. The core object now imports only `memcmp`/`memcpy`/`memset`/`__stack_chk_*`; the clock is injected. |
| G5 Exit/detach | Session state on native exit, and the detach join bound with a live session |
| G6 Hardware | Stage-5 acceptance: fresh changing frames, measured Stop/expiry/disconnect, stock recovery |
