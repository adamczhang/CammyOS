"""Real portal on loopback, fake file-job worker. No media or hardware."""
import json,socket,subprocess,tempfile,time,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'sdk'))
from cammy import Cammy,CammyError
class ManagedMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):subprocess.run(['make','-s','native'],cwd=ROOT,check=True,capture_output=True)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.job=self.root/'prepare';self.token='test-'+('a'*48);(self.root/'token').write_text(self.token)
        with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
        self.start()
    def start(self):
        self.p=subprocess.Popen([str(ROOT/'build/host/cammy-portal'),'--port',str(self.port),'--token-file',str(self.root/'token'),'--media-prepare-root',str(self.job),'--stock-preview'])
        self.a=Cammy(f'http://127.0.0.1:{self.port}',self.token,'alpha',timeout=2);self.boot=Cammy(self.a.endpoint,self.token,'cammy-boot',timeout=2)
        for _ in range(100):
            try:self.a.state();return
            except (OSError,CammyError):time.sleep(.01)
        self.fail('Portal not ready')
    def tearDown(self):
        if self.p.poll() is None:self.p.terminate()
        self.p.wait(timeout=5);self.tmp.cleanup()
    def error(self,code,fn):
        with self.assertRaises(CammyError) as e:fn()
        self.assertEqual(e.exception.status,code)
    def test_explicit_job_reserves_lease_and_duplicates_do_not_reissue(self):
        self.assertEqual(self.a.media_status()['phase'],'unprepared');self.assertIsNone(self.a.state()['owner'])
        self.assertTrue(self.a.prepare_media()['queued']);raw=(self.job/'request').read_bytes()
        self.assertIn(b'format=cammy-media-prepare/1',raw);self.assertEqual(self.a.state()['owner'],'cammy-boot')
        self.error(409,self.a.acquire);self.assertTrue(self.a.prepare_media()['queued']);self.assertEqual((self.job/'request').read_bytes(),raw)
        (self.job/'state').write_text('ready\n');self.boot.release();self.assertTrue(self.a.media_status()['ready']);self.a.acquire()
    def test_user_lease_blocks_preparation_without_side_effect(self):
        self.a.acquire();self.error(409,self.a.prepare_media);self.assertFalse(self.job.exists())
    def test_stop_cancels_pending_job_and_prevents_automatic_retry(self):
        self.a.prepare_media();self.a.stop();self.assertTrue((self.job/'cancel').exists())
        self.assertTrue(self.a.media_status()['requires_recovery']);self.error(503,self.a.prepare_media)
    def test_portal_restart_cancels_uncertain_job(self):
        self.a.prepare_media();self.p.kill();self.p.wait(timeout=5);self.start()
        self.assertEqual(self.a.media_status()['phase'],'cancelled');self.assertIsNone(self.a.state()['owner']);self.error(503,self.a.prepare_media)
    def test_request_schema_remains_strict(self):
        self.error(400,lambda:self.a.request('POST','/v1/media/prepare',{'path':'/dev/mem'}));self.assertFalse(self.job.exists())
    def test_the_preparation_job_may_start_the_preview_it_checks(self):
        # 0.7.5: native-media-start's 'start' step is POST /v1/preview/start as cammy-boot while
        # the job is still 'initializing'. Refusing it (409) failed every managed preparation as
        # event-unavailable (0.6.0-rc3, 0.7.3, 0.7.4 on 2026-11-16).
        self.a.prepare_media();(self.job/'state').write_text('initializing\n')
        try:self.boot.preview_start()
        except CammyError as e:self.assertNotEqual(e.status,409,'preparation must be allowed to start the preview')
        self.error(409,self.a.preview_start)                    # nobody else, and not before ready
        (self.job/'state').write_text('requires-recovery\n');self.boot.preview_stop()
        self.error(409,self.boot.preview_start)

