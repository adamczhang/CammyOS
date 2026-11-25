# PCCAM USB probe and Android preview trace — 2026-11-14

Status: observed metadata plus a bounded host-hook audio trial. This is not working
USB video, usable microphone audio, or a new camera runtime deployment. Camera
firmware/code, mixer values, drivers and raw USB negotiation were not modified by
our probes. Adam selected PCCAM and later returned to Charging Mode/live view.
Private evidence: `private/pccam-probe/`.

## Exact USB device and advertised media

PCCAM enumerated on physical port1-4 as18d1:100d, serial20080411. Identity and
sysfs ancestry distinguish it from the PC's separate Poly Studio P15; the existing
video0/video1 nodes belonged to that other camera and were not opened.

Descriptors advertise:

- UVC1.00, one MJPEG format/frame at1280×720, interval333333 in100ns units
  (approximately30fps), bulk video endpoint0x81 and status endpoint0x85.
- USB Audio playback and capture, each mono S16_LE,16000Hz; host OUT endpoint0x02
  and IN endpoint0x83. Linux bound snd-usb-audio and assigned ALSA card6 for this
  connection. Card number is dynamic and must not be hard-coded.
- The video interfaces did not retain a uvcvideo binding and no M105 video node
  appeared. The descriptor set did not include an ADB interface in PCCAM mode.

The kernel log records a GET_INFO timeout on unit1/control2, then missing
GET_DEF(PROBE) with an automatic workaround, followed by GET_CUR(PROBE) timeout
(expected26bytes) and initialization failure(-5). Thus the host video driver is
present, but its standard control negotiation fails. No missing-driver diagnosis
or raw-USB workaround has been established. No alternate/default camera was used.

## Bounded USB audio trial through a CammyOS host hook

`host/usb_media_probe.py` adds an experimental PC-side hook, not a body audio
adapter. Default invocation only identifies metadata. Explicit capture mode:

1. Matches VID/PID, exact serial and sysfs USB ancestry to a single ALSA card.
2. Checks advertised capture format and authenticates to the real CammyOS body.
3. Acquires a short body control lease, revalidates USB identity, and captures
   only from that card using standard ALSA with no default-device fallback.
4. Watches body lease ownership, USB identity and a capture deadline. A changed
   owner/stop, connection failure or device change aborts the capture process.
   Cleanup terminates/reaps the probe and releases its lease.
5. Saves a new private PC WAV with mode0600. No on-camera recording is requested.

The actual3second trial produced48000 samples, mono16bit/16kHz, **all zero**
(peak0,RMS0). This proves a host capture transaction can complete; it does not
prove actual microphone samples reached the USB endpoint or intelligible audio.
No speaker playback was attempted. Routing, gadget bridging and possible silence
padding remain hypotheses to investigate; no mixer was altered to chase them.

Six host-only tests cover identity/device exclusion, no default fallback, lease
loss, disconnect/deadline, process termination/reaping and release on failure.
The complete native/host suite passed43tests. Capture cancellation is supervised
with bounded HTTP timeouts and polling, not a hard real-time hardware guarantee.

Example commands while the verified M105 is actually in PCCAM mode:

```sh
python3 host/usb_media_probe.py
python3 host/usb_media_probe.py --capture-audio --seconds 3 --output private/pccam-probe/new-sample.wav
```

Capture requires a reachable body portal and exclusive control lease; it refuses
another/default camera or microphone. Do not treat this development CLI as the
future fully integrated desktop probe-job API. The body still truthfully reports
its own audio adapter unavailable.

## Android app is a separate, valuable control source

The saved HDV CAM QZ code is being traced beyond isolated command strings:

- `p069n3/I.java:m` constructs `rtsp://<QZ-host>:8554/ch00`.
- `I.java:y` starts the event client; `C0486f.java:a` connects specifically to
  **192.168.10.1:9999**, obtains input/output streams and keeps reading events.
  That implementation is AP-specific even when another HTTP host can be stored.
- `I.java:C` sends `{"cmd_id":"4016"}` through the retained event output stream.
  `C0485e.java` performs the write, not an HTTP preview request.
- The recovered instruction dump for `p127z3/C0643u.java:f` shows a listener init
  delay, event-client startup, then a loop that calls `C` with a30000ms delay in
  the non-G branch (the QZ backend is I). Another protocol branch uses5000ms.
  This is static decompiler evidence; live packet timing has not been captured.

The current CammyOS hook sends4016 once and keeps the socket. Repeating the app's
keepalive is a concrete compatibility item for a future long-lived preview hook.
It has not been live-tested and does not by itself explain an RTSP listener that
never starts. The native media-bootstrap hypothesis remains independently open.

The full application connection/mode-selection sequence is **not yet completely
mapped**. Many decompiled methods have unreliable names and mixed protocol-family
branches. Some setdeviceinfo paths alter settings or perform reset-like actions;
do not replay them as a generic startup script. Likewise the bundled Allwinner/
TUTK library is not proof that QZ live mode uses TUTK or supports two-way audio.
Next work should map callers, query versus mutation, message ordering and native
callbacks, then implement only understood operations as CammyOS hooks.

## Restoration and remaining limits

Adam returned the camera to Charging Mode and normal live view. A subsequent
read-only USB check found ADB and a recent kernel boot; the home portal returned
on0.2.0 with HOME_STATION, owner null and preview off. The initial restoration
snapshot was still within the startup-latch clearance window. Do not claim an
in-place mode round trip or immediate reconnect when the observed sequence also
included a fresh boot. A final settled diagnostic is recorded privately.

Mass-storage mode remains pending. USB audio/video descriptors and one silent
capture do not complete webcam acceptance, and neither substitutes for the
Wi-Fi desk-companion goal. No raw USB driver detach/rebind, protocol override,
VM/VRAM edit or camera executable upload was performed.
