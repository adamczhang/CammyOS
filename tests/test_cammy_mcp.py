"""Official MCP SDK in-memory/stdio protocol: the default CCC server (fake C3, loopback) and the
explicit --legacy server (real local agent HTTP). The two tool sets are never mixed."""
import asyncio,base64,http.server,json,sys,tempfile,threading,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(ROOT/'sdk'))
from mcp import Client,MCPError
from mcp.client.stdio import StdioServerParameters
import cammy_mcp
from cammy_mcp import create_server
from cammy_mcp_legacy import create_legacy_server,LEGACY_TOOLS
from cammy_agent_client import AgentClient
from cammy_agent_service import make_server
from c3_client import C3Client
from tests import test_agent_control as fixtures

C3_TOOLS={'cammy_c3_status','cammy_c3_look','cammy_c3_say','cammy_c3_message','cammy_c3_transcript','cammy_c3_follow',
          'cammy_c3_show_camera','cammy_c3_look_around','cammy_c3_turn_around','cammy_c3_capture','cammy_c3_providers',
          'cammy_c3_brain','cammy_c3_brain_history','cammy_c3_brain_message','cammy_c3_look_back_history','cammy_c3_look_back_frame'}
JPEG=b'\xff\xd8jpeg'

class McpLegacyTests(unittest.IsolatedAsyncioTestCase):
    """--legacy: the old finite-session tools, unchanged semantics, on the real agent service."""
    async def asyncSetUp(self):
        self.fixture=fixtures.AgentTests();self.fixture.setUp();self.root=self.fixture.root
        self.token='x'*48;self.token_path=self.root/'token';self.token_path.write_text(self.token);self.token_path.chmod(0o600)
        self.http=make_server(self.fixture.core,self.token,('127.0.0.1',0));self.thread=threading.Thread(target=self.http.serve_forever);self.thread.start()
        self.client=AgentClient('alpha','http://127.0.0.1:'+str(self.http.server_port),self.token_path)
    async def asyncTearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.fixture.tearDown()
    async def test_declared_narrow_tools_and_legacy_handshake(self):
        for mode in ['auto','legacy']:
            async with Client(create_legacy_server(self.client),mode=mode) as c:
                tools=await c.list_tools();names={t.name for t in tools.tools}
                self.assertEqual(names,set(LEGACY_TOOLS));self.assertEqual(len(names),9)
                self.assertFalse({n for n in names if n.startswith('cammy_c3_')})
                r=await c.call_tool('cammy_status',{});self.assertFalse(r.is_error);self.assertTrue(r.structured_content['quiet'])
                self.assertEqual(self.fixture.runners,[])
    async def test_real_image_content_and_quiet_error(self):
        async with Client(create_legacy_server(self.client)) as c:
            opened=await c.call_tool('cammy_open',{'seconds':30,'sensing':True});sid=opened.structured_content['owned_session']
            frame=await c.call_tool('cammy_frame',{'session':sid});self.assertFalse(frame.is_error)
            image=next(x for x in frame.content if x.type=='image');self.assertEqual(image.mime_type,'image/jpeg');self.assertTrue(base64.b64decode(image.data).startswith(b'\xff\xd8'))
            quiet=await c.call_tool('cammy_say',{'text':'Hello','session':sid});self.assertTrue(quiet.is_error);self.assertEqual(self.fixture.speech.calls,[])
            stopped=await c.call_tool('cammy_stop',{'session':sid});self.assertFalse(stopped.is_error)
            stale=await c.call_tool('cammy_frame',{'session':sid});self.assertTrue(stale.is_error)
    async def test_explicit_thread_binding_works_without_ambient_identity(self):
        thread='11111111-2222-3333-4444-555555555555'
        async with Client(create_legacy_server(self.client)) as c:
            value=await c.call_tool('cammy_open',{'sensing':True,'thread_id':thread});sid=value.structured_content['owned_session']
            ordinary=await c.call_tool('cammy_status',{});self.assertIsNone(ordinary.structured_content['owned_session'])
            frame=await c.call_tool('cammy_frame',{'session':sid,'thread_id':thread});self.assertFalse(frame.is_error)
            wrong=await c.call_tool('cammy_stop',{'session':sid});self.assertTrue(wrong.is_error)
            self.assertFalse((await c.call_tool('cammy_stop',{'session':sid,'thread_id':thread})).is_error)
    async def test_real_stdio_launch_legacy_flag_uses_same_control_service(self):
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'host/cammy_mcp.py'),'--legacy'],env={'CAMMY_AGENT_ENDPOINT':self.client.endpoint,'CAMMY_AGENT_TOKEN_FILE':str(self.token_path),'T3CODE_THREAD_ID':'alpha'})
        async with Client(params,mode='legacy') as c:
            self.assertEqual({t.name for t in (await c.list_tools()).tools},set(LEGACY_TOOLS))
            r=await c.call_tool('cammy_status',{});self.assertTrue(r.structured_content['quiet'])
            opened=await c.call_tool('cammy_open',{'sensing':False});sid=opened.structured_content['owned_session']
            self.assertFalse((await c.call_tool('cammy_stop',{'session':sid})).is_error)
    async def test_default_stdio_never_falls_back_to_agent_service(self):
        """No flag: CCC tools only. C3 down -> an error, never a legacy session or lease."""
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'host/cammy_mcp.py')],env={'CAMMY_AGENT_ENDPOINT':self.client.endpoint,'CAMMY_AGENT_TOKEN_FILE':str(self.token_path),'T3CODE_THREAD_ID':'alpha','CAMMY_C3_ENDPOINT':'http://127.0.0.1:9','CAMMY_C3_TOKEN_FILE':str(self.token_path)})
        async with Client(params,mode='legacy') as c:
            names={t.name for t in (await c.list_tools()).tools};self.assertEqual(names,C3_TOOLS);self.assertFalse(names&set(LEGACY_TOOLS))
            for tool in ['cammy_c3_status','cammy_c3_look','cammy_c3_brain']:
                r=await c.call_tool(tool,{});self.assertTrue(r.is_error);self.assertEqual(json.loads(r.content[0].text)['code'],503)
            self.assertTrue((await c.call_tool('cammy_open',{'sensing':True})).is_error)
        self.assertIsNone(self.fixture.core.status('alpha')['owned_session']);self.assertEqual(self.fixture.runners,[])
    async def test_unknown_argument_refused_by_real_sdk(self):
        async with Client(create_legacy_server(self.client)) as c:
            with self.assertRaises(MCPError):await c.call_tool('cammy_open',{'seconds':30,'shell':'touch /tmp/nope'})
            self.assertEqual(self.fixture.runners,[])
    def test_default_factory_takes_no_agent_client(self):
        with self.assertRaises(TypeError):create_server(self.client)
        with self.assertRaises(SystemExit):cammy_mcp.main(['--bogus'])

class C3ToolTests(unittest.IsolatedAsyncioTestCase):
    """The default cammy_c3_* tools call the C3 API with its bearer token (fake C3, loopback)."""
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();token=Path(self.tmp.name)/'api-token';token.write_text('t'*48);token.chmod(0o600)
        calls=self.calls=[];test=self
        self.brain={'backend':'resident','active_profile':'kitchen','pending':0,'profiles':[{'id':'kitchen','name':'Kitchen','provider':'claude','instructions':'secret','session_id':'s-1'},{'id':'study','name':'Study','provider':'codex'}]}
        self.histories={'kitchen':[{'id':'1','role':'user','text':'hi','status':'done'},{'id':'2','job_id':'1','role':'assistant','text':'A'*9000,'status':'done','session_id':'s-1'}],'study':[{'id':'9','role':'user','text':'other profile'}]}
        self.transcript=[{'t':1.0,'who':'gemini','text':'x'*4000} for _ in range(400)]
        self.preview={'profile':'kitchen','profile_name':'Kitchen','bytes':10,'capacity_bytes':100,'frames':3,'oldest':1763400000.0,'newest':1763400100.0}
        self.frame_profile_header=None
        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def reply(self,code,body,kind='application/json',headers=()):
                self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
                for k,v in headers:self.send_header(k,v)
                self.end_headers();self.wfile.write(body)
            def j(self,value,code=200):return self.reply(code,json.dumps(value).encode())
            def handle_one(self):
                if self.headers.get('Authorization')!='Bearer '+'t'*48:return self.reply(401,b'{"error":"unauthorized"}')
                n=int(self.headers.get('Content-Length') or 0);body=json.loads(self.rfile.read(n) or b'{}') if n else None
                calls.append((self.command,self.path,body));path,_,query=self.path.partition('?')
                from urllib.parse import parse_qs;q={k:v[0] for k,v in parse_qs(query).items()}
                if path=='/v1/status':return self.j({'duplex':'half','body':{'up':True},'voice':{'state':'asleep'},'moves':{'following':False,'camera_view':False,'sweeping':False},'capture':{'busy':False}})
                if path=='/v1/look':return self.reply(200,JPEG,'image/jpeg')
                if path=='/v1/look-around':return self.reply(409,b'{"error":"already looking around"}')
                if path=='/v1/transcript':return self.j({'entries':test.transcript[-int(q.get('n',50)):]})
                if path=='/v1/providers':return self.j({'providers':[{'id':'claude','name':'Claude Code','installed':True,'auth':'subscription','check':'passed','detail':'d'*900,'recovery':'r','token':'SECRET-TOKEN'}]})
                if path=='/v1/brain':return self.j(test.brain)
                if path=='/v1/brain/history':return self.j({'entries':test.histories[q.get('profile',test.brain['active_profile'])]})
                if path.startswith('/v1/brain/profiles/') and path.endswith('/message'):
                    profile=path.split('/')[-2]
                    if profile!=test.brain['active_profile']:return self.j({'error':'profile changed'},409)
                    return self.j({'queued':True,'profile':profile})
                if path=='/v1/preview/history':return self.j(test.preview)
                if path=='/v1/preview/frame':
                    if q.get('profile')!=test.preview['profile']:return self.reply(409,b'{"error":"the active profile changed"}')
                    return self.reply(200,JPEG,'image/jpeg',[('X-Cammy-Frame-Time','%.6f'%(float(q['at'])+0.4)),('X-Cammy-Profile',test.frame_profile_header or q['profile'])])
                return self.j({'ok':True})
            do_GET=do_POST=handle_one
        self.http=http.server.ThreadingHTTPServer(('127.0.0.1',0),Fake);self.thread=threading.Thread(target=self.http.serve_forever);self.thread.start()
        self.c3=C3Client('http://127.0.0.1:%d'%self.http.server_port,token)
    async def asyncTearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.tmp.cleanup()
    def paths(self):return [p.partition('?')[0] for _,p,_ in self.calls]
    async def test_c3_tools(self):
        async with Client(create_server(c3=self.c3)) as c:
            self.assertEqual({t.name for t in (await c.list_tools()).tools},C3_TOOLS)
            r=await c.call_tool('cammy_c3_status',{});self.assertEqual(json.loads(r.content[0].text)['cammy_connected'],True)
            r=await c.call_tool('cammy_c3_follow',{'on':True});self.assertFalse(r.is_error)
            self.assertIn(('POST','/v1/follow',{'on':True}),self.calls)
            r=await c.call_tool('cammy_c3_look',{});self.assertEqual(base64.b64decode(r.content[0].data),JPEG)
            r=await c.call_tool('cammy_c3_look_around',{});self.assertTrue(r.is_error);self.assertIn('already',r.content[0].text)
            with self.assertRaises(MCPError):await c.call_tool('cammy_c3_say',{'text':'hi','extra':1})
            self.assertTrue((await c.call_tool('cammy_status',{})).is_error)
    async def test_live_look_is_labelled_live_with_receive_time_only(self):
        async with Client(create_server(c3=self.c3)) as c:
            r=await c.call_tool('cammy_c3_look',{});self.assertEqual(r.content[0].type,'image')
            meta=json.loads(r.content[1].text);self.assertEqual(meta['source'],'live');self.assertIsNone(meta['capture_time'])
            self.assertGreater(meta['received_at'],0);self.assertEqual(meta['bytes'],len(JPEG))
    async def test_look_back_is_stored_profile_gated_and_timestamped(self):
        async with Client(create_server(c3=self.c3)) as c:
            h=(await c.call_tool('cammy_c3_look_back_history',{})).structured_content
            self.assertEqual((h['source'],h['profile'],h['frames']),('history','kitchen',3));self.assertTrue(h['newest_iso'])
            r=await c.call_tool('cammy_c3_look_back_frame',{'profile':'kitchen','at':1763400050.0});self.assertFalse(r.is_error)
            meta=json.loads(r.content[0].text);self.assertEqual((meta['source'],meta['profile']),('history','kitchen'))
            self.assertAlmostEqual(meta['frame_time'],1763400050.4,places=3);self.assertAlmostEqual(meta['offset_s'],0.4,places=3)
            self.assertEqual(base64.b64decode(r.content[1].data),JPEG)
            for bad in [{'profile':'study','at':1763400050.0},{'profile':'','at':1763400050.0},{'profile':'kitchen','at':-1}]:
                r=await c.call_tool('cammy_c3_look_back_frame',bad);self.assertTrue(r.is_error);self.assertFalse(any(x.type=='image' for x in r.content))
            self.assertEqual(json.loads(r.content[0].text)['code'],400)
            self.assertTrue((await c.call_tool('cammy_c3_look_back_frame',{'at':1763400050.0})).is_error)
    async def test_look_back_refuses_a_frame_labelled_for_another_profile(self):
        self.frame_profile_header='study'
        async with Client(create_server(c3=self.c3)) as c:
            r=await c.call_tool('cammy_c3_look_back_frame',{'profile':'kitchen','at':1763400050.0})
            self.assertTrue(r.is_error);self.assertEqual(json.loads(r.content[0].text)['code'],409);self.assertFalse(any(x.type=='image' for x in r.content))
    async def test_brain_status_history_and_message_only_for_the_active_profile(self):
        async with Client(create_server(c3=self.c3)) as c:
            s=(await c.call_tool('cammy_c3_brain',{})).structured_content
            self.assertEqual(s['active_profile'],'kitchen');self.assertNotIn('instructions',json.dumps(s));self.assertNotIn('session_id',json.dumps(s))
            h=(await c.call_tool('cammy_c3_brain_history',{'profile':'kitchen','n':5})).structured_content
            self.assertEqual((h['source'],h['profile']),('history','kitchen'));self.assertEqual(len(h['entries']),2)
            self.assertTrue(h['truncated']);self.assertEqual(len(h['entries'][1]['text']),cammy_mcp.MAX_ENTRY_CHARS);self.assertNotIn('session_id',h['entries'][1])
            self.assertEqual(len((await c.call_tool('cammy_c3_brain_history',{'profile':'kitchen','n':1})).structured_content['entries']),1)
            before=len(self.calls)
            r=await c.call_tool('cammy_c3_brain_history',{'profile':'study'});self.assertTrue(r.is_error);self.assertEqual(json.loads(r.content[0].text)['code'],409)
            r=await c.call_tool('cammy_c3_brain_message',{'profile':'study','text':'hi'});self.assertTrue(r.is_error)
            self.assertNotIn('/v1/brain/history',[p.partition('?')[0] for _,p,_ in self.calls[before:]]);self.assertNotIn('/v1/brain/message',self.paths())
            r=await c.call_tool('cammy_c3_brain_message',{'profile':'kitchen','text':'hi'});self.assertEqual(r.structured_content,{'queued':True,'profile':'kitchen'})
            self.assertIn(('POST','/v1/brain/profiles/kitchen/message',{'text':'hi'}),self.calls)
            self.brain['active_profile']=None
            r=await c.call_tool('cammy_c3_brain_message',{'profile':'kitchen','text':'again'});self.assertTrue(r.is_error)
            self.assertEqual(json.loads(r.content[0].text)['code'],409)
            self.assertNotIn('/v1/brain/message',self.paths())
    async def test_providers_read_only_and_filtered(self):
        async with Client(create_server(c3=self.c3)) as c:
            r=await c.call_tool('cammy_c3_providers',{});row=r.structured_content['providers'][0]
            self.assertNotIn('SECRET-TOKEN',r.content[0].text);self.assertNotIn('token',row);self.assertEqual(len(row['detail']),cammy_mcp.MAX_DETAIL_CHARS)
            self.assertNotIn('/v1/providers/test',self.paths());self.assertFalse(any('refresh' in p for _,p,_ in self.calls))
            names={t.name for t in (await c.list_tools()).tools}
            for forbidden in ['login','test','inject','echo','debug','profiles','select','instructions','cancel','sleep','wake','duplex']:
                self.assertFalse([n for n in names if forbidden in n],forbidden)
    async def test_transcript_reply_is_capped_newest_kept(self):
        self.transcript[-1]={'t':2.0,'who':'adam','text':'newest'}
        async with Client(create_server(c3=self.c3)) as c:
            r=await c.call_tool('cammy_c3_transcript',{'n':400});v=r.structured_content
            self.assertLessEqual(len(json.dumps(v['entries'])),cammy_mcp.MAX_RESPONSE_BYTES+2);self.assertTrue(v['truncated']);self.assertGreater(v['dropped'],0)
            self.assertEqual(v['entries'][-1]['text'],'newest')
    async def test_oversized_or_non_jpeg_image_refused(self):
        self.c3.look=lambda:b'not a jpeg'
        async with Client(create_server(c3=self.c3)) as c:
            r=await c.call_tool('cammy_c3_look',{});self.assertTrue(r.is_error);self.assertEqual(json.loads(r.content[0].text)['code'],502)
        self.c3.look=lambda:b'\xff\xd8'+b'0'*cammy_mcp.MAX_IMAGE_BYTES
        async with Client(create_server(c3=self.c3)) as c:
            self.assertTrue((await c.call_tool('cammy_c3_look',{})).is_error)

if __name__=='__main__':unittest.main()
