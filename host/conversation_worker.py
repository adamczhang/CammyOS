"""Local speech worker. Bounded stdin/stdout; no network, recording or playback.

Run with the existing voice-keys Python environment, not the Reachy app process.
Models remain in SERVE/models. Raw microphone/synthesized PCM stays in pipes/RAM.
"""
import contextlib
import json
import os
from pathlib import Path
import sys

MODELS=Path(os.environ.get('CAMMY_MODELS', Path.home() / 'cammy-models'))


def main():
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='4')
    mode=sys.argv[1] if len(sys.argv)==2 else ''
    raw=sys.stdin.buffer.read(256001)
    if len(raw)>256000:raise ValueError('Input limit')
    import numpy as np
    if mode=='stt':
        if not 11200<=len(raw)<=256000 or len(raw)%2:raise ValueError('Mono16k bounded PCM required')
        from pywhispercpp.model import Model
        with contextlib.redirect_stdout(sys.stderr):
            model=Model(str(MODELS/'ggerganov/whisper.cpp/en_whisper_small.ggml'),
                n_threads=6,language='en',no_context=True,print_progress=False,print_realtime=False,
                redirect_whispercpp_logs_to=None,context_params={'use_gpu':False})
            rows=model.transcribe(np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768)
        text=' '.join(row.text.strip() for row in rows).strip()
        if len(text)>500:raise ValueError('Transcript limit')
        print(json.dumps({'text':text,'provider':'local-whisper-small.en','synthetic':False}))
    elif mode=='tts':
        text=raw.decode('utf-8')
        if not text.strip() or len(text)>160 or any(ord(c)<32 for c in text):raise ValueError('Reply limit')
        from kokoro import KModel,KPipeline
        root=MODELS/'hexgrad/Kokoro-82M'
        with contextlib.redirect_stdout(sys.stderr):
            model=KModel(config=str(root/'config.json'),model=str(root/'kokoro-v1_0.pth')).eval()
            pipeline=KPipeline(lang_code='a',repo_id='hexgrad/Kokoro-82M',model=model)
            chunks=[];samples=0
            for row in pipeline(text,voice=str(root/'voices/af_heart.pt'),speed=1.28):
                if row.audio is None:continue
                value=row.audio.numpy();samples+=len(value)
                if samples>24000*15:raise ValueError('Speech duration limit')
                chunks.append(np.clip(value*2,-1,1))
        if not chunks:raise ValueError('No speech produced')
        sys.stdout.buffer.write((np.concatenate(chunks)*32767).astype('<i2').tobytes())
    else:raise ValueError('Only stt/tts supported')


if __name__=='__main__':
    try:main()
    except Exception:
        # Never put captured speech/model output in logs or an HTTP error.
        print('Local speech worker failed',file=sys.stderr);sys.exit(1)
