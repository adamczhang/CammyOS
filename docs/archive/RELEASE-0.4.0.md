# CammyOS 0.4.0 — experimental Wi-Fi microphone transport

**Boot update:**0.4.0 booted on home Wi-Fi, but its optional audio setup safely
fell back off because two shell utilities are absent. A RAM-only correction
activates the profile; [0.4.1 records the actual result and prepared fix](../RELEASE-0.4.1.md).
The later [RAM-corrected Wi-Fi transport and speech trial](../WIFI-AV-2026-11-15.md)
passed; the subsequent0.4.1 card update is verified, with its normal boot pending.

This TF userspace release adds an authenticated live PCM endpoint alongside the
existing stock RTSP video route. Local tests and the ARM build are complete;
actual Wi-Fi PCM and combined sustained acceptance are pending. The previous
[combined proof](../LIVE-AV-2026-11-15.md) used USB for microphone transport.
It does not prove that this release works on hardware.

The kernel, bootloader, native `sdvcam`, and internal startup files are not replaced.
The optional profile changes only RAM-backed views of two ALSA configuration
paths. Original file contents are pinned by SHA-256, copied to RAM, and restored
by unmounting those views. A byte-identical fresh copy of the global ALSA config
forces the stock application's cached configuration to refresh. Playback keeps
its hardware route; only card0/device0/default-or-zero-subdevice capture is shared.
Other capture card/device names fail closed in this experimental profile.

## Enablement and lifecycle

The generic archive defaults to audio disabled. For this camera test only, a
separate `CammyOS/enable-shared-capture` file containing exactly
`experimental-shared-capture-v1` opts in at the next normal TF runtime start.
The profile refuses an active capture device, mismatched stock configuration,
pre-existing shared IPC, existing config mounts, or a live earlier portal.
It has no warm-update or native-app restart path. Remove the opt-in file before
booting to return to the default audio-disabled runtime.

On success startup adds `--shared-audio` to the portal and writes a private RAM
marker. A PID/start-time-bound guardian restores the config views after the
portal exits. Partial activation attempts restore what they mounted; failed
restoration retains a `recovery-required` record. Restoration is serialized
between the supervisor, guardian and explicit stop. If activation fails, the
portal still starts with audio disabled so diagnostics/control remain reachable. This is configuration-path
restoration, not proof that an already opened physical PCM has stopped.

## API and desktop integration

- `GET /v1/audio/stream/status`: authenticated, read-only lifecycle and byte count.
- `POST /v1/audio/stream` with `{}`: authenticated current lease owner only,
  connected stock preview required. Mono 16kHz signed 16-bit little-endian raw PCM
  in a close-delimited HTTP response, with format/rate/channel headers.
- One direct, fixed-command `arecord -D cammy_shared` child; no shell, arbitrary
  device, path, format, or recording destination supplied by the client.
- No success headers until actual PCM arrives. Capture startup is limited to2s;
  stream lifetime is limited to600s and the existing control lease.
- Close the response, release the lease, stop preview, or issue Stop to end the
  stream. The parent closes the stream and terminates/reaps the capture child,
  escalating after250ms. Parent death kills that child. Slow readers are dropped
  rather than accumulating audio; only8192 bytes of output buffering are held.
- `Cammy.open_audio_stream()` returns a context-managed HTTP response. The caller
  renews the lease separately. The desktop gateway proxies PCM without buffering
  the whole response; its control requests use separate connections.

```python
# Use the existing securely configured SDK client, after normal preview startup.
body.acquire(30)
try:
    with body.open_audio_stream() as pcm:
        block = pcm.read(3200)  # 100ms mono16k PCM; keep renewing for longer sessions
finally:
    body.release()
```

A read can split a sample; consumers must retain an odd trailing byte across
reads. The SDK does not automatically persist audio, run recognition, renew a
lease, or start sensing when a desktop gateway connects.

## Hardware test and rollback

1. Back up and verify the existing TF runtime on LAB; preserve DCIM, `.data`,
   previous rollback folders, and all raw boot metadata.
2. Stage the verified0.4.0 runtime folder, retain0.3.0 as rollback, and add only
   the explicit opt-in test configuration. Verify every payload file after copy.
3. With the camera off, insert the safely ejected card; boot normally on USB
   power. Choose Charging Mode if a PC data cable is connected. Leave Wi-Fi
   controls alone until portal/profile startup has been checked.
4. Check0.4.0 identity, profile readiness, stock app/boot/watchdog identities,
   and normal live view. Native RTSP initialization is still manual: stock Wi-Fi
   UI activation may change the interface IP while it remains associated with
   home Wi-Fi. The previously proven DHCP recovery is a separate setup step;
   it is not automated by this release. Do not assume RTSP readiness from a flag.
5. Acquire an explicit lease and preview, stream video and PCM over home Wi-Fi,
   measure fresh video, PCM arrival, intelligible speech, responsiveness and
   byte rates. Stop and verify child exit; inspect native capture separately.
6. On failure stop the owned session. Power off before moving the TF card.
   Remove the opt-in flag for normal audio-disabled boot, or replace `CammyOS/`
   with the retained0.3.0 folder. No internal firmware rollback is involved.

Native preview itself may acquire the microphone even when agent PCM forwarding
is off. **Idle forwarding off does not mean physical microphone mute while video
preview remains active.** True PTT record suppression, physical idle policy,
low-latency recognition, unattended RTSP startup, arbitrary streamed speaker
output, face rendering and full duplex remain separate unfinished milestones.
The20s USB proof does not establish sustained stability, end-to-end latency, or
an enforced15Mb/s ceiling. PCM payload here is0.256Mb/s; network overhead is extra.

## Card preparation result —2026-11-15

The C87D-17EA camera-formatted card now contains the verified16-file runtime and
one explicit experimental opt-in file.0.3.0 is retained both on LAB and as
`CammyOS-rollback-0.3.0` on-card. All25 existing media files, `.data` and older
rollback trees match their pre-update SHA-256 inventories. No raw device or boot
metadata was written. The reader is unmounted and powered off.
Private audit: `private/tf-updates/20261115T161209Z/update.json`.

Local validation:555 tests passed in139.917s; final targeted protocol25,
shared-profile lifecycle7 and runtime-package/startup5 tests pass. The ARM
protocol and preview suites passed16 and9 tests respectively; those emulator
results are neither camera execution nor physical cleanup evidence.
The audio-disabled API test passed on both host and ARM emulator. Hardware boot,
Wi-Fi PCM, sustained A/V and recognition acceptance await the physical card swap.
