# Architecture history (archived 2026-11-16)

Sections removed from `architecture.md` when the project moved to the Gemini Live plan ([PLAN](../PLAN.md)). Historical; not current direction.


[Local RC result and exact package](../RC06-LOCAL-RESULTS.md):254local tests plus27 ARM portal/client checks passed; candidate remains uninstalled.

**Current implementation,2026-11-15: integrated CammyOS0.6.0-rc1 local candidate.**
[Contract](../RELEASE-0.6.0-CANDIDATE.md), [installation review](../RC06-INSTALL-REVIEW.md)
and [control inventory/PTT status](../M105-CONTROL-INVENTORY.md). The new native
service retains twelve ParamFace mood/blink frames and processes a fixed256byte
Cammy-owned RAM IPC page on the native GUI thread. Boot/instance/generation,
request order, deadlines and GUI heartbeat bind commands. Repeated show/hide/
replay, screen-change yield and Stop are locally tested against the actual
saved-libc/time32 DSO with synthetic GUI bindings. No VRAM writes or arbitrary
native dispatch are added. Physical native animation is still untested.

The0.6 portal/SDK/gateway/app share boot and random lease identities. Delayed
renewals cannot reacquire expired leases; ordered face/input intents reject stale
holds after release. An independent input guardian emits only observed stock keys
and releases finite holds on expiry/parent loss. No angle or autonomous tracking
stop is claimed. Media preparation is an explicit once-per-boot job with a fixed
client, reserved ownership, deadline and fault latch; boot does not initialize it.
The versioned TF/runtime/desktop package carries exact native patch/ABI and member
hashes, disabled selection and original startup rollback. Network loss degrades
health without reboot; health alone never rearms. Resident policy stays opt-in.

Generic VERSION remains0.5.0; the separately compiled candidate reports0.6.0-rc1.
No new candidate files are installed. The last stable camera remains untouched
while local assembly runs. Unexplained prior resets, three cold boots, one-hour
combined operation, physical input expiry and stock rollback remain hardware gates.

**Physical LCD plus live A/V passed a finite trial.** The GUI-owned Pikachu happy
image appeared for about ten seconds on boot a68e8ed8, observed by the P15, then
stock UI returned. Conservative overlap8.873s contained45 decoded frames and
140,800 microphone samples; entire session148frames/448,000samples. Release
ended idle/owner null/preview off/ALSA CLOSED; original S01 restored. P15 exposure
prevents a fine-detail/colour claim. Reusable native animation/live face commands now exist in the local candidate;
only the earlier static image has physical evidence. The PC loop remains available.

**Release blocker: subsequent resets.** Later boot IDs changed without a further
agent reset command. Latest bdc7d9ca stock boot was stable at458s with automatic
runtime blocked by the preceding boot's startup guard. Cause is unresolved;
keep that guard intact. The missing `rmdir` cleanup defect was separately fixed
and proven in RAM on Cammy; it is not a complete diagnosis of later resets.
See [trial evidence/recovery](../FACE-BOOT-TRIAL-2026-11-15.md). Voice work paused.


**Character voices and speaker routing,2026-11-15:** the existing PC conversation
worker is still Kokoro/P15. An [offline audition tool](../../tools/audition_character_voice.py)
now composes small expressive Silencio-2 speech with four character-trained RVC
models, without app/camera contact. Twelve emotion-tagged Fin and Fur greetings
were generated; Adam rejected all four voices. The paused next research route is
direct reference-conditioned cloning from clean audio, with independent speaker/
emotion references where supported. Models
stay on SERVE, inference is in an isolated LAB environment, and voice selection
remains above the transport. See [evidence and candidates](VOICE-OPTIONS-2026-11-15.md).

Speaker routing update,2026-11-15: the [private-route investigation](../SPEAKER-ROUTING-2026-11-15.md)
fixed the earlier `dmix`/`asym` open failure by using an inline raw hardware slave
inside the existing stock dmix namespace, plus process-private timer/control
definitions. Actual finite PCM writes now finish and restore all mixer values;
SPK stayed ON during two-second output. The
initial trials were silent because fresh LINEOUT was0 (hard mute). After Adam's
stock playback reference established LINEOUT20, a guarded temporary gain trial
played the cue audibly; a second played local Kokoro “Hello world” clearly,
confirmed by Adam. Both restored LINEOUT0 and the exact SPK baseline with stock
boot/native/hash/mixer/PCM state unchanged. Transport was USB ADB PCM transfer and
finite playback; Wi-Fi speaker API, integrated Stop, media coexistence and echo
remain unverified. No global ALSA/init/native restart or OS-candidate change was
made. The existing0.6.0-rc1 artifact stays unchanged. The new
[0.6.0-rc2 candidate](../RELEASE-0.6.0-RC2.md) adds opt-in authenticated
`/v1/speaker/play`/status/cancel and SDK `play_pcm()`. Its independent device
guardian checks live shared PCM/mixer baseline, bounds50ms..5s and peak4000,
restores gain/switch state, and cancels on Stop/release/expiry/parent loss.
Local fake-worker API tests pass; Wi-Fi audio and new-worker physical acceptance
remain pending. Startup selection stays disarmed until its checked install.
RC2 release `48124da5e5405eb32797664b` is now TF-staged with37files fully
read back and the reader safely ejected; the active runtime and media are
preserved. See [staging evidence](../RC2-STAGING-2026-11-15.md).


**Live agent turn passed,2026-11-15:** the official Reachy app now includes a
[finite conversation inbox and P15 reply path](../LIVE-CONVERSATION-2026-11-15.md).
A known P15 phrase traveled acoustically through Cammy's live microphone into
local Whisper, then a session/turn-bound agent reply used local Kokoro and the
P15. Cammy's microphone independently recovered that spoken reply while video
continued. Final media counted78frames/251,200 mono samples. Stop and separate
recognition-cancellation/expiry trials ended with owner null, preview off,
workers/WebRTC clean and ALSA CLOSED; stock/native/watchdog identity stayed intact.

`host/cammy_conversation.py` consumes the existing app's rolling PCM, with desktop
hold/release,8second utterances, current-boot/session checks, one expiring agent
turn, bounded owned workers and late-result rejection. The standard-library
`sdk/cammy_conversation_client.py` exposes that existing app extension to agents.
Model files are existing/offline; captured PCM stays in RAM. This is a real
finite external-agent turn path, not an installed autonomous model provider.
The legacy companion engine is still separate. PTT gates recognition during an
explicit media session; it is not physical microphone mute. Output is P15 only;
physical red-button PTT, VAD, Cammy speaker and full Reachy body remain open.
A later finite native LCD trial passed as described above.

**Native face trial built,2026-11-15:** a [GUI-owned surface, camera-ABI library
and exact-stock dual-hook candidate](../NATIVE-FACE-TRIAL.md) now pass local
verification. Native canvas APIs handle the vendor's padded image descriptor;
top-layer and delete-observer entrypoints are mapped. Cache invalidation is a
no-op in this exact build. The finite trial uses one immutable ParamFace image,
GUI-thread-only object operations, deletion observation and policy expiry;
pixel/library storage is retained through process exit. It requires no C++
runtime inside the camera app and no direct framebuffer drawing.

The452-byte main/GUI stubs fit in the existing mapped RX page, with one explicit
four-byte publication slot at the original BSS end in its existing RW page.
The library passes saved-libc/time32 ABI auditing;17 new focused tests and5
image-ABI regressions pass. The initial offline build was followed by the
physical drawing/coexistence trial described above.
The new `gui-face-trial-v1` class is refused by the old status-only activation
route. The separate GUI profile has since booted and shown a static face with
concurrent A/V. Full native draw retirement, reusable live control, restored-stock
boot/endurance acceptance and the later reset regression remain open.

**Live Wi-Fi media and intelligible speech,2026-11-15:** [35.015s of changing
native video overlapped20s of microphone PCM through the desktop gateway](../WIFI-AV-2026-11-15.md).
A local recognizer processed the first8s prefix while video continued;118 more
monitor frames followed. The full20s sample separately recovered the intended
request for a live conversation. This proves transport/intelligibility and
concurrent processing, not a finished rolling-transcript or conversation loop.
The measured interface average was1.303Mb/s, with no enforced cap.

Explicit Stop and lease expiry ended owned streams/preview and reaped capture
children; native capture CLOSED was independently observed afterward. Buffered
PCM cannot be recalled, so host consumers must discard it and stale recognition
on Stop. Physical microphone cutoff latency is unmeasured. The0.5.0 boot path now automates native initialization and owned DHCP recovery
without the Wi-Fi UI. Repeated unattended restart/endurance, end-to-end latency,
arbitrary speaker output and duplex remain open.

**Current accepted runtime:** [0.5.0](../RELEASE-0.5.0.md) normal TF boot,
automatic station/profile/guardian/native media initialization and fresh Wi-Fi
A/V passed without a RAM correction. Adam confirmed normal live view and no
Wi-Fi input. At observed uptime63.70s, startup was `sdp-ready-idle` and home
recovery `restored`, with no live stock DHCP helper.30.021s yielded145 decoded
monitor frames/144 changes alongside10s PCM through gateway8790; combined
interface average2.330Mb/s was measured, without a hard cap. Release left no
owner/session/capture child and driver capture CLOSED. Stock PID/start/hash
identities stayed intact. See [acceptance and limits](../COLD-BOOT-AV-2026-11-15.md).

**Integration lesson:** the transport test discarded decoded pixels. The surprise
scene was identified only after a separate live session retained two frames.
That gap is now addressed by the explicit-session Reachy app extension below,
with timestamped latest-frame access, freshness checks and the finite agent-turn
service above. The legacy companion is not yet wired to that new provider.
A screen face, Wi-Fi-only motor control and
arbitrary camera speaker output remain unverified. The existing companion core
is still wired to simulated/legacy capture contracts rather than the accepted
live stream. [Boot implementation details](../VIDEO-BOOTSTRAP.md) remain the
basis for the body runtime; the real Reachy Mini PC control contract is the
user-selected integration target. See the [compatibility plan and command mapping](REACHY-COMPATIBILITY.md).

**ParamFace feasibility,2026-11-15:** the pinned upstream face renderer now runs
on host, ARM/qemu and the real camera CPU in RAM. A356×400 cat/happy animation
rendered240frames in an observed3.25s; final pixels matched the host exactly.
Stock identities were preserved and RAM copies removed. This is not an LCD
write or visible-face proof. See [renderer evidence and scope](../../native/paramface/README.md).
The official Reachy app extension is now installed/running: ParamFace preview and
upstream editor at127.0.0.1:8801, with an explicit finite real-Cammy A/V session.
One20s trial yielded98 frames and312,000 PCM samples, then owner release/decoder
exit and camera microphone CLOSED. Stopping the app through Reachy's own manager
also closed real sensing; it was restarted idle. Timestamped latest JPEGs and a
bounded rolling PCM ring are now agent-accessible.

The follow-up now publishes real Cammy media through the upstream GStreamer
WebRTC protocol, consumed by a receive-only extension of the official Reachy
MediaManager/GstWebRTCClient. Two hardware trials passed (47 frames/147,200 audio
sample frames, then23 frames/73,280 after receive-only enforcement); Stop emptied
SDK output and camera capture was CLOSED. K/D are None, mono is duplicated only
for the SDK's two-channel float format, and speaker/motion operations refuse.
The full Cammy-backed ReachyMini body daemon/automatic media selection remains
unimplemented. [SDK media contract/evidence](REACHY-MEDIA-2026-11-15.md).

The native GUI mutex/loop and framebuffer flush path have12 exact static checks.
A new read-only camera query confirms356×400,32bpp,1424-byte stride and BGRA byte
order. Offline emulation of exact native image primitives establishes a12-byte
header and pointer offsets, distinct from standard LVGL8.3.11. The finite native
canvas surface is now locally implemented above; complete draw/window lifetime
and guarded native activation remain open, and no LCD was drawn.
[Native face ABI evidence](../NATIVE-FACE-ABI.md). See the
[implementation, acceptance and LCD route](PARAMFACE-REACHY-LAB.md).

The opt-in microphone endpoint is owner-scoped; the PC SDK and gateway implement
streaming alongside stock RTSP. Generic releases keep forwarding off. Native
preview itself can still acquire the physical mic even with forwarding disabled;
PTT does not yet provide physical mute while continuous video is running.

**Earlier USB entry proof:**35s of native home-Wi-Fi video overlapped20s of
shared ALSA microphone PCM over USB and recognition of Adam's speech while
video remained live. That established shared capture before the newer all-Wi-Fi
transport result above. Native RTSP audio itself still sends no packets; Wi-Fi
voice intelligibility is now verified above; low-latency complete transcription
and conversational duplex remain open. See [USB entry evidence](../LIVE-AV-2026-11-15.md).

This prototype uses stock RTSP and standard ALSA sharing. It required neither a
new kernel driver nor an in-process native bridge. The bridge remains a separate
fallback/investigation track, not a prerequisite for the demonstrated live path.

**Product priority,2026-11-15:** Cammy is intended to be a live robot terminal
for a PC agent, with continuous camera media and session-gated microphone audio,
incoming speech, gimbal and face. File recording/download is diagnostic, not the
conversation transport. The [current milestone](LIVE-MEDIA-MILESTONE.md)
prioritizes sustained live receive before control-console polish or more buttons.

**Local companion update, 2026-11-14:** the [push-to-talk engagement policy](PUSH-TO-TALK.md)
now wraps existing companion sensing/lease/cancellation with idle audio disabled,
finite follow-up windows, agent wake and mute. This is simulator-validated host
code, not a TF update or physical red-button implementation. On2026-11-15 passive
capture mapped record to event3/code28, sharing a bank with power116. Stock
recording suppression remains unimplemented. A native recorded speech clip was
intelligible to two local recognizers; it was4K/33.40Mb/s, above the15Mb/s target.
See [button/audio evidence](../RECORD-BUTTON-AUDIO-2026-11-15.md).

This document records the hardware identified on Adam's Ausek AT-M105, the implemented CammyOS prototype, and the access paths investigated so far. Hardware observations and test results are from November 2026; addresses and the current running state can change. **Implemented**, **tested**, **reported**, and **unverified** are used deliberately.

Latest functional review: **2026-11-15**, including passive red-button mapping
and recorded speech recognition, following remote stock-input gimbal movement
and audible file playback. Evidence and qualifications accompany findings.

The project goal is a Wi-Fi desktop companion with a real gimbal, a screen face, camera, microphone and speaker. The current body runs the **vendor Linux kernel and `sdvcam` application**, plus a CammyOS runtime loaded from TF. The separate stock-derived firmware candidate has not been installed. The PC gateway is working; the complete companion agent and hardware adapters are future work.



---

### Native media integration candidate — offline only, 2026-11-14

Exact-binary tracing now identifies 4016 as liveness bookkeeping and 3033
ENTERPREVIEWSCREEN as a log-only handler. Native media creation responds to
SOFTAP_ENABLE, while window-based recreation requires internal AP mode 3. The
examined HTTP/event/hotplug interfaces did not expose a verified independent start.

A private, exact-hash ELF candidate preserves normal APP_CONNECTED callbacks then
enters the existing media-only initialization body. APP_DISCONNECTED additionally
clears the started flag and tail-enters the existing media destructor. It reuses
stock camera/encoder/RTSP code without publishing a radio event. Four guard/layout
tests and six synthetic ARM slice/ABI cases pass; full native/host suite: 69 tests.
Hardware behavior, original observer callbacks, native exception recovery and
media frames remain unvalidated. No camera executable was changed or launched.

The actual S01app launches the stock executable before S99 loads TF runtime; its
`.sdvcam` branch is backup handling, not an alternate loader. Thus this offline
candidate requires separate guarded TF launch/handoff work before a hardware
trial. It is not included in the 0.3 runtime or deployed. See [candidate details,
addresses, limitations and activation requirements](../NATIVE-MEDIA-CANDIDATE-2026-11-14.md).

### Guarded TF launch design — plan only, 2026-11-14

The [launch plan](GUARDED-TF-LAUNCH-PLAN.md) chooses a late, controlled
stock-app handoff from a separate TF-supplied supervisor, after ordinary network
startup. It proposes copying the exact private candidate to RAM and preserving
the internal executable/init scripts. Card insertion or a reboot must never
start a trial alone: each attempt requires a fresh authenticated boot-bound
request, a control lease and a consumed one-shot arm record. Trial state stays
separate from the ordinary internal startup latch.

This is a design, not implemented launcher behavior. Native shutdown, watchdog,
respawn, resource release, startup radio effects and original-app restoration
are the first mapping gates. The inspected S01app stop uses force-kill and its
start writes power/MMIO; neither is a verified handoff to replay. Local supervisor
fault tests, preflight-only TF boot, original-app handoff/restore acceptance and
one bounded candidate trial follow in that order. Recovery must not run two
native apps or retry indefinitely. Current camera software/runtime is unchanged;
the trial is not hardware-ready and real media remains unverified.



---

### Direction after lifecycle review — assessment, 2026-11-14

The [go-forward assessment](GO-FORWARD-OPTIONS-2026-11-14.md) keeps the stock
kernel/tuning and existing host API, and compares cooperative native ownership
with boot-time app selection before active launch work. An early selector could
avoid warm watchdog/resource transfer, but no such TF loader exists; a one-time
internal init change or proven alternate boot would be a distinct workflow choice.
The late-handoff plan remains conditional on an actual shutdown/restore contract.

External research found a public Lindenis V853 Linux 4.9 tree with the same DT
watchdog compatible string, and OpenIPC source/emulation leads. These are not
M105 source/firmware compatibility proofs: Divinus marks V853 media in development,
and the checked qemu-bench board models target V831/V833 rather than this camera.
Seek matching SDK/ABI/IMX298 tuning as a fallback; full kernel replacement stays
separate. The companion's missing media, physical face/audio/motion adapters and
resident agent remain the product milestones; the existing gateway/simulator
should be retained while the hardware-owner boundary is resolved.

### Cooperative bridge contract — WP1 static review, 2026-11-14

WP1 maps one task worker, synchronous subject notifications and a selected
status-only attach candidate around main's queue-run call at 0x31DB0. A status
bridge must answer entirely from its own state; posting work does not establish
exclusive ownership of the lock-free presenter/media paths. The older media
candidate remains call-path evidence, not a launch-eligible integration.

The saved camera libc is musl 1.1.24/time32; the existing cross toolchain uses
musl 1.2.5/time64. WP2A now supplies a compatible device-ABI shared library and
synthetic loader/lifecycle proof, described below. A detach timeout must
retain every reachable state object and keep the library mapped until process
exit. The manager reproduced 42 static checks; hardware behavior remains untested.
See [WP1 ABI contract](../native-bridge/NATIVE-ABI.md).

### Status bridge foundation — WP2 local integration, 2026-11-14

A bounded status-only SEQPACKET bridge now implements hello/status/capabilities,
with an explicit fake backend and absent native backend. Protocol 1.0 clamps all
physical capabilities unavailable. The manager reproduced 29 tests on host,
ASan/UBSan and qemu-arm, plus an 88-check core fixture. Resource-exhausted accept
now fails the bridge instance instead of busy-spinning in the native owner.
These WP2 results are standalone/inert fixtures; the separate in-process proof is
WP2A below. Neither establishes camera acceptance. See the
[protocol](../native-bridge/PROTOCOL.md) and [WP2 report](../work-packages/WP2-STATUS-BRIDGE.md).

### In-process status loader — WP2A local proof, 2026-11-14

The status-only library builds with musl 1.1.24 headers against the saved camera
libc. Its release ABI has exactly two exports, one libc dependency and 42 imports,
with no time64 imports, constructors or TLS. The manager reproduced the ABI audit
and all 27 tests, including saved-libc qemu loading, inert ARM stub execution,
identity refusal, fallback, detach abandonment and descriptor exhaustion.

The private candidate is built from pristine stock, with one call-site change,
a 360-byte loader stub and an executable LOAD size extension within the already
mapped page. No media callbacks change. The library path contains its SHA256;
the loader itself does not verify that digest, so staging integrity remains an
activation responsibility. The library and all abandoned-thread state stay mapped
until process death. Measured qemu timing is not a hardware timing guarantee.
No candidate is deployed; physical capabilities remain unavailable. The separate
RAM-only synthetic-host smoke below adds library evidence only. See the
[loader proof](../native-bridge/LOADER-PROOF.md).

### RAM-only library smoke — real M105, SYNTHETIC host, 2026-11-14

The exact WP2A release library now has one bounded real-board smoke in a separate,
disposable synthetic process built with saved-camera musl 1.1.24/time32. This
establishes library loading and private Unix hello/status/capabilities on this
board, with every physical capability unavailable. It establishes no in-sdvcam
integration, media/ownership readiness or selected-candidate boot acceptance.

Observed board load/attach/detach were 316/464/1,255 µs (one sample). Detach
removed the socket and restored the exact three-descriptor set and one-thread
baseline; the process exited zero. Exclusive root-owned 0700 tmpfs staging,
bridge socket/lock and all trial files were removed. Stock boot ID, native
PID/start, watchdog fd identity, init/app/libc/startup hashes, mount table and
native mappings matched before/after. The real home gateway stayed connected,
with sensing off, no lease owner and preview idle. This short software check does
not repeat visual LCD acceptance or prove long-duration/in-process timing.
See [RAM library smoke](../work-packages/RAM-BRIDGE-SMOKE.md); exact private
checkpoint: `private/bridge-smoke/current.json`. No persistent camera changes.

### Owned video supervision — WP3 local component, 2026-11-14

The owned-media core implements one owner/session, boot and instance binding,
request deadlines, lease revalidation before dispatch, a bounded completion
mailbox, cancellation and explicit uncertain cleanup. A late successful start
after a cleanup acknowledgement requires another cleanup; a timed-out cleanup
blocks a new owner. Synthetic frames are always labeled synthetic and video
availability stays false.

The manager reproduced 31 tests each on host, ASan/UBSan, TSan and qemu-arm,
plus 15 static checks. These establish local supervision behavior only. Static
analysis did not establish serialization across the native RTSP entrypoints;
native-kind backend tables are refused. A separate future media transport is
specified, but neither portal/native dispatch integration nor camera media is
implemented by this package. See the [media contract](../native-bridge/MEDIA-CONTRACT.md).

### Activation blocked after review — WP4/WP5, 2026-11-14

WP4 adds local policy/bundle/fake-process tooling and an inert early-selector
proposal. WP5 expands this to 94 activation tests plus an acceptance-evidence
validator with 32 tests after manager review, all reproduced locally. The SHA256
library path and structural-versus-reviewed classification are aligned with WP2A.
A manager regression ensures a fixture item cannot be hidden by a passing item
for the same acceptance stage. Evidence validation grants no installation approval.

The bind-overlay design is activation-ineligible (EA blocked): if card detach
fails, stock S01app can write a backup into internal storage; an unrestorable
foreign executable mount can still be launched. Card I/O before the one-shot
claim can also repeat a hang across boots. The shipped select gate is blocked,
and no bundle can produce an installable allowlist. The manager has selected a
reviewed S01app launch substitution, now locally implemented and packaged below,
with explicit stock fallback and no bind over the executable. The next test is
a separately approved stock-only bootstrap, not an installed native candidate. Core-dump
policy, early-boot timing, persistence and real restore remain open. See the
[independent review](../work-packages/WP5-ACCEPTANCE.md).

Chunk 3 now specifies the replacement-launch boundary in the
[reviewed contract](../work-packages/CHUNK3-BOOT-LAUNCH-CONTRACT.md). The saved
S01app is 156 lines/4,246 bytes, with launch sites at lines 91, 99, 101, 114 and
118. A future generator must reject other source bytes, preserve stock startup
setup, route all five sites through one launch authority and tighten backup
eligibility to the normal TF mount. Invalid stock identity means no launch;
candidate exec attempt is never followed by an automatic second app. The
pre-TF breadcrumb needs real persistence proof. This is a local specification,
with a RAM launch stamp and candidate-only core limit proposed; neither is installed.

Chunk 4 implements the inactive stock-fallback slice locally. The manager
reproduced27 focused tests after hardening RAM-directory ownership/mode, normal
TF mount source identity, exec-attempt reporting and generator output handling.
The five launch branches run only with a synthetic stock executable under a
relocated dash harness; hardware startup ranges are preserved byte-for-byte and
parsed, never executed. No candidate path exists in this slice, and no camera
init files changed. See the [stock-fallback proof](../work-packages/CHUNK4-STOCK-FALLBACK.md).

The [pre-chunk-5 USB inventory](../CAMERA-COMPATIBILITY-2026-11-14.md) freshly
matches the live app/init/libc hashes to saved inputs and confirms the tested
shell/tool features. `/run` and `/tmp` are root-owned1777 tmpfs; the native app's
core limit is unlimited and its fd3 points to the watchdog. Watchdog timeout and
nowayout sysfs attributes are absent. This metadata-only check changed no camera
files/settings and does not establish early-boot timing, persistence or recovery.

Chunk 5 adds fixture-only one-shot selection behind the unchanged blocked gate.
Manager review fixed ignored sync errors and binds the external allowlist's
S01app checksum to the actual replacement script. Forty-eight focused tests pass;
the independent review found no additional blocking bug under the declared trust
model. Manager follow-ups validate private persistent state, finish multi-claim
observations, exercise read-back corruption and label preparation evidence
separately from exec attempts. Final repository verification passed 367 tests,
including 50 boot-launch/selection tests. Hardware gates remain open. No selected hardware boot, flash
durability, effective watchdog timeout or physical cleanup has been established.

Chunk 6 adds an offline review-package builder/validator over these frozen inputs.
It binds the replacement S01app checksum in the allowlist, includes the exact
stock rollback script, and reclassifies the candidate against pristine stock and
the loader evidence. The 14-file private package is non-executable and disarmed:
arm/ack files are non-consumed templates, the enable template says disabled, and
the generated launcher retains its blocked gate. Hardware checklist observations
remain unset. Validation returns LOCAL_ONLY/BLOCKED_FOR_ACTIVATION; it supplies no
installer or live authority. See the [review and rollback runbook](../NATIVE-TRIAL-REVIEW.md).

Stage-A hardware checkpoint (2026-11-14): the approved exact blocked S01app
proposal booted the unchanged stock app. Preparation recorded 1.68–4.75 seconds
(3.07s on this single boot); launch records selected stock with no enabled config.
One PID/start time and boot ID stayed unchanged across 21 samples over 600.086s.
The home gateway stayed connected and Adam confirmed normal live LCD. No candidate
library, private TF mount or selector record was observed.

The exact original S01app (SHA256 3327c3a9...) is restored. A second cold boot
has original init/app hashes, no launcher RAM directory and normal LCD. Its
21-sample, 600.066s stability window passed with one unchanged PID/start time and
boot ID and gateway connected at every sample. Temporary artifacts are absent;
the original startup is left installed. This is
in-band return to stock after one internal-overlay file test, not a full-image
flash, candidate activation or proof of arbitrary corruption recovery. See the
[hardware report](../BOOTSTRAP-HARDWARE-2026-11-14.md).

Target inspection revealed native chown, rmdir and df absent (runtime BusyBox df
also absent). The transaction now verifies ownership of root-created files and
uses rm -r only on its own exclusive private directory; it never removes a stale
foreign lock. Required-command inventory is part of preflight. Thirteen focused
fixtures pass, plus actual on-camera install/restore read-back and the blocked
stock boot described above; candidate early-boot and power-loss behavior remain unproven. Private execution state is indexed at
`private/bootstrap-hardware-test/current.json`.

Next-test readiness (2026-11-14) narrows Stage A to one persistent change:
replace S01app with the exact blocked proposal, test unchanged stock startup,
then restore the exact stock script and verify another boot. No candidate, TF
arming or selector configuration is installed. The inert transaction serializes
writers with a private temporary directory, validates payload/current identities,
parses the staged file, syncs, renames on the same filesystem and verifies read-back.
A sync/rename failure is not permission to reboot or retry automatically.

Live read-only preflight confirms the current stock hashes, init ownership/modes,
root access, executable /run tmpfs and one stock app with watchdog fd3. Target
shell controls return 0 for parse-only `exit 73` and 2 for invalid syntax. Exact
proposal, transaction and rollback bytes parse successfully after on-device
SHA256 checks; no startup code ran and no camera file was written. Stock adbd
supports only legacy PTY with a short command limit; -T/exec-out fail, base64/
gzip/gunzip applets and stty are absent. The PC collector streams to a shell
variable then hash-checks and pipes to sh -n, with no target temporary file.
These observations leave early-boot creation, timing, physical persistence and
recovery gates open. See the [stock-bootstrap test plan](../STOCK-BOOTSTRAP-TEST.md)
and private `private/bootstrap-readiness/checkpoint.json` for exact evidence.

Fresh bounded read-only USB metadata confirms the live S01app SHA matches the
preserved script and /tmp plus /run are tmpfs on this boot; /dev/mmcblk0 is the
current superfloppy TF device. This does not prove availability at S00 or validate
mount options. BusyBox self-list and shell command-discovery probes were unavailable;
applet/options evidence remains partial. Raw evidence is private at
private/ownership-program/manager-e3-live.json. No camera code or firmware changed.

### Paused local adapter and companion work — 2026-11-14

The remaining delegate work was stopped before final delivery. WP6 ended with a
provider safeguard rejection and left only a draft static verifier: the manager
initially reproduced 9/22 passing checks. Chunk 2 repaired static GOT-symbol
resolution and independently confirmed AO2: this exact executable passes
`g_audio_mutex` to `pthread_mutex_trylock` at 0x80E8C. This establishes one call
site, not global audio serialization, runtime thread ownership or safe playback.
The manager reproduced four synthetic tests and a partial 11/22 verifier result.
G8 also passes incidentally but was not reviewed; the remaining draft claims are
not accepted architecture evidence. No body adapter exists from that package.
See the [AO2 review](../work-packages/CHUNK2-ADAPTER-AO2.md) and the
[draft status](../../native/adapters/README.md).

WP7 left a tick-driven desktop companion prototype above the existing SDK and
simulator, with explicit sensing, lease management, bounded queues and stale-work
rejection. The manager reproduced 29 local tests after stopping the delegate.
The later chunk 1 Stop/lease-loss review found no code defect; two new regression
tests bring the companion suite to 31, reproduced by the manager. Delayed jobs
that ignore cancellation do not dispatch new actions after Stop/lease loss in
these fixtures. Already dispatched speech cannot be individually recalled by
the v1 API, and physical cleanup is unverified. See the
[bounded review](../work-packages/CHUNK1-COMPANION-STOP.md).
The broader prototype still needs review; no real media/provider integration,
resident installation or physical audio/gaze/face behavior is established.
See the [companion checkpoint](COMPANION-SERVICE.md). No new bootstrap design
package was started. Work is paused; resume from this checkpoint when requested.



---

## v0.3 TF runtime — boot/probes verified; media unavailable

The [v0.3 candidate](RELEASE-0.3.0.md) now combines the probe foundation,
nonblocking preview readiness, compatibility diagnostics isolation and simulator/
desktop support. Its TF update and actual-camera boot/probe checks are complete;
the PC gateway was restarted and the routes verified. No internal firmware was
flashed. The bounded live readiness result is rtsp-unreachable; frame delivery and
all physical adapters remain outside this acceptance.

Four fixed collectors cover runtime markers/uptime, selected IPv4 listeners,
framebuffer layout and ALSA PCM state. A single forked worker uses parent and child
one-second deadlines, bounded nonblocking pipe reads, two retained public results
and a separate legacy diagnostics buffer (each below16000bytes). Missing/error/
truncated sources are explicit. Unreaped workers make probing degraded and block
replacements. Children close inherited portal/client/preview sockets, request
SIGKILL on parent death and check for the fork/parent-exit race. The legacy
`GET /v1/diagnostics` response shape remains compatible while its read is deferred
into that worker. Stop can cancel it; busy/timeout responses are explicit.

Preview startup now accepts a nonblocking attempt with202. A retained event socket
sends4016 on connection and about every30seconds while the body lease remains valid.
It never renews that lease. A five-second fixed-interface8554 `/ch00` DESCRIBE check
reports event, reachable transport and structurally accepted video SDP separately.
Stop/release/expiry close both sockets. Readiness failure leaves a healthy ordinary
event session owned by its caller; an explicit owner-only check can retry. No new
native command, radio switch, pixels, motor bytes or audio operation is added.
SDP is a dated snapshot; `frame_fresh` stays null until a separate host frame path
exists. Wrong-interface causes need the separate metadata binding evidence.

The desktop exposes catalogue/run/cancel, job results and preview readiness. Stop
remains available during background reads. The simulator models serialized jobs,
probe hangs and event-connected/no-listener/slow-SDP/protocol-error states with
synthetic labels. Auxiliary result eviction does not mark the whole body offline.
The native and emulated ARM suites cover Stop during blocked metadata/legacy reads,
slow SDP and incomplete HTTP input; this is not a real-hardware timing guarantee.
Eight bounded HTTP client slots and nonblocking replies remain capacity limits.

The [saved-app trace](../MEDIA-STARTUP-TRACE-2026-11-14.md) found no verified narrow
native media-start bridge. Native process/mixer/IPC collectors and physical media,
face, motion and conversation remain open. The [probe contract](../PROBE-SUPERVISOR.md)
and [readiness contract](../PREVIEW-READINESS.md) describe exact effects and limits.



---

## T3-thread companion implementation —2026-11-16

The end product must support voice and fresh visual context for selected existing
T3 Code threads, as well as a separate standalone API-chat mode. Reachy remains
the desktop/SDK target. A PC companion service and T3 adapter own routing, media
and cancellation; GPT-Live may later handle speech while the vision-capable T3
backend handles images/tasks. The resident PC agent service now implements this
control seam using the official MCP Python SDK and installed T3 CLI. It is idle
on startup, with explicit finite sessions and playback blocked by the current
quiet policy. The Reachy app proxies thread selection, camera/mic sessions,
utterances and correlated replies through the same service. It never exposes
service credentials to the browser.

`cammy-agent.service` listens on loopback8791, behind the existing body gateway
on8790. Nine standard stdio MCP tools provide status, device readiness, session
open/Stop, fresh JPEGs, bounded pan, face, speech and local transcription.
Explicit T3 thread IDs bind callers to the selected device session; delayed
results from a stopped session cannot control a replacement. Physical cleanup
is never inferred from a successful release acknowledgement.

The T3 adapter uses public `thread list/show/send --json`, queues into existing
threads, and records a durable request receipt before sending. Uncertain delivery
is not retried automatically. Replies must match the acknowledged run and its
completed/settled state. CLI sends contain text only; the selected vision-capable
agent obtains a fresh image through the MCP frame tool, whose response includes
actual JPEG content and timestamp/boot/sequence metadata. Native image attachment
through external T3 MCP remains a separate OAuth integration. No provider token
is borrowed and no T3 database is accessed.

Evidence includes local fake-provider/loopback tests, actual stdio protocol
handshakes and a silent USB-forwarded control-lease/status/Stop test against the
real camera portal. That device test captured no media, played no audio, moved
no motor and preserved the stock boot/process/hashes/mixer state. The complete
spoken T3 turn and Wi-Fi speaker overlap are still unverified. RC2 remains the
frozen, staged TF candidate; this newer PC addon does not modify that package.
See [implementation and morning checks](../T3-CONTROL-IMPLEMENTATION.md) and
the [researched plan](T3-VOICE-VISION-PLAN.md).
