"""External user turns -> selected EXISTING T3 thread. No self-delegation tools."""
import threading,json,os,re
from cammy_t3 import T3Cli,ThreadOutbox
from cammy_agent_core import AgentError,ROOT,quiet_policy,thread_actor
class ThreadBridge:
    def __init__(self,core,backend=None,outbox=None,project=None):
        self.core=core;self.backend=backend or T3Cli();self.outbox=outbox or ThreadOutbox(ROOT/'private/agent-control/outbox',self.backend)
        self.project=str(project or ROOT.parent/'Cammy Bot');self.routes={};self.capturing=set();self.lock=threading.RLock()
    def threads(self):return self.backend.list(self.project)
    def select(self,actor,thread_id):
        self.backend.thread_id(thread_id)
        if actor!=thread_actor(thread_id):raise AgentError(409,'Controller actor must match selected T3 thread')
        value=self.backend.read(thread_id,1)['thread']
        with self.lock:self.routes[actor]=thread_id
        return {k:value.get(k) for k in ['threadId','projectId','title','link','providerInstanceId','model','status']}
    def send(self,actor,session,text,request_id):
        with self.lock:thread=self.routes.get(actor)
        if not thread:raise AgentError(409,'Select an existing T3 thread first')
        with self.core.lock:runner,epoch=self.core._check(actor,session)
        # Text is the actual user utterance. Camera access is a separate explicit
        # MCP tool call by that same T3 actor, not a claim that text contains video.
        self.core._current(actor,session,runner,epoch)
        return self.outbox.send(request_id,thread,text,context={'actor':actor,'session':session,'epoch':epoch})
    def result(self,actor,request_id):
        path=self.outbox._path(request_id)
        if not path.is_file() or path.is_symlink():raise AgentError(404,'Unknown utterance')
        import json
        record=json.loads(path.read_text())
        if record.get('context',{}).get('actor')!=actor:raise AgentError(409,'Utterance owner required')
        value=self.outbox.poll(request_id);context=value['context'];allowed=False
        with self.core.lock:
            try:
                runner,epoch=self.core._check(actor,context['session'])
                allowed=epoch==context['epoch'] and self.routes.get(actor)==value['thread_id']
            except AgentError:pass
        return {**value,'playback_allowed':allowed,'auto_playback':False}
    def speak(self,actor,session,request_id):
        if quiet_policy(self.core.policy):raise AgentError(423,'Audio playback disabled by quiet-night policy')
        value=self.result(actor,request_id)
        if value['phase']!='completed' or not value['playback_allowed'] or value['context']['session']!=session:raise AgentError(409,'Completed current-session reply required')
        # Consume before output; a lost HTTP acknowledgement must never replay it.
        claim=self.outbox.path/('spoken-'+request_id+'.json')
        try:fd=os.open(claim,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
        except FileExistsError:raise AgentError(409,'Spoken reply already attempted; no automatic replay') from None
        with os.fdopen(fd,'w') as f:json.dump({'actor':actor,'session':session,'request_id':request_id,'attempted':True},f);f.flush();os.fsync(f.fileno())
        text=re.sub(r'```.*?```',' Code details are in the T3 thread. ',value['reply'],flags=re.S)
        text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',text)
        text=re.sub(r'https?://\S+','',text);text=re.sub(r'[#*_`>]+','',text);text=' '.join(text.split())
        truncated=len(text)>160
        if truncated:text=text[:160].rsplit(' ',1)[0]+'. More details are in the T3 thread.'
        if not text:raise AgentError(409,'Reply contains no speakable text')
        chunks=[]
        while text:
            part=text[:70]
            if len(text)>70 and ' ' in part:part=part.rsplit(' ',1)[0]
            chunks.append(part);text=text[len(part):].lstrip()
        for part in chunks:
            self.core.say(actor,session,part);self.core.wait_speaker(actor,session)
        return {'speech_attempted':True,'chunks':len(chunks),'excerpt':truncated,'hardware_audible':False,'full_reply_remains_in_t3':True}

    def voice(self,actor,session,seconds=3,request_id=None):
        if not isinstance(request_id,str):raise AgentError(400,'Stable request id required for voice turn')
        # Refuse duplicate capture/delivery before listening again.
        path=self.outbox._path(request_id)
        if path.exists():return self.result(actor,request_id)
        with self.lock:
            if actor not in self.routes:raise AgentError(409,'Select a thread before listening')
        with self.lock:
            if actor in self.capturing:raise AgentError(409,'A voice utterance is already being captured')
            self.capturing.add(actor)
        try:
            transcript=self.core.listen(actor,session,seconds)
            value=self.send(actor,session,transcript['text'],request_id)
            return {**value,'transcript':transcript,'camera_context':'Available through Cammy MCP tools; no image attachment sent by CLI'}
        finally:
            with self.lock:self.capturing.discard(actor)
