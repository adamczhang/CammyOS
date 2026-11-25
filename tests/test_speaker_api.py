"""Real portal/SDK on loopback; synthetic guardian, no camera/audio devices."""
import base64,json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'sdk'))
from cammy import Cammy,CammyError
class SpeakerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):subprocess.run(['make','-s','native'],cwd=ROOT,check=True,capture_output=True)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.runtime=self.root/'speaker';self.runtime.mkdir(mode=0o700)
        self.token='test-'+('a'*48);(self.root/'token').write_text(self.token)
        (self.runtime/'speaker.conf').write_text('synthetic only');(self.runtime/'speaker.conf').chmod(0o600)
        self.guard=self.runtime/'speaker-guardian'
        self.guard.write_text('''#!/usr/bin/python3
import pathlib,sys,signal,time
root=pathlib.Path(__file__).parent
assert sys.argv[1:]==[str(root/'speaker.pcm'),str(root/'speaker.conf'),'auto']
assert 1600<=len(pathlib.Path(sys.argv[1]).read_bytes())<=160000
(root/'playing').touch()
def done(sig,frame):
 (root/'cancel-observed').touch()
 sys.exit(0)
signal.signal(signal.SIGTERM,done)
if (root/'close').exists():  # the feeder's close tombstone (speaker_stream.h), then the restore window
 import os;os.close(os.open(str(root/'speaker.pcm.1'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600))
 while not (root/'exit').exists():time.sleep(.01)
if (root/'hang').exists():
 while True:time.sleep(.01)
if (root/'fail').exists():sys.exit(4)
if (root/'refuse').exists():sys.exit(3)
''');self.guard.chmod(0o700)
        with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
        self.start()
    def start(self):
        command=json.loads(os.environ['CAMMY_SPEAKER_TEST_PORTAL']) if os.environ.get('CAMMY_SPEAKER_TEST_PORTAL') else [str(ROOT/'build/host/cammy-portal')]
        self.p=subprocess.Popen(command+['--port',str(self.port),'--token-file',str(self.root/'token'),'--speaker-root',str(self.runtime),'--strict-control'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        self.a=Cammy(f'http://127.0.0.1:{self.port}',self.token,'alpha',timeout=2)
        for _ in range(100):
            try:self.a.state();return
            except OSError:time.sleep(.01)
        self.fail('Portal not ready')
    def tearDown(self):
        if self.p.poll() is None:self.p.terminate()
        self.p.communicate(timeout=5);self.tmp.cleanup()
    def error(self,code,fn):
        with self.assertRaises(CammyError) as e:fn()
        self.assertEqual(e.exception.status,code)
    def settled(self):
        for _ in range(150):
            state=self.a.speaker_status()
            if not state['active']:return state
            time.sleep(.01)
        self.fail('Job not settled')
    def playing(self):
        for _ in range(150):
            if (self.runtime/'playing').exists():return
            time.sleep(.01)
        self.fail('Guardian not started')
    def test_finite_pcm_sdk_job_and_no_physical_claim(self):
        self.a.acquire();self.assertTrue(self.a.play_pcm(bytes(60800))['queued'])
        state=self.settled();self.assertEqual(state['phase'],'finished');self.assertFalse(state['hardware_audible']);self.assertFalse(state['hardware_cleanup_confirmed'])
        self.assertFalse((self.runtime/'speaker.pcm').exists())
    def test_menu_volume_scales_pcm_down_only(self):
        (self.root/'volume').write_text('50\n')
        self.p.terminate();self.p.wait(5)
        os.environ['CAMMY_VOLUME_FILE']=str(self.root/'volume')
        try:self.start()
        finally:del os.environ['CAMMY_VOLUME_FILE']
        self.guard.write_text(self.guard.read_text().replace("(root/'playing').touch()","(root/'playing').touch();(root/'heard.pcm').write_bytes(pathlib.Path(sys.argv[1]).read_bytes())"))
        samples=[1000,-1000,9999,-10000]*400
        self.a.acquire();self.a.play_pcm(b''.join(s.to_bytes(2,'little',signed=True) for s in samples));self.settled()
        heard=(self.runtime/'heard.pcm').read_bytes()
        got=[int.from_bytes(heard[i:i+2],'little',signed=True) for i in range(0,8,2)]
        self.assertEqual(got,[250,-250,2499,-2500])  # 50% -> 0.25 amplitude (squared curve)
    def test_owner_boot_and_lease_are_required(self):
        self.error(409,lambda:self.a.play_pcm(bytes(1600)))
        self.a.acquire();b=Cammy(self.a.endpoint,self.token,'beta');b.state();self.error(409,lambda:b.play_pcm(bytes(1600)))
        self.a._lease_binding=None;self.error(409,lambda:self.a.play_pcm(bytes(1600)))
        self.assertFalse((self.runtime/'playing').exists())
    def test_old_boot_or_lease_never_plays(self):
        self.a.acquire();lease=self.a._lease_binding;self.a._boot_binding='f'*32
        self.error(409,lambda:self.a.play_pcm(bytes(1600)))
        self.a._boot_binding=None;self.a.state();self.a.release();self.a.acquire();self.a._lease_binding=lease
        self.error(409,lambda:self.a.play_pcm(bytes(1600)));self.assertFalse((self.runtime/'playing').exists())
    def test_short_remaining_lease_and_replayed_sequence_refused(self):
        self.a.acquire(1);self.error(409,lambda:self.a.play_pcm(bytes(1600)))
        self.a.acquire(30);self.a.play_pcm(bytes(1600));self.settled()
        body={'sequence':self.a._speaker_sequence,'pcm_s16le_mono16k':base64.b64encode(bytes(1600)).decode()}
        self.error(409,lambda:self.a.request('POST','/v1/speaker/play',body))
    def test_stop_cancel_release_and_expiry_retire_owned_job(self):
        for operation in ['stop','cancel','release','expiry']:
            with self.subTest(operation=operation):
                for name in ['playing','cancel-observed']:(self.runtime/name).unlink(missing_ok=True)
                (self.runtime/'hang').touch();self.a.acquire(30);self.a.play_pcm(bytes(160000));self.playing()
                self.error(409,lambda:self.a.play_pcm(bytes(1600)))
                if operation=='stop':self.a.stop()
                if operation=='cancel':self.a.speaker_cancel()
                if operation=='release':self.a.release()
                if operation=='expiry':self.a.acquire(1);time.sleep(1.05)
                self.assertEqual(self.settled()['phase'],'cancelled');self.assertTrue((self.runtime/'cancel-observed').exists());self.assertFalse((self.runtime/'speaker.pcm').exists())
                if operation=='cancel':self.a.release()
    def test_stream_append_queue_limit_and_cleanup(self):
        self.a.acquire(30);(self.runtime/'hang').touch();self.a.play_pcm(bytes(1600));self.playing()
        self.error(409,lambda:self.a.play_pcm(bytes(1600)))  # plain play never joins an active job
        self.assertTrue(self.a.play_pcm(bytes(3200),append=True)['appended'])
        self.assertTrue(self.a.play_pcm(bytes(4800),append=True)['appended'])
        self.assertEqual((self.runtime/'speaker.pcm.1').stat().st_size,3200)
        self.assertEqual((self.runtime/'speaker.pcm.2').stat().st_size,4800)
        self.assertEqual(oct((self.runtime/'speaker.pcm.2').stat().st_mode&0o777),'0o600')
        self.error(409,lambda:self.a.play_pcm(bytes(1600),append=True))  # chunk 1 unplayed: queue full
        (self.runtime/'speaker.pcm.1').unlink()  # the guardian consumed it
        self.assertTrue(self.a.play_pcm(bytes(1600),append=True)['appended'])
        self.a.speaker_cancel();self.assertEqual(self.settled()['phase'],'cancelled')
        self.assertEqual(sorted(x.name for x in self.runtime.glob('speaker.pcm*')),[])
        (self.runtime/'hang').unlink();self.a.play_pcm(bytes(1600));self.assertEqual(self.settled()['phase'],'finished')
    def consumed(self,n):
        for _ in range(150):
            if not (self.runtime/('speaker.pcm.%d'%n)).exists():return
            time.sleep(.01)
        self.fail('chunk %d not consumed'%n)
    def test_stream_total_limit_matches_guardian(self):
        self.assertEqual(self.a.speaker_status()['stream_max_seconds'],60)
        self.a.acquire(30);(self.runtime/'hang').touch();self.a.play_pcm(bytes(160000));self.playing()
        for n in range(1,12):  # 5 s + 11 x 5 s = exactly 60 s: accepted
            self.assertTrue(self.a.play_pcm(bytes(160000),append=True)['appended'])
            (self.runtime/('speaker.pcm.%d'%n)).unlink()  # consumed by the feeder
        with self.assertRaises(CammyError) as e:self.a.play_pcm(bytes(1600),append=True)
        self.assertEqual(e.exception.status,409);self.assertIn('speaker stream limit',str(e.exception))
        self.assertEqual(sorted(x.name for x in self.runtime.glob('speaker.pcm.*')),[])
        self.a.speaker_cancel();self.assertEqual(self.settled()['phase'],'cancelled')
    def test_append_admitted_before_close_owns_the_slot(self):
        self.a.acquire(30);(self.runtime/'hang').touch();self.a.play_pcm(bytes(1600));self.playing()
        self.assertTrue(self.a.play_pcm(bytes(3200),append=True)['appended'])
        chunk=self.runtime/'speaker.pcm.1';self.assertEqual(chunk.stat().st_nlink,1);self.assertFalse((self.runtime/'speaker.pcm.1.tmp').exists())
        with self.assertRaises(FileExistsError):os.open(chunk,os.O_WRONLY|os.O_CREAT|os.O_EXCL)  # the feeder's close loses
        self.a.speaker_cancel();self.settled();self.assertEqual(sorted(x.name for x in self.runtime.glob('speaker.pcm*')),[])
    def test_append_after_feeder_close_is_409_then_reuse_starts_new_job(self):
        self.a.acquire(30);(self.runtime/'close').touch();self.a.play_pcm(bytes(1600));self.playing()
        tomb=self.runtime/'speaker.pcm.1'
        for _ in range(150):
            if tomb.exists():break
            time.sleep(.01)
        with self.assertRaises(CammyError) as e:self.a.play_pcm(bytes(3200),append=True)
        self.assertEqual(e.exception.status,409);self.assertIn('speaker stream closed',str(e.exception))
        self.assertEqual(tomb.stat().st_size,0);self.assertFalse((self.runtime/'speaker.pcm.1.tmp').exists())
        self.assertTrue(self.a.speaker_status()['active'])
        (self.runtime/'close').unlink();(self.runtime/'exit').touch();self.assertEqual(self.settled()['phase'],'finished')
        self.assertFalse(tomb.exists())  # reaped: the tombstone is cleaned with the job
        result=self.a.play_pcm(bytes(3200),append=True);self.assertTrue(result['queued']);self.assertNotIn('appended',result)
        self.assertEqual(self.settled()['phase'],'finished')
    def test_c3_speaker_retries_a_closed_stream_as_a_new_job(self):
        """CCC's bounded retry: 409 'speaker stream closed' clears when the guardian exits."""
        import threading,types
        sys.path.insert(0,str(ROOT/'host'))
        from c3 import body as B
        speaker=B.Speaker(types.SimpleNamespace(cammy=self.a,epoch=0,_current=lambda epoch:True))
        self.a.acquire(30);(self.runtime/'close').touch();self.a.play_pcm(bytes(1600));self.playing()
        for _ in range(150):
            if (self.runtime/'speaker.pcm.1').exists():break
            time.sleep(.01)
        def finish():(self.runtime/'close').unlink();(self.runtime/'playing').unlink();(self.runtime/'exit').touch()
        timer=threading.Timer(.5,finish);timer.start();started=time.monotonic()
        try:self.assertTrue(speaker._send_blocking(bytes(3200),False,0,speaker.generation))
        finally:timer.cancel()
        self.assertLess(time.monotonic()-started,B.SPEAKER_BUSY_S);self.assertGreater(time.monotonic()-started,.4)
        self.playing();self.assertEqual(self.settled()['phase'],'finished')
    def test_leftover_chunk_or_tombstone_blocks_new_job(self):
        self.a.acquire(30);(self.runtime/'speaker.pcm.1').touch()
        self.error(503,lambda:self.a.play_pcm(bytes(1600)));self.assertFalse((self.runtime/'playing').exists())
        self.assertFalse((self.runtime/'speaker.pcm').exists())
    def test_real_guardian_close_handshake_end_to_end(self):
        """Real guardian.c (SYNTHETIC) behind the real portal: just-before close, after close, reuse."""
        fake=self.root/'fake';fake.mkdir();gate=self.root/'gate';gate.mkdir();streamed=self.root/'streamed';hold=self.root/'hold'
        (fake/'hw_params').write_text('access: MMAP_INTERLEAVED\nformat: S16_LE\nchannels: 1\nrate: 16000 (16000/1)\nperiod_size: 960\nbuffer_size: 7680\n')
        (fake/'aplay').write_text('#!/usr/bin/python3\nimport shutil\nwith open("/proc/self/fd/3","rb") as i,open(%r,"wb") as o:shutil.copyfileobj(i,o)\n'%str(streamed))
        (fake/'amixer').write_text('#!/usr/bin/python3\nimport pathlib,sys,time\nif sys.argv[3]=="cget":\n c={"16":("LINEOUT volume","0"),"20":("LINEOUT Switch","on"),"22":("LINEOUT Output Select","1"),"1":("tx hub mode","1"),"21":("SPK Switch","off")}[sys.argv[4].split("=")[1]]\n print("numid=x,name="+repr(c[0])+"\\n  : values="+c[1])\nelse:\n while sys.argv[-2]=="numid=21" and pathlib.Path(%r).exists():time.sleep(.01)\n'%str(hold))
        for tool in ('aplay','amixer'):(fake/tool).chmod(0o700)
        self.guard.unlink()
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-DSYNTHETIC=1','-DSTREAM_IDLE_MS=100','-DHW_PARAMS="%s"'%(fake/'hw_params'),
            '-DPLAY="%s"'%(fake/'aplay'),'-DMIXER="%s"'%(fake/'amixer'),str(ROOT/'native/speaker_probe/guardian.c'),'-o',str(self.guard)],check=True,capture_output=True)
        self.guard.chmod(0o700);self.p.terminate();self.p.communicate(timeout=5)
        os.environ['CAMMY_SYNTH_CLOSE_GATE']=str(gate)
        try:self.start()
        finally:del os.environ['CAMMY_SYNTH_CLOSE_GATE']
        def wait(path):
            for _ in range(500):
                if path.exists():return
                time.sleep(.01)
            self.fail(str(path))
        first,late,refused,next_job=b'\x01\x00'*800,b'\x02\x00'*1600,b'\x03\x00'*800,b'\x04\x00'*800
        self.a.acquire(30);self.a.play_pcm(first)
        wait(gate/'closing.1')                                   # feeder idle: about to close slot 1
        self.assertTrue(self.a.play_pcm(late,append=True)['appended']);(gate/'go.1').touch()
        wait(gate/'closing.2');hold.touch();(gate/'go.2').touch();wait(self.runtime/'speaker.pcm.2')
        with self.assertRaises(CammyError) as e:self.a.play_pcm(refused,append=True)
        self.assertEqual(e.exception.status,409);self.assertIn('speaker stream closed',str(e.exception))
        hold.unlink();self.assertEqual(self.settled()['phase'],'finished')
        self.assertEqual(streamed.read_bytes(),first+late)       # the late append played; the refused one never queued
        self.assertEqual(sorted(x.name for x in self.runtime.glob('speaker.pcm*')),[])
        self.assertTrue(self.a.play_pcm(next_job,append=True)['queued']);self.assertEqual(self.settled()['phase'],'finished')
        self.assertEqual(streamed.read_bytes(),next_job)
    def test_append_without_active_job_starts_a_new_job(self):
        self.a.acquire(30);self.a.play_pcm(bytes(1600),append=True);self.assertEqual(self.settled()['phase'],'finished')
    def test_cleanup_fault_blocks_new_jobs_across_restart(self):
        self.a.acquire();(self.runtime/'fail').touch();self.a.play_pcm(bytes(1600))
        self.assertEqual(self.settled()['phase'],'cleanup-uncertain');self.error(503,lambda:self.a.play_pcm(bytes(1600)))
        self.p.terminate();self.p.communicate(timeout=5);self.start();self.a.acquire();self.error(503,lambda:self.a.play_pcm(bytes(1600)))
    def test_preflight_refusal_does_not_latch_cleanup_fault(self):
        self.a.acquire();(self.runtime/'refuse').touch();self.a.play_pcm(bytes(1600));self.assertEqual(self.settled()['phase'],'preflight-refused')
        (self.runtime/'refuse').unlink();self.a.play_pcm(bytes(1600));self.assertEqual(self.settled()['phase'],'finished')
    def test_bad_schema_base64_size_peak_and_generic_body_limit(self):
        self.a.acquire()
        good=base64.b64encode(bytes(1600)).decode()
        for body in [{'sequence':1,'pcm_s16le_mono16k':good,'path':'/dev/mem'}, {'sequence':1,'pcm_s16le_mono16k':good[:-1]+'!'}, {'sequence':1,'pcm_s16le_mono16k':'AA=='}, {'sequence':1,'pcm_s16le_mono16k':base64.b64encode(b'\xff\x7f'*800).decode()}]:
            self.error(400,lambda:self.a.request('POST','/v1/speaker/play',body))
        self.error(413,lambda:self.a.request('POST','/v1/face',{'extra':'x'*1100}))
        for pcm in [bytes(1598),bytes(160002),b'\xff\x7f'*800]:
            with self.assertRaises(ValueError):self.a.play_pcm(pcm)
        self.assertFalse((self.runtime/'playing').exists())
    def test_mode_links_and_residue_are_not_overwritten(self):
        self.a.acquire();self.guard.chmod(0o777);self.error(501,lambda:self.a.play_pcm(bytes(1600)));self.guard.chmod(0o700)
        residue=self.runtime/'speaker.pcm';residue.symlink_to(self.root/'token');self.error(503,lambda:self.a.play_pcm(bytes(1600)))
        self.assertTrue(residue.is_symlink());self.assertEqual((self.root/'token').read_text(),self.token)
    def test_portal_termination_requests_guardian_cleanup(self):
        (self.runtime/'hang').touch();self.a.acquire();self.a.play_pcm(bytes(160000));self.playing()
        self.p.terminate();self.p.communicate(timeout=5)
        self.assertTrue((self.runtime/'cancel-observed').exists());self.assertFalse((self.runtime/'speaker.pcm').exists())
