# WP1: native ownership contract (stage 1), 2026-11-14

**Result: a status-only bridge has one safe initialization site and needs no native
dispatch.**
- **Where it initializes:** on the main thread, by replacing the single call to the
  task-queue run at 0x31DB0 with attach → original → detach.
- **How it answers status:** from bridge-owned state only. No native object is read and
  no native task is posted.
- **What is still missing for anything beyond status:**
  - an identified owning lock or thread for presenter/media state
  - a device-ABI build profile (musl 1.1.24 with 32-bit `time_t`); the local toolchain
    is musl 1.2.5/time64

All results are **static analysis of the exact saved executable**. Nothing was run on
the camera. The contract is in [NATIVE-ABI.md](../native-bridge/NATIVE-ABI.md).

## Method and evidence

- **Input.** The exact saved `sdvcam`, SHA256
  `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`. Its hash was
  re-checked and is unchanged.
- **Tooling.** A local copy of the existing pyelftools/capstone analyzer.
- **Techniques:**
  - ELF load segments and exception-table function bounds
  - `.dynsym` C++ names, which are present for many methods, and vtables
  - GOT-relative literal references
  - direct branch enumeration
- **Mislabel check.** Nearest-symbol labels proved misleading again (for example, the
  queue code lies "after" unrelated symbols). Function identity therefore comes from
  exception-table bounds, vtables and source-file log strings (`subject.h`,
  `ff_task_queue_i.h`, `window_manager.cpp`, `main.cpp`).
- **Private, ignored evidence** in `private/ownership-program/wp1/`:
  - checkpoint: `checkpoint.json`
  - disassembly slices: `tq-impl.txt`, `main.txt`, `isubject-post.txt`,
    `mm-update.txt`, `presenter-update.txt`, `sym-*.txt`
  - helpers: `gotrefs.py`, `msgsites.py`
  - re-derivation script: `verify_wp1.py`, which passes **42/42** static checks
    (`verify-result.json`)

## Native dispatch model

The native app is a single-process "Eyesee" framework. It has a few distinct execution
contexts:

1. **Main thread.** It initializes everything, then calls `TaskQueue::Run` through
   0xA4294 at 0x31DB0. That run creates **one** worker pthread for the global
   `g_task_queue` (an `ff::task_queue_t` inside `EyeseeLinux::TaskQueue`) and joins it.
   The main thread therefore sleeps for the life of the app. It continues only after
   the queue is closed, then deletes MainModule, stops the watchdog and takes the exit
   branch.
2. **Task-queue worker.** It runs posted tasks one at a time, including asynchronous
   subject notifications (`ISubject::PostMsg` 0x37430), window messages
   (`WindowManager::PostMsg` 0xE3F38, `ChangeWindow` 0xE45C8), HTTP setting posts and
   presenter status-thread posts. There are 16 posting sites in total. Tasks bound to
   objects check a global "living book" and then lock a per-object mutex. Plain
   function tasks do neither.
3. **Synchronous notification on the caller's thread.** `ISubject::Notify` 0x3770C runs
   every observer on the notifying thread while holding that subject's mutex. The
   connect/disconnect events come from the 9999 socket thread (0x88EC0). The AP-enable
   event comes from inside 0x901EC.
4. **GUI thread** (`GUIMainWindowThread`). LVGL work runs under the WindowManager lock,
   together with a private message deque.

Static analysis shows the queue closes on only two paths, both inside
`MainModule::Update` 0xA4D6C: power-off at 0xA52B4 and settings-reset at 0xA5404.
In normal operation the worker never stops.

## Facts, inferences and unknowns

| # | Statement | Class | Evidence |
| --- | --- | --- | --- |
| F1 | `g_task_queue` 0xAEFF54 (136 B) is built by the static initializer 0x32028; `~TaskQueue` 0x58018 is registered with `__aeabi_atexit` | Fact | `.dynsym`, 0x3204C |
| F2 | Virtual methods of `task_queue_t` (vtable 0x8F6C20): close 0x5842C, produce 0x586E0, consume 0x5847C, run 0x5836C | Fact | vtable layout and the `ff` source path string |
| F3 | `TaskQueue::Run` 0x57F38 creates exactly 1 thread (`mov r2,#1` → 0x59060 → `pthread_create`), then joins (0x5911C) | Fact | disassembly |
| F4 | Run's only caller is `main` 0x31DB0; after it returns, `main` deletes MainModule (0x31DE4) and calls `StopWatchDog` (0x31DFC) | Fact | branch enumeration |
| F5 | Close (0x57FB8) is called only at 0xA52B4 (power-off) and 0xA5404 (settings-reset → exit action 3) | Fact | branch enumeration and log strings |
| F6 | `produce` locks the queue mutex and never checks the closed flag; `consume` returns -1 only when the queue is empty and closed | Fact | disassembly |
| F7 | Task ABI: `task_t` = `impl*`; vtable = destructor, deleting destructor, `run`, `fork`; `produce` and `consume` each fork; the worker's copy is destroyed lazily | Fact | 0x5831C, 0x58688, 0x5835C |
| F8 | `PostMsg` posts `Notify` (slot +0x10) to the queue; `Notify` runs observers on the calling thread under the subject mutex (+0x28), trying a non-blocking lock first and then blocking | Fact | 0x37538/0x37570, 0x37740/0x37820 |
| F9 | APP_CONNECTED/DISCONNECTED are synchronous `Notify` calls on `httpServer::initSocketServer` (0x8937C/0x8931C); SOFTAP_ENABLE is synchronous at 0x9051C | Fact | message-site scan |
| F10 | `PreviewPresenter::Update` 0xAB934–0xAD09C and RTSP create/start/sender take no mutex | Fact | instruction scan |
| F11 | `debug_thread` 0xA42E8 reads window/msg/value triples from `/tmp/debug.fifo` and passes them to `WindowManager` slot +0x18 (`PostMsg`) | Fact | disassembly |
| F12 | The GUI thread is `UIInit` 0xA4468, started as a `std::thread` by 0xA57F0, renamed `GUIMainWindowThread`; it calls loop 0xE582C on MainModule+0x60 (the WindowManager), which uses locks +0x80 (message deque) and +0x24 (around the GUI handler) | Fact | disassembly |
| F13 | The executable LOAD segment has 532 zero bytes before the data segment (504 still free after the media candidate's trampoline); `dlopen`/`dlsym` are imported | Fact | ELF headers and bytes |
| F14 | Device libc is musl 1.1.24 (32-bit `time_t`); the local cross sysroot is musl 1.2.5 with time64 redirects | Fact | saved rootfs strings/exports; sysroot headers |
| I1 | A bridge that never touches native objects can attach on the main thread before Run without changing native behaviour, apart from delaying the worker by its attach time | Inference | F3, F4; no shared state |
| I2 | Running a task on the worker does not make presenter or media state exclusive | Inference | F8–F10: notifiers on other threads reach `Update` without a presenter lock |
| I3 | In the media candidate, 0xAC46C would execute on the 9999 socket thread under the httpServer subject mutex, concurrently with any other subject's `Update` | Inference | F9, F10, candidate branch 0xAC5D0 |
| I4 | A late task posted after a terminal close leaks and never runs | Inference | F5, F6; process termination follows |
| U1 | Whether the subject mutex is recursive (re-entrant `Notify` would self-deadlock otherwise) | Unknown | attributes not traced |
| U2 | Which threads call 0x901EC (callers 0x907F4, 0xAF7D4, 0xAFAF4, 0xAFC24), and whether those calls are posted or synchronous | Unknown | not traced |
| U3 | Any native lock or ownership rule for the presenter media flag, RTSP controller (+4) and recorder | Unknown | none found in the traced bodies |
| U4 | Effects of the 7 direct `exit()` call sites, which would run `~TaskQueue` through atexit while threads are live (`abort`, 37 sites, does not run atexit) | Unknown | not traced |
| U5 | Actual timing: attach cost on the board, worker latency, live thread names and IDs | Unknown | hardware not used |

## Leads, not APIs

| Address | Identity | Context | Bridge status |
| --- | --- | --- | --- |
| 0x318C8 | `main` | main thread | Attach/detach bracket only, at 0x31DB0 |
| 0xA3788 | `MainModule::PrepareExit` (vtable 0x8F7AC4, thunk 0xA384C) | native lifecycle only | Never called or exposed |
| 0xA39B4 | device teardown (from `~MainModule` 0xA5984) | main, after Run | Never called or exposed |
| 0xAC46C | media-init body inside `Update` | whichever thread notifies (I3) | Stage 3 lead; owning lock unknown (U3) |
| 0xADB90 / 0xAD90C / 0xAE210 | RTSP create / start (calls 0x7D9B0) / sender | same as caller; lock-free | Stage 3 lead |
| 0xADD60 | RTSP destroy (one internal lock) | same as caller | Stage 3 lead; cleanup behaviour unexercised |

## Decision for the status-only bridge

1. **Initialize** on the main thread at the single 0x31DB0 call, in a bounded attach
   (≤ 50 ms target). Run the original unchanged on any failure.
2. **Dispatch** nothing native. The WP2 core runs on one bridge-owned thread. The
   native backend table answers `hello`/`status`/`capabilities` from bridge-owned data,
   and every physical capability stays unavailable.
3. **Detach** after Run returns and before MainModule deletion, with a bounded join and
   an abandon-on-timeout. Treat every other exit as an abrupt disappearance.
4. **Do not use** the task queue for v1. Do not read native state. Never use the debug
   FIFO, raw `MSG_TYPE` values, window messages or `PrepareExit`.

The worker task ABI is recorded for stage 3. Its safe use still needs: one outstanding
task per class, a deadline with a generation number, a non-throwing `run`, and
static-only references.

## Can an exact offline status-only integration be built next?

**Yes, offline, if it first gets a device-ABI build profile. No for hardware.** WP2's
core is not yet proven ABI-safe for in-process use. A library built with the present
musl 1.2.5 toolchain would reference `__*_time64` symbols that are absent from the
camera's libc. That fails cleanly at `RTLD_NOW`, but it is not usable. The remaining
proofs are listed in [NATIVE-ABI §7](../native-bridge/NATIVE-ABI.md#7-what-a-build-must-prove-before-a-native-backend-exists).
None of them requires the camera.

**Decisive next investigation (bounded, local, about half a day):**
1. Produce a 1.1.24-compatible link profile. Link against the saved rootfs `libc.so`
   and forbid time64 symbols. Build a trivial status-only `.so` with the WP2 core, then
   check its undefined symbols mechanically.
2. Load it under `qemu-arm` in a synthetic host that mimics the bracket: a blocking
   fake run, then attach/serve/detach.
3. Write the stub's ARM slice and its fallback tests. Do this only after the manager
   decides whether the status-only stub shares the media candidate's file or is
   separate.

If step 1 cannot meet the ABI gate, the alternative is a minimal in-process shim
(about 10 libc calls, time-free) plus an out-of-process core built statically like the
portal.

## Package report

- **Changed paths:**
  - `docs/work-packages/WP1-NATIVE-OWNERSHIP.md` (new)
  - `docs/native-bridge/NATIVE-ABI.md` (new)
  - `private/ownership-program/wp1/` (ignored analysis; the original
    `private/native-start/analyze.py` was copied, not modified)
- **Checks run:**
  - `verify_wp1.py` 42/42 (static)
  - source SHA256 re-checked as unchanged
  - device libc export and version checks (static)

  No ARM, fake or hardware tests were needed or run in WP1.
- **Risks:**
  - time64 ABI mismatch for in-process code
  - lock-free presenter `Update` across threads (stage 3)
  - leak of tasks posted after close
  - the stock debug FIFO is an unauthenticated raw GUI injector
  - `exit()` atexit ordering
- **Unimplemented gates:** everything in NATIVE-ABI §7; stage 3 ownership (U2/U3);
  hardware stage 5.
- **Next dependency:**
  - WP2 aligns its native table and proposed reason codes with §5
  - WP4 decides the bridge-object location (the TF is unmounted at stock S01 launch;
    a boot selector would provide it)

**Suggested architecture/status text (for the manager):** "WP1 (static, exact hash):
`sdvcam` runs posted work on one `g_task_queue` worker started from `main` 0x31DB0. The
main thread blocks there until a terminal close (power-off or reset). Subject `Notify`
runs observers synchronously on the notifier's thread. The presenter media path has no
identified lock. A status-only bridge will attach at 0x31DB0, answer only from
bridge-owned state, and detach before MainModule deletion. In-process code needs a
musl 1.1.24/32-bit-`time_t` build profile. No native dispatch or hardware capability is
enabled."
