"""Finite process guardian using synthetic stock tools. No ALSA/device access."""
import pathlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
ROOT=Path(__file__).resolve().parents[1]


class SpeakerProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.play=self.root/'aplay';self.mixer=self.root/'amixer';self.marker=self.root/'restored'
        self.cue=self.root/'cue';self.cue.write_bytes(b'\0'*8000)
        self.play.write_text('#!/usr/bin/python3\nimport os,sys\nassert sys.argv[1:]==["-q","-D","PlaybackRateDmix","-t","raw","-f","S16_LE","-r","16000","-c","1","/proc/self/fd/3"]\nassert len(open(sys.argv[-1],"rb").read())==8000\n')
        self.mixer.write_text('#!/usr/bin/python3\nimport pathlib,sys\nassert sys.argv[1:]==["-q","-c","0","cset","numid=21","off"]\npathlib.Path('+repr(str(self.marker))+').write_text("restored")\n')
        self.play.chmod(0o700);self.mixer.chmod(0o700);self.binary=self.root/'guardian'
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-DSYNTHETIC=1',
            '-DHW_PARAMS="'+str(self.root/'hw_params')+'"','-DPLAY="'+str(self.play)+'"','-DMIXER="'+str(self.mixer)+'"',
            str(ROOT/'native/speaker_probe/guardian.c'),'-o',str(self.binary)],check=True,capture_output=True)
    def tearDown(self):self.temp.cleanup()
    def run_probe(self):return subprocess.run([str(self.binary),str(self.cue)],capture_output=True,text=True,timeout=5)
    def test_stock_route_arguments_and_always_restore(self):
        result=self.run_probe();self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(result.stdout);self.assertTrue(report['synthetic']);self.assertFalse(report['hardware_audible'])
        self.assertTrue(report['playback_exit_ok'] and report['restore_exit_ok']);self.assertTrue(self.marker.exists())
    def test_private_configuration_is_an_open_fd_only_in_player(self):
        config=self.root/'private.conf';config.write_text('pcm.fixture { type null }\n');config.chmod(0o600)
        self.play.write_text('#!/usr/bin/python3\nimport os,sys\nassert sys.argv[3]=="CammyPlayback"\nassert os.environ["ALSA_CONFIG_PATH"]=="/proc/self/fd/4"\nassert open("/proc/self/fd/4").read()=="pcm.fixture { type null }\\n"\nassert len(open(sys.argv[-1],"rb").read())==8000\n')
        result=subprocess.run([str(self.binary),str(self.cue),str(config)],capture_output=True,text=True,timeout=4)
        self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(self.marker.exists())
    def test_private_config_bad_mode_links_or_size_refused_before_mixer(self):
        config=self.root/'private.conf';config.write_text('safe');config.chmod(0o644)
        def run():return subprocess.run([str(self.binary),str(self.cue),str(config)],capture_output=True,timeout=3).returncode
        self.assertEqual(run(),2);config.chmod(0o600)
        hard=self.root/'hard-conf';os.link(config,hard);self.assertEqual(run(),2);hard.unlink()
        config.write_bytes(b'x'*8193);self.assertEqual(run(),2)
        self.assertFalse(self.marker.exists())

    def test_play_refusal_still_restores(self):
        self.play.write_text('#!/bin/sh\nexit 1\n')
        result=self.run_probe();self.assertEqual(result.returncode,4);self.assertTrue(self.marker.exists())
    def gain_fixture(self):
        config=self.root/'private.conf';config.write_text('pcm.fixture { type null }\n');config.chmod(0o600)
        # Like real aplay, drain the input to EOF (stream mode feeds it through a pipe).
        self.play.write_text('#!/usr/bin/python3\nimport pathlib\nopen("/proc/self/fd/3","rb").read()\npathlib.Path('+repr(str(self.root/'played'))+').touch()\n')
        self.mixer.write_text('#!/usr/bin/python3\nimport pathlib,sys,os\np=pathlib.Path('+repr(str(self.marker))+')\nassert "ALSA_CONFIG_PATH" not in os.environ\nwith p.open("a") as f:f.write(" ".join(sys.argv[1:])+"\\n")\nif os.environ.get("FAIL_MIX")==sys.argv[-2]+"/"+sys.argv[-1]:sys.exit(1)\n')
        return [str(self.binary),str(self.cue),str(config),'lineout20-from0']
    def test_fixed_gain_and_independent_restore_order(self):
        result=subprocess.run(self.gain_fixture(),capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.marker.read_text().splitlines(),['-q -c 0 cset numid=16 27','-q -c 0 cset numid=21 off','-q -c 0 cset numid=16 0'])
        self.assertTrue(json.loads(result.stdout)['gain_restore_exit_ok'])
    def test_gain_setup_or_amplifier_restore_failure_still_restores_mute(self):
        command=self.gain_fixture()
        for fault in ['numid=16/27','numid=21/off']:
            with self.subTest(fault=fault):
                self.marker.unlink(missing_ok=True);(self.root/'played').unlink(missing_ok=True)
                result=subprocess.run(command,env={**os.environ,'FAIL_MIX':fault},capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,4,result.stderr)
                self.assertTrue(self.marker.read_text().endswith('numid=16 0\n'))
                self.assertEqual((self.root/'played').exists(),fault!='numid=16/27')
    def test_signal_with_gain_enabled_restores_both_controls(self):
        command=self.gain_fixture()
        self.play.write_text('#!/usr/bin/python3\nimport pathlib,time\npathlib.Path('+repr(str(self.root/'played'))+').touch()\ntime.sleep(30)\n')
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            end=time.monotonic()+2
            while not (self.root/'played').exists() and time.monotonic()<end:time.sleep(.01)
            self.assertTrue((self.root/'played').exists());process.send_signal(signal.SIGTERM)
            stdout,stderr=process.communicate(timeout=3)
            self.assertEqual(process.returncode,0,stderr);self.assertTrue(json.loads(stdout)['gain_restore_exit_ok'])
            self.assertTrue(self.marker.read_text().endswith('numid=21 off\n-q -c 0 cset numid=16 0\n'))
        finally:
            if process.poll() is None:process.kill();process.wait()
    def test_unknown_gain_profile_refused_before_controls(self):
        command=self.gain_fixture();command[-1]='lineout31-from0'
        result=subprocess.run(command,capture_output=True,timeout=3)
        self.assertEqual(result.returncode,2);self.assertFalse(self.marker.exists())
    def test_gain_profile_preserves_observed_amplifier_on_baseline(self):
        command=self.gain_fixture();command[-1]='lineout20-from0-spkon'
        result=subprocess.run(command,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(self.marker.read_text().endswith('numid=21 on\n-q -c 0 cset numid=16 0\n'))
    def auto_fixture(self):
        command=self.gain_fixture();command[-1]='auto'
        (self.root/'hw_params').write_text('access: MMAP_INTERLEAVED\nformat: S16_LE\nchannels: 1\nrate: 16000 (16000/1)\nperiod_size: 960\nbuffer_size: 7680\n')
        self.mixer.write_text('#!/usr/bin/python3\nimport pathlib,sys,json\np=pathlib.Path('+repr(str(self.marker))+')\nif sys.argv[3]=="cget":\n id=sys.argv[4].split("=")[1]\n controls={"16":("LINEOUT volume","0"),"20":("LINEOUT Switch","on"),"22":("LINEOUT Output Select","1"),"1":("tx hub mode","1"),"21":("SPK Switch","on")}\n name,value=controls[id]\n override=pathlib.Path('+repr(str(self.root/'baseline.json'))+')\n if override.exists():value=json.loads(override.read_text()).get(id,value)\n print("numid="+id+",iface=MIXER,name="+repr(name)+"\\n  : values="+value)\nelse:\n with p.open("a") as f:f.write(" ".join(sys.argv[1:])+"\\n")\n')
        return command
    def stream_fixture(self):
        command=self.auto_fixture();self.streamed=self.root/'streamed'
        self.play.write_text('#!/usr/bin/python3\nimport shutil\nwith open("/proc/self/fd/3","rb") as i,open('+repr(str(self.streamed))+',"wb") as o:shutil.copyfileobj(i,o)\n')
        return command
    def test_stream_mode_plays_appended_chunks_in_order_then_restores(self):
        command=self.stream_fixture();cue=pathlib.Path(command[1])
        cue.write_bytes(b'\x01\x00'*800);pathlib.Path(str(cue)+'.1').write_bytes(b'\x02\x00'*1600)
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        time.sleep(0.4);tmp=pathlib.Path(str(cue)+'.2.tmp');tmp.write_bytes(b'\x03\x00'*800);tmp.rename(str(cue)+'.2')
        out,err=process.communicate(timeout=10)
        self.assertEqual(process.returncode,0,err)
        self.assertEqual(self.streamed.read_bytes(),b'\x01\x00'*800+b'\x02\x00'*1600+b'\x03\x00'*800)
        self.assertFalse(pathlib.Path(str(cue)+'.1').exists());self.assertFalse(pathlib.Path(str(cue)+'.2').exists())
        self.assertIn('"feeder_exit":0',out)
        self.assertTrue(self.marker.read_text().endswith('numid=21 on\n-q -c 0 cset numid=16 0\n'))
    def test_auto_accepts_a_stock_lineout_level_and_restores_it(self):
        command=self.stream_fixture();cue=pathlib.Path(command[1]);cue.write_bytes(b'\x01\x00'*800)
        for level in ('10','27'):
            with self.subTest(level=level):
                self.marker.unlink(missing_ok=True)
                for left in cue.parent.glob(cue.name+'.*'):left.unlink()
                cue.write_bytes(b'\x01\x00'*800)
                (self.root/'baseline.json').write_text('{"16":"%s"}'%level)
                process=subprocess.run(command,capture_output=True,text=True,timeout=10)
                self.assertEqual(process.returncode,0,process.stderr)
                self.assertEqual(self.streamed.read_bytes(),b'\x01\x00'*800)
                self.assertTrue(self.marker.read_text().endswith('numid=21 on\n-q -c 0 cset numid=16 %s\n'%level))
    def test_auto_refuses_an_unreadable_lineout_level_before_controls(self):
        for bad in ('32','-1','x','100'):
            with self.subTest(bad=bad):
                self.marker.unlink(missing_ok=True)
                command=self.stream_fixture();pathlib.Path(command[1]).write_bytes(b'\x01\x00'*800)
                (self.root/'baseline.json').write_text('{"16":"%s"}'%bad)
                self.assertEqual(subprocess.run(command,capture_output=True,timeout=10).returncode,12)
                self.assertFalse(self.marker.exists())
    def test_stream_stop_is_clean_when_mixer_restored(self):
        command=self.stream_fixture();cue=pathlib.Path(command[1]);cue.write_bytes(b'\x01\x00'*800)
        self.play.write_text('#!/usr/bin/python3\nimport time\nopen("/proc/self/fd/3","rb").read(1600)\ntime.sleep(30)\n')
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        time.sleep(0.5);process.send_signal(signal.SIGTERM);out,err=process.communicate(timeout=10)
        self.assertEqual(process.returncode,0,err);self.assertIn('"signal_observed":true',out)
        self.assertTrue(self.marker.read_text().endswith('numid=21 on\n-q -c 0 cset numid=16 0\n'))
    def test_stream_mode_refuses_loud_chunk_and_still_restores(self):
        command=self.stream_fixture();cue=pathlib.Path(command[1])
        cue.write_bytes(b'\x01\x00'*800);pathlib.Path(str(cue)+'.1').write_bytes(b'\xff\x7f'*800)
        result=subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,4);self.assertIn('"feeder_exit":3',result.stdout)
        self.assertTrue(self.marker.read_text().endswith('numid=21 on\n-q -c 0 cset numid=16 0\n'))
    # Close-vs-enqueue handshake (native/speaker_probe/speaker_stream.h): the portal admits
    # chunk N with link(tmp, <job>.N); the feeder closes with an O_EXCL tombstone at <job>.N.
    def handshake_fixture(self,idle=20000):
        command=self.stream_fixture();binary=self.root/('guardian-idle%d'%idle)
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-Wshadow','-Wconversion','-DSYNTHETIC=1','-DSTREAM_IDLE_MS=%d'%idle,
            '-DHW_PARAMS="'+str(self.root/'hw_params')+'"','-DPLAY="'+str(self.play)+'"','-DMIXER="'+str(self.mixer)+'"',
            str(ROOT/'native/speaker_probe/guardian.c'),'-o',str(binary)],check=True,capture_output=True)
        command[0]=str(binary);self.gate=self.root/'gate';self.gate.mkdir()
        # Restore of numid=21 waits while 'hold' exists: the window after the feeder closed.
        self.mixer.write_text(self.mixer.read_text().replace('else:\n','else:\n import time\n while sys.argv[-2]=="numid=21" and pathlib.Path('+repr(str(self.root/'hold'))+').exists():time.sleep(.01)\n'))
        self.cue_path=pathlib.Path(command[1]);self.cue_path.write_bytes(b'\x01\x00'*800)
        return command
    def start_guardian(self,command):
        return subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env={**os.environ,'CAMMY_SYNTH_CLOSE_GATE':str(self.gate)})
    def wait_for(self,path,seconds=5):
        end=time.monotonic()+seconds
        while not path.exists() and time.monotonic()<end:time.sleep(.005)
        self.assertTrue(path.exists(),path)
    def portal_append(self,n,data):
        """The portal's admission step; True = admitted, False = stream closed."""
        tmp=pathlib.Path('%s.%d.tmp'%(self.cue_path,n));tmp.write_bytes(data)
        try:os.link(tmp,'%s.%d'%(self.cue_path,n));return True
        except FileExistsError:return False
        finally:tmp.unlink()
    def test_append_admitted_just_before_close_is_played(self):
        process=self.start_guardian(self.handshake_fixture(idle=100))
        self.wait_for(self.gate/'closing.1')          # idle expired; the feeder is about to claim slot 1
        self.assertTrue(self.portal_append(1,b'\x02\x00'*1600))
        (self.gate/'go.1').touch();self.wait_for(self.gate/'closing.2');(self.gate/'go.2').touch()
        out,err=process.communicate(timeout=10)
        self.assertEqual(process.returncode,0,err);self.assertIn('"feeder_exit":0',out)
        self.assertEqual(self.streamed.read_bytes(),b'\x01\x00'*800+b'\x02\x00'*1600)
        self.assertEqual(pathlib.Path(str(self.cue_path)+'.2').stat().st_size,0)  # tombstone at the next slot
        self.assertFalse(pathlib.Path(str(self.cue_path)+'.1').exists())
    def test_append_after_close_is_refused_while_restore_runs(self):
        command=self.handshake_fixture(idle=100);(self.root/'hold').touch();(self.gate/'go.1').touch()
        process=self.start_guardian(command)
        try:
            tomb=pathlib.Path(str(self.cue_path)+'.1');self.wait_for(tomb);time.sleep(.05)
            self.assertIsNone(process.poll())             # still restoring: the old loss window
            self.assertFalse(self.portal_append(1,b'\x02\x00'*1600))
            self.assertEqual(tomb.stat().st_size,0);self.assertFalse(pathlib.Path(str(tomb)+'.tmp').exists())
        finally:(self.root/'hold').unlink()
        out,err=process.communicate(timeout=10)
        self.assertEqual(process.returncode,0,err);self.assertEqual(self.streamed.read_bytes(),b'\x01\x00'*800)
        self.assertTrue(self.marker.read_text().endswith('numid=21 on\n-q -c 0 cset numid=16 0\n'))
    def test_exact_total_limit_plays_then_closes_without_idle(self):
        command=self.handshake_fixture(idle=20000);self.cue_path.write_bytes(b'\x01\x00'*80000)
        for n in range(1,12):self.assertTrue(self.portal_append(n,b'\x02\x00'*80000))
        (self.gate/'go.12').touch();started=time.monotonic()
        out,err=self.start_guardian(command).communicate(timeout=15)
        self.assertLess(time.monotonic()-started,10)    # closed at the cap, not after the 20 s idle
        self.assertIn('"feeder_exit":0',out);self.assertIn('"playback_exit_ok":true',out)
        self.assertEqual(len(self.streamed.read_bytes()),60*32000)
        self.assertEqual(pathlib.Path(str(self.cue_path)+'.12').stat().st_size,0)
        self.assertFalse(self.portal_append(12,b'\x02\x00'*800))
    def test_chunk_past_total_limit_is_refused_and_restores(self):
        command=self.handshake_fixture(idle=20000);self.cue_path.write_bytes(b'\x01\x00'*80000)
        for n in range(1,13):self.assertTrue(self.portal_append(n,b'\x02\x00'*(80000 if n<12 else 800)))
        (self.gate/'go.12').touch()
        result=subprocess.run(command,capture_output=True,text=True,timeout=15,env={**os.environ,'CAMMY_SYNTH_CLOSE_GATE':str(self.gate)})
        self.assertEqual(result.returncode,4);self.assertIn('"feeder_exit":3',result.stdout)
        self.assertEqual(len(self.streamed.read_bytes()),60*32000)
        self.assertTrue(self.marker.read_text().endswith('numid=21 on\n-q -c 0 cset numid=16 0\n'))
    def test_cancel_while_waiting_closes_the_stream(self):
        command=self.handshake_fixture(idle=20000)
        self.play.write_text('#!/usr/bin/python3\nimport time\nopen("/proc/self/fd/3","rb").read(1600)\ntime.sleep(30)\n')
        process=self.start_guardian(command);time.sleep(.5);process.send_signal(signal.SIGTERM)
        out,err=process.communicate(timeout=10)
        self.assertEqual(process.returncode,0,err);self.assertIn('"signal_observed":true',out)
        self.assertEqual(pathlib.Path(str(self.cue_path)+'.1').stat().st_size,0)
        self.assertFalse(self.portal_append(1,b'\x02\x00'*800))
        self.assertFalse((self.gate/'closing.1').exists())  # Stop never waits on the test gate
    def test_feeder_waits_for_portal_tmp_unlink(self):
        command=self.handshake_fixture(idle=2000);process=self.start_guardian(command)
        tmp=pathlib.Path(str(self.cue_path)+'.1.tmp');tmp.write_bytes(b'\x02\x00'*800)
        os.link(tmp,str(self.cue_path)+'.1');time.sleep(.2);tmp.unlink()   # link count 2 for 200 ms
        self.wait_for(self.gate/'closing.2');(self.gate/'go.2').touch()
        out,err=process.communicate(timeout=10)
        self.assertEqual(process.returncode,0,err);self.assertEqual(self.streamed.read_bytes(),b'\x01\x00'*800+b'\x02\x00'*800)
    def test_auto_preflight_checks_live_controls_then_restores_observed_baseline(self):
        result=subprocess.run(self.auto_fixture(),capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(self.marker.read_text().endswith('numid=21 on\n-q -c 0 cset numid=16 0\n'))
    def test_auto_preflight_busy_gain_route_format_or_pcm_peak_never_mutates(self):
        command=self.auto_fixture()
        for fault, code in [('gain',12),('route',12),('format',10),('hub',13),('peak',3)]:   # gain: an invalid level (any 0..31 is a baseline)
            with self.subTest(fault=fault):
                (self.root/'baseline.json').unlink(missing_ok=True)
                (self.root/'hw_params').write_text('access: MMAP_INTERLEAVED\nformat: S16_LE\nchannels: 1\nrate: 16000 (16000/1)\nperiod_size: 960\nbuffer_size: 7680\n')
                self.cue.write_bytes(bytes(8000))
                if fault=='gain':(self.root/'baseline.json').write_text('{"16":"40"}')
                if fault=='route':(self.root/'baseline.json').write_text('{"20":"off"}')
                if fault=='hub':(self.root/'baseline.json').write_text('{"1":"0"}')
                if fault=='format':(self.root/'hw_params').write_text('closed\n')
                if fault=='peak':self.cue.write_bytes(b'\xff\x7f'*4000)
                result=subprocess.run(command,capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,code,result.stderr);self.assertFalse(self.marker.exists())

    def test_hung_player_is_killed_and_reaped_then_restored(self):
        self.play.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(30)\n')
        started=time.monotonic();result=self.run_probe()
        self.assertEqual(result.returncode,4);self.assertLess(time.monotonic()-started,3.5);self.assertTrue(self.marker.exists())
    def test_signal_cancels_player_then_restores(self):
        self.play.write_text('#!/usr/bin/python3\nimport pathlib,time\npathlib.Path('+repr(str(self.root/'playing'))+').touch()\ntime.sleep(30)\n')
        process=subprocess.Popen([str(self.binary),str(self.cue)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            end=time.monotonic()+2
            while not (self.root/'playing').exists() and time.monotonic()<end:time.sleep(.01)
            self.assertTrue((self.root/'playing').exists());process.send_signal(signal.SIGTERM)
            stdout,stderr=process.communicate(timeout=3)
            self.assertEqual(process.returncode,0);self.assertTrue(json.loads(stdout)['signal_observed']);self.assertTrue(self.marker.exists())
        finally:
            if process.poll() is None:process.kill();process.wait()
    def test_bad_input_refused_without_playback_or_mixer(self):
        for size in [3,1598,160001,160002]:
            self.cue.write_bytes(bytes(size));self.assertEqual(self.run_probe().returncode,2);self.assertFalse(self.marker.exists())
    def test_longer_finite_payload_is_supported(self):
        self.cue.write_bytes(bytes(64000))
        self.play.write_text('#!/usr/bin/python3\nimport sys\nassert len(open(sys.argv[-1],"rb").read())==64000\n')
        result=self.run_probe();self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(self.marker.exists())
    def test_symlink_or_hardlink_input_refused(self):
        link=self.root/'link';link.symlink_to(self.cue)
        r=subprocess.run([str(self.binary),str(link)],capture_output=True,timeout=3);self.assertEqual(r.returncode,2)
        link.unlink();os.link(self.cue,link);self.assertEqual(self.run_probe().returncode,2)
