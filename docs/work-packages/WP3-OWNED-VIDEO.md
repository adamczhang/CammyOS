# WP3 — owned video slice: serialization finding and local media core (2026-11-14)

**Result:**
- A local owned-media supervision core with an explicit fake backend is implemented
  and tested.
- **The native media backend stays unavailable.** Static analysis of the exact
  `sdvcam` shows that no existing lock or thread owns every media entrypoint.

All work was offline:
- static analysis of the saved binary
- host builds
- a static ARM fixture under qemu-arm

No camera, ADB, network, device node, ioctl, vendor executable, TF card or service
was touched. Base commit c81d295. Files are uncommitted for the manager. Contract:
[MEDIA-CONTRACT.md](../native-bridge/MEDIA-CONTRACT.md).

## A. Native serialization investigation

**Input:** `M105 investigation/work/firmware-rootfs/usr/bin/sdvcam`, SHA256
`8771961f…637d62b0ec` (verified). The analyzer is a read-only copy of WP1's.
Reproduce with:

```
python3 private/ownership-program/wp3/verify_wp3.py
```

Result: **15/15 checks pass**, written to `verify-result.json`.

| # | Statement | Class | Evidence |
| --- | --- | --- | --- |
| F1 | All 24 named `ISubject_d<…>` constructors zero-fill the 24-byte subject mutex at +0x28 (`vst1` q8 at +0x28, `vstr` d16 at +0x38, `str` at +0x40). None calls `pthread_mutex_init` or `pthread_mutexattr_settype`. The binary's only 3 `settype` calls (0x17B144, 0x363754, 0x48926C) are outside them. This covers httpServer, NetManager, SoftApController, WifiController, PreviewPresenter, Camera, Recorder and MainModule. | Fact | ctor scan; e.g. 0x89D60, 0x92E10, 0xAD44C |
| F2 | `ISubject::Notify` 0x3770C takes `this+0x28`: `trylock`, immediately `unlock` on success, or log `deadlocks!!!` on contention. It then blocks in `std::mutex::lock` (0x37298: `pthread_mutex_lock`, throws `system_error` on error) and holds the lock across observer calls. | Fact | 0x37718–0x37760 |
| F3 | `Notify` is virtual slot +0x10 in the NetManager, PreviewPresenter and httpServer subject vtables | Fact | vtables 0x8F75F8 / 0x8F7C1C / 0x8F732C |
| F4 | 0x901EC is `NetManager::SwitchToSoftAp` (`net_manager.cpp`). At 0x9051C it raises SOFTAP_ENABLE (0x8F8) by a **synchronous** `Notify` on NetManager. | Fact | log strings; `blx [vtable+0x10]` |
| F5 | Callers of 0x901EC, with their contexts:<br>• `SetWifiSsid` 0xAFA5C / `SetWifiPwd` 0xAFB8C ← `HandleSetIotCtrlCmd` 0x85848 ← `SetDeviceInfoHandler::handlePost` 0x85DE8, all direct `BL` on a **CivetWeb** request thread<br>• `httpServer::postmsg3devicesetting`, **posted** to `g_task_queue` by 0x84154, running on the worker → 0xB164C → 0xB0C38 → 0x907D4<br>• `DeviceSettingPresenter` GUI message handler 0xB1094<br>• `PreviewPresenter::HandleGUIMessage` 0xAAD34<br>• the `PreviewPresenter::Update` "reset factory complete" case (0xABC28 → 0x925A4 → 0x907D4)<br>• `SetWifi` 0xAF708 ← 0x83F28 (caller of 0x83F28 not found) | Fact | branch, vtable and GOT scans |
| F6 | `PreviewPresenter::Update` case 0x8F8 (0xABA94 → 0xAC44C) logs `MSG_SOFTAP_ENABLE`, checks preview mode and recorder, calls `usleep(500000)`, then RTSP create 0xADB90, start 0xAD90C and sender 0xAE210 | Fact | 0xAC44C–0xAC524 |
| F7 | RTSP create/start/sender are called from `Update` and the helper 0xAE3C8. 0xAE3C8 is called from `OnWindowLoaded` in the normal record/capture modes (0x43954, 0x4B888). `DestroyRtspServer` 0xADD60 is called from `Update`, `HandleGUIMessage`, 0xAE3C8 and the work-mode `OnWindowDetached` (0x46908, 0x4A03C). Update, create, start and sender call no mutex lock. | Fact | caller scan, instruction scan |
| I1 | The subject mutex is **non-recursive**. A zero-initialized musl 1.1.24 mutex is `PTHREAD_MUTEX_NORMAL`, so a same-thread relock blocks forever with no `EDEADLK`. A nested `Notify` on a subject already notifying on that thread self-deadlocks. | Inference (musl semantics; `libc.so` not disassembled) | F1, F2 |
| I2 | Update → SwitchToSoftAp → `NetManager::Notify` nests subject locks (outer notifier, then NetManager) with no global order | Inference | F4, F5 |
| I3 | RTSP create, start and destroy can run concurrently on unrelated threads, each holding a different lock or none:<br>• CivetWeb threads (NetManager mutex)<br>• the 9999 socket thread (httpServer mutex, APP_CONNECTED/DISCONNECTED)<br>• the task-queue worker<br>• GUI/window callbacks | Inference | F2–F7, WP1 F9/F10 |
| I4 | **No existing lock or thread can own all media entrypoints.** Posting to the worker (WP1's candidate) does not exclude these paths. | Inference (strong) | I3 |
| U1 | Whether PreviewPresenter is attached as an observer of NetManager (attach sites not traced; the 0x8F8 case strongly suggests it) | Unknown | — |
| U2 | Which thread runs `HandleGUIMessage` and the window load/detach callbacks (GUI thread or worker) | Unknown | — |
| U3 | Which subject notifies the "reset factory complete" case; the CivetWeb thread count; who calls 0x83F28 | Unknown | — |
| U4 | What `DestroyRtspServer` releases and how failure is detected (gate G3) | Unknown | — |

**Decision:** serialization is **not provable**. Static evidence argues against any
existing owner. No lock is invented, no generic event is posted, and the debug FIFO
and live memory are not used. `cm_native_media_backend()` returns NULL, and the
core refuses any native-kind table with `serialization_unproven`. Owning media
needs a reviewed design for gate G1:
- removing or mediating the competing SOFTAP_ENABLE, APP_CONNECTED/DISCONNECTED,
  GUI and work-mode paths, **or**
- the matching-SDK route.

## B–E. Local owned-media core

**Changed paths (all new):**
- `native/media/cammy_media.h`, `cammy_media.c`: the core. Caller-owned storage; no
  allocation, stdio or signals; one stepping thread; lock-free bounded `cm_post()`.
- `native/media/media_clock_posix.c`: injectable host clock, kept out of the core
  object.
- `native/media/media_backend_native_unavailable.c`: native table absent.
- `native/media/media_backend_fake.{h,c}`: explicit test-only fake that models side
  effects (`running`, `partial`).
- `native/media/media_fixture.c`: line-scripted scenario driver with injected clock
  and threaded `stress`.
- `native/media/Makefile`: `host`, `host-asan`, `host-tsan`, `arm` (fixture plus
  core-only PIC object), all into `build/native-media-owned/`.
- `native/media/run_arm_tests.py`: runs the suite under local qemu-arm.
- `tests/test_owned_media.py`: 31 tests.
- `docs/native-bridge/MEDIA-CONTRACT.md` and this report.
- Private, not in git, under `private/ownership-program/wp3/`:
  - `verify_wp3.py`, `analyze_ref.py` (copy), `wp3trace.py`, `q_*.py` query
    helpers, `verify-result.json`
  - `mutate.py`, `mutation-result.json`
  - `checkpoint.json`

**Rules implemented.** Details are in the contract.
- Typed acquire/renew/release/start/stop/status bind instance, boot, generation,
  request ID and deadline. Mutating requests accept no wildcards.
- One owner and one session. A bounded queue. One dispatch per step.
- Admission and dispatch-time revalidation.
- Stop, expiry and controller loss cancel queued starts.
- Separate phases plus listener/SDP/frame flags.
- Start timeout → uncertain.
- A late success after an acknowledged cleanup triggers a second cleanup.
- Cleanup timeout blocks new owners.
- Cleanup failure: bounded retry, then `faulted`.
- Token-checked stale completions and events.
- Core-labeled synthetic frames.
- Video unavailable in all states.

## Checks run (all local)

| Command | Result |
| --- | --- |
| `make -C native/media host host-asan host-tsan arm` | Clean under `-Werror -Wconversion -Wsign-conversion -Wshadow -Wformat=2 -Wmissing-prototypes -Wstrict-prototypes -Wvla -Wundef -Wcast-qual -Wpointer-arith` |
| `python3 -m unittest tests.test_owned_media` | **31/31 OK**; 5/5 repeats |
| Same suite with `CAMMY_MEDIA_FIXTURE` set to the `host-asan` / `host-tsan` builds | 31/31 OK, 5/5 repeats each (ASan+UBSan no-recover; TSan) |
| `python3 native/media/run_arm_tests.py` | **31/31 OK under qemu-arm (Cortex-A7)**, 5/5 repeats. The stress test moved about 12,000 events across 4 threads with balanced accounting. |
| `python3 private/ownership-program/wp3/mutate.py <scratch>` | **24/25 safety mutants killed.** The survivor (`dispatch_owner_revalidation`) is equivalent through the public API: every owner transition cancels queued starts in the same call. The check is kept as defense in depth. |
| `arm-…-nm -u build/native-media-owned/arm/cammy_media_core.o` | Imports only `memcmp`, `memcpy`, `memset`, `__stack_chk_fail`, `__stack_chk_guard`; no `__*_time64`, libgcc or atomic libcalls |
| `python3 -m unittest tests.test_native_bridge` | OK (WP2 unaffected) |
| A suite run with a broken fixture (`["false"]`) | 31/31 fail: the runner really executes the selected binary |

The tests cover:
- duplicate and conflicting controllers
- replayed and stale-generation starts
- request-deadline and lease expiry before dispatch
- Stop before dispatch
- host loss while queued and while active
- native stall: start timeout → uncertain; cleanup ack not final; late success →
  second cleanup
- late start after Stop
- FIFO late completion
- lease expiry in flight
- start failed, bogus and no-effect results
- submit and cleanup refusal
- cleanup failure → retry → faulted
- cleanup timeout blocking a new owner until a late ack
- release while active
- reconnect/recovery with an old generation and old tokens
- stale events
- a new instance
- startup with the native backend absent
- native, mislabeled, bad-ABI and init-failed tables
- 16 binding rejections with field IDs
- status wildcard rules
- mailbox overflow and threaded posting
- a clock regression

**Not proven:**
- real native cleanup, lock ownership or thread safety of any vendor call
- frame delivery
- in-process loading
- behavior on kernel 4.9
- the fake's FIFO and "no effect" semantics for a real backend

Fake success never makes a physical capability available. Every status line in
the suite asserts `video=unavailable`.

## Remaining native gates

G1 serialization owner, G2 completion source, G3 cleanup meaning, G4 in-process ABI
(time32 sysroot), G5 exit/detach with a live session and G6 hardware acceptance.
See MEDIA-CONTRACT §9. G1 blocks everything else.

## Next integration hook (proposal; WP2 files not edited)

1. Embed one `struct cm_core` (static storage) in the bridge. Call `cm_step()` from
   the bridge thread's loop, right after `cammy_bridge_step()`.
2. Add a second listener for the `CBMQ` media protocol. Bind it only when the media
   backend is `ready`, which is never with the native backend absent.
3. Map connection close and idle drops to `cm_controller_lost(conn_nonce)`.
4. Add a host wake callback so that `cm_post()` interrupts the poll. Until then, the
   loop must poll at ≤ 50 ms while a session is not idle.
5. Portal side: a C client sharing the WP2 codec. Report the phases and the
   `last_cleanup_done` flag verbatim, and never turn an HTTP timeout into "stopped".

## Suggested architecture/status text (for the manager)

> 2026-11-14 WP3: static analysis of the exact sdvcam found that subject mutexes are
> zero-initialized (musl normal, non-recursive). `Notify` runs observers
> synchronously under the notifier's mutex. `SwitchToSoftAp` 0x901EC is reached
> synchronously from CivetWeb request threads, through posted tasks and from GUI
> and presenter paths, and its SOFTAP_ENABLE `Notify` reaches a `PreviewPresenter`
> case that sleeps 500 ms and then creates and starts RTSP with no lock. Media
> entrypoints therefore run on several threads under different locks or none: no
> existing owner, and the native media backend stays unavailable. A local
> owned-media core (`native/media/`) implements leases, typed requests, a bounded
> queue, dispatch revalidation, uncertain/cleanup accounting and a lock-free
> completion mailbox. It is tested only with a fake backend: 31 host, ASan, TSan
> and qemu-arm tests, and 24/25 safety mutants killed.
