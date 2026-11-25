"""Snapshot transaction tests: synthetic native app, real loopback gateway/SDK."""
import io
import json
from pathlib import Path
import sys
import struct
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from cammy_vision import Vision, VisionError, NativePhoto, _Listing, jpeg_info, jpeg_payload, DIRECTORY
from cammy_host import Gateway, make_server
from cammy import Cammy, CammyError
from PIL import Image
from tools.vision_trial import preserved

BOOT = '8a97b4f6-cf3c-46a6-bcf9-e0013c4e88fb'


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (48, 32), '#1177aa').save(out, 'JPEG')
    return out.getvalue()


class Body:
    def __init__(self):
        self.owner = 'test'
        self.ttl = 60
        self.events = []
        self.boot = BOOT
        self.connected = True
        self.preview = False
        self.calls = []

    def snapshot(self):
        return {'connected': self.connected, 'endpoint': 'http://127.0.0.1:8787'}

    def request(self, endpoint, method, path, body=None, agent='test'):
        self.calls.append((method, path))
        if path == '/v1/state':
            result = {'owner': self.owner, 'lease_remaining_seconds': self.ttl,
                      'preview_active': self.preview, 'event_sequence': len(self.events)}
        elif path == '/v1/events':
            result = {'latest': len(self.events), 'events': self.events}
        elif path == '/v1/probes':
            result = {'job_id': 'job-1'}
        elif path == '/v1/probes/job-1':
            result = {'status': 'completed', 'result': {'boot_id': {
                'available': True, 'truncated': False, 'value': self.boot + '\n'}}}
        else:
            result = {'ok': True}
        return 200, json.dumps(result).encode()


class Photo:
    synthetic = True

    def __init__(self):
        self.current = 0
        self.names = {'OLD.JPG'}
        self.calls = []
        self.sent = threading.Event()
        self.ready = threading.Event()
        self.ready.set()
        self.fail_restore = False
        self.on_image = None
        self.data = jpeg()

    def status(self):
        return self.current

    def files(self):
        return self.names | ({'NEW.JPG'} if self.sent.is_set() and self.ready.is_set() else set())

    def mode(self, value):
        self.calls.append(('mode', value))
        if value == 0 and self.fail_restore:
            raise OSError('restore failed')
        self.current = value

    def shutter(self):
        self.calls.append(('shutter', 1))
        self.sent.set()

    def image(self, name):
        self.calls.append(('image', name))
        if self.on_image:
            self.on_image()
        return self.data


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.body, self.photo = Body(), Photo()
        self.vision = Vision(self.body, True, lambda _: self.photo)

    def tearDown(self):
        self.photo.ready.set()
        self.vision.close()

    def finish(self):
        self.vision.thread.join(3)
        self.assertFalse(self.vision.thread.is_alive())
        return self.vision.get('test', self.vision.job['id'])

    def start(self):
        return self.vision.start('test')

    def test_fresh_jpeg_restores_mode_and_labels_synthetic(self):
        first = self.start()
        result = self.finish()
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(result['filename'], 'NEW.JPG')
        self.assertTrue(result['synthetic'])
        self.assertEqual(result['boot_id'], BOOT)
        self.assertTrue(result['mode_restored'])
        self.assertFalse(result['hardware_cleanup_confirmed'])
        self.assertEqual((result['width'], result['height']), (48, 32))
        self.assertEqual(self.photo.calls, [('mode', 4), ('shutter', 1), ('image', 'NEW.JPG'), ('mode', 0)])
        self.assertEqual(self.vision.get('test', first['id'], True), self.photo.data)
        self.assertNotIn('data', result)
        self.assertNotIn('endpoint', result)

    def test_admission_guards_never_dispatch(self):
        for field, value, code in [('owner', 'other', 403), ('ttl', 20, 409),
                                   ('preview', True, 409), ('connected', False, 503)]:
            old = getattr(self.body, field)
            setattr(self.body, field, value)
            with self.assertRaises(VisionError) as exc:
                self.start()
            self.assertEqual(exc.exception.status, code)
            setattr(self.body, field, old)
        self.vision.enabled = False
        with self.assertRaises(VisionError) as exc:
            self.start()
        self.assertEqual(exc.exception.status, 501)
        self.assertEqual(self.photo.calls, [])

    def test_missing_optional_decoder_refuses_before_native_action(self):
        with patch.dict(sys.modules, {'PIL': None}):
            with self.assertRaises(VisionError) as exc:
                self.start()
        self.assertEqual(exc.exception.status, 503)
        self.assertEqual(self.photo.calls, [])

    def test_already_photo_mode_is_preserved(self):
        self.photo.current = 4
        self.start()
        self.assertEqual(self.finish()['phase'], 'completed')
        self.assertFalse(any(c[0] == 'mode' for c in self.photo.calls))

    def test_cancel_waits_for_finite_capture_then_restores_without_delivery(self):
        self.photo.ready.clear()
        self.start()
        self.assertTrue(self.photo.sent.wait(2))
        with self.assertRaises(VisionError) as exc:
            with self.vision.control('/v1/stop', 'test'):
                self.fail('Stop must not acknowledge native cleanup prematurely')
        self.assertEqual(exc.exception.status, 409)
        for path in ['/v1/session', '/v1/preview/start', '/v1/look']:
            with self.assertRaises(VisionError):
                with self.vision.control(path, 'test'):
                    self.fail('Mutation raced capture')
        self.photo.ready.set()
        result = self.finish()
        self.assertEqual(result['phase'], 'cancelled')
        self.assertFalse(result['image_available'])
        self.assertEqual(self.photo.current, 0)
        with self.vision.control('/v1/stop', 'test'):
            pass

    def test_owner_takeover_drops_image_and_does_not_clobber_owner_mode(self):
        self.photo.on_image = lambda: setattr(self.body, 'owner', 'other')
        self.start()
        result = self.finish()
        self.assertEqual(result['phase'], 'failed')
        self.assertFalse(result['image_available'])
        self.assertEqual(self.photo.current, 4)
        self.assertIsNotNone(self.vision.fault)

    def test_disconnect_suppresses_late_result(self):
        self.photo.on_image = self.vision.disconnected
        self.start()
        result = self.finish()
        self.assertEqual(result['phase'], 'cancelled')
        self.assertFalse(result['image_available'])
        self.assertEqual(self.photo.current, 0)

    def test_release_reacquire_same_name_invalidates_result(self):
        def cycle():
            self.body.events = [{'sequence': 1, 'type': 'session.released'},
                                {'sequence': 2, 'type': 'session.acquired'}]
        self.photo.on_image = cycle
        self.start()
        result = self.finish()
        self.assertEqual(result['phase'], 'cancelled')
        self.assertFalse(result['image_available'])

        self.assertEqual(self.photo.current, 4)
        self.assertIn('owner changed', self.vision.fault)

    def test_boot_change_rejects_frame(self):
        self.photo.on_image = lambda: setattr(self.body, 'boot', '00000000-0000-0000-0000-000000000000')
        self.start()
        result = self.finish()
        self.assertEqual(result['phase'], 'failed')
        self.assertFalse(result['image_available'])
        self.assertEqual(self.photo.current, 4)
        self.assertIn('identity changed', self.vision.fault)

    def test_restore_failure_latches_backend_unavailable(self):
        self.photo.fail_restore = True
        self.start()
        self.assertEqual(self.finish()['phase'], 'failed')
        self.assertFalse(self.vision.capabilities()['available'])
        with self.assertRaises(VisionError):
            self.start()

    def test_shutter_failure_is_never_retried(self):
        def fail():
            self.photo.calls.append(('shutter', 1))
            raise TimeoutError('reply lost')
        self.photo.shutter = fail
        self.start()
        self.assertEqual(self.finish()['phase'], 'failed')
        self.assertEqual(self.photo.calls.count(('shutter', 1)), 1)
        self.assertIsNotNone(self.vision.fault)

    def test_uncertain_mode_dispatch_never_claims_restoration(self):
        def fail(value):
            self.photo.calls.append(('mode', value))
            raise TimeoutError('mode echo lost while action may still be queued')
        self.photo.mode = fail
        self.start()
        result = self.finish()
        self.assertEqual(result['phase'], 'failed')
        self.assertIsNone(result['mode_restored'])
        self.assertEqual(self.photo.calls, [('mode', 4)])
        self.assertIn('Mode dispatch uncertain', self.vision.fault)

    def test_cancel_after_mode_echo_waits_for_queued_mode_before_restoring(self):
        original = self.photo.mode
        def cancel(value):
            original(value)
            if value == 4:
                self.vision.job['cancel'].set()
        self.photo.mode = cancel
        self.start()
        result = self.finish()
        self.assertEqual(result['phase'], 'cancelled')
        self.assertTrue(result['mode_restored'])
        self.assertEqual(self.photo.calls, [('mode', 4), ('mode', 0)])

    def test_command_value_is_success_code_not_echoed_parameter(self):
        native = NativePhoto('http://127.0.0.1:8787')
        native._read = lambda *args: b'{"Cmd":3034,"Value":0}'
        native.mode(4)
        for result in [{'Cmd': 3034, 'Value': 4}, {'Cmd': 1100, 'Value': 0},
                       {'Cmd': 3034, 'Value': False}, {'status': 0}]:
            native._read = lambda *args, result=result: json.dumps(result).encode()
            with self.assertRaises(VisionError):
                native.mode(4)

    def test_image_access_requires_same_owner_and_freshness(self):
        self.start()
        result = self.finish()
        with self.assertRaises(VisionError) as exc:
            self.vision.get('other', result['id'], True)
        self.assertEqual(exc.exception.status, 403)
        self.vision.job['received_mono'] -= 11
        with self.assertRaises(VisionError) as exc:
            self.vision.get('test', result['id'], True)
        self.assertEqual(exc.exception.status, 410)

    def test_reboot_after_capture_invalidates_cached_image(self):
        self.start()
        result = self.finish()
        self.body.boot = '00000000-0000-0000-0000-000000000000'
        with self.assertRaises(VisionError) as exc:
            self.vision.get('test', result['id'], True)
        self.assertEqual(exc.exception.status, 409)
        self.assertIsNone(self.vision.job['data'])

    def test_stop_during_image_provenance_read_suppresses_pending_delivery(self):
        self.start()
        result = self.finish()
        entered, ready = threading.Event(), threading.Event()
        errors = []
        def blocked_boot(job):
            entered.set()
            if not ready.wait(2):
                raise TimeoutError('test barrier')
            return BOOT
        self.vision._boot = blocked_boot
        def read():
            try:
                self.vision.get('test', result['id'], True)
            except VisionError as exc:
                errors.append(exc)
        thread = threading.Thread(target=read)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.vision.control('/v1/stop', 'test'):
                pass
        finally:
            ready.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].status, 409)

    def test_ambiguous_new_files_are_not_published(self):
        original = self.photo.files
        self.photo.files = lambda: original() | ({'SECOND.JPG'} if self.photo.sent.is_set() else set())
        self.start()
        self.assertEqual(self.finish()['phase'], 'failed')
        self.assertIsNotNone(self.vision.fault)
        self.assertFalse(any(c[0] == 'image' for c in self.photo.calls))

    def test_no_old_photo_fallback_when_capture_has_no_new_file(self):
        # Advance the capture clock instead of waiting twelve real seconds.
        self.photo.ready.clear()
        clock = [0]
        self.vision.clock = lambda: clock[0]
        def files():
            if self.photo.sent.is_set():
                clock[0] = 13
            return {'OLD.JPG'}
        self.photo.files = files
        self.start()
        self.assertEqual(self.finish()['phase'], 'failed')
        self.assertFalse(any(c[0] == 'image' for c in self.photo.calls))
        self.assertIsNotNone(self.vision.fault)

    def test_directory_parser_and_jpeg_validation(self):
        parser = _Listing()
        parser.feed(''.join('<a href="' + p + '">x</a>' for p in [
            'OK.JPG', DIRECTORY + 'NEXT.jpeg', '../BAD.JPG', '%2e%2e/BAD.JPG',
            'http://other/OTHER.JPG', '/etc/SECRET.JPG', 'x.JPG?q=1']))
        self.assertEqual(parser.names, {'OK.JPG', 'NEXT.jpeg'})
        for data in [b'not-jpeg', jpeg()[:-2], b'\xff\xd8bad\xff\xd9']:
            with self.assertRaises(VisionError):
                jpeg_info(data)
        for endpoint in ['https://127.0.0.1', 'http://user:pw@127.0.0.1', 'http://camera.invalid']:
            with self.assertRaises(VisionError):
                NativePhoto(endpoint)

    def test_m105_footer_is_length_bound_and_normalized_before_sdk_delivery(self):
        payload = jpeg()
        raw = payload + struct.pack('<4I', 1344, 0, 6848, len(payload))
        self.photo.data = raw
        self.start()
        result = self.finish()
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(result['stripped_footer_bytes'], 16)
        self.assertEqual(result['camera_file_bytes'], len(raw))
        self.assertEqual(self.vision.get('test', result['id'], True), payload)
        for invalid in [raw[:-1], raw[:-4] + struct.pack('<I', len(payload) + 1),
                        payload[:-2] + raw[-16:], payload + b'unknown']:
            with self.assertRaises(VisionError):
                jpeg_payload(invalid)

    def test_preservation_checks_code_identity_without_allocator_annotations(self):
        before = {k: 'same' for k in ('boot', 'native', 'hashes', 'kernel', 'cmdline', 'wd_info', 'mounts', 'selection')}
        before['native_maps'] = '1000-2000 r-xp 0 01:00 42 a:778:sdvcam 1:778 /usr/bin/sdvcam\n3000-4000 rwxp 0 00:00 0 a:778:sdvcam 1:778\n'
        after = dict(before, native_maps=before['native_maps'].replace('1:778', '2:778-1185'))
        preserved(before, after)
        for changed in [dict(after, boot='new'), dict(after, native_maps=after['native_maps'].replace('1000-2000', '1000-3000'))]:
            with self.assertRaises(ValueError):
                preserved(before, changed)


class GatewayVisionTests(unittest.TestCase):
    def test_sdk_loopback_delivery_authentication_and_defaults(self):
        body, photo = Body(), Photo()
        gateway = Gateway('secret', [], native_photo=True)
        gateway.request = body.request
        gateway.state.update(body.snapshot())
        gateway.vision.native_factory = lambda _: photo
        http = make_server(gateway, ('127.0.0.1', 0))
        thread = threading.Thread(target=http.serve_forever)
        thread.start()
        endpoint = 'http://127.0.0.1:' + str(http.server_port)
        try:
            client = Cammy(endpoint, 'secret', agent='test')
            self.assertTrue(client.vision_capabilities()['available'])
            result = client.capture_frame()
            self.assertTrue(result['metadata']['synthetic'])
            self.assertEqual(result['jpeg'], photo.data)
            bad = Cammy(endpoint, 'wrong', agent='test')
            with self.assertRaises(CammyError) as exc:
                bad.frame_image(result['metadata']['id'])
            self.assertEqual(exc.exception.status, 401)
            with client.opener.open(endpoint + '/') as response:
                self.assertIn('img-src blob:', response.headers['Content-Security-Policy'])
            gateway.vision.enabled = False
            with self.assertRaises(CammyError) as exc:
                client.frame_start()
            self.assertEqual(exc.exception.status, 501)
        finally:
            gateway.vision.close()
            http.shutdown()
            http.server_close()
            thread.join()


class NativeHTTPTests(unittest.TestCase):
    def test_exact_methods_parameters_directory_and_bounded_download(self):
        calls, data = [], jpeg()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                calls.append(('GET', self.path))
                if 'cmd=2008' in self.path:
                    payload = b'{"current_mode":0,"curworkmodename":"NormalRecordeMode"}'
                elif 'cmd=2005' in self.path:
                    payload = b'{"RecodStatus":0}'
                elif self.path == DIRECTORY:
                    payload = ('<a href="' + DIRECTORY + 'ONE.JPG">photo</a>').encode()
                elif self.path == DIRECTORY + 'ONE.JPG':
                    payload = data
                else:
                    self.send_response(302)
                    self.send_header('Location', '/elsewhere')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_POST(self):
                calls.append(('POST', self.path, self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                from urllib.parse import urlsplit, parse_qs
                args = parse_qs(urlsplit(self.path).query)
                self.wfile.write(json.dumps({'Cmd': int(args['cmd'][0]), 'Value': 0}).encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            native = NativePhoto('http://127.0.0.1:8787')
            # Test fixture only: production derives a fixed camera port, no URL input.
            native.origin = 'http://127.0.0.1:' + str(server.server_port)
            self.assertEqual(native.status(), 0)
            native.mode(4)
            native.shutter()
            self.assertEqual(native.files(), {'ONE.JPG'})
            self.assertEqual(native.image('ONE.JPG'), data)
            self.assertIn(('POST', '/api/setdeviceinfo/?custom=1&cmd=3034&par=4', b''), calls)
            self.assertIn(('POST', '/api/setdeviceinfo/?custom=1&cmd=1100&par=1', b''), calls)
            with self.assertRaises(VisionError):
                native._read('/redirect')
            self.assertNotIn(('GET', '/elsewhere'), calls)
            with self.assertRaises(VisionError):
                native._read(DIRECTORY + 'ONE.JPG', limit=20)
            before = len(calls)
            with self.assertRaises(VisionError):
                native.image('../OTHER.JPG')
            self.assertEqual(len(calls), before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
