# Persistent conversation and camera-side activation —2026-11-15

Adam wants an always-available desk companion with an explicit ongoing hands-free
conversation mode, activated on Cammy itself. He offers an OpenAI API key for
voice testing; stop optimizing this milestone around Claude credit eligibility.
Adam also requires finishing Cammy’s own speaker; P15 is a temporary test output, not completion of the robot conversation milestone. This is the next integration target, not an implemented hardware feature.

## Current truth

Real Cammy live microphone/video and P15 conversation audio have physical evidence.
The desktop conversation code still accepts only5..90second sessions (UI60s) and
held utterances up to8s, with an attached agent furnishing replies. It is not an
automatic persistent speech-to-speech backend. Generic engagement.py has a longer
local state-machine policy, but that is not a deployed live voice solution.

A custom static face was shown on Cammy's native LCD with concurrent A/V. The
reusable0.6 native face service is locally tested and packaged, not installed or
physically accepted. It currently renders a canvas with deletion handling; it has
no microphone/mode touch actions or physical conversation-button binding. Red
record retains its stock recording function. No physical button starts this mode
today. Prior reset/endurance gates are unresolved.

## Proposed integrated behavior

- PC companion service remains available and retains conversation context while
  Cammy is on Wi-Fi. Default Idle requests no capture; it is available, not
  automatically streaming to the cloud.
- In explicit Robot mode, map the red record button to Conversation on/off and
  suppress stock recording for that input. Hold-to-talk can be an alternative.
  Event mapping and suppression must be verified before advertising the control.
- Conversation stays live for repeated natural turns and interruptions until
  stopped, muted, timed out by a chosen policy or disconnected. Replace the demo
  duration cap in a dedicated mode; continue renewing short device leases while
  PC health is valid. Do not remove expiry/Stop protections globally.
- Custom LCD shows character plus Idle/Listening/Thinking/Speaking/Muted/Offline,
  explicit microphone state and a Stop/Mute control. Implement and verify actual
  input handling; drawing a face does not prove an interactive menu.
- OpenAI voice adapter receives Cammy PCM and returns speech to P15 first. Native
  Cammy arbitrary speech was physically confirmed by a guarded USB test; its
  production Wi-Fi API and conversation integration remain unavailable. Retain dialogue state across
  bounded provider connections; handle provider limits/reconnection explicitly.
- Verify echo handling with the actual Cammy microphone/P15 speaker path, so
  playback does not become a new user turn. Full-duplex model support alone does
  not establish acoustic echo cancellation for these separate devices.
- Device input -> PC session control and PC state -> native LCD use the existing
  ownership contract. No per-turn USB shell or firmware reboot.

GPT-Live documents full-duplex speech with an independently chosen backend agent:
https://developers.openai.com/api/docs/guides/live . Its session lifecycle supports
explicit microphone mute and closing/reopening voice while backend work continues:
https://developers.openai.com/api/docs/guides/live-conversations . This establishes
an API pathway, not Adam's actual model entitlement or a tested Cammy adapter.

## Next proof

Implement provider adapter and local lifecycle tests, then one bounded funded
OpenAI voice test with actual account/model access. Collect the API key through
secure input only when the test can consume it; keep it on PC, out of TF/git/logs.
Then integrate physical Robot-mode activation/custom state UI and verify repeated
hands-free turns, interruptions, Stop, disconnect and persistent context on the
planned camera candidate. No such test, account change or OS rebuild was performed
by this clarification. Keep the existing0.6 package immutable.

## Speaker prerequisite clarified

The private route now has physical proof: Adam heard the finite patterned cue
and Kokoro-generated “Hello world” from Cammy's own speaker. See
[SPEAKER-ROUTING-2026-11-15.md](../SPEAKER-ROUTING-2026-11-15.md).
Two blockers were found: a symbolic hw/asym alias broke dmix open, and LINEOUT0
kept successful writes muted. An inline private raw slave within the existing
native dmix identity plus a bounded LINEOUT20 trial resolved these. Both controls
were restored after the tests. These USB tests neither installed an OS nor
implemented a production Wi-Fi speaker endpoint. Next move the verified route and
fresh preflight/cleanup into a lease-bound device worker, then test Stop, duplex
media and echo before claiming integrated conversation.

## T3 destination clarification —2026-11-15

Adam's primary agent destination includes his existing T3 Code threads with
project history/tools, not only a fresh API chatbot. Fresh camera frames must
reach the selected vision-capable thread. Standalone API chat remains supported
as a separate mode. A realtime speech frontend can coexist with that thread,
but a third vision model is not inherently required. Start with local STT/TTS
and one real T3 audiovisual loop, then add GPT-Live client delegation if it
improves the experience. See [the researched plan](T3-VOICE-VISION-PLAN.md).
