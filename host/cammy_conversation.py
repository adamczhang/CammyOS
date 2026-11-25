"""Finite agent turn inbox over the accepted Reachy app media. Idle by default.

Desktop hold/release is an utterance gate, not the physical record key or VAD.
No speech is forwarded to a hosted model. An attached agent polls and replies.
"""
import json
import math
import os
from pathlib import Path
import secrets
import selectors
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
VOICE_PYTHON=ROOT.parent/'voice-keys/.venv/bin/python'
MODEL_ROOT=Path(os.environ.get('CAMMY_MODELS', Path.home() / 'cammy-models'))


class ProcessJob:
    """One owned process, bounded pipe I/O/deadline, kill+reap on cancellation."""
    def __init__(self,command,data,limit,timeout):
        self.lock=threading.Lock();self.cancelled=threading.Event();self.done=False
        self.value=None;self.error=None;self.process=None;self.reaped=False
        self.command,self.data,self.limit,self.timeout=command,data,limit,timeout
        self.thread=threading.Thread(target=self._run,daemon=True,name='cammy-local-speech')
        self.thread.start()

    def _run(self):
        process=None
        try:
            if self.cancelled.is_set():return
            command=[sys.executable,str(ROOT/'host/media_child.py'),str(os.getpid()),*self.command]
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            with self.lock:self.process=process
            deadline=time.monotonic()+self.timeout;output=bytearray();sent=0
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdin.fileno(),False);os.set_blocking(process.stdout.fileno(),False)
                selector.register(process.stdin,selectors.EVENT_WRITE,'input')
                selector.register(process.stdout,selectors.EVENT_READ,'output')
                while selector.get_map():
                    if self.cancelled.is_set() or time.monotonic()>=deadline:raise RuntimeError('cancelled-or-timeout')
                    for key,events in selector.select(.05):
                        if key.data=='input':
                            if sent==len(self.data):
                                selector.unregister(process.stdin);process.stdin.close();continue
                            try:sent+=os.write(process.stdin.fileno(),self.data[sent:sent+32768])
                            except BlockingIOError:continue
                        else:
                            try:part=os.read(process.stdout.fileno(),32768)
                            except BlockingIOError:continue
                            if not part:selector.unregister(process.stdout);continue
                            output.extend(part)
                            if len(output)>self.limit:raise RuntimeError('output-limit')
            if process.wait(timeout=max(.01,deadline-time.monotonic())):raise RuntimeError('worker-failed')
            self.value=bytes(output)
        except Exception:self.error='local-worker-failed-or-cancelled'
        finally:
            if process is not None:
                if process.poll() is None:process.kill()
                process.wait()
                for pipe in (process.stdin,process.stdout):
                    if pipe is not None:pipe.close()
            self.data=b'';self.reaped=True;self.done=True

    def poll(self):
        return None if not self.done else (self.value,self.error)

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:self.process.kill()
                except ProcessLookupError:pass


class LocalSpeech:
    """Existing installed engines/models only; no download or default speaker."""
    def ready(self,speaker):
        paths=[VOICE_PYTHON,MODEL_ROOT/'ggerganov/whisper.cpp/en_whisper_small.ggml']
        if speaker:paths += [MODEL_ROOT/'hexgrad/Kokoro-82M/config.json',MODEL_ROOT/'hexgrad/Kokoro-82M/kokoro-v1_0.pth',MODEL_ROOT/'hexgrad/Kokoro-82M/voices/af_heart.pt']
        if not all(p.is_file() for p in paths):raise RuntimeError('Local speech resources unavailable')
        self.target=self.sink() if speaker else None

    def sink(self):
        rows=json.loads(subprocess.check_output(['pw-dump'],text=True,timeout=2))
        found=[r['info']['props']['node.name'] for r in rows
            if r.get('info',{}).get('props',{}).get('media.class')=='Audio/Sink'
            and 'Poly_Studio_P15' in r.get('info',{}).get('props',{}).get('node.name','')]
        if len(found)!=1:raise RuntimeError('Exactly one P15 speaker required')
        return found[0]

    def transcribe(self,pcm):return ProcessJob([str(VOICE_PYTHON),str(ROOT/'host/conversation_worker.py'),'stt'],pcm,4096,25)
    def synthesize(self,text):return ProcessJob([str(VOICE_PYTHON),str(ROOT/'host/conversation_worker.py'),'tts'],text.encode(),720000,25)
    def play(self,pcm):
        if not pcm or len(pcm)%2 or len(pcm)>720000:raise ValueError('Bounded mono24k speech required')
        target=getattr(self,'target',None)
        if target is None:raise RuntimeError('P15 output was not enabled for this session')
        return ProcessJob(['/usr/bin/pw-play','--target',target,'--volume','.9','--raw',
            '--rate','24000','--channels','1','--format','s16','-'],pcm,4096,len(pcm)/48000+3)


class Conversation:
    def __init__(self,live,start_media,stop_media,face=lambda expression:None,speech=None,clock=time.monotonic,autostep=True,cleanup_ready=lambda:True):
        self.live,self.start_media,self.stop_media,self.face=live,start_media,stop_media,face
        self.speech=speech or LocalSpeech();self.clock=clock;self.lock=threading.RLock()
        self.cleanup_ready=cleanup_ready
        self.phase='idle';self.session=self.boot=self.turn=None;self.generation=0
        self.job=None;self.retired=[];self.buffer=bytearray();self.inbox=None;self.reply_text=None
        self.error=None;self.reason='not-started';self.cursor=0;self.speaker=False;self.ends=0;self.stale_rejected=0
        self.quit=threading.Event();self.thread=None
        if autostep:
            self.thread=threading.Thread(target=self._run,daemon=True,name='cammy-conversation')
            self.thread.start()

    def _run(self):
        while not self.quit.wait(.05):
            try:self.step()
            except Exception:self.stop('conversation-failed')

    def _clear_job(self):
        if self.job:
            self.job.cancel();self.retired.append(self.job);self.job=None
        self.retired=[j for j in self.retired if j.poll() is None]

    def start(self,seconds=60,speaker=False):
        if type(seconds) is not int or not 5<=seconds<=90 or type(speaker) is not bool:raise ValueError('Finite5..90s session and explicit speaker bool required')
        with self.lock:
            if self.phase!='idle':raise RuntimeError('Session already active')
            self.retired=[j for j in self.retired if j.poll() is None]
            if self.retired or self.status()['media_cleanup_pending']:raise RuntimeError('Worker/media cleanup active')
            self.speech.ready(speaker)
            state=self.start_media(seconds)
            self.generation+=1;self.session=state['session'];self.boot=None
            self.speaker=speaker;self.phase='starting';self.reason='started'
            self.requested_seconds=seconds;self.start_deadline=self.clock()+100
            self.ends=self.clock()+seconds
            self.error=self.inbox=self.reply_text=self.turn=None;self.buffer.clear()
            return self.status()

    def _bound(self,state):
        return state.get('session')==self.session and (self.boot is None or state.get('boot_id')==self.boot)

    @staticmethod
    def _fresh(state):
        return state.get('frames',0)>0 and all(type(state.get(k)) in (int,float)
            and math.isfinite(state[k]) and 0<=state[k]<=2
            for k in ('frame_age_seconds','audio_age_seconds'))

    def listen(self,session,seconds=8):
        if type(seconds) is not int or not 1<=seconds<=8:raise ValueError('Utterance1..8s required')
        with self.lock:
            if session!=self.session or self.phase not in ('ready','awaiting-agent','recognizing','synthesizing','playing'):
                raise RuntimeError('Current ready conversation required')
            state=self.live.status()
            if not self._bound(state) or state.get('phase')!='live' or not self._fresh(state):raise RuntimeError('Fresh bound media required')
            self._clear_job()
            if self.retired:raise RuntimeError('Prior speech worker cleanup active')
            self.generation+=1;self.turn=secrets.token_hex(12);self.inbox=self.reply_text=None
            self.buffer.clear();self.cursor=state['audio_sequence'];self.capture_at=self.clock()
            self.capture_ends=min(self.ends,self.capture_at+seconds);self.phase='listening'
            self.face('neutral');return self.status()

    def finish(self,session):
        with self.lock:
            if session!=self.session or self.phase!='listening':raise RuntimeError('Current listening turn required')
            self._audio()
            self._recognize();return self.status()

    def _audio(self):
        pcm,meta=self.live.audio_tail(self.cursor)
        if not self._bound(meta) or meta.get('history_gap'):raise RuntimeError('Audio binding/gap')
        latest=meta.get('latest_sequence')
        if type(latest) is not int or latest<self.cursor:raise RuntimeError('Audio sequence reset')
        if pcm:
            if not isinstance(pcm,bytes) or len(pcm)%2:raise RuntimeError('PCM format')
            self.buffer.extend(pcm)
            if len(self.buffer)>256000:raise RuntimeError('Utterance limit')
        self.cursor=latest

    def _recognize(self):
        if len(self.buffer)<11200:
            self.buffer.clear();self.phase='ready';return
        self.job=self.speech.transcribe(bytes(self.buffer));self.buffer.clear();self.phase='recognizing'
        self.face('doubt')

    def reply(self,session,turn,text):
        if not isinstance(text,str) or not text.strip() or len(text)>160 or any(ord(c)<32 for c in text):
            raise ValueError('One plain-text reply1..160characters required')
        with self.lock:
            if (session!=self.session or turn!=self.turn or self.phase!='awaiting-agent'
                    or not self.inbox or self.clock()>=self.inbox['expires_at']):
                raise RuntimeError('Current unconsumed agent turn required')
            state=self.live.status()
            if not self._bound(state) or state.get('phase')!='live' or not self._fresh(state):raise RuntimeError('Fresh bound media required')
            self.reply_text=text.strip();self.inbox=None
            if self.speaker:self.job=self.speech.synthesize(self.reply_text);self.phase='synthesizing'
            else:self.phase='ready'
            self.face('happy');return self.status()

    def step(self):
        with self.lock:
            self.retired=[j for j in self.retired if j.poll() is None]
            if self.phase=='idle':return
            now=self.clock();state=self.live.status()
            deadline=self.start_deadline if self.phase=='starting' else self.ends
            if not self._bound(state) or state.get('phase') not in ('starting','preparing','live') or now>=deadline:
                self.stop('session-ended-or-changed');return
            if self.phase=='starting':
                if state.get('phase')=='live' and self._fresh(state):
                    if not isinstance(state.get('boot_id'),str):self.stop('boot-identity-unavailable');return
                    self.boot=state['boot_id'];self.phase='ready';self.ends=now+self.requested_seconds
                return
            if not self._fresh(state):self.stop('media-stale');return
            if self.phase=='listening':
                try:self._audio()
                except RuntimeError:
                    self.stop('audio-unavailable');return
                if now>=self.capture_ends:self._recognize()
            if self.phase=='awaiting-agent' and now>=self.inbox['expires_at']:
                self.inbox=None;self.phase='ready';self.stale_rejected+=1
            if not self.job:return
            result=self.job.poll()
            if result is None:return
            self.job=None;value,error=result
            if error:self.error='local-speech-failed';self.phase='ready';self.face('neutral');return
            if self.phase=='recognizing':
                row=json.loads(value)
                text=row.get('text')
                if (not isinstance(text,str) or len(text)>500 or not text.strip()
                        or now-self.capture_at>20):
                    self.phase='ready';self.stale_rejected+=1;self.face('neutral');return
                self.inbox={'session':self.session,'turn':self.turn,'boot_id':self.boot,'text':text,
                    'synthetic':state.get('synthetic',False),'source':'Cammy live microphone; local Whisper',
                    'captured_at':self.capture_at,'expires_at':min(self.ends,now+20)}
                self.phase='awaiting-agent'
            elif self.phase=='synthesizing':
                self.job=self.speech.play(value);self.phase='playing'
            elif self.phase=='playing':self.phase='ready';self.face('neutral')

    def stop(self,reason='requested'):
        with self.lock:
            session=self.session;self.generation+=1;self._clear_job();self.buffer.clear()
            self.inbox=self.reply_text=self.turn=None;self.phase='idle';self.error=None;self.reason=reason
            self.face('neutral')
            # Never stop a replacement/other controller's app session.
            if session and self.live.status().get('session')==session:
                try:self.stop_media(session)
                except RuntimeError:self.reason='media-session-changed-or-stop-unconfirmed'
            return self.status()

    def status(self):
        with self.lock:
            media=self.live.status()
            media_cleanup=not self.cleanup_ready() or any(media.get(key,True) is not True for key in
                ('worker_threads_joined','decoder_reaped','microphone_stream_closed'))
            return {'phase':self.phase,'session':self.session,'boot_id':self.boot,'turn':self.turn,
                'inbox':self.inbox,'reply_text':self.reply_text,'error':self.error,'reason':self.reason,
                'seconds_left':max(0,round(self.ends-self.clock(),2)) if self.phase!='idle' else 0,
                'speaker':'P15 desktop' if self.speaker else 'disabled','camera_speaker_available':False,
                'physical_ptt':False,'vad':False,'lcd_face':False,'capture_bytes':len(self.buffer),
                'worker_cleanup_pending':bool(self.retired),'media_cleanup_pending':media_cleanup,
                'stale_rejected':self.stale_rejected}

    def close(self):
        self.stop('app-close');self.quit.set()
        if self.thread:self.thread.join(timeout=2)
        for job in self.retired:job.thread.join(timeout=1)
