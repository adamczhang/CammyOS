# Custom character voices and Pikachu research — 2026-11-15

**Current decision:** Adam rejected **all four RVC auditions** on2026-11-15.
They are failed candidates, not a voice shortlist or pending acceptance. Keep
those artifacts for comparison only. The next approach is direct cloning from
high-quality reference audio, prioritizing fidelity over the smallest model.
See [reference-cloning plan](VOICE-CLONING-PLAN.md).


Kokoro is the current working baseline, not a required part of CammyOS or Reachy
compatibility. The PC speech worker produces PCM; the selected robot/desktop
output transport should not depend on which model produced it. The resident speech engine remains unchanged. An isolated local audition
environment now runs Silencio-2 plus four character-trained RVC checkpoints;
model files live on SERVE, separate from the application environment.

## Shortlist to audition

This is a source-verified shortlist, **not a listening benchmark or a verified
ranking of Pikachu similarity**. Model parameter counts do not equal total
runtime memory. Published speed numbers are author claims on their hardware,
not measurements on RED-LINUX or on the M105.

| Candidate | Published size | Why test it for Cammy | Important limit |
| --- | --- | --- | --- |
|[Silencio-2](https://huggingface.co/BlueRoseLabs/Silencio-2)|18.2M speech parameters;27.9M including cloning encoder;112MB weights|First expressive small-model audition: CPU, short-reference cloning, emotion and sound tags,24k streaming|English only; author documents seed/voice-dependent delivery. No demonstrated Pikachu preset|
|[Kyutai Pocket TTS](https://github.com/kyutai-labs/pocket-tts)|100M; smaller18.2M/6.9M/1.9M English variants also listed|Established CPU/streaming/cloning baseline; exported voice embeddings can form a small reusable voice pack|A reference voice is needed for a custom clone; smaller size is not evidence of better cartoon quality|
|[Qwen3-TTS0.6B Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base)|600M|Higher-capacity reference-cloning comparison; English/Japanese among supported languages|Heavier. Free-form voice design is a separate1.7B model;0.6B Base is not the voice-design checkpoint|
|[VoxCPM-0.5B](https://github.com/OpenBMB/VoxCPM)|500M|Already used by the closest found Pikachu companion project below|Still needs an actual listening/latency test here; not a shipped Cammy voice|
|[Chatterbox Nano](https://huggingface.co/ResembleAI/chatterbox-nano)|110M|CPU-oriented cloning and paralinguistic-tag comparison|The current [upstream README](https://github.com/resemble-ai/chatterbox) labels the project retired/unmaintained; lower priority for a new dependency|

The practical initial comparison is Silencio-2 versus Pocket TTS, then VoxCPM or
Qwen if the small models fail to preserve the desired character. RED-LINUX has
AMD Radeon AI PRO hardware, not an observed NVIDIA CUDA device; CPU-first is
the simplest portable baseline. No ROCm inference compatibility or latency was
established by this search. None of these PC model sizes implies they fit or run
usefully on the M105 itself.

## Rejected audition: Pocket Pikachu / PocketDM

[The project](https://github.com/Amal-David/pocketdm) combines a Pikachu-themed
companion, persona/mood behavior and a VoxCPM speech option. Three bundled
prerecorded examples were played through P15. **Adam rejected them as a pure
robot voice, not Pikachu.** Do not replay them as an accepted candidate or infer
quality from the package's branding. They were supplied examples, not a locally
run VoxCPM benchmark; this rejection does not establish every VoxCPM voice fails.
Feedback is retained in ignored `private/voice-research/p15-audition.json`.

## Actual character-model auditions

Four public RVC v2 checkpoints have now been inspected and run, using restricted
`torch.load(weights_only=True)`. They use40/48kHz output and about55–58MB checkpoint files. Their cards provide minimal provenance; the filenames are model labels,
not proof of the training speaker or of perceptual likeness.

| Checkpoint | Pinned revision | Archive SHA256 |
| --- | --- | --- |
|[EthanRhys/Pikachu](https://huggingface.co/EthanRhys/Pikachu)|`e9997ec60be18b3712c3ce76feb64cfe6fe6ac5f`|`3291347451a7722bce15b1f2c80808ad3515804e9da1c54030c32ae1016209fa`|
|[EthanRhys/Pikachu-Kate-Bristol](https://huggingface.co/EthanRhys/Pikachu-Kate-Bristol)|`67c60ba1536ab3229f5c3e9aabd5c4bf89b28142`|`da2aa5332fd474ae6bf7defccf7f8d7d3372f77b6d46b1ce0303c94f98138fba`|
|[Kuma6/Pikachu](https://huggingface.co/Kuma6/Pikachu)|`c6566a8a3d9e46c4fb8c09d6b27734ac3adb8b6b`|`66c03bd0a91937d56c4ba59c07f8f892924cc918a4b0ebff5ffcfd1d5ee56dc7`|
|[Cooliebruv/CoolieRVC, Pikachu archive](https://huggingface.co/Cooliebruv/CoolieRVC)|`04d7f163e245e4793d3088f34ffe0ef443ca6f79`|`c8791eb91d357d22ba39e4e37d9d1b65451a279e34b0e90e3b4cf57976ea9b7f`|

The offline chain is **text + emotion → Silencio-2 source speech → RVC character
conversion → WAV**, not a pitch shift of the rejected PocketDM sample. Source
voice260, seed7, tags happy/surprised/whisper; exact source text:
“Welcome to the Fin and Fur Channel!” RVC applies a12-semitone F0 register
adjustment through the trained generator, RMVPE pitch extraction and retrieval
strength0.65. Four character models × three tags produce twelve clips. Perceived
emotion and character similarity were **rejected by Adam**; none passed.

The final local artifacts are in ignored `build/voice-audition/results-p12/`:
individual WAVs, two three-mood reels, `manifest.json` and an independent local
Whisper `transcript-check.json`. The two additional models use the same layout
in `build/voice-audition/results-more/`. Happy/surprised/whisper is the reel order, with
0.7s silence between clips. All comparison clips use the same RMS target0.08
with peak capped0.85, no time stretching. An18-semitone exploratory variant
blurred Fin/Fur/Channel in recognition; it is not the default audition.
The initial Ethan checkpoints still blur Fin/Fur (Whisper often recovers
“Finn and Finn”, “Fan and Fire” or similar), so these are experimental
candidates rather than accepted speech. Recognition is only a rough intelligibility check, not proof of the words a
human hears or proof that a synthetic character sounds right.

Reproduce with already-downloaded inputs, no network or camera access:

```sh
HF_HOME=<models-drive>/models/huggingface \
  build/voice-audition/venv/bin/python tools/audition_character_voice.py \
  --models-root <models-drive>/models/hf \
  --silencio-code downloads/silencio2 \
  --out build/voice-audition/new-comparison
```

Silencio source is pinned to `BlueRoseLabs/Silencio-2` revision
`20930f5f6a6bb564f2a8207152fd5c5d8eecff03`, with code in ignored
`downloads/silencio2` and weights/tokenizer/voices on SERVE. Inference package
`huggingface-hub-rvc==0.1.4` runs in a disposable Python3.12 environment.
CPU torch2.15.0+cpu and torchaudio2.11.1+cpu import successfully together here.
The script scopes a soundfile WAV-I/O adapter to its own process to avoid the
optional TorchCodec loader; tensor resampling remains torchaudio. It enables
offline HF mode and restricted checkpoint loading. HuBERT and RMVPE auxiliary
weights are from `lj1995/VoiceConversionWebUI`, with pinned revision/checksums in
its SERVE `hubert_base/download-provenance.json`; RMVPE SHA256 is
`6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193`.
No weights, input speech, generated WAVs or third-party source enter git.

## Further model candidates

- **[Qwen3-TTS0.6B Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base):**
  next larger reference-cloning comparison; English and Japanese. Not installed
  or auditioned. Its free-form VoiceDesign sibling is1.7B, not this checkpoint.
- **[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS):** few-shot TTS and
  character-specific fine-tuning route if off-the-shelf conversion cannot
  preserve intelligibility and identity. More setup, no local audition yet.
- **[VoxCPM](https://github.com/OpenBMB/VoxCPM):** another reference-conditioned
  generator. The0.5B generation is distinct from the newer2B VoxCPM2. No local
  synthesis benchmark yet; choose actual reference/weights rather than assuming
  PocketDM's rejected examples reflect all outputs.
- **[Pocket TTS](https://github.com/kyutai-labs/pocket-tts):** CPU voice-cloning
  baseline; Silencio already provides a small emotion-tuned derivative.

## What a portable character pack should carry

Separate three things: the dialogue persona, voice identity/delivery, and short
reaction sounds. A TTS model can change timbre or delivery, but the agent controls
word choice, habits and personality. For example, useful English speech and
nonverbal “pika” reactions should be tested separately.

Use an existing [Character Card V2](https://github.com/malfoyslastname/character-card-spec-v2)
for personality, scenario, greetings and dialogue examples, with a model-specific
voice reference/embedding and optional reaction assets alongside it. Keep
ParamFace's existing face JSON and future gaze/expression mappings separate.
This is a proposed package layout, not implemented native Reachy character-card
support or a new robot-control protocol. Imported persona text should describe
character behavior, not grant device/tool permissions.

For the first audition, use the same short English lines and happy/curious/
sleepy reactions on each candidate. Measure cold and warm time-to-first-audio,
real-time factor, peak memory, stable voice identity, intelligibility and
character likeness. Listen through the eventual M105 speaker too: its actual
frequency response can change which voice works best. Include a consistent
reference clip before promising an exact character match. No benchmark or
best-Pikachu claim is made yet.

Arbitrary Cammy speech remains a separate open transport milestone. The first
finite stock-ALSA RAM probe refused before playback because the shared-mic
`pcm.!hw` override resolves the dmix slave to `asym`. Mixer/native state was
restored. Next transport experiment should use a process-private raw-hardware
slave configuration without changing the working global microphone profile.
