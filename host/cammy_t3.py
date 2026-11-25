"""Supported installed T3 CLI adapter. Existing threads only, no DB/provider-token access."""
import fcntl,hashlib,json,os,re,secrets,time,uuid
from pathlib import Path
from cammy_agent_core import AgentError,ROOT,thread_ok
from cammy_conversation import ProcessJob

class T3Cli:
    def __init__(self,cli=None,job_factory=ProcessJob):
        self.cli=str(cli or os.environ.get('T3CODE_CLI_PATH') or Path.home()/'.t3/bin/t3');self.job_factory=job_factory
        if not Path(self.cli).is_absolute():raise ValueError('Absolute installed T3 CLI required')
    def run(self,args,data=b'',timeout=15):
        job=self.job_factory([self.cli,*args],data,524288,timeout);end=time.monotonic()+timeout+1
        try:
            while time.monotonic()<end:
                result=job.poll()
                if result is not None:
                    raw,error=result
                    if error:raise AgentError(503,'T3 CLI request failed or delivery is uncertain')
                    try:return json.loads(raw)
                    except (ValueError,TypeError):raise AgentError(503,'T3 CLI returned invalid JSON') from None
                time.sleep(.02)
            raise AgentError(503,'T3 CLI request timeout; do not automatically resend')
        finally:job.cancel()
    @staticmethod
    def thread_id(value):
        if not thread_ok(value):raise AgentError(400,'Existing T3 thread ID required')
        return value
    def read(self,thread,messages=8):
        self.thread_id(thread)
        if type(messages) is not int or not 1<=messages<=20:raise AgentError(400,'Read1..20messages')
        result=self.run(['thread','show',thread,'--messages',str(messages),'--json'])
        if not isinstance(result,dict) or result.get('thread',{}).get('threadId')!=thread or not isinstance(result.get('messages'),list):raise AgentError(503,'T3 thread identity/shape differs')
        return result
    def list(self,project):
        if not isinstance(project,str) or not project or len(project)>1024:raise AgentError(400,'Explicit project path/id required')
        return self.run(['thread','list','--project',project,'--json'])
    def send(self,thread,text):
        self.thread_id(thread)
        if not isinstance(text,str) or not 1<=len(text)<=4000:raise AgentError(400,'Bounded user text required')
        value=self.run(['thread','send',thread,'-','--json'],text.encode())
        if not isinstance(value,dict) or value.get('threadId')!=thread or not isinstance(value.get('runId'),str):raise AgentError(503,'T3 acknowledgement uncertain; reconcile instead of resending')
        return value

class ThreadOutbox:
    """Durable request identity. Ambiguous CLI sends are never auto-retried."""
    def __init__(self,path,backend):
        self.path=Path(path);self.backend=backend;self.path.mkdir(parents=True,exist_ok=True,mode=0o700)
        info=self.path.lstat()
        if not __import__('stat').S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o777!=0o700:raise ValueError('Private outbox directory required')
    def _path(self,request):
        if not isinstance(request,str) or not re.fullmatch('[a-f0-9]{32}',request):raise AgentError(400,'Request id must be32hexcharacters')
        return self.path/(request+'.json')
    def _write(self,path,value):
        temp=self.path/('.'+secrets.token_hex(12))
        try:
            with temp.open('x') as f:os.chmod(temp,0o600);json.dump(value,f);f.flush();os.fsync(f.fileno())
            os.replace(temp,path)
            fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
        finally:temp.unlink(missing_ok=True)
    def send(self,request,thread,text,context=None):
        target=self._path(request);self.backend.thread_id(thread)
        if not isinstance(text,str) or not text.strip() or len(text)>2000:raise AgentError(400,'One bounded spoken utterance required')
        context=context or {}
        digest=hashlib.sha256((thread+'\0'+text+'\0'+json.dumps(context,sort_keys=True)).encode()).hexdigest()
        lock=os.open(self.path/'lock',os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise AgentError(409,'Another utterance is being delivered') from None
            if target.exists():
                if target.is_symlink():raise AgentError(503,'Invalid outbox record')
                prior=json.loads(target.read_text())
                if prior['digest']!=digest:raise AgentError(409,'Request id already belongs to different content')
                return prior
            # Read first: a missing/wrong thread must not become a new thread.
            self.backend.read(thread,1)
            record={'request_id':request,'thread_id':thread,'digest':digest,'phase':'sending','created_at':time.time(),'run_id':None,'context':context}
            self._write(target,record)
            message='[Cammy voice request '+request+']\n'+text+'\n\n[Cammy routing: thread_id='+thread+'. Use Cammy MCP tools with this explicit thread_id for fresh camera context or a short spoken reply. No image is attached by this CLI message. Check quiet/session state before output; do not assume the camera is active.]'
            try:
                ack=self.backend.send(thread,message)
                record.update(phase='accepted',run_id=ack['runId'],link=ack.get('link'),delivery=ack.get('delivery'))
            except Exception:
                record.update(phase='delivery-uncertain')
            self._write(target,record);return record
        finally:os.close(lock)
    def poll(self,request):
        target=self._path(request)
        if not target.is_file() or target.is_symlink():raise AgentError(404,'Unknown utterance request')
        record=json.loads(target.read_text())
        if record['phase']!='accepted':return record
        latest=self.backend.read(record['thread_id'],20);thread=latest['thread'];run=record['run_id']
        result={**record,'thread_status':thread['status'],'waiting_for_user':thread.get('pendingRequestCount',0)>0,'reply':None}
        if thread.get('latestRunId')!=run:return {**result,'phase':'queued-or-superseded'}
        if thread.get('activeRunId')==run or not thread.get('settled'):return {**result,'phase':'working'}
        if thread['status']!='completed':return {**result,'phase':'waiting' if result['waiting_for_user'] else 'failed'}
        replies=[m['text'] for m in latest['messages'] if m.get('runId')==run and m.get('role')=='assistant' and isinstance(m.get('text'),str)]
        if not replies:return {**result,'phase':'completed-reply-unavailable'}
        text='\n'.join(replies)
        return {**result,'phase':'completed','reply':text[:12000],'reply_truncated':len(text)>12000}
