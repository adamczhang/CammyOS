# Reference-based character voice cloning — 2026-11-15

**Paused by Adam,2026-11-15.** The selected reference is preserved. No cloning
installation/generation is in progress; the next stretch goal is the animated
character face. Resume this plan only when voice work is resumed.


Adam rejected all four Silencio→RVC auditions, after previously rejecting the
PocketDM supplied samples. No Pikachu voice has passed. Stop treating those
checkpoints or high-pitched source conversion as the current delivery route.
The requested target remains “Welcome to the Fin and Fur Channel”, with
recognizable character identity, clear words and emotional delivery.

## Selected next comparisons

1. **[IndexTTS2.5](https://github.com/index-tts/index-tts): first emotional clone
   experiment.** The current primary documentation exposes a speaker reference,
   a separate emotional audio reference, emotion intensity, English/Japanese
   support, speed control and English CMU pronunciation overrides. This fits the
   need to hold voice identity constant while changing delivery. Its controls
   are documented capabilities, not proof of cartoon likeness or local AMD speed.
2. **[Qwen3-TTS1.7B Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base):
   independent larger-model cloning comparison.** Supply original reference
   audio plus an accurate transcript, using full in-context cloning, not only
   the speaker embedding. Upstream explicitly says embedding-only mode can
   reduce cloning quality. The Base clone and VoiceDesign/CustomVoice checkpoints
   have different interfaces; do not promise arbitrary emotion instructions on
   the Base API. Emotion-matched references are the initial experiment.
3. **[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS): curated fine-tuning
   fallback.** Upstream supports5-second zero-shot references and describes
   few-shot fine-tuning from about1minute. English/Japanese cross-lingual support
   is documented. Training quality and the amount of useful character audio
   needed here remain unknown; a minute is not a guaranteed Pikachu result.

No one of these is installed or auditioned for this new approach. Do not call
one the best-sounding model based on its README. Quality takes priority over the
smallest parameter count; establish acceptable output before optimizing latency.

## Reference pack

Adam selected [this video](https://www.youtube.com/watch?v=5x6BOqP8Y3s),27–37seconds,
“Pikachu Speaks To Ash - Pokémon the Movie I Choose You!” The requested excerpt
is saved in ignored `private/voice-reference/youtube-5x6BOqP8Y3s/` as an MP4 and
an exactly10-second48kHz stereo PCM WAV, decoded from best available original
audio without intermediate lossy re-encoding. `clip-manifest.json` records source,
timing and hashes. No noise removal or vocal separation has been performed.
This is the selected target reference, not evidence of successful cloning.

Practical collection target (our trial design, not a model requirement):

- One clean5–15second identity reference, one speaker, no music, overlap,
  clipping, heavy reverb or synthetic pitch processing. Preserve original
  source/rate; lossless WAV is useful for preparation, but converting an MP3 to
  WAV does not restore lost quality.
- Separate short happy, curious and calm/sleepy reference performances where
  available. Keep the same incarnation/speaker and avoid mixing incompatible
  voices into a single prompt. Label exact transcript, source URL and time range.
- Prefer clean original voice assets or isolated dialogue over recordings of
  speakers. If separation is unavoidable, audition the separated stem against
  the original; removal artifacts can become part of the clone.
- If fine-tuning is needed, seek1–3minutes of varied, clean utterances as an
  initial curated corpus; preserve phonetic and emotional variety, not repeated
  syllables merely to increase duration.

Risk/inference: mostly “pi/ka/chu” cries supply very little ordinary spoken
phonetic coverage. A model may capture squeak/timbre yet fail fluent English.
A clean spoken line in the desired voice is a better reference experiment when
available. Do not transcribe cries as unrelated English sentences to force the
prompt, and do not claim cross-lingual support solves non-speech vocalizations.

## Bounded next test

1. Play the short original reference by itself for target confirmation.
2. Establish identity and intelligibility on one neutral greeting using each of
   the first two models, original pitch, no RVC or post-processing disguise.
3. Reject obvious failures before generating more material. Human acceptance
   decides voice similarity; ASR only helps detect word errors.
4. Once identity passes, test happy/curious/sleepy references or documented
   emotion controls. Check specifically that “Fin”, “Fur” and “Channel” survive.
5. Keep this in an isolated PC environment with weights/caches on SERVE. Only
   after a voice passes should it replace the resident Kokoro provider. Camera
   speaker transport remains a separate open issue; no firmware/card update is
   necessary for this research.

Sources checked2026-11-15. No new audio/reference has been obtained or generated
for the reference-cloning approach yet. Earlier rejected reels are retained as
failed baselines, not replayed or promoted.

## Required voice-pack outputs

Adam clarified that the pack must support both newly generated intelligible
speech (arbitrary sentences, including the Fin and Fur greeting) and direct
playback of selected “pika pika”/Pikachu reaction clips. Neither replaces the
other. Use the clone for linguistic speech; keep approved original reactions as
separate assets with source/timing/emotion labels. They share the eventual output
transport, volume policy and Stop/interruption behavior; do not overlap by
accident. A cloned speaker prompt does not restrict the agent to the reference's
words. Actual speech quality still requires human acceptance; no new clone or
reaction library is implemented yet.
