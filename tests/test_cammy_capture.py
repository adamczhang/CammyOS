"""host/cammy_capture.py: picture/video hand-off with a scripted stock camera (no hardware).

.venv-live/bin/python -m unittest tests.test_cammy_capture
"""
import io
import struct
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import cammy_capture as C  # noqa: E402


def jpeg(size=(64, 48)):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', size, (200, 120, 40)).save(out, 'JPEG')
    return out.getvalue()


class BatteryTests(unittest.TestCase):
    def test_stock_get_battery_reports_raw_level_and_usb_power(self):
        device = C.Native('http://127.0.0.1:8787')
        for level, powered, warning in [(0, False, True), (1, False, True),
                                        (4, False, False), (5, True, False)]:
            with self.subTest(level=level), unittest.mock.patch.object(
                    device.photo, '_read', return_value=b'{"level":%d}' % level) as read:
                battery = device.battery()
                read.assert_called_once_with('/api/getdeviceinfo/?custom=1&cmd=2009')
                self.assertEqual((battery['level'], battery['external_power'], bool(battery['warning'])),
                                 (level, powered, warning))

    def test_invalid_battery_level_is_unavailable(self):
        device = C.Native('http://127.0.0.1:8787')
        for raw in [b'{}', b'[]', b'{"level":true}', b'{"level":6}']:
            with self.subTest(raw=raw), unittest.mock.patch.object(device.photo, '_read', return_value=raw):
                with self.assertRaises(C.CaptureError):
                    device.battery()


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeNative:
    """Stock camera. script: {time: action}; actions run once the clock passes them."""

    def __init__(self, clock, mode=C.VIDEO, files=('ANMP0000.jpg',), script=None, switches=True):
        self.clock, self.mode, self.recording = clock, mode, False
        self.names = set(files)
        self.script = sorted((script or {}).items())
        self.switches, self.log, self.blobs, self.deletes = switches, [], {}, True

    def _tick(self):
        while self.script and self.script[0][0] <= self.clock.t:
            _, action = self.script.pop(0)
            action(self)

    def state(self):
        self._tick()
        return self.mode, self.recording

    def set_mode(self, value):
        self.log.append(('mode', value))
        if self.switches:
            self.mode = value

    def stop_recording(self):
        self.log.append(('stop',))
        self.recording = False
        self.names.add('L0010099.mp4')

    def files(self):
        self._tick()
        return set(self.names)

    def delete(self, name):
        self.log.append(('delete', name))
        if self.deletes:
            self.names.discard(name)

    def download(self, name, dest, limit=0):
        self.log.append(('download', name))
        j = jpeg()
        dest.write_bytes(self.blobs.get(name, j + bytes(12) + struct.pack('<I', len(j))))
        return dest.stat().st_size


class Hooks:
    def __init__(self, log):
        self.log = log

    def hide_face(self):
        self.log.append(('hide',))

    def show_face(self):
        self.log.append(('show',))


def photo_taken(at, name='ANMP0001.jpg'):
    return {at: lambda n: n.names.add(name)}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def capture(self, native, **kw):
        return C.Capture(native, Hooks(native.log), out_dir=self.tmp.name, clock=self.clock,
                         sleep=self.clock.sleep, **kw)

    def test_photo_switches_mode_hides_face_and_restores_everything(self):
        n = FakeNative(self.clock, mode=C.VIDEO, script=photo_taken(7.0))
        r = self.capture(n).run('photo')
        self.assertNotIn('error', r)
        self.assertEqual(r['name'], 'ANMP0001.jpg')
        self.assertEqual(n.log, [('hide',), ('mode', C.PHOTO), ('mode', C.VIDEO), ('show',), ('download', 'ANMP0001.jpg'),
                                 ('delete', 'ANMP0001.jpg')])
        self.assertFalse(r['on_card'])
        self.assertEqual(n.names, {'ANMP0000.jpg'})          # Adam's own picture stays
        saved = Path(r['path']).read_bytes()
        self.assertTrue(saved.endswith(b'\xff\xd9'))       # vendor footer stripped
        self.assertEqual(len(r['gemini']), 1)
        self.assertTrue(r['gemini'][0].startswith(b'\xff\xd8'))
        self.assertIn('photo', C.gemini_text(r))

    def test_unreadable_copy_stays_on_the_card(self):
        n = FakeNative(self.clock, mode=C.PHOTO, script=photo_taken(1.0))
        n.blobs['ANMP0001.jpg'] = b''
        r = self.capture(n).run('photo')
        self.assertIn('error', r)
        self.assertNotIn('delete', [e[0] for e in n.log])
        self.assertIn('ANMP0001.jpg', n.names)

    def test_failed_delete_is_reported_not_fatal(self):
        n = FakeNative(self.clock, mode=C.PHOTO, script=photo_taken(1.0))
        n.deletes = False
        r = self.capture(n).run('photo')
        self.assertNotIn('error', r)
        self.assertTrue(r['on_card'])

    def test_keep_on_card_option(self):
        n = FakeNative(self.clock, mode=C.PHOTO, script=photo_taken(1.0))
        r = self.capture(n, keep_on_card=True).run('photo')
        self.assertTrue(r['on_card'])
        self.assertNotIn('delete', [e[0] for e in n.log])

    def test_photo_mode_kept_when_already_selected(self):
        n = FakeNative(self.clock, mode=C.PHOTO, script=photo_taken(1.0))
        r = self.capture(n).run('photo')
        self.assertNotIn('error', r)
        self.assertNotIn(('mode', C.VIDEO), n.log)
        self.assertEqual([e for e in n.log if e[0] == 'mode'], [])

    def test_video_waits_for_start_and_stop(self):
        def start(n):
            n.recording = True

        def stop(n):
            n.recording = False
            n.names.add('L0010010.mp4')
        n = FakeNative(self.clock, mode=C.PHOTO, script={5.0: start, 6.0: lambda n: n.names.add('L0010010.mp4.tmp'), 40.0: stop})
        cap = self.capture(n)
        with unittest.mock.patch.object(C, 'clip_frames', return_value=[b'\xff\xd8']):
            r = cap.run('video')
        self.assertNotIn('error', r, r)
        self.assertEqual(r['name'], 'L0010010.mp4')
        self.assertGreaterEqual(self.clock.t, 40.0)
        self.assertEqual(n.log[:3], [('hide',), ('mode', C.VIDEO), ('mode', C.PHOTO)])
        self.assertIn(('mode', C.PHOTO), n.log)   # restored after the clip
        self.assertFalse(cap.busy)
        self.assertEqual(n.log[-1], ('delete', 'L0010010.mp4'))
        self.assertNotIn('L0010010.mp4', n.names)

    def test_forgotten_recording_is_stopped_at_the_cap(self):
        def start(n):
            n.recording = True
        n = FakeNative(self.clock, mode=C.VIDEO, script={2.0: start})
        r = self.capture(n, max_video=30.0).run('video')
        self.assertIn(('stop',), n.log)
        self.assertEqual(r.get('name'), 'L0010099.mp4')

    def test_no_press_times_out_and_the_face_comes_back(self):
        n = FakeNative(self.clock, mode=C.PHOTO)
        r = self.capture(n, photo_wait=20.0).run('photo')
        self.assertIn('timed out', r['error'])
        self.assertEqual(n.log, [('hide',), ('show',)])
        self.assertIn('failed', C.gemini_text(r))

    def test_waiting_for_a_press_lists_the_card_rarely(self):
        n = FakeNative(self.clock, mode=C.PHOTO)
        listings = []
        files = n.files
        n.files = lambda: (listings.append(self.clock()), files())[1]
        r = self.capture(n).run('photo')
        self.assertIn('timed out', r['error'])
        during = [t for t in listings[1:]]          # the first one is the 'before' snapshot
        self.assertLessEqual(len(during), C.PHOTO_WAIT_S / C.LIST_EVERY_S + 2)
        self.assertTrue(all(b - a >= C.LIST_EVERY_S - 1e-6 for a, b in zip(during, during[1:])))

    def test_mode_that_never_switches_restores_prior_face(self):
        n = FakeNative(self.clock, mode=C.VIDEO, switches=False)
        r = self.capture(n).run('photo')
        self.assertIn('photo mode', r['error'])
        self.assertIn(('hide',), n.log)
        self.assertIn(('show',), n.log)   # show is always sent on the way out

    def test_refuses_while_the_camera_records_or_busy(self):
        n = FakeNative(self.clock)
        n.recording = True
        self.assertIn('already recording', self.capture(n).run('video')['error'])
        cap = self.capture(FakeNative(self.clock))
        cap.busy = True
        self.assertIn('already', cap.run('photo')['error'])

    def test_cooldown_and_unique_path_for_reused_stock_counter(self):
        n = FakeNative(self.clock, script=photo_taken(1.0))
        cap = self.capture(n)
        first = cap.run('photo')
        self.assertIn('cooling down', cap.run('photo')['error'])
        self.clock.sleep(30)
        n.script = list(photo_taken(self.clock.t + 1).items())
        second = cap.run('photo')
        self.assertEqual(first['name'], second['name'])
        self.assertNotEqual(first['path'], second['path'])
        self.assertTrue(Path(first['path']).is_file())

    def test_failed_restore_leaves_face_hidden_and_refuses_more_jobs(self):
        n = FakeNative(self.clock, script=photo_taken(1.0))
        switch = n.set_mode
        n.set_mode = lambda value: switch(value) if value == C.PHOTO else None
        cap = self.capture(n)
        self.assertIn('restore was not confirmed', cap.run('photo')['error'])
        self.assertNotIn(('show',), n.log)
        before = list(n.log)
        self.clock.sleep(100)
        self.assertIn('halted', cap.run('video')['error'])
        self.assertEqual(n.log, before)

    def test_delete_request_uses_the_stock_command_and_full_path(self):
        native = C.Native.__new__(C.Native)
        native.photo = unittest.mock.Mock()
        native.delete('L0010010.mp4')
        native.photo._post.assert_called_once_with(4003, '/mnt/extsd/DCIM/100MEDIA/L0010010.mp4')
        for bad in ('../x.mp4', 'a=b.mp4', 'x.txt', 'sub/x.jpg'):
            with self.assertRaises(C.CaptureError):
                native.delete(bad)

    def test_listing_keeps_photos_and_clips_only(self):
        p = C._MediaListing()
        p.feed('<a href="/mnt/extsd/DCIM/100MEDIA/ANMP0001.jpg">a</a><a href="L0010009.mp4">b</a>'
               '<a href="../etc/passwd">c</a><a href="x.txt">d</a><a href="sub/ANMP2.jpg">e</a>')
        self.assertEqual(p.names, {'ANMP0001.jpg', 'L0010009.mp4'})


if __name__ == '__main__':
    unittest.main()
