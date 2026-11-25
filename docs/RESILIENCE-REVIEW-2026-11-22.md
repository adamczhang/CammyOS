# CammyOS resilience review · 2026-11-22

Reviewed the 0.8.3 candidate and main at `a75c54e`, the immutable stock
executable, and the unfinished 0.8.4 stash. Incident times and reasons are
field observations from November 21. This review made no device measurements.

## Failure domains, ordered by impact

| Rank | Domain | Evidence and consequence |
|---|---|---|
| 1 | Vendor process, GUI and watchdog | Both field faults were `native-pid` (after a tap at 17:22 and during the fourth photo at 20:05:28). This code means the probe did not find exactly one PID. It gives no exit signal, stack or causal attribution. The association with window and mode changes is a hypothesis. The CammyOS path keeps a native top-layer canvas across screen replacement, and the GUI tick can continue into the chooser after synchronous deletion in its clickable callback. Creation and callback registration run vendor allocation paths under the same mutex as the stock timer. The process also owns the vendor media/display and watchdog. [Renderer](../native/face_service/face_service.c), [binding](../native/face_service/native_service.c), [ABI evidence](NATIVE-FACE-TRIAL.md) |
| 2 | Bootstrap, NOR and recovery | S00 applies stock power settings. S01 selects stock or stages verified native code and writes a per-boot stamp. S99 stages and verifies a release before writing a guard and starting the runtime. A leftover latch, native fault, inflight selection or prior claim can strand the next boot in stock. The legacy late-start power retry is separately opted in. The stash's counter can cross releases and its uptime test conflates elapsed boot time with continuous health. [Selector](../scripts/rc06/selector-retry.sh), [bootstrap](../tools/rc_runtime_integrity.py), [health](../scripts/rc06/health.sh) |
| 3 | Forensics | Boot latch and short reason survive on NOR. Candidate core dumps are disabled, RAM logs vanish on reset, and optional TF heartbeat logging stops after 420 seconds. Neither observed incident has a surviving kernel trace. [Startup](../scripts/tf-runtime-startup.sh), [launcher](../native/activation/launch_selected_or_stock.proposal) |
| 4 | Stock camera HTTP, CCC capture and preview | CCC capture waits up to 120 seconds for a photo. The face is hidden only after the mode switch and shown before an unverified restore. Transfer and processing can extend total time. The 5 → 39 → 111 second sequence cannot identify the blocked phase. A repeated stock filename is reused on the PC. RTSP decoding, portal keepalive, other HTTP callers and physical keys have separate ownership. [Capture](../host/cammy_capture.py), [CCC body and Eyes](../host/c3/body.py), [voice dispatch](../host/c3/voice.py) |
| 5 | Flash consumption and maintenance | The 512 KiB JFFS2 settings overlay carries boot files, policy, per-boot claims and install rollback copies. 0.8.3 staging hit ENOSPC in the field. Preflight checks identity, while installation disables selection before creating backups and staging new files. [Installer](../tools/rc_install.py), [prior audit](DEVICE-RESOURCE-AUDIT-2026-11-17.md) |
| 6 | Local supervision, portal and UI | The shell respawns exited UI processes a bounded number of times. Portal startup failure can exit before the health loop, and the health loop checks process identity and GUI freshness rather than portal request progress. A stale UI can stay alive. Display IPC intentionally retains bounded old mappings. [Runtime generation](../tools/rc06_build.py), [UI](../native/ui/cammy_ui.c), [health](../scripts/rc06/health.sh) |
| 7 | Radio, TF and native media worker | Station uses wpa_supplicant and DHCP. The AP uses hostapd and udhcpd. Association alone does not prove end-to-end reachability. A lease-scoped media worker carries its own persistent guard because it temporarily owns mode and radio restoration. Incomplete cleanup must retain stock fallback. Missing TF, invalid staged bytes, or failed AP services are independent of GUI health. [Startup](../scripts/tf-runtime-startup.sh), [media worker](../scripts/rc06/media-worker.sh), [media transaction](../scripts/native-media-start.sh) |
| 8 | Audio and PC voice | The shared-capture profile depends on ALSA, mount ownership and IPC. Speaker runs a separate bounded guardian job that joins stock dmix and refuses if the PCM closes or hub mode is zero, as observed after roughly 70 minutes. CCC can have a live Gemini connection without PCM. Family/local STT and TTS, remote providers, the user service, API lease and LAB mount are separate failure domains. [Shared capture](../scripts/shared-capture.sh), [speaker](../native/speaker_probe/guardian.c), [CCC](C3.md) |

### Process ownership

- Stock kernel, init, display, ISP, motor/input, audio and watchdog remain
  vendor-owned. Stock sdvcam provides the GUI, stock HTTP and event server,
  RTSP and media-mode teardown. The face DSO shares that process and its
  failure domain. The loader and native media client are finite jobs.
- S99 launches a RAM-backed runtime supervisor. cammy-ui owns Settings
  state and frames. The portal owns the authenticated lease, request bounds,
  input safety and audio/preview subscriptions. The speaker guardian,
  recorder child, shared-capture guard and media worker each have a
  separate termination and cleanup boundary. Radio client and DHCP or AP
  daemons provide the network path. Fault evidence is a finite child and
  can be lost with sudden reset or supervisor disappearance.
- The PC user service supervises lease renewal, HTTP capture, ffmpeg RTSP
  decoding, microphone reads, speech gating, echo handling and output.
  Gemini Live connection, reply PCM and actual speaker output are separate
  health stages. Family/local STT and TTS, Brain provider calls, and the
  Calendar broker have separate resources and availability. The desktop and
  agent front ends rely on the PC service and the boot-bound lease.

## Resource baseline

Sizes below are regular file bytes from `candidate-0.8.3-speaker-any-level`.
The native executable is 11,335,176 bytes, `face.so` is 39,323,436 bytes,
and 64 raw 569,600-byte BGRA frames account for 36,454,400 bytes of that
library. Only adult Cammy uses CPK1. The loading planes take 144,000 bytes.
The TF slot contains 52,575,900 bytes including the 1,916,985-byte runtime.
The runtime's largest files are wpa_supplicant (772,260), cammy-ui (272,560),
BusyBox (238,832), and portal (167,524). Internal bootstrap totals 52,316
bytes, before rollback, history, filesystem metadata and erase reserves.

The native and runtime files above amount to about 50 MiB of staged data
backed by RAM. Read-only mappings of those same pages should not be counted
again as private anonymous allocations. The adult decoder adds 1,566,404
bytes of static working storage when touched, and the menu uses shared pages
of about 1.1 MiB per start, with at most five starts. The device has about
507 MB RAM and no observed swap. The earlier idle sample observed 332.6 MiB
available on 0.7.6, which does not measure peak ISP buffers, contiguous
memory pressure or peak GUI/CCC media activity on 0.8.3. TF capacity and
NOR free space vary with device history. [Earlier measurement](DEVICE-RESOURCE-AUDIT-2026-11-17.md)

## GUI evidence and architecture choice

The stock hook is an ARM branch at 0xE58C0 within the WindowManager +0x24
lock. The caller keeps the WindowManager in r5. The shim preserves r0–r3 for
the stock call, return r0–r1, callee-saved registers and 8-byte stack
alignment. The software timer is called once on both paths. The stock loop
dispatches its queued messages after the timer and before unlocking, so a
post-timer check alone cannot detect all window changes in the same bracket.
The next pre-timer sample must also be checked. The native object observer
can invalidate the canvas synchronously. Clearing ownership before our own
delete prevents duplicate removal. Registering the observer itself still
depends on vendor allocation and initialization.

Keep a small GUI-owned adapter inside sdvcam because that process owns the
LVGL context and LCD. Remove the canvas and hold off redraw while stock
changes display or mode. Keep pixel storage live for the whole process and
write only a buffer the canvas is not using. Move asset rendering and
compression to build time, keep UI policy and network or media management
out of sdvcam, and measure decode time under the vendor mutex. A fully
out-of-process renderer still requires a vendor-owned display bridge or
independent compositor. Opening framebuffer or restarting the vendor app
would introduce a second owner of display, watchdog and ISP resources.
Stock without the adapter is the smallest safe fallback and a useful
control during diagnosis.

Keep the portal and bounded audio/media helpers because they provide lease
and hardware ownership. Keep current network fallback and stock rollback.
Avoid new always-on forensic processes, full core dumps, and raw frame
banks. Treat the frequent diagnostic poll and unbounded boot history as
separate candidates for deletion or reduction after the guarded release
passes. Cold-boot retry should be narrower than normal health and have one
durable spend point. Measure JFFS2 reserve and VFAT behavior on hardware
before any resident rollout. Current work and acceptance order are tracked
in [PLAN.md](PLAN.md).

## Offline candidate measurements

| Regular file bytes | 0.8.3 | 0.9.0 |
|---|---:|---:|
| Native face library | 39,323,436 | 6,776,288 |
| Patched stock executable | 11,335,176 | 11,335,176 |
| Runtime payload | 1,916,985 | 2,012,530 |
| Sum staged to tmpfs | 52,575,597 | 20,123,994 |
| TF slot | 52,575,900 | 20,124,296 |
| NOR bootstrap | 52,316 | 57,072 |

The pre-rendered child art is 3,907,657 bytes packed. All 64 decoded frames
match the original BGRA bytes. The decoder still has two 569,600-byte slots
and an adult key plane. The saved tmpfs file bytes are 32,451,603 (61.7%).
This is a file budget. Peak RSS, decode time, GUI stalls and JFFS2 space
reclamation require the ordered bench checks in [PLAN.md](PLAN.md).

The packed bank decodes individual frames, so the runtime avoids staging
or inflating a second complete library. The tradeoff is work under the GUI
mutex on a cache miss. The read-only install probe measures available
JFFS2 bytes before selector changes. Its estimate includes changed boot
targets, missing rollback copies, block rounding and a provisional 32 KiB
reserve. The installer keeps its stock-first transaction ordering in case
real JFFS2 garbage collection still runs out of room.

The snapshot is attempted before the NOR latch write and publishes
independently. A cut can lose TF evidence while leaving a quarantine
record. The low-space threshold is 1 MiB of free TF capacity. The
0.9 build does not change stock PCM ownership or add an automatic audible
health probe. The CCC UI distinguishes missing reply PCM from a refused
speaker job.
