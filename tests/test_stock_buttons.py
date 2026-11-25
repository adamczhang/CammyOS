import struct
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'host'))
from cammy_buttons import BUTTONS, input_packet, tap_once, tap_script, pulse_script
from tools.stock_button_trial import preserve_button_identity

IDENTITY = {'boot': 'bf452aa4-9a72-45c1-8ff5-9de66f268269',
            'native': {'pid': 778, 'starttime': '165'}}


class Client:
    agent = 'test'
    value = {'owner': 'test', 'lease_remaining_seconds': 30, 'preview_active': False}
    def state(self): return dict(self.value)


class Reader:
    def __init__(self, reply=b'CAMMY_TAP_DONE=64\n'):
        self.calls = []; self.reply = reply
    def shell(self, command):
        self.calls.append(command); return self.reply


class StockButtonTests(unittest.TestCase):
    def test_observed_mapping_and_complete_16_byte_pairs(self):
        self.assertEqual(BUTTONS, {'left': (0,115), 'right': (0,102),
                                  'zoom_in': (0,5), 'zoom_out': (0,6), 'turn_180': (1,3),
                                  'center': (0,7), 'playback_cycle': (1,139)})
        for button, (_, code) in BUTTONS.items():
            data = input_packet(button)
            self.assertEqual(len(data), 64)
            records = [struct.unpack('<IIHHi', data[n:n+16])[2:] for n in range(0,64,16)]
            self.assertEqual(records, [(1,code,1),(0,0,0),(1,code,0),(0,0,0)])

    def test_unknown_button_or_untrusted_identity_refused(self):
        for name in ['power', 'reset', 'left;reboot', '', '../event3']:
            with self.assertRaises(ValueError): input_packet(name)
        for field in [{'boot': 'bad'}, {'native': {'pid': 1, 'starttime': '165'}},
                      {'native': {'pid': 778, 'starttime': '1;reboot'}}]:
            with self.assertRaises(ValueError): tap_script('left', {**IDENTITY, **field})
        with self.assertRaises(ValueError): tap_script('left', IDENTITY, 'bad')

    def test_single_data_batch_and_device_and_boot_guards(self):
        script = tap_script('left', IDENTITY, 'a'*24)
        self.assertIn('sunxi-gpadc0', script)
        self.assertIn('13:64', script)
        self.assertIn(IDENTITY['boot'], script)
        self.assertIn('bs=64 count=1', script)
        self.assertEqual(script.count('/bin/dd '), 1)
        self.assertLess(script.index('sha256sum'), script.index('/bin/dd '))
        self.assertIn("trap '/tmp/cammy-runtime/busybox rm -rf", script)

    def test_lease_refusal_prevents_any_input(self):
        for state in [{'owner': 'other'}, {'lease_remaining_seconds': 14}, {'preview_active': True}]:
            client = Client(); client.value = {**Client.value, **state}; reader = Reader()
            with self.assertRaises(RuntimeError): tap_once(reader, client, 'left', IDENTITY)
            self.assertEqual(reader.calls, [])

    def test_uncertain_or_refused_response_never_retries(self):
        for reply in [b'', b'CAMMY_TAP_UNCERTAIN=write\n', b'CAMMY_TAP_REFUSED=boot\n']:
            reader = Reader(reply)
            with self.assertRaises(RuntimeError): tap_once(reader, Client(), 'left', IDENTITY)
            self.assertEqual(len(reader.calls), 1)

    def test_input_ack_does_not_claim_motion_or_stop_feedback(self):
        reader = Reader(); result = tap_once(reader, Client(), 'right', IDENTITY)
        self.assertEqual(len(reader.calls), 1)
        self.assertFalse(result['physical_effect_confirmed'])
        self.assertFalse(result['pose_or_stop_feedback'])
        self.assertFalse(result['firmware_or_code_installed'])

    def test_check_only_has_no_input_write(self):
        script = tap_script('left', IDENTITY, 'a'*24, check_only=True)
        self.assertNotIn('/bin/dd ', script)
        self.assertNotIn('of=/dev/input', script)
        self.assertIn('CAMMY_TAP_CHECKED=64', script)

    def test_pulse_has_independent_release_and_parent_exit_cleanup(self):
        script = pulse_script('left', IDENTITY)
        self.assertIn("trap '' HUP INT TERM", script)
        self.assertIn('/bin/sleep 0.15', script)
        self.assertIn('trap cleanup_pulse EXIT', script)
        self.assertLess(script.index('releaser=$!'), script.index('if="$d/down" of=/dev/input'))
        self.assertIn('wait "$releaser"', script)
        self.assertIn('pulse_released=1', script)
        for button, duration in [('turn_180',150),('left',1000),('right',0)]:
            with self.assertRaises(ValueError): pulse_script(button, IDENTITY, duration)

    def test_pulse_uncertainty_does_not_repeat_keydown(self):
        reader = Reader(b'CAMMY_TAP_UNCERTAIN=pulse\n')
        with self.assertRaises(RuntimeError): tap_once(reader, Client(), 'left', IDENTITY, 150)
        self.assertEqual(len(reader.calls), 1)

    def test_explicit_five_second_hold_keeps_release_path(self):
        script = pulse_script('left', IDENTITY, 5000)
        self.assertIn('/bin/sleep 5.00', script)
        self.assertIn('trap cleanup_pulse EXIT', script)
        self.assertIn('final_up_rc=$?', script)
        self.assertIn('pulse_released=1', script)
        for duration in (1000,4999,5001,10000):
            with self.assertRaises(ValueError): pulse_script('left', IDENTITY, duration)

    def test_media_controls_are_distinct_and_never_long_held(self):
        self.assertNotEqual(BUTTONS['center'], BUTTONS['turn_180'])
        for name in ('center','playback_cycle'):
            self.assertIn('/bin/sleep 0.15',pulse_script(name,IDENTITY,150))
            with self.assertRaises(ValueError): pulse_script(name,IDENTITY,5000)

    def test_watchdog_proc_link_metadata_is_not_device_identity(self):
        before={k:'same' for k in ('boot','native','hashes','kernel','cmdline','mounts','selection')}
        before['native_maps']='00010000-00020000 r-xp 00000000 1f:03 12 /usr/bin/sdvcam\n'
        before['wd_info']='pos: 0\nflags: 02400001\nmnt_id: 14\nWDNODE=8794 link old-time -> /dev/watchdog'
        after={**before,'wd_info':'pos: 0\nflags: 02400001\nmnt_id: 14\nWDNODE=14687 link new-time -> /dev/watchdog'}
        preserve_button_identity(before,after)
        for field,value in [('wd_info',after['wd_info'].replace('02400001','02400000')),
                            ('wd_info',after['wd_info'].replace('/dev/watchdog','/dev/null')),
                            ('native','different process'),('boot','different boot')]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                preserve_button_identity(before,{**after,field:value})

    def run_pulse_fixture(self, mode):
        """Execute actual release/trap shell with confined fake device I/O."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root/'dd'
            fake.write_text('''#!/usr/bin/env python3
import json,os,struct,sys,time
from pathlib import Path
root=Path(os.environ['PULSE_ROOT'])
args=dict(arg.split('=',1) for arg in sys.argv[1:])
src=Path(args['if']);dst=Path(args['of'])
assert src.is_relative_to(root) and dst.is_relative_to(root)
size=int(args['bs']);skip=int(args.get('skip',0))*size
data=src.read_bytes()[skip:skip+size*int(args['count'])]
if dst==root/'device':
 value=struct.unpack('<IIHHi',data[:16])[-1]
 if value==1 and os.environ['PULSE_MODE']=='late-down':time.sleep(.3)
 with (root/'log').open('a') as out:out.write(json.dumps({'value':value})+'\\n')
 if value==1 and os.environ['PULSE_MODE']=='down-fails':sys.exit(1)
else:dst.write_bytes(data)
''')
            fake.chmod(0o700)
            script = pulse_script('left', IDENTITY)
            script = script[script.index('d=/tmp/cammy-button-'):]
            first = script.splitlines()[0]
            script = script.replace(first, 'd='+str(root/'work'))
            script = script.replace('/tmp/cammy-runtime/busybox mkdir','/bin/mkdir')
            script = script.replace('/tmp/cammy-runtime/busybox rm','/bin/rm')
            script = script.replace('/tmp/cammy-runtime/busybox sha256sum','/usr/bin/sha256sum')
            script = script.replace('/bin/dd',str(fake)).replace('/dev/input/event0',str(root/'device'))
            env={**os.environ,'PULSE_ROOT':tmp,'PULSE_MODE':mode}
            p=subprocess.Popen(['dash'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               text=True,env=env)
            p.stdin.write(script);p.stdin.close();p.stdin=None
            if mode=='terminate':
                deadline=time.monotonic()+2
                while not (root/'log').exists() and time.monotonic()<deadline:time.sleep(.002)
                self.assertTrue((root/'log').exists());p.terminate()
            out,err=p.communicate(timeout=4)
            rows=[json.loads(line)['value'] for line in (root/'log').read_text().splitlines()]
            self.assertEqual(rows.count(1),1,(rows,out,err))
            self.assertEqual(rows[-1],0,(rows,out,err))
            self.assertFalse((root/'work').exists())
            return p.returncode,out

    def test_late_down_still_has_final_release(self):
        rc,out=self.run_pulse_fixture('late-down')
        self.assertEqual(rc,0);self.assertIn('CAMMY_TAP_DONE=64',out)

    def test_down_failure_cleans_up_without_retry(self):
        rc,out=self.run_pulse_fixture('down-fails')
        self.assertNotEqual(rc,0);self.assertNotIn('CAMMY_TAP_DONE=64',out)

    def test_parent_termination_releases_and_cleans_up(self):
        rc,out=self.run_pulse_fixture('terminate')
        self.assertNotEqual(rc,0);self.assertNotIn('CAMMY_TAP_DONE=64',out)
