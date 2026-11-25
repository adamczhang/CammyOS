"""Supported T3 CLI shape and outbox semantics; never messages a real thread."""
import json,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(ROOT/'sdk'))
from cammy_t3 import T3Cli,ThreadOutbox
from cammy_agent_core import AgentError
ID='11111111-2222-3333-4444-555555555555';RID='run1';REQ='a'*32
class Fake:
    thread_id=staticmethod(T3Cli.thread_id)
    def __init__(self):self.sent=[];self.fail=False;self.status='completed';self.latest=RID;self.active=None;self.settled=True;self.pending=0
    def read(self,thread,messages=8):return {'thread':{'threadId':thread,'latestRunId':self.latest,'activeRunId':self.active,'status':self.status,'settled':self.settled,'pendingRequestCount':self.pending},'messages':[{'role':'assistant','runId':RID,'text':'Actual matching reply'},{'role':'assistant','runId':'other','text':'Wrong task'}]}
    def send(self,thread,text):
        self.sent.append((thread,text))
        if self.fail:raise OSError('unknown delivery')
        return {'threadId':thread,'runId':RID,'delivery':'queued','link':'t3://fixture'}
class Tests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.backend=Fake();self.box=ThreadOutbox(Path(self.tmp.name)/'outbox',self.backend)
    def tearDown(self):self.tmp.cleanup()
    def test_duplicate_exact_utterance_not_resent_and_id_conflict_refused(self):
        first=self.box.send(REQ,ID,'Hello');self.assertEqual(first,self.box.send(REQ,ID,'Hello'));self.assertEqual(len(self.backend.sent),1)
        with self.assertRaises(AgentError):self.box.send(REQ,ID,'Different')
        self.assertIn('[Cammy voice request '+REQ+']',self.backend.sent[0][1])
    def test_uncertain_delivery_survives_restart_without_retry(self):
        self.backend.fail=True;r=self.box.send(REQ,ID,'Hello');self.assertEqual(r['phase'],'delivery-uncertain')
        box=ThreadOutbox(self.box.path,self.backend);box.send(REQ,ID,'Hello');self.assertEqual(len(self.backend.sent),1)
    def test_only_matching_completed_run_is_spoken(self):
        self.box.send(REQ,ID,'Hello');r=self.box.poll(REQ);self.assertEqual(r['phase'],'completed');self.assertEqual(r['reply'],'Actual matching reply')
        self.backend.active=RID;self.backend.settled=False;r=self.box.poll(REQ);self.assertEqual(r['phase'],'working');self.assertIsNone(r['reply'])
        self.backend.latest='new';r=self.box.poll(REQ);self.assertIsNone(r['reply']);self.assertEqual(r['phase'],'queued-or-superseded')
    def test_waiting_failed_and_missing_reply_are_not_completion(self):
        self.box.send(REQ,ID,'Hello');self.backend.status='waiting';self.backend.pending=1
        self.assertEqual(self.box.poll(REQ)['phase'],'waiting');self.backend.status='failed';self.backend.pending=0;self.assertEqual(self.box.poll(REQ)['phase'],'failed')
    def test_invalid_thread_and_outbox_path_never_sends(self):
        for request,thread in [('../escape',ID),(REQ,'not-a-thread')]:
            with self.assertRaises(AgentError):self.box.send(request,thread,'Hello')
        self.assertFalse(self.backend.sent)
    def test_cli_uses_exact_public_commands_stdin_and_bounded_jobs(self):
        calls=[]
        class Job:
            def __init__(self,command,data,limit,timeout):self.command=command;calls.append((command,data,limit,timeout))
            def poll(self):
                value={'thread':{'threadId':ID},'messages':[]} if self.command[2]=='show' else {'threadId':ID,'runId':RID,'delivery':'queued'}
                return json.dumps(value).encode(),None
            def cancel(self):pass
        cli=T3Cli('/supported/t3',Job);cli.read(ID,3);cli.send(ID,'Literal `$(no shell)`')
        self.assertEqual(calls[0][0],['/supported/t3','thread','show',ID,'--messages','3','--json'])
        self.assertEqual(calls[1][0],['/supported/t3','thread','send',ID,'-','--json']);self.assertEqual(calls[1][1],b'Literal `$(no shell)`');self.assertNotIn('--wait',calls[1][0])
