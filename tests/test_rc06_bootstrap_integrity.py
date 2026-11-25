"""Offline fake-root checks: the bootstrap runs only RAM bytes it verified.

Fixture-only: no package build, camera, card or init. The generated S99 and the
wrapped runtime head run under the host shell with their absolute paths moved
into a temporary root; host BusyBox stands in for the copied payload BusyBox.
"""
import os,shutil,signal,stat,subprocess,tempfile,time,unittest
from pathlib import Path
from tools import rc06_build as B, rc_runtime_integrity as I
HOST_BUSYBOX=shutil.which('busybox')
SLOT='CammyOS-releases/0123456789abcdef01234567'
CARD='/mnt/extsd/'+SLOT+'/runtime'
DAEMON='#!/bin/sh\nlog=$1\nshift\necho "exec $*" >>"@ROOT@/ran"\nexec "$@" >>"$log" 2>&1\n'
STARTUP='#!/bin/sh\necho "startup $0" >>"@ROOT@/ran"\n'

@unittest.skipUnless(HOST_BUSYBOX,'host busybox needed for the copied sha256sum applet')
class BootstrapIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);r=str(self.root)
        self.payload={'busybox':Path(HOST_BUSYBOX).read_bytes(),'daemon-exec':DAEMON.replace('@ROOT@',r).encode(),
            'startup.sh':STARTUP.replace('@ROOT@',r).encode(),'dhcp.sh':b'#!/bin/sh\n','boot-helper.sh':b'cammy_get() { :; }\n',
            'rc-health.sh':b'#!/bin/sh\nexit 0\n','RELEASE':b'0.7.9\n','enable-shared-capture':b'experimental-shared-capture-v1\n'}
        self.payload['SHA256SUMS']=I.manifest(self.payload)
        self.s99=I.bootstrap_s99((B.ROOT/'scripts/S99cammy-tf').read_text(),CARD,self.payload)
        self.card=self.root/('mnt/extsd/'+SLOT+'/runtime');self.card.mkdir(parents=True)
        for n,d in self.payload.items():(self.card/n).write_bytes(d)
        (self.root/'etc/cammy').mkdir(parents=True);(self.root/'tmp').mkdir()
        for n in ('home.conf','portal.token'):(self.root/'etc/cammy'/n).write_text('x\n')
        self.ram=self.root/'tmp/cammy-runtime';self.guard=self.root/'etc/cammy/startup.pending'
        self.script=self.root/'S99';self.script.write_text(self.fake(self.s99))
        self.bin=self.root/'bin';self.bin.mkdir()
        self.procs=[]
    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:p.kill()
            p.wait()
        self.tmp.cleanup()
    def fake(self,text):
        r=str(self.root)
        for old in ('/tmp/','/run/cammy-select','/etc/cammy','/mnt/extsd','/dev/mmcblk0'):text=text.replace(old,r+old)
        return text
    def run_s99(self,action='start',cp_shim=None):
        env=dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH'])
        if cp_shim:
            # A card that returns other bytes on the copy read than any earlier read.
            (self.bin/'cp').write_text('#!/bin/sh\n/bin/cp "$@" || exit\ncase "$1" in */%s) printf X >>"$2" ;; esac\n'%cp_shim)
            (self.bin/'cp').chmod(0o755)
        return subprocess.run(['sh',str(self.script),action],env=env,capture_output=True,text=True,timeout=30)
    def ran(self):
        p=self.root/'ran';return p.read_text() if p.exists() else ''
    def assert_refused(self,result):
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertIn('payload differs from the pinned release',result.stdout)
        self.assertFalse(self.guard.exists(),'refusal must not write the one-shot guard')
        self.assertEqual(self.ran(),'','nothing may run after a refusal')
        self.assertFalse((self.ram/'.stage').exists())
    def test_good_payload_runs_only_verified_ram_copies(self):
        result=self.run_s99();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        for n,d in self.payload.items():
            self.assertEqual((self.ram/n).read_bytes(),d,n)
            self.assertEqual(stat.S_IMODE((self.ram/n).stat().st_mode),0o700 if n in I.PROGRAMS else 0o600,n)
        self.assertEqual(stat.S_IMODE(self.ram.stat().st_mode),0o700)
        self.assertEqual(set(os.listdir(self.ram)),set(self.payload))
        self.assertTrue(self.guard.read_text().startswith('phase=launch\n'))
        ram=str(self.ram)
        self.assertEqual(self.ran(),f'exec /bin/sh {ram}/startup.sh\nstartup {ram}/startup.sh\n')
        self.assertFalse((self.root/'tmp/cammy-launcher').exists())
    def test_tampered_manifest_is_refused_before_any_execution(self):
        # Even a manifest consistent with a tampered member differs from the pin.
        (self.card/'rc-health.sh').write_bytes(b'#!/bin/sh\nrm -rf /\n')
        rest={n:(self.card/n).read_bytes() for n in self.payload if n!='SHA256SUMS'}
        (self.card/'SHA256SUMS').write_bytes(I.manifest(rest))
        self.assert_refused(self.run_s99());self.assertFalse((self.ram/'SHA256SUMS').exists())
    def test_wrong_executable_byte_on_card_is_refused(self):
        (self.card/'daemon-exec').write_bytes(self.payload['daemon-exec']+b'#')
        self.assert_refused(self.run_s99());self.assertFalse((self.ram/'daemon-exec').exists())
    def test_ram_copy_differing_from_card_read_is_refused(self):
        for name in ('daemon-exec','startup.sh','rc-health.sh','dhcp.sh'):
            with self.subTest(name=name):
                shutil.rmtree(self.ram,ignore_errors=True)
                self.assert_refused(self.run_s99(cp_shim=name))
                self.assertFalse((self.ram/name).exists())
    def test_busybox_is_pinned_by_stock_md5_before_its_applet_is_used(self):
        self.assert_refused(self.run_s99(cp_shim='busybox'))
        self.assertFalse((self.ram/'busybox').exists());self.assertFalse((self.ram/'SHA256SUMS').exists())
    def test_running_supervisor_is_left_alone(self):
        p=subprocess.Popen(['/bin/sh','-c','sleep 30; :',str(self.ram/'startup.sh')]);self.procs.append(p)
        (self.root/'tmp/cammy-supervisor.pid').write_text(f'{p.pid}\n')
        for _ in range(50):
            if str(self.ram/'startup.sh').encode() in Path(f'/proc/{p.pid}/cmdline').read_bytes():break
            time.sleep(0.05)
        result=self.run_s99();self.assertEqual(result.returncode,0)
        self.assertFalse(self.ram.exists());self.assertFalse(self.guard.exists());self.assertEqual(self.ran(),'')
        self.assertEqual(self.run_s99('stop').returncode,0)
        self.assertEqual(p.wait(timeout=10),-signal.SIGTERM)
    def test_stale_pid_and_stale_ram_files_are_replaced_without_write_through(self):
        p=subprocess.Popen(['/bin/sh','-c','sleep 30; :','unrelated']);self.procs.append(p)
        (self.root/'tmp/cammy-supervisor.pid').write_text(f'{p.pid}\n')
        self.ram.mkdir();outside=self.root/'outside';outside.write_text('keep\n')
        (self.ram/'rc-health.sh').symlink_to(outside);(self.ram/'busybox').write_bytes(b'old');(self.ram/'.stage').write_bytes(b'junk')
        result=self.run_s99();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(outside.read_text(),'keep\n');self.assertFalse((self.ram/'rc-health.sh').is_symlink())
        self.assertEqual((self.ram/'busybox').read_bytes(),self.payload['busybox'])
        self.assertEqual(self.run_s99('stop').returncode,0);self.assertIsNone(p.poll())
    def test_directory_at_a_member_name_is_refused(self):
        (self.ram/'startup.sh').mkdir(parents=True)
        self.assert_refused(self.run_s99());self.assertEqual(os.listdir(self.ram/'startup.sh'),[])
    def test_symlinked_ram_directory_is_refused(self):
        (self.root/'elsewhere').mkdir();self.ram.symlink_to(self.root/'elsewhere')
        self.assert_refused(self.run_s99());self.assertEqual(os.listdir(self.root/'elsewhere'),[])
    def test_existing_guard_still_skips_before_card_io(self):
        self.guard.write_text('phase=payload\n');shutil.rmtree(self.card)
        result=self.run_s99();self.assertEqual(result.returncode,0);self.assertIn('did not finish safely',result.stdout)
        self.assertFalse(self.ram.exists())

    def test_stock_and_wrong_boot_stamp_skip_runtime_before_staging(self):
        self.script.write_text(self.fake(I.bootstrap_s99(
            (B.ROOT/'scripts/S99cammy-tf').read_text(), CARD, self.payload, require_candidate=True)))
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        stamp = self.root / 'run/cammy-select/launch.stamp'
        stamp.parent.mkdir(parents=True)
        for target, identity in [('stock', boot), ('candidate', 'stale')]:
            stamp.write_text(f'target={target}\nboot={identity}\n')
            self.assertEqual(self.run_s99().returncode, 0)
            self.assertFalse(self.ram.exists())
            self.assertFalse(self.guard.exists())
        stamp.write_text(f'target=candidate\nboot={boot}\n')
        self.assertEqual(self.run_s99().returncode, 0)
        self.assertTrue(self.ram.exists())
        self.assertTrue(self.guard.exists())

    # The runtime wrapper, on the real accepted runtime() output.
    def wrapped(self):
        return I.ram_runtime(B.runtime(SLOT),self.runtime_names()).decode()
    def runtime_names(self):
        return {'busybox','wpa_supplicant','wpa_cli','cammy-portal','daemon-exec','native-service-client','face-status','speaker-guardian',
                'cammy-ui','fault-evidence','dhcp.sh','shared-capture.sh','shared-capture.fragment','native-media-start.sh','native-release.conf','rc-health.sh',
                'rc-media-worker.sh','speaker.conf','RELEASE','enable-shared-capture','SHA256SUMS','startup.sh','boot-helper.sh'}
    def run_head(self,tamper=None):
        text=self.wrapped();head=text[:text.index('hostname cammyos\n')]+'echo "HEAD_OK shared=$SHARED_AUDIO"\n'
        payload={n:(Path(HOST_BUSYBOX).read_bytes() if n=='busybox' else ('# '+n+'\n').encode()) for n in self.runtime_names()-{'SHA256SUMS'}}
        payload['enable-shared-capture']=b'experimental-shared-capture-v1\n';payload['startup.sh']=text.encode()
        payload['SHA256SUMS']=I.manifest(payload)
        self.ram.mkdir(mode=0o700)
        for n,d in payload.items():(self.ram/n).write_bytes(d);(self.ram/n).chmod(0o700 if n in I.PROGRAMS else 0o600)
        shutil.rmtree(self.root/'mnt')  # no card at all: the head must not need one
        if tamper:(self.ram/tamper).write_bytes(b'tampered\n')
        (self.root/'head.sh').write_text(self.fake(head))
        return subprocess.run(['sh',str(self.root/'head.sh')],capture_output=True,text=True,timeout=30)
    def test_wrapped_runtime_reads_no_card_payload_and_does_not_rehash(self):
        result=self.run_head();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('HEAD_OK shared=1',result.stdout)
        # S99 verified these RAM copies just before it ran startup.sh; no second pass.
        self.assertFalse((self.ram/'payload-check.log').exists())
    def test_each_ram_member_is_checked_once_by_s99_before_startup_runs(self):
        for name in sorted(self.payload):
            if name in ('busybox','SHA256SUMS'):continue
            line=f'cammy_take {name} {"700" if name in I.PROGRAMS else "600"} {I.sha(self.payload[name])} || cammy_refuse'
            self.assertEqual(self.s99.count(line),1,name)
            self.assertLess(self.s99.index(line),self.s99.index('"$R/daemon-exec"'))
    def test_generated_text_shapes(self):
        text=self.wrapped()
        self.assertNotIn('cp "$CARD',text);self.assertNotIn('sha256sum -c',text);self.assertIn('B=$R/busybox\n',text)
        self.assertIn('"$CARD/enable-diag"',text)  # the explicit diagnostics opt-in stays on TF
        for name in ('mktemp','rmdir','mv -T','/tmp/cammy-launcher'):self.assertNotIn(name,self.s99)
        code='\n'.join(l for l in self.s99.splitlines() if not l.lstrip().startswith('#'))
        self.assertEqual(code.count('sha256sum'),code.count('"$R/busybox" sha256sum'))  # no stock sha256sum
        self.assertLess(self.s99.index('cammy_stage busybox 700 && [ "$(md5sum "$S"'),self.s99.index('cammy_take SHA256SUMS'))
        self.assertLess(self.s99.index('cammy_take startup.sh'),self.s99.index('(set -C;'))
        self.assertLess(self.s99.index('(set -C;'),self.s99.index('"$R/daemon-exec"'))
        self.assertIn(I.sha(self.payload['SHA256SUMS']),self.s99)
        self.assertNotIn(I.sha(self.payload['SHA256SUMS']).encode(),self.payload['startup.sh'])
        health=I.rc_script('rc-health.sh',(B.ROOT/'scripts/rc06/health.sh').read_text(),CARD).decode()
        self.assertIn('. "$R/boot-helper.sh"',health);self.assertNotIn('$CARD/boot-helper.sh',health)
        self.assertIn('ARM=$CARD/../native/arm',health)  # authorized arm checks stay on TF
        for shell in (['dash'],['bash','--posix'],['busybox','sh']):
            for body in (self.s99,text,health):
                subprocess.run(shell+['-n','/dev/stdin'],input=body,text=True,check=True,capture_output=True)
    def test_generators_fail_closed(self):
        bad=dict(self.payload);bad['dhcp.sh']=b'changed'
        with self.assertRaisesRegex(ValueError,'manifest differs'):I.stage_block(CARD,bad)
        bad=dict(self.payload);bad['../x']=b'';bad['SHA256SUMS']=I.manifest({k:v for k,v in bad.items() if k!='SHA256SUMS'})
        with self.assertRaisesRegex(ValueError,'Unsafe'):I.stage_block(CARD,bad)
        with self.assertRaisesRegex(ValueError,'does not stage'):I.ram_runtime(B.runtime(SLOT),self.runtime_names()-{'boot-helper.sh','dhcp.sh'})
        with self.assertRaisesRegex(ValueError,'context differs'):I.ram_runtime(B.runtime(SLOT).replace(b'chmod 700 "$R/dhcp.sh"',b''),self.runtime_names())
        with self.assertRaisesRegex(ValueError,'Source context differs'):I.rc_script('rc-health.sh','. "$CARD/x.sh"\n',CARD)
if __name__=='__main__':unittest.main()
