# Combined media budget — 15 Mb/s

Adam's2026-11-14 target is **15,000,000bits/s combined in both directions**
between Cammy and its PC: video, microphone uplink, speaker downlink, face
commands, control and protocol overhead. This is1.875MB/s. It is an engineering
target; no shared traffic limiter or continuous duplex pipeline is installed yet.

The2026-11-15 [real live preview](LIVE-AV-2026-11-15.md) is640×360 H.264,
with about1.023Mb/s video RTP payload measured over8.035s in one scene. This
excludes protocol overhead and other streams; it is not a worst-case total.
Microphone PCM currently travels over USB in the combined prototype. Recording
file profiles/rates do not determine this distinct live-preview profile.

Adam subsequently accepted720p/30 Low as the working preset. Native resolution5
and quality2 were applied and verified after the earlier PCCAM/Charging-mode cycles.
A subsequent user recording was4K, so current recording-preset persistence needs
reconfirmation. The earlier
temporary test/restoration below remains a historical measurement. Native
microphone tone capture now works; useful USB duplex and speaker output remain
unproven. See [audio follow-up](AUDIO-TESTS-2026-11-14.md).

## Measured native choices

The camera's live language/menu resources expose4K/30,2.7K/30,1080p/60,
1080p/30,720p/60 and720p/30, with High/Medium/Low quality. Its settings-key
resource maps resolution to index0 and quality to index7 in command2002.
The phone-app menu setter is the native HTTP POST route
`/api/setdeviceinfo/?custom=1&mode=0&item=<menu-key>&par=<index>`.
Only the two evidenced keys are allowed in the trial tool.

| Native profile | H.264 stream rate | Complete-file rate | AAC rate |
| --- | ---: | ---: | ---: |
|4K/30 High, original |32.90Mb/s |36.04Mb/s |43.22kb/s |
|720p/30 Low, temporary test |5.34Mb/s |8.40Mb/s |42.39kb/s |

Both clips decoded completely. Lower settings were read back before recording
and restored afterwards. The complete-file rate is file size×8/video duration,
including audio and native file overhead. Short-clip overhead matters. These
are measurements of one scene/short clip each, not wire throughput, worst-case
motion, transport bursts or a constant-bitrate guarantee. See [hardware report](LIVE-VISION-2026-11-14.md).

The stock menu couples resolution and FPS; the lowest evidenced video preset
is720p/30. Separate15FPS and explicit bitrate controls have not been established.
Old generic SDK Lua files show FPS/bitrate fields but do not prove that this
M105 application consumes them. Do not edit those files on that assumption.
Analyzing only1–5frames/s on the PC can save inference cost, but dropping frames
after reception does not reduce camera network traffic.

Each native MP4 reserves its first1,048,576bytes for `ftyp`, `moov` and mostly
empty `free` padding. That fixed padding explains much of the short-file rate
above the encoded-stream rate. A read-only HTTP Range test returned206 and
exactly the requested8bytes from `mdat`. A future file transport can skip
padding with range reads and reconstruct/remux locally. This optimization is
not implemented; the budget conservatively uses the complete-file measurement.
Simply stripping bytes after downloading them would save no Wi-Fi bandwidth.

## Proposed allocation

| Component | Reserved Mb/s | Basis |
| --- | ---: | --- |
| Camera video / native file transport |9.00 | Slightly above measured720p Low complete-file rate |
| Microphone → PC |0.35 |16kHz×16bits mono PCM is0.256Mb/s; reserve packaging margin |
| PC → speaker |0.35 | Same conservative PCM allowance |
| Face commands |0.05 | Small expressions/gaze/blink commands; render animation on Cammy |
| Control / telemetry |0.10 | Bounded small messages |
| Transport, retransmissions and variation |2.00 | Provisional allowance, must measure on Wi-Fi |
| **Allocated total** |**11.85** | |
| **Unallocated headroom** |**3.15** | To the15Mb/s ceiling |

The video/file reservation already includes a recorded AAC track in the measured
test; the separate microphone reserve is conservative for a future independent
audio path. Audio codecs could reduce these allowances, but the budget does not
depend on adding another codec. Actual audio packetization/format is unresolved.

## Face and audio architecture

Render the face **locally** at the camera's native356×400 display using an owned
CammyOS hook. Send expression parameters, not complete framebuffer images.
Raw32bit356×400 at10FPS would require45.57Mb/s before overhead; at30FPS it would
require136.70Mb/s. A local renderer separates animation smoothness from Wi-Fi
traffic. The renderer and its display-ownership backend are still unimplemented;
this budget does not authorize raw framebuffer writes.

Network bandwidth is unlikely to be the main audio blocker at this profile.
Two mono16kHz/16bit PCM streams need only0.512Mb/s of raw payload together.
The remaining question is safe simultaneous access to capture/playback, routing,
output audibility, buffering and acoustic echo cancellation. The native clip
proves decoded AAC capture, not two-way speech. No simultaneous speaker test
has passed.

Allwinner's [V853 documentation](https://docs.aw-ol.com/v853/en/#audio) lists
one DAC and two ADCs, making concurrent input/output a plausible hardware route.
That is an inference about feasibility, not proof of the M105 board, current
driver configuration, stock ownership or clean full-duplex speech.

## Acceptance before calling the cap implemented

1. Apply a reviewed low-bandwidth profile and measure motion/speech, sustained
   traffic and bursts. Current settings are restored to their original values.
2. Establish the actual streaming source; a file fetched after recording ends
   is not live video. Keep encoder output inside its allocation, or choose a
   genuinely supported lower-rate path.
3. Pace file transfers and share one aggregate limiter across both directions.
   A smaller stored bitrate does not prevent HTTP downloads from bursting above
   15Mb/s. A hard wire ceiling needs transport/network enforcement, not just
   a budget table. No system-wide traffic shaping was changed in this work.
4. Give audio and Stop/control priority. Drop outdated video rather than letting
   audio/control queue behind it. Do not silently buffer minutes of media.
5. Test microphone and speaker concurrently, measure latency and echo, and
   preserve Stop/lease-loss behavior before adding conversation.
6. Verify the local face renderer and repeat the combined test with animation,
   both audio directions and video, using measured total bytes both ways.

The initial conclusion is **720p/30 Low has a measured path into this budget**.
Continuous transport, a hard cap and simultaneous two-way audio remain work.
