# Stock controls and first remote input results — 2026-11-14

Adam verified two physical capabilities using the camera's normal controls:

- **Speaker:** the native media viewer contained nine files, four videos and
  five photos. Viewer items1/9 and2/9 played the recorded beep cues audibly from
  Cammy; items3/9 and4/9 had no beeps. This is operator-observed stock playback,
  not a remotely controlled CammyOS speaker endpoint or measured duplex audio.
- **Gimbal:** the body's left and right arrows move the camera head. Adam also
  identified normal zoom-in, zoom-out and180-degree-turn controls. Their remote
  operation, exact mappings, travel and stop behavior are not yet verified.

These supersede the earlier statement that no Cammy-speaker output had been
observed. Prior unsuccessful USB/ALSA waveform tests remain unsuccessful; they
do not contradict successful native file playback.

The PC verified the TF CammyOS runtime and root USB access before the manual
test. A new boot ID appeared during the interaction; Adam confirmed that he
power-cycled Cammy. No agent reboot, native app restart, firmware edit or motor
command was sent. The existing ADB device moved from host server5037 to5038;
neither server was restarted. Subsequent stock HTTP availability was not
established, so repeated stock HTTP queries were stopped.

Read-only USB inspection confirms that sdvcam opens `/dev/input/event0` through
`event4`. Devices0/1 are sunxi-gpadc keyboard inputs,3 is gpio-keys,4 is the
SPD2010 touch input. This provides a concrete normal-input control surface to
trace before implementing a private motor or media API. A bounded passive
capture on keyboard devices0/1/3 uses existing stock tools and separate reader
queues, with no grab, injected event, uploaded executable or firmware change.
Its results and exact runtime checkpoints are private in `private/stock-controls/`.

Passive captures now establish this mapping. Each listed action includes both
press (`EV_KEY=1`) and release (`EV_KEY=0`); holds also emit repeat (`2`).

| Physical action | Input node | Key code |
|---|---|---:|
| Left | `/dev/input/event0` (`sunxi-gpadc0`) |115|
| Right | `/dev/input/event0` |102|
| Zoom in | `/dev/input/event0` |5|
| Zoom out | `/dev/input/event0` |6|
|180° turn | `/dev/input/event1` (`sunxi-gpadc1`) |3|
| OK/center: video play/pause | `/dev/input/event0` |7|
| Side circle/play: viewer cycle | `/dev/input/event1` |139|

The first64-record trace filled during a hold and was incomplete. A larger
capture independently recorded both arrow transitions. A subsequent capture of
all five input nodes recorded zoom/turn transitions; touchscreen traffic was
also observed but no touch coordinates are being replayed. These are M105's
actual codes, not conventional Linux LEFT/RIGHT codes.

Adam identified a left-side circle/play-icon button cycling through live view,
full-screen mixed photo/video viewer and four-card viewer. A subsequent passive
trace matched three side presses to code139 and three center presses to code7
while he played a video through, replayed it with a pause, then returned live.
**Center7 and turnaround3 are distinct controls.** An earlier conversational
assumption that these were the same context-dependent button was incorrect.

[`tools/stock_button_trial.py`](../tools/stock_button_trial.py) now prepares one
operator-observed left/right tap under a CammyOS control lease using existing
USB root transport. [`host/cammy_buttons.py`](../host/cammy_buttons.py) encodes one
64-byte ARM32 batch containing down/sync/up/sync. It checks the boot, stock PID
start identity, input name and device number, hash-checks nonexecutable RAM data,
then uses stock dd for a complete data write. There is no automatic retry,
executable upload, native patch or firmware edit. A lost/partial reply is
uncertain and must be inspected before another command. It requires the operator
to establish live-view context and release the physical buttons first.

Check-only camera runs first validated both input nodes, packet hash and RAM
cleanup without injecting events. The subsequent operator-observed trials were:

| Trial | Result |
|---|---|
| Immediate left down/up batch | Input write acknowledged; Adam saw no movement. |
|150ms left press | Click heard; no clearly visible movement. |
|5000ms left hold | **Adam confirmed roughly90° left movement.** A separate evdev reader captured down/up5.0065s apart with95 repeat events. |
|150ms side viewer press | **Adam confirmed full-screen viewer opened.** |
|150ms center press on selected video1/9 | **Adam confirmed video played and beeps sounded from Cammy.** |
| Two further side presses | **Adam confirmed normal live view restored and head stationary.** |

Timed trials use an independent release process, plus a final redundant release
after key-down and timed release finish. Parent exit/signal cleanup also attempts
release. The target durations are an explicit whitelist:150ms or5000ms for
left/right; media controls permit only150ms. Process-wide SIGKILL or kernel
failure is outside this mechanism's guarantee. The normal five-second trial
wrote96 input bytes including the redundant release; kernel filtering left one
observed down/up pair and normal repeats. No next key-down is retried on error.

The first viewer trial exposed an overly strict preservation check: `/proc`
fd3 symlink inode/timestamp changed while its watchdog target, fd flags and
mount ID were unchanged. The button trial now ignores only that ephemeral
symlink metadata. Actual watchdog fdinfo, boot/PID/start identity, fixed-file
hashes, mount/selection state and executable mappings are still checked. Later
media transactions passed the corrected check. The last observed boot was
`af9ac50b-da8e-4686-adf8-a2f8abd68924`; control leases were released.

## Mechanical observations supplied by Adam

Total pan travel is approximately350°. Forward is the default orientation when
looking at the rear screen, approximately90° from the left endpoint. Relative
to that forward reference, a useful **estimated** range is−90° to+260°.
Adam reports about3s holding left to its endpoint, about8s for the described
right sweep, and approximately10s end-to-end. These are approximate manual
observations, not encoder readings, calibrated speed or a duration-to-angle API.
The five-second left test reached the reported left region; it does not measure
mid-travel stopping latency independently of the mechanical endpoint.

The turnaround button reportedly selects the back/face-user orientation and,
on the next click, returns forward, even after manual panning. Treat this as a
native preset toggle, **not** a relative180° command. `turn_180` is only the
legacy test-module button name; its remote action and active-preset query have
not been verified. Zoom controls likewise have captured mappings but no remote
effect test in this milestone.

## Scope and next work

The interim transport requires USB. CammyOS authenticates and owns the trial
lease, but the stock app interprets input and performs motion/playback. No new
camera code/firmware or root-device drivers were installed. This proves remote
button-driven gimbal movement and playback of an existing file. It does not
complete Wi-Fi-only actuation, arbitrary speech, streaming/duplex, calibrated
pose feedback or a mid-hold `/v1/stop` integration. Native UI context is supplied
by the operator; it is not automatically detected. Existing `/v1/look` and
`/v1/speak` remain501. Next is a TF-runtime button backend with lease/Stop-aware
release and explicit UI state handling, reusing these observed input controls.

Final local verification:512 repository tests passed in123.190s, including15
button tests. Shell fixtures exercise late key-down scheduling, a failed
key-down write and parent termination, verifying release/cleanup without a
second key-down. Hardware work is paused at Adam's request after the confirmed
restoration to live view. Nothing from this experiment starts automatically.
