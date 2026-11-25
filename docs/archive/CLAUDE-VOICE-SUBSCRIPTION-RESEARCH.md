# Claude subscription and voice for Cammy — 2026-11-15

Opus5.5 investigated; manager reviewed the fetched official pages and corrected
three overstatements below. Adam confirms Claude Max ($100/month). Credit activation, balance and account-specific model access remain unverified.
No credentials, account settings, paid requests, camera or OS candidate were touched.

## Voice itself

Claude provides spoken conversations in its web, desktop and mobile apps on all
listed plans, consuming ordinary plan limits. Hands-free and push-to-talk modes
are documented. This is app functionality, not a documented embedded voice API.
[Voice mode](https://support.claude.com/en/articles/11101966-use-voice-mode).

The current model overview lists text/image inputs and text outputs. No official
Claude audio-in/audio-out, STT, TTS or speech-to-speech API was found in this review.
This is a documentation finding, not proof that every private/preview interface is
absent. [Current models](https://platform.claude.com/docs/en/models/overview).

For an integrated Cammy adapter, the documented pattern is a chain: STT -> streaming
Claude text -> TTS. Local speech components can remain independent of the brain.
Anthropic's [voice cookbook](https://platform.claude.com/cookbook/third-party-elevenlabs-low-latency-stt-claude-tts)
uses third-party speech services. Its example latency is not a prediction for
Cammy. A chain can implement playback cancellation/barge-in locally; it does not
inherently provide a native full-duplex speech model.

## Subscription can still help with the brain

The current [Agent SDK plan article](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
explicitly says signed-in Agent SDK and `claude -p` usage still consumes subscription
limits; the June2026 changes were paused. Therefore a blanket claim that personal
Cammy use cannot use subscription-backed Claude at all is too strong. This is a
potential text-agent route to assess using the official, unmodified sign-in/SDK,
not a way to obtain Claude's app speech backend.

The [legal/compliance page](https://code.claude.com/docs/en/legal-and-compliance)
assumes ordinary individual use and distinguishes a user signing into unmodified
Claude Code from third-party developers collecting/intermediating credentials or
offering plan login for their own users. Product/service integrations are directed
to API keys. Do not extract/replay tokens or promise unrestricted unattended use.
The reviewed help article is explicit about subscription SDK billing; a deployment
choice still needs the correct supported authentication and workload assumptions.
[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview).

Max includes$100/month and Ultra$200/month of API credits; Pro is ineligible.
Eligible subscriptions need seven days and a linked Console organization; credits
expire each cycle. Credits cover Messages/Agent SDK usage, not another company's
speech service. Additional usage draws on purchased credits if present; otherwise
requests stop. Account-specific availability and activation were not verified.
[Credit eligibility and claiming](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans).

## Practical choices (engineering inference, untested)

- Subscription app test: publish Cammy PCM as a PC virtual microphone, choose it
  in a normal Claude Voice session, and play replies through P15. This may work
  through standard audio routing; the earlier report's categorical claim that it
  cannot connect to these devices was unsupported. It remains app-operated, not
  a Reachy/headless voice SDK. No routing test has been performed.
- Personal agent: keep local STT/TTS and investigate supported subscription-backed
  Agent SDK/CLI turns as the text brain. Plan limits apply; no native speech API.
- API robot: keep STT/TTS and use the streaming Messages API as brain. Claimed
  Max/Ultra credits could fund that part; Pro needs separate API funding.
- Native speech-to-speech comparison: OpenAI GPT-Live is an independently billed
  voice option; see [OpenAI review](OPENAI-VOICE-SUBSCRIPTION-RESEARCH.md). Do not
  equate a Claude or ChatGPT app subscription with an unrestricted audio API.

Remaining account facts: seven-day eligibility, claimed Console credits/balance, and
supported sign-in/workload for a personal SDK trial. No implementation was started.
