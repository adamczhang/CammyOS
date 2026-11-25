# CammyOS 0.4.1 — stock-shell compatibility and detached profile guard

TF-updated, read-back verified and normal boot/microphone accepted on2026-11-15.
This corrects the experimental
[0.4.0 Wi-Fi PCM release](archive/RELEASE-0.4.0.md); its media API and format are unchanged.
The0.4.1 archive now boots on the camera without a RAM helper patch.

## Actual0.4.0 boot and bounded RAM correction

Adam inserted the updated TF card and booted normally. Authenticated home-Wi-Fi
identity reports0.4.0, HOME_STATION, no lease owner and no preview. USB references
confirm the original native app, watchdog, kernel and startup-file hashes.
The shared profile safely declined activation because both stock shell utilities
and the bundled BusyBox lack `id` and `rmdir`. The audio-disabled portal remained
reachable. No config mounts were installed; both stock ALSA hashes matched.
A failed `rmdir` left an empty restoration-lock directory in RAM.

The helper now checks root credentials using `/proc/self/status` and removes
its exclusively owned restoration-lock directory with the available `rm`.
Local fixtures deliberately make `id` and `rmdir` fail, and still cover profile
activation, refusal, restoration, PID reuse and concurrent-restorer exclusion.

To continue the bounded hardware investigation without a card swap, only the
RAM helper was corrected after backing up and hashing its original. The idle
CammyOS portal was stopped and its exit verified, the empty owned RAM lock was
removed, the profile activated, and the exact already-loaded0.4.0 portal binary
restarted with `--shared-audio`. No native app restart, new camera binary,
internal firmware/init change, or TF-file update occurred. The portal now reports
configured/profile-ready with no capture child. This is profile activation,
not proof of live Wi-Fi PCM or microphone intelligibility.

The background watcher launched through the interactive ADB shell did not
survive that session. The existing verified detached launcher then started the
watcher successfully; `guard.ready` binds the active portal's PID/start time,
and its own process identity was checked. Consequently0.4.1 copies the already
packaged `daemon-exec` to RAM and uses it explicitly for guard detachment, rather
than relying on a shell background job. Local lifecycle tests execute the real
host build of that launcher with confined fake config/mount fixtures.

## Current limits and next test

During the RAM proof the card still contained uncorrected0.4.0, so reboot lost
that correction. The reader update below now installs0.4.1; normal boot, profile and microphone validation are accepted below. The subsequent
[RAM-corrected0.4.0 media and speech trial](WIFI-AV-2026-11-15.md) passed.
The stock video listener still requires manual initialization through Cammy's
Wi-Fi screen; the separately documented home-IP recovery may then be needed.
After that, test both media streams over home Wi-Fi, lease/Stop cleanup and
intelligibility, and restore the temporary runtime setup when the trial ends.

Local checks:40 focused portal/SDK/gateway/profile/package/startup tests pass
in18.315s. The ARM binary builds. These are local/emulator results, not physical
media or cleanup evidence. Exact boot, helper hashes, guard and next action:
`private/wifi-audio-build/checkpoint.json` and `ram-profile-compat.json`.

The final idle check confirms no capture child and the physical capture device
closed; the detached guardian remains running. Boot/native/file hashes/watchdog
fdinfo and file-backed mapping identities match. Full maps are not byte-identical:
the heap grew from end0x1719000 to0x182f000 after native service checks, and vendor
thread-access annotations changed. This is not classified as a leak, and cleanup
or sustained memory stability has not been demonstrated. ARM emulator checks:
16 portal and9 preview tests passed. The listener was absent at that initial checkpoint; Adam subsequently
initialized it through the native Wi-Fi screen, as the later report records.

## Corrected TF update — complete

The reconnected Genesys USB reader exposed the expected camera-formatted vfat
C87D-17EA card. Its0.4.0 manifest and experimental opt-in configuration matched.
The new16-member0.4.1 archive matched the current helper/startup sources and ARM
build, SHA-256 `ae58bfcbf0c843d5dc46541a28b4fefb97d72785c43b95cf772652a4604e988e`.

Before writing,0.4.0 was backed up on LAB. It is also retained on-card as
`CammyOS-rollback-0.4.0`; the previously accepted0.3.0 rollback remains available.
All16 new payload files plus the explicit experimental opt-in file were copied
and verified. DCIM's25 files, `.data`, and all older rollback file hashes and
directory inventories were preserved. No raw block/boot metadata was written.

After unmounting, the card was remounted read-only. New runtime, opt-in, both0.4.0
backup copies and every preserved tree matched their recorded inventories. The
card was unmounted again and the reader powered off. Exact private audit:
`private/tf-updates/20261115T173343Z/update.json`.

Boot procedure (now completed): with Cammy off, insert TF and boot with PC USB connected; select Charging
Mode and leave Wi-Fi controls untouched for about90–120s. Verify actual0.4.1
identity, automatic shared-profile activation, detached guard readiness, normal
camera view and idle capture closed without another RAM compatibility patch.
Native RTSP UI initialization/home-IP recovery still require the separate setup
already documented; this update does not automate them.

## Actual normal boot and screen-free microphone — accepted

Adam booted with TF and PC USB, selected Charging Mode, and reported normal live
view without opening the Wi-Fi screen. After the ordinary startup delay the
body and RAM release both report0.4.1, HOME_STATION, configured/profile-ready,
owner null and preview inactive. The startup latch cleared after60s of portal
survival. RAM portal, profile helper and detached launcher hashes match the
accepted archive/build. The guard's ready record matches the live portal
PID/start time; its own process remains running. Stock app/kernel/init/watchdog
identities match the saved reference. Native capture is CLOSED while idle.
No corrective RAM script, new USB-deployed binary or internal init change was
needed. Private evidence: `boot-accepted.json` in the update audit folder.

A bounded microphone-only test then acquired an authenticated lease and opened
the ordinary9999/4016 event session. Native RTSP8554 was still absent, but PCM
streaming through the resident desktop gateway worked. A4s/128,000byte sample
detected all three P15 tones (700/1100/1700Hz) in order, with spectral fractions
0.995/0.996/0.989; first3200-byte read arrived0.388s after the request. The P15
playback route was verified. It was a PC speaker cue, not Cammy speaker output.
No USB media, TF recording or Wi-Fi-screen action was used. Thus microphone
transport can start directly after boot through the agent API, independently
of native video-server initialization.

The first short harness asserted native capture CLOSED immediately after release
and failed; that was an invalid synchronization assumption. The corrected
bounded poll observed RUNNING at0.035s and0.169s, then CLOSED at0.296s after
release in the acoustic trial. Native PCM owner task1010 (`initSocketServe`)
belongs to sdvcam TGID778. The direct CammyOS capture child was reaped, owner
null and preview inactive. This is an observed driver-state transition, not a
physical mute/acoustic-cutoff guarantee. Private evidence:
`audio-acoustic-accepted.json`, `audio-only-cleanup-investigation.json`, and
`post-audio-reference.json` in the same audit folder.

The remaining [video boot-initialization work](VIDEO-BOOTSTRAP.md) is separate:
CammyOS's home-station network already works, while the stock application's
remote-video state still needs initialization. User requested bypassing the
Wi-Fi screen completely and starting the service bundle from CammyOS boot.
