"""Real portal + fake capture executable + loopback native services. No devices."""
import json
import os
from pathlib import Path
import subprocess
import struct
import tempfile
import time
import unittest
import threading
from contextlib import contextmanager
from tests import test_preview_readiness as fixture


class AudioStreamTests(unittest.TestCase):
    setUp = fixture.PreviewTests.setUp
    tearDown = fixture.PreviewTests.tearDown
    status_until = fixture.PreviewTests.status_until
    start = fixture.PreviewTests.start

    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(); root = Path(cls.build.name)
        cls.modefile = root/'mode'; cls.pidfile = root/'child.pid'
        capture = root/'capture'
        capture.write_text('#!/usr/bin/python3\nimport os,time,signal\nfrom pathlib import Path\n'
            f"Path({str(cls.pidfile)!r}).write_text(str(os.getpid()))\n"
            f"mode=Path({str(cls.modefile)!r}).read_text()\n"
            "if mode=='fail':raise SystemExit(3)\n"
            "if mode=='ignore':signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            "if mode=='stall':time.sleep(10)\n"
            "while True:\n os.write(1,b'\\x11\\x22'*320)\n time.sleep(.02)\n")
        capture.chmod(0o700)
        harness = root/'harness.c'; cls.binary = root/'portal'
        harness.write_text('#define CAMMY_AUDIO_TEST 1\n#define CAMMY_AUDIO_EXEC '+json.dumps(str(capture))+'\n'
            '#define main portal_main\n#include "'+str(fixture.ROOT/'src/cammy_portal.c')+'"\n#undef main\n'
            'int main(int argc,char **argv){if(argc<5)return 2;preview_event_port=atoi(argv[1]);'
            'preview_rtsp_port=atoi(argv[2]);preview_keepalive_seconds=.12;probe_prefix=argv[3];audio_enabled=true;'
            'char path[256];snprintf(path,sizeof path,"%s/capture-page",argv[3]);'
            'if(cfs_map_file(path,1,&face_page))return 3;'
            'atomic_store(face_page->header+CFS_HEADER_CAPTURE,2u);'
            'return portal_main(argc-3,argv+3);}\n')
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror',str(harness),'-o',str(cls.binary)],check=True,capture_output=True)
        cls.modefile.write_text('normal')

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def prepare(self, mode='normal', ttl=10):
        self.modefile.write_text(mode)
        self.start(ttl); self.status_until(lambda s:s['event_connected'] and s['commands_sent']>0)

    def reaped(self):
        deadline=time.monotonic()+2
        while True:
            state=self.a.audio_stream_status()
            if not state['capture_child_alive'] and not state['cleanup_pending']:return state
            if time.monotonic()>deadline:self.fail('Capture child not reaped: '+str(state))
            time.sleep(.02)

    @contextmanager
    def gateway(self):
        from tests.test_host import host
        from cammy import Cammy
        gateway=host.Gateway(self.a.token,[self.a.endpoint])
        self.assertTrue(gateway.refresh()['connected'])
        server=host.make_server(gateway,('127.0.0.1',0))
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:yield Cammy('http://127.0.0.1:'+str(server.server_port),self.a.token,self.a.agent)
        finally:server.shutdown();server.server_close();thread.join()

    def test_gateway_stream_and_stop_are_independent(self):
        self.prepare()
        with self.gateway() as client:
            with client.open_audio_stream() as response:
                self.assertEqual(response.read(640),b'\x11\x22'*320)
                self.assertTrue(client.audio_stream_status()['capture_child_alive'])
                start=time.monotonic();client.stop();self.assertLess(time.monotonic()-start,.5)
                self.reaped()

    def test_gateway_disconnect_cancels_source_and_invalid_json_is_400(self):
        import urllib.request,urllib.error
        self.prepare()
        with self.gateway() as client:
            request=urllib.request.Request(client.endpoint+'/v1/audio/stream',data=b'{',method='POST',
                headers={'Authorization':'Bearer '+client.token,'X-Agent-ID':client.agent})
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(request,timeout=2)
            self.assertEqual(e.exception.code,400);e.exception.close()
            with client.open_audio_stream() as response:response.read(640)
            self.reaped()

    def test_live_pcm_arrives_and_disconnect_reaps_child(self):
        self.prepare()
        with self.a.open_audio_stream() as response:
            data=response.read(1280)
            self.assertEqual(data,b'\x11\x22'*640)
            self.assertTrue(self.a.audio_stream_status()['capture_child_alive'])
        state=self.reaped();self.assertFalse(state['hardware_cleanup_confirmed'])

    def test_physical_switch_stops_and_refuses_microphone_until_unmuted(self):
        def press(sequence):
            with (self.root / 'capture-page').open('r+b', buffering=0) as page:
                page.seek(15 * 4)
                page.write(struct.pack('=I', sequence))
            self.assertEqual(self.a.state()['capture_sequence'], sequence)

        self.prepare()
        with self.a.open_audio_stream() as response:
            self.assertEqual(len(response.read(640)), 640)
            press(3)
            self.reaped()
        with self.assertRaisesRegex(fixture.CammyError, 'physical capture mute'):
            self.a.open_audio_stream()
        self.assertTrue(self.a.preview_status()['event_connected'])
        press(4)
        with self.a.open_audio_stream() as response:
            self.assertEqual(len(response.read(640)), 640)

    def test_stop_remains_responsive_and_kills_ignoring_child(self):
        self.prepare('ignore')
        with self.a.open_audio_stream() as response:
            self.assertEqual(len(response.read(640)),640)
            start=time.monotonic();self.a.stop();self.assertLess(time.monotonic()-start,.5)
            self.reaped()

    def test_release_expiry_and_preview_stop_cancel(self):
        for action in ('release','expiry','preview_stop'):
            with self.subTest(action=action):
                self.prepare(ttl=1 if action=='expiry' else 10)
                with self.a.open_audio_stream() as response:
                    response.read(640)
                    if action=='expiry':time.sleep(1.05)
                    else:getattr(self.a,action)()
                    self.reaped()

    def test_auth_owner_and_duplicate_stream_refused(self):
        from cammy import Cammy, CammyError
        self.prepare()
        for client,status in ((Cammy(self.a.endpoint,'bad','alpha'),401),(self.b,409)):
            with self.assertRaises(CammyError) as e:client.open_audio_stream()
            self.assertEqual(e.exception.status,status)
        with self.a.open_audio_stream() as response:
            response.read(640)
            with self.assertRaises(CammyError) as e:self.a.open_audio_stream()
            self.assertEqual(e.exception.status,409)

    def test_failed_capture_returns_json_error_without_false_pcm_headers(self):
        from cammy import CammyError
        self.prepare('fail')
        with self.assertRaises(CammyError) as e:self.a.open_audio_stream()
        self.assertEqual(e.exception.status,503);self.reaped()

    def test_stalled_start_is_bounded_and_control_works(self):
        from cammy import CammyError
        self.prepare('stall');self.a.timeout=4
        start=time.monotonic()
        with self.assertRaises(CammyError) as e:self.a.open_audio_stream()
        self.assertEqual(e.exception.status,503);self.assertLess(time.monotonic()-start,3)
        self.reaped();self.assertEqual(self.a.state()['owner'],'alpha')

    def test_portal_death_stops_capture_child(self):
        self.prepare()
        with self.a.open_audio_stream() as response:
            response.read(640);pid=int(self.pidfile.read_text())
            self.proc.kill();self.proc.wait(timeout=2)
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                p=Path('/proc')/str(pid)/'stat'
                if not p.exists() or p.read_text().split()[2]=='Z':break
                time.sleep(.02)
            else:self.fail('Capture still running after portal death')


if __name__=='__main__':unittest.main()
