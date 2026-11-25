# Cammy status

This file tracks the running camera, CCC and installation evidence.
[PLAN.md](PLAN.md) is the forward plan. Older checkpoints and evidence indices
are in [archive/](archive/) and [README.md](README.md). Git checkpoint
`e2f9d45` preserves the longer November 17 handoff.

## Camera and release

### 0.9.4 face candidate

The November 23 local candidate ships compacted CPK1 packs and a compressed
Loading alpha plane. The complete face library is 2,962,384 bytes, down
495,616 bytes (14.3%) from 0.9.3. All 16 retained adult frames and 56
reachable child selections match 0.9.3 pixel for pixel. The child bank
stores 55 distinct frames. The ARM GUI fixture confirmed the chooser can
switch to the other child pack after a decode failure and detach when both
child chooser images fail. Physical display, camera timing, and soak
acceptance are pending. See the [0.9.4 candidate record](../private/rc06/candidate-0.9.4-face/candidate.json)
and the [acceptance gates](PLAN.md#face-library-upgrade).

The candidate is verified on the camera's TF card. The checked install
archived and pruned one obsolete internal-flash selector archive, then
stopped before changing the live boot files. A follow-up maintenance prune
found no further eligible archives. The 512 KiB writable NOR partition
reported 140 KiB free through the installer probe, against a 172 KiB
transaction requirement including the 32 KiB reserve. The live and retained
rollback copies of `S01app` are about 54 KiB and 52 KiB respectively. The
stock backup, active 0.9.3 receipt and unfinished 0.9.4 journal remain
protected. The live boot files still match the 0.9.3 receipt.
[Installer and cleanup rules](../tools/rc_install.py)

Adam designated factory stock as the only on-camera recovery target.
Maintenance verified the factory `S01app` against its pinned hash,
archived the 0.9.2 boot backup to LAB, then removed that backup from NOR.
The read-only preflight passed with 196 KiB free. The stock-only installer
then verified the factory backup and all 26 TF slot files, archived the
0.9.3 resident authority, and armed 0.9.4 for a single manual boot. The
post-install probe reported 228 KiB free and exactly one NOR recovery
directory, containing the pinned factory `S01app`. Historical CammyOS
boot files and authority archives are on LAB. The physical boot and
display checks are recorded below.
[0.9.4 install receipt](../private/rc06/install-0.9.4/installed.json)

The November 23 USB one-shot boot `271d186d` ran 0.9.4. The release
health record confirmed HOME_STATION, and the startup latch cleared after
the 120-second health window. Native PID 1097 and the Settings process
were present. Physical display acceptance is pending. Stock logged
USBModeWindow between two PreviewWindow opens. The first visible CammyOS
chooser frame remains unmeasured.
[Timing and Settings follow-up](PLAN.md#settings-assets-and-boot-chooser-timing)

| Item | Last verified state |
|---|---|
| Running camera | Current boot unqueried on November 22. November 21 field records report two native PID losses. |
| Camera install | A local 0.9.1 install receipt records transaction `20261122-230956`, with 0.8.3 as its NOR rollback. The running boot has not been checked here. |
| Latest validated local build | 0.9.0, release `376c373acf37c8d21abfa280`, 147 manifest members. Inert and awaiting hardware acceptance. |
| CCC | Running service left in place. The host voice recovery and health changes are in the checkout and await live acceptance after an approved restart. |

On November 22, offline voice tests exercised silent and stalled Gemini
replies, mic gate reset on mute and disconnect, queued turns across lease
loss, refused and stuck speaker jobs, and the separate silent check. The
check reads speaker state and discards Gemini audio. Guardian reason reporting,
actual playback and recovery on the running camera remain to be verified.
[Voice recovery and health](C3.md#voice-recovery-and-health)

The 0.9.0 candidate is in [candidate-0.9.0-resilience](../private/rc06/candidate-0.9.0-resilience/candidate.json).
The manifest SHA256 is
`e571ceef850131abffdaf8bb41a7c1f01cd0ffd76130b53565117172ee8509c9`.
The face DSO shrank by 82.8% and the staged payload by 61.7%. ARM fixtures
cover screen and work-mode suspension, redraw after quiescence, and
the loader ABI. Selector and installer fixtures exercise interrupted
recovery writes. The device crash cause, physical GUI timing, VFAT/JFFS2
power-loss behavior and speaker audibility remain unverified.
[Evidence](RESILIENCE-REVIEW-2026-11-22.md) · [Hardware acceptance](PLAN.md#09-resilience-acceptance)

Offline verification ran 1,382 tests with 1,373 passing, seven skipped and
the two known errors. The bootstrap readiness test still rejects its old
pinned fixture, and the MCP test still needs the `mcp` dependency. The
RC06 target passed 252 tests and the native-face target passed 22.
The strengthened selector cut fixture and synchronous GUI deletion fixture
also passed on their focused reruns.

### Camera-side 0.9.3 source checkpoint

The November 23 field record shows a whole-system reset on battery after a
65-minute USB run, then a reset during media preparation on boot
`36d1364a`. The reset source remains unresolved. The next boot selected
stock with the media latch still present. The source changes below await
the lead's build and silent hardware acceptance.

- `CammyOS-trail` is a 32 KiB TF ring with 64 checksummed 512-byte slots. A
  single C helper samples about every eight seconds and on observed phase
  changes. A subsequent CammyOS boot publishes the last valid unclean
  sample as `CammyOS-faults/previous-boot.txt`. A stock fallback leaves the
  ring available for extraction. The trail omits battery polling, which
  CCC performs through the existing stock endpoint.
- A release-bound `idle_ready` or `media_prepare` latch can spend the
  existing one-boot crash budget after a matching resident commit in NOR
  history. The synced ledger names the failed and designated retry boots.
  Health retains the latch through the claim commit and replenishes the
  budget after 900 seconds of consecutive passing samples on the retry boot.
- The committed supervisor normally does about 0.75 fork/execs per second
  on home Wi-Fi, including shared capture. The guardian checks ownership
  every second in C without a fork. The rate follows the fixture-tested
  schedule. Native process loss
  still triggers full health on the two-second supervisor pass. Uncommitted
  boots, active media work and fault recovery use full health checks.
- The face library shrank from 6,776,288 to 3,458,000 bytes. The 591,548-byte
  CPK1 child bank replaces 3,907,657 bytes of raw BGRA LZ blocks. All 64
  frames match the original bytes in the reference and C decoder tests.
  ARM portal and UI BSS fell from 2,446,648 to 233,576 bytes and from
  629,936 to 60,336 bytes, respectively. Packaged runtime ELFs are stripped.
- Idle leased portal polling falls from 40 to about one wake per second,
  or four while renewing a face request. The GUI observes WindowManager
  transitions on both sides of the stock timer, suppresses drawing on a
  failed stock-mode read, and samples face tracking every 250 ms. Replacing
  a UI page unmaps the old page and replaces the GUI-side page reference.
- Startup now needs two persistent guard publications before idle readiness,
  down from nine. Intermediate display progress stays in RAM. Explicit
  media preparation drops one redundant candidate SHA-256 pass and keeps the
  independent stock SHA and the candidate check immediately before native use.

AP-to-station retry and speaker mixer changes require separate bench checks.
Silent acceptance and evidence capture are in [PLAN.md](PLAN.md#093-silent-camera-acceptance).

### NOR overlay maintenance

On November 22, 0.8.3 hit ENOSPC and the 0.9.1 preflight measured
122,880 bytes available against 163,840 required. After manual archival, the
TF statfs probe reported 172,032 bytes free. The factory S01app (4,246 bytes)
and the rollback to 0.8.3 were retained. [0.9.1 install receipt](../private/rc06/install-0.9.1/installed.json)

The installer now copies obsolete rollback directories, selector archive
directories and standalone archived records to private LAB storage. It
verifies each camera MD5 and publishes a synced receipt before deletion,
then measures headroom again before changing the boot files. The factory
identity, installed receipt's rollback and unfinished journals determine
what stays. `python3 tools/rc_install.py prune` previews maintenance. Pass
`--replacing <current installed.json> --apply` to archive and prune without
installing. New selector and health code reserve space for their own history
writes and retain at most four managed entries. USB lifecycle inspection
includes the prune plan and the installed receipt's NOR rollback presence.
After a successful install it prunes again against its own new receipt, so the
camera keeps only the factory selector and the rollback to the release just
replaced, plus that install's own selector archive, which rollback restores
from (2026-11-23; before this, the 0.8.3 rollback outlived the 0.9.2 install).
Verified on hardware 2026-11-23: prune kept the factory and 0.9.1 rollbacks and
left 180,224 bytes free. [Hardware follow-up](PLAN.md#nor-overlay-verification)

### Earlier hardware evidence

Transaction `20261117-221707` verified all 25 microSD release files, the
internal boot files and the 0.7.11 rollback. The first attempt ran out of
room staging `S01app` on the 512 KiB internal settings overlay. Verified
archival to LAB removed 104,924 bytes of older rollback files and claims
from the overlay, including two identical copies of the large boot script.
The resumed transaction finished and armed the trial. A separate cleanup
removed 145,654,826 bytes of superseded release files from the microSD. The
card retains 0.7.13 and 0.7.11. The factory boot copy and the current
rollback remain on Cammy. Quiet start and both Wi-Fi profiles were preserved.
[Install receipt](../private/rc06/install-0.7.13/installed.json) ·
[Cleanup inventory](../private/rc06/install-0.7.13/off-camera-cleanup/inventory.json)

During the manual 0.7.13 boot, Adam saw the Loading screen at stage 3
(Cammy's services). CCC had reconnected and native media was ready. The
stability window began at 91 seconds of uptime and the same-boot health
acknowledgement and cleared guard were verified at 213 seconds. The
screen kept the same label during the check. The UI then logged Ready
and Adam confirmed that Loading cleared.

0.8.0 adds a startup progress bar alongside the check marks and lists the
Cammy pack in Sound & Face. Select that pack in CCC's Family tab. The face
appears after six seconds of passing health checks. Release acceptance
continues in the background. The native UI, face, package and boot suites
passed 130 tests, including ARM rendering and faults that restart the
short check. [0.8.0 manifest](../private/rc06/candidate-0.8.0-loading-progress/candidate.json)

Transaction `20261117-224039` verified four internal boot targets and all
25 microSD files for 0.8.0. The preceding stock fallback boot recorded
`native-exe` because the 0.7.13 trial claim was already used. With Adam's
approval, the fault and guard were archived before arming 0.8.0. The
internal settings overlay had 184,320 bytes available before this update.
[0.8.0 install receipt](../private/rc06/install-0.8.0/installed.json)

The 0.8.0 trial reached Ready within about 12 seconds of the services
stage. Loading had cleared when CCC requested media after reconnecting.
The camera passed release health after that job and cleared the guard.
The trial initially used one-shot selection. The following power-on boot
`3bd16c3f` selected stock with `post-attempt-boot`, then reported
`native-exe` because the running application was stock.
Adam approved resident mode for 0.8.0. The internal boot files and all
25 TF members matched the install receipt. The stock fallback records
were preserved before enabling the resident record and restoring select.
The next boot `aa270058` selected CammyOS, reported Ready and reconnected
CCC. Its release health commit on home Wi-Fi verified that it rearmed
the next boot. [Resident activation evidence](../private/rc06/resident-20261117-230054/armed.json)

Adam confirmed the Cammy row was visible in the 0.8.0 menu. In 0.8.1 the
camera menu can select and save all three packs using touch or buttons.
CCC still supports temporary selections, and a physical pick supersedes
them. All 94 focused tests passed, including ARM menu and renderer checks.
[0.8.1 manifest](../private/rc06/candidate-0.8.1-face-packs/candidate.json)

Transaction `20261117-231311` verified the 0.8.1 card slot and the 0.8.0
rollback. Internal flash ran out of space staging `S01app`. Moving the
47,005-byte superseded 0.7.11 rollback copy to LAB let the same transaction
finish. The resident record now names 0.8.1. USB reads timed out
intermittently. Restarting the dedicated maintenance ADB server restored
the shell on the same boot.
[0.8.1 install receipt](../private/rc06/install-0.8.1/installed.json) ·
[Resident authority](../private/rc06/install-0.8.1/resident-armed.json)

Boot `eaf1f071` saved the physical Cammy selection in `/etc/cammy/face-pack`.
CCC reported the Cammy face and switched to Adult. Resident health
acknowledged this boot on home Wi-Fi and cleared the guard.

The physical M button still needs a hardware check.
[Update and recovery procedure](CAMMY-BRAIN-FOUNDATION.md#checked-update-and-recovery)

The 0.7.13 candidate makes the physical M button toggle the CCC microphone
and Look Back capture in CammyOS. The portal closes the microphone stream
and CCC pauses the decoder. Existing profile history stays available.
Settings opens by touch or OK. The 83-test native and package suite and
the 376-test CCC run (23 skips) passed. The M button action still needs
an on-camera trial. [0.7.13 manifest](../private/rc06/candidate-0.7.13-capture-mute/candidate.json)

The 0.7.12 candidate adds the on-device Loading screen and the desktop
maintenance tools. The display waits for health acknowledgement on the
same boot and release, a cleared guard and a live network and portal.
Requested media preparation must settle before the face appears. The
face library and decode buffers fit in 39.0 MiB. The 160-test firmware
suite and package validator passed. [0.7.12 manifest](../private/rc06/candidate-0.7.12-loading/candidate.json)

The 0.7.11 native runtime includes the recovery work from 0.7.10, the faster
chooser, the third face pack and the Settings and preview controls. The
face library and decode buffers fit in 38.86 MiB. The candidate passed
offline validation and 69 focused native and installer tests, including an
ARM chooser fixture that waits for valid stock screen and top-layer pointers.
The C3 suite ran 374 tests with 23 skips, followed by 86 focused Family and
Calendar tests and a GTK smoke check. All 15 Cammy MCP tests passed in
the agent-control environment. [Candidate manifest](../private/rc06/candidate-0.7.11-final/candidate.json)

Install transaction `20261117-200346` verified the 25-file TF slot, internal
boot targets and rollback copies. It replaced the armed 0.7.10 trial before
that build booted. No fault guard was cleared and no resident authority
was added. The receipt is `private/rc06/install-0.7.11/installed.json`.
ADB reboot powers this unit off. Adam must turn it back on physically.
[Resource audit](DEVICE-RESOURCE-AUDIT-2026-11-17.md)

On boot `4000fb74`, native media preparation completed, CCC reconnected at
connection epoch 3, and the native renderer reported CammyOS mode with Fin and
Fur and all three packs available. The health acknowledgement matched the
installed release and boot, and the startup guard was clear at 210 seconds of
uptime. Physical Settings and side circle/play presses changed the camera
UI's menu and live-preview states on press and reversed them on the second
press. Camera trace and cropped evidence are under
`private/rc06/install-0.7.11/lcd/`.

The P15 captured the adult Cammy pack awake and its Zzz sleep pose during a
CCC lease release. CCC switched to Adult automatically on Cammy, held Kids
with the Cammy face on the LCD under the override, then returned to Adult
on reset. Calendar stayed locked. The reconnect restored Fin and Fur in
Kids mode. Transaction `20261117-201851` armed the same release for one
more boot and archived the healthy boot's used claim.

The recorded power cycle at 20:23 CST showed the stock splash followed by
Language setup, with English highlighted. Adam reported that the startup
chime returned. After he confirmed English and opened the camera's Wi-Fi
screen, the stock access point allowed read-only ADB file access. The
boot script and ADB wrapper matched the factory files byte for byte.
`/etc/cammy` was absent, including the two saved Wi-Fi profiles, and
the internal CammyOS startup hook and USB debug hook were absent. The
writable overlay was mounted. On the TF card, 24 of the 25 release files
matched the installed package. The face library's size matched and its full
read timed out. The cause of the internal loss is unknown. This boot did not
provide a CammyOS chooser timing measurement. Video evidence is in
`private/rc06/install-0.7.11/lcd/cold-boot-202252.mp4`.

Recovery restored USB maintenance without a TCP ADB listener and verified
all 25 release files directly on the camera. Transaction
`20261117-205947` restored the boot hooks and selector, verified rollback
copies, and armed one boot of the same release. Both Wi-Fi profiles, the
fallback AP and the portal credential were provisioned separately over USB.
The repair receipt is `private/rc06/repair-0.7.11-d7877c50/installed.json`.

The first boot after the repair selected CammyOS and cleared the startup
guard after passing health on the fallback AP. The restored Wi-Fi config
contained `update_config=0`, which is unsupported by this build of
`wpa_supplicant`. It was removed and the target parser accepted the
corrected file with both profiles intact. Transaction `20261117-210956`
armed boot `e22d6b2a`, which joined home Wi-Fi. Native media still refused
preflight because the factory reset had restored the stock network name.
The `CammyOS` name in the native config was restored and read back after
the retry limit had been reached.

Adam heard the stock startup chime on both boots. A live edit to
`ml_startup_volume` was overwritten on shutdown by the camera app's cached
value. The early `S00cammy-power` hook now applies the existing Startup
Music setting before the stock app and audio script start when
`quiet-start.enabled` exists. The change passed host fixtures and a smoke
test on the device shell before it was installed. Transaction
`20261117-211816` armed verification boot `4fe03440`. Adam confirmed it
started without a chime while disconnected from USB. Readback showed Startup
Music at 0, the native name at `CammyOS` and the camera on home Wi-Fi in
CammyOS mode. Native media reached ready and CCC reconnected with its preview
lease. Health acknowledged the same release and boot on home Wi-Fi, and the
startup guard was absent at 278 seconds of uptime. No resident or power-retry
authority was added.

## CCC

- The November 23 host changes are in source and covered by offline tests.
  Gemini retries authentication and missing-model errors after 10 minutes,
  distinguishes ignored speech from missing replies, and pauses the reply
  watchdog during tools. CCC reports camera battery power, waits for a
  stable stock mode after reboot or unusual mode, and reduces idle state
  polling. The half-duplex latency estimates and status fields are in the
  [C3 guide](C3.md#pc-side-latency). Live deployment awaits the approved
  CCC restart.

- The desktop now keeps its management tools in either camera mode using a
  separate local credential. Kids still pauses new camera history and
  restricts the ordinary API. Verified the live routes and both provider
  connection tests. Rendered all nine tabs and both sidebar pages in an
  isolated GTK session at three widths, down to 320 pixels of content,
  and fixed the clipped controls. The Calendar broker is live, with no
  Executor connection authorized for the selected profile.

- Resident Codex and Claude profiles both passed native sign-in and session
  resumption trials. CCC's Providers tab shows connection and recovery status.
  [Brain guide](BRAIN.md)
- Look Back keeps one local frame per second for the selected profile.
  The host changes add a 14,400-frame cap alongside the 250 MB budget.
  Earlier live checks verified a 640×360 sample and recovery across CCC and
  camera restarts. [Look Back](C3.md#look-back)
- The desktop has Agent and Skills editors. Default Cammy MCP calls the CCC
  API. The finite-session controls remain available with the explicit legacy
  option. [C3 guide](C3.md)
- The OS tab and lifecycle CLI are implemented in source. Read-only USB
  inspection and repair preflight correlated the live 0.7.11 boot,
  installation receipt and all 25 TF files. Stock setup and device writes
  were tested with fixtures. The running desktop services have not been
  redeployed. [Maintenance and Brain guide](CAMMY-BRAIN-FOUNDATION.md)
- Family and Calendar are implemented in source. CCC uses the selected face
  and a saved Kids override to determine its mode. The Calendar PIN opens a
  separate grant for the active Brain profile. The host passed a synthetic
  Kokoro/Whisper round trip and local desktop checks. The Calendar tool schema
  was checked with Executor metadata. Live scoped sign-in and a real Calendar
  read are pending. [Family and Calendar](C3.md#family)

## Hardware evidence

- In one half-duplex acoustic trial on 0.7.6, Cammy heard the complete
  question and began her correct answer 3.01 s after the prompt ended, measured
  at her own microphone. The detected short pauses matched the source audio.
  This was one case. The P15 input produced zero samples during the independent
  microphone check. Private evidence is in
  `private/c3/full-loop-capture/run-20261117T171830-713028/case-1/`.
- The P15 LCD recording showed speaking animations with blank speech bubbles
  on Pikachu and Fin and Fur. The adult Cammy speaking animation still awaits
  an LCD check.
- One quiet 60-second interval on the separate Gemini Live path processed
  local microphone frames and recorded no additional Gemini audio, turns,
  connections or usage report. This checked runtime counters.
- The two Wi-Fi profiles were restored after the overlay loss. ALPHABET
  is the lower-priority phone profile with the Tailscale marker. Hotspot
  association and tailnet reachability await testing.
  [Network and storage](HOME-WIFI.md)
- A previous fault selected stock. Archived recovery records are in
  `private/rc06/stock-recovery-78101442/`. A later 600-second microphone rollover
  completed without changing camera boot or CCC connection epoch.
