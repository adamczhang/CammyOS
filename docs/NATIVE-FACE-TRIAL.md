# GUI-owned ParamFace trial — 2026-11-15

**Latest state:** the [Pikachu trial](FACE-BOOT-TRIAL-2026-11-15.md) booted and
showed its happy image for about ten seconds, with at least8.873s of simultaneous
Wi-Fi video/microphone advancement. P15 observed display/removal. Original S01
was restored. Subsequent reboots block stability acceptance; current stock boot
is stable while the startup guard blocks automatic runtime. Colour/fine detail,
reusable face commands, native animation and full draw retirement remain open.

The real-device library and exact-stock dual-hook candidate passed local native
primitive/loader/ABI/synthetic checks before that physical test. The following
implementation uses one immutable image, with default3s or trial60s delay. It is
not a resident face-control service. Next work is consolidated into the
[integrated0.6 candidate](RELEASE-0.6.0-CANDIDATE.md).

## Concrete implementation

`native/face/` contains the finite surface, native binding, loader assembly and
fixtures. `tools/build_native_face_candidate.py` produces ignored, mode0600
artifacts only. It has no install, flash, arm or launch operation.

The first trial uses one actual ParamFace happy image rendered on the PC at
build time,356×400 BGRA8888,569,600bytes. The immutable pixels live in the
library's read-only data until process exit. Nothing renders C++ or copies a
full frame in the GUI callback, and no pixel storage is freed/reused. This
deliberately avoids depending on an unproved draw/flush retirement fence.
It is a static face proof, not an animated/blinking face or live face-command
transport. The existing StackChan face API still controls the PC preview.

The main attach hook validates a separate48-byte host record, ABI1/profile2
and the pristine stock executable hash. Profile1/status-only is refused. It
initializes a3-second delay and a10-second visibility interval using monotonic
time. Publication is release/acquire; the GUI hook is inert before attach.

Only the native GUI tick creates, positions, updates or removes the canvas.
An atomic Stop request crosses from main/detach to that thread. The surface:

- admits one immutable, panel-bounded image and attempts creation at most once;
- does not show if the first eligible tick is already past expiry;
- removes itself on expiry, Stop, backward clock or active-screen change;
- registers a native delete observer before positioning/installing pixels;
- drops its pointer if the stock GUI deletes the object, including during X/Y
  positioning, and never tries to show it again;
- retains library/pixels when detach or a stopped GUI cannot prove removal.

The ten seconds is a policy evaluated on GUI ticks. It is **not** a guaranteed
physical cutoff if native allocation, drawing, callbacks or the GUI itself hang.
Native allocation assertion paths are unbounded; the fixture does not remove
that hardware risk. The library must never be dlclosed.

## Native API evidence

All addresses are bound to stock SHA256
`8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`.
Upstream LVGL8.3.11 is a source-order/reference comparison, not identification
of the vendor release. In particular its packed image header is incompatible
with the [observed native header](NATIVE-FACE-ABI.md).

| Native entry | Address | Evidence and limit |
| --- | --- | --- |
| Active-screen getter |0x48AAD0|Exact primitive: display+16; NULL uses default display|
| Top-layer getter |0x48AB5C|Exact primitive: display+28; NULL/default path tested|
| Canvas create |0x4AAA3C|Exact primitive calls class-create with class0x8FAD00 and parent, then class-init; those callees are synthetic|
| Canvas constructor |0x4AA9F0|Exact primitive initializes embedded descriptor atobject+84 and calls native image-set-source; setter synthetic|
| Canvas set-buffer |0x4AAA7C|Exact primitive writes cf/width/height/pixels at+84/+88/+92/+100; setter synthetic; NULL buffer asserts indefinitely|
| Image set-source |0x4ABEFC|Native call target identified; complete function not emulated|
| Canvas destructor |0x4AA9E8|Exact primitive tail-calls cache invalidation for embedded descriptor|
| Cache invalidation |0x49C268|Exact stock instruction is `bx lr`: no operation|
| Add event callback |0x496A6C|Exact primitive registers callback/user/filter12-byte record; allocator/spec-attribute setup synthetic|
| Event target getter |0x4969BC|Exact stock `ldr r0,[r0]; bx lr`|
| Object delete |0x496294|Static public delete wrapper; complete tree/destruction not emulated|
| Delete event |33|Static core0x4960FC loads33 before event-send0x49694C|
| Object X/Y setters |0x490838/0x49088C|Exported stock symbols; real binding uses them; fixture substitutes them|

The native canvas is104bytes and its base image is84bytes according to the
class records and observed field accesses. The adapter uses native APIs instead
of copying these structures. Cache invalidation being a no-op does **not** prove
that arbitrary draw buffers can be changed/freed safely. Complete rendering,
parent ownership, deletion reentrancy, display teardown and physical colours
remain hardware/integration questions.

`tools/native_canvas_abi.py` runs only audited short native ranges in Unicorn,
with explicit fake callees. It rejects every other PC target, interrupts,
out-of-RAM writes and execution beyond1,000 instructions/50ms. Eleven assertions
cover the table's primitive claims; the full GUI/app/driver never executes.
The event design also references the primary
[LVGL event source](https://github.com/lvgl/lvgl/blob/v8.3.11/src/core/lv_event.c).

## Exact candidate and loader

The candidate starts from pristine stock, not the earlier connect-triggered
media candidate. Its only five changed ranges are:

1. Main call0x31DB0: attach, original task-queue run, clear publication, detach.
2. GUI call0xE58C0: optional face begin, original timer0x4A7950, face end.
3.452bytes at0x8E2E20–0x8E2FE4: two stubs, identity, names and library path.
4. RX program-header file/memory sizes, still within the same original mapped page.
5. RW program-header memory size+4: explicit zero-initialized pointer slot at
   original BSS end0xAFE098, also within its existing mapped page.

The GUI call remains inside the observed WindowManager mutex bracket. No mutex,
task queue, work mode, network-connect site, watchdog or media entrypoint is
patched. The loader preserves the original arguments, callee-saved registers,
stack alignment and return pair. Missing library/symbol or rejected attach
returns to the original call; the unpopulated GUI slot runs the original timer.
It never dlcloses even a partially loaded library.

The path is `/tmp/cammy-face/<library-sha256>/face.so`. Its name alone does not
authenticate content. Any future launcher must stage/hash/verify a root-owned
private RAM copy before execution; the separate trial launcher is now built and staged as documented above.

The library is built with musl1.1.24/time32 headers against the saved camera
libc. The existing ABI auditor, with a separate expected export list, verifies
exactly three exports, only libc dependency, no constructors/TLS/time64 symbols,
and imports only `clock_gettime`, `memcmp`, `memset`. A64-bit division initially
pulled in libgcc's `raise`; narrowing the nanosecond division removed it without
loosening the forbidden-import check.

The new class is **`gui-face-trial-v1`**, not status-only. A regression proves
that the existing status-only classifier refuses this candidate. Old gates,
TF packaging and `SELECT_GATE=blocked` are untouched. No fixture gate is an
approved production launch mechanism.

## Reproduction and tests

With the existing pinned ParamFace/toolchain, saved camera rootfs, time32
sysroot, project-local qemu and Reachy verification venv available:

```sh
make test-native-face
make -C native/face device binding-fixture
python3 -m tools.build_native_face_candidate --out-dir private/native-face/review-new
```

The focused suite has22 tests with no skips in the verification venv:
17 new face tests plus5 existing image-ABI tests. The C surface fixture separately
checks49 assertions on host, ASan/UBSan and static ARM/qemu. Six synthetic
address-binding cases dlopen the actual release DSO under qemu with the saved
camera libc and fake native APIs: expiry, early detach, external deletion,
screen change, deletion during positioning and detach after showing. That
fixture deliberately interposes a fake clock; the real-app ABI audit requires
the clock import to resolve first to libc.

Unicorn stub tests deliberately clobber caller-saved registers at external
calls and cover successful attach/publication/clear/detach, five refusal paths,
zero-slot timer fallback and GUI begin/timer/end. They check preserved argument
and return registers, callee-saved registers, stack alignment and the exact host
record. They do not prove two-thread scheduling or native timing.

Ignored evidence/artifacts: `private/native-face/` and `build/native-face/`.
The compact checkpoint names the latest exact candidate and verification.

## Following jump

The finite static drawing/coexistence proof is complete. Keep its immutable
pixel lifetime and native GUI ownership guarantees while building reusable
current-boot owner/session-bound face/replay/idle-loop commands. Live transport,
repeated expression swaps, asynchronous draw retirement and Stop behaviour need
local tests and physical acceptance; the single static image does not prove them.
Resolve the later automatic-startup resets before promoting an integrated
candidate. Do not repeatedly re-arm the one-shot demo as the normal workflow.
