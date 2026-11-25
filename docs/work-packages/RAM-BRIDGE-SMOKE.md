# RAM-only status-library smoke — 2026-11-14

**PASS: exact release library + SYNTHETIC host on the real M105.** This is a
short, disposable-process library test. It does not establish in-`sdvcam`
integration, native hardware ownership, media availability or candidate boot
acceptance. All six physical capabilities remain unavailable. No gate beyond
this synthetic-host smoke is closed.

The user authorized this bounded RAM-only test. Implementation was made from clean
`main` at `2ca554b66577ee9bd4659c10dd54b9e910f26d7a`, without staging or committing.
The existing loader, bridge, library and candidate sources/artifacts were preserved.
The [stock bootstrap test](../BOOTSTRAP-HARDWARE-2026-11-14.md) remained restored.

## Exact artifacts and local validation

The new [dedicated host](../../native/smoke/smoke_host.c) reuses the loader's saved-camera
musl 1.1.24/time32 profile. It is a separate ARM32 hard-float executable, with
`/lib/ld-musl-armhf.so.1` as interpreter and only `libc.so` needed. All imports are
provided by the saved camera libc; no time64, hardware, exec, signal or `dlclose`
imports occur. The host has compile-time checks for 4-byte `time_t`, 8-byte
`timespec`, 104-byte `stat` and the 48-byte host record. It uses the stock source
hash in that record solely to satisfy the library contract; its caller is explicitly
**SYNTHETIC**, with no native object pointer or native dispatch.

- Exact existing library, 21,456 B: SHA256
  `db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065`.
  Its existing ABI audit passed again (42 imports, exactly two exports).
- New `smoke_host`: SHA256
  `835d9eed0b49136452ac1c5a447e51777d4378a0fe629d84a165e34f659a4a88`.
- Local qemu success path used those same bytes and the saved libc. Wrong-source-hash
  refusal returned `-EPROTO` with no socket or descriptor leak. No refusal, stress,
  signal, crash, FD exhaustion or fuzz case was run on the camera.
- qemu timings for this run: load 987 µs, attach 10,948 µs, detach 1,590 µs.
  These are emulator results, not board timing or a worst-case bound.
- The dedicated success trace has no `ioctl`, `execve`, kill, signal installation,
  mount, reboot or `/dev/` access. `/proc/sys/kernel/random/boot_id` is read;
  no sysfs device access is used.

Commands, from the canonical Cammy-OS root:

```sh
make -C native/smoke smoke
python3 -m unittest tests.test_bridge_smoke -v
python3 -m tools.bridge_smoke local
python3 -m tools.bridge_smoke device --local-report private/bridge-smoke/20261114T233700Z-local-eea8b2/local.json
```

The local command records the full build invocation, ABI audit and qemu
trace in private evidence. Its exact success invocation was:

```sh
trial="$PWD/private/bridge-smoke/20261114T233700Z-local-eea8b2"
unshare -rm sh -c 'mount --bind "$1" /tmp && shift && exec "$@"' sh \
  "$trial/tmp-success" \
  '<projects>/Cammy Bot/tools/build-deps/usr/bin/qemu-arm' \
  -strace -L "$PWD/build/native-loader/device-rootfs" \
  "$PWD/build/bridge-smoke/smoke_host" \
  "$PWD/private/ownership-program/wp2a/library/libcammy_native_status.so"
```

The refusal invocation used `tmp-reject-hash` and appended `reject-hash`.
The `local` runner supplies fresh directories for replay; existing lock files are
never reclaimed by the smoke host.

It uses `unshare -rm`, bind-mounting a
fresh local evidence directory onto `/tmp` inside that namespace; `qemu-arm
-strace -L build/native-loader/device-rootfs` runs only the dedicated host.
No replacement libc or vendor executable was staged on the camera.

## Real-device observations

The existing root ADB server/transport was used: port 5038, serial 20080411,
legacy PTY shell, with bounded command timeouts. No new ADB server or mode change.
`/tmp` was live-verified as root-owned 1777 executable tmpfs with no submounts.
The fixed `/tmp/cammy-bridge` namespace was absent before exclusive creation.
Both trial directories were root-owned 0700, with recorded inode and random owner
marker. Both binaries were exact-hash verified on camera before execution.

The only executed payload was `smoke_host`, PID 5527, starttime 192029 in boot
`cbd60ebd-629e-4e38-b533-810d56b3901f`. Its `/proc/self/maps` recorded the exact
staged library under `/tmp/cammy-smoke-b983e92132ae3e7a02a2b426/`. There was no
native-app attach, injected call or native-object dereference.

Observed results:

- Hello/status/capabilities succeeded over private AF_UNIX `SOCK_SEQPACKET`, with
  one consistent instance and the actual camera boot ID. Status was native-kind,
  ready/none, with hardware-actions and owner-present flags clear. Native-kind is
  the library's backend label; it does not change the synthetic caller identity.
- Only status IPC was available. Video, audio capture/playback, gimbal, display
  and lifecycle handoff were unavailable/not-in-protocol-v1.
- Board timings: load 316 µs, attach 464 µs, detach 1,255 µs. One sample only;
  neither a worst-case nor in-native-app timing guarantee. The ADB launch/wait
  window was 0.129156 s, including an intentional 100 ms startup bookkeeping
  window to let the launcher record the child's PID/starttime.
- Serving had seven descriptors and two threads; every descriptor beyond stdio
  in that snapshot had CLOEXEC. Normal detach removed the socket, restored the
  exact baseline descriptor set (three) and thread count (one). No library unload
  or abandoned-state free was attempted. Shell exit marker was zero; the PID
  was independently absent afterward. Emergency termination was not needed.
- After confirming no executable from this trial remained, cleanup checked the
  owner markers, numeric ownership/mode, directory inode and member allowlists.
  The only bridge residual was its own lock file. Both owned directories,
  including that lock, the two binaries and RAM logs/PID records, were removed.
  No process, socket or directory from this test remains on the camera.

## Preservation and limits

Pre-stage, pre-run, post-run and final snapshots matched for kernel boot ID,
version/cmdline, stock native PID 778/starttime 166, watchdog fd3 identity
(inode 3178, target `/dev/watchdog`, flags and mount ID), mount table, and the
native process's mappings. The test library never appeared in native mappings.
Read-back SHA256s matched before/after for:

- Stock S01app:
  `3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136`.
- Stock sdvcam:
  `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`.
- libc, `/etc/inittab`, init `rcS` and `rc.final`, and the existing hash utility.
  Exact values are in the private snapshots. Kernel image/flash blocks were not
  read or hashed; boot identity here means boot ID plus version/cmdline.

The home gateway remained connected to `<camera-ip>:8787` with real CammyOS 0.3.0
identity, simulation/offline false, sensing not started by the gateway, no lease
owner, sensing default off, preview idle/inactive and no event connection. Only
GET health/state/preview status was used. No service was restarted. These software
observations support preservation of normal operation over the short trial;
no new visual LCD acceptance or long-duration stability claim is made.

All camera writes were confined to the two owned tmpfs directories. No flash,
startup/app/config, TF, selector, device-node, watchdog, framebuffer, media,
audio, motor, radio or debug-FIFO operation was requested. No reboot, native-app
signal/restart or TCP listener was used. The runner's sole emergency signal path
is offline-tested and requires the trial PID, starttime, boot identity, exact
executable path and hash; it was not exercised on hardware. Detach abandonment
would fail this success test; the host pins the library/state until process exit.

Drive readings before/after: LAB (nvme1) 47.85°C, nvme0 58.85°C,
nvme2 59.85°C; none reached the 81°C stop threshold.

## Evidence and handoff

Private final checkpoint: `private/bridge-smoke/current.json`.
Real run: `private/bridge-smoke/20261114T233706Z-device-673136/` contains
`result.json`, `before.json`, `after.json`, `hardware.jsonl`, `exit.txt`,
`ownership.json`, and the gateway snapshots. Local receipt, build log, library
ABI report and qemu traces are in
`private/bridge-smoke/20261114T233700Z-local-eea8b2/`.
All logs, binaries and device/process data remain ignored/private.

The [runner](../../tools/bridge_smoke.py) and
[offline tests](../../tests/test_bridge_smoke.py) passed 23 tests (36 with the
reused bootstrap-readiness tests), covering identity gating, RAM confinement, exact protocol/physical-capability invariants, lifecycle cleanup,
foreign-directory/member refusal and emergency PID-reuse/hash refusal. The
project-specific memory-bank recall returned HTTP 404; current repository docs
and live checks supplied the project evidence. No additional hardware action is
pending; manager review and integration remain.

## Manager integration review

The manager independently repeated the exact-host ABI/qemu success and
wrong-hash refusal checks, reproduced the focused suite, and read the current
camera state without rerunning the device payload. Boot/native/watchdog/hash,
mount and native-map identities still match the child's post-run snapshot; the
trial process and both owned RAM paths are absent, and the home gateway is idle
and connected.

One replay guard was strengthened: failure of the launcher’s core-limit setter
now stops before starting the host or writing PID/result files. A local shell
regression forces that primitive to fail and verifies no host execution or trial
records occur. The host/library bytes are unchanged. The revised focused suite
passes 37 tests (24 smoke tests plus 13 reused bootstrap tests); this guard did
not require another hardware run. Final combined-suite results are recorded in
STATUS and the private manager checkpoint.

Final manager combined verification: **417 repository tests passed** in 117.236s.
Three pre-existing HTTPError ResourceWarnings appeared; no test failed.
