# ParamFace and the official Reachy app — 2026-11-15

Cammy now has an **installed, running app in the actual Reachy Mini app manager**,
with a real ParamFace preview/editor and finite real-camera Wi-Fi A/V sessions.
It is an app extension to the official reference daemon. A follow-up
[receive-only SDK/WebRTC media extension](REACHY-MEDIA-2026-11-15.md) now passes
actual camera trials; the full Reachy body daemon remains unfinished. The reference daemon remains a simulator; its poses
never command Cammy. A later finite native LCD face+A/V trial passed; reusable native animation
and stable automatic startup remain open.

## Latest character-loop trial

The revised [character skin and idle loop](../ANIMATED-CHARACTER-FACE.md) are live in
this app. The custom full-panel overlay requires the generated Cammy renderer;
the pinned upstream core and six original presets remain unchanged. A30second
real session delivered148 frames/448,000 microphone samples while all six host
moods cycled, then expired and cleaned up. The later physical LCD test showed a static happy face with8.873s confirmed
A/V overlap. Subsequent reboots block release acceptance. See the
[boot-bound trial](../FACE-BOOT-TRIAL-2026-11-15.md).

## What is proven

- Actual pinned ParamFace C++ core: six upstream presets plus a [local character skin](../ANIMATED-CHARACTER-FACE.md), six expressions, animation,
  driven mouth/gaze, schema1/2 JSON and upstream editor. Host and ARM output match.
- On the real M105 CPU,240 animation steps at356×400 completed in3.25s in one
  CPU-only run; final pixels matched host exactly. No LCD or native app was written.
  See [renderer evidence](../../native/paramface/README.md).
- `cammy_reachy_app` is discovered as `installed` by upstream Reachy Mini1.14.0,
  with `custom_app_url=http://127.0.0.1:8801`. The official app-manager start
  returned running; this is not a replacement console/daemon protocol.
- A20-second real session through that app produced98 video frames and312,000
  16kHz mono PCM samples (310,966 nonzero), while ParamFace PNGs/expressions were
  requested. Three agent-visible640×360 JPEGs carried boot/session/sequence and
  PC receipt timestamps. They showed this chat on the monitor.
- The same session expired to idle, released its owner, reaped its decoder and
  joined workers. Camera ALSA capture reported CLOSED. Boot, native PID/start,
  watchdog, stock/init/library hashes and file-backed mappings stayed unchanged.
  Allocator-history annotations in anonymous native mappings advanced; the RAM
  smoke test's strict all-maps equality is inappropriate for active native media.
- A first test received PCM but hit a video startup timeout. Applying the working
  receiver's1s FFmpeg probe/analyze limits resolved it. Decoder diagnostics are
  bounded to2KiB. This does not establish worst-case startup time or latency.

Exact private reports: `private/paramface/ram-render.json` and
`private/paramface/live-app/report.json`. Sensor media and tokens stay out of git.
A separate real trial stopped this app through upstream
`POST /api/apps/stop-current-app` while it had6 frames/22,400 microphone samples.
The app stopped, owner became null, preview stopped and ALSA capture was CLOSED;
stock identities were preserved. Restart through the upstream manager returned
an idle app. Evidence: `private/paramface/app-stop-report.json`.
No new acoustic intelligibility, camera speaker or duplex-audio proof is claimed.

## Use and reproduce

The existing official Reachy Control desktop and reference daemon are under
`LAB/Projects/Reachy-Mini-Control`. In its Apps list select `cammy_reachy_app`,
then open the app at **http://127.0.0.1:8801**. If discovery is cached, refresh Apps.
The daemon needs one host restart after a new editable install adds its import
path; this was performed on the idle reference daemon, without camera changes.

```sh
python3 tools/prepare_face_lab.py
<projects>/Reachy-Mini-Control/runtime/venv/bin/python \
  -m unittest tests.test_paramface tests.test_cammy_face_app tests.test_cammy_live
```

Preparation builds the real renderer/editor and installs only into the explicit
reference venv. It does not contact the camera or start sensing. It assumes the
pinned reference SDK environment is already installed. The app is an editable
workspace package and resolves sibling host/SDK/build/download data; it is not a
standalone distributable wheel yet.

The editor at `/editor/` was browser-checked: its real320×240 WebAssembly canvas
contained1,954 colored pixels, with no console/network failures. The root preview
also fetched and displayed live ParamFace PNGs without console errors. The editor its Wi-Fi target is
`127.0.0.1:8801`. Existing StackChan contract is preserved:

| Existing contract | Cammy app behavior |
| --- | --- |
| `GET /status` | Host-preview status and explicit unavailable physical features |
| `GET /face` | Current ParamFace JSON |
| `GET /face?expr=happy&hold=1` | Named preview expression; hold prevents30s neutral return |
| `POST /face` | Apply bounded JSON to host RAM |
| `POST /face?save=1` | Save0600 JSON under private PC data, never camera flash |

`sdk/paramface.py` is a standard-library client for that existing contract.
Mouth and eye-gaze controls drive only the preview. They do not imply calibrated
physical gaze. Automatic blinking/mouth animation can be restored separately.

The pinned upstream npm audit reports five high findings in **development/build
packages** (Vite, puppeteer-core and transitive dependencies). This app serves
built static assets only; it does not launch Vite or Puppeteer. No upstream source
or lockfile was silently upgraded. A dependency refresh should be reviewed and
renderer/editor equivalence rerun before development-server use.

## Verification profile

The full system-Python `make test` run passed605 tests, with8 optional app tests
skipped because that interpreter has no Reachy/FastAPI environment. The dedicated
`make test-face-lab` profile then passed all29 focused tests without skips, including
native desktop iframe policy, exact renderer/ARM/sanitizer comparisons and fake
body/real-pipe lifecycle checks. The final iframe-policy change was covered by
that focused run; the full suite was not unnecessarily repeated afterward.

## Media and lifecycle

`host/cammy_live.py` owns one short-lived unique body agent and one FFmpeg process.
The default is idle, with no sensing requested. Sessions are explicit5..90seconds;
the UI requests30seconds. Existing gateway8790 keeps its body token server-side.

The app uses the existing lease and preview endpoints, native RTSP video, and the
existing16kHz PCM HTTP stream. One decoder scales the received source to640×360 at
5fps and fans out its latest JPEG. This reduces host preview work, **not the camera
encoder's Wi-Fi bitrate**. The previously measured source averaged2.330Mb/s; this
app test did not remeasure or enforce the15Mb/s goal.

PCM is a five-second RAM ring (160kB); no automatic PC playback or TF recording is
requested. `/api/live/audio.pcm?after=N` provides bounded rolling PCM and gap
metadata for future ASR providers. It is not a complete continuous transcript or
PTT implementation. Current physical red-record-button remapping is still open.

Frames carry a real boot UUID from the named runtime probe, session ID, sequence,
and PC receipt UTC/age. They do not carry measured sensor-exposure timestamps.
Frames older than2s are refused. Stop/expiry/fault clears all exposed media and
invalidates in-flight workers; a new session gets new identity. Lost ownership or
sequence rewind halts rather than reclaiming another controller. Release is
owner-scoped; no global Stop is issued against another lease owner.

The dedicated Linux exec shim sets `PR_SET_PDEATHSIG` and checks its parent before
and after registration. Tests verify the decoder dies when its host is killed.
The camera lease bounds remaining sensing after host disappearance. New sessions
are refused while worker/decoder/stream cleanup is uncertain. An acknowledgment
always retains `hardware_cleanup_confirmed:false`; the separate camera status
observation above is the physical cleanup evidence for that particular trial.

Loopback Host/Origin/Sec-Fetch checks reject other browser origins. The official
Linux desktop embeds app pages in an iframe; only the inert root navigation can
arrive cross-site, and its CSP permits framing by self and the exact native Tauri
origins. Sensor APIs retain same-origin checks even for those native origins;
no cross-origin sensor credential is granted. Desktop cache prefetch is best-effort
upstream and may be denied; actual app subresources load inside their own origin. Media API
requests also require a random per-app-boot token and JSON mutation bodies.
The same-origin UI receives that token; it is not a camera credential. Raw face
JSON is limited to64KiB; control bodies to1KiB. No wildcard CORS is enabled.
These controls do not protect against another malicious process under the same
local user account.

## Next LCD integration: one native GUI owner

`tools/display_owner_audit.py` rechecks12 static claims against exact stock
`sdvcam` SHA256 `8771961f…62b0ec`. Nothing is patched/executed by the audit:

- GUI loop0xE582C saves WindowManager in r5. At0xE58B0 it selects the mutex at
  `this+0x24`;0xE58BC calls a wrapper that locks `pthread_mutex_lock`.
- Within that bracket,0xE58C0 calls periodic handler0x4A7950 and0xE591C invokes
  window message handlers.0xE5928 unlocks through the verified pthread wrapper;
  the loop then sleeps2000µs. This is a **candidate cooperative GUI-thread hook**,
  not a claim that every other LVGL access is serialized by that mutex.
- Exported `sunxifb_flush`0x4B0910 copies rows and performs framebuffer ioctls,
  including `FBIOPAN_DISPLAY`0x4606. Native UI owns buffer selection/flush.
  A second process writing fb0 would bypass that owner.

The next meaningful device milestone is an owned face window/surface created,
updated and deleted on this GUI thread while RTSP/PCM continue. ParamFace can
already produce the pixels on that processor. Before a guarded native candidate:

1. Identify exact LVGL build/configuration, object/image descriptor ABI and
   window transition/lifetime. The binary's driver reads32-bit area coordinates;
   generic upstream LVGL headers must not be assumed ABI-compatible.
2. Prove a bounded GUI-thread handoff, pixel-buffer ownership and deletion/restore
   on Stop, lease loss, window changes and native exit. No bridge-thread LVGL calls.
3. Prepare/review a narrowly scoped candidate using the existing time32 loader
   profile and activation gates; the current status-only library cannot draw.
4. Run one guarded hardware trial with a recoverable launch and physical LCD
   observation, then concurrent A/V and Stop. Existing boot-substitution gates
   remain open; no stock app restart or firmware change was attempted here.

A fallback experiment using a face JPEG in the stock photo viewer could show a
static face, but would not prove a persistent robot face alongside live media.
It is not the chosen implementation path.
