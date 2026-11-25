"""Reachy adapter uses the shared API and stops only sessions it opened."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(ROOT/'sdk'))
from cammy_reachy_agent import AgentProxy
from cammy_agent_core import AgentError
ID='11111111-2222-3333-4444-555555555555'
class Tests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        outer=self
        class Client:
            def __init__(self,actor):self.actor=actor
            def session(self,value):return value or 'session1'
            def request(self,op,body=None,session=None,image=False):
                outer.calls.append((self.actor,op,body,session))
                return {'owned_session':'session1'} if op=='open' else {'ok':True}
        self.proxy=AgentProxy(Client)
    def test_known_thread_and_strict_fields_only(self):
        with self.assertRaises(AgentError):self.proxy.call('open','not-a-uuid')
        with self.assertRaises(AgentError):self.proxy.call('open',ID,{'shell':'no'})
        self.assertFalse(self.calls)
    def test_selection_is_explicit_and_close_stops_only_its_own_session(self):
        self.proxy.call('thread-select',ID);self.assertEqual(self.calls[-1][2],{'thread_id':ID})
        self.proxy.close();self.assertEqual(len(self.calls),1)
        self.proxy.call('open',ID,{'sensing':True});self.proxy.close()
        self.assertEqual(self.calls[-1],(ID,'stop',{},'session1'))
    def test_stop_does_not_release_a_replacement_session(self):
        self.proxy.call('open',ID,{'sensing':False});self.proxy.call('stop',ID,{},'different');self.proxy.close()
        self.assertEqual(self.calls[-1],(ID,'stop',{},'session1'))
