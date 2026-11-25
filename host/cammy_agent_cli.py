"""CLI for the same narrow control API exposed by Cammy's MCP tools."""
import argparse,json,os,secrets,sys
from pathlib import Path
from cammy_agent_client import AgentClient
from cammy_agent_core import AgentError,ROOT

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--actor');p.add_argument('--session');p.add_argument('--endpoint',default='http://127.0.0.1:8791');p.add_argument('--token-file',type=Path)
    s=p.add_subparsers(dest='op',required=True)
    for op in ['status','device','stop','frame','threads']:s.add_parser(op)
    o=s.add_parser('open');o.add_argument('--seconds',type=int,default=30);o.add_argument('--sensing',action='store_true')
    o=s.add_parser('pan');o.add_argument('direction',choices=['left','right']);o.add_argument('--seconds',type=float,default=.3)
    o=s.add_parser('face');o.add_argument('--expression',default='neutral');o.add_argument('--action',default='show')
    o=s.add_parser('say');o.add_argument('text')
    o=s.add_parser('listen');o.add_argument('--seconds',type=int,default=3)
    o=s.add_parser('thread-select');o.add_argument('thread_id')
    o=s.add_parser('thread-send');o.add_argument('text');o.add_argument('--request-id',required=True)
    for op in ['thread-result','thread-speak']:
        o=s.add_parser(op);o.add_argument('request_id')
    o=s.add_parser('voice-turn');o.add_argument('--seconds',type=int,default=3);o.add_argument('--request-id',required=True)
    a=p.parse_args();c=AgentClient(a.actor,a.endpoint,a.token_file)
    body={}
    if a.op=='open':body={'seconds':a.seconds,'sensing':a.sensing}
    elif a.op=='pan':body={'direction':a.direction,'seconds':a.seconds}
    elif a.op=='face':body={'expression':a.expression,'action':a.action}
    elif a.op=='say':body={'text':a.text}
    elif a.op=='listen':body={'seconds':a.seconds}
    if a.op=='thread-select':body={'thread_id':a.thread_id}
    elif a.op=='thread-send':body={'text':a.text,'request_id':a.request_id}
    elif a.op in ('thread-result','thread-speak'):body={'request_id':a.request_id}
    elif a.op=='voice-turn':body={'seconds':a.seconds,'request_id':a.request_id}
    try:
        session=c.session(a.session) if a.op not in ('status','device','open','threads','thread-select','thread-result') else None
        if a.op=='frame':
            raw,metadata=c.request('frame.jpg',session=session,image=True)
            folder=ROOT/'private/agent-control/frames';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            target=folder/(secrets.token_hex(12)+'.jpg')
            with target.open('xb') as f:os.chmod(target,0o600);f.write(raw)
            value={'file':str(target),'metadata':metadata}
        else:value=c.request(a.op,None if a.op in ('status','device','threads') else body,session)
        print(json.dumps(value));return 0
    except AgentError as e:print(json.dumps({'error':str(e),'code':e.code}));return 1
if __name__=='__main__':sys.exit(main())
