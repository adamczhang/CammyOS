# Native bridge ABI: status-only contract (WP1, 2026-11-14)

**Offline implementation and contract.** WP2A now supplies a status-only native
table, shared library and exact-build loader candidate, tested only in synthetic
hosts. No native media backend or hardware deployment exists. Every
address below is a link-time virtual address in the exact stock `sdvcam`, found by
offline static analysis. Nothing here was exercised on the camera. Hardware
capabilities stay **unavailable** in every version of this contract until a later
reviewed stage changes that explicitly. The evidence and reasoning are in the
[WP1 report](../work-packages/WP1-NATIVE-OWNERSHIP.md) and
[loader proof](LOADER-PROOF.md).

## 1. Identity scope

| Item | Value |
| --- | --- |
| Executable | `/usr/bin/sdvcam`, SHA256 `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec` |
| Device C library | musl **1.1.24** armhf (`/lib/libc.so` from the saved rootfs, SHA256 `5fea26c4e562809794d33fd9c62304cef68b2db6233a8372bedd31e0baac7f51`), with 32-bit `time_t` |
| Local cross toolchain | musl **1.2.5** with `_REDIR_TIME64`/64-bit `time_t`: **not** ABI-compatible with in-process code (see §7) |

A library built for one source hash rejects a different host record with
`-EPROTO` before opening IPC; the stub falls back to the original call.
`NATIVE_IDENTITY_MISMATCH` is reserved for an out-of-process diagnostic, since
a rejected library cannot serve that status. Addresses are never derived at runtime, never
received over IPC and never logged to clients.

## 2. Native execution contexts

| Context | Created by (address) | Role for the bridge |
| --- | --- | --- |
| Main thread | ELF `main` 0x318C8 | **Only** attach/detach site. From 0x31DB0 it blocks inside the task-queue run until a terminal close. |
| Task-queue worker | One pthread from `TaskQueue::Run` 0x57F38 → `ff::thread_t` create 0x59060 (N=1) | Runs every posted task in order. Future dispatch target; **not used** by status v1. |
| `httpServer::initSocketServer` | 0x88EC0 thread | Port-9999 socket thread. It raises APP_CONNECTED/DISCONNECTED (0x9B8/0x9B9) through **synchronous** `Notify`, so presenter code runs on it. Not a bridge context. |
| `GUIMainWindowThread` | `UIInit` 0xA4468 (started via 0xA57F0), loop 0xE582C | LVGL owner. Keeps its own message deque (WindowManager+0xE4/+0x80 lock) and a GUI lock (+0x24). Not a bridge context (stage 6). |
| `debug_thread` | 0xA42E8, `/tmp/debug.fifo` | Stock raw window/msg/value injector. **Forbidden** as a transport or reference. |
| Watchdog feeder | 0x999FC | Never touched, never replaced. |
| Timer/signal threads | e.g. `LogProc` timer 0xA4264 | Never used. |

## 3. Lifecycle bracket (the v1 initialization decision)

The selected attach candidate is the single call `bl 0xA4294` at `main`+0x4E8 (0x31DB0). Static
analysis shows it has exactly one caller. 0xA4294 loads `g_task_queue` (0xAEFF54) and
tail-branches to `TaskQueue::Run`, which creates one worker and joins it. A loader stub
that replaces this call must behave as:

```
stub(MainModule *mm):              /* main thread, r0 = mm, AAPCS */
    rc = attach(&host)             /* bounded; never throws; never touches mm */
    0xA4294(mm)                    /* original; returns only after a terminal close */
    if (rc == 0) detach(EXIT_QUEUE_CLOSED)
    return                         /* main resumes at 0x31DB4: MainModule delete, StopWatchDog */
```

**State at attach (static facts):** the watchdog is armed and fed by its own thread
(0x31914). The MainModule singleton is constructed (0x31958). The `mppinit` and
`Datainit` threads have been joined (0x31BE4/0x31BEC). The debug thread, GUI thread,
log timer and one unnamed thread have started. `g_task_queue` exists (static
constructor 0x32028), but its worker has **not** started, so tasks posted during
initialization are waiting in it. The TF card may be absent: stock S01 starts the app
before S99.

**Attach rules:**
- Run on the main thread.
- Bounded: target ≤ 50 ms. Every millisecond spent here delays the worker that runs the
  queued initialization messages.
- Make no native calls and never dereference `mm`.
- Create exactly one bridge thread, with every blockable signal masked, so
  process-directed signals still reach native threads as in stock.
- All descriptors are `CLOEXEC` (the app calls `system()` repeatedly). Writes use
  `MSG_NOSIGNAL`. SIGPIPE is already ignored live.
- Any failure returns non-zero. The stub then runs the original unchanged, giving
  stock behaviour.

**Serve:** the bridge thread runs the WP2 core loop (`cammy_bridge_step`). Backend
callbacks run only on that thread.

**Detach:**
- Runs only after the original returns. Static evidence shows that happens only after
  `TaskQueue::Close` (0x57FB8): on the power-off branch (0xA52B4) or the settings-reset
  branch (0xA5404) of `MainModule::Update` 0xA4D6C.
- Sets phase `NATIVE_EXITING`, refuses new requests, wakes the thread and joins it for
  at most 200 ms.
- On timeout it abandons the thread and returns, so the power-off path is never held.
  This is eligible only if the library stays mapped and every allocation, callback
  and state object reachable by the thread stays valid until process death. Never
  dlclose the library or free its state on the abandon path. This lifetime rule
  needs a fixture; bridge-owned data alone does not prove safety.
- It must finish before 0x31DB4 deletes MainModule. The watchdog stays fed until
  `StopWatchDog` at 0x31DFC.

**No detach occurs** on any of these:
- glog failure-signal re-raise (SIGSEGV/ILL/FPE/ABRT/BUS/TERM)
- `exit()` from a native path (atexit runs `~TaskQueue` 0x58018 while threads still live)
- `_exit`
- native power-off before `main`'s final branch
- a watchdog reset

The bridge must therefore be correct if it disappears at any instruction. Peers see
EOF, and a stale socket path is recovered by the next attach (WP2 instance lock).

## 4. Loader-stub ↔ bridge interface (v1)

Exported by the bridge object with C linkage. The stub resolves exactly these names and
no others.

```c
#define CAMMY_NATIVE_HOST_ABI 1u
enum cammy_native_profile { CAMMY_PROFILE_STATUS_ONLY = 1 };
struct cammy_native_host_v1 {          /* built by the stub; const; copied at attach */
    uint32_t abi_version;              /* CAMMY_NATIVE_HOST_ABI */
    uint32_t struct_size;              /* sizeof(struct cammy_native_host_v1) */
    uint32_t profile;                  /* CAMMY_PROFILE_STATUS_ONLY; anything else rejected */
    uint8_t  source_sha256[32];        /* constant baked into the stub at build time */
    void    *main_module;              /* opaque; v1 MUST NOT dereference or store beyond attach */
};
int  cammy_native_attach_v1(const struct cammy_native_host_v1 *host); /* 0 or -errno */
void cammy_native_detach_v1(uint32_t reason);   /* 1 = EXIT_QUEUE_CLOSED; idempotent */
```

The bridge rejects the host struct (attach returns `-EPROTO`) when the ABI or size
differs, the profile is not status-only, or the hash differs from its own compiled
constant. `detach` before a successful `attach` is a no-op. No function pointer, task
queue address or native object is passed in v1.

## 5. Status-only backend table (`CB_BACKEND_NATIVE`, WP2 ABI 1)

The native table fills WP2's `struct cammy_bridge_backend` with these rules:

| Callback | Allowed behaviour |
| --- | --- |
| `init` | Mark the table initialized after attach validates the host record. No native calls. |
| `query_status` | `READY` only while attached and `SERVING`; an invalid table is rejected by the core. While exiting, `UNAVAILABLE`/`NATIVE_EXITING`. Attach-time identity failure prevents the server from starting. |
| `query_capabilities` | Every physical capability `UNAVAILABLE`/`NOT_IN_PROTOCOL_V1`. `STATUS_IPC` is filled by the core. |
| `fini` | Release bridge-owned resources only. |

`hello` is handled entirely by the core. The backend adds nothing native to it.

**Allowed status data:**
- compile-time constants and the validated host copy
- bridge-owned atomics: phase, counters, attach time
- `getpid()`, `CLOCK_MONOTONIC` and `/proc/sys/kernel/random/boot_id`

**Forbidden in status v1:**
- dereferencing `main_module` or any native object or global (for example
  `g_exit_process`, presenter fields, NetManager mode +0x64)
- posting tasks, `PostMsg`/`Notify`, WindowManager calls, the debug FIFO
- device nodes, ioctls, sysfs or procfs writes, `system()`/exec, radio, storage or
  watchdog actions

No identified lock makes presenter or network fields consistent (§6.3), so even reads
are excluded.

**Accepted WP2 reason codes:** `CB_BREASON_NATIVE_EXITING` (6) and
`CB_BREASON_NATIVE_IDENTITY_MISMATCH` (7), distinct from table rejection.

## 6. Future dispatch ABI: documented, UNAVAILABLE

These facts constrain later stages. They do not authorize any call.

### 6.1 Task queue

| Item | Evidence |
| --- | --- |
| Object | `EyeseeLinux::TaskQueue g_task_queue` at 0xAEFF54 (136 B): an `ff::thread_t` list at +0 and an `ff::task_queue_t` at +0xC |
| Post | `TaskQueue::Produce` wrapper 0x58010 (`add r0,#0xC; b produce 0x586E0`), AAPCS: `r0 = &g_task_queue`, `r1 = const task_t*` |
| Thread safety of post | `produce` takes the queue mutex (+0x30 of `task_queue_t`) and signals its condition on empty→non-empty. 16 native call sites post from timer, socket, UVC and status threads. |
| Close | `close` 0x5842C clears the flag (+0x20) and broadcasts. **`produce` never checks the flag**: a task posted after the worker has drained is never run and never freed. |
| Worker | `run` 0x5836C loops `consume`→`task.run()`. `consume` 0x5847C returns -1 only when the queue is empty and closed. An exception from a task leaves `run` and reaches the thread entry, giving `std::terminate`. |

### 6.2 Task object ABI

- `task_t` is one word: `impl*`.
- The `impl` vtable is `[0]` destructor, `[1]` deleting destructor, `[2]` `run()` and
  `[3]` `fork()` (returns a new heap copy). This matches `ff::task_impl_t` at 0x8F6BE4
  (`run` = `func(arg)` from +4/+8).
- `produce` calls `src->fork()` with no null check. The caller still owns `src`
  afterwards.
- `consume` forks again and deletes the node copy.
- The worker's copy is deleted lazily, at the next `consume` or at loop exit, so a
  destructor can run on the worker long after `run`.
- Our `impl` must therefore:
  - never throw, and never block
  - refer only to static bridge memory, matched by a generation number
  - allocate and free with one matching allocator inside its own `fork` and deleting
    destructor
- A failed node allocation inside `produce` throws `bad_alloc` through the caller. This
  is accepted only as a process-fatal out-of-memory case, as in stock.

### 6.3 What the worker does not give

- `ISubject::PostMsg` 0x37430 posts `Notify` (virtual slot +0x10 = 0x3770C) to the
  worker.
- `ISubject::Notify` itself runs observers **on the calling thread** while holding that
  subject's mutex (+0x28). It tries a non-blocking lock first, logs `deadlocks!!!` on
  contention, then blocks. It never drops the message.
- APP_CONNECTED/DISCONNECTED reach `PreviewPresenter::Update` 0xAB934 on the
  9999-socket thread. SOFTAP_ENABLE 0x8F8 is raised synchronously at 0x9051C inside
  0x901EC.
- `Update` (0xAB934–0xAD09C) takes **no** mutex. The RTSP create (0xADB90), start
  (0xAD90C → 0x7D9B0) and sender (0xAE210) routines take none either.
- So running on the worker is **not** exclusive ownership of presenter or media state.
  A future media call must first establish an owning lock or thread (§8).

### 6.4 Rules for a later dispatch
- At most one outstanding task per operation class.
- An absolute deadline, with a generation-checked completion slot.
- Completion is signalled through a non-blocking `eventfd`.
- Stale operation tokens are ignored. A valid late completion after a deadline
  can still reveal a physical effect: retain its accounting and require cleanup,
  as specified in [MEDIA-CONTRACT.md](MEDIA-CONTRACT.md). Timeout is not cancellation.
- A timeout is reported as `native-dispatch-timeout`, never as success.
- A queued acknowledgement is distinct from confirmed native effect.

## 7. Build proofs and their current scope

WP2A supplies the device-ABI and status-only loader/lifecycle proofs; see
[LOADER-PROOF.md](LOADER-PROOF.md). These are offline results, not native media
serialization or hardware acceptance. Requirements for any later build:

1. **Identity and patch scope.** The input hash is exact, and the candidate hash is
   recorded. The changed ranges are limited to:
   - the one `bl` at 0x31DB0
   - stub bytes inside the verified zero code cave (file 0x8D2E1C–0x8D3030, VA
     0x8E2E1C–0x8E3030). The separate media candidate uses 0x8E2E20–0x8E2E38;
     the pristine-stock status candidate leaves that range untouched.
   - the executable LOAD sizes

   The entry point, writable segment, dependencies and section headers are unchanged.
   `dlopen`/`dlsym` are already imported, if the loader uses them.
2. **Device ABI.**
   - Every undefined symbol of the bridge object resolves in the saved musl 1.1.24
     `libc.so`, plus any library already listed in `sdvcam`'s dependencies.
   - There are **no** `__*_time64` references.
   - No libc struct that contains `time_t` is shared unless its 32-bit layout is proven.
   - It is EABI5 hard-float.
   - It does not depend on `libstdc++`, `libgcc_s` or initial-exec TLS unless the exact
     device versions are proven.
   - Its constructors are trivial.
   - `RTLD_NOW` loading succeeds under `qemu-arm` with the saved rootfs libc in a
     synthetic host, never `sdvcam`.
3. **Stub slice (ARM fixture).** Inert `dlopen`/`dlsym`/original stubs show:
   - fallback to the original on a NULL handle, a NULL symbol or a non-zero attach
   - `r0` passed through
   - callee-saved registers and `sp` preserved
   - a return to 0x31DB4
   - detach called exactly once, and only after a successful attach
4. **Lifecycle fixture.** A synthetic host blocks in a fake queue run. Tests cover:
   - serve, then detach
   - the detach-timeout abandon path
   - attach within budget
   - the bridge thread's signal mask
   - `CLOEXEC` on every descriptor
   - killing the host while serving, so peers see EOF
   - recovery from a stale socket on the next attach
5. **Status semantics.** Host, fake and native tables return identical
   `hello`/`status`/`capabilities` shapes, with every physical capability unavailable.
   An identity mismatch is rejected, not relabelled.
6. **Before any dispatch (later).** A fake `ff` queue built to §6.1/6.2 tests:
   - close-before-run, with a bounded leak and a timeout
   - stale completions
   - a worker stall
   - a non-throwing `run`
   - destructor-after-detach
   - one outstanding task per class

Hardware proof is **not** performed in WP1. It is gated as stage 5 of the
[execution plan](../archive/HARDWARE-OWNERSHIP-EXECUTION.md): a status-only build with the
original hash retained, one app, a stable watchdog, home Wi-Fi and LCD, hello/status,
then disarm.

## 8. Never exposed, any version

None of these is ever exposed:
- raw addresses, arbitrary methods or vtable slots
- generic `MSG_TYPE` values, `PostMsg`/`Notify` passthrough, WindowManager messages
- the debug FIFO, HTTP/9999 command passthrough
- `PrepareExit` 0xA3788, the teardown 0xA39B4, `g_exit_action`
- watchdog, radio, storage, USB or power controls
- shell text or device writes

The media leads (0xAC46C, 0xADB90, 0xAD90C, 0xAE210, 0xADD60) stay leads until stage 3
proves their owning context, lock and cleanup.
