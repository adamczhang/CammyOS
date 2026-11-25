"""Control-only ownership and explicit media startup, using local fake bodies."""
import sys,threading,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'host'),str(ROOT/'sdk')]
from cammy_control import ControlSession
from tests.test_cammy_live import FakeClient,LiveSession,fake_process,wait_for

class ControlTests(unittest.TestCase):
    def setUp(self):
        self.body=FakeClient();self.boot='1'*32
        old=self.body.state
        self.body.state=lambda:dict(old(),boot_id=self.boot)
        self.control=ControlSession(lambda:self.body,read_boot=lambda _:self.boot)
    def tearDown(self):self.control.stop(wait=True)
    def test_control_lease_has_no_media_and_refuses_commands_after_stop(self):
        calls=[];self.body.face=lambda **v:calls.append(v) or {'queued':True}
        self.control.start(5);wait_for(lambda:self.control.status()['phase']=='control')
        self.control.body_command('face',expression='happy')
        self.assertEqual(calls,[{'expression':'happy'}]);self.assertFalse(self.body.preview)
        self.assertEqual(self.control.samples,0);self.assertFalse(self.control.status()['sensing_requested'])
        self.control.stop(wait=True);self.assertEqual(self.body.released,1)
        with self.assertRaises(RuntimeError):self.control.body_command('face',expression='angry')
    def test_boot_change_faults_without_reacquiring_or_stopping_another_owner(self):
        self.control.start(5);wait_for(lambda:self.control.status()['phase']=='control')
        self.boot='2'*32
        wait_for(lambda:self.control.status()['phase']=='faulted')
        self.assertEqual(self.body.acquired,1);self.assertFalse(self.body.preview)

class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.body=FakeClient();self.ready=threading.Event();self.prepared=threading.Event();self.cancelled=0
        self.body.media_status=lambda:{'managed':True,'ready':self.ready.is_set(),'requires_recovery':False}
        def prepare():self.prepared.set();return {'created':True}
        def cancel():self.cancelled+=1
        self.body.prepare_media=prepare;self.body.cancel_media_preparation=cancel
        self.live=LiveSession(lambda:self.body,fake_process,lambda _:False,lambda _:'boot-a',synthetic=True)
    def tearDown(self):self.live.stop(wait=True)
    def test_preparation_finishes_before_acquire_decoder_and_microphone(self):
        self.live.start(5);self.assertTrue(self.prepared.wait(1))
        self.assertEqual(self.body.acquired,0);self.assertFalse(self.body.preview)
        self.assertEqual(self.live.status()['phase'],'preparing')
        self.ready.set();wait_for(lambda:self.live.status()['frames']>0 and self.live.samples>0)
        self.assertEqual(self.body.acquired,1)
        self.live.stop(wait=True);self.assertEqual(self.cancelled,0)
    def test_stop_cancels_only_its_created_preparation(self):
        self.live.start(5);self.assertTrue(self.prepared.wait(1))
        self.live.stop(wait=True)
        self.assertEqual(self.cancelled,1);self.assertEqual(self.body.acquired,0)
        self.assertEqual(self.body.released,0);self.assertFalse(self.body.preview)
    def test_existing_preparation_is_not_cancelled_by_this_session(self):
        def prepare():self.prepared.set();return {'created':False}
        self.body.prepare_media=prepare
        self.live.start(5);self.assertTrue(self.prepared.wait(1));self.live.stop(wait=True)
        self.assertEqual(self.cancelled,0)
