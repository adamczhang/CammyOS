"""Small agent client for the official Cammy Reachy app's finite turn inbox.

No model provider is selected. Agents poll a recognized turn, inspect the existing
live frame endpoint, and reply. Constructors never enable sensing/playback.
"""
import json
import re
import urllib.request


class CammyConversation:
    def __init__(self):
        self.base='http://127.0.0.1:8801'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.opener.open(self.base+'/',timeout=2) as response:html=response.read(65536).decode()
        match=re.search(r'data-token="([A-Za-z0-9_-]{32,80})"',html)
        if match is None:raise RuntimeError('App token unavailable')
        self.token=match[1];self.session=None

    def _request(self,path,value=None):
        request=urllib.request.Request(self.base+'/api/conversation/'+path,
            data=None if value is None else json.dumps(value).encode(),
            headers={'X-Cammy-Lab':self.token,'Content-Type':'application/json'})
        with self.opener.open(request,timeout=3) as response:raw=response.read(16385)
        if len(raw)>16384:raise RuntimeError('Response limit')
        return json.loads(raw)

    def status(self):return self._request('status')
    def start(self,seconds=60,speaker=False):
        value=self._request('start',{'seconds':seconds,'speaker':speaker});self.session=value['session'];return value
    def listen(self,seconds=8):return self._request('listen',{'session':self.session,'seconds':seconds})
    def release(self):return self._request('finish',{'session':self.session})
    def turn(self):
        value=self.status()
        if value.get('session')!=self.session:raise RuntimeError('Conversation session changed')
        return value.get('inbox')
    def reply(self,turn,text):
        return self._request('reply',{'session':self.session,'turn':turn,'text':text})
    def stop(self):return self._request('stop',{'session':self.session})
