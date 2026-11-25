"""Reachy app proxy to the shared agent service; no browser-visible service key."""
import re,threading
from cammy_agent_client import AgentClient
from cammy_agent_core import AgentError
class AgentProxy:
    def __init__(self,client_factory=AgentClient):self.client_factory=client_factory;self.owned={};self.lock=threading.RLock()
    def call(self,operation,thread_id,body=None,session=None):
        if not isinstance(thread_id,str) or not re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',thread_id):raise AgentError(400,'Choose an existing T3 thread UUID')
        fields={'status':set(),'device':set(),'threads':set(),'frame':set(),'open':{'seconds','sensing'},'stop':set(),'pan':{'direction','seconds'},'face':{'expression','action'},'say':{'text'},'listen':{'seconds'},'thread-select':set(),'thread-send':{'text','request_id'},'thread-result':{'request_id'},'thread-speak':{'request_id'},'voice-turn':{'seconds','request_id'}}
        body={} if body is None else body
        if operation not in fields or not isinstance(body,dict) or body.keys()-fields[operation]:raise AgentError(400,'Unknown agent operation/field')
        c=self.client_factory(actor=thread_id)
        if operation=='thread-select':body={'thread_id':thread_id}
        if operation=='frame':return c.request('frame.jpg',session=c.session(session),image=True)
        bound=None if operation in ('status','device','threads','open','thread-select','thread-result') else c.session(session)
        value=c.request(operation,None if operation in ('status','device','threads') else body,bound)
        with self.lock:
            if operation=='open':self.owned[thread_id]=value['owned_session']
            if operation=='stop' and self.owned.get(thread_id)==bound:self.owned.pop(thread_id,None)
        return value
    def close(self):
        with self.lock:owned=dict(self.owned);self.owned.clear()
        for actor,session in owned.items():
            try:self.client_factory(actor=actor).request('stop',{},session)
            except Exception:pass # Finite service window/body lease remains the fallback.
