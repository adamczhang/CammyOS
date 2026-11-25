# Cammy's brain

## Resident mode

In the resident implementation, the C3 service owns the active profile and its
ongoing conversation. Start C3 with `--brain resident` (the default). Choose
**Brain** in the window and create a profile with a name, provider, and optional
standing instructions. Each profile keeps its identity, conversation state, and
working files under `private/c3/brain/profiles/<id>/`. The provider session ID
lets that profile continue after a service restart. Interrupted turns stay in
history, where you can see which request to send again.

Use the native sign-in of the Linux account running C3. Run `codex login` for a
ChatGPT subscription or `claude auth login --claudeai` for a Claude subscription.
Provider credentials stay with the provider CLI. Brain text turns use the
selected resident provider. Calendar requests use the local broker and appear
in CCC. The family deployment closes Brain access in Kids mode.
[Family and Calendar controls](C3.md#family)

Use `--brain off` to disable Brain. The separate adult-only deployment
supports the legacy `--brain t3` path described below.

The resident Brain loads [Cammy's technical foundation](CAMMY-BRAIN-FOUNDATION.md)
alongside each profile's own instructions. In the selected Adult Brain text
channel, `/os status`, `/os prepare` and `/os request` can read status, review
an artifact and queue a maintenance request for the desktop operator.

## Legacy T3 mode

Adam's decision (2026-11-16): the T3 thread **"Cammy — brain"** is CammyOS's single point
of access into T3 Code. It is Adam's local agent manager for anything asked through
Cammy: it sends project work to that project's existing threads or delegated tasks, and
does everything else through subagents in its own folder (`brain/work/`). Its standing
instructions are `brain/CLAUDE.md`.

### The path, end to end

```
Adam ── voice ──> Cammy mic ── Wi-Fi ──> PC: host/c3/voice.py ──> Gemini Live (cloud)
                                                │  tool ask_brain(request)
                                                ▼
                         host/cammy_brain.py: t3 thread send  (0.18 s)
                                                ▼
                    T3 thread "Cammy — brain" (project Cammy Brain, folder brain/)
                       ├─ quick answer: replies in its own turn
                       ├─ project ask: t3_thread_send to that project's thread / delegate_task
                       └─ other work: background subagent in brain/work/<date-task>/
                                                │  reply = last message of each finished run
                                                ▼
                 cammy_brain.watch(): t3 thread show, 1 s pending / 10 s idle
                                                ▼
             C3.brain_in(): queues "[Brain] ..." for Gemini
                                                ▼
                          Gemini speaks it ── Wi-Fi ──> Cammy speaker
```

- The live loop is the only program that controls Cammy (lease, mic, speaker, camera,
  face). The brain never takes a lease and needs no Cammy tools.
- Results of delegated work come back without polling by Adam: when a subagent or task
  finishes, the brain gets a turn, its reply is picked up and spoken.
- The brain thread's ID lives in `private/brain/thread.json` (or `CAMMY_BRAIN_THREAD`);
  the loop also shows it as the agent ID on Cammy's Agent screen.
- Session start: the loop puts `brain/context.md` into Gemini's instructions (no round
  trip). C3 syncs new conversation in batches while running and again on shutdown.

### Where MCP and T3 fit

MCP clients use the same local CCC API as the desktop. The default stdio
adapter offers resident Brain status, active-profile history and private text
turns. The older session tools use the separate opt-in legacy adapter.
[Agent and bot access](C3.md#agent-and-bot-access)

In `--brain t3` mode, CCC also sends brain requests and reads completed runs
through the local T3 CLI in `host/cammy_brain.py`. A configured thread ID
describes the binding. A successful T3 read verifies reachability. With
`--brain off`, voice, chat and device controls run without a Brain provider.

### Who Gemini is

The persona follows the face pack (`host/cammy_persona.py`): the portal reports
`face_pack` in `/v1/state` (from `/etc/cammy/face-pack`, default `pikachu`; builds
before 0.7.1 omit it and get Pikachu). Asked who it is, Gemini answers "Hi, I'm Pikachu
on CammyOS!" (checked against gemini-3.8-live, 2026-11-16); with the Fin and Fur pack
(0.7.2) it is both characters at once: "Hi, we're Fin and Fur on CammyOS!". A new pack adds
one entry to `PACKS` (name, who, introduction, speaking style) and its ID to
`native/ui/cammy_ui_page.h`.

### Measured (2026-11-16, Opus 5.5 brain)

| Step | Time |
|---|---|
| `t3 thread send` / `t3 thread show` | 0.18 s / 0.16 s |
| Lookup ("which project is about benchmarks?"), ask to spoken-ready reply | 4.9 s |
| Delegated task: acknowledgement / finished result delivered | 9.6 s / 18.9 s |
| Pickup delay after the brain finishes | at most 1 s (poll) |

The brain's own turn dominates. A busy brain queues the next request until its turn
ends (T3 does not interrupt), so its instructions keep turns short and delegate the rest.

### What runs, and what is required outside T3

Required for the voice path:
- **CammyOS 0.7** on the camera (portal, face, `cammy-ui`).
- **`.venv-live`** (Python with `google-genai` 3.3) running `python -m c3` from `host/`.
- **ffmpeg** on the PC (camera preview frames for `look`).
- **A Gemini API key** in `private/credentials/gemini.key` (Google, cloud).

Inside T3 (nothing extra): the brain thread, its subagents and delegated tasks, the T3
CLI. Shared memory is the Agent-Memory vault (Basic Memory MCP `agent-memory`), as for every agent.

Not on this path: `cammy-host.service` (local gateway on :8790) and
`cammy-agent.service` with its `cammy` MCP server (other agents' Cammy tools; the MCP
server currently fails to connect). PipeWire's `filter-chain.service` is the stock
Ubuntu service with no filters configured; Cammy's mic does not pass through PipeWire.

### Known limits

- C3 gates the microphone locally and sleeps after the configured idle period.
  [Voice path and duplex settings](C3.md)
- `look` sends one preview frame inside the tool response; Gemini does not watch video.
- `take_picture` / `take_video` hand the screen to the stock live view and wait for Adam's
  red-button press (docs/CAPTURE.md); not yet tried on Cammy.
- C3 supports a finite trial with `--minutes`. For a long-running session, use
  the systemd user service.
