import unittest
from tools.native_clip_trial import Movie, MovieListing, record_three_seconds


class Client:
    agent = 'trial'
    def state(self):
        return {'owner': self.agent, 'lease_remaining_seconds': 60, 'preview_active': False}


class Native:
    def __init__(self):
        self.active = 0
        self.calls = []
    def recording(self):
        return self.active
    def _post(self, command, value):
        self.calls.append((command, value))
        self.active = value


class ClipTrialTests(unittest.TestCase):
    def test_one_start_three_seconds_and_one_confirmed_stop(self):
        native, sleeps = Native(), []
        record_three_seconds(native, Client(), sleeps.append)
        self.assertEqual(native.calls, [(1100, 1), (1100, 0)])
        self.assertEqual(sleeps, [3, .15])
        self.assertEqual(native.active, 0)

    def test_existing_recording_and_foreign_lease_untouched(self):
        native = Native()
        native.active = 1
        with self.assertRaises(RuntimeError):
            record_three_seconds(native, Client(), lambda _: None)
        self.assertEqual(native.calls, [])
        client = Client()
        client.state = lambda: {'owner': 'other', 'lease_remaining_seconds': 60, 'preview_active': False}
        with self.assertRaises(RuntimeError):
            record_three_seconds(Native(), client, lambda _: None)

    def test_exception_after_start_attempts_stop_once(self):
        native = Native()
        def interrupted(seconds):
            if seconds == 3:
                raise RuntimeError('test cancellation')
        with self.assertRaises(RuntimeError):
            record_three_seconds(native, Client(), interrupted)
        self.assertEqual(native.calls, [(1100, 1), (1100, 0)])

    def test_movie_listing_confinement(self):
        parser = MovieListing()
        parser.feed('<a href="ANMV0000.MP4">ok</a><a href="../bad.mov">bad</a>'
                    '<a href="http://other/bad.mp4">bad</a><a href="x.mp4?auth=x">bad</a>')
        self.assertEqual(parser.names, {'ANMV0000.MP4'})

    def test_media_profile_route_and_strict_preset_readback(self):
        native = Movie('http://127.0.0.1:8787')
        native.recording = lambda: 0
        calls = []
        native.settings = lambda: {'ml_record_resolution': 5, 'ml_video_quality': 2}
        native._read = lambda *args: calls.append(args) or b'{}'
        native.setting('ml_record_resolution', 5)
        self.assertEqual(calls, [('/api/setdeviceinfo/?custom=1&mode=0&item=ml_record_resolution&par=5', 'POST')])
        for key, value in [('arbitrary', 1), ('ml_record_resolution', 6), ('ml_video_quality', 3)]:
            with self.assertRaises(ValueError):
                native.setting(key, value)
        native.recording = lambda: 1
        with self.assertRaises(RuntimeError):
            native.setting('ml_video_quality', 2)
