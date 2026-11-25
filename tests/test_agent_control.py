"""Shared controller and actual HTTP client; fake hardware/providers only."""
import io,json,os,sys,tempfile,threading,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(ROOT/'sdk'))
from cammy_agent_core import AgentController,AgentError,quiet_policy,speaker_pcm
from cammy_agent_service import make_server
from cammy_agent_client import AgentClient
from PIL import Image
class Job:
    def __init__(self,value,delayed=False):self.value=value;self.delayed=delayed;self.cancelled=False
    def poll(self):return None if self.delayed else (self.value,None)
    def cancel(self):self.cancelled=True
class Speech:
    def __init__(self):self.calls=[];self.delayed=False
    def synthesize(self,text):self.calls.append(('tts',text));self.job=Job(bytes(48000),self.delayed);return self.job
    def transcribe(self,pcm):self.calls.append(('stt',len(pcm)));return Job(json.dumps({'text':'fake speech','provider':'synthetic'}).encode())
class Runner:
    synthetic=True
    def __init__(self,sensing):self.phase='idle';self.sensing=sensing;self.commands=[];self.at=time.monotonic();self.sequence=0;self.cleanup=True
    def start(self,seconds):self.phase='live' if self.sensing else 'control';self.session=os.urandom(12).hex();return self.status()
    def status(self):return {'phase':self.phase,'session':getattr(self,'session',None),'audio_sequence':self.sequence,'worker_threads_joined':self.cleanup,'decoder_reaped':self.cleanup,'microphone_stream_closed':self.cleanup}
    def stop(self,wait=False):self.phase='idle' if self.cleanup else 'stopping';return self.status()
    def body_command(self,operation,**value):
        if self.phase not in ('live','control'):raise RuntimeError('stopped')
        self.commands.append((operation,value));return {'queued':True}
    def latest_frame(self):
        if not self.sensing:raise RuntimeError('no sensing')
        out=io.BytesIO();Image.new('RGB',(4,4),'blue').save(out,'JPEG');return (out.getvalue(),1,self.at,'fake-utc'),self.session,'fake-boot'
    def audio_tail(self,after):
        if not self.sensing:raise RuntimeError('no sensing')
        self.sequence+=1;return b'\x10\x00'*800,{'latest_sequence':self.sequence,'history_gap':False}
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.policy=self.root/'policy';self.policy.write_text('{"quiet":true}');self.policy.chmod(0o600);self.runners=[];self.speech=Speech()
        def factory(sensing):
            r=Runner(sensing);self.runners.append(r);return r
        self.core=AgentController(self.policy,client_factory=lambda:None,live_factory=lambda:factory(True),control_factory=lambda:factory(False),speech=self.speech)
    def tearDown(self):self.core.close();self.tmp.cleanup()
    def open(self,sensing=False):return self.core.open('alpha',30,sensing)['owned_session']
    def error(self,code,fn):
        with self.assertRaises(AgentError) as e:fn()
        self.assertEqual(e.exception.code,code)
    def test_construction_status_never_senses_and_default_quiet_fail_closed(self):
        self.assertEqual(self.runners,[]);self.assertTrue(self.core.status('alpha')['quiet'])
        self.policy.unlink();self.assertTrue(quiet_policy(self.policy));self.error(423,lambda:self.core.say('alpha','missing','Hello'));self.assertFalse(self.speech.calls)
    def test_ownership_stale_session_and_clean_restart(self):
        sid=self.open();self.error(409,lambda:self.core.open('beta'));self.error(409,lambda:self.core.stop('beta',sid));self.error(409,lambda:self.core.face('alpha','stale'))
        self.assertIsNone(self.core.status('beta')['owned_session']);self.core.stop('alpha',sid);new=self.open();self.assertNotEqual(new,sid)
    def test_uncertain_cleanup_blocks_new_session_until_reaped(self):
        sid=self.open();r=self.runners[-1];r.cleanup=False;self.core.stop('alpha',sid)
        self.error(409,lambda:self.core.open('alpha'));r.cleanup=True;r.phase='idle';self.open()
    def test_fresh_frame_is_image_and_stale_frame_is_refused(self):
        sid=self.open(True);raw,meta=self.core.frame('alpha',sid);self.assertTrue(raw.startswith(b'\xff\xd8'));self.assertTrue(meta['synthetic'])
        self.runners[-1].at-=3;self.error(409,lambda:self.core.frame('alpha',sid))
    def test_pan_releases_and_bad_intents_do_not_dispatch(self):
        sid=self.open();self.core.pan('alpha',sid,'left',.1)
        self.assertEqual(self.runners[-1].commands[-1],('input_button',{'action':'release'}))
        self.error(400,lambda:self.core.pan('alpha',sid,'up',.1));self.error(400,lambda:self.core.pan('alpha',sid,'right',float('nan')))
    def test_quiet_blocks_before_synthesis_and_device_dispatch(self):
        sid=self.open();self.error(423,lambda:self.core.say('alpha',sid,'Hello'))
        self.assertEqual(self.speech.calls,[]);self.assertEqual(self.runners[-1].commands,[])
    def test_generated_speech_only_uses_bounded_camera_pcm(self):
        self.policy.write_text('{"quiet":false}');sid=self.open();value=self.core.say('alpha',sid,'Hello')
        self.assertFalse(value['hardware_audible']);op,data=self.runners[-1].commands[-1];self.assertEqual(op,'play_pcm');self.assertEqual(len(data['pcm']),32000)
    def test_speaker_completion_does_not_confuse_idle_or_fault_with_finished(self):
        self.policy.write_text('{"quiet":false}');sid=self.open();runner=self.runners[-1]
        for phase in ('idle','cancelled','preflight-refused','recovery-required'):
            with self.subTest(phase=phase):
                runner.body_command=lambda operation,phase=phase:{'active':False,'phase':phase}
                self.error(503,lambda:self.core.wait_speaker('alpha',sid))
        runner.body_command=lambda operation:{'active':False,'phase':'finished'}
        self.assertEqual(self.core.wait_speaker('alpha',sid)['phase'],'finished')
    def test_speaker_quiet_change_and_timeout_cancel_but_stop_never_touches_new_owner(self):
        self.policy.write_text('{"quiet":false}');sid=self.open();runner=self.runners[-1];calls=[]
        def active(operation):calls.append(operation);return {'active':True,'phase':'playing'}
        runner.body_command=active
        self.error(503,lambda:self.core.wait_speaker('alpha',sid,timeout=.001))
        self.assertEqual(calls[-1],'speaker_cancel');calls.clear()
        self.policy.write_text('{"quiet":true}')
        self.error(423,lambda:self.core.wait_speaker('alpha',sid))
        self.assertEqual(calls,['speaker_cancel']);calls.clear()
        self.core.stop('alpha',sid);self.core.open('beta',30,False)
        self.error(409,lambda:self.core.wait_speaker('alpha',sid));self.assertEqual(calls,[])
    def test_stop_rejects_late_synthesis_and_queued_output(self):
        self.policy.write_text('{"quiet":false}');sid=self.open();self.speech.delayed=True;errors=[]
        def work():
            try:self.core.say('alpha',sid,'Hello')
            except AgentError as e:errors.append(e.code)
        thread=threading.Thread(target=work);thread.start()
        for _ in range(100):
            if hasattr(self.speech,'job'):break
            time.sleep(.01)
        self.core.stop('alpha',sid);self.speech.job.delayed=False;thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[409]);self.assertTrue(self.speech.job.cancelled);self.assertFalse(self.runners[-1].commands)
    def test_listen_uses_only_new_sequence_and_local_transcription(self):
        sid=self.open(True);value=self.core.listen('alpha',sid,1);self.assertEqual(value['text'],'fake speech');self.assertTrue(value['synthetic']);self.assertLessEqual(value['audio_bytes'],40000)
    def test_digital_silence_never_reaches_speech_model(self):
        sid=self.open(True);runner=self.runners[-1]
        def silence(after):
            runner.sequence+=1;return bytes(1600),{'latest_sequence':runner.sequence,'history_gap':False}
        runner.audio_tail=silence
        self.error(409,lambda:self.core.listen('alpha',sid,1));self.assertEqual(self.speech.calls,[])

    def test_pcm_resampling_peak_and_duration_are_bounded(self):
        raw=speaker_pcm(b'\xff\x7f'*24000);import array
        data=array.array('h');data.frombytes(raw);self.assertEqual(len(raw),32000);self.assertLessEqual(max(data),4000)
        self.error(400,lambda:speaker_pcm(bytes(48000*6)))
    def test_http_narrow_client_auth_owner_and_unknown_fields(self):
        token='t'*48;p=self.root/'token';p.write_text(token);p.chmod(0o600)
        server=make_server(self.core,token,('127.0.0.1',0));thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            a=AgentClient('alpha','http://127.0.0.1:'+str(server.server_port),p);b=AgentClient('beta',a.endpoint,p)
            self.assertTrue(a.request('status')['quiet']);sid=a.request('open',{'sensing':True})['owned_session'];raw,meta=a.request('frame.jpg',session=sid,image=True);self.assertTrue(raw.startswith(b'\xff\xd8'))
            self.error(409,lambda:b.request('stop',{},sid));self.error(400,lambda:a.request('face',{'path':'/dev/mem'},sid));self.error(423,lambda:a.request('say',{'text':'Hello'},sid))
            a.request('stop',{},sid)
            with self.assertRaises(ValueError):AgentClient('a','http://192.0.2.53:8787',p)
        finally:server.shutdown();server.server_close();thread.join()
