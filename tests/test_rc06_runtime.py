"""Relocated RC shell workers. Fake process/files, no real devices or mounts."""
import hashlib,json,os,shutil,subprocess,tempfile,time,unittest
from pathlib import Path
from tools.native_trial import records as R
ROOT=Path(__file__).resolve().parents[1]
BOOT='11111111-2222-3333-4444-555555555555'
STOCK_SDVCAM='8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec'
def stock_applets():
    """Command names the stock rootfs links to its BusyBox (no rmdir, no timeout)."""
    from tools.boot_launch.generator import SAVED_STOCK_S01APP
    rootfs=SAVED_STOCK_S01APP.parents[2];names=set()
    for folder in ['bin','sbin','usr/bin','usr/sbin']:
        d=rootfs/folder
        if d.is_dir():names.update(x.name for x in d.iterdir() if x.is_symlink() and os.readlink(x).endswith('busybox'))
    return names
class TrailTests(unittest.TestCase):
    def test_ring_wrap_torn_write_and_previous_boot_report(self):
        # Test the record format against an ordinary directory. Production validates
        # the TF mount and free space before it opens any file.
        code = r"""
#define main evidence_main
#include "src/boot/fault_evidence.c"
#undef main
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int root=open(argv[1], O_DIRECTORY|O_RDONLY);
    int fd=trail_open(root);
    if (fd<0) return 3;
    const char *prior="11111111-2222-3333-4444-555555555555";
    const char *now="22222222-2222-3333-4444-555555555555";
    struct trail_slot sample={0};
    for (uint32_t i=1; i<=TRAIL_SLOTS*2; ++i) {
        sample.sequence=i;
        if (i==TRAIL_SLOTS*2-1)
            snprintf(sample.text,sizeof sample.text,"boot=%s\nrelease=aaaaaaaaaaaaaaaaaaaaaaaa\n"
                     "uptime_s=124\nphase=media_prepare\nlast_request=media-prepare\n"
                     "MemAvailable_kB=8\nCached_kB=7\nSlab_kB=6\nsdvcam_pid=5\n"
                     "sdvcam_RSS_kB=4\nsdvcam_threads=3\n",prior);
        sample.checksum=trail_checksum(&sample);
        if (i==TRAIL_SLOTS*2) sample.text[0]='?'; /* torn newest slot */
        if (pwrite(fd,&sample,sizeof sample,(off_t)((i%TRAIL_SLOTS)*sizeof sample))!=sizeof sample)
            return 4;
    }
    struct trail_slot latest=trail_latest(fd);
    if (latest.sequence!=TRAIL_SLOTS*2-1) return 5;
    copy_previous(root,&latest,prior);
    if (!faccessat(root,"CammyOS-faults/previous-boot.txt",F_OK,0)) return 6;
    copy_previous(root,&latest,now);
    return close(fd) || close(root);
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'test.c';source.write_text(code)
            binary=root/'trail-test'
            subprocess.run(['cc','-Wall','-Wextra','-Werror','-I',str(ROOT),str(source),
                            '-o',str(binary)],check=True,capture_output=True)
            subprocess.run([str(binary),tmp],check=True)
            self.assertEqual((root/'CammyOS-trail').stat().st_size,32768)
            report=(root/'CammyOS-faults/previous-boot.txt').read_text()
            self.assertIn('reason=interrupted-boot\nphase=media_prepare',report)
            self.assertIn('last_request=media-prepare',report)
            self.assertIn('sdvcam_threads=3',report)

class EvidenceHelperTests(unittest.TestCase):
    """fault-evidence's small helpers, compiled for the host with a private RUN_DIR."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.run=self.root/'rd';self.run.mkdir()
        self.binary=self.root/'fe'
        subprocess.run(['cc','-Wall','-Wextra','-Werror',f'-DRUN_DIR="{self.run}"',str(ROOT/'src/boot/fault_evidence.c'),'-o',str(self.binary)],
                       check=True,capture_output=True)
    def tearDown(self):self.tmp.cleanup()
    def fe(self,*args,timeout=10):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,timeout=timeout)

    def test_alarm_interrupts_only_the_current_write(self):
        code=r"""
#define main evidence_main
#include "src/boot/fault_evidence.c"
#undef main
int main(void) {
    int p[2]; char c;
    if (pipe(p) || bound_writes()) return 2;
    alarm(1);
    ssize_t r = read(p[0], &c, 1);  /* a stalled write stands in for a stuck card */
    return r < 0 && errno == EINTR && alarm_fired ? 0 : 1;
}
"""
        source=self.root/'alarm.c';source.write_text(code);binary=self.root/'alarm'
        subprocess.run(['cc','-Wall','-Wextra','-Werror','-I',str(ROOT),str(source),'-o',str(binary)],check=True,capture_output=True)
        self.assertEqual(subprocess.run([str(binary)],timeout=5).returncode,0)

    def test_trail_oops_watch_reads_the_whole_taint_and_marks_once(self):
        fake=self.root/'proc';(fake/'sys/kernel').mkdir(parents=True);(fake/'sys/kernel/tainted').write_text('4224\n')
        code=r"""
#define main evidence_main
#include "src/boot/fault_evidence.c"
#undef main
int main(void) {
    if (read_taint() != 4224) return 2;
    if (!mark_oops(read_taint(), 51) || !mark_oops(read_taint(), 52)) return 3;
    return 0;
}
"""
        source=self.root/'oops.c';source.write_text(code);binary=self.root/'oops'
        subprocess.run(['cc','-Wall','-Wextra','-Werror','-I',str(ROOT),f'-DPROC="{fake}"',f'-DRUN_DIR="{self.run}"',str(source),'-o',str(binary)],check=True,capture_output=True)
        self.assertEqual(subprocess.run([str(binary)],timeout=5).returncode,0)
        self.assertEqual((self.run/'kernel-oops').read_text(),'taint=4224\nuptime_s=51\n')

    def test_mkfifo_only_inside_the_run_directory(self):
        self.assertEqual(self.fe('mkfifo',self.run/'wake').returncode,0)
        import stat
        self.assertTrue(stat.S_ISFIFO((self.run/'wake').stat().st_mode))
        self.assertNotEqual(self.fe('mkfifo',self.root/'outside').returncode,0)
        self.assertNotEqual(self.fe('mkfifo',str(self.run)+'/../escape').returncode,0)

    def test_cap_keeps_the_newest_half_of_an_oversized_log_in_place(self):
        log=self.run/'ui.log';small=self.run/'wpa.log'
        lines=''.join(f'line {i:06d}\n' for i in range(20000))
        log.write_text(lines);small.write_text('short\n')
        with open(log,'a') as writer:
            r=self.fe('cap',65536,log,small,self.run/'missing.log')
            self.assertEqual(r.returncode,0,r.stderr)
            writer.write('after cap\n')  # an O_APPEND writer continues at the new end
        text=log.read_text()
        self.assertLessEqual(len(text),65536)
        self.assertTrue(text.startswith('[earlier lines dropped: log cap]\nline '))
        self.assertTrue(text.endswith('line 019999\nafter cap\n'))
        self.assertEqual(small.read_text(),'short\n')
        self.assertNotEqual(self.fe('cap',65536,self.root/'outside.log').returncode,0)
        self.assertNotEqual(self.fe('cap',100,log).returncode,0)

    def test_wpa_status_over_the_control_socket(self):
        import socket,threading
        ctrl=self.run/'ctrl';ctrl.mkdir()
        server=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);server.bind(str(ctrl/'wlan0'));self.addCleanup(server.close)
        replies=iter([b'bssid=x\nwpa_state=COMPLETED\nip_address=1\n',b'wpa_state=SCANNING\n',b'OK\n'])
        seen=[]
        def serve():
            for reply in replies:
                data,addr=server.recvfrom(64);seen.append(data);server.sendto(reply,addr)
        t=threading.Thread(target=serve,daemon=True);t.start()
        r=self.fe('wpa',ctrl,'wlan0','STATUS');self.assertEqual((r.returncode,r.stdout),(0,'wpa_state=COMPLETED\n'))
        r=self.fe('wpa',ctrl,'wlan0','STATUS');self.assertEqual((r.returncode,r.stdout),(1,'wpa_state=SCANNING\n'))
        self.assertEqual(self.fe('wpa',ctrl,'wlan0','REASSOCIATE').returncode,0)
        t.join(2);self.assertEqual(seen,[b'STATUS',b'STATUS',b'REASSOCIATE'])
        self.assertEqual(sorted(p.name for p in ctrl.iterdir()),['wlan0'])  # nothing left behind
        # No reply: bounded failure, not a hang.
        started=time.monotonic();self.assertEqual(self.fe('wpa',ctrl,'wlan0','STATUS').returncode,1)
        self.assertLess(time.monotonic()-started,3)
        self.assertEqual(self.fe('wpa',ctrl,'wlan0','TERMINATE').returncode,2)
        self.assertEqual(self.fe('wpa',self.root,'wlan0','STATUS').returncode,2)

class RadioWatchTests(unittest.TestCase):
    """Startup's radio watch (boot fbaa812c): kernel-oops taint, a dead
    wpa_supplicant, bounded network commands and the radio-fault latch. Runs
    the real startup text under dash; only the taint and uptime files are fake,
    process polling reads the host's /proc."""
    RELEASE='a'*24;MD5='b'*32
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.run=self.base/'run';self.cfg=self.base/'etc/cammy';self.fake=self.base/'fakeproc';self.bin=self.base/'bin'
        for p in [self.run,self.cfg,self.fake,self.bin]:p.mkdir(parents=True)
        self.write(self.fake/'tainted','4096\n');self.write(self.fake/'uptime','1000.0 0\n')
        self.calls=self.base/'calls'
        for name in ('ifconfig','iw','hostapd','udhcpd'):
            self.script(self.bin/name,f'echo "{name} $*" >>"{self.calls}"\n[ "$*" != "dev wlan0 info" ] || printf \'\\ttype managed\\n\'\n')
        self.script(self.run/'fault-evidence',f'echo "fe $*" >>"{self.calls}"\n[ "$1" != wpa ] || exit 1\n')
        self.script(self.run/'busybox',f'echo "busybox $*" >>"{self.calls}"\n')
        self.script(self.run/'rc-health.sh',f'echo "health $*" >>"{self.calls}"\n')
        self.script(self.run/'wpa_supplicant','exit 0\n')
        self.env=dict(os.environ,PATH=str(self.bin)+':/usr/bin:/bin')
    def tearDown(self):self.tmp.cleanup()
    def write(self,p,text):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    def script(self,p,text):self.write(p,'#!/bin/sh\n'+text);p.chmod(0o700)
    def source(self,release=True):
        from tools.rc06_build import runtime
        raw=runtime('synthetic').decode()
        funcs=raw[raw.index('# Radio watch'):raw.index('phase() {')]
        station=raw[raw.index('{ read -r UP _ </proc/uptime; } 2>/dev/null || UP=0\nWLAN_AT'):raw.index('phase portal\n')]
        def relocate(t):
            return (t.replace('/proc/sys/kernel/tainted',str(self.fake/'tainted')).replace('/proc/uptime',str(self.fake/'uptime'))
                     .replace('/etc/cammy',str(self.cfg)))
        head=(f'set -u\nR="{self.run}"\nGUARD="{self.cfg}/startup.pending"\nBOOT_ID={BOOT}\nB="$R/busybox"\n'
              + (f'RELEASE_ID={self.RELEASE}\nCANDIDATE_MD5={self.MD5}\n' if release else '')
              + f'diag(){{ :; }}\nphase(){{ echo "phase $1" >>"{self.calls}"; }}\nstop_clients(){{ :; }}\nstop_ap(){{ :; }}\n'
              f'ap_fallback(){{ echo ap-fallback >>"{self.calls}"; echo AP_CammyOS >"$R/state"; }}\n'
              # Each wait advances the fake clock by a second.
              f'sleep(){{ echo sleep >>"{self.calls}"; read -r u _ <"{self.fake}/uptime"; echo "$((${{u%%.*}}+1)).0 0" >"{self.fake}/uptime"; }}\n')
        return head,relocate(funcs),relocate(station)
    def go(self,body,release=True,timeout=20):
        head,funcs,_=self.source(release)
        script=self.base/'t.sh';self.write(script,head+funcs+body)
        started=time.monotonic()
        r=subprocess.run(['dash',str(script)],env=self.env,capture_output=True,text=True,timeout=timeout)
        self.elapsed=time.monotonic()-started
        return r
    def station(self,release=True,timeout=20):
        _,_,station=self.source(release)
        return self.go(station+'echo reached-end\n',release,timeout)
    def log(self):return self.calls.read_text().splitlines() if self.calls.exists() else []
    def state(self):return (self.run/'state').read_text().strip() if (self.run/'state').exists() else ''

    def test_new_oops_taint_is_a_radio_fault_but_a_baseline_oops_is_not(self):
        r=self.go(f'radio_check && echo clean\necho 4224 >"{self.fake}/tainted"\nradio_check || echo faulted\nnet_run 5 ifconfig wlan0 down || echo refused\n')
        self.assertEqual(r.stdout.split('\n')[0],'clean')
        self.assertIn('faulted',r.stdout);self.assertIn('refused',r.stdout)
        self.assertEqual(self.state(),'RADIO_FAULT')
        self.assertIn('reason=kernel-oops',(self.run/'radio-fault').read_text())
        self.assertIn('health snapshot radio-kernel-oops',self.log())
        self.assertNotIn('ifconfig wlan0 down',self.log())
        self.calls.unlink();(self.run/'state').unlink()
        self.write(self.fake/'tainted','4224\n')
        r=self.go(f'echo 4352 >"{self.fake}/tainted"\nradio_check && echo clean\n')
        self.assertIn('clean',r.stdout);self.assertEqual(self.state(),'')

    def test_the_trail_marker_replaces_the_taint_read_while_the_trail_runs(self):
        self.script(self.run/'fault-evidence',f'echo "fe $*" >>"{self.calls}"\n[ "$1" != hold ] || {{ /bin/sleep 3 >/dev/null 2>&1; exit 0; }}\n[ "$1" != wpa ] || exit 1\n')
        r=self.go('"$R/fault-evidence" hold &\nTRAIL_PID=$!\n/bin/sleep 0.2\n'
                  f'echo 4224 >"{self.fake}/tainted"\nradio_check && echo trusts-trail\n'
                  f': >"$R/kernel-oops"\nradio_check || echo faulted\nkill $TRAIL_PID\n')
        self.assertIn('trusts-trail',r.stdout);self.assertIn('faulted',r.stdout)
        self.assertEqual(self.state(),'RADIO_FAULT')
        # Without a live trail the taint is read directly.
        (self.run/'state').unlink();(self.run/'kernel-oops').unlink();self.write(self.fake/'tainted','4096\n')
        r=self.go(f'TRAIL_PID=999999999\necho 4224 >"{self.fake}/tainted"\nradio_check || echo faulted\n')
        self.assertIn('faulted',r.stdout)

    def test_dead_wpa_supplicant_ends_the_association_wait_at_once(self):
        r=self.station()
        self.assertEqual(r.returncode,0,r.stderr);self.assertIn('reached-end',r.stdout)
        self.assertIn('wpa_supplicant exited during station setup',r.stdout)
        log=self.log()
        # Three settle seconds after wlan0, then at most one association wait (not 35).
        self.assertLessEqual(log.count('sleep'),5)
        self.assertIn('ap-fallback',log);self.assertNotIn('phase dhcp',log)
        self.assertEqual(self.state(),'AP_CammyOS')

    def test_driver_station_interface_is_not_retyped_and_wpa_waits_three_seconds(self):
        self.script(self.run/'wpa_supplicant',f'read -r u _ <"{self.fake}/uptime";echo "wpa-start $u" >>"{self.calls}"\n')
        self.station()
        log=self.log()
        self.assertNotIn('ifconfig wlan0 down',log);self.assertNotIn('iw dev wlan0 set type managed',log)
        self.assertIn('ifconfig wlan0 0.0.0.0 up',log);self.assertIn('wpa-start 1003.0',log)
        self.calls.unlink()
        self.script(self.bin/'iw',f'echo "iw $*" >>"{self.calls}"\n[ "$*" != "dev wlan0 info" ] || printf \'\\ttype AP\\n\'\n')
        self.station()
        log=self.log()
        self.assertLess(log.index('ifconfig wlan0 down'),log.index('iw dev wlan0 set type managed'))
        self.assertLess(log.index('iw dev wlan0 set type managed'),log.index('ifconfig wlan0 0.0.0.0 up'))

    def test_oops_in_the_first_scan_records_the_latch_and_runs_no_more_network_commands(self):
        guard=self.cfg/'startup.pending';guard.write_text(f'phase=launch\nboot_id={BOOT}\n')
        self.script(self.run/'wpa_supplicant',f'echo 4224 >"{self.fake}/tainted"\n')
        r=self.station()
        self.assertEqual(r.returncode,1);self.assertNotIn('reached-end',r.stdout)
        self.assertEqual(self.state(),'RADIO_FAULT')
        self.assertEqual(guard.read_text(),f'phase=radio_fault\nboot_id={BOOT}\nrelease_id={self.RELEASE}\ncandidate_md5={self.MD5}\n')
        log=self.log()
        start=log.index('ifconfig wlan0 0.0.0.0 up')
        self.assertFalse([c for c in log[start+1:] if c.split()[0] in ('ifconfig','iw','hostapd','udhcpd','busybox','ap-fallback')])
        # Synced before and after the rename.
        self.assertEqual([c for c in log if c.startswith('fe sync')],['fe sync','fe sync'])
        self.assertEqual(list(self.cfg.glob('.startup-radio.*')),[])

    def test_radio_latch_replaces_only_a_latch_this_startup_owns(self):
        guard=self.cfg/'startup.pending'
        owned=f'phase=idle_ready\nboot_id={BOOT}\nrelease_id={self.RELEASE}\ncandidate_md5={self.MD5}\n'
        for before,release,replaced in [(owned,True,True),(f'phase=media_prepare\nboot_id={BOOT}\n',True,False),
                                        (f'phase=launch\nboot_id={"0"*8}-0000-4000-8000-000000000000\n',True,False),
                                        (f'phase=launch\nboot_id={BOOT}\n',False,False)]:
            with self.subTest(before=before,release=release):
                guard.write_text(before)
                self.go('radio_fault RADIO_FAULT kernel-oops\n',release)
                self.assertEqual(guard.read_text()!=before,replaced)
                if replaced:self.assertTrue(guard.read_text().startswith('phase=radio_fault\n'))

    def test_a_hung_network_command_is_abandoned_at_its_bound(self):
        self.script(self.bin/'ifconfig',f'echo "ifconfig $*" >>"{self.calls}"\nexec /bin/sleep 4 >/dev/null 2>&1\n')
        r=self.go('net_run 1 ifconfig wlan0 down; echo "rc=$?"\nnet_run 1 iw dev wlan0 set type __ap || echo refused\n',timeout=10)
        self.assertIn('rc=1',r.stdout);self.assertIn('refused',r.stdout)
        # The bound is five 0.2 s polls; the abandoned task keeps running.
        self.assertLess(self.elapsed,3)
        self.assertEqual(self.state(),'RADIO_HUNG')
        self.assertIn('command=ifconfig',(self.run/'radio-fault').read_text())
        self.assertIn('health snapshot radio-command-hung',self.log())
        self.assertNotIn('iw dev wlan0 set type __ap',self.log())

    def test_a_quick_network_command_keeps_its_exit_status(self):
        self.script(self.bin/'iw','exit 3\n')
        r=self.go('net_run 5 iw dev wlan0 info; echo "rc=$?"\nradio_ok && echo ok\n')
        self.assertIn('rc=3',r.stdout);self.assertIn('ok',r.stdout)
        self.assertLess(self.elapsed,2)

class RuntimeTests(unittest.TestCase):
    SH=['/usr/bin/dash']
    PATH_TAIL='/usr/bin:/bin'
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.run=self.base/'tmp/cammy-runtime';self.cfg=self.base/'etc/cammy';self.proc=self.base/'proc';self.card=self.base/'card/runtime';self.native=self.base/'run/cammy-select/sdvcam'
        for p in [self.run,self.cfg,self.proc,self.card,self.native.parent,self.base/'bin']:
            p.mkdir(parents=True,exist_ok=True)
        self.native.write_bytes(b'synthetic-native');self.h=hashlib.sha256(self.native.read_bytes()).hexdigest()
        self.m=hashlib.md5(self.native.read_bytes()).hexdigest()
        # S01's stamp of the MD5 it verified just before exec; health binds it to the release.
        self.write(self.native.parent/'launch.stamp',f'target=candidate\nboot={BOOT}\nmd5={self.m}\n')
        self.write(self.proc/'sys/kernel/random/boot_id',BOOT+'\n');self.write(self.proc/'uptime','1000.0 0\n')
        self.write(self.proc/'77/stat',' '.join(['77','(sdvcam)','S']+['0']*18+['123'])+'\n')
        self.write(self.proc/'88/comm','cammy-portal\n');self.write(self.run/'portal.pid','88\n')
        self.write(self.proc/'asound/card0/pcm0c/sub0/status','closed\n')
        (self.proc/'77/exe').symlink_to(self.native)
        self.write(self.run/'native-release.conf',f'format=cammy-native-release/1\nrelease_id=local-test\ncandidate_sha256={self.h}\ncandidate_md5={self.m}\n')
        self.write(self.run/'state','HOME_STATION\n')
        self.script(self.base/'bin/pidof','case "$1" in sdvcam) echo 77;; hostapd|udhcpd) [ ! -e "'+str(self.base)+'/ap-$1" ] || echo 5;; esac\n')
        self.script(self.run/'fault-evidence', '[ "$1" != sync ] || exec sync\n')
        self.script(self.base/'bin/sync','echo sync >>"$TEST_CALLS"\n[ ! -e "$TEST_FAIL_SYNC" ]\n')
        self.script(self.run/'face-status','[ ! -e "'+str(self.base/'no-gui')+'" ] || exit 1\necho native_pid=77\n')
        self.script(self.run/'busybox','[ "$1" = sha256sum ] || exit 1\nshift\nexec /usr/bin/sha256sum "$@"\n')
        self.script(self.run/'native-service-client','echo "client:$*" >>"$TEST_CALLS"\n')
        self.script(self.run/'native-media-start.sh','echo native-media >>"$TEST_CALLS"\necho sdp-ready-idle >"'+str(self.run/'native-video.state')+'"\n')
        self.env=dict(os.environ,PATH=str(self.base/'bin')+':'+self.PATH_TAIL,TEST_CALLS=str(self.base/'calls'),TEST_FAIL_SYNC=str(self.base/'fail-sync'))
    def tearDown(self):self.tmp.cleanup()
    def write(self,p,text):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    def script(self,p,text):self.write(p,'#!/bin/sh\n'+text);p.chmod(0o700)
    def relocated(self,name):
        s=(ROOT/'scripts/rc06'/name).read_text().replace('@CARD@',str(self.card))
        for prefix in ['/tmp/cammy-runtime','/etc/cammy','/proc','/run/cammy-select']:
            s=s.replace(prefix,str(self.base)+prefix)
        p=self.base/name;self.write(p,s);return p
    def execute(self,name):return subprocess.run([*self.SH,str(self.relocated(name))],env=self.env,capture_output=True,text=True,timeout=3)
    def job(self,boot=BOOT):
        self.write(self.run/'media-prepare/request',f'format=cammy-media-prepare/1\nboot={boot.replace("-","")}\ndeadline_ms=1050000\n')
    def calls(self):return (self.base/'calls').read_text() if (self.base/'calls').exists() else ''
    def crash_trial(self):
        from tools.rc06_build import helper
        release, md5 = 'a'*24, 'b'*32
        self.write(self.run/'native-release.conf',
                   f'format=cammy-native-release/1\nrelease_id={release}\ncandidate_sha256={self.h}\ncandidate_md5={md5}\n')
        self.write(self.native.parent/'launch.stamp', f'target=candidate\nboot={BOOT}\nmd5={md5}\n')
        self.write(self.cfg/'native-select.resident', f'format=cammy-resident/1\nrelease_id={release}\n')
        self.write(self.cfg/'native-select.allow',
                   f'format=x\ns01app_md5=x\nsource_md5=x\nsource_size=x\ncandidate={md5}:1:x\nlibrary=x\n')
        self.write(self.run/'boot-helper.sh',helper('synthetic').decode().replace('/etc/cammy',str(self.cfg)))
        self.write(self.run/'release-health/committed','already checked\n')
        self.write(self.run/'release-health/owner','77 123\n')
        return release, md5

    def test_native_loss_eligibility_uses_verified_resident_owner_and_media_state(self):
        for busy in (False, True):
            with self.subTest(media_busy=busy):
                self.tearDown()
                self.setUp()
                release, md5 = self.crash_trial()
                shutil.rmtree(self.proc/'77')
                self.script(self.base/'bin/pidof', 'exit 1\n')
                if busy:
                    self.write(self.run/'media-prepare/work.lock', 'active\n')
                result = self.execute('health.sh')
                self.assertEqual(result.stdout.strip(), 'health_reason=native-pid')
                self.write(self.run/'health.log', result.stdout)
                result = subprocess.run([*self.SH, str(self.relocated('health.sh')), 'latch'],
                                        env=self.env, capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
                guard = (self.cfg/'startup.pending').read_text()
                if busy:
                    self.assertIn('phase=runtime_health_fault', guard)
                else:
                    self.assertIn(f'phase=post_healthy_fault\nboot_id={BOOT}\nrelease_id={release}\ncandidate_md5={md5}', guard)

    def test_crash_fault_write_cuts_never_publish_fault_before_guard(self):
        for cut in range(1, 5):
            with self.subTest(sync=cut):
                self.tearDown()
                self.setUp()
                self.crash_trial()
                shutil.rmtree(self.proc/'77')
                self.script(self.base/'bin/pidof', 'exit 1\n')
                result = self.execute('health.sh')
                self.write(self.run/'health.log', result.stdout)
                self.script(self.base/'bin/sync', """
n=$(cat "$TEST_CALLS" 2>/dev/null || echo 0)
n=$((n+1))
echo "$n" >"$TEST_CALLS"
[ "$n" -ne "$TEST_CUT" ] || kill -KILL "$PPID"
""")
                result = subprocess.run([*self.SH, str(self.relocated('health.sh')), 'latch'],
                                        env={**self.env, 'TEST_CUT': str(cut)}, capture_output=True,
                                        text=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                fault = self.cfg/'runtime-health-fault'
                guard = self.cfg/'startup.pending'
                self.assertEqual(guard.exists(), cut >= 2)
                self.assertEqual(fault.exists(), cut == 4)
                if fault.exists():
                    self.assertIn('guard_before=absent', fault.read_text())
                    self.assertIn('phase=post_healthy_fault', guard.read_text())

    def test_crash_retry_replenishes_after_900_seconds_of_continuous_probes(self):
        release, md5 = self.crash_trial()
        ledger = self.cfg/'native-select.crash-used'
        self.write(ledger, f'format=cammy-crash-retry-used/1\nrelease_id={release}\ncandidate_md5={md5}\n'
                   f'failed_boot=00000000-0000-4000-8000-000000000000\nretry_boot={BOOT}\n')
        self.write(self.run/'release-health/since','100\n')
        self.write(self.run/'release-health/network','HOME_STATION\n')
        self.write(self.run/'release-health/retry-since','100\n')
        self.write(self.run/'release-health/retry-last','950\n')
        self.assertEqual(self.execute('health.sh').returncode, 0)
        self.assertEqual((self.run/'release-health/retry-since').read_text(),'1000\n')
        self.assertTrue(ledger.exists())
        # Each passing probe must extend the same interval. A GUI miss discards it.
        for moment in (1012, 1024):
            self.write(self.proc/'uptime', f'{moment}.0 0\n')
            self.assertEqual(self.execute('health.sh').returncode, 0)
        (self.base/'no-gui').touch()
        self.write(self.proc/'uptime', '1036.0 0\n')
        self.assertEqual(self.execute('health.sh').returncode, 0)
        self.assertFalse((self.run/'release-health/retry-since').exists())
        (self.base/'no-gui').unlink()
        self.write(self.run/'release-health/since', '100\n')
        for moment in range(1048, 1949, 12):
            self.write(self.proc/'uptime', f'{moment}.0 0\n')
            result = self.execute('health.sh')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ledger.exists(), moment < 1948)

    # Radio retry ledger (boot fbaa812c): the same 900 s of sampled health
    # replenishes it; a ledger of another release is retired at once.
    def test_radio_retry_ledger_replenishes_after_900_seconds_of_continuous_probes(self):
        release, md5 = self.crash_trial()
        ledger = self.cfg/'native-select.radio-used'
        self.write(ledger, f'format=cammy-radio-retry-used/1\nrelease_id={release}\ncandidate_md5={md5}\n'
                   f'failed_boot=00000000-0000-4000-8000-000000000000\nretry_boot=00000000-0000-4000-8000-000000000001\n')
        self.write(self.run/'release-health/since','100\n')
        self.write(self.run/'release-health/network','HOME_STATION\n')
        self.assertEqual(self.execute('health.sh').returncode, 0)
        self.assertEqual((self.run/'release-health/radio-since').read_text(),'1000\n')
        (self.base/'no-gui').touch()
        self.write(self.proc/'uptime', '1012.0 0\n')
        self.assertEqual(self.execute('health.sh').returncode, 0)
        self.assertFalse((self.run/'release-health/radio-since').exists())
        (self.base/'no-gui').unlink()
        self.write(self.run/'release-health/since', '100\n')
        for moment in range(1024, 1937, 12):
            self.write(self.proc/'uptime', f'{moment}.0 0\n')
            result = self.execute('health.sh')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ledger.exists(), moment < 1924, moment)

    def test_radio_retry_ledger_of_another_release_is_retired_and_malformed_is_kept(self):
        release, md5 = self.crash_trial()
        ledger = self.cfg/'native-select.radio-used'
        self.write(self.run/'release-health/since','100\n')
        self.write(self.run/'release-health/network','HOME_STATION\n')
        self.write(ledger, 'format=cammy-radio-retry-used/1\nbroken\n')
        self.assertEqual(self.execute('health.sh').returncode, 0)
        self.assertTrue(ledger.exists())
        self.write(ledger, f'format=cammy-radio-retry-used/1\nrelease_id={"c"*24}\ncandidate_md5={md5}\n'
                   f'failed_boot=00000000-0000-4000-8000-000000000000\nretry_boot=00000000-0000-4000-8000-000000000001\n')
        self.assertEqual(self.execute('health.sh').returncode, 0)
        self.assertFalse(ledger.exists())

    def test_radio_fault_latch_is_kept_and_never_becomes_a_health_fault(self):
        release, md5 = self.crash_trial()
        (self.run/'release-health/committed').unlink()
        guard = self.cfg/'startup.pending'
        text = f'phase=radio_fault\nboot_id={BOOT}\nrelease_id={release}\ncandidate_md5={md5}\n'
        self.write(guard, text)
        self.assertEqual(self.execute('health.sh').returncode, 0)
        self.write(self.run/'health.log', 'health_reason=native-pid\n')
        result = subprocess.run([*self.SH,str(self.relocated('health.sh')),'latch'],env=self.env,capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('latch-kept-radio-fault', result.stdout)
        self.assertEqual(guard.read_text(), text)
        self.assertFalse((self.cfg/'runtime-health-fault').exists())

    def test_health_binds_the_selector_stamp_md5_without_rehashing_the_candidate(self):
        self.write(self.native.parent/'launch.stamp', f'target=candidate\nboot={BOOT}\nmd5={"0"*32}\n')
        result = self.execute('health.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('health_reason=native-hash', result.stdout)
        self.script(self.run/'busybox', 'echo hashed >>"$TEST_CALLS"\nexit 1\n')
        self.write(self.native.parent/'launch.stamp', f'target=candidate\nboot={BOOT}\nmd5={self.m}\n')
        self.assertEqual(self.execute('health.sh').returncode, 0)
        self.assertNotIn('hashed', self.calls())

    def test_no_request_never_touches_native_or_guard(self):
        self.assertEqual(self.execute('media-worker.sh').returncode,0);self.assertEqual(self.calls(),'')
        self.assertFalse((self.cfg/'startup.pending').exists())
    def test_job_prepares_once_leaves_no_capture_and_restores_boot_guard(self):
        original=f'phase=idle_ready\nboot_id={BOOT}\n';self.write(self.cfg/'startup.pending',original);self.job()
        self.assertEqual(self.execute('media-worker.sh').returncode,0)
        self.assertEqual((self.run/'media-prepare/state').read_text(),'ready\n')
        self.assertEqual((self.cfg/'startup.pending').read_text(),original)
        self.execute('media-worker.sh');self.assertEqual(self.calls().count('native-media'),1)
        self.assertIn('client:idle',self.calls());self.assertIn('client:release',self.calls())
    def test_cancel_old_boot_and_expired_request_never_start_native(self):
        for fault in ['cancel','boot','deadline']:
            with self.subTest(fault=fault):
                d=self.run/'media-prepare'
                import shutil
                if d.exists():shutil.rmtree(d)
                self.job('99999999-2222-3333-4444-555555555555' if fault=='boot' else BOOT)
                if fault=='cancel':self.write(d/'cancel','cancel\n')
                if fault=='deadline':self.write(self.proc/'uptime','2000.0 0\n')
                self.assertNotEqual(self.execute('media-worker.sh').returncode,0)
                self.assertNotIn('native-media',self.calls());self.assertEqual((d/'state').read_text(),'requires-recovery\n')
    def test_media_failure_retains_guard_and_never_retries(self):
        self.job();self.script(self.run/'native-media-start.sh','echo native-failed >>"$TEST_CALLS"\nexit 1\n')
        self.assertNotEqual(self.execute('media-worker.sh').returncode,0)
        self.assertIn('phase=media_prepare',(self.cfg/'startup.pending').read_text())
        self.execute('media-worker.sh');self.assertEqual(self.calls().count('native-failed'),1)
    def test_clean_refusal_restores_guard_and_allows_a_new_request(self):
        # 0.7.4: a preflight refusal touched nothing on the camera, so no startup latch.
        for prior in (False,True):
            with self.subTest(prior=prior):
                guard=self.cfg/'startup.pending';guard.unlink(missing_ok=True)
                if prior:self.write(guard,f'phase=idle_ready\nboot_id={BOOT}\n')
                self.job();self.script(self.run/'native-media-start.sh','echo native-refused >>"$TEST_CALLS"\necho refused-native-mode >"'+str(self.run/'native-video.state')+'"\nexit 1\n')
                self.assertEqual(self.execute('media-worker.sh').returncode,0)
                d=self.run/'media-prepare'
                self.assertEqual((d/'state').read_text(),'refused\n')
                self.assertEqual((d/'reason').read_text(),'native-refused-native-mode\n')
                self.assertFalse((d/'request').exists());self.assertFalse((d/'work.lock').exists())
                if prior:self.assertEqual(guard.read_text(),f'phase=idle_ready\nboot_id={BOOT}\n')
                else:self.assertFalse(guard.exists())
                self.assertEqual(self.execute('health.sh').returncode,0)
    def test_failure_after_native_init_still_latches(self):
        self.job();self.script(self.run/'native-media-start.sh','echo event-unavailable >"'+str(self.run/'native-video.state')+'"\nexit 1\n')
        self.assertNotEqual(self.execute('media-worker.sh').returncode,0)
        self.assertIn('phase=media_prepare',(self.cfg/'startup.pending').read_text())
        self.assertEqual((self.run/'media-prepare/state').read_text(),'requires-recovery\n')
    def test_sync_failure_never_reaches_native(self):
        self.job();self.write(self.base/'fail-sync','1')
        self.assertNotEqual(self.execute('media-worker.sh').returncode,0)
        self.assertNotIn('native-media',self.calls())
    def test_health_requires_continuity_and_does_not_automatically_rearm(self):
        self.write(self.cfg/'startup.pending',f'phase=idle_ready\nboot_id={BOOT}\n')
        self.assertEqual(self.execute('health.sh').returncode,0)
        self.assertFalse((self.run/'release-health/healthy').exists())
        self.write(self.proc/'uptime','1121.0 0\n')
        self.assertEqual(self.execute('health.sh').returncode,0)
        self.assertTrue((self.run/'release-health/healthy').exists())
        self.assertFalse((self.cfg/'startup.pending').exists())
        self.assertFalse((self.card.parent/'native/arm').exists())
    def test_display_ready_is_early_and_resets_when_checks_stop_passing(self):
        guard = self.cfg / 'startup.pending'
        self.write(guard, f'phase=idle_ready\nboot_id={BOOT}\n')
        health = self.run / 'release-health'
        display = health / 'display-ready'
        for uptime in (1000, 1005):
            self.assertEqual(self.health_at(uptime).returncode, 0)
            self.assertFalse(display.exists())
        self.assertEqual(self.health_at(1006).returncode, 0)
        self.assertEqual(display.read_text(), f'release_id=local-test\nboot={BOOT}\nnetwork=HOME_STATION\n')
        self.assertTrue(guard.exists())
        self.assertFalse((health / 'healthy').exists())

        self.write(self.base / 'no-gui', '1')
        self.assertEqual(self.health_at(1007).returncode, 0)
        self.assertFalse(display.exists())
        (self.base / 'no-gui').unlink()
        self.assertEqual(self.health_at(1008).returncode, 0)
        self.assertEqual(self.health_at(1014).returncode, 0)
        self.assertTrue(display.exists())
        self.script(self.run / 'face-status', 'echo native_pid=99\n')
        self.assertEqual(self.health_at(1015).returncode, 1)
        self.assertFalse(display.exists())
        self.assertTrue(guard.exists())
    def test_network_loss_resets_health_timer_without_quarantine(self):
        self.execute('health.sh');self.write(self.run/'state','AP_FALLBACK\n')
        self.assertEqual(self.execute('health.sh').returncode,0)
        self.assertFalse((self.run/'release-health/since').exists())
        self.write(self.run/'state','HOME_STATION\n');self.write(self.proc/'uptime','1121.0 0\n')
        self.assertEqual(self.execute('health.sh').returncode,0)
        self.assertFalse((self.run/'release-health/healthy').exists())
    def health_at(self, uptime):
        self.write(self.proc/'uptime', f'{uptime}.0 0\n')
        return self.execute('health.sh')
    def test_queued_media_defers_health_and_ready_starts_a_new_window(self):
        guard=self.cfg/'startup.pending'
        prior=f'phase=idle_ready\nboot_id={BOOT}\n'
        self.write(guard,prior)
        self.health_at(1000)
        self.job()
        self.assertEqual(self.health_at(1120).returncode,0)
        self.assertEqual(guard.read_text(),prior)
        self.assertFalse((self.run/'release-health/since').exists())
        self.write(self.run/'media-prepare/state','initializing\n')
        self.write(guard,f'phase=media_prepare\nboot_id={BOOT}\n')
        self.assertEqual(self.health_at(1124).returncode,0)
        self.write(self.run/'media-prepare/state','ready\n')
        self.write(guard,prior)
        self.assertEqual(self.health_at(1130).returncode,0)
        self.assertEqual(self.health_at(1249).returncode,0)
        self.assertTrue(guard.exists())
        self.assertEqual(self.health_at(1250).returncode,0)
        self.assertFalse(guard.exists())
    def test_fast_media_preparation_restarts_window_and_pcm_rollover_does_not(self):
        guard=self.cfg/'startup.pending'
        self.write(guard,f'phase=idle_ready\nboot_id={BOOT}\n')
        self.health_at(1000)
        self.job()
        self.write(self.run/'media-prepare/state','ready\n')
        pcm=self.proc/'asound/card0/pcm0c/sub0/status'
        for uptime,status in [(1120,'state: RUNNING'),(1122,'closed'),(1124,'state: RUNNING')]:
            self.write(pcm,status+'\n')
            self.assertEqual(self.health_at(uptime).returncode,0)
            self.assertTrue(guard.exists())
        self.assertEqual(self.health_at(1240).returncode,0)
        self.assertFalse(guard.exists())
    def test_a_stuck_media_transaction_times_out_without_clearing_guard(self):
        self.job()
        guard=self.cfg/'startup.pending'
        prior=f'phase=media_prepare\nboot_id={BOOT}\n'
        self.write(guard,prior)
        self.write(self.run/'media-prepare/state','initializing\n')
        self.assertEqual(self.health_at(1000).returncode,0)
        self.assertEqual(self.health_at(1119).returncode,0)
        result=self.health_at(1120)
        self.assertIn('health_reason=media-timeout',result.stdout)
        self.assertEqual(result.returncode,1)
        self.assertEqual(guard.read_text(),prior)
    def test_gui_gap_resets_window_and_a_persistent_gap_fails(self):
        guard=self.cfg/'startup.pending'
        self.write(guard,f'phase=idle_ready\nboot_id={BOOT}\n')
        self.health_at(1000)
        self.write(self.base/'no-gui','1')
        self.assertEqual(self.health_at(1119).returncode,0)
        (self.base/'no-gui').unlink()
        self.assertEqual(self.health_at(1121).returncode,0)
        self.assertEqual((self.run/'release-health/since').read_text(),'1121\n')
        self.assertTrue(guard.exists())

        self.write(self.base/'no-gui','1')
        self.assertEqual(self.health_at(1123).returncode,0)
        self.assertEqual(self.health_at(1132).returncode,0)
        result=self.health_at(1133)
        self.assertIn('health_reason=gui-timeout',result.stdout)
        self.assertEqual(result.returncode,1)
        self.assertTrue(guard.exists())
    def test_wrong_gui_owner_fails_immediately(self):
        self.script(self.run/'face-status','echo native_pid=99\n')
        result=self.health_at(1000)
        self.assertIn('health_reason=gui-owner',result.stdout)
        self.assertEqual(result.returncode,1)
    def supervisor_polls(self,between_polls):
        from tools.rc06_build import runtime
        raw=runtime('synthetic').decode()
        loop=raw[raw.index(' # Media preparation runs beside this loop'):raw.index(' read -r NET <"$R/state" || NET=')]
        state=raw[raw.index('HEALTH_PASSES=0\n'):raw.index('MISSES=0\n# The loop waits')]
        self.script(self.run/'rc-media-worker.sh','echo media-worker >>"$TEST_CALLS"\n')
        health=self.relocated('health.sh')
        (self.run/'rc-health.sh').write_bytes(health.read_bytes())
        script=self.base/'poll.sh'
        # The worker now runs in the background; each poll reaps it before the next step.
        self.write(script,f'R="{self.run}"\nGUARD="{self.cfg}/startup.pending"\nHEALTH_FAULT=0\nMEDIA_PID=\nMEDIA_TICK=\n{state}\npoll() {{\n{loop} wait\n}}\n{between_polls}')
        return subprocess.run([*self.SH,str(script)],env=self.env,capture_output=True,text=True,timeout=3)
    def test_supervisor_preserves_media_guard_and_persists_first_fault_only(self):
        guard=self.cfg/'startup.pending'
        media=self.run/'media-prepare'
        fault=self.cfg/'runtime-health-fault'
        prior=f'phase=media_prepare\nboot_id={BOOT}\n'
        self.write(guard,prior)
        self.job()
        self.write(media/'state','initializing\n')
        result=self.supervisor_polls(f'''poll
[ ! -e "{fault}" ] || exit 2
printf 'requires-recovery\n' >"{media/'state'}"
printf 'native-not-ready\n' >"{media/'reason'}"
poll
printf 'cancelled\n' >"{media/'state'}"
poll
''')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(guard.read_text(),prior)
        self.assertEqual(self.calls().count('media-worker'),2)
        self.assertIn('reason=media-requires-recovery\nmedia_reason=native-not-ready\n',fault.read_text())
        self.assertIn(f'boot_id={BOOT}\nrelease_id=local-test\n',fault.read_text())
        self.assertEqual(fault.stat().st_mode & 0o777,0o600)
    def test_well_formed_health_cannot_clear_unexpected_guard(self):
        guard=self.cfg/'startup.pending'
        for phase,boot in [('runtime_health_fault',BOOT),('media_prepare',BOOT),('idle_ready','wrong-boot')]:
            with self.subTest(phase=phase,boot=boot):
                prior=f'phase={phase}\nboot_id={boot}\n'
                self.write(guard,prior)
                self.assertEqual(self.health_at(1000).returncode,1)
                self.assertEqual(self.health_at(1121).returncode,1)
                self.assertEqual(guard.read_text(),prior)
    def test_health_rearm_is_explicit_internal_only_and_never_writes_the_card(self):
        from tools.rc06_build import helper
        raw=helper('synthetic')
        # Functions only; variables are relocated before sourcing by health.
        hp=raw.decode().replace('/etc/cammy',str(self.cfg))
        self.write(self.card/'boot-helper.sh',hp)
        att='a'*32;state=self.cfg/'native-select';state.mkdir()
        (state/('claimed-'+att)).write_bytes(R.claim_record(att,BOOT,'b'*32))
        arm=self.card.parent/'native/arm';arm.parent.mkdir();arm.write_bytes(R.arm_record(att,'b'*32,123,'gui-face-service-v1','c'*64,'d'*32,456))
        self.write(self.cfg/'native-select.resident','format=cammy-resident/1\nrelease_id=local-test\n')
        card_before=self.card_files()
        self.execute('health.sh');self.write(self.proc/'uptime','1121.0 0\n')
        result=self.execute('health.sh');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.card_files(),card_before)
        self.assertFalse((state/('claimed-'+att)).exists())
        self.assertEqual((self.cfg/'native-release-history'/f'claimed-{att}.healthy-{BOOT}').read_bytes(),R.claim_record(att,BOOT,'b'*32))
        self.assertIn(f'next_attempt={att}\n',(self.run/'release-health/committed').read_text())
        rearmed=arm.read_text()
        calls=self.calls()
        self.assertEqual(self.health_at(1123).returncode,0)
        self.assertEqual(arm.read_text(),rearmed)
        self.assertEqual(self.calls(),calls)
        # A portal failure after rearm must still quarantine the next boot.
        result=self.supervisor_polls(f'''poll
printf 'wrong-process\n' >"{self.proc/'88/comm'}"
poll
printf 'cammy-portal\n' >"{self.proc/'88/comm'}"
poll
''')
        self.assertEqual(result.returncode,0,result.stderr)
        guard=self.cfg/'startup.pending'
        self.assertEqual(guard.read_text(),f'phase=runtime_health_fault\nboot_id={BOOT}\n')
        self.assertIn('reason=portal-owner\n',(self.cfg/'runtime-health-fault').read_text())
        self.assertIn('rearmed=yes\n',(self.cfg/'runtime-health-fault').read_text())
        self.assertEqual(self.health_at(1244).returncode,1)
        self.assertTrue(guard.exists())
        self.assertEqual(arm.read_text(),rearmed)
    def test_prior_runtime_fault_selects_stock_before_card_or_state_writes(self):
        from tools.rc06_build import helper,GATE
        from tools.boot_launch.model import BootLaunchFakeTree,relocate_helper_for_test
        tree=BootLaunchFakeTree.build(self.base/'selector',enabled_content='mode=select\n')
        tree.path('/etc/cammy/startup.pending').write_text('phase=old-fault\n')
        text=relocate_helper_for_test(helper('synthetic').decode(),tree,select_gate=GATE)
        script=tree.ctl/'rc-helper.sh';script.write_text(text+'\ncammy_prepare_launch\ncammy_launch_selected_or_stock\nwait\n')
        result=subprocess.run(['dash',str(script)],env={'PATH':str(tree.ctl/'bin')+':/usr/bin:/bin','LC_ALL':'C'},cwd=tree.root,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('previous-runtime-fault',result.stdout)
        snap=tree.snapshot();self.assertEqual(snap['violations'],[])
        self.assertEqual(snap['state_files'],{});self.assertEqual(snap['launched_entries'],['sdvcam-stub'])
        self.assertFalse(any(call.startswith('mount ') for call in tree.calls()))
    def card_files(self):
        return {str(p.relative_to(self.card.parent)):p.read_bytes() for p in self.card.parent.rglob('*') if p.is_file()}
    def test_rearm_fault_keeps_claim_and_card(self):
        from tools.rc06_build import helper
        hp=helper('synthetic').decode().replace('/etc/cammy',str(self.cfg))
        self.write(self.card/'boot-helper.sh',hp)
        att='a'*32;state=self.cfg/'native-select';state.mkdir()
        claim=state/('claimed-'+att);claim.write_bytes(R.claim_record(att,BOOT,'b'*32))
        arm=self.card.parent/'native/arm';arm.parent.mkdir();arm.write_bytes(R.arm_record(att,'b'*32,123,'gui-face-service-v1','c'*64,'d'*32,456))
        self.write(self.cfg/'native-select.resident','format=cammy-resident/1\nrelease_id=local-test\n')
        card_before=self.card_files()
        self.execute('health.sh');self.write(self.proc/'uptime','1121.0 0\n')
        self.script(self.base/'bin/mv','case "$1" in *claimed-*) exit 1;; esac\nexec /usr/bin/mv "$@"\n')
        result=self.execute('health.sh');self.assertNotEqual(result.returncode,0)
        self.assertIn('health_reason=claim-retire',result.stdout)
        self.assertTrue(claim.exists());self.assertEqual(self.card_files(),card_before)
        self.assertFalse((self.run/'release-health/committed').exists())
    # Health commit: internal renames only, each synced; power loss at every sync.
    def commit_fixture(self,authority,ledger=True):
        from tools.rc06_build import helper
        self.write(self.card/'boot-helper.sh',helper('synthetic').decode().replace('/etc/cammy',str(self.cfg)))
        self.att='a'*32;self.state=self.cfg/'native-select';self.state.mkdir()
        self.claim=self.state/('claimed-'+self.att);self.claim.write_bytes(R.claim_record(self.att,BOOT,'b'*32))
        arm=self.card.parent/'native/arm';arm.parent.mkdir();arm.write_bytes(R.arm_record(self.att,'b'*32,123,'gui-face-service-v1','c'*64,'d'*32,456))
        self.write(self.cfg/'startup.pending',f'phase=idle_ready\nboot_id={BOOT}\n')
        self.once=self.cfg/'native-select.advance'
        if authority=='resident':self.write(self.cfg/'native-select.resident','format=cammy-resident/1\nrelease_id=local-test\n')
        else:self.write(self.once,f'format=cammy-advance/1\nrelease_id=local-test\nboot={BOOT}\n')
        self.ledger=self.cfg/'native-select.retry-used'
        if ledger:self.write(self.ledger,f'format=cammy-power-retry-used/1\nattempt={self.att}\nfailed_boot=00000000-0000-0000-0000-000000000000\nretry_boot={BOOT}\n')
        self.hist=self.cfg/'native-release-history'
        self.assertEqual(self.execute('health.sh').returncode,0)
        self.write(self.proc/'uptime','1121.0 0\n')
    def test_release_bound_commit_preserves_reset_authority_at_each_sync(self):
        for cut in (1, 2, 3):
            with self.subTest(cut=cut):
                self.tearDown();self.setUp()
                self.commit_fixture('resident', ledger=False)
                rid, md5 = 'a'*24, 'b'*32
                self.write(self.run/'native-release.conf',
                           f'format=cammy-native-release/1\nrelease_id={rid}\ncandidate_sha256={self.h}\ncandidate_md5={md5}\n')
                self.write(self.cfg/'native-select.resident',f'format=cammy-resident/1\nrelease_id={rid}\n')
                self.write(self.native.parent/'launch.stamp',f'target=candidate\nboot={BOOT}\nmd5={md5}\n')
                guard = self.cfg/'startup.pending'
                self.write(guard, f'phase=idle_ready\nboot_id={BOOT}\nrelease_id={rid}\ncandidate_md5={md5}\n')
                self.script(self.base/'bin/sync',
                            'n=$(cat "$TEST_CALLS" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" >"$TEST_CALLS"\n'
                            '[ "$n" != "$TEST_CRASH_AT" ] || kill -9 "$PPID"\n')
                self.env['TEST_CRASH_AT'] = str(cut)
                result = self.execute('health.sh')
                self.assertEqual(result.returncode, -9 if cut < 3 else 0, result.stdout)
                # The synced claim rename always precedes guard removal.
                self.assertTrue((self.hist/f'claimed-{self.att}.healthy-{BOOT}').is_file())
                self.assertFalse(self.claim.exists())
                self.assertEqual(guard.exists(), cut == 1)

    def test_resident_commit_retires_claim_and_ledger(self):
        self.commit_fixture('resident');card=self.card_files()
        result=self.execute('health.sh');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(self.claim.exists());self.assertFalse(self.ledger.exists())
        self.assertTrue((self.hist/f'claimed-{self.att}.healthy-{BOOT}').exists())
        self.assertTrue((self.hist/f'native-select.retry-used.healthy-{BOOT}').exists())
        self.assertFalse((self.cfg/'startup.pending').exists());self.assertEqual(self.card_files(),card)
    def test_malformed_ledger_is_kept_and_keeps_retry_disabled(self):
        self.commit_fixture('resident',ledger=False);self.write(self.ledger,'format=cammy-power-retry-used/1\n')
        result=self.execute('health.sh');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(self.claim.exists());self.assertEqual(self.ledger.read_text(),'format=cammy-power-retry-used/1\n')
    def test_advance_is_spent_before_the_claim_is_retired(self):
        self.commit_fixture('advance',ledger=False)
        self.script(self.base/'bin/mv','case "$1" in *claimed-*) exit 1;; esac\nexec /usr/bin/mv "$@"\n')
        result=self.execute('health.sh');self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.once.exists());self.assertTrue(self.claim.exists())
    def test_stale_advance_from_another_boot_never_retires_a_claim(self):
        self.commit_fixture('advance',ledger=False)
        self.write(self.once,'format=cammy-advance/1\nrelease_id=local-test\nboot=00000000-0000-0000-0000-000000000000\n')
        self.assertNotEqual(self.execute('health.sh').returncode,0);self.assertTrue(self.claim.exists())
    def test_power_loss_at_each_commit_sync(self):
        crash=('n=$(cat "$TEST_SYNC_COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" >"$TEST_SYNC_COUNT"\n'
               '[ "$n" != "$TEST_CRASH_AT" ] || kill -9 "$PPID"\n')
        for authority in ['resident','advance']:
            for n in range(1,10):
                with self.subTest(authority=authority,sync=n):
                    self.tearDown();self.setUp();self.commit_fixture(authority);card=self.card_files()
                    self.script(self.base/'bin/sync',crash)
                    self.env.update(TEST_SYNC_COUNT=str(self.base/'sync-count'),TEST_CRASH_AT=str(n))
                    result=self.execute('health.sh')
                    retired=not self.claim.exists()
                    self.assertEqual(retired,(self.hist/f'claimed-{self.att}.healthy-{BOOT}').exists())
                    self.assertEqual(self.card_files(),card)
                    if retired:
                        self.assertFalse((self.cfg/'startup.pending').exists())
                        self.assertFalse(self.once.exists())
                    if not self.ledger.exists():self.assertTrue(retired)
                    if result.returncode==0:break
                    self.assertEqual(result.returncode,-9);self.assertLess(n,10)
            # Flag removal, [advance spent], claim retired, ledger retired: one crash point each.
            self.assertEqual(n,{'resident':4,'advance':5}[authority])
    def test_full_history_eviction_and_health_commit_at_every_sync(self):
        crash = ('n=$(cat "$TEST_SYNC_COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" >"$TEST_SYNC_COUNT"\n'
                 '[ "$n" != "$TEST_CRASH_AT" ] || kill -9 "$PPID"\n')
        for cut in range(1, 7):
            with self.subTest(sync=cut):
                self.tearDown()
                self.setUp()
                self.commit_fixture('resident')
                self.hist.mkdir()
                entries = []
                for n in range(4):
                    entry = self.hist / f'claimed-{n:032x}.healthy-{BOOT}'
                    entry.write_text('previous evidence\n')
                    os.utime(entry, (n + 10, n + 10))
                    entries.append(entry)
                self.script(self.base/'bin/sync', crash)
                self.env.update(TEST_SYNC_COUNT=str(self.base/'sync-count'), TEST_CRASH_AT=str(cut))
                result = self.execute('health.sh')
                self.assertEqual(result.returncode, -9 if cut <= 5 else 0)
                self.assertEqual(entries[0].exists(), cut < 2)
                self.assertEqual(entries[1].exists(), cut < 3)
                self.assertTrue(all(p.exists() for p in entries[2:]))
                self.assertEqual(self.claim.exists(), cut < 4)
                self.assertEqual(self.ledger.exists(), cut < 5)
                self.assertLessEqual(len(list(self.hist.iterdir())), 4)
                if cut <= 3:
                    self.env['TEST_CRASH_AT'] = '99'
                    self.assertEqual(self.execute('health.sh').returncode, 0)
                    self.assertFalse(self.claim.exists())
                    self.assertFalse(self.ledger.exists())
                    self.assertEqual(len(list(self.hist.iterdir())), 4)

    # Device applet set: the camera has no rmdir or timeout (stock BusyBox 1.27.2).
    def device_path(self):
        names=stock_applets()
        if not names:self.skipTest('stock rootfs applet list unavailable')
        self.assertNotIn('rmdir',names)
        dev=self.base/'device-bin';dev.mkdir()
        for name in sorted(names):
            stub=self.base/'bin'/name
            if stub.exists():shutil.copy2(stub,dev/name)
            elif shutil.which(name,path='/usr/bin:/bin'):(dev/name).symlink_to(shutil.which(name,path='/usr/bin:/bin'))
        self.env['PATH']=str(dev)
        self.assertIsNone(shutil.which('rmdir',path=self.env['PATH']))
    def refusing_native(self,state):
        self.script(self.run/'native-media-start.sh','echo native-media >>"$TEST_CALLS"\necho '+state+' >"'+str(self.run/'native-video.state')+'"\nexit 1\n')
    def test_refusal_cleans_up_with_device_applets_and_a_second_request_runs(self):
        self.device_path();d=self.run/'media-prepare';guard=self.cfg/'startup.pending'
        prior=f'phase=idle_ready\nboot_id={BOOT}\n';self.write(guard,prior)
        for refusal in ['refused-native-mode','skipped-network','refused-deadline']:
            with self.subTest(refusal=refusal):
                self.job();self.refusing_native(refusal)
                result=self.execute('media-worker.sh');self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((d/'state').read_text(),'refused\n');self.assertEqual((d/'reason').read_text(),f'native-{refusal}\n')
                self.assertFalse((d/'work.lock').exists());self.assertFalse((d/'request').exists())
                self.assertEqual(guard.read_text(),prior)
                self.assertEqual(self.health_at(1000).returncode,0)
                (d/'state').unlink()  # what the portal does before accepting the next request
        # The next request is not blocked by a stale lock and runs the native step again.
        self.job();self.script(self.run/'native-media-start.sh','echo native-media >>"$TEST_CALLS"\necho sdp-ready-idle >"'+str(self.run/'native-video.state')+'"\n')
        result=self.execute('media-worker.sh');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((d/'state').read_text(),'ready\n');self.assertEqual(self.calls().count('native-media'),4)
        self.assertEqual(guard.read_text(),prior)
    def test_worker_deadline_and_cancel_outcomes_fail_closed_after_native_start(self):
        guard=self.cfg/'startup.pending';d=self.run/'media-prepare'
        for name,body,reason in [
            ('deadline-in-native','echo deadline-exceeded >"{state}"\nexit 1\n','deadline-exceeded'),
            ('cancel-in-native','echo cancel >"{cancel}"\necho cancelled >"{state}"\nexit 1\n','cancelled'),
            ('late-success','printf "1060.0 0\\n" >"{uptime}"\necho sdp-ready-idle >"{state}"\n','deadline-exceeded')]:
            with self.subTest(name=name):
                if d.exists():shutil.rmtree(d)
                self.write(self.proc/'uptime','1000.0 0\n');guard.unlink(missing_ok=True);self.job()
                self.script(self.run/'native-media-start.sh',body.format(state=self.run/'native-video.state',cancel=d/'cancel',uptime=self.proc/'uptime'))
                self.assertEqual(self.execute('media-worker.sh').returncode,1)
                self.assertEqual((d/'state').read_text(),'requires-recovery\n');self.assertEqual((d/'reason').read_text(),reason+'\n')
                self.assertEqual(guard.read_text(),f'phase=media_prepare\nboot_id={BOOT}\n')
                self.assertTrue((d/'work.lock').exists())
    def test_worker_passes_deadline_and_cancel_record_to_native_phases(self):
        self.job();self.script(self.run/'native-media-start.sh','echo "env:$CAMMY_PREPARE_DEADLINE_MS:$CAMMY_PREPARE_CANCEL" >>"$TEST_CALLS"\necho sdp-ready-idle >"'+str(self.run/'native-video.state')+'"\n')
        self.assertEqual(self.execute('media-worker.sh').returncode,0)
        self.assertIn(f'env:1050000:{self.run}/media-prepare/cancel\n',self.calls())
    def test_worker_never_overwrites_a_fault_latch_written_meanwhile(self):
        guard=self.cfg/'startup.pending';self.write(guard,f'phase=idle_ready\nboot_id={BOOT}\n');self.job()
        fault=f'phase=runtime_health_fault\nboot_id={BOOT}\n'
        self.script(self.run/'native-media-start.sh',f'printf "{fault.replace(chr(10),"\\\\n")}" >"{guard}"\necho refused-native-mode >"{self.run/"native-video.state"}"\n')
        self.assertEqual(self.execute('media-worker.sh').returncode,1)
        self.assertEqual(guard.read_text(),fault);self.assertEqual((self.run/'media-prepare/reason').read_text(),'guard-changed\n')
    # Like src/boot/native_service_client.c and the portal: a mutating call needs
    # a deadline 1..90 s ahead with at least one whole second left (its lease
    # TTL), and every owned call needs the cammy-boot lease unexpired and not
    # dropped (the portal drops it on its own cancel). Outcomes go to client.log.
    ENFORCE='''now=$(awk '{{printf "%.0f",$1*1000}}' "{uptime}")
L="{b}/lease";LOG="{b}/client.log"
case "$1" in acquire|init|photo|video|start)
 d=${{CAMMY_PREPARE_DEADLINE_MS:-}};ttl=120
 if [ -n "$d" ];then left=$((d-now));ttl=$((left/1000))
  if [ "$left" -le 0 ] || [ "$left" -gt 90000 ] || [ "$ttl" -lt 1 ];then echo "refused-expired $1 now=$now limit=$d" >>"$LOG";exit 1;fi
 fi;;
esac
case "$1" in
 acquire) echo $((now+ttl*1000)) >"$L";echo "acquire lease_end=$(cat "$L")" >>"$LOG";;
 release) rm -f "$L";;
 idle) [ ! -e "$L" ] || exit 1;;
 preflight|init|photo|video|start|mode-photo|mode-video|sdp)
  if [ ! -e "$L" ] || [ -e "{b}/lease-dropped" ] || [ "$now" -ge "$(cat "$L")" ];then echo "unowned $1 now=$now" >>"$LOG";exit 1;fi
  case "$1" in init|photo|video|start) echo "ok $1 now=$now limit=${{CAMMY_PREPARE_DEADLINE_MS:-}} lease_end=$(cat "$L")" >>"$LOG";; esac;;
esac
'''
    # The generated native start script, with the device applet set and stub clients.
    def native_fixture(self):
        from tools.rc06_build import media_script
        self.device_path()
        self.write(self.run/'native-release.conf',f'format=cammy-native-release/1\nrelease_id=local-test\ncandidate_sha256={STOCK_SDVCAM}\n')
        self.script(self.run/'busybox',f'echo "{STOCK_SDVCAM}  file"\n')
        self.write(self.run/'wpa.pid','12\n');self.write(self.run/'dhcp.pid','13\n')
        b=self.base
        self.script(self.run/'native-service-client',f'echo "$1" >>"$TEST_CALLS"\n{self.ENFORCE.format(b=b,uptime=self.proc/"uptime")}[ ! -f "{b}/hook-$1" ] || . "{b}/hook-$1"\n[ ! -e "{b}/fail-$1" ] || exit 1\ncase "$1" in ip) echo 192.0.2.53;; esac\nexit 0\n')
        nv=self.run/'native-video'
        self.script(self.run/'daemon-exec',f'echo daemon >>"$TEST_CALLS"\necho ready >"{nv}/ready"\n[ -e "{b}/no-recovery" ] || echo restored >"{nv}/recovery"\n')
        s=media_script(STOCK_SDVCAM,self.m).decode()
        for prefix in ['/tmp/cammy-runtime','/proc','/run/cammy-select']:s=s.replace(prefix,str(self.base)+prefix)
        # Process identity is covered in test_native_media_boot; isolate the phases here.
        s=s.replace('if [ "${1:-}" = recover ];then','owned(){ return 0; }\nstarttime(){ echo 1; }\nstation(){ return 0; }\nap_helpers(){ return 0; }\nif [ "${1:-}" = recover ];then')
        self.nscript=self.base/'native-start.sh';self.write(self.nscript,s)
        self.env.update(CAMMY_PREPARE_DEADLINE_MS='1050000',CAMMY_PREPARE_CANCEL=str(self.run/'media-prepare/cancel'))
    def native_start(self):
        started=time.monotonic()
        r=subprocess.run([*self.SH,str(self.nscript),'start'],env=self.env,capture_output=True,text=True,timeout=5)
        self.elapsed=time.monotonic()-started
        return r,(self.run/'native-video.state').read_text().strip(),[c for c in self.calls().splitlines() if c not in ('ip','idle','mode-photo','mode-video','sdp')]
    def test_native_success_then_one_attempt_per_boot(self):
        self.native_fixture()
        r,state,calls=self.native_start();self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(state,'sdp-ready-idle')
        self.assertEqual(calls,['acquire','preflight','daemon','init','photo','video','start','release'])
        r,state,again=self.native_start();self.assertEqual(r.returncode,0);self.assertEqual(again,calls)
    def test_native_refusal_removes_namespace_and_a_retry_runs(self):
        self.native_fixture();(self.base/'fail-preflight').touch()
        r,state,calls=self.native_start();self.assertEqual(state,'refused-native-mode')
        self.assertEqual(calls,['acquire','preflight','release']);self.assertFalse((self.run/'native-video').exists())
        (self.base/'fail-preflight').unlink();(self.base/'calls').unlink()
        r,state,calls=self.native_start();self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(state,'sdp-ready-idle')
        self.assertEqual(calls.count('init'),1)
    def test_native_deadline_before_guard_is_a_clean_refusal(self):
        self.native_fixture();self.write(self.base/'hook-preflight',f'printf "1050.0 0\\n" >"{self.proc}/uptime"\n')
        r,state,calls=self.native_start();self.assertEqual(state,'refused-deadline')
        self.assertEqual(calls,['acquire','preflight','release']);self.assertFalse((self.run/'native-video').exists())
    def test_native_deadline_and_cancel_stop_every_later_phase_promptly(self):
        uptime=lambda:f'printf "1050.0 0\\n" >"{self.proc}/uptime"\n'
        reload_limit=lambda:f'printf "1030.0 0\\n" >"{self.proc}/uptime"\n'
        cancel=lambda:f': >"{self.run}/media-prepare/cancel"\n'
        cases=[('init',uptime,True,'deadline-exceeded',['acquire','preflight','daemon','init','release']),
               ('init',cancel,True,'cancelled',['acquire','preflight','daemon','init','release']),
               ('photo',reload_limit,False,'deadline-exceeded',['acquire','preflight','daemon','init','photo','video','release']),
               ('start',cancel,False,'cancelled',['acquire','preflight','daemon','init','photo','video','start','release'])]
        for op,hook,stall,expected,sequence in cases:
            with self.subTest(op=op,expected=expected):
                self.tearDown();self.setUp();self.native_fixture();(self.run/'media-prepare').mkdir()
                self.write(self.base/('hook-'+op),hook())
                if stall:(self.base/'no-recovery').touch()  # recovery never reports: the 30 s wait must not run out
                if op=='start':(self.base/'fail-start').touch()  # event server down: the 30-try loop must stop
                r,state,calls=self.native_start()
                self.assertEqual((r.returncode,state),(1,expected),r.stderr)
                self.assertEqual(calls,sequence);self.assertLess(self.elapsed,3)
                self.assertTrue((self.run/'native-video/command.done').exists())
    # Network path: home station or an operating AP fallback.
    def ap_fallback(self,operating=True):
        self.write(self.run/'state','AP_CammyOS\n')
        for name in ['hostapd','udhcpd']:
            if operating:self.write(self.base/('ap-'+name),'1')
    def test_operating_ap_fallback_is_healthy_and_clears_the_startup_flag(self):
        guard=self.cfg/'startup.pending';self.write(guard,f'phase=idle_ready\nboot_id={BOOT}\n');self.ap_fallback()
        self.assertEqual(self.health_at(1000).returncode,0);self.assertTrue(guard.exists())
        self.assertEqual(self.health_at(1121).returncode,0);self.assertFalse(guard.exists())
        self.assertIn('network=AP_CammyOS\n',(self.run/'release-health/healthy').read_text())
        self.assertFalse((self.cfg/'runtime-health-fault').exists())
    def test_ap_without_running_servers_defers_without_fault_or_clearing(self):
        guard=self.cfg/'startup.pending';prior=f'phase=idle_ready\nboot_id={BOOT}\n';self.write(guard,prior)
        self.ap_fallback(operating=False)
        for t in [1000,1121,1300]:self.assertEqual(self.health_at(t).returncode,0)
        self.assertEqual(guard.read_text(),prior);self.assertFalse((self.run/'release-health/since').exists())
    def test_network_path_change_restarts_the_window(self):
        guard=self.cfg/'startup.pending';self.write(guard,f'phase=idle_ready\nboot_id={BOOT}\n')
        self.health_at(1000);self.ap_fallback()
        self.assertEqual(self.health_at(1100).returncode,0);self.assertEqual(self.health_at(1121).returncode,0)
        self.assertTrue(guard.exists());self.assertEqual(self.health_at(1220).returncode,0);self.assertFalse(guard.exists())
    def test_ap_commit_rearms_but_keeps_the_power_retry_ledger(self):
        self.ap_fallback();self.commit_fixture('resident')
        result=self.execute('health.sh');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(self.claim.exists());self.assertFalse((self.cfg/'startup.pending').exists())
        self.assertTrue(self.ledger.exists());self.assertFalse((self.hist/f'native-select.retry-used.healthy-{BOOT}').exists())
        self.assertIn('network=AP_CammyOS\n',(self.run/'release-health/committed').read_text())
    def test_terminal_gui_and_media_faults_still_fail_on_ap_fallback(self):
        guard=self.cfg/'startup.pending';prior=f'phase=idle_ready\nboot_id={BOOT}\n'
        for fault in ['gui','media']:
            with self.subTest(fault=fault):
                self.tearDown();self.setUp();self.write(guard,prior);self.ap_fallback()
                self.health_at(1000)
                if fault=='gui':
                    self.write(self.base/'no-gui','1');self.health_at(1001);result=self.health_at(1011);reason='gui-timeout'
                else:
                    self.write(self.run/'media-prepare/state','requires-recovery\n');result=self.health_at(1001);reason='media-requires-recovery'
                self.assertEqual(result.returncode,1);self.assertIn('health_reason='+reason,result.stdout)
                self.assertEqual(guard.read_text(),prior)
    def test_refusal_in_progress_waits_and_each_request_gets_its_own_bound(self):
        d=self.run/'media-prepare';self.job();self.write(d/'state','refused\n')
        self.assertEqual(self.health_at(1000).returncode,0)
        self.assertEqual(self.health_at(1110).returncode,0)
        # A successor request (new deadline) restarts the bound; a stuck one still times out.
        self.write(d/'request',f'format=cammy-media-prepare/1\nboot={BOOT.replace("-","")}\ndeadline_ms=1200000\n');self.write(d/'state','initializing\n')
        self.assertEqual(self.health_at(1200).returncode,0);self.assertEqual(self.health_at(1319).returncode,0)
        result=self.health_at(1320);self.assertEqual(result.returncode,1);self.assertIn('health_reason=media-timeout',result.stdout)
    def test_steady_supervisor_samples_gui_every_third_loop_and_escalates_pid_loss(self):
        from tools.rc06_build import runtime
        raw=runtime('synthetic').decode()
        body=raw[raw.index('HEALTH_PASSES=0\n'):].replace(
            'while true; do', 'N=0\nwhile [ "$N" -lt 13 ]; do\n N=$((N+1))',1)
        for prefix in ('/etc/cammy','/proc','/run/cammy-select'):
            body=body.replace(prefix,str(self.base)+prefix)
        self.write(self.run/'release-health/committed','yes\n')
        self.write(self.run/'release-health/owner','77 123\n')
        self.write(self.run/'release-health/network','HOME_STATION\n')
        self.script(self.run/'face-status','echo face >>"$TEST_CALLS"\necho native_pid=77\n')
        self.script(self.run/'fault-evidence','[ "$1" != wpa ] || echo wpa >>"$TEST_CALLS"\n')
        self.script(self.run/'rc-health.sh','echo health >>"$TEST_CALLS"\n')
        script=self.base/'steady.sh'
        self.write(script,f'R="{self.run}"\nGUARD="{self.cfg}/startup.pending"\n'
            'UI_STARTS=5\nHEALTH_FAULT=0\nMEDIA_PID=\nMEDIA_TICK=\n'
            f'sleep() {{ [ "$N" -ne 13 ] || rm -f "{self.proc}/77/stat"; }}\n{body}')
        result=subprocess.run([*self.SH,str(script)],env=self.env,capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.calls().splitlines(),['face','face','wpa','face','face','wpa','health'])

    # The supervisor keeps its menu, health and Wi-Fi watches while a worker runs.
    def test_supervisor_stays_responsive_and_defers_ap_fallback_during_preparation(self):
        from tools.rc06_build import runtime
        raw=runtime('synthetic').decode();body=raw[raw.index('HEALTH_PASSES=0\n'):]
        body=body.replace('while true; do','N=0\nwhile [ "$N" -lt 30 ]; do\n N=$((N+1))',1)
        calls=self.base/'calls';release=self.base/'release';wpid=self.base/'worker.pid'
        self.job()
        self.script(self.run/'rc-media-worker.sh',f'echo $$ >"{wpid}"\necho worker-start >>"{calls}"\nmkdir "{self.run}/media-prepare/work.lock"\ni=0\nwhile [ ! -e "{release}" ] && [ "$i" -lt 500 ];do /usr/bin/sleep 0.01;i=$((i+1));done\necho worker-end >>"{calls}"\n')
        self.script(self.run/'rc-health.sh',f'echo health >>"{calls}"\n')
        self.script(self.run/'fault-evidence','[ "$1" != wpa ]\n')
        # Each loop "sleep": release the worker at the 25th and wait until it has exited.
        self.script(self.base/'bin/sleep',f'n=$(cat "{self.base}/n" 2>/dev/null || echo 0);n=$((n+1));echo $n >"{self.base}/n"\n'
            f'if [ $n = 25 ];then : >"{release}";w=$(cat "{wpid}");i=0\n'
            f' while [ -e /proc/$w ] && [ "$(awk \'{{print $3}}\' /proc/$w/stat 2>/dev/null)" != Z ] && [ $i -lt 300 ];do /usr/bin/sleep 0.01;i=$((i+1));done\nfi\n')
        script=self.base/'loop.sh'
        self.write(script,f'R="{self.run}"\nGUARD="{self.cfg}/startup.pending"\nUI_STARTS=1\nHEALTH_FAULT=0\nMEDIA_PID=\nMEDIA_TICK=\n'
            f'start_ui(){{ echo ui >>"{calls}"; }}\nap_fallback(){{ echo ap-fallback >>"{calls}";echo AP_CammyOS >"$R/state"; }}\n{body}')
        result=subprocess.run([*self.SH,str(script)],env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        log=self.calls().splitlines()
        self.assertEqual(log.count('worker-start'),1);self.assertEqual(log.count('health'),30)
        during=log[:log.index('worker-end')]
        self.assertEqual(during.count('ui'),4);self.assertEqual(during.count('health'),24)
        self.assertNotIn('ap-fallback',during)
        self.assertEqual(log.count('ap-fallback'),1)
        # The first loop after the worker exits: health, then the deferred fallback.
        self.assertEqual(log[log.index('worker-end')+1:log.index('worker-end')+3],['health','ap-fallback'])
    # Mode restoration after a stop: the enforcing client refuses an expired
    # deadline or a lost lease, so only time reserved inside the lease works.
    def client_log(self):
        p=self.base/'client.log';return p.read_text() if p.exists() else ''
    def no_op_sleep(self):
        s=self.base/'device-bin/sleep';s.unlink(missing_ok=True);self.script(s,'exit 0\n')
    def test_restoration_runs_inside_the_lease_it_was_acquired_with(self):
        self.native_fixture();(self.run/'media-prepare').mkdir()
        self.write(self.base/'hook-mode-photo',f'printf "1030.0 0\\n" >"{self.proc}/uptime"\n')
        r,state,calls=self.native_start()
        self.assertEqual((r.returncode,state),(1,'deadline-exceeded'),r.stderr)
        self.assertEqual(calls,['acquire','preflight','daemon','init','photo','video','release'])
        log=self.client_log()
        self.assertIn('acquire lease_end=1050000\n',log)
        self.assertIn('ok photo now=1000000 limit=1025000 lease_end=1050000\n',log)
        self.assertIn('ok video now=1030000 limit=1048000 lease_end=1050000\n',log)
        self.assertNotIn('refused',log);self.assertNotIn('unowned',log)
        self.assertEqual(calls.count('acquire'),1)
    def test_unprovable_restoration_is_an_explicit_terminal_state(self):
        # (hook time, extra setup, state, video attempted, client.log refusal)
        cases=[('1049.0',None,'mode-restore-expired',False,None),
               ('1047.5',None,'mode-restore-failed',True,'refused-expired video now=1047500 limit=1048000'),
               ('1030.0','lease-dropped','mode-restore-failed',True,'unowned video now=1030000'),
               ('1030.0','fail-mode-video','mode-restore-unconfirmed',True,None)]
        for at,extra,expected,video,refusal in cases:
            with self.subTest(at=at,extra=extra):
                self.tearDown();self.setUp();self.native_fixture();self.no_op_sleep();(self.run/'media-prepare').mkdir()
                hook=f'printf "{at} 0\\n" >"{self.proc}/uptime"\n'
                if extra:hook+=f': >"{self.base/extra}"\n'
                self.write(self.base/'hook-mode-photo',hook)
                r,state,calls=self.native_start()
                self.assertEqual((r.returncode,state),(1,expected),r.stderr)
                self.assertEqual(calls.count('video'),1 if video else 0)
                self.assertEqual(calls.count('acquire'),1);self.assertEqual(calls[-1],'release')
                if expected!='mode-restore-unconfirmed':self.assertNotIn('ok video',self.client_log())
                if refusal:self.assertIn(refusal,self.client_log())
    def test_cancel_in_the_reload_restores_under_the_held_lease_once(self):
        self.native_fixture();(self.run/'media-prepare').mkdir()
        self.write(self.base/'hook-mode-photo',f': >"{self.run}/media-prepare/cancel"\n')
        r,state,calls=self.native_start()
        self.assertEqual((r.returncode,state),(1,'cancelled'),r.stderr)
        self.assertEqual(calls,['acquire','preflight','daemon','init','photo','video','release'])
        self.assertIn('ok video now=1000000 limit=1048000',self.client_log())
    def test_a_failed_normal_restoration_is_not_retried_after_the_lease(self):
        self.native_fixture();self.no_op_sleep();(self.base/'fail-video').touch()
        r,state,calls=self.native_start()
        self.assertEqual((r.returncode,state),(1,'mode-restore-failed'),r.stderr)
        self.assertEqual(calls,['acquire','preflight','daemon','init','photo','video','release'])
    def test_reload_needs_the_reserve_ahead(self):
        # Recovery finishes 24 s before the deadline: no mode change is started.
        self.native_fixture();self.write(self.base/'hook-init',f'printf "1026.0 0\\n" >"{self.proc}/uptime"\n')
        r,state,calls=self.native_start()
        self.assertEqual((r.returncode,state),(1,'deadline-exceeded'),r.stderr)
        self.assertEqual(calls,['acquire','preflight','daemon','init','release'])
    def test_worker_reports_unproved_restoration_and_keeps_the_latch(self):
        guard=self.cfg/'startup.pending';d=self.run/'media-prepare'
        for nv in ['mode-restore-expired','mode-restore-failed','mode-restore-unconfirmed']:
            with self.subTest(nv=nv):
                if d.exists():shutil.rmtree(d)
                guard.unlink(missing_ok=True);self.job()
                # Stopped by a cancel, then the restoration could not be proved.
                self.script(self.run/'native-media-start.sh',f': >"{d/"cancel"}"\necho {nv} >"{self.run/"native-video.state"}"\nexit 1\n')
                self.assertEqual(self.execute('media-worker.sh').returncode,1)
                self.assertEqual((d/'state').read_text(),'requires-recovery\n')
                self.assertEqual((d/'reason').read_text(),f'native-{nv}\n')
                self.assertEqual(guard.read_text(),f'phase=media_prepare\nboot_id={BOOT}\n')
    def test_worker_never_writes_the_latch_past_its_cutoff(self):
        guard=self.cfg/'startup.pending';prior=f'phase=idle_ready\nboot_id={BOOT}\n';self.write(guard,prior);self.job()
        # A refusal reported 46 s after the 1050 s deadline: health's latch may
        # already have read this flag, so the worker leaves its media latch.
        self.script(self.run/'native-media-start.sh',f'printf "1096.0 0\\n" >"{self.proc/"uptime"}"\necho refused-native-mode >"{self.run/"native-video.state"}"\n')
        self.assertEqual(self.execute('media-worker.sh').returncode,1)
        self.assertEqual((self.run/'media-prepare/reason').read_text(),'guard-restore-late\n')
        self.assertEqual(guard.read_text(),f'phase=media_prepare\nboot_id={BOOT}\n')
        self.assertFalse(list(self.cfg.glob('.media-guard.*')))

    # Health beside a running worker: no comparison of its records mid-transaction.
    def health_with(self,uptime,worker,*args):
        self.write(self.proc/'uptime',f'{uptime}.0 0\n')
        env=dict(self.env,CAMMY_MEDIA_WORKER=worker)
        return subprocess.run([*self.SH,str(self.relocated('health.sh')),*args],env=env,capture_output=True,text=True,timeout=3)
    TORN=[('media','ready',True,'media-guard-not-restored'),     # guard restored after health read it
          ('idle','initializing',False,'media-request-missing'),   # refusal removed the request
          ('media','refused',False,'media-guard-not-restored'),
          ('absent','queued',False,'media-request-missing')]
    def torn(self,guard,state,request):
        g=self.cfg/'startup.pending';g.unlink(missing_ok=True)
        if guard!='absent':self.write(g,f'phase={"media_prepare" if guard=="media" else "idle_ready"}\nboot_id={BOOT}\n')
        d=self.run/'media-prepare'
        if d.exists():shutil.rmtree(d)
        self.write(d/'state',state+'\n')
        if request:self.job()
        return g,(g.read_text() if g.exists() else None)
    def test_torn_worker_records_defer_while_it_runs_and_fail_once_it_has_exited(self):
        for guard,state,request,reason in self.TORN:
            with self.subTest(guard=guard,state=state,request=request):
                self.tearDown();self.setUp()
                self.health_at(800)  # a healthy window that would otherwise complete
                g,before=self.torn(guard,state,request)
                r=self.health_with(1000,'4242:99')
                self.assertEqual(r.returncode,0,r.stdout);self.assertNotIn('health_reason',r.stdout)
                self.assertEqual(g.read_text() if g.exists() else None,before)
                self.assertFalse((self.run/'release-health/healthy').exists())
                self.assertFalse((self.run/'release-health/since').exists())
                r=self.health_at(1001)
                self.assertEqual(r.returncode,1);self.assertIn('health_reason='+reason,r.stdout)
    def test_running_worker_keeps_identity_and_terminal_checks(self):
        cases=[('requires-recovery',None,'media-requires-recovery'),('cancelled',None,'media-cancelled'),
               ('initializing','cancel','media-cancelled'),('garbage',None,'media-invalid-state'),
               ('initializing','owner','native-owner-changed'),('initializing','portal','portal-owner')]
        for state,extra,reason in cases:
            with self.subTest(state=state,extra=extra):
                self.tearDown();self.setUp();self.health_at(1000)
                self.write(self.run/'media-prepare/state',state+'\n');self.job()
                if extra=='cancel':(self.run/'media-prepare/cancel').touch()
                if extra=='owner':self.write(self.proc/'77/stat',' '.join(['77','(sdvcam)','S']+['0']*18+['124'])+'\n')
                if extra=='portal':self.write(self.proc/'88/comm','other\n')
                r=self.health_with(1001,'4242:99')
                self.assertEqual(r.returncode,1);self.assertIn('health_reason='+reason,r.stdout)
        r=self.health_with(1002,'4242')
        self.assertEqual(r.returncode,1);self.assertIn('health_reason=media-worker-identity',r.stdout)
    def test_running_worker_watchdog_is_per_worker_and_independent_of_its_records(self):
        self.job();self.write(self.run/'media-prepare/state','initializing\n')
        self.assertEqual(self.health_with(1000,'4242:99').returncode,0)
        # The worker's own records changing does not restart the bound.
        self.write(self.run/'media-prepare/state','ready\n')
        self.assertEqual(self.health_with(1149,'4242:99').returncode,0)
        r=self.health_with(1150,'4242:99')
        self.assertEqual(r.returncode,1);self.assertIn('health_reason=media-worker-timeout',r.stdout)
        # A different worker (a refused job's successor) is timed on its own.
        self.assertEqual(self.health_with(1150,'4343:120').returncode,0)
    def test_latch_waits_for_a_running_worker_within_its_watchdog(self):
        guard=self.cfg/'startup.pending';fault=self.cfg/'runtime-health-fault'
        self.write(guard,f'phase=idle_ready\nboot_id={BOOT}\n');self.write(self.run/'health.log','health_reason=native-owner-changed\n')
        self.assertEqual(self.health_with(1000,'4242:99').returncode,0)
        r=self.health_with(1100,'4242:99','latch')
        self.assertEqual(r.returncode,3);self.assertIn('latch-deferred-media-worker',r.stdout)
        self.assertEqual(guard.read_text(),f'phase=idle_ready\nboot_id={BOOT}\n');self.assertFalse(fault.exists())
        # Past the watchdog the worker can no longer write the flag: latch now.
        r=self.health_with(1150,'4242:99','latch');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(guard.read_text(),f'phase=runtime_health_fault\nboot_id={BOOT}\n')
        self.assertIn('reason=native-owner-changed\n',fault.read_text())
    # A real worker, the generated supervisor loop and the real health check.
    # Health's pidof lookup comes between its boot-flag read and its media
    # reads; there the worker is let through to the end of its transaction.
    def interleaved_supervisor(self,native_state):
        from tools.rc06_build import runtime
        raw=runtime('synthetic').decode();body=raw[raw.index('HEALTH_PASSES=0\n'):]
        body=body.replace('while true; do','N=0\nwhile [ "$N" -lt 8 ]; do\n N=$((N+1))',1)
        b=self.base;calls=b/'calls';state=self.run/'media-prepare/state'
        for name in ['health.sh','media-worker.sh']:
            (self.run/('rc-'+name)).write_bytes(self.relocated(name).read_bytes())
        self.script(self.run/'native-media-start.sh',f'i=0;while [ ! -e "{b}/gate" ] && [ $i -lt 500 ];do /usr/bin/sleep 0.01;i=$((i+1));done\necho {native_state} >"{self.run/"native-video.state"}"\n')
        self.script(b/'bin/pidof',f'case "$1" in sdvcam)\n if [ -e "{b}/interleave" ] && grep -q rc-health /proc/$PPID/cmdline 2>/dev/null;then\n'
            f'  rm -f "{b}/interleave";: >"{b}/gate";echo interleaved >>"{calls}";i=0\n'
            f'  while [ "$(cat "{state}" 2>/dev/null)" != {"ready" if native_state=="sdp-ready-idle" else "refused"} ] && [ $i -lt 500 ];do /usr/bin/sleep 0.01;i=$((i+1));done\n'
            f'  i=0;while [ -e "{self.run}/media-prepare/work.lock" ] && [ {"0" if native_state=="sdp-ready-idle" else "1"} = 1 ] && [ $i -lt 500 ];do /usr/bin/sleep 0.01;i=$((i+1));done\n'
            f' fi\n echo 77;;\nesac\n')
        self.script(self.run/'wpa_cli','echo wpa_state=COMPLETED\n')
        # Each loop "sleep": the second arms the interleaving; later ones log.
        self.script(b/'bin/sleep',f'n=$(cat "{b}/n" 2>/dev/null || echo 0);n=$((n+1));echo $n >"{b}/n"\n[ $n != 2 ] || : >"{b}/interleave"\n'
            f'echo "tick $n state=$(cat "{state}" 2>/dev/null) $(sed -n 1p "{self.run}/health.log")" >>"{calls}"\n')
        script=b/'loop.sh'
        self.write(script,f'R="{self.run}"\nGUARD="{self.cfg}/startup.pending"\nUI_STARTS=5\nHEALTH_FAULT=0\nMEDIA_PID=\nMEDIA_TICK=\nap_fallback(){{ echo ap-fallback >>"{calls}"; }}\n{body}\necho "final HEALTH_FAULT=$HEALTH_FAULT"\n')
        return subprocess.run([*self.SH,str(script)],env=self.env,capture_output=True,text=True,timeout=20)
    def test_worker_finishing_inside_a_health_check_never_latches(self):
        for native_state,final in [('sdp-ready-idle','ready'),('refused-native-mode','refused')]:
            with self.subTest(native_state=native_state):
                self.tearDown();self.setUp()
                guard=self.cfg/'startup.pending';prior=f'phase=idle_ready\nboot_id={BOOT}\n';self.write(guard,prior);self.job()
                r=self.interleaved_supervisor(native_state)
                self.assertEqual(r.returncode,0,r.stderr);self.assertIn('final HEALTH_FAULT=0',r.stdout)
                log=self.calls()
                self.assertEqual(log.count('interleaved'),1);self.assertNotIn('health_reason',log)
                self.assertEqual((self.run/'media-prepare/state').read_text(),final+'\n')
                self.assertEqual(guard.read_text(),prior)
                self.assertFalse((self.cfg/'runtime-health-fault').exists())
    # Shutdown: cancellation is published and the exact worker awaited before
    # Wi-Fi and the portal go; nothing is signalled.
    def shutdown(self,worker,sleep_body):
        from tools.rc06_build import runtime
        raw=runtime('synthetic').decode();a=raw.index('cleanup() {\n');fn=raw[a:raw.index('\n}\n',a)+3]
        b=self.base;calls=b/'calls'
        self.script(b/'bin/sleep',sleep_body)
        (self.run/'media-prepare').mkdir(exist_ok=True)
        script=b/'stop.sh'
        self.write(script,f'R="{self.run}"\nSHARED_AUDIO=0\nstop_clients(){{ echo stop-clients >>"{calls}"; }}\n{fn}'
            f'{worker} >/dev/null 2>&1 &\nMEDIA_PID=$!\nMEDIA_TICK=$(awk \'{{print $22}}\' /proc/$MEDIA_PID/stat)\n'
            f'[ "${{SPOOF_TICK:-}}" = "" ] || MEDIA_TICK=$SPOOF_TICK\necho "$MEDIA_PID" >"{b}/worker.pid"\n/usr/bin/sleep 0.1\ncleanup\n')
        return subprocess.run([*self.SH,str(script)],env=self.env,capture_output=True,text=True,timeout=10)
    def test_shutdown_cancels_and_waits_for_the_worker_before_stopping_wifi(self):
        c=self.run/'media-prepare/cancel';calls=self.base/'calls'
        worker=f'(i=0;while [ ! -e "{c}" ] && [ $i -lt 500 ];do /usr/bin/sleep 0.01;i=$((i+1));done;echo worker-saw-cancel >>"{calls}";/usr/bin/sleep 0.2;echo worker-exit >>"{calls}")'
        r=self.shutdown(worker,'/usr/bin/sleep 0.05\n')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.calls().splitlines(),['worker-saw-cancel','worker-exit','stop-clients'])
        self.assertEqual((self.run/'media-shutdown').read_text(),'worker-exited\n')
        self.assertEqual(c.read_text(),'cancel\n')
    def test_shutdown_wait_is_bounded_and_never_signals(self):
        calls=self.base/'calls'
        worker='(trap "" TERM INT HUP;i=0;while [ $i -lt 3000 ];do /usr/bin/sleep 0.01;i=$((i+1));done)'
        r=self.shutdown(worker,f'echo sleep >>"{calls}"\n')
        try:
            self.assertEqual(r.returncode,0,r.stderr)
            log=self.calls().splitlines()
            self.assertEqual(log,['sleep']*45+['stop-clients'])
            self.assertEqual((self.run/'media-shutdown').read_text(),'worker-still-running\n')
            pid=int((self.base/'worker.pid').read_text())
            self.assertTrue(Path(f'/proc/{pid}').exists())  # still running: not signalled
        finally:
            pid=int((self.base/'worker.pid').read_text())
            try:os.kill(pid,9)
            except ProcessLookupError:pass
    def test_shutdown_skips_a_reused_pid_without_waiting(self):
        calls=self.base/'calls'
        self.env['SPOOF_TICK']='1'
        worker='(i=0;while [ $i -lt 300 ];do /usr/bin/sleep 0.01;i=$((i+1));done)'
        r=self.shutdown(worker,f'echo sleep >>"{calls}"\n')
        pid=int((self.base/'worker.pid').read_text())
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.calls().splitlines(),['stop-clients'])
        self.assertEqual((self.run/'media-shutdown').read_text(),'worker-exited\n')
        self.assertTrue(Path(f'/proc/{pid}').exists())
        os.kill(pid,9)
