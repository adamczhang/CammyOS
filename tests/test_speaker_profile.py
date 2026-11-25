"""Real host/saved ARM ALSA config parsers; no audio/control device opens."""
import json,subprocess,tempfile,unittest
from pathlib import Path
from tools.speaker_profile import profile
ROOT=Path(__file__).resolve().parents[1]
class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):subprocess.run(['make','-s','-C',str(ROOT/'native/speaker_probe'),'config-tools'],check=True,capture_output=True)
    def inspect(self,text,good=True):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'alsa.conf';p.write_text(text)
            commands=[[str(ROOT/'build/speaker-probe/config-host')],
                      [str(ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'),'-L',str(ROOT.parent/'Cammy Bot/M105 investigation/work/firmware-rootfs'),str(ROOT/'build/speaker-probe/config-arm')]]
            for command in commands:
                with self.subTest(binary=command[-1]):
                    r=subprocess.run(command+[str(p)],capture_output=True,text=True,timeout=5)
                    if good:
                        self.assertEqual(r.returncode,0,r.stderr);self.assertFalse(json.loads(r.stdout)['device_opened'])
                    else:self.assertNotEqual(r.returncode,0)
    def test_inline_hardware_slave_and_exact_native_mixing_parameters(self):self.inspect(profile())
    def test_global_symbol_override_does_not_change_private_inline_slave(self):
        self.inspect('pcm.!hw { type asym playback.pcm { type null } }\n'+profile())
    def test_wrong_shared_identity_format_or_amplifier_control_is_refused(self):
        for old,new in [('ipc_key 1111','ipc_key 1120'),('format S16_LE','format S32_LE'),('rate 16000','rate 48000'),('channels 1','channels 2'),('period_size 960','period_size 1024'),('type hw card 0 device 0 subdevice -1','type asym'),('"SPK Switch"','"MIC2 Switch"')]:
            with self.subTest(old=old):self.inspect(profile().replace(old,new),False)
