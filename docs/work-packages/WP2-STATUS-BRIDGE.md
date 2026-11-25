# WP2 — status-only bridge and fake backend (report, 2026-11-14)

**Result: implemented and locally tested; no hardware.** Every claim below is from
host fixtures, static ARM builds or ARM user-mode emulation (qemu-arm). Nothing
contacted the camera, used ADB or opened a device node, and no service was
restarted. No vendor binary ran, nothing was packaged into the TF release, and no
version changed. Base commit 5a8c096; the files are uncommitted, for the manager
to integrate. Specification: [native-bridge/PROTOCOL.md](../native-bridge/PROTOCOL.md).

## Changed paths (all new)

- `native/bridge/cammy_bridge_proto.h`: wire layouts, enums, little-endian codec and client helpers (shared with a future portal client)
- `native/bridge/cammy_bridge_backend.h`: backend table ABI 1
- `native/bridge/cammy_bridge.h`, `native/bridge/cammy_bridge.c`: embeddable core (listener, validation, dispatch, capability clamp, wake)
- `native/bridge/backend_fake.c`: status-only fake; `native/bridge/backend_native_unavailable.c`: native table placeholder that returns NULL
- `native/bridge/bridge_main.c`: standalone daemon for fixtures; `native/bridge/bridge_selftest.c`: core tests with misbehaving tables
- `native/bridge/Makefile`: targets `host`, `host-asan`, `arm`; outputs only to ignored `build/native-bridge/`
- `native/bridge/cammy_bridge_client.py`: independent stdlib codec and CLI; `native/bridge/run_arm_tests.py`: qemu-arm runner
- `tests/test_native_bridge.py`: 29 adversarial tests
- `docs/native-bridge/PROTOCOL.md`, `docs/work-packages/WP2-STATUS-BRIDGE.md`
- Private, not in git: `private/ownership-program/wp2/checkpoint.json`

## Checks run (all local)

| Command | Result | Kind |
| --- | --- | --- |
| `make -C native/bridge host host-asan arm` | Clean under `-Werror -Wconversion -Wsign-conversion -Wshadow -Wformat=2 -Wmissing-prototypes -Wstrict-prototypes -Wvla -Wundef -Wcast-qual` | host + static ARM (armv7-a, NEON-VFPv4, hard-float, musl 1.2.5); daemon 54,448 B stripped |
| `python3 -m unittest tests.test_native_bridge` | 29/29 OK, about 0.6 s; 10/10 repeated runs | host, fake/absent backends |
| Same suite against the `host-asan` build | 29/29 OK (ASan + UBSan, no-recover) | host |
| `python3 native/bridge/run_arm_tests.py` | 29/29 OK; 5/5 repeated runs | **ARM binary under qemu-arm (Cortex-A7)** |
| `bridge-selftest DIR` (host, ASan, qemu-arm) | 80 checks, 0 failures on each | inert test tables |
| `python3 -m unittest discover -s tests -p test_native_bridge.py` (the `make test` style) | OK | host |
| Mutation check: a private scratch script applied 19 single-line safety regressions | **19/19 caught** (each broke at least one test) | host |

The mutants removed or broke these protections: capability clamp (fake and native),
request-ID replay check, ancillary rejection, replacement-safe unlink, boot-file type
check, idle expiry, slow-reader drop, unknown-field check, peer-UID check,
late-result expiry, mutating-range rejection, directory-mode check, instance lock,
error budget, minor-version check, table validation, wake polling, and the native
READY/reason rule.

The suite covers: framing, size, truncation and empty packets; version and
unknown-field policy with exact offsets; request-ID duplicates and per-connection
scope; instance, boot and deadline binding; the error budget; pipelining; rejection
of passed descriptors (the test proves no descriptor is kept); Stream/Dgram
refusal; 3 concurrent callers; peer disconnect mid-request (40 times, no leaked
slot); busy limit and slot reuse; idle expiry; slow reader; native-absent labeling;
denied peer UID with identity withheld; 0600/0700 modes and clean SIGTERM; a second
instance refused; stale socket reclaimed after SIGKILL; a replacement socket never
unlinked; unsafe, occupied or symlinked paths refused; argument bounds; and a FIFO
or device as the boot-ID source refused without hanging.

## Design decisions

- **SEQPACKET with fixed 64-byte little-endian frames**, no text parser. The kernel
  keeps message boundaries, so the bridge has no partial-read state and no
  slowloris case.
- **Must-understand requests:** a newer minor, or any non-zero flag, reserved or
  owner field, is refused with the byte offset.
- **Status data is bridge-owned only.** The fake reads only process-local state. The
  core reads only its own counters, `CLOCK_MONOTONIC`, `getrandom` and the boot-ID
  file. This matches the WP1 status table: no native object or task.
- The fake and native kinds are chosen explicitly and appear in every response. The
  core enforces the protocol-1.0 clamp, so no physical capability is ever reported
  available.
- `NATIVE_EXITING` (6) and `NATIVE_IDENTITY_MISMATCH` (7) were added as backend
  reasons. They fit 1.0 cleanly: responses are append-only and unknown reasons
  already mean unavailable. A native `ready` must carry reason `none`.
- `cammy_bridge_wake()` (a self-pipe) was added so that WP1's detach join of at most
  200 ms does not depend on the poll wait.

## Findings and risks

- **Libc ABI.** The camera's saved libc is musl 1.1.24 (time32). The toolchain is
  musl 1.2.5 (time64). The static standalone daemon is valid, but it is **not an
  in-process build**. A PIC core object from this toolchain imports
  `__clock_gettime64`, `__fstat_time64` and `__lstat_time64`, which 1.1.24 lacks. It
  also imports `accept4`, `pipe2`, `flock`, `getrandom`, `poll`, `recvmsg`,
  `getsockopt`, `calloc`/`free` and `__stack_chk_*`. An in-process object needs a
  1.1.24 time32 sysroot (not present) and the separate loader proof.
- The camera kernel rejects `statx`, so the ARM build links the existing
  legacy-stat shim. Under qemu it ran against the host kernel; it has **not run on
  kernel 4.9**.
- The socket directory must be on RAM-backed storage. This package did not
  revalidate the camera's `/tmp`; the launcher must check it on the actual boot.
- Manager review fixed the accept resource-exhaustion path: a nontransient
  accept error now fails the bridge instance instead of busy-spinning inside
  the native hardware owner. It must never terminate the containing native app.
- The `SO_PEERCRED` UID check is the only peer authorization. On the camera both
  processes probably run as root, so lease authentication must stay in the portal.
- The kernel's per-socket queue length (`net.unix.max_dgram_qlen`: 512 on the host,
  unknown on the camera) sets when a slow reader is dropped. The tests check the
  behavior, not a count.
- A connect burst beyond the backlog gets `EAGAIN` or `busy`. The portal must back
  off with a bound; neither means the bridge is down.

## ABI assumptions to confirm (WP1 / loader package)

1. The bridge runs on one dedicated thread created at attach with signals masked
   (WP1 §3). The core installs no handlers and never touches umask.
2. Attach calls `cammy_bridge_open` once: one `calloc`, a pipe, a lock file and a
   socket. Step never allocates, so an abandoned thread holds no allocator lock.
   Detach is wake, then join with a bound, then `close`. If the thread is
   abandoned, the handle is leaked, never closed.
3. The native table is called only on the bridge thread and returns bridge-owned
   state (phase, attach time, host-record check). Whether WP1 maps a host mismatch
   to `NATIVE_TABLE_REJECTED` or the new `NATIVE_IDENTITY_MISMATCH` is for the
   manager to settle in NATIVE-ABI.md.
4. The boot ID comes from `/proc/sys/kernel/random/boot_id`, assumed readable in
   the app's context.

## Unimplemented gates

- No native table, loader stub or in-process build (separate package). Without a
  reviewed table, `--backend native` reports `unavailable/native_table_absent`.
- No mutating command, lease, Stop or cleanup semantics (stage 3).
- Not run on the camera kernel. No TF staging, and no daily-use authentication
  path.

## Proposed portal integration (parent)

- Add a small C client in `src/` that includes `native/bridge/cammy_bridge_proto.h`.
  It connects on SEQPACKET, sends hello once (records instance, boot ID and limits)
  and polls status with a deadline (the bridge boot ID plus `CLOCK_MONOTONIC` ms).
- Expose a read-only, authenticated `bridge` object in portal diagnostics:
  reachable, `backend_kind`, `backend_state`/`reason`, instance, uptime and
  counters. Show `fake` explicitly, and keep `hardware_adapters_available=false`
  unless a future native table **and** a protocol with physical capabilities
  report available.
- Treat `busy`, `EAGAIN` and closing errors as transient, with bounded backoff.
  Treat a changed instance as "bridge restarted; prior state void". An HTTP timeout
  never implies a native stop.
- Optionally add a simulator endpoint returning the same fields, labeled simulated.

## Suggested architecture/status update (for the manager)

> 2026-11-14 WP2: a status-only native bridge (protocol 1.0: SEQPACKET, fixed
> 64-byte frames, hello/status/capabilities) and a fake backend are implemented in
> `native/bridge/`. Tests: 29 host and 29 qemu-arm adversarial tests plus an 80-check
> core self-test; 19/19 mutants caught. These are local fixture results only: no
> camera, in-process or native-table run. The core keeps physical capabilities
> unavailable in 1.0, and the native table is absent. An in-process build is blocked
> on a musl 1.1.24 time32 sysroot and the loader proof. Stage-3 hooks
> (owner_generation, same-boot deadlines, instance binding, reserved 0x10–0x1F) are
> defined but rejected today.

**Next dependency:** the ABI/loader proof package (time32 sysroot, symbol-compatible
core object, WP1 attach/detach stub with a status-only native table), then portal
client integration.

## Manager integration review

The manager reproduced the 29-test suite on host, ASan/UBSan and qemu-arm. The
core self-test now has 88 checks, including a real queued connection with a
temporarily exhausted descriptor limit: `step` must return failure, permitting
the bridge thread to stop without a CPU spin or native-app exit. Review also
fixed a test-helper socket leak on failed/backlogged connects. These checks remain
local fixtures; native/hardware acceptance is still unavailable.
