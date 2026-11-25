"""Narrow local agent API client; no arbitrary URLs, file tools or body shells."""
import json,os,re,urllib.request,urllib.error,copy,uuid
from pathlib import Path
from urllib.parse import urlsplit
from cammy_agent_core import AgentError,ROOT,thread_actor
from cammy_agent_service import private_token
class AgentClient:
    def __init__(self,actor=None,endpoint='http://127.0.0.1:8791',token_file=None,timeout=40):
        u=urlsplit(endpoint)
        if u.scheme!='http' or u.hostname!='127.0.0.1' or u.path or u.username or u.password or u.query or u.fragment:raise ValueError('Exact loopback agent endpoint required')
        self.endpoint=endpoint;self.actor=actor or os.environ.get('T3CODE_THREAD_ID','local-console');self.timeout=timeout
        if not re.fullmatch('[A-Za-z0-9_-]{1,64}',self.actor):
            try:self.actor=thread_actor(self.actor)
            except AgentError:raise ValueError('Invalid agent identity') from None
        if not re.fullmatch('[A-Za-z0-9_-]{1,64}',self.actor):raise ValueError('Invalid agent identity')
        self.token=private_token(token_file or ROOT/'private/agent-control/service.token');self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def for_thread(self,thread_id):
        other=copy.copy(self);other.actor=thread_actor(thread_id);return other
    def request(self,operation,body=None,session=None,image=False):
        allowed={'status','device','frame.jpg','open','stop','pan','face','say','listen','threads','thread-select','thread-send','thread-result','thread-speak','voice-turn'}
        if operation not in allowed:raise ValueError('Unsupported agent operation')
        method='GET' if operation in ('status','device','frame.jpg','threads') else 'POST'
        if method=='POST' and body is None:body={}
        headers={'Authorization':'Bearer '+self.token,'X-Cammy-Agent':self.actor,'Content-Type':'application/json'}
        if session:headers['X-Cammy-Session']=session
        request=urllib.request.Request(self.endpoint+'/v1/'+operation,data=None if body is None else json.dumps(body,allow_nan=False).encode(),headers=headers,method=method)
        try:
            with self.opener.open(request,timeout=self.timeout) as r:
                raw=r.read(512001)
                if len(raw)>512000:raise AgentError(502,'Agent reply limit')
                if image:
                    if r.headers.get_content_type()!='image/jpeg':raise AgentError(502,'JPEG frame required')
                    return raw,json.loads(r.headers['X-Cammy-Frame'])
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            with e:
                try:message=json.loads(e.read(4096)).get('error','Agent operation refused')
                except (ValueError,AttributeError):message='Agent operation refused'
                raise AgentError(e.code,message) from None
    def session(self,explicit=None):
        value=explicit or self.request('status')['owned_session']
        if not value:raise AgentError(409,'Open an owned session first')
        return value
