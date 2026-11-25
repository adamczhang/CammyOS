# CammyOS 0.5.0 — automatic native video startup

2026-11-15. **Normal boot and live Wi-Fi A/V accepted.**
[0.4.1](RELEASE-0.4.1.md) remains the verified rollback runtime.

This TF userspace release adds an explicit experimental boot initializer for the
stock embedded RTSP service. It preserves the stock app, kernel, watchdog owner
and internal startup files. It uses the phone-supported native HTTP API rather
than a patched executable, internal function calls or injected window messages.

## Startup transaction

The existing station/profile/portal startup runs first. A separate on-card file
`enable-native-video`, containing exactly `experimental-native-video-v1`, opts in.
It is deliberately absent from the generic archive and checksum manifest, like
the existing shared-capture opt-in. Remove this flag to disable the new behavior.

Before entering its usual network supervision loop, startup runs one transaction:

1. Verify the exact stock executable, one native process, owned station/DHCP
   identities, station association, home address, no AP helpers, normal video
   mode, no recording, and an available authenticated control lease.
2. Arm a detached recovery helper with boot/PID/start-time/executable identities.
3. Send exactly one stock command3003 with the existing AP prefix `CammyOS`.
   This initializes native media **and rewrites the native network settings
   file**, despite using the same prefix; it can temporarily assign192.168.10.1.
   In the warm trial all parsed top-level key/value fields, including prefix and
   credential fields, compared unchanged; serialization bytes differed.
   It is not a firmware/code install or a pure media-only operation.
4. Stop only newly started, exact-argv stock DHCP helpers; renew only the owned
   station DHCP client and verify home address, association and no live AP helper.
   The recovery helper survives loss of the initiating shell. Failure is reported;
   no native-app restart or blind request retry follows.
5. Reload normal preview through photo mode4 then video mode0, without a shutter
   or recording request. This rebinds RTSP to the recovered home address.
6. Briefly acquire the native event session, wait boundedly for video SDP, release
   the lease/session and poll for driver microphone CLOSED. This temporary event
   session can open the physical microphone; it does not forward PCM to a client.

`/tmp/cammy-runtime/native-video.state` reports the outcome, also included as
`native_video_boot_state` in authenticated diagnostics. `sdp-ready-idle` means
SDP was served and capture subsequently closed; **it does not mean fresh frames
were decoded or cold boot was accepted**. Private RAM logs/identity records are
under `/tmp/cammy-runtime/native-video/`. The directory prevents another attempt
within the boot; reboot clears it. Existing startup latch behavior remains.

A failed or uncertain photo-mode transition attempts normal-video restoration
while its lease remains owned. Failure to restore, recover networking or confirm
cleanup remains visible instead of being reported as success. If another agent
already owns the portal, boot initialization is skipped rather than taking over.

## Evidence and remaining acceptance

- Warm real-camera same-prefix request acknowledged in3.835s; independent DHCP
  renewal restored the home address without native-app restart.
- TERM did **not** stop stock `udhcpd`. A subsequent exact PID/start/executable/
  argv guarded KILL removed it; production recovery verifies absence before
  reporting restored. The original warm guard's success was incomplete cleanup.
- Following normal mode reload and an SDP readiness wait,9.012s of fresh video
  yielded39 monitor frames/38 changes alongside3s of microphone PCM
  (96,000B;47,923 nonzero samples). These are **warm** observations: a listener
  already existed before the command because the camera controls had been used.
- The actual ARM boot client passed owner acquisition, native read-only preflight
  and release on the camera after adding stock close-delimited HTTP support.
  No init command was sent by that smoke test; the RAM client was removed.
- Both proposed scripts passed Cammy's `/bin/sh -n` with exact-byte input checking.
- Local socket/transaction/recovery fixtures cover ownership, protocol failures,
  uncertain single requests, restoration, PID reuse, cleanup and one-attempt state.
  These do not prove a cold native initialization, timing or physical recovery.

[The normal boot and live A/V test passed](COLD-BOOT-AV-2026-11-15.md):
Adam confirmed normal live view with Wi-Fi untouched; startup reached
`sdp-ready-idle` by observed uptime63.70s.30.021s of changing video overlapped10s
of microphone PCM through the desktop gateway. Release left no owner/session/
capture child and native driver capture CLOSED. This is a finite first-boot
acceptance, not an endurance, latency, arbitrary speaker or duplex result.

If the new startup fails, disable `enable-native-video` through the reader and
boot the accepted path; retain the previous TF payload as rollback. This release
adds no internal startup/firmware write and requires no candidate-app activation.

## Verification and local artifact

- Broad host discovery:576 tests passed. After final prefix-whitespace hardening,
  all24 focused boot/client/release tests passed (19 boot tests +5 release tests).
- Final ARM client under Cortex-A7 qemu:8 protocol/ownership tests passed.
  ARM portal and preview suites:16 +9 tests passed.
- The final packaged ARM client also passed real-camera read-only preflight and
  owner release; its temporary RAM copy was removed and microphone CLOSED checked.
- Both scripts passed the real camera shell parser. Relative target checks passed
  for180 documentation links; `git diff --check` passed.

Commands: `python3 -m unittest discover -s tests`,
`python3 -m unittest tests.test_native_media_boot tests.test_runtime_release`,
`python3 tools/test_arm_local.py`, and `python3 scripts/package-runtime.py`.
The boot-client tests additionally accept `CAMMY_BOOT_CC` and
`CAMMY_BOOT_RUNNER` JSON argv arrays for local cross-build/qemu fixtures.
They do not run on hardware or access the actual camera token in fixture mode.

The18-file credential-free generic archive is
`dist/cammyos-tf-runtime-0.5.0.tar.gz` (695,592B), SHA256
`97760658edf1b7764f547a24912c6d4c3a00f11632e4d82041e595a658164cd4`.
Verified local staging is `dist/staged-0.5.0/CammyOS/`.

## TF update verification

The expected USB card (vfat UUID `C87D-17EA`) was updated through the PC reader.
The prior runtime verified against the0.4.1 release before writing. It was backed
up on LAB and retained on-card as `CammyOS-rollback-0.4.1`; `.data` also has a
private LAB backup. All25 media files (68,553,723B) and all82 protected files
outside the active runtime remained byte-identical.

The18-file payload plus two explicit opt-ins were staged, flushed and switched
into place. After unmounting and remounting read-only, every active runtime file,
the rollback inventory and protected files passed hash/size read-back checks.
The reader was then unmounted and powered off. Exact private receipt:
`private/video-bootstrap/tf-update-20261115T192357Z/receipt.json`.
The subsequent normal boot and media acceptance are documented above.
