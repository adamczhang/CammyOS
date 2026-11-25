# Future milestones

**Current next milestone: three real T3 voice-and-vision follow-ups on Cammy.**
The [resident control implementation](../T3-CONTROL-IMPLEMENTATION.md) now has
standard MCP tools, supported queued CLI transport, thread/run correlation,
Stop/stale-result handling and a Reachy thread panel. 72 integrated local tests
pass; a silent real-device MCP lease/status/Stop test preserved stock state.
This does not yet prove a spoken T3 conversation or Wi-Fi speaker operation.
Quiet mode remains ON until daytime playback testing resumes.

Required next: reinsert the staged RC2 TF, checked bootstrap/user boot, accept
Wi-Fi speech/cancellation alongside video/mic, reload a provider with Cammy MCP,
then perform three project-aware turns about newly changed physical content.
After that: longer follow-up windows, physical record-button PTT and Robot GUI,
VAD/AEC, browser return audio, endurance/recovery and optional cloud speech.
Frozen RC2 artifacts remain intact. Earlier milestones below record history.

[Local RC result and exact package](../RC06-LOCAL-RESULTS.md):254local tests plus27 ARM portal/client checks passed; candidate remains uninstalled.

**Current milestone: [CammyOS0.6.0-rc1 integrated companion](../RELEASE-0.6.0-CANDIDATE.md).**
The versioned TF/native/desktop candidate now builds locally with reusable screen
expressions/replay/idle, explicit live A/V preparation, bounded input and shared
boot/lease/Stop semantics. Its private manifest/ABI/patch checks and rollback
package are disarmed; no installation has occurred. Hardware acceptance still
requires three deliberate cold boots, one hour combined operation, repeated
replay/head/Stop/disconnect and a verified stock rollback. Prior reset cause is
unresolved. Generic VERSION stays0.5.0; candidate binary is0.6.0-rc1.

**Control/PTT follow-up:** [inventory](../M105-CONTROL-INVENTORY.md) records the
stock speed setting, DOWN face tracking and startup sweep. Verify readback,
tracking disable/manual arbitration and event mappings before exposing new RPCs.
Desktop PTT already selects1..8second speech turns within a5..90second live-mic
session, with local transcription and agent replies on P15. Physical record-button
PTT, VAD and automatic follow-up/idle remain later work. Arbitrary Cammy speech
was subsequently heard in USB trials; its Wi-Fi API acceptance remains open.
Maple is the requested ChatGPT app voice preference; no provider is renamed Maple.

**New proof:** a static native LCD happy face coexisted with real video/mic for
at least8.873s; the complete session delivered148frames/448,000samples and cleaned
to idle/ALSA CLOSED. P15 observed removal; original S01 restored. Fine visual
quality, physical acceptance of native animation/live commands and reset stability remain open.
Voice work paused; screen replay deferred while planning the integrated RC.


**Requested stretch goal,2026-11-15:** customizable character voice/personality
packs, with a Pikachu-like Cammy option. Kokoro remains replaceable. The
[voice investigation](VOICE-OPTIONS-2026-11-15.md) now has an isolated
Silencio-2 → character RVC audition pipeline: four checkpoints × happy/surprised/
whisper tags, using “Welcome to the Fin and Fur Channel!” Adam rejected all four voices; PocketDM's prerecorded examples were also
rejected as robotic. Next is [reference-based cloning](VOICE-CLONING-PLAN.md)
with IndexTTS2.5, Qwen3-TTS1.7B Base and optional GPT-SoVITS fine-tuning.
No voice backend change in the resident Reachy app. Cammy-speaker speech was
later confirmed over USB; output transport and voice/persona remain independent.

**Finite live agent turns passed,2026-11-15:** the [Reachy-app conversation
service](../LIVE-CONVERSATION-2026-11-15.md) now supports desktop hold/release,
local recognition, an expiring agent inbox and bound replies spoken on the P15.
Both cue and reply were recovered acoustically through Cammy while video
continued. Stop/recognition cancellation and expiry passed with driver CLOSED
and stock identity preserved. Next: attach a resident agent, improve response
timing/follow-up policy, then physical PTT and Cammy-native output. This is not
autonomous always-on listening, VAD, Cammy speaker output or physical LCD proof.

**Current priority,2026-11-15:** adopt the actual Reachy Mini PC control surface,
SDK and daemon/media contract as the compatibility target. See the
[one-to-one control mapping and upgrade packages](REACHY-COMPATIBILITY.md). Map Cammy's body
capabilities honestly, integrate the accepted live media path into a resident
agent/control surface, and prove a face on the physical LCD while A/V continues.

**Reachy app integration passed:** [ParamFace lab](PARAMFACE-REACHY-LAB.md)
is installed in the actual upstream app manager. One20s real Wi-Fi A/V session
produced98 fresh frames/312,000 PCM samples with host face rendering, followed by
expiry/driver CLOSED. Official app-manager Stop also cleaned real sensing. The
app now provides fresh agent-visible JPEGs and a bounded PCM ring; a receive-only official SDK/WebRTC media extension now also passes hardware
trials. Full ReachyMini body/daemon compatibility and rolling speech providers
remain next work.
ParamFace runs on the camera CPU; the later static native LCD trial passed,
while reusable native expression/animation control remains open.

**Native face trial:** local ABI/surface checks were followed by physical static
face+A/V proof. The exact class remains distinct from the blocked status-only
launcher. See the evidence and subsequent reset blocker in the RC contract.

**Automatic live media entry passed:** [0.5.0 normal boot](../COLD-BOOT-AV-2026-11-15.md)
without Wi-Fi UI input,30.021s changing video and10s simultaneous Wi-Fi PCM
through the desktop gateway, followed by clean release/driver capture CLOSED.
Separate saved live frames allowed actual scene identification. The transport
counter test did not preserve a movie or agent-visible frames. Speech playback,
rolling conversation, browser media, Wi-Fi-only motion and endurance remain open.

These are planned outcomes; they are not claims that the corresponding hardware or agent functionality already works. The detailed design and acceptance targets are in the [full-feature draft](FULL-FEATURE-ROADMAP.md).

**The first real-frame milestone passed on2026-11-14:** four authenticated SDK
transactions delivered new5120×3840 camera JPEGs with boot identity, mode restoration
and stock preservation. Three resident captures took6.050–8.519seconds. Native
finite H.264/AAC recording also passed, including720p/30 Low at8.40Mb/s total-file
rate. This does not complete continuous streaming or native hardware ownership.
See [results](../LIVE-VISION-2026-11-14.md).

The [Wi-Fi control design](NEXT-CONTROL-JUMP.md) now feeds the actual
[Reachy Mini compatibility target](REACHY-COMPATIBILITY.md): smooth holds/
release, verified UI context and body adapters must work behind that surface.
The accepted media transport must become resident, agent-visible media through
this same interface. No separate custom console is the primary delivery.

The next media milestone is useful low-latency vision plus independently verified
simultaneous microphone/speaker operation, within a combined15Mb/s budget.
Native microphone signal capture has now passed a known acoustic-cue test.
Remote stock-input trials now also demonstrate a roughly90° left turn and
audible playback of an existing camera video. Live view was restored and the
head stationary afterward. These use leased USB input, not Wi-Fi-only body
actuation or arbitrary speech. See [results](../STOCK-CONTROL-BASELINE-2026-11-14.md).
USB duplex transports ran but the physical codec was not bridged to the gadget;
direct speaker streaming and conversational duplex remain open. The accepted720p/30 Low
preset is active. See [audio evidence](../AUDIO-TESTS-2026-11-14.md).
Finite owned speaker supervision is now locally tested on host/sanitizer/ARM.
An exact native audit identifies an unbounded stock callback wait; an eligible
native stop/cleanup adapter is still needed before speaker testing. See
[audio component and next boundary](../OWNED-AUDIO-2026-11-14.md).
The asynchronous worker/mailbox and saved-camera-libc shared component also pass
local fixtures. Native frame-layout and amplifier/channel ownership mismatches
still prevent a speaker trial; see [integration evidence](../ASYNC-AUDIO-2026-11-14.md).
Render the face locally from small commands. Native720p/30 and quality presets
exist; separate15FPS/bitrate controls and a hard transport limiter remain to be
established. See [measured budget and acceptance](../MEDIA-BUDGET.md).

PC-only progress: the [desktop panel and offline gateway](../DESKTOP.md) now
cover authenticated diagnostics, lease/stop/preview controls, and offline development.
A [local simulator](../SIMULATION.md) also supports virtual adapters, synthetic media,
fault injection and repeatable host tuning. Continuous media, physical adapters and the resident companion remain pending; milestone 5 is
partially implemented, not complete.

The earlier v0.2 added read-only body diagnostics and verified TF packaging; its card
update and home-Wi-Fi boot were verified. See [current handoff](../STATUS.md).

The [v0.3 probe/readiness candidate](RELEASE-0.3.0.md) is locally tested and
staged, with simulator/desktop integration. TF files and actual camera boot/probes are verified; the restarted gateway works.
RTSP readiness still reports unreachable; actual media acceptance remains pending.

Latest local foundation: a device-compatible status-only library and inert loader
proof, plus an owned-video supervision core with its native backend disabled.
The [activation review](../work-packages/WP5-ACCEPTANCE.md) blocks the early
bind-overlay selector despite passing local tests. The reviewed S01app launch
substitution and one-shot selection now have local fixtures and a disarmed
[review/rollback package](../NATIVE-TRIAL-REVIEW.md). Selected native-candidate
boot, one-shot claims and post-attempt recovery remain pending. The earlier
media-connect-triggered candidate remains
ineligible. Working frames and owned native cleanup are still open gates.

The [stock-only hardware test](../BOOTSTRAP-HARDWARE-2026-11-14.md) passed one
600-second modified boot with normal live LCD and home connection. Exact stock
S01app is restored and the second stock boot also passed a 600-second window,
with correct hashes, live view and home connection. Candidate selection and media
remain gated; the original startup is left installed.

Manager-led local startup work resumed after the 2026-11-14 checkpoint; hardware
activation remains gated. The preserved
[companion prototype](COMPANION-SERVICE.md) passes 31 local tests after the
bounded Stop/lease-loss review, but has no
resident or real media integration. The body-adapter package stopped with a
provider rejection; its draft verifier now passes 11/22 checks after chunk 2's
AO2 fix. Only AO2 was independently reviewed in that chunk; G8's incidental pass
remains unreviewed. These partial
results do not complete physical milestones below; review them before resuming.
The [v0.3 plan](PLAN-0.3.0.md) remains the earlier release design.

Lifecycle mapping now identifies a native watchdog and crash-style SIGTERM
handling; a generic late restart is not eligible. Before implementing active
launch, use the [go-forward assessment](GO-FORWARD-OPTIONS-2026-11-14.md) to
choose cooperative native integration or separately designed boot-time selection,
with a matching-SDK media backend as fallback. This is a new decision gate within
milestone 1, not a completed media capability or a switch to kernel development.

1. **Home-network vision and media:** provide fresh frames and a continuous camera stream through the PC gateway; recover from disconnects and avoid presenting stale frames.
2. **Microphone and speaker foundation:** stream intelligible microphone audio and play an audible phrase through Cammy. Verify simultaneous capture/playback and measure actual hardware limits.
3. **Agent-controlled gimbal:** identify the real control protocol, axes, travel limits, stop and feedback; expose bounded real movement and stop through the shared gateway, including safe behavior on disconnect.
4. **Screen face:** render an animated face with idle, listening, thinking, speaking, muted and disconnected states while the stock media and watchdog continue working.
5. **Resident companion and desktop controls:** extend the existing gateway with media supervision and a persistent agent runtime. Provide a local control panel for status, modes, preview, volume, movement and diagnostics; work without an open chat.
6. **Push-to-talk and finite conversation windows:** use the red record button in robot mode to open listening, keep follow-ups available after release, then return to idle without ongoing audio capture. Allow authorized agent wake; preserve mute/Stop. Local engagement policy exists; physical mapping passed2026-11-15 (event3/code28, shared bank with power116). Suppression of stock recording remains an acceptance gate. See [contract and acceptance](PUSH-TO-TALK.md).
7. **Two-way conversation and voice quality:** integrate replaceable speech/agent providers; tune capture, output and voice; add echo cancellation, streaming playback, interruption and cancellation for genuine full duplex.
8. **Embodied attention and ambient behavior:** coordinate real gaze, expressions and voice; add fresh visual context, presence/tracking, selected memory, quiet modes and configurable proactive speech.
9. **Agent and robotics integration:** expose supported capabilities to other authorized agents using the versioned HTTP/Python API and an MCP adapter; add optional host-side ROS 2 mappings once actual hardware behavior is known.
10. **Daily reliability:** improve recovery and return from fallback, address discovery, startup-latch handling, updates/rollback and observability. Pass a loaded 24-hour test including power, network, PC and model failures.
11. **Separate firmware/OS track:** verify flash restoration and rollback, then investigate a recoverable internal installation and board-matched source-built components. Full replacement OS boot and flashing are not prerequisites for the earlier companion milestones.

The next implementation slice is decoded video and session microphone audio arriving on the PC while the source is running, without TF record/download as transport. Follow with simultaneous speaker output, measured delay and sustained operation. If existing hardware cannot meet the requested experience, document measurements and alternatives before treating a degraded workaround as completion.


### 2026-11-15 transport candidate

0.4.0 implements authenticated leased Wi-Fi PCM alongside stock RTSP, with a
bounded RAM shared-capture profile and restoration guard. Local protocol,
gateway and fake-mount lifecycle tests pass; ARM builds. TF hardware acceptance,
continuous home-network video+intelligible Wi-Fi microphone, sustained/latency
measurements, true PTT idle behavior and streamed Cammy speech remain open.
See [release and next test](RELEASE-0.4.0.md).


### First all-Wi-Fi transport —2026-11-15

[Passed a bounded simultaneous receive trial](../WIFI-AV-2026-11-15.md):25s
changing video and20s microphone PCM over home Wi-Fi, with no USB media or TF
recording. Measured interface average1.363Mb/s; no peak cap or sustained claim.
Next: human voice recognition while video continues, actual desktop PCM proxy,
corrected TF deployment, startup automation, sustained/latency and speaker duplex.


### Understood Wi-Fi speech through the desktop gateway —2026-11-15

[Passed](../WIFI-AV-2026-11-15.md):35s video with20s live microphone through the
resident desktop gateway; local recognition of an8s prefix finished while video
continued, followed by118 frames. Full-sample recognition confirmed the intended
request. Actual Stop and lease expiry ended streams; native capture closed.
Next: corrected0.4.1 TF deployment, automatic startup/IP repair, complete rolling
recognition inside the engagement window, and streamed Cammy speaker output.


### Corrected TF runtime staged —2026-11-15

[0.4.1](../RELEASE-0.4.1.md) is now card-updated and verified after read-only
remount.0.4.0 is backed up on LAB/on-card;25 media files and older rollback trees
are unchanged. Reader safely powered off. Next: normal0.4.1 boot/profile/guardian
acceptance without a RAM patch, followed by the already-proven live media route.

### Automatic native video startup candidate — 2026-11-15

[0.5.0](../RELEASE-0.5.0.md) packages an opt-in one-shot existing-API initializer,
independent home-IP recovery and normal preview reload. Warm fresh video/PCM
passed; real ARM read-only preflight and camera shell parsing passed. Normal TF
boot without opening Wi-Fi and subsequent live A/V acceptance have now passed. Stock DHCP ignores
TERM; verified exact-helper cleanup is now required before recovery succeeds.

## T3 voice and vision —2026-11-15 target

- Accept RC2 speaker over Wi-Fi with simultaneous video/mic and Stop.
- Choose an existing T3 Code thread in the Reachy app; send spoken turns and
  fresh camera frames, then hear that actual thread's reply from Cammy.
- Preserve project/thread context across follow-ups and reconnection; keep API
  chatbot mode as an alternative destination.
- Add natural interruptions/VAD/echo handling after the first real thread loop.
- Integrate physical Talk/Conversation/Idle and visible microphone state.

[Architecture, controller options and acceptance](T3-VOICE-VISION-PLAN.md).
