# Targeted functionality investigation — 2026-11-14

The camera ran the TF-backed CammyOS0.2 runtime on home Wi-Fi, with USB data
connected. Adam authorized sensing/control through CammyOS hooks, excluding
direct VRAM edits. Escalation consisted of read-only metadata, bounded hook
sessions, and local analysis of the saved vendor executable. No code was uploaded,
firmware flashed, mixer altered, framebuffer/VRAM written or motor bus commanded.
Private evidence is under `private/targeted-probe/`.

## 1. USB menu versus normal operation

Adam confirmed the camera was on its USB mode-selection screen. At that point:
all observed PCM paths were closed; RTSP8554 was absent; the portal, HTTP8082 and
app-event9999 services were listening. After Adam selected Charging Mode, normal
live camera view returned and card0 playback opened as16kHz mono S16_LE,
period960/buffer7680. Capture stayed closed. The native RTSP listener remained
absent during a bounded6.13second preview-hook readiness test.

The menu suppresses parts of the native media pipeline. It does not fully explain
home RTSP failure: selecting Charging Mode restored native playback but did not
establish network preview. Normal camera view is a prerequisite for subsequent
media tests; do not mistake an accessible control portal for ready media.

## 2. Missing native RTSP lifecycle step

The local stock `/usr/bin/sdvcam` and the actual camera executable have matching
SHA256 `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`.
Address-based analysis therefore applies to this exact binary, rather than merely
a related SDK. Disassembler labels inherited from nearby exported symbols are
not reliable names for stripped functions; the addresses and strings below are
the evidence.

The presenter handler around0xAC45C logs `MSG_SOFTAP_ENABLE`. Its branch calls the
RTSP creation routine at0xADB90 (call0xAC504), another initialization routine at
0xAD90C (call0xAC50C), then stream-sender creation at0xAE210 (call0xAC520).
It also checks that preview/work-mode objects exist. The server creation code
requests the `wlan0` address and has a0.0.0.0 fallback. A separate recreate caller
also exists; this is not proof that the soft-AP event is the only possible entry.

The current CammyOS preview hook only opens the native9999 event session and sends
`{"cmd_id":"4016"}`. The phone app has the same payload. Neither establishes that
the presenter has performed the server-creation branch. The managed Wi-Fi runtime
configures the radio independently of the native soft-AP UI lifecycle.

**Inference:** a missing native media initialization/event step is now the strongest
explanation for the absent listener. This is better supported than a codec or
URL-only problem, but the inference has not been proved by safely triggering that
initialization. No Wi-Fi-mode toggle, native event injection or process patch was
used as a workaround. The preview API's success currently proves the event
connection, not a ready RTSP service. Final hook state was owner null/preview off.

## 3. Audio constraints and routing

A read-only `amixer -c 0 contents` query showed `MIC1 Switch=off`, MIC1 gain30
(on a0..31 scale), ADC1/ADC2 volume160 and DAC volume160. These are current values,
not quality recommendations or proof that MIC1 is the only input path.

A query with the stock `tinypcminfo -D 0 -d 0` utility timed out after8seconds.
No surviving tinypcminfo process was found afterward, and subsequent USB reads
worked. The cause is unlocalized; no supported-rate/channel range was obtained.
Do not repeatedly run this query while the stock app owns playback. No PCM samples
were captured and no test audio was played in this investigation.

A future capture hook should own mixer-routing changes, restore them on stop,
report actual format and acquisition timestamps, and cooperate with stock device
ownership. Opening capture alone while MIC1 is off is not an intelligibility test.
A speech hook needs explicit playback ownership, bounded volume and cancellation;
this investigation did not establish a usable independent playback path.

## 4. Motor paths — recovered structure, no motion

Two distinct code paths are present and must not be conflated:

- `MotorUartManager`, source string `source/device_model/motor_uart.cpp`, uses
  `/dev/ttyS3`; a configuration log names1152008N1. The send routine at0x7E0A0
  writes13bytes at0x7E228. Inspection shows header0xAA, a control byte formed from
  an operation plus0x10/0x20 selected by a flag, four big-endian16-bit coordinate
  fields, a speed byte, an8-bit additive two's-complement checksum over the ten
  bytes after the header, and trailer0xBB. Code checks speed1..5 and coordinate
  upper bounds480/270 for alternating fields. These are protocol observations,
  **not physical pan/tilt limits or verified units**. Operation IDs, signs, stop
  semantics, feedback and the relationship to tracking/ROI remain unresolved.
- `MotorCtrl`, source string `source/device_model/motor_ctrl.cpp`, has angle/reset
  and speed-related routines and opens `/dev/i2c-3`. Constructor code at0x9F1C4
  uses ioctl0x0703 with address0x50. This is a static code observation, not an I2C
  scan or identification of the installed controller. Nearby code transforms an
  internal angle value and maps speed selections; its values are not safe
  physical calibration limits. Related access also appears in a GUI routine.

No UART bytes were sent or consumed, no I2C scan/register operation was performed,
and no calibration/update command was issued. Future movement hooks must resolve
which path controls which physical function, arbitrate with sdvcam, establish a
bounded stop path and distinguish commanded pose from real feedback.

## 5. Native IPC and display

Read-only socket/proc inspection found `/tmp/event/unix.domain` owned by
`/proc/778/fd/5` (sdvcam). The saved `udev_handler` binary also names this socket.
Local receiver analysis near0x9A5A0 shows an accepted stream connection, reads up
to128bytes, and forwards the first byte as an event value. There were no System V
message queues in the observed inventory. This looks like a hotplug/udev channel,
not an established arbitrary presenter-message API. No messages were injected.

Stock sdvcam owns `/dev/fb0` and `/dev/disp`. A future face hook needs an explicit
rendering/ownership contract with that UI. Readable framebuffer metadata does not
provide a safe face API, and direct pixel/VRAM edits remain excluded.

## 6. API boundary and next work

After checking all four capabilities were unavailable and confirming body0.2.0,
an authenticated control lease was used to probe the installed `/v1/look`,
`/v1/face`, `/v1/speak`, and `/v1/audio/capture` hooks with empty request objects.
Each returned501 (`hardware adapter not implemented in portal v0.2`). The lease
was released; preview remained off. These are implementation gaps, not successful
hardware operations.

Next local/runtime work, in order:

1. Make preview readiness explicit, distinguish native event-session state from
   RTSP service readiness, and investigate a narrow supported native media-start
   bridge that preserves managed home Wi-Fi. Test proposed IPC/commands against a
   local model before packaging any new camera-side hook.
2. Build a bounded capture hook with mixer restoration and measured formats; use
   the TF release workflow for deployment rather than ad-hoc shell capture.
3. Resolve motor bus/function mapping and operation/stop semantics before enabling
   any real look command. Keep simulated coordinates separate from hardware units.
4. Design face rendering around stock display ownership, then add a hook instead
   of direct VRAM edits. Playback/duplex work follows a verified ownership path.

No new camera-side adapter was installed. The next hardware-control experiment
requires a reviewed, locally tested hook and a versioned TF update; guessing
native messages or motor packets would exceed what the evidence supports.
