# Next control jump: a usable Wi-Fi console

**Priority superseded2026-11-15:** Adam approved [sustained live media](LIVE-MEDIA-MILESTONE.md)
as the next milestone. This console design is retained as supporting work.
Further button coverage or console polish must not delay live video/microphone
transport. Recorded-file playback is not the target conversation architecture.

Assessment after hardware milestone `ee22319`, 2026-11-14. Hardware work is
paused at Adam's request. This is a proposed next deliverable, not implemented
features or authorization to resume camera tests.

## What the milestone changed

The stock application already supplies useful motion, zoom, orientation and
media controls. CammyOS has root access and can deliver the same Linux input
events. The USB trial demonstrated real left motion and audible file playback,
without replacing the kernel, patching sdvcam or binding its private audio ABI.
The immediate gap is now a usable controller and verified operating state.

Progress was slowed by building native ownership infrastructure before proving
normal stock operations, and by treating one proposed backend's unknowns as
global obstacles. The local lifecycle tests are useful foundations, but their
count was not evidence of new physical capabilities. Future milestones should
finish an operator-visible behavior, with verification inside that delivery.

The current shell/USB tool is an investigation instrument: process launch,
identity hashing and legacy ADB sessions surround every action. It cannot be
the smooth production control loop. An immediate down/up did not visibly move
the head;150ms made a click; a sustained5s hold did move it. Repeated network
"tap" requests would keep resetting the stock key-hold behavior. Preserve the
hold on the camera instead.

## One integrated deliverable

Deliver one desktop console and matching Python/agent interface controlling
Cammy over its home Wi-Fi, with USB supplying power only. Bundle the TF runtime
backend, host/SDK integration, UI feedback and hardware acceptance together.
The visible controls are pan hold/release, zoom, native turnaround, viewer
selection/play/pause and a clear Stop. Keep existing HTTP/JSON authentication
and ownership; no new ROS dependency, control framework or flashed OS is needed
for this deliverable.

Put the input driver beside the existing root portal in the TF runtime. Open
the validated input nodes directly and use the observed codes; avoid shell
commands, temporary packet files and USB per action. The vendor app continues
to own motor/media hardware. Keep its speed and limits until measurements
justify another interface. Do not convert hold duration into claimed angles:
the reported350° range and asymmetric forward reference are estimates.

## Smooth motion and release ownership

The same console should expose [finite listening and push-to-talk](PUSH-TO-TALK.md):
idle audio off, hold/release followed by a conversation tail, agent wake and mute.
Host policy is implemented locally; red record mapping passed on2026-11-15
(event3/code28, bank shared with power116). Suppressing its native recording
action remains a physical integration gate. A TF update
is justified when the camera-local control backend is concrete and locally verified.

Use start/renew/release for one active direction. Write key-down once, let the
normal repeat behavior run, and write key-up on release or direction change.
Use a short motion deadline independent of the longer agent control lease.
An initial design target is renewal around10Hz with expiry around300ms;
measure command round-trip, visible movement onset and actual mid-travel stop
separately before accepting those settings. These are targets, not benchmarks.

A small independent input guardian should own the key and its expiry. Portal
Stop, lease loss, host disconnect and parent-process exit must release it. The
guardian receives only typed commands from the authenticated portal, checks
boot/owner generation and sequence, and rejects stale commands after Stop.
This is a concrete requirement of the demonstrated held-key mechanism, not a
reason to build another generic robotics framework. The current USB tool's
independent timed release is not an integrated `/v1/stop` implementation.

Test release while moving in the middle of the range. The successful left test
reached the reported left endpoint, so stationary-at-the-end does not establish
mid-travel stopping latency. Local physical controls should take priority;
unexpected physical/touch activity invalidates the agent's UI assumptions.

## State feedback makes the controls usable

The console needs verified live/viewer/grid state and current input state.
Center play/pause is code7; turnaround is a different code3. Do not infer an
action's meaning merely from its button label or the last command sent.
Add read-only native status or an on-demand LCD observation hook, and verify
what display layers it actually captures. Framebuffer access alone does not
prove that a sensor preview is included. Label UI images separately from camera
frames. Resynchronize after manual controls, reconnect or reboot.

The turnaround is reportedly a forward/face-user preset toggle, not a relative
180° offset. Expose the observed toggle first. A reliable "face forward" action
requires knowing the active preset or another verified native state source.
Do not report calibrated pose or completed motion without evidence.

## The media question to settle early

Our speaker success plays an existing video through the native viewer. It does
not establish arbitrary TTS, live audio, or simultaneous sensing and playback.
Early in the next hardware session, measure whether camera/microphone capture
continues while file playback runs. If native playback suspends sensing, it is
a notification/turn-taking route; conversational duplex still needs a separate
audio backend. That would be a measured reason to revisit native audio work.
Likewise, snapshots/finite clips do not establish a low-latency home-LAN stream.
The combined15Mb/s budget remains a measured acceptance target, not an installed
traffic limiter. Do not build conversation polish on an assumed media route.

## Acceptance for the jump

- With PC USB data unplugged, the console connects over home Wi-Fi and controls
  a sustained, visibly smooth pan without launching a shell for each update.
- Release, Stop, lost renewals and controller failure end a mid-range movement;
  fresh ownership cannot resurrect a stale command.
- Zoom and native orientation controls have observed effects; UI transitions
  and play/pause operate on the intended screen and return to live view.
- The console/SDK distinguish accepted input, observed UI state, physical
  motion and unavailable feedback. Reconnect and manual intervention recover.
- A concurrency measurement states exactly which camera/mic/playback functions
  coexist, and the result determines the next media implementation.

Release this as one usable control milestone. Full arbitrary-angle positioning,
screen-face rendering and continuous conversation remain separate outcomes;
they should not be prerequisites for proving this console.
