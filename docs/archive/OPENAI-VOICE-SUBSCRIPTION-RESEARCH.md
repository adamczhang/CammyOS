# OpenAI subscription and Cammy voice — verified2026-11-15

Adam confirms ChatGPT Plus. This review fetched current official documentation;
it did not inspect credentials, query his API billing, make paid requests or change
a subscription, runtime or OS candidate. Claude is a separate Opus investigation.

## Subscription verdict

Plus provides ChatGPT app Voice, subject to availability and plan limits. The
app's desktop Voice experience uses its subscription budget; it is not a reusable
voice API credential. See [ChatGPT Voice](https://learn.chatgpt.com/docs/features/voice)
and [desktop Voice pricing](https://learn.chatgpt.com/docs/pricing).

There is now a subscription API-credit benefit, so the old universal claim that
subscriptions never include API credits is outdated. Eligible Pro and Business
owners can activate it by linking an API organization. Plus is explicitly
ineligible. Eligible credits can cover GPT-Live and Realtime usage; activation,
API organization and model access still matter. This does not establish Adam's
separate API balance or access. [Official credit eligibility and scope](https://developers.openai.com/api/docs/guides/chatgpt-api-credits).

## Programmable conversation

GPT-Live lets a voice session listen while speaking and delegate work to a
separately chosen backend agent, including a client-managed harness. It uses
an API key/server and WebRTC or WebSocket; it does not consume Plus app Voice
as an embedded service. [GPT-Live guide](https://developers.openai.com/api/docs/guides/live).

The documented model `gpt-live-1` takes audio/text and returns audio/text, with
no direct image/video input. Voice sessions cost$0.05 per minute, billed per
second of session duration; backend model/tools are additional. Idle-open
sessions therefore still cost money. API free tier is unsupported. Documentation
alone does not prove this account's access. [Model and pricing](https://developers.openai.com/api/docs/models/gpt-live-1).

Realtime is another supported speech-to-speech architecture with interruptions
and tool calls, using its own API sessions. [Realtime guide](https://developers.openai.com/api/docs/guides/realtime).

Maple is not listed in the fetched GPT-Live voice table, so no exact Maple API
mapping is verified. Do not infer that a ChatGPT preference selects an API voice.
[Live voice options](https://developers.openai.com/api/docs/guides/live-conversations#voice-options).

## Fit for Cammy (engineering inference; not tested)

For subscription-only use, a user-operated ChatGPT Voice session could potentially
use Cammy's PCM exposed as a PC virtual microphone and P15 speaker output. This
requires an audio-routing test and remains the normal app experience; it does not
supply Cammy a headless SDK voice/control interface.

For an integrated robot, a PC-side GPT-Live adapter matches the desired split:
Cammy microphone -> voice session -> P15, with a separately owned backend agent
requesting existing Cammy SDK tools. Video would remain a separate backend input.
Keep finite sessions, PTT/Stop, explicit cloud-audio consent and budget limits;
no camera firmware change is required just to choose this voice provider.
This would need separately funded API access on Plus. No upgrade is recommended
solely on this review, and no implementation or paid test was started.

## Pricing detail reconfirmed

Current official cost guidance: silence and muted time in an open GPT-Live session
are billable. WebRTC session creation initially bills15seconds, credited against
running-session duration; do not add another15seconds to an established session.
Backend costs remain separate. [Cost accounting](https://developers.openai.com/api/docs/guides/voice-latency-cost).
