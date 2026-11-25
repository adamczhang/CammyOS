"""Offline character-voice audition; no playback, microphone or camera access.

Run in the isolated voice-audition environment described in VOICE-OPTIONS.
Upstream models/code must already exist locally; this never downloads them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

MODELS = {"pikachu": "EthanRhys/Pikachu",
          "pikachu-kate-bristol": "EthanRhys/Pikachu-Kate-Bristol",
          "pikachu-kuma": "Kuma6/Pikachu",
          "pikachu-coolie": "Cooliebruv/CoolieRVC"}
EMOTIONS = ("happy", "surprised", "whisper")
TEXT = "Welcome to the Fin and Fur Channel!"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--silencio-code", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pitch", type=int, default=12, choices=range(-12, 25))
    parser.add_argument("--voices", nargs="+", choices=MODELS, default=list(MODELS))
    args = parser.parse_args()
    models = args.models_root.resolve(strict=True)
    code = args.silencio_code.resolve(strict=True)
    output = args.out.resolve()
    if output.exists():
        parser.error("Output must be a new directory")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      TORCH_FORCE_WEIGHTS_ONLY_LOAD="1")
    sys.path.insert(0, str(code))
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    from huggingface_hub_rvc import RVCPipeline
    from silencio2 import Silencio2TTS

    # RVC uses torchaudio's tensor resampler. For WAV I/O only, use soundfile
    # instead of the optional TorchCodec backend. Scoped to this worker process.
    def load_wav(path, **_):
        data, rate = sf.read(path, dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), rate

    def save_wav(path, value, rate, **_):
        sf.write(path, value.detach().cpu().numpy().T, rate, subtype="PCM_16")

    torchaudio.load = load_wav
    torchaudio.save = save_wav
    torch.set_num_threads(4)
    output.mkdir(parents=True)
    source_root = output / "sources"
    source_root.mkdir()
    tts = Silencio2TTS.from_pretrained(models / "BlueRoseLabs/Silencio-2", device="cpu")
    manifest = dict(text=TEXT, synthetic=True, character_likeness_accepted=False,
                    perceived_emotion_verified=False, camera_access=False,
                    playback_requested=False, source_voice="260", seed=7,
                    pitch_semitones=args.pitch, f0="rmvpe", retrieval_strength=.65,
                    source_model="BlueRoseLabs/Silencio-2", rows=[])
    for emotion in EMOTIONS:
        audio = tts.generate(TEXT, voice="260", emotion=emotion, seed=7).numpy()
        sf.write(source_root / f"{emotion}.wav", audio, tts.sample_rate)
    for model in args.voices:
        folder = models / MODELS[model]
        weights = list(folder.glob("*.pth"))
        if len(weights) != 1:
            raise ValueError(f"Expected exactly one checkpoint in {folder}")
        pipe = RVCPipeline.from_pretrained(weights[0], local_files_only=True,
            model_config=dict(device="cpu",
                hubert_model_path=str(models / "lj1995/VoiceConversionWebUI/hubert_base"),
                rmvpe_model_path=str(models / "lj1995/VoiceConversionWebUI/rmvpe.pt")))
        combined = []
        for emotion in EMOTIONS:
            filename = f"{model.lower()}-{emotion}.wav"
            path = output / filename
            started = time.monotonic()
            pipe.convert_file(source_root / f"{emotion}.wav", path,
                device="cpu", audio_mode="vocal_only", f0_method="rmvpe",
                retrieval_strength=.65, pitch_shift=args.pitch)
            data, rate = sf.read(path, dtype="float32")
            if data.ndim != 1 or not np.isfinite(data).all() or not .5 < len(data)/rate < 10:
                raise ValueError("Invalid audition audio")
            rms = float(np.sqrt(np.mean(data**2)))
            peak = float(np.max(np.abs(data)))
            if rms < .0001:
                raise ValueError("Silent audition")
            # Equal comparison level, no clipping, no time stretch or added FX.
            data *= min(.08/rms, .85/max(peak, .0001))
            sf.write(path, data, rate, subtype="PCM_16")
            combined.extend((data, np.zeros(int(rate*.7), dtype=np.float32)))
            row = dict(model=model, repository=MODELS[model], checkpoint_sha256=digest(weights[0]),
                       emotion_tag=emotion, file=filename, sample_rate=rate,
                       duration_seconds=len(data)/rate,
                       conversion_seconds=time.monotonic()-started,
                       sha256=digest(path), rms=float(np.sqrt(np.mean(data**2))),
                       peak=float(np.max(np.abs(data))))
            manifest["rows"].append(row)
            print(json.dumps(row), flush=True)
        sf.write(output / f"{model.lower()}-three-moods.wav", np.concatenate(combined), rate)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")


if __name__ == "__main__":
    main()
