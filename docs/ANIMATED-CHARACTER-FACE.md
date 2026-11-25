# Character face and physical LCD trial — 2026-11-15

The revised Pikachu skin is running in the official Reachy app. Its idle loop
cycles neutral, happy, angry, sad, doubt and sleepy every four seconds, with
actual ParamFace blinking/gaze, forehead sparks for angry and Zzz for sleepy.
The mouth stays quiet in idle. Manual face controls cancel the loop; conversation
activity cannot silently be replaced by it. Voice work remains paused.

## Renderer and artwork

`tools/build_character_face.py` authors the approximately32KB schema2 skin in
`assets/faces/pikachu.json` using manually drawn paths and palette sprites.
References were the [official character image](https://www.pokemon.com/us/pokedex/pikachu)
and [animation model sheets](https://characterdesignreferences.com/art-of-animation-1/pokemon).
Cheeks now sit inside the head contour. Ears are static; expression-specific
mouth/eye sprites and the renderer's existing motion supply animation.

The new full-panel body overlay exceeds upstream's80×60 overlay limit. The
pinned upstream checkout is untouched; `tools/paramface_source.py` generates a
Cammy core copy with exactly two overlay bounds extended to356×400. Existing
48×48 dynamic-sprite bounds and JSON admission limits remain. The six upstream
presets match the unmodified core; the custom overlay requires the Cammy build.
This is an explicit renderer extension, not universal upstream editor parity.

The design canvas matches the356×400 panel. Integer positions and binary-exact
sprite scales avoid host/ARM rounding differences. Expression numeric fields
are additive deltas; sprite-frame geometry remains absolute. Tests cover cheek
margins, visible features/effects in all moods and exact host/ARM pixels.

`tools/render_character_preview.py --idle` exports a24-second20fps silent loop;
the original18-second reel separately demonstrates scripted mouth/gaze. Both
use the actual renderer. Neither proves audio lip sync or physical LCD refresh.
Ignored files: `build/character-face/pikachu-idle-loop.mp4`, individual PNGs,
`expression-sheet.png`, and timing/hash reports.

## Concurrent real Wi-Fi media

A bounded Reachy-app session delivered148 fresh video frames and448,000 mono
16kHz samples while the host loop cycled all six expressions. Of these,446,634
samples were nonzero. Expiry reached idle with owner release acknowledged,
decoder reaped, microphone stream closed and worker threads joined. A separate
USB read found ALSA capture CLOSED. This was host face plus real camera sensing;
it did not show a face on Cammy's LCD or play audio through its speaker.

The warm-start camera had **two native LISTEN sockets at<camera-ip>:8554**,
both belonging to the stock app. Read-only DESCRIBE checks alternated200/404.
The exact native lifecycle cause remains unresolved. `host/cammy_live.py` now
waits for portal preview initialization and allows at most four sequential
FFmpeg starts within five seconds, only for exit8 with an exact pre-frame
DESCRIBE404. Each owned decoder is reaped before retry; no native command or
lease is replayed. Microphone streaming starts after the first decoded frame.
The successful real test used two attempts. Other errors are not retried.

Local verification:30 character/ParamFace/app tests and14 media lifecycle tests
passed. The native GUI and launcher tests separately passed21 tests, plus two
new boot/hash-bound media-script tests. Results are local/synthetic unless
explicitly described as the real Wi-Fi session above.

## Physical LCD proof and integrated release

The one-shot GUI profile booted and displayed the compiled happy face for about
ten seconds. P15 captured it and its removal; at least8.873s overlapped live
video/microphone advancement. The complete session had148frames/448,000samples,
then owner null/preview off/ALSA CLOSED. The first earlier host-loop session
above and this later physical session are separate runs with equal total counts.
Colour/eyes/mouth fidelity remains unverified because the P15 overexposed the
LCD and Adam missed the display. Native animation/live face commands are open.

Original startup was restored, but subsequent boot IDs changed without another
agent reboot request. Current stock boot is stable with automatic runtime
guarded off; cause remains unresolved. See [evidence/recovery](FACE-BOOT-TRIAL-2026-11-15.md).

Adam requested consolidation into [CammyOS0.6.0-rc1](RELEASE-0.6.0-CANDIDATE.md):
one versioned TF/native/desktop release with repeatable boot/recovery, reusable
GUI expressions/replay/idle loop and session-based media/control. Stop treating
the one-shot demo as a production workflow. Build/test locally while leaving the
current stable camera untouched. Voice work stays paused.
