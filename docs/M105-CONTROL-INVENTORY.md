# M105 controls and push-to-talk (2026-11-15)

The0.6 candidate preserves the camera's existing motor-speed setting. Bounded
button-hold time is a release deadline, not a hard-coded angular speed. Adam
reports a speed menu, successful DOWN-button face tracking, and a faster left/right
startup sweep. These are stock capabilities, not yet verified CammyOS RPCs.

The saved family manual (`sixmou-manual-8.png`, page6) explicitly says DOWN starts
face tracking and describes tilt as manual. A sibling manual crop instead calls
DOWN "clear screen"; model/UI context matters. The saved stock executable contains
`ml_device_motor_speed`, `MotorCtrl::SetSpeed`, motor calibration, mirror and face
tracking paths. Strings confirm implementation leads, not safe command encoding
or permissible speed values. Saved `data/setting.lua` contains motor speed0
(line221), face follow0 and aspect-ratio face follow0 (lines195–196). These are
saved defaults, not current camera readback or validated enum meanings. The UART routine's1..5 validation does not establish
the menu's units, full control coverage or current speed. No motor bus was opened.

Next targeted hardware observation: read current speed and available menu options;
map a physical DOWN press/release; determine tracking ON/OFF feedback and disable
semantics; observe speed persistence and startup sweep without forcing travel.
Add tracking as an exclusive motion mode only once disabling it is verified.
A synthesized key release or lease expiry cannot promise to stop native autonomous
tracking. The candidate does not add a blind DOWN toggle, raw motor packets or an
automatic speed change. Existing input commands also depend on live-view context.

The machine-readable inventory is [m105.json](../assets/controls/m105.json). It
separates user reports, saved manual/static evidence and remote verification.

## Push-to-talk now

The desktop Reachy app has a finite5..90second conversation session (UI60seconds).
It first prepares live A/V explicitly. Hold Talk for up to8seconds, release to
transcribe locally, then an attached agent consumes the turn and submits a reply.
Speech output is the P15 desktop. A new press can interrupt a prior reply after
its worker has stopped. Stop/session expiry drops the turn and releases media.

During an active conversation the camera microphone streams into a bounded RAM
ring between presses. Only held speech is sent for local transcription. This is
turn selection, not physical microphone-off between turns. Idle outside a session
requests no capture. Physical red-record-button PTT, VAD, automatic short follow-up
windows and Cammy-speaker arbitrary replies remain unfinished. The record button
still has its stock recording function; no remapping is installed.

## Voice preference

Adam requests ChatGPT Maple as the default app voice, independently of CammyOS.
No connected setting-control tool can change that preference here. The requested
preference is recorded, not falsely marked activated. Cammy currently uses its
labelled local speech provider; Maple is absent from the published Speech API
built-in voice list. No substitute is selected or renamed Maple, and Pikachu
voice experiments remain paused.

Sources checked2026-11-15: [ChatGPT Voice](https://learn.chatgpt.com/docs/features/voice)
and [Speech API voice options](https://developers.openai.com/api/docs/guides/text-to-speech#voice-options).
