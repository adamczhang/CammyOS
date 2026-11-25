import os,sys,unittest
sys.path[:0]=['host','sdk','.']
from cammy_agent_core import AgentError,thread_ok,thread_actor
from cammy_t3 import T3Cli

UUID='eba9f4f6-a5d3-4cd6-be0e-98179166004d'
LAUNCH='mcp:launch:38c2a3f1febe77b4aa1a4a94440a00b1aa373fdddcfafd631d1e4378e0f84ff3'

class ThreadIdTests(unittest.TestCase):
    def test_accepts_uuid_and_mcp_launch_ids(self):
        self.assertTrue(thread_ok(UUID));self.assertTrue(thread_ok(LAUNCH))
        self.assertEqual(T3Cli.thread_id(LAUNCH),LAUNCH)
    def test_rejects_unsafe_or_noncanonical(self):
        for bad in (UUID.upper(),'-rf','mcp:launch:'+'x'*64,'a b','mcp:launch:abc',LAUNCH+'\n',None,'mcp:launch:'+'a'*200):
            self.assertFalse(thread_ok(bad),bad)
            with self.assertRaises(AgentError):thread_actor(bad)
    def test_actor_mapping_is_stable_and_valid(self):
        self.assertEqual(thread_actor(UUID),UUID)
        a=thread_actor(LAUNCH);self.assertEqual(a,thread_actor(LAUNCH))
        self.assertRegex(a,r'^t3-[0-9a-f]{40}$')
        self.assertNotEqual(a,thread_actor(LAUNCH[:-1]+'0'))
    def test_client_maps_env_thread(self):
        from cammy_agent_client import AgentClient
        import tempfile,pathlib
        with tempfile.TemporaryDirectory() as d:
            tok=pathlib.Path(d)/'t';tok.write_text('x'*43);tok.chmod(0o600)
            old=os.environ.get('T3CODE_THREAD_ID');os.environ['T3CODE_THREAD_ID']=LAUNCH
            try:c=AgentClient(token_file=tok)
            finally:
                if old is None:os.environ.pop('T3CODE_THREAD_ID')
                else:os.environ['T3CODE_THREAD_ID']=old
            self.assertEqual(c.actor,thread_actor(LAUNCH))
            self.assertEqual(c.for_thread(LAUNCH).actor,thread_actor(LAUNCH))

if __name__=='__main__':unittest.main()
