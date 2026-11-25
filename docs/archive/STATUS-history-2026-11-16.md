# CammyOS handoff — 2026-11-16

## Current: resident T3 control service; quiet overnight

[Implementation, evidence and morning checklist](../T3-CONTROL-IMPLEMENTATION.md).
The PC agent service is installed on loopback8791, with nine standard MCP tools,
explicit thread-bound finite sessions, the public T3 CLI adapter/durable outbox
and a thread panel in the official Reachy Cammy app. The existing gateway is
refreshed with speaker forwarding. Codex MCP registration is installed; fresh
provider sessions discover it. Service startup captures nothing. Quiet policy
is ON and no overnight audio playback occurred.

72 integrated local tests pass. A silent real-device USB-forwarded MCP test
acquired/released a control lease and checked status/quiet refusal, preserving
stock boot/process/hashes/mixer/capture/mount state. It captured no media, moved
no motor and performed no firmware/init write or reboot. All trial RAM files
and processes were removed. Actual T3 list/read works; real message delivery and
the complete spoken/fresh-vision turn remain untested.

The staged RC2 TF card is not in Cammy. Its current stock USB boot has no wlan0
or Cammy Wi-Fi runtime. Next requires card reinsertion and the checked RC2
bootstrap/user boot, then daytime Wi-Fi speaker/Stop/overlap acceptance and three
project-aware T3 voice/vision turns. Frozen RC2 artifacts remain unchanged; the
new PC addon is newer than its source receipt. Earlier entries below are history.

[Local RC result and exact package](../RC06-LOCAL-RESULTS.md):254local tests plus27 ARM portal/client checks passed; candidate remains uninstalled.

## Current:0.6.0-rc2 speaker candidate; TF staged and safely ejected

[Staging/results](../RC2-STAGING-2026-11-15.md), [RC2 changes](../RELEASE-0.6.0-RC2.md), [physical speaker evidence](../SPEAKER-ROUTING-2026-11-15.md).
Adam heard arbitrary PCM and Kokoro “Hello world” from Cammy in USB trials.
The private shared playback profile plus bounded LINEOUT20-from0 gain handling
resolved silence. RC2 adds authenticated boot/lease/sequence-bound PCM jobs,
automatic device preflight and independent gain/switch cleanup. Local job tests
cover Stop/expiry/release/faults; Wi-Fi speaker/duplex/echo hardware acceptance
is pending. Existing RC1 artifact is preserved. No internal camera installation
or reboot was performed while preparing the TF update.

## Previous: integrated0.6.0-rc1 local candidate; hardware acceptance pending

[Contract](../RELEASE-0.6.0-CANDIDATE.md), [install/rollback review](../RC06-INSTALL-REVIEW.md),
[control inventory](../M105-CONTROL-INVENTORY.md). Reusable native mood/blink/idle/
replay, boot+lease+ordered command binding, bounded input guardian, explicit
media-preparation worker and matching desktop app/SDK are implemented locally.
Actual saved-libc/time32 DSO tests use synthetic GUI bindings; independent guardian
also passes host/ARM fake-pipe tests. Package build is reproducible, private,
versioned and disarmed, with exact source/provenance and tamper rejection checks.
No camera, card, init or running desktop service was changed in this milestone.

Adam's stock speed menu, DOWN face tracking and faster startup sweep are recorded.
Do not infer angular rates or expose an unverified tracking toggle. Desktop PTT
selects held speech within a finite live-mic session; physical PTT/VAD/follow-up
window remain open. Maple is a requested ChatGPT app preference, not activated
Cammy TTS. Voice experiments stay paused.

Generic VERSION remains0.5.0, candidate portal0.6.0-rc1. Prior physical static face
and live A/V passed148frames/448,000samples (8.873s overlap); later resets remain
unresolved. Current stock is guarded off. Next: concrete checked installation
transaction and one deliberate candidate boot, then three cold boots, one-hour
combined run and stock rollback. Earlier entries below are historical.

## Real acoustic agent turn passed; app running idle

[The finite conversation service](../LIVE-CONVERSATION-2026-11-15.md) now connects
the accepted live Cammy microphone to local Whisper, a bound agent inbox, local
Kokoro and opt-in P15 playback within the official Reachy app. The final known-cue
trial recognized both the incoming phrase and the P15 reply acoustically through
Cammy while video continued, ending at78frames/251,200 mono samples. The manager
supplied the reply through the public SDK; no resident general agent is installed.

Stop-during-recognition and7second expiry also passed: stale reply409, worker
reaping, owner null, preview off, WebRTC clean and driver CLOSED. Stock boot,
native/watchdog and hashes were preserved. No firmware/init/TF/camera reboot or
native restart occurred. The PC app was restarted through its official manager.

The new panel offers finite sessions, desktop hold/release and explicit P15
output. The agent client is `sdk/cammy_conversation_client.py`; raw audio stays
in RAM. Capture/turn limits, current boot/session binding, one-shot replies and
cleanup guards apply. Physical PTT, VAD, physical mic mute during preview,
Cammy speaker streaming and LCD output are not claimed. The previous native
face candidate remains local-only and uninstalled.

## GUI-owned face trial built; physical LCD acceptance next

The [finite native face trial](../NATIVE-FACE-TRIAL.md) now has a camera-compatible
time32 library, one immutable real ParamFace image, native canvas/delete-event
bindings and an exact-stock452-byte dual-hook candidate. It is offline-only,
mode0600 and a separate `gui-face-trial-v1` class; old status-only activation
rejects it. No native app, init, firmware or TF file was installed or changed.

22 focused tests pass with no skips:17 face tests plus5 image-ABI regressions.
The surface has49 host/sanitizer/ARM assertions and six actual-DSO/saved-libc
cases using synthetic GUI address bindings. Eleven bounded native primitive
assertions verify the canvas descriptor, top layer and deletion registration.
Complete drawing/destruction, timing and physical LCD output remain unproved.

The read-only camera check found the same boot, stock PID778/start164 and
watchdog/hash identities; microphone capture is closed. Next: review a new-class
one-shot launch route, then physically observe a short face/restoration trial
and stock return before attempting concurrent A/V or live expression updates.

## Reachy SDK/WebRTC real-media subset passed

[The media extension](REACHY-MEDIA-2026-11-15.md) now receives Cammy's real video
and microphone using upstream GStreamer WebRTC and Reachy SDK classes. The first
hardware trial yielded47 video frames and147,200 audio sample frames; the final
receive-only trial yielded23 frames and73,280. Stop cleared SDK outputs and
verified owner null, preview off, decoder/bridge gone and ALSA capture CLOSED.
Native app/boot/watchdog/hash identities stayed unchanged. The app is running
with this profile, idle; signalling8443 is closed until an explicit finite session.

The SDK extension rejects stale/session-changed data, exposes unknown K/D as None,
labels duplicated mono honestly and refuses speaker/ReSpeaker/motion features.
Its existing-wire signalling relay rejects browser Origins and binds only loopback.
Full ReachyMini body/daemon compatibility and desktop generic-camera integration
remain future work. No new firmware/init/TF update was installed.

[Native LCD evidence](../NATIVE-FACE-ABI.md) also advanced: real read-only BGRA panel
metadata plus bounded offline native-image primitive emulation. The header is12
bytes, not the standard packed4; normal dimensions/pointer work, while the
standard layout is misread as absurd dimensions. Physical LCD drawing, full
LVGL drawing/window lifetime and a guarded native launch remain unproved.

## Real Reachy app + ParamFace + agent-visible live media

[The new app](PARAMFACE-REACHY-LAB.md) is installed and running through the official
Reachy Mini app manager at127.0.0.1:8801. Its six-preset ParamFace renderer/editor
uses the existing StackChan HTTP face contract. Face commands target the PC
preview; the LCD is unverified. The exact ARM renderer separately passed on the
real M105 CPU in RAM (240 animation steps/3.25s, host-identical final pixels).

An explicit20s app session delivered98 fresh video frames and312,000 microphone
samples, alongside face updates. Agent-visible JPEGs and a five-second rolling
PCM ring are bounded in RAM and invalidated on Stop/expiry. Automatic expiry and
an independent official app-manager Stop both released the owner and returned
the camera microphone to CLOSED; native/boot/watchdog/stock identities stayed
unchanged. The app was restarted idle; no continuous sensing is enabled.

The full Reachy SDK/WebRTC body adapter, physical LCD face, arbitrary speaker
streaming, physical red-button PTT and Wi-Fi motor guardian remain open. Twelve
static checks identify a GUI-thread mutex/periodic-handler bracket as the next
cooperative display integration candidate. No native app restart, firmware/init
write, raw framebuffer write or new TF update occurred in this work.

## 0.5.0 automatic boot and live Wi-Fi A/V accepted

[Normal boot acceptance](../COLD-BOOT-AV-2026-11-15.md) passed: live view confirmed,
Wi-Fi controls untouched, home recovery restored, `sdp-ready-idle` by observed
uptime63.70s.30.021s of video yielded145 monitor frames/144 changes alongside10s
PCM through gateway8790. Average interface traffic2.330Mb/s; no hard cap. Both
the A/V test and a later two-frame scene inspection released cleanly; driver
capture CLOSED was checked. Native app/start/hash identities stayed unchanged.

The scene inspection identified the YouTube courtyard/statue content. The
transport test itself kept counters rather than images, exposing the need for
resident agent-visible frames. A known PC cue had tonal peaks but failed its
strict acoustic threshold; no new intelligibility/speaker/duplex claim follows.

Current work: adopt the actual official Reachy Mini PC control system as the
compatibility target, map its SDK/daemon/media contracts to CammyOS and plan a
bounded real LCD face proof. Wi-Fi motor control, screen faces, arbitrary camera
speaker output and real companion live-provider integration remain open. The
prior runtime is backed up on LAB/on-card;25 media files and82 protected files
were preserved during the verified card update.


## Corrected0.4.1 TF update history — boot now accepted

The16-file release plus explicit opt-in is now on TF. Old0.4.0 is backed up on
LAB and retained on-card;25 media files, `.data` and prior rollback inventories
match. Everything passed a read-only remount/read-back check. Reader powered off.
See [release audit and next boot](../RELEASE-0.4.1.md).

Those normal boot/profile/guardian checks have now passed, as recorded above.
Native Wi-Fi-screen initialization and guarded DHCP renewal remain separate.


## Live Wi-Fi video + understood speech through the desktop gateway — passed

[Actual human trial](../WIFI-AV-2026-11-15.md):35.015s of changing video overlapped
20s of live microphone PCM via the resident desktop gateway. The first8s prefix
was recognized while video remained active, with118 frames afterward. The full
sample recovered the intended request for a live conversation. This proves
intelligible Wi-Fi audio and concurrent local recognition; the prefix was partial,
and full rolling transcription/agent responses are not yet implemented.

Both media routes use home Wi-Fi; USB was setup/metadata only, no TF recording.
The interface averaged1.303Mb/s. Explicit Stop and2s lease expiry separately
closed the streams and reaped capture children. Native microphone CLOSED was
verified afterward. Owner is null and preview inactive; no human stream is open.
Buffered PCM remains readable after Stop and must be dropped with stale results.

That correction is now on TF as recorded above; its boot acceptance is next. Follow-up:
boot/IP automation, shorter continuous recognition in an active engagement
window, and arbitrary Cammy speaker output. No sustained, end-to-end latency,
physical power-only, speaker/duplex or physical red-button PTT pass is claimed.


## Previous0.4.0 boot and RAM compatibility proof

[0.4.0 boot](../RELEASE-0.4.1.md) is stable on home Wi-Fi. Its shared profile initially
refused safely because `id` and `rmdir` are absent; the portal stayed reachable
with audio disabled and both ALSA originals unchanged. After a verified RAM-only
helper correction and restarting only the idle existing portal, profile readiness
is true. A detached watcher is verified against the portal PID/start time.
No new camera binary/internal/TF write or native app restart occurred.

That setup step is now complete: the stock Wi-Fi screen initialized8554, and
owned DHCP renewal restored the home address. The first Wi-Fi transport trial
above passed. No microphone stream is open now.
The [0.4.1 correction](../RELEASE-0.4.1.md) is now card-updated as recorded above.
Forty focused tests and the ARM build passed before that update.


## TF update preparation history — followed by boot and transport checks above

[0.4.0](RELEASE-0.4.0.md) implements raw16kHz/mono/S16_LE PCM through the
existing authenticated portal, SDK and desktop gateway. Lease expiry, release,
Stop, preview loss, disconnect, parent death and bounded capture failures have
local test coverage. An opt-in RAM ALSA profile and independent restoration
guard package the earlier shared-capture finding; stock app/kernel/init bytes
are unchanged. The generic archive contains no opt-in flag. The card now contains0.4.0 plus the explicit experimental opt-in flag.
All16 payload files and that flag were verified;0.3.0 was backed up on LAB and
retained as `CammyOS-rollback-0.3.0`. All25 media files, `.data` and older rollback
folders match their pre-update hashes. The reader is safely powered off.
Local full suite:555 passing; additional focused tests cover final profile
restoration serialization, startup fallback and audio-disabled API behavior.
ARM protocol/preview emulator suites pass.
The later RAM compatibility correction and actual transport result are recorded above.


## Concurrent live video and understood speech: first entry proof

Normal stock Wi-Fi initialization plus home DHCP recovery unlocked home-LAN
RTSP video. The latest35.079s window delivered169 monitor frames while20s of
microphone PCM arrived over USB; two recognizers recovered Adam's main sentence.
Recognition completed while video was still running. No TF recording was used.
Shared ALSA capture required temporary RAM configuration views; both originals
and the original mount table were restored, microphone closed, leases released,
and RAM staging removed. Stock boot/app/init/watchdog identities matched.
537 local tests pass in125.025s. No new camera executable or TF release installed.
Next: authenticated Wi-Fi PCM transport and a bounded shared-capture runtime
profile, then shorter recognition windows, latency and sustained acceptance.
RTSP still publishes no audio, arbitrary speaker output remains unfinished,
and native preview acquires the physical mic until preview stops. See
[actual results, preservation and remaining work](../LIVE-AV-2026-11-15.md).

## Priority correction: live media first

Adam approved [sustained live video and enabled-session microphone audio](LIVE-MEDIA-MILESTONE.md)
as the next delivery, followed by simultaneous streamed speaker output. Native
clips are diagnostics, not the intended experience. The Wi-Fi console and PTT
button reassignment are supporting work; further button coverage is deferred.
Existing network and USB media routes must be assessed before another native
framework or firmware change. No live-media success is claimed by this plan.

## Red button mapped; recorded speech understood locally

Adam resumed a passive USB button test and requested analysis of its recording.
Record is event3/code28 with down/repeat/up; the bank also includes power116.
No input grab/injection or install occurred. The retrieved6.966s native clip's
speech was consistently recognized by two existing local models. Stock
identities matched before/after. This is finite recorded speech, not streaming
or duplex. The clip is4K/33.40Mb/s, so the previously accepted720p profile must
be reconfirmed before a combined15Mb/s trial. Record-action suppression is next
for PTT; mapping alone does not repurpose it. See [evidence](../RECORD-BUTTON-AUDIO-2026-11-15.md).

## Push-to-talk policy prepared locally; no TF update required yet

Adam selected the red record button for robot-mode voice activation with a finite
follow-up conversation window. `EngagementController` now wraps the companion's
existing lease, sensing and cancellation machinery. Idle capture is disabled;
press/release, agent wake, mute, stuck holds and hard session expiry are bounded.
The finite-clip pipeline remains interim turn-taking, not streaming or duplex.
Physical record-key mapping subsequently passed; suppression of stock recording
is pending. This host-code change did not install a camera runtime. See [contract](PUSH-TO-TALK.md).
Verification:526-test full suite passes;48 focused companion/engagement tests
pass, including three boundary tests added after the full-suite run.

## Real remote gimbal movement and audible native playback

The CammyOS leased USB stock-input tool produced operator-confirmed roughly90°
left movement on a five-second hold, opened the full-screen viewer and started
an existing video with audible beeps. Two side-button presses restored live
view; Adam confirmed the head stationary. The normal evdev path works, without
camera code/firmware installation or native-app surgery. Center play/pause is
event0/code7; viewer cycle is event1/code139; turnaround is a distinct code3.
Input release was independently observed5.0065s after down. Stock identities
were preserved and leases released. Mechanical range/preset observations are
operator estimates, not calibrated feedback. This USB prototype is not Wi-Fi
actuation, TTS/live audio or an integrated mid-hold Stop. See
[complete results and limits](../STOCK-CONTROL-BASELINE-2026-11-14.md).
512 repository tests pass in123.190s. Adam requested a hardware pause after this
milestone; camera last confirmed in live view with head stationary, leases
released. Next work should consolidate normal input into usable Wi-Fi control.
The proposed [next integrated delivery](NEXT-CONTROL-JUMP.md) covers camera-local
holds, Stop/expiry, UI feedback and a console/SDK over Wi-Fi. It also calls for
an early media-concurrency measurement; native file playback is not duplex proof.

## Stock speaker and gimbal baselines now confirmed by Adam

Native media playback audibly reproduces recorded beep cues on Cammy itself.
The physical left/right arrows move its head. Zoom and180-degree-turn controls
were also identified, but remote operation is not verified. This establishes
working stock hardware paths, not new CammyOS endpoints. Root inspection shows
the native app reading ordinary Linux input devices; passive keyboard tracing
is the next step. The user confirmed power-cycling during these checks, which
accounts for the changed boot ID. No agent reboot or native/motor write.
See [stock baseline and input route](../STOCK-CONTROL-BASELINE-2026-11-14.md).

## Asynchronous audio worker, callbacks and saved-libc shared build

Local worker/mailbox integration now handles blocked open/send/close and genuine
buffer/channel/callback quiescence separately.16 scenarios pass host, ASan/UBSan,
TSan, static ARM and dynamic ARM with saved musl1.1.24. Library ABI audit passes
with19 exports/22 imports and libc-only dependency. No camera access/deployment.
Full repository suite:497 tests passed in123.061s; core18-case ASan/ARM suites
also pass, and asynchronous GCC static analysis reports no findings.
Exact native audit identifies StopChn's untimed condition-variable wait, stock
channel reuse, incompatible public SDK frame layout and PA timer globals outside
the speaker mutex. Native resolver remains NULL; speaker and duplex still do
not work through CammyOS. Next: exact frame binding and cooperative native
channel/PA ownership, then a real cue/stop trial. See [report](../ASYNC-AUDIO-2026-11-14.md).

## Finite speaker component and exact native stop blocker

Local-only speaker supervision is implemented in `native/audio`: one finite
16kHz mono PCM job, owned binding, dispatch-time expiry, cancellation and buffer
retention through uncertain cleanup. Native backend is absent. Its16 scenarios
pass host, ASan/UBSan and ARM qemu; four static-verifier tests and11 exact-stock
assertions pass. No camera access or deployment occurred during this work.
Full repository suite:475 tests passed in119.291s; new core GCC static analysis
reported no findings.
The stock player has an unbounded EOF callback wait while retaining the normal
speaker-wrapper mutex. Calling it from the bridge would not provide a bounded
stop. Next: prove native asynchronous output/stop/cleanup types and ownership,
then connect a callback mailbox and device-ABI adapter. Speaker, duplex, AEC
and native activation remain open. See [evidence and boundary](../OWNED-AUDIO-2026-11-14.md).

## Native microphone proved; USB gadget/codec routing isolated

Adam accepted720p/30 Low; native menu values5/2 are active and retained after
the operator's PCCAM/Charging-mode cycles. A known audible P15 three-tone cue
was detected in Cammy's native AAC recording, with verified P15 routes and
physical card0 capture RUNNING. Word intelligibility is not yet tested.

Two leased five-second USB tests ran capture/playback concurrently through
standard host drivers. Both USB captures were80,000 exact-zero samples; Adam
heard only P15, with no observed Cammy-speaker cue. During active USB capture,
Cammy had a UAC1Gadget card but its physical card0 microphone remained closed.
This narrows the next work to an owned gadget/codec audio bridge, not a proven
driver deficiency. Stock contains WebRTC AEC/config components; active AEC is
unverified. See [audio findings](../AUDIO-TESTS-2026-11-14.md).

Final state is Charging Mode/live view, stock hashes verified, normal video0,
recording0,owner null,preview off; no recorder left running. USB is now held by
the existing PC ADB server5037; preservation tools accept `--adb-port 5037`.
No camera firmware/code, mixer or raw-device changes. Local verification:
455tests passed in123.026s, plus44 focused audio/USB/vision tests. The15Mb/s limit is
still an architectural target rather than an installed shared traffic shaper.

## Native-photo milestone passed — real frames and finite video

Argon exhausted quota during static research; the manager recovered its saved
findings and implemented a desktop-only experimental native-photo backend,
SDK delivery, explicit photo control and finite test harness. Four real SDK
transactions passed, three through the resident service at6.050–8.519seconds
each. Explicit capture is enabled there; automatic sensing remains off. Mode0,
stock init/app hashes, boot/PID/watchdog and idle gateway were preserved.
Known native start/stop commands also produced fully decoded H.264/AAC clips:
4K/30 High at36.04Mb/s complete-file rate, and720p/30 Low at8.40Mb/s.
The temporary resolution/quality changes were read back and restored.
No firmware/code deployment, app restart, reboot or radio change was needed.
Two-way speaker/microphone operation, face rendering and continuous streaming
remain open. The user sets a combined15Mb/s media budget; it is a target, not
an installed traffic shaper. See [hardware results](../LIVE-VISION-2026-11-14.md),
[snapshot contract](../NATIVE-PHOTO-SNAPSHOTS.md) and [budget](../MEDIA-BUDGET.md).

Verification:446 repository tests passed in119.597s;38 focused snapshot/HTTP/SDK/
desktop/clip tests and29 native-bridge tests also passed. The final broad run
first exposed an existing BUSY-close race in the peer-disconnect fixture: it now
accepts only a verified closing BUSY response when send loses that race, and
still accounts for all40 connections. Native bridge code was unchanged.
The three existing HTTPError ResourceWarnings remain.
Source defaults keep snapshots disabled; private resident config enables explicit
capture, with no active controller, preview, recording or sensing loop left.

## Stock-only hardware test passed — stock restored

The approved blocked S01app replacement booted unchanged stock successfully.
One PID/start time and boot ID remained stable across 21 samples over 600.086s;
the home gateway stayed connected at every sample and Adam confirmed live LCD.
Preparation recorded 1.68–4.75s after boot (3.07s, this one boot only).

The exact original S01app is restored, SHA256
`3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136`.
A second normal cold boot has matching stock init/app hashes, no launcher RAM
directory and normal live LCD. Its 21-sample, 600.066s stability window also passed,
with the same PID/start time and boot ID and gateway connected at each sample.
Final hashes match original stock, temporary files are absent, and no controller
lease or active preview remains. No further user action is needed for this test. No native candidate, TF payload
or selector config was installed. The file transactions did not signal or restart the running app.

See the [hardware report](../BOOTSTRAP-HARDWARE-2026-11-14.md),
[test plan](../STOCK-BOOTSTRAP-TEST.md) and private execution ledger
`private/bootstrap-hardware-test/current.json` before further hardware work.
This stock-only test does not authorize a selected native-candidate boot or prove
power-loss durability/effective watchdog timeout. Native chown/rmdir/df are absent;
transaction compatibility is guarded and covered by 13 local tests.

## RAM-only status-library smoke passed

The exact existing release library loaded on the real M105 in a separate
**SYNTHETIC host** and served bounded hello/status/capabilities. All physical
capabilities stayed unavailable. Normal detach restored descriptors/thread count,
removed the socket, and the process exited zero. Owned RAM staging and lock files
were removed; stock boot/native/watchdog/hash identities and the idle home gateway
were preserved. Board load/attach/detach: 316/464/1,255 µs, one sample only.
This closes only the synthetic-host library smoke; in-sdvcam integration,
media ownership and candidate boot remain unproven. See
[report](../work-packages/RAM-BRIDGE-SMOKE.md) and private checkpoint
`private/bridge-smoke/current.json`. No persistent write or app restart. Manager
review reproduced ABI/qemu and 37 focused tests, independently checked camera
preservation/cleanup and added a core-limit-refusal-before-launch regression.
Final manager verification passed 417 repository tests in 117.236s. Three
existing HTTPError ResourceWarnings remain; there were no failures.
The next outcome-sized milestone is a real camera frame through the desktop
service, bundling native integration, recovery and media lifecycle work.

## Current state

- **TF runtime0.3.0 is boot/probe verified on the actual M105**, on home Wi-Fi at
 <camera-ip>. Identity and RAM release agree; HOME_STATION and startup_pending
 false. Vendor kernel remains4.9.191; no full firmware image was flashed. The approved
 init-file trial and restoration are documented above.
- Adam reports Charging Mode and normal live camera view. USB debug18d1:1002 is
 available. Display metadata is356×800virtual,32bpp,stride1424. Stock playback is
 S16_LE/mono/16kHz, capture closed. All five values matched read-only USB reference.
- All four named read-only probes completed through the restarted PC gateway with
 every source available and no truncation. Compatibility diagnostics also passed.
- **Network video is still unavailable.** Selected listeners were9999/8082/8787,
 all wildcard-bound;8554 was absent. A bounded owned preview check connected the
 event socket and sent4016 once, then reported rtsp-unreachable after5seconds.
 No SDP or camera frame was collected. Stop replied in0.0123seconds in this single
 live check. A separate1second lease test closed the pending preview on expiry.
 Final owner null, preview inactive/idle; no session left running.
- Earlier v0.3 native/host validation: 69 tests, including four offline candidate
 guards. Existing portal ARM emulation:15protocol plus9preview fault tests;
 the separate native-candidate fixture passes six synthetic cases.
 These do not emulate the full board or guarantee hardware timing. Gateway user
 service cammy-host.service was restarted and its new routes verified. Simulator/
 offline services were not restarted. P15 test recording/playback is stopped;
 screen idle/AC sleep remains disabled as requested, with restoration settings saved.

## Release, update and rollback

Runtime archive: `dist/cammyos-tf-runtime-0.3.0.tar.gz`,646235bytes, SHA256
`1778d0bffae3c7692ecb7e72c8315247383c021dd6858789ed79ea504019c5f1`.
Two builds matched; local stage `dist/tf-runtime-0.3.0/CammyOS/` was strictly verified.
Implementation commit:e8695d5. [Release and acceptance](RELEASE-0.3.0.md).

The camera-formatted UDISK card UUIDC87D-17EA, reader serial000000002961, had a
14-file0.2 runtime matching its accepted archive. LAB backup:
`private/tf-updates/20261114T151950Z/CammyOS-before`. On-card rollback:
`CammyOS-rollback-0.2.0`; the earlier0.1 rollback is preserved too. All14new files,
old copies and unchanged DCIM/.data trees passed after remount. Card was safely
ejected before reinsertion. No format/raw block write occurred. v0.2 archives and
older backup records remain available; the separate flash-image candidate is not installed.

Private evidence in `private/tf-updates/20261114T151950Z/`:
`update.json`, `boot-initial.json`, `boot-accepted.json`, `preview-accepted.json`,
`lease-expiry.json`, `usb-reference.json`. Exact state is
`boot_and_probes_verified_preview_unavailable`. Files stay private and out of git.

## Hardware ownership program in progress

WP1 completed static ownership/thread mapping; the manager reproduced its 42
checks. The selected status-only attach candidate is not a hardware validation.
The camera's older musl/time ABI now has an offline compatibility/loader proof.
WP2 status IPC/fake backend is locally implemented and manager-reviewed: 29
tests pass on host, ASan/UBSan and qemu-arm, with an 88-check core fixture.
WP2A's device-compatible shared library passes its ABI audit and 27 local tests,
reproduced by the manager. Its pristine-stock status candidate remains private
and undeployed; no media hooks are enabled.
WP3's owned-video supervision core passes 31 tests each on host, ASan/UBSan,
TSan and qemu-arm plus 15 static checks, reproduced by the manager. Native
serialization is still unproven, so its native backend remains unavailable.
The media transport and portal integration remain future work. WP4/WP5 pass 94
activation tests and 32 acceptance tests, reproduced after a manager fix for
mixed fixture/hardware evidence aggregation. The early bind-overlay selector is
blocked (EA): failed detach can trigger durable stock backup writes, and an
unrestorable foreign mount can still execute. Bundle-to-installable-patch output
is refused. The revised S01app launch substitution now has local fixtures and a disarmed review package;
hardware acceptance and internal installation stay open. See
[WP5 review](../work-packages/WP5-ACCEPTANCE.md). A bounded read-only
USB check confirms live S01app/source hashes and tmpfs at both /tmp and /run;
current facts do not prove early-boot TF timing, shell/options or watchdog behavior.
No selector was installed, card updated, app restarted or signal sent. No native hardware backend, camera
activation or resident release update is accepted from these local results.
See [native ABI contract](../native-bridge/NATIVE-ABI.md).

## Next hardware-test preparation complete — 2026-11-14

The [stock-only bootstrap test](../STOCK-BOOTSTRAP-TEST.md) now has a minimal four-file
kit, local verifier and reviewed install/restore transaction. Only S01app would
change persistently; native code, TF, selector config and candidate activation
are outside this test. The plan requires a modified boot and an exact-stock
restore boot, with independent process/watchdog and stability checks.

Fresh live **read-only** preflight passed: exact stock hashes, root, init ownership,
/run tmpfs, one stock app/watchdog fd3, stable sampled boot ID, positive/negative
shell parser controls and successful parse of exact proposal/transaction/rollback
bytes. The parser stream is hash-checked and held in memory; legacy ADB transport
limits were handled without camera file writes. Private kit/evidence and exact
hashes are indexed by `private/bootstrap-readiness/checkpoint.json`.

**393 repository tests passed** (121.603s), including the new readiness fixtures.
Three existing HTTPError ResourceWarnings remain. Final focused strengthening
uses nonzero synthetic source exits to detect accidental execution and checks
unknown-current-target refusal and parent-directory ownership/mode. All 13 focused
readiness tests pass after these final guards (1.250s). No internal bootstrap, RAM test files, card data,
mode, app or firmware was changed during readiness work. Preparation is complete;
Adam's approval of the exact one-file internal change and USB-only recovery
boundary remains the next action before installation (existing E2).

## Chunk 6 local review package complete — 2026-11-14

The 14-file private package contains the blocked S01app proposal, exact stock
rollback copy, status-only candidate/library, allowlist, disabled enable template,
non-consumed arm/ack templates and an unpopulated hardware checklist. The builder
and validator enforce inert files, exact identities and candidate reproduction;
there is no install/apply/enable command. Manager review corrected inherited S00
bind-overlay gate descriptions to this revised S01app route and added a regression
rejecting stale gate metadata. No gate status was promoted.

Verification: **379 repository tests passed** in 118.221s before that metadata
correction; **13 focused package tests passed** afterward in 13.287s. The same
three pre-existing HTTPError ResourceWarnings appeared in the full run. The final
private package and hash record are under `private/ownership-program/chunk6/`;
see its `manager-final-manifest.json` and the [review/rollback runbook](../NATIVE-TRIAL-REVIEW.md).
No camera, TF card or internal firmware was changed by this chunk. Chunks 3–6 are
complete locally; the next boundary is review of the exact stock-fallback bootstrap
and its recovery limits, before any separately authorized hardware work.

## Chunk 5 local review complete — 2026-11-14

The one-shot selection fixtures pass 48 focused tests after manager fixes for
ignored sync errors and an unchecked runtime S01app identity. The allowlist's
init checksum must bind the generated replacement, not the pristine input.
The independent review found no additional blocking bug under its trust model.
Manager follow-ups fix multi-claim observation, check private state permissions,
cover read-back corruption and document preparation/launch evidence separately.
Final verification passed **367 repository tests**, including 50 boot-launch/selection
tests. Three pre-existing HTTPError ResourceWarnings remain; there were no test
failures. Chunk 6 packages these reviewed inputs with activation blocked.
The helper remains blocked and ineligible for installation; early-boot timing,
watchdog semantics, persistence, core-limit enforcement and restore remain open.
See the [chunk 5 report](../work-packages/CHUNK5-ONE-SHOT-SELECTION.md).

## Chunk 4 stock fallback reviewed — 2026-11-14

The inactive exact-source generator preserves150/156 stock lines and routes all
five launch sites through the stock-only helper. Manager review added private
RAM-directory ownership/mode checks, exact TF source validation, truthful exec
attempt logging and output overwrite/symlink refusal. **27 focused tests pass**
using synthetic executables and relocated dash/model fixtures; original startup
hardware writes are only compared/parsed. Candidate selection remains blocked;
no TF probing, selector flash records, installer or camera execution is included.
See the [chunk 4 report](../work-packages/CHUNK4-STOCK-FALLBACK.md).

Before chunk 5, a bounded [read-only inventory](../CAMERA-COMPATIBILITY-2026-11-14.md)
confirmed exact live app/init/libc hashes, root access and needed shell/tool
examples. `/run` and `/tmp` are1777 tmpfs, reinforcing private-directory checks.
One native app holds the watchdog; its core limit is unlimited. Watchdog
timeout/nowayout attributes are absent, so those facts remain unknown. No camera
files or settings changed. Late-boot facts do not prove early-boot timing,
persistence or restore; the shipped selection gate remains blocked.

## Chunk 3 design reviewed — 2026-11-14

The manager reviewed the revised [boot-launch contract](../work-packages/CHUNK3-BOOT-LAUNCH-CONTRACT.md)
against the saved S01app (156 lines, 4,246 bytes; five launch sites). Corrections
separate build-input and replacement hashes, refuse unverified stock fallback,
require a per-boot RAM launch stamp, and prohibit a second app after candidate
exec was attempted. Breadcrumb recovery is conditional on unproven flash durability.
Backup gating and candidate-only core suppression are explicit design changes.
No implementation or hardware acceptance follows from this document.

Manager-led chunks 4–6 remain authorized locally. Next is the inactive
stock-fallback implementation and focused fixture proof; installation is separate.

## Chunk 2 complete — 2026-11-14

The bounded AO2 review confirmed the instruction/address claim and fixed the
verifier's missing static GOT-symbol mapping. The manager cross-checked the
saved binary independently, reproduced **four synthetic tests**, strengthened
the relocation-precedence case, and reproduced **11/22 passing checks** in the
standalone verifier (11 still fail; exit 1). AO2 is reviewed static evidence;
G8's incidental pass is unreviewed. No audio ownership or hardware behavior is
proved. See the [chunk 2 report](../work-packages/CHUNK2-ADAPTER-AO2.md).

Chunk 2 stops here; no later chunk or camera operation started. Earlier test
counts below are historical checkpoints, not a new full-suite result.

## Chunk 1 complete — 2026-11-14

The requested Antigravity Gemini 4 Argon High review found no code defect in the
companion Stop/lease-loss boundary. Two regression tests cover stubborn delayed
providers, queued work, restart/reacquisition, takeover during speech and local
expiry. The manager reviewed the relevant paths and reproduced **31/31 companion
tests**. Production code is unchanged. This is local simulator/loopback evidence;
already dispatched speech and physical cleanup remain unverified. See the
[chunk 1 report](../work-packages/CHUNK1-COMPANION-STOP.md).

Chunk 1 stops here. No later chunk, camera operation or new bootstrap work started.
The 311-test result below belongs to the earlier checkpoint, not this review.

## Previous stopped checkpoint — 2026-11-14

Adam requested a stop followed by committing all remaining local files. WP7 was
cancelled; WP6 had already failed because its provider safeguards rejected the
session. No replacement delegate or new launch-design package was started.

The preserved [companion prototype](COMPANION-SERVICE.md) passes 29 local tests.
It is unfinished integration work, with simulator/fake-provider evidence only.
The preserved [adapter verifier draft](../../native/adapters/README.md) passes 9/22
checks (13 fail); its address claims and decoder remain unverified. No audio,
gimbal or face backend was delivered. Raw evidence stays private and ignored.

The completed foundation and activation review passed 282 tests before this
checkpoint. Final verification including the companion passed **311 tests**;
the failing standalone adapter verifier is not part of that suite. The first
resume task is review of the preserved prototypes, then
the offline S01app launch-substitution design. No camera write or deployment is
authorized by committing this work.

## Next work and constraints

**Accepted direction: [hardware ownership execution plan](HARDWARE-OWNERSHIP-EXECUTION.md).**
Keep the native app as the initial hardware owner and add a cooperative bridge
through the authenticated portal. First local deliverables are the native-thread
contract and status-only fake/ARM bridge. Boot-time selection is the preferred
activation design to evaluate; it is not implemented or authorized for installation.
The [go-forward assessment](GO-FORWARD-OPTIONS-2026-11-14.md) supplies alternatives. New
[lifecycle mapping](../NATIVE-LIFECYCLE-2026-11-14.md) confirms native watchdog ownership,
SIGTERM crash/re-raise behavior, ignored SIGINT and broad media/network/USB teardown.
Actual watchdog timeout/nowayout and warm restore remain unverified. The handoff
contract is currently preflight/report only; no process-stop backend is eligible.

Compare cooperative native hooks with a boot-time app selector, and inventory
matching SDK/ABI components as a fallback. Public V853 kernel source is a useful
new lead, not a verified source match. An early selector would need a separate
bootstrap/workflow decision; ordinary S99 TF updates do not implement it. Keep
the [guarded late-launch plan](GUARDED-TF-LAUNCH-PLAN.md) as a gated option rather
than proceeding with kill/restart. No supervisor, TF update or camera activation
was performed. Fresh read-only USB hashes match stock sdvcam/glog; the portal
still reports 0.3.0, owner null and preview off. No signal, ioctl or radio change.

Investigate the missing native RTSP initialization while preserving managed home
Wi-Fi. Fresh [driver/application research](../RTSP-BLOCKER-RESEARCH-2026-11-14.md)
confirms loaded capture drivers and native camera/encoder resource ownership.
Prefer a narrow native bridge, then a matching-SDK user-space media backend;
a custom kernel driver is not yet justified. No matching SDK/backend was built. The [saved QZ trace](../MEDIA-STARTUP-TRACE-2026-11-14.md) distinguishes the
9999/4016 heartbeat and2008/2005 read queries from another backend's StreamStatus=ON.
No eligible existing public startup command is established. Route 1 now has an
[offline native integration candidate](../NATIVE-MEDIA-CANDIDATE-2026-11-14.md): app
connect/disconnect can reuse stock media init/teardown without radio events. Four
guard/layout tests and six synthetic ARM slice cases pass; full native suite 69.
The candidate is private, uninstalled and outside the TF portal package. Actual
S01app precedes TF startup, so guarded native launch/resource handoff, rollback
and real media validation remain required before a hardware trial. Do not guess/replay presenter,
udev, recording/reset/mode commands, force radio changes, restart stock sdvcam ad hoc,
or patch process/VRAM memory as a substitute for that missing bridge.

Sensing/control must use authenticated CammyOS hooks. New camera code/firmware
edits remain on the powered-off TF update path; USB is for bounded read-only
reference/maintenance under the current constraints. Do not flash internal NOR.
Native ownership/mixer/IPC mapping is still narrower than the full plan. Actual
frames, microphone/speaker adapters, gimbal, screen face and conversation remain
open milestones. See [architecture](../../architecture.md), [v0.3 plan](PLAN-0.3.0.md),
[probe contract](../PROBE-SUPERVISOR.md) and [readiness contract](../PREVIEW-READINESS.md).

## Next product milestone: talk to an existing T3 thread

The [new research plan](T3-VOICE-VISION-PLAN.md) keeps Reachy as the app/SDK
foundation and treats Cammy as a voice/vision interface to selected T3 threads,
with standalone API chat as an alternate mode. Next: RC2 wireless speaker
acceptance, then an actual three-turn T3 conversation with fresh images and
Cammy speech. Realtime frontend, browser duplex, physical Talk and persistent
engagement are later integration steps, not present capabilities.
