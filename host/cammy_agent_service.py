"""Loopback-only shared agent control API. Credentials never returned by status."""
import argparse,hmac,json,os,secrets,signal,stat,sys,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from cammy_agent_core import AgentController,AgentError,ROOT

def private_token(path,create=False):
    p=Path(path)
    if create and not p.exists():
        p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with p.open('x') as f:os.chmod(p,0o600);f.write(secrets.token_urlsafe(32)+'\n')
    s=p.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1 or s.st_size>256:raise ValueError('Private credential file required')
    token=p.read_text().strip()
    if not 32<=len(token)<=128:raise ValueError('Invalid credential size')
    return token

def make_server(core,token,address=('127.0.0.1',8791),bridge=None):
    if address[0]!='127.0.0.1':raise ValueError('Loopback listener required')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def reply(self,code,value,metadata=None):
            raw=value if isinstance(value,bytes) else json.dumps(value,allow_nan=False).encode()
            self.send_response(code);self.send_header('Content-Type','image/jpeg' if metadata else 'application/json')
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            if metadata:self.send_header('X-Cammy-Frame',json.dumps(metadata))
            self.end_headers()
            try:self.wfile.write(raw)
            except OSError:pass
        def handle_api(self):
            self.connection.settimeout(3)
            if self.headers.get('Origin') is not None or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):self.reply(403,{'error':'Local non-browser API required'});return
            auth=self.headers.get_all('Authorization',[]);actors=self.headers.get_all('X-Cammy-Agent',[]);sessions=self.headers.get_all('X-Cammy-Session',[])
            if len(auth)!=1 or not hmac.compare_digest(auth[0].encode(),('Bearer '+token).encode()):self.reply(401,{'error':'Agent credential required'});return
            if len(actors)!=1 or not core.actor_ok(actors[0]) or len(sessions)>1:self.reply(400,{'error':'One valid actor/session header required'});return
            actor=actors[0];session=sessions[0] if sessions else None
            try:
                if self.command=='GET':
                    if self.path=='/v1/status':self.reply(200,core.status(actor));return
                    if self.path=='/v1/threads':
                        if not bridge:raise AgentError(501,'Thread bridge not configured')
                        self.reply(200,bridge.threads());return
                    if self.path=='/v1/device':self.reply(200,core.device_status());return
                    if self.path=='/v1/frame.jpg':raw,meta=core.frame(actor,session);self.reply(200,raw,meta);return
                    raise AgentError(404,'Unknown read operation')
                operations={'open':{'seconds','sensing'},'stop':set(),'pan':{'direction','seconds'},'face':{'expression','action'},'say':{'text'},'listen':{'seconds'},'thread-select':{'thread_id'},'thread-send':{'text','request_id'},'thread-result':{'request_id'},'thread-speak':{'request_id'},'voice-turn':{'seconds','request_id'}}
                operation=self.path.removeprefix('/v1/')
                if self.path!='/v1/'+operation or operation not in operations:raise AgentError(404,'Unknown control operation')
                lengths=self.headers.get_all('Content-Length',[])
                if self.headers.get('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit() or not 0<int(lengths[0])<=4096:raise AgentError(413,'Bounded JSON body required')
                if self.headers.get('Content-Type')!='application/json':raise AgentError(415,'JSON required')
                raw=self.rfile.read(int(lengths[0]))
                if len(raw)!=int(lengths[0]):raise AgentError(400,'Incomplete body')
                def pairs(rows):
                    result={}
                    for key,value in rows:
                        if key in result:raise ValueError('Duplicate key')
                        result[key]=value
                    return result
                value=json.loads(raw,object_pairs_hook=pairs)
                if not isinstance(value,dict) or value.keys()-operations[operation]:raise AgentError(400,'Unknown control field')
                if operation.startswith('thread-') or operation=='voice-turn':
                    if not bridge:raise AgentError(501,'Thread bridge not configured')
                    functions={'thread-select':bridge.select,'thread-send':bridge.send,'thread-result':bridge.result,'thread-speak':bridge.speak,'voice-turn':bridge.voice}
                    self.reply(200,functions[operation](actor,**value) if operation in ('thread-select','thread-result') else functions[operation](actor,session,**value));return
                function=getattr(core,operation)
                self.reply(200,function(actor,**value) if operation=='open' else function(actor,session,**value))
            except AgentError as e:self.reply(e.code,{'error':str(e)})
            except (ValueError,TypeError):self.reply(400,{'error':'Invalid control value'})
            except Exception:self.reply(503,{'error':'Operation unavailable; inspect session/device status'})
        do_GET=do_POST=handle_api
    server=ThreadingHTTPServer(address,Handler);server.daemon_threads=True;return server

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8791);p.add_argument('--token-file',type=Path,default=ROOT/'private/agent-control/service.token');p.add_argument('--policy',type=Path,default=ROOT/'private/agent-control/policy.json');args=p.parse_args()
    token=private_token(args.token_file,create=True);core=AgentController(args.policy)
    from cammy_thread_bridge import ThreadBridge
    server=make_server(core,token,('127.0.0.1',args.port),ThreadBridge(core))
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:server.serve_forever()
    finally:core.close();server.server_close()
if __name__=='__main__':main()
