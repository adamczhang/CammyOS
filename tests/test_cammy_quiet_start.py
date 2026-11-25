import subprocess
import tempfile
import unittest
from pathlib import Path


HOOK = Path(__file__).resolve().parents[1] / 'overlay/etc/init.d/S00cammy-power'


class QuietStartTests(unittest.TestCase):
    def test_boot_reapplies_mute_after_app_saves_cached_volume(self):
        saved = (
            '    ["ml_startup_volume"] = 1,\n'
            '    ["ml_device_beep"] = 2,\n'
            '    ["ml_camera_timedshutdown"] = 1,\n'
        )
        muted = saved.replace('["ml_startup_volume"] = 1', '["ml_startup_volume"] = 0')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / 'setting.lua'
            flag = root / 'quiet-start.enabled'
            script = root / 'S00cammy-power'
            script.write_text(HOOK.read_text().replace('/data/setting.lua', str(settings))
                              .replace('/etc/cammy/', str(root) + '/'))

            settings.write_text(saved)
            subprocess.run(['sh', script, 'start'], check=True)
            self.assertEqual(settings.read_text(), saved)

            flag.touch()
            for phase in ('start', 'boot'):
                # The running app can rewrite the old setting at shutdown.
                settings.write_text(saved)
                subprocess.run(['sh', script, phase], check=True)
                self.assertEqual(settings.read_text(), muted)
