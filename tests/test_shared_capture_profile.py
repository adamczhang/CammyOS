"""ALSA grammar/routing with null devices only; no soundcard is opened."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tools.shared_capture_profile import fragment, routing, IPC_KEY


class ProfileTests(unittest.TestCase):
    def test_profile_has_fixed_format_and_private_ipc(self):
        text=fragment()
        self.assertIn('rate 16000',text);self.assertIn('channels 1',text)
        self.assertIn('ipc_perm 0600',text);self.assertIn(str(IPC_KEY),text)

    def test_only_known_capture_card_and_subdevices_resolve(self):
        # Replace the profile's physical endpoints with null PCMs for this test.
        text='pcm.cammy_shared { type null }\n'+routing().replace(
            'playback.pcm { type hw card $CARD device $DEV subdevice $SUBDEV }',
            'playback.pcm { type null }')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'alsa.conf';path.write_text(text)
            code='''import ctypes,json
a=ctypes.CDLL('libasound.so.2')
a.snd_pcm_open.argtypes=[ctypes.POINTER(ctypes.c_void_p),ctypes.c_char_p,ctypes.c_int,ctypes.c_int]
a.snd_pcm_close.argtypes=[ctypes.c_void_p]
result={}
for name in ['hw:0','hw:0,0','hw:0,0,0','hw:1','hw:0,1','hw:0,0,1']:
 h=ctypes.c_void_p();rc=a.snd_pcm_open(ctypes.byref(h),name.encode(),1,0)
 result[name]=rc
 if rc==0:a.snd_pcm_close(h)
print(json.dumps(result))
'''
            env={**os.environ,'ALSA_CONFIG_PATH':str(path)}
            p=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(p.returncode,0,p.stderr);result=json.loads(p.stdout)
            for name in ('hw:0','hw:0,0','hw:0,0,0'):self.assertEqual(result[name],0,name)
            for name in ('hw:1','hw:0,1','hw:0,0,1'):self.assertLess(result[name],0,name)

    def test_shell_parses_without_execution(self):
        path=Path(__file__).resolve().parents[1]/'scripts/shared-capture.sh'
        subprocess.run(['dash','-n',str(path)],check=True,capture_output=True)


if __name__=='__main__':unittest.main()
