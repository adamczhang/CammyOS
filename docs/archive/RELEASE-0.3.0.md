# CammyOS0.3.0 — TF runtime, boot/probes verified

Built, TF-updated and boot/probe verified on2026-11-14. **Network media remains
unavailable; full companion acceptance is not complete.** This candidate preserves the vendor kernel,
bootloader and `sdvcam`; it is a userspace TF folder update. It does not make a
new native media-start command, internal firmware image or finished companion.

## What changed

- Four named, read-only metadata collectors: runtime, network bindings, display
  layout and ALSA PCM state, with source availability/errors/truncation.
- One supervised worker with one-second parent/child deadlines, crash cleanup,
  cancellation and bounded RAM results. Compatibility diagnostics now uses the
  same worker while retaining its successful v0.2 response shape; busy is409,
  cancellation/failure/timeout is503.
- Incremental nonblocking HTTP request handling. Stop remains responsive in local
  blocked-collector/diagnostics, slow-RTSP and partial-request fixtures.
- Nonblocking native event startup,30second4016 keepalive without lease renewal,
  bounded fixed-target RTSP DESCRIBE readiness and explicit status/recheck APIs.
  Start returns202accepted; SDP is separate from frame delivery.
- Gateway/SDK routes plus desktop probe/run/cancel and readiness displays. The
  simulator models synthetic probe deadlines and preview failure states.
- A further saved-app trace distinguishes QZ from another backend's stream-enable
  command. No new verified native bootstrap was found or guessed.

No camera, TF card, internal flash, running PC gateway or PC audio/sleep settings
were changed while building this candidate. No sensor or probe starts at boot.
Camera face, motor, capture and speech adapters remain unavailable. Home-network
video is still unverified. Richer process ownership, mixer routing and IPC mapping
collectors are deferred; `audio-state` only reads existing metadata files.

## Local artifacts and validation

- `dist/cammyos-tf-runtime-0.3.0.tar.gz` —646235bytes.
- SHA256: `1778d0bffae3c7692ecb7e72c8315247383c021dd6858789ed79ea504019c5f1`.
- `dist/cammyos-tf-runtime-0.3.0.tar.gz.sha256`.
- `dist/tf-runtime-0.3.0/CammyOS/` —strictly verified local staging folder.

Two package builds produced identical bytes. The payload has14files, checksums,
manifest and license/source notices, with no credentials or stock firmware binary.
The v0.2 archive/staging and prior rollback data remain intact. Artifacts/private
evidence are ignored by git.

Validation: **65native/host tests**, **15ARM protocol tests** and **9ARM preview
fault tests** passed. The native suite includes archive corruption/traversal/link
rejection and reproducible temporary packaging. ARM uses Cortex-A7 userspace
emulation and local fake services; it does not emulate the board/kernel/media
hardware. Stop timing assertions are local and not a physical hardware guarantee.

Rebuild and stage to a new, nonexisting local folder:

```sh
make test
make arm
make test-arm-local
python3 scripts/package-runtime.py
python3 tools/stage_tf_release.py dist/cammyos-tf-runtime-0.3.0.tar.gz --stage-name tf-runtime-0.3.0
```

The stager refuses an existing destination. These commands do not write a card.

## TF update and actual-camera boot/probe checks — complete

The card update completed2026-11-14. Its0.2 runtime matched the accepted archive,
was backed up under `private/tf-updates/20261114T151950Z/CammyOS-before` and moved
to `CammyOS-rollback-0.2.0` on the card. All14new files, both0.2 copies and
preserved DCIM/.data/earlier0.1 rollback trees passed checks after remount. The
card was safely unmounted/powered off. Private record: that folder's `update.json`.
After reinsertion, actual portal and RAM release report0.3.0, HOME_STATION and
startup_pending false. Adam reports normal live camera view. The gateway was
restarted; all four probes completed with every source available and no truncation.
Read-only USB matched display geometry/depth/stride and playback/capture metadata.

A12second owned preview check connected the event service and sent4016 once;
RTSP8554 stayed unreachable during the five-second budget. No SDP/frame collected.
Stop response was12.3ms in this one live call. A separate1second lease expired
and closed preview. Final owner null and preview idle. Thus boot/probes/readiness
reporting/cleanup are verified; video is not. Private evidence: `boot-accepted.json`,
`preview-accepted.json`, `lease-expiry.json`, `usb-reference.json` in that LAB folder.
Steps1–6 below have been exercised within those limits. Step7 is the rollback
procedure if a regression appears; rollback was not needed or tested this time.


1. Power Cammy off and put its TF card in the PC reader. Identify the actual device,
   expected camera-formatted UDISK filesystem and UUIDC87D-17EA; do not assume its
   current `/dev/sdX` name. Check LAB is mounted before backup/writes.
2. Back up the entire existing `CammyOS/` folder and manifest/hash evidence on LAB;
   retain v0.2 on-card rollback separately. Preserve DCIM, .data, existing earlier
   rollback copies, private configuration and the restored blank boot-stub region.
3. Copy only the verified runtime folder, verify every new/rollback file, unmount,
   remount and verify again, then safely eject. No format or raw-sector write.
4. Insert while the camera is off; boot on USB power with Wi-Fi controls untouched.
   Confirm normal native view, HOME_STATION, release0.3.0 and eventual latch clear.
5. Restart the reviewed PC gateway source for its new routes. Validate identity,
   diagnostics, catalogue and one read-only job; compare known display/audio/
   listener metadata with a bounded read-only USB reference if available.
6. With an explicit short lease, start one preview check, inspect readiness and
   binding evidence, then Stop/release. Confirm no active session remains and no
   unexpected native view/network/power changes occurred. Decode a frame only
   through a later explicitly requested host media operation if a server exists.
7. If startup/network/native operation regresses, stop testing and restore the
   backed-up v0.2 folder via the powered-off TF workflow. Inspect the startup latch
   and evidence; do not blindly clear it or repeatedly rerun a failing startup.

Successful boot/probes still do not complete the live-video, audio, gimbal, screen
face or companion milestones. Record actual results separately from this prepared
candidate. See [architecture](../../architecture.md), [readiness](../PREVIEW-READINESS.md),
[probe contract](../PROBE-SUPERVISOR.md) and [app trace](../MEDIA-STARTUP-TRACE-2026-11-14.md).
