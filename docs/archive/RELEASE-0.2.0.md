# CammyOS 0.2.0 — prepared TF runtime release

Built locally on 2026-11-14, then installed via the TF card and **boot-verified
as portal/runtime0.2.0 on home Wi-Fi**. The earlier read-only investigation ran
on portal0.1.0. This is a userspace TF
payload, not a kernel, flash image, boot-sector update, or a completed companion.

## Changes

- Authenticated `GET /v1/diagnostics` reads a fixed allowlist: kernel release,
  runtime release/state, startup-latch presence, framebuffer virtual size, pixel
  depth/stride, ALSA cards, and card0 playback/capture hardware parameters.
- Reads are bounded and JSON-escaped. Missing files return null. There is no shell,
  caller-selected path, mixer write, framebuffer write, capture/playback, UART
  command or settings change in this endpoint.
- Python SDK and host gateway expose diagnostics. The desktop has an explicit
  read-diagnostics button. A v0.1 body returns an understandable unavailable error.
  The PC gateway has been restarted and the new route verified against the camera.
- TF startup copies a verified `RELEASE` marker into RAM for diagnostics. Its
  existing radio, timing, failure latch and fallback behavior are retained.
- A deterministic runtime packager and strict verifier/stager prepare a complete,
  credential-free `CammyOS/` filesystem folder under local `dist/`.

Face, gimbal, microphone capture and speech adapters remain unavailable on actual
hardware. The new diagnostics help investigate their ownership conflicts; they do
not resolve them by taking control away from the stock application.

## Artifacts and rebuild

Prepared files:

- `dist/cammyos-tf-runtime-0.2.0.tar.gz`
- `dist/cammyos-tf-runtime-0.2.0.tar.gz.sha256`
- `dist/tf-runtime-0.2.0/CammyOS/` — verified local staging folder

```sh
make native
make arm
python3 scripts/generate-api.py
make test
make test-arm-local
python3 scripts/package-runtime.py
python3 tools/stage_tf_release.py dist/cammyos-tf-runtime-0.2.0.tar.gz --stage-name tf-runtime-0.2.0
```

The stage folder must not already exist. Choose a new stage name when verifying a
repeat build. The archive contains static ARM portal/network helpers, launcher,
startup/DHCP scripts, release marker, license/source notices, `manifest.json` and
`SHA256SUMS`. It contains no Wi-Fi configuration, API credential, vendor camera
application, rootfs dump, init-hook installer or boot sectors. Existing binaries
for network helpers/launcher are reused from the verified local build inputs.

The manifest and SHA256 detect corruption, not authenticity. Packages are for
private local use; matching complete sources/notices and license compliance are
required before redistribution of bundled third-party components.

## Card-update procedure — performed for this release

1. Power the camera off. Put the TF card in the PC reader; identify its actual
   mounted FAT volume. Do not format it or write to the raw block device.
2. Back up the existing `CammyOS` folder on the PC and preserve the rest of the card.
3. Copy the verified staged folder to a temporary folder on the card. Verify its
   hashes after copying. Keep the previous folder as a rollback copy, then replace
   the active `CammyOS` directory while the camera is off.
4. Flush/eject the card, insert it, and boot. Allow the existing delayed startup to
   complete. Verify portal0.2.0 and diagnostics before testing anything else.
5. If necessary, restore the previous folder while powered off. The existing
   persistent startup latch may also need inspection/manual clearance; restoring
   files alone does not guarantee recovery from a latched failed boot.

The staging tool deliberately writes only under local `dist/`; it cannot install
onto the camera or automatically choose/mount a card. No camera-side hook or
credential changes are needed for this release, assuming the currently tested
S99cammy-tf hook remains installed and enabled. The existing launcher runs from
card before the runtime checksum check; this is not a signed secure-boot chain.

## Acceptance

- Native/host suite:37tests passed, including package reproducibility, corrupted
  files, extra members, symlinks/traversal, no overwrite, and diagnostic JSON
  escaping/bounds/missing-file behavior.
- Actual ARM portal under local Cortex-A7 user-mode emulation:13tests passed.
- ARM compiler used warnings-as-errors; the pinned toolchain archive verified.
- The packaged ARM executable identifies as0.2.0 under local emulation.
- During the earlier read-only probe, no executable was uploaded or run there,
  and no files, firmware, settings or motor commands were changed. Deployment
  subsequently occurred through the TF-card procedure. See [probe findings](../PROBE-2026-11-14.md).

Card boot acceptance and actual diagnostics have since passed, including the
restarted PC gateway. The runtime is HOME_STATION with startup latch cleared.
Media quality and physical adapters remain unverified; a preview-hook test did
not establish a reachable home RTSP listener. See [current handoff](../STATUS.md).
