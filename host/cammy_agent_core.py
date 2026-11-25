"""Shared finite agent sessions. No I/O/sensing on construction; no shell tools."""
import array,hashlib,json,math,os,re,secrets,stat,threading,time,uuid
from pathlib import Path
from cammy_live import LiveSession,real_client
from cammy_control import ControlSession
from cammy_conversation import LocalSpeech

ROOT=Path(__file__).resolve().parents[1]
class AgentError(RuntimeError):
    def __init__(self,code,message):self.code=code;super().__init__(message)

# T3 thread IDs are canonical UUIDs, or namespaced IDs such as
# `mcp:launch:<64 hex>` for threads created through T3's MCP launcher.
_NAMESPACED_THREAD=re.compile(r'[a-z]{1,16}(?::[a-z]{1,16}){0,3}:[0-9a-f]{16,128}')
def thread_ok(value):
    if not isinstance(value,str):return False
    try:return str(uuid.UUID(value))==value
    except ValueError:return bool(_NAMESPACED_THREAD.fullmatch(value))
def thread_actor(value):
    """Stable session-actor name for a T3 thread; UUIDs map to themselves."""
    if not thread_ok(value):raise AgentError(400,'Existing T3 thread ID required')
    if re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value):return value
    return 't3-'+hashlib.sha256(value.encode()).hexdigest()[:40]

def quiet_policy(path):
    try:
        p=Path(path);s=p.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1 or s.st_size>4096:return True
        value=json.loads(p.read_text());return value.get('quiet') is not False
    except (OSError,ValueError,TypeError,AttributeError):return True

def speaker_pcm(pcm):
    """Local worker24k S16 -> bounded16k S16, conservatively normalized peak4000."""
    if not isinstance(pcm,bytes) or not pcm or len(pcm)%2 or len(pcm)>720000:raise AgentError(400,'Invalid generated PCM')
    source=array.array('h');source.frombytes(pcm)
    if __import__('sys').byteorder!='little':source.byteswap()
    result=array.array('h')
    for i in range(len(source)*2//3):
        a=i*3//2;result.append(source[a] if i%2==0 or a+1>=len(source) else (int(source[a])+int(source[a+1]))//2)
    peak=max((abs(v) for v in result),default=0)
    if peak:result=array.array('h',(round(v*min(1,4000/peak)) for v in result))
    if not 800<=len(result)<=80000:raise AgentError(400,'Generated speech must fit50ms..5seconds')
    if __import__('sys').byteorder!='little':result.byteswap()
    return result.tobytes()

class AgentController:
    def __init__(self,policy,client_factory=real_client,live_factory=None,control_factory=None,speech=None,clock=time.monotonic):
        self.policy=Path(policy);self.client_factory=client_factory
        self.live_factory=live_factory or (lambda:LiveSession(client_factory=client_factory))
        self.control_factory=control_factory or (lambda:ControlSession(client_factory=client_factory))
        self.speech=speech or LocalSpeech();self.clock=clock;self.lock=threading.RLock()
        self.active=None;self.retiring=None;self.actor=None;self.session=None;self.epoch=0;self.job=None;self.closed=False
    @staticmethod
    def actor_ok(actor):return isinstance(actor,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',actor)
    def _check(self,actor,session):
        if not self.active or actor!=self.actor or session!=self.session:raise AgentError(409,'Current owned agent session required')
        state=self.active.status()
        if state['phase'] in ('idle','faulted','stopping'):raise AgentError(409,'Session stopped, expired or unavailable')
        return self.active,self.epoch
    def _current(self,actor,session,runner,epoch):
        with self.lock:
            if self.active is not runner or self.epoch!=epoch:raise AgentError(409,'Result belongs to a stopped session')
            self._check(actor,session)
    def status(self,actor):
        with self.lock:
            state=None if self.active is None else self.active.status()
            return {'service':'cammy-agent','quiet':quiet_policy(self.policy),'owned_session':self.session if actor==self.actor else None,
                    'busy':self.active is not None and state['phase'] not in ('idle','faulted'),'session':state,
                    'owner_matches':actor==self.actor and self.actor is not None,'cleanup_pending':self.retiring is not None,'sensing_default':'off','hardware_cleanup_confirmed':False}
    def device_status(self):
        try:
            c=self.client_factory();return {'connected':True,'identity':c.identity(),'state':c.state(),'capabilities':c.capabilities()}
        except Exception:return {'connected':False,'error':'Configured body gateway unavailable'}
    def open(self,actor,seconds=30,sensing=False):
        if not self.actor_ok(actor) or type(seconds) is not int or not 5<=seconds<=90 or type(sensing) is not bool:raise AgentError(400,'Actor, finite5..90seconds and sensing bool required')
        with self.lock:
            if self.closed:raise AgentError(503,'Controller closed')
            if self.retiring:
                retired=self.retiring.status()
                if retired['phase'] in ('idle','faulted') and all(retired.get(k,True) for k in ['worker_threads_joined','decoder_reaped','microphone_stream_closed']):self.retiring=None
                else:raise AgentError(409,'Previous cleanup still running')
            if self.active:
                state=self.active.status()
                if state['phase'] not in ('idle','faulted'):raise AgentError(409,'An agent session is already active')
                if not all(state.get(k,True) for k in ['worker_threads_joined','decoder_reaped','microphone_stream_closed']):raise AgentError(409,'Previous cleanup unconfirmed')
            runner=self.live_factory() if sensing else self.control_factory()
            state=runner.start(seconds);self.epoch+=1;self.active=runner;self.actor=actor;self.session=state['session']
            return self.status(actor)
    def stop(self,actor,session):
        with self.lock:
            if actor!=self.actor or session!=self.session:raise AgentError(409,'Current owned agent session required')
            runner=self.active;self.active=None;self.retiring=runner;self.epoch+=1;job=self.job;self.job=None
        if job:job.cancel()
        try:
            result=runner.stop(wait=True) if runner else {}
            return {'stop_requested':True,'state':result,'hardware_cleanup_confirmed':False}
        finally:
            with self.lock:
                state=runner.status() if runner else {}
                if state.get('phase') in ('idle','faulted') and all(state.get(k,True) for k in ['worker_threads_joined','decoder_reaped','microphone_stream_closed']):self.retiring=None
    def frame(self,actor,session):
        with self.lock:runner,epoch=self._check(actor,session)
        try:(jpeg,sequence,received_at,utc),body_session,boot=runner.latest_frame()
        except (RuntimeError,AttributeError):raise AgentError(409,'Fresh live camera frame unavailable') from None
        if not isinstance(jpeg,bytes) or len(jpeg)>512000 or not jpeg.startswith(b'\xff\xd8') or self.clock()-received_at>2:raise AgentError(409,'Invalid or stale frame')
        self._current(actor,session,runner,epoch)
        return jpeg,{'session':session,'boot_id':boot,'sequence':sequence,'pc_receipt_utc':utc,'age_seconds':max(0,self.clock()-received_at),'synthetic':bool(getattr(runner,'synthetic',False))}
    def face(self,actor,session,expression='neutral',action='show'):
        if expression not in ('neutral','happy','angry','sad','doubt','sleepy') or action not in ('show','hide','replay','idle'):raise AgentError(400,'Unsupported native face intent')
        with self.lock:runner,epoch=self._check(actor,session)
        result=runner.body_command('face',expression=expression,action=action);self._current(actor,session,runner,epoch);return result
    def pan(self,actor,session,direction,seconds=.3):
        if direction not in ('left','right') or type(seconds) not in (int,float) or not math.isfinite(seconds) or not .1<=seconds<=5:raise AgentError(400,'Pan left/right for0.1..5seconds required')
        with self.lock:runner,epoch=self._check(actor,session)
        end=self.clock()+seconds
        try:
            while self.clock()<end:
                self._current(actor,session,runner,epoch);runner.body_command('input_button',button=direction,action='hold');time.sleep(.08)
        finally:
            try:
                self._current(actor,session,runner,epoch);runner.body_command('input_button',action='release')
            except (AgentError,RuntimeError):pass # Old lease must never release a replacement owner's key.
        return {'commanded_direction':direction,'commanded_seconds':seconds,'physical_motion_confirmed':False}
    def _wait_job(self,job,actor,session,runner,epoch,timeout=30):
        try:
            with self.lock:
                self._current(actor,session,runner,epoch)
                if self.job:raise AgentError(409,'A speech job is already active')
                self.job=job
        except Exception:
            job.cancel();raise
        deadline=self.clock()+timeout
        try:
            while self.clock()<deadline:
                self._current(actor,session,runner,epoch);result=job.poll()
                if result is not None:
                    value,error=result
                    if error:raise AgentError(503,'Local speech worker unavailable')
                    return value
                time.sleep(.02)
            raise AgentError(503,'Local speech worker timeout')
        finally:
            job.cancel()
            with self.lock:
                if self.job is job:self.job=None
    def say(self,actor,session,text):
        # User's night policy is checked BEFORE synthesis or any body access.
        if quiet_policy(self.policy):raise AgentError(423,'Audio playback disabled by quiet-night policy')
        if not isinstance(text,str) or not text.strip() or len(text)>80 or any(ord(c)<32 for c in text):raise AgentError(400,'One short speech phrase up to80characters required')
        with self.lock:runner,epoch=self._check(actor,session)
        raw=self._wait_job(self.speech.synthesize(text),actor,session,runner,epoch)
        pcm=speaker_pcm(raw)
        self._current(actor,session,runner,epoch)
        if quiet_policy(self.policy):raise AgentError(423,'Audio playback disabled by quiet-night policy')
        result=runner.body_command('play_pcm',pcm=pcm)
        self._current(actor,session,runner,epoch);return {'playback':result,'hardware_audible':False,'model':'local-kokoro','seconds':len(pcm)/32000}
    def wait_speaker(self,actor,session,timeout=14):
        with self.lock:runner,epoch=self._check(actor,session)
        until=self.clock()+timeout
        while self.clock()<until:
            self._current(actor,session,runner,epoch)
            if quiet_policy(self.policy):
                runner.body_command('speaker_cancel');raise AgentError(423,'Quiet policy interrupted playback')
            state=runner.body_command('speaker_status')
            if not state.get('active'):
                if state.get('phase')=='finished':return state
                raise AgentError(503,'Camera playback refused, cancelled or requires recovery')
            time.sleep(.1)
        runner.body_command('speaker_cancel');raise AgentError(503,'Camera playback completion timeout')

    def listen(self,actor,session,seconds=3):
        if type(seconds) is not int or not 1<=seconds<=8:raise AgentError(400,'Listen1..8seconds required')
        with self.lock:runner,epoch=self._check(actor,session)
        cursor=runner.status().get('audio_sequence',0);end=self.clock()+seconds;pcm=bytearray()
        while self.clock()<end:
            self._current(actor,session,runner,epoch)
            try:chunk,meta=runner.audio_tail(cursor)
            except (RuntimeError,AttributeError):raise AgentError(409,'Live microphone PCM unavailable') from None
            if meta.get('history_gap'):raise AgentError(409,'Microphone sequence gap')
            next_cursor=meta.get('latest_sequence',cursor)
            if type(next_cursor) is not int or next_cursor<cursor:raise AgentError(409,'Stale microphone sequence')
            cursor=next_cursor
            pcm.extend(chunk)
            if len(pcm)>256000:raise AgentError(503,'Microphone buffer limit')
            time.sleep(.05)
        if len(pcm)<11200:raise AgentError(409,'Not enough fresh microphone audio')
        if not any(pcm):raise AgentError(409,'Microphone delivered only digital silence')
        value=self._wait_job(self.speech.transcribe(bytes(pcm)),actor,session,runner,epoch)
        try:result=json.loads(value)
        except (ValueError,TypeError):raise AgentError(503,'Invalid local transcript') from None
        return {'text':result['text'],'provider':result.get('provider'),'session':session,'audio_bytes':len(pcm),'synthetic':bool(getattr(runner,'synthetic',False))}
    def close(self):
        with self.lock:self.closed=True;actor,session=self.actor,self.session
        if self.active:self.stop(actor,session)
