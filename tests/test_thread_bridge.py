"""Thread selection and live-session correlation through the actual PC HTTP API."""
import json,sys,threading,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(ROOT/'sdk'))
from tests import test_agent_control as fixtures
from tests.test_t3_adapter import Fake,ID,REQ
from cammy_thread_bridge import ThreadBridge
from cammy_t3 import ThreadOutbox
from cammy_agent_service import make_server
from cammy_agent_client import AgentClient
from cammy_agent_core import AgentError
class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AgentTests();self.f.setUp();self.backend=Fake();self.backend.list=lambda project:[]
        self.bridge=ThreadBridge(self.f.core,self.backend,ThreadOutbox(self.f.root/'outbox',self.backend))
    def tearDown(self):self.f.tearDown()
    def test_thread_actor_must_match_and_no_send_without_owned_session(self):
        with self.assertRaises(AgentError):self.bridge.select('wrong',ID)
        self.bridge.select(ID,ID)
        with self.assertRaises(AgentError):self.bridge.send(ID,'stale','Hello',REQ)
        self.assertEqual(self.backend.sent,[])
    def test_live_turn_result_keeps_context_but_stop_disables_playback(self):
        self.bridge.select(ID,ID);sid=self.f.core.open(ID,30,True)['owned_session']
        self.bridge.send(ID,sid,'Hello',REQ);value=self.bridge.result(ID,REQ)
        self.assertTrue(value['playback_allowed']);self.assertFalse(value['auto_playback']);self.assertEqual(value['reply'],'Actual matching reply')
        self.f.core.stop(ID,sid);self.assertFalse(self.bridge.result(ID,REQ)['playback_allowed'])
        with self.assertRaises(AgentError):self.bridge.result('other',REQ)
    def test_duplicate_voice_turn_does_not_capture_or_send_twice(self):
        self.bridge.select(ID,ID);sid=self.f.core.open(ID,30,True)['owned_session']
        self.bridge.voice(ID,sid,1,REQ);first=list(self.f.speech.calls)
        self.bridge.voice(ID,sid,1,REQ);self.assertEqual(first,self.f.speech.calls);self.assertEqual(len(self.backend.sent),1)
    def prepare_reply(self):
        self.bridge.select(ID,ID);sid=self.f.core.open(ID,30,True)['owned_session']
        self.bridge.send(ID,sid,'Hello',REQ);return sid
    def test_night_policy_refuses_reply_before_claim_model_or_device(self):
        sid=self.prepare_reply()
        with self.assertRaises(AgentError) as error:self.bridge.speak(ID,sid,REQ)
        self.assertEqual(error.exception.code,423);self.assertEqual(self.f.speech.calls,[])
        self.assertEqual(self.f.runners[-1].commands,[])
        self.assertFalse((self.bridge.outbox.path/('spoken-'+REQ+'.json')).exists())
    def test_correlated_reply_speaks_once_without_replay_after_ack_loss(self):
        self.f.policy.write_text('{"quiet":false}');sid=self.prepare_reply();runner=self.f.runners[-1]
        original=runner.body_command
        runner.body_command=lambda op,**kw:({'active':False,'phase':'finished'} if op=='speaker_status' else original(op,**kw))
        value=self.bridge.speak(ID,sid,REQ)
        self.assertTrue(value['speech_attempted']);self.assertFalse(value['hardware_audible'])
        self.assertEqual(self.f.speech.calls,[('tts','Actual matching reply')])
        with self.assertRaises(AgentError):self.bridge.speak(ID,sid,REQ)
        self.assertEqual(len(self.f.speech.calls),1)
    def test_pending_reply_and_expired_session_cannot_claim_speech(self):
        self.f.policy.write_text('{"quiet":false}');sid=self.prepare_reply();self.backend.settled=False
        with self.assertRaises(AgentError):self.bridge.speak(ID,sid,REQ)
        self.backend.settled=True;self.f.core.stop(ID,sid)
        with self.assertRaises(AgentError):self.bridge.speak(ID,sid,REQ)
        self.assertEqual(self.f.speech.calls,[])
        self.assertFalse((self.bridge.outbox.path/('spoken-'+REQ+'.json')).exists())
    def test_stop_between_chunks_drops_remaining_reply_and_prevents_replay(self):
        self.f.policy.write_text('{"quiet":false}');sid=self.prepare_reply()
        read=self.backend.read
        def long_reply(*args):
            value=read(*args);value['messages'][0]['text']='A clear project update with several useful details. '*6;return value
        self.backend.read=long_reply
        self.f.core.wait_speaker=lambda actor,session:self.f.core.stop(actor,session)
        with self.assertRaises(AgentError):self.bridge.speak(ID,sid,REQ)
        self.assertEqual(len(self.f.speech.calls),1)
        self.assertTrue((self.bridge.outbox.path/('spoken-'+REQ+'.json')).exists())
    def test_http_select_send_result_and_no_automatic_audio(self):
        token='x'*48;path=self.f.root/'token';path.write_text(token);path.chmod(0o600)
        server=make_server(self.f.core,token,('127.0.0.1',0),self.bridge);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            c=AgentClient(ID,'http://127.0.0.1:'+str(server.server_port),path)
            c.request('thread-select',{'thread_id':ID});sid=c.request('open',{'sensing':True})['owned_session']
            c.request('thread-send',{'text':'Hello','request_id':REQ},sid)
            result=c.request('thread-result',{'request_id':REQ})
            self.assertEqual(result['reply'],'Actual matching reply');self.assertTrue(result['playback_allowed']);self.assertEqual(self.f.speech.calls,[])
            c.request('stop',{},sid)
        finally:server.shutdown();server.server_close();thread.join()
