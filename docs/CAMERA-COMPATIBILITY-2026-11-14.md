# Camera compatibility inventory — 2026-11-14

Bounded read-only checks through the existing USB ADB transport, after chunk 4.
No file was written on Cammy, no native candidate/init script executed, and no
app restart, signal, mode change, radio change or device-node open was performed.
Raw commands/results and boot identity are private at
`private/ownership-program/pre-chunk5-camera-inventory.json`.

## Confirmed on this boot

- The live `sdvcam`, `S01app` and `libc.so` SHA256s exactly match the saved
  [loader-proof identities](native-bridge/LOADER-PROOF.md). Stock `sdvcam` MD5
  also matches. Saved-libc compatibility now has a live identity cross-check;
  this is not an in-process loader test on hardware.
- `/bin/sh` and its running process resolve to `/bin/busybox`. The exact ash/hush
  implementation was not identified. Kernel status confirms UID/GID 0; `id` is
  unavailable, so future scripts must not assume that utility exists.
- The shell accepts `set -C`, variable-length expansion and integer arithmetic.
  No file was created: actual exclusive-creation semantics remain untested.
- Read-only examples confirm `ls -ldn`, `awk -v`, `tail -c 1`, `wc -c`,
  `cut -c`, `sed -n`, grep word boundaries and native `md5sum` work. SHA256
  was obtained with the existing runtime BusyBox helper.
- `/run` and `/tmp` are tmpfs without `noexec`, root-owned, mode **1777**.
  Their shared permissions make validation of root-owned0700 private staging
  directories necessary; the chunk 4 helper now performs that check.
- The normal TF mount is `/dev/mmcblk0` at `/mnt/extsd`, vfat read/write.
  `/proc/filesystems` lists vfat and msdos; namespace/unshare support is unproven.
- One `sdvcam` process was found. Its descriptor 3 points to `/dev/watchdog`;
  only symlink metadata was read. Its actual soft/hard core limits are unlimited,
  confirming the need for candidate-only core suppression before launch.
- The watchdog sysfs device exists but lacks `identity`, `nowayout`, `timeout`
  and `state` attributes. Those reads failed. Effective timeout, close behavior
  and nowayout remain unknown; no watchdog device was opened to query them.

## Limits and next use

The boot was about293 seconds old at the uptime sample. These are **late-boot**
facts, not proof of S01-time card availability, executable mounts, free RAM,
watchdog timing, power-loss record durability or a successful stock restore.
The core-limit setter was not exercised. Home Wi-Fi reachability was not tested
by this USB inventory. No physical capability or hardware gate is enabled.

Chunk 5 can proceed with one-shot selection fixtures using these compatibility
facts. Keep the shipped gate blocked and treat early-boot, persistence, core-limit
and recovery measurements as explicit prerequisites for a later approved trial.
