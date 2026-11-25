# Automatic Wi-Fi media and agent-visible scene — 2026-11-15

**Passed:** normal TF boot of CammyOS0.5.0, automatic home-network/media startup,
then fresh live video and simultaneous microphone PCM through the resident PC
gateway. Adam confirmed normal live view and no Wi-Fi controls touched.

## Boot and transport evidence

Boot `34d8cbba-f236-4569-ac9c-002024548f65` loaded the verified0.5.0 TF payload.
The initializer reported network `HOME_STATION`, independent recovery `restored`,
and `sdp-ready-idle` by observed uptime63.70s. No live stock DHCP helper remained.
The stock app stayed PID778/start164 before and after the media trial; stock
app/init/libc hashes matched. No live RAM correction or native-app restart was
needed. USB carried metadata/root checks, not the video or microphone stream.

| Observation | Actual result |
| --- | --- |
| Video reception | 30.021s,145 decoded monitor frames,144 changes |
| Monitor sample cadence | 4.83fps after the receiver's5fps sampling filter; not a source-FPS measurement |
| First decoded video | 1.536s after receiver start; not measured glass-to-glass latency |
| Concurrent microphone | 10s,320,000B mono16kHz S16LE PCM,159,786 nonzero samples |
| First PCM | 0.366s after audio request;1.997s after trial start |
| Interface traffic | 2.330Mb/s combined average over30.105s, includes other traffic; no hard cap |
| Release | Owner null, preview inactive, capture child gone, cleanup not pending |
| Native microphone | Driver capture `closed` observed after release |

The initial absence of port8554 was not sampled before initialization in this
boot. The acceptance is observed automatic startup without the Wi-Fi UI plus
fresh media afterward; it is not a controlled experiment proving every native
initialization dependency. Only one0.5.0 boot and this finite stream are accepted;
charger-only repetition, reconnect/soak and end-to-end latency remain open.

The ordered P15 cue was played on its verified PC sink, not Cammy's speaker.
Three tonal peaks occurred in order, but the existing strict cue detector returned
false: the1100Hz peak did not exceed twice the elevated baseline RMS. Nonzero
microphone transport passed; this run does not establish new acoustic-cue,
transcription-quality, speaker or duplex acceptance. Earlier intelligibility
and acoustic proofs are documented separately in WIFI-AV and RELEASE-0.4.1.

## What the surprise vision test exposed

The30s transport test decoded frames and retained counters plus microphone audio;
it did not save a movie or retain those images for a vision agent. Therefore the
agent could not retrospectively describe that exact interval from the counters.

A separate owned live session then saved two fresh960px-wide PNG frames from the
same RTSP source. Inspection showed YouTube playing “Suleiman Gets Angry at
Everyone | Magnificent Century”: a palace courtyard, white statue and people by
an arched doorway. This demonstrates inspecting a real Cammy frame, not only
receiving packets. The second session also released cleanly with capture closed.
It does not claim the earlier30s sequence had identical content throughout.

The integration gap is now clear: the resident host must decode once and expose
fresh timestamped frames to the agent and control surface, rather than discard
pixels in a transport test and require a separate capture for every question.
A short bounded RAM history can support recent-event questions only while an
observation session is enabled; stale or unavailable frames must be explicit.

## Exact private evidence

- `private/video-bootstrap/cold-boot-monitor.json`
- `private/video-bootstrap/cold-startup.json`, `cold-before.json`
- `private/video-bootstrap/cold-acceptance/result.json`, `after.json`
- `private/video-bootstrap/surprise-view/result.json`, `frame-01.png`, `frame-02.png`

Raw frames, audio and root snapshots remain private and outside git.
