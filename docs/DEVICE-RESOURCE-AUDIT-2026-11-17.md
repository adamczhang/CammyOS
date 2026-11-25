# Camera-side resource and reliability audit · 2026-11-17

The running Cammy had substantial RAM headroom in a short idle-connected
sample. The staged 0.7.9 candidate has recovery and lifecycle defects that
matter more for the next trial than RAM capacity. Selection, playback and
menu recovery need explicit regression gates.

## Scope and evidence

Three independent reviews covered CammyOS source at `0a1613f` and the
130-file candidate `17ae76337dcd67403642288f`. They covered the selector,
init/runtime, Wi-Fi, portal, media workers, input and display. The stock
kernel, ISP and camera application were observed at their interfaces.
A separate native speaker fixture reproduced the late-append loss.
Passive measurements came from the installed **0.7.6**, connected and
quiet, at about 58 minutes of uptime. The camera was left running.

| Resource | Observed or calculated |
|---|---|
| Device RAM | 332.6 MiB available, 48.1 MiB shared memory, no swap on 0.7.6. The snapshot cannot determine peak heap, CMA pressure or growth. |
| CPU | 58.3% busy overall across 20.07 s on one core, including 42.2% from stock `sdvcam` and 0.15% from `cammy-ui`. The load average also includes tasks in uninterruptible wait. |
| Native payload | 0.7.9 stages an 11,335,176-byte stock-derived executable and a 36,471,484-byte face library in tmpfs. The full TF slot is 49,696,557 bytes. |
| Face bank | 64 raw BGRA frames, 569,600 bytes each. The library is 34.8 MiB against a 40 MiB build cap, leaving 5.2 MiB. Both packs fit. A third similar pack would require compact assets. |
| Display sharing | One double-buffer page is 1,143,296 bytes. Up to four mappings can remain pinned per `sdvcam` process, including stale pages after menu restarts. |
| Internal storage | The settings overlay shares a 512 KiB JFFS2 partition. Current free space was not measured. Healthy boot history and archived recovery records have no retention bound. |

The face uses pre-rendered images directly and publishes display updates only
when the active frame changes. Speaker and microphone run in bounded jobs,
with explicit ownership and cleanup. The main portal has finite client slots
and input deadlines. These are appropriate choices for this camera.

## Release blockers and recovery

| Finding | Evidence and consequence | Required verification |
|---|---|---|
| Resident-policy handoff | The installed record names `440191e11935ce0f116f0e18` (0.7.6). The installer preserves it and candidate health rejects it when the running release differs. | Test release replacement with an existing resident record. New authority must remain explicit. [Installer](../tools/rc_install.py), [health](../scripts/rc06/health.sh) |
| Media refusal cleanup | Cammy's PATH and runtime BusyBox lack `rmdir`. A clean refusal can leave the lock directory, blocking the next request until health times out. | Exercise refusal followed by retry with the device applet set. [Media worker](../scripts/rc06/media-worker.sh) |
| Startup without station Wi-Fi | AP fallback remains active for the boot. Before first health commit, it keeps the startup guard because health requires `HOME_STATION`. A later boot can select stock or spend the opted-in power retry on the outage. | Exercise a router outage, fallback and later return with a safe guard state. [Startup](../scripts/tf-runtime-startup.sh), [health](../scripts/rc06/health.sh) |
| Speaker acceptance and drain | Portal acceptance can allow 64 chunks of up to five seconds. The guardian's total cap is 60 seconds. Exceeding it can fault the speaker until recovery. A separate fixture reproduced a late append failing to play between feeder close and guardian exit. | Pair the limit on both sides. Verify close, append, cancel, and restart ordering offline before testing audible drain. [Portal](../src/cammy_speaker.h), [guardian](../native/speaker_probe/guardian.c), [playback audit](FACE-LOOP-RESEARCH-2026-11-17.md#speaker-timing-audit) |
| Menu and face recovery | The UI supervisor allows five starts but the face service maps four pages. The 32-create display recovery counter also includes normal preview toggles. | Exercise repeated legitimate view transitions and each supported restart, followed by an unexpected LVGL deletion. [Builder](../tools/rc06_build.py), [native binding](../native/face_service/native_service.c), [renderer](../native/face_service/face_service.c) |

## Efficiency and endurance

- **Make the media supervisor responsive.** The native service client bounds
  individual waits using the passed deadline. The shell preparation sequence
  has its own retries and sleeps, and lacks a single enforced end-to-end
  deadline. The parent supervisor waits for it inline. Exercise cancellation
  throughout each phase and verify the Wi-Fi and UI watches still run.
  [Native start](../scripts/native-media-start.sh),
  [native service client](../src/boot/native_service_client.c)
- **Reduce constant device work.** The portal, input guardian and UI set up
  roughly 100 timer wake opportunities per second. The pre-commit shell loop
  also starts roughly 15–20 helper processes per second by static count.
  Use deadline-based waits when idle and measure CPU, process starts and
  context switches before and after. Keep audio and release deadlines intact.
  [Portal](../src/cammy_portal.c), [input guardian](../src/cammy_input_guardian.h)
- **Avoid repeated parsing and media polling.** Portal parsing can clear or
  copy more than 200 KiB on each read, even for small requests. On fragmented
  maximum-sized uploads the work can be tens of megabytes of RAM traffic
  per request. Parse headers once, retain a bounded body and watch the audio
  pipe directly. Check fairness under sustained audio and slow connections.
  [Request loop](../src/cammy_portal.c), [mic streaming](../src/cammy_audio_stream.h)
- **Bound retained logs and flash history.** Successful health commits keep
  one claim per boot in internal flash, with more files for recovery and
  installs. Bound retained evidence and surface low-space failures. Several
  RAM logs append throughout a boot. Optional TF boot tracing is limited
  to 420 seconds and 200 files, but its frequent syncs and process inspection
  skew timing. [Health](../scripts/rc06/health.sh),
  [runtime](../scripts/tf-runtime-startup.sh)
- **Tighten release integrity and provenance.** Startup hashes TF files
  after copying them and later sources a helper from TF. Verify the RAM
  bytes against a manifest pinned by bootstrap and retain the verified
  helper in RAM. Bind native sources, including face and portal sources, to
  the build receipt. [Runtime](../scripts/tf-runtime-startup.sh),
  [builder](../tools/rc06_build.py)
- **Compact assets before growing the gallery.** The two current banks use
  34.8 MiB, including two duplicate frames. Offline run-length encoding
  reduced the bank to about 5.1 MiB before decode buffers. Verify pixel
  parity and LCD timing before adding a decoder to the camera.
  [Asset generator](../tools/build_face_service_assets.py)

## Hardware verification after the fixes

Record baseline and sustained `MemAvailable`, shared memory, process RSS,
CPU, context switches, helper starts and internal free space separately.
Exercise cold start, saved-network fallback and recovery, preview and mic
rollover, long audio with cancellation, LCD mode changes, and repeated UI
restarts. Compare idle, active and recovered states. The short 0.7.6 sample
and the older [0.7.0 audit](MEMORY-AUDIT-2026-11-16.md) are useful reference
points, with different releases and workloads.
