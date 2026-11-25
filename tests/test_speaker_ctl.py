"""Speaker guardian mixer path (0.9): ALSA control ioctls replace amixer forks.

Builds the guardian twice: once with the old amixer exec path (-DMIXER, a fake
amixer) and once with the production ioctl path over tests/fixtures/ctl_shim.h.
Both run against the same control state; the (control, value) sequences, exit
codes and reports must match, and match the sequences amixer was given in 0.8.
No ALSA device, no sound.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARDIAN = ROOT / 'native/speaker_probe/guardian.c'
FLAGS = ['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wformat=2', '-Wshadow', '-Wconversion',
         '-DSYNTHETIC=1', '-DSTREAM_IDLE_MS=40']
# Stock M105 controls as `amixer -c 0 cget` reported them on 2026-11-23 (read-only).
STOCK = {1: ('E', 0, 1, 1, 'tx hub mode'), 16: ('I', 0, 31, 10, 'LINEOUT volume'),
         20: ('B', 0, 1, 1, 'LINEOUT Switch'), 21: ('B', 0, 1, 0, 'SPK Switch'),
         22: ('E', 0, 1, 1, 'LINEOUT Output Select')}
FAKE_AMIXER = r'''#!/usr/bin/python3
import os,sys
state=os.environ["CAMMY_CTL_STATE"];rows=[l.split(None,5) for l in open(state).read().splitlines()]
def log(line):open(os.environ["CAMMY_CTL_LOG"],"a").write(line+"\n")
def text(r):return ("on" if r[4]!="0" else "off") if r[1]=="B" else r[4]
args=[a for a in sys.argv[1:] if a!="-q"];assert args[:2]==["-c","0"],args
numid=args[3].split("=")[1];row=[r for r in rows if r[0]==numid][0]
log("read numid="+numid)
if args[2]=="cget":print("numid="+numid+",iface=MIXER,name='"+row[5]+"'\n  : values="+text(row))
else:
 value=args[4];log("set numid=%s %s"%(numid,value))
 row[4]={"on":"1","off":"0"}.get(value,value) if row[1]=="B" else value
 open(state,"w").write("".join(" ".join(r)+"\n" for r in rows))
'''
HW_PARAMS = 'access: MMAP_INTERLEAVED\nformat: S16_LE\nchannels: 1\nrate: 16000 (16000/1)\nperiod_size: 960\nbuffer_size: 7680\n'
READS = ['read numid=20', 'read numid=22', 'read numid=1', 'read numid=16', 'read numid=21']


class SpeakerControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        (cls.root / 'aplay').write_text('#!/usr/bin/python3\nimport sys\nopen(sys.argv[-1],"rb").read()\n')
        (cls.root / 'amixer').write_text(FAKE_AMIXER)
        for tool in ('aplay', 'amixer'): (cls.root / tool).chmod(0o700)
        (cls.root / 'hw_params').write_text(HW_PARAMS)
        common = ['-DHW_PARAMS="%s"' % (cls.root / 'hw_params'), '-DPLAY="%s"' % (cls.root / 'aplay')]
        cls.binaries = {'amixer': cls.root / 'guardian-amixer', 'ioctl': cls.root / 'guardian-ioctl'}
        subprocess.run(FLAGS + common + ['-DMIXER="%s"' % (cls.root / 'amixer'), str(GUARDIAN), '-o', str(cls.binaries['amixer'])], check=True)
        subprocess.run(FLAGS + common + ['-DCAMMY_CTL_SHIM="%s"' % (ROOT / 'tests/fixtures/ctl_shim.h'), str(GUARDIAN), '-o', str(cls.binaries['ioctl'])], check=True)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def run_guardian(self, backend, mode, controls):
        work = Path(tempfile.mkdtemp(dir=self.root))
        state = work / 'state'; log = work / 'log'; cue = work / 'cue'
        state.write_text(''.join('%d %s %d %d %d %s\n' % ((n,) + controls[n]) for n in sorted(controls)))
        cue.write_bytes(b'\x01\x00' * 800)
        config = work / 'conf'; config.write_text('pcm.fixture { type null }\n'); config.chmod(0o600)
        argv = [str(self.binaries[backend]), str(cue)] + ([] if mode is None else [str(config), mode])
        env = dict(os.environ, CAMMY_CTL_STATE=str(state), CAMMY_CTL_LOG=str(log))
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10, env=env)
        sequence = log.read_text().splitlines() if log.exists() else []
        return result.returncode, result.stdout, sequence, state.read_text()

    def both(self, mode, controls):
        old = self.run_guardian('amixer', mode, controls)
        new = self.run_guardian('ioctl', mode, controls)
        self.assertEqual(new, old)
        return new

    def with_values(self, **values):
        controls = dict(STOCK)
        for numid, value in values.items():
            row = controls[int(numid[1:])]; controls[int(numid[1:])] = row[:3] + (value,) + row[4:]
        return controls

    def test_auto_restores_found_lineout_and_spk(self):
        for spk, lineout in ((0, 10), (1, 27), (0, 0), (1, 31)):
            with self.subTest(spk=spk, lineout=lineout):
                code, out, seq, state = self.both('auto', self.with_values(n21=spk, n16=lineout))
                self.assertEqual(code, 0, out)
                self.assertEqual(seq, READS + ['read numid=16', 'set numid=16 27',
                                               'read numid=21', 'set numid=21 %s' % ('on' if spk else 'off'),
                                               'read numid=16', 'set numid=16 %d' % lineout])
                self.assertIn('16 I 0 31 %d LINEOUT volume' % lineout, state)

    def test_auto_route_refusals_stop_before_any_write(self):
        for values, reads in ((dict(n20=0), 1), (dict(n22=0), 2), (dict(n1=0), 3)):
            with self.subTest(values=values):
                code, out, seq, _ = self.both('auto', self.with_values(**values))
                self.assertNotEqual(code, 0)
                self.assertEqual(seq, READS[:reads])
                self.assertEqual(out, '')

    def test_auto_refuses_renamed_controls_like_amixer(self):
        for numid in (1, 16, 20, 21, 22):
            with self.subTest(numid=numid):
                controls = dict(STOCK); row = controls[numid]; controls[numid] = row[:4] + ('Other Control',)
                old = self.run_guardian('amixer', 'auto', controls)
                new = self.run_guardian('ioctl', 'auto', controls)
                # amixer read the misnamed element before rejecting its name; the ioctl path
                # rejects it from ELEM_INFO without reading. Everything else is identical.
                self.assertEqual((new[0], new[1], new[3]), (old[0], old[1], old[3]))
                self.assertEqual(new[2], [line for line in old[2] if line != 'read numid=%d' % numid])
                self.assertNotEqual(new[0], 0)
                self.assertFalse([line for line in new[2] if line.startswith('set')])

    def test_fixed_gain_trials_and_plain_restore(self):
        expected = {'lineout20-from0': ['set numid=16 27', 'set numid=21 off', 'set numid=16 0'],
                    'lineout20-from0-spkon': ['set numid=16 27', 'set numid=21 on', 'set numid=16 0'],
                    None: ['set numid=21 off']}
        for mode, sets in expected.items():
            with self.subTest(mode=mode):
                code, out, seq, _ = self.both(mode, dict(STOCK))
                self.assertEqual(code, 0, out)
                self.assertEqual([line for line in seq if line.startswith('set')], sets)

    def test_ioctl_writes_only_named_stock_controls(self):
        """Stricter than amixer cset: a write checks the stock name, so a moved numid is never set."""
        controls = dict(STOCK); controls[21] = STOCK[21][:4] + ('Headphone Switch',)
        code, out, seq, state = self.run_guardian('ioctl', 'lineout20-from0', controls)
        self.assertEqual(code, 4)
        self.assertIn('"restore_exit_ok":false', out)
        self.assertNotIn('set numid=21 off', seq)
        self.assertIn('21 B 0 1 0 Headphone Switch', state)


if __name__ == '__main__':
    unittest.main()
