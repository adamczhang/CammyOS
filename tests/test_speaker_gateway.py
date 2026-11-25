"""Full SDK->PC gateway->real portal path, synthetic guardian only."""
import sys,threading,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'host'));sys.path.insert(0,str(ROOT/'sdk'))
from tests import test_speaker_api as fixtures
from cammy_host import Gateway,make_server
from cammy import Cammy,CammyError
class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.SpeakerApiTests.setUpClass()
    def setUp(self):
        self.fixture=fixtures.SpeakerApiTests();self.fixture.setUp()
        self.gateway=Gateway(self.fixture.token,[self.fixture.a.endpoint]);self.assertTrue(self.gateway.refresh()['connected'])
        self.http=make_server(self.gateway,('127.0.0.1',0));self.thread=threading.Thread(target=self.http.serve_forever);self.thread.start()
        self.client=Cammy('http://127.0.0.1:'+str(self.http.server_port),self.fixture.token,'gateway-test',timeout=2)
    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.gateway.vision.close();self.fixture.tearDown()
    def test_large_bounded_pcm_and_status_forward_with_boot_lease_binding(self):
        self.client.acquire();self.assertTrue(self.client.play_pcm(bytes(60800))['queued'])
        self.fixture.settled();self.assertEqual(self.client.speaker_status()['phase'],'finished');self.client.release()
    def test_cancel_forwarding_stops_only_current_owner(self):
        (self.fixture.runtime/'hang').touch();self.client.acquire();self.client.play_pcm(bytes(160000));self.fixture.playing()
        self.client.speaker_cancel();self.assertEqual(self.fixture.settled()['phase'],'cancelled');self.client.release()
    def test_gateway_keeps_small_limit_for_other_operations(self):
        self.client.acquire()
        with self.assertRaises(CammyError) as error:self.client.request('POST','/v1/face',{'extra':'x'*1200})
        self.assertEqual(error.exception.status,413);self.client.release()
