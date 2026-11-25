"""Relocated shell with fake bind mounts (hardlinks); no real mount/device calls."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from tools.shared_capture_profile import fragment, STOCK_ASOUND, STOCK_GLOBAL

ROOT=Path(__file__).resolve().parents[1]


class SharedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.r=self.root/'run';self.r.mkdir(mode=0o700)
        self.proc=self.root/'proc';self.proc.mkdir()
        for name,data in [('self/status','Uid:\t0\t0\t0\t0\n'),('mounts',''),('asound/card0/pcm0c/sub0/status','closed\n'),('sysvipc/shm','')]:
            p=self.proc/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)
        self.a=self.root/'asound.conf';self.g=self.root/'global.conf'
        self.a.write_text('original local fixture\n');self.g.write_text('original global fixture\n')
        self.original=[self.a.read_bytes(),self.g.read_bytes()]
        binpath=self.root/'bin';binpath.mkdir()
        def script(name,text):
            p=binpath/name;p.write_text(text);p.chmod(0o700)
        script('id','#!/bin/sh\nexit 127\n')
        script('rmdir','#!/bin/sh\nexit 127\n')
        script('ls','#!/bin/sh\nif [ "$1" = -ldn ]; then echo "drwx------ 2 0 0 4096 fixture"; else exec /bin/ls "$@"; fi\n')
        for name in ('mount','umount'):
            script(name,'#!'+sys.executable+'\n'+f'ROOT={str(self.root)!r}\n'+'''
import os,sys,json,hashlib
from pathlib import Path
root=Path(ROOT);table=root/'proc/mounts';state=root/'mount-state.json'
entries=json.loads(state.read_text()) if state.exists() else {}
target=Path(sys.argv[-1]);assert target.is_relative_to(root)
if Path(sys.argv[0]).name=='mount':
 source=Path(sys.argv[-2]);assert source.is_relative_to(root)
 if os.environ.get('FAIL_GLOBAL')=='1' and target.name=='global.conf':raise SystemExit(1)
 backup=root/('backup-'+hashlib.sha256(str(target).encode()).hexdigest())
 os.link(target,backup);target.unlink();os.link(source,target);entries[str(target)]=str(backup)
else:
 if os.environ.get('FAIL_UMOUNT')=='1':raise SystemExit(1)
 backup=Path(entries.pop(str(target)));target.unlink();os.link(backup,target);backup.unlink()
state.write_text(json.dumps(entries));table.write_text(''.join('tmpfs '+t+' tmpfs rw 0 0\\n' for t in entries))
''')
        bb=self.r/'busybox';bb.write_text('#!/bin/sh\nshift\nexec /usr/bin/sha256sum "$@"\n');bb.chmod(0o700)
        text=(ROOT/'scripts/shared-capture.sh').read_text()
        text=text.replace('/tmp/cammy-runtime',str(self.r)).replace('/etc/asound.conf',str(self.a)).replace('/usr/share/alsa/alsa.conf',str(self.g)).replace('/proc/',str(self.proc)+'/')
        text=text.replace(STOCK_ASOUND,hashlib.sha256(self.original[0]).hexdigest()).replace(STOCK_GLOBAL,hashlib.sha256(self.original[1]).hexdigest())
        subprocess.run(['cc','-std=c11',str(ROOT/'src/boot/daemon_exec.c'),'-o',str(self.r/'daemon-exec')],check=True,capture_output=True)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                        f'-DPROC="{self.proc}"',f'-DRUN_DIR="{self.r}"',
                        str(ROOT/'src/boot/fault_evidence.c'),'-o',str(self.r/'fault-evidence')],
                       check=True,capture_output=True)
        self.tool=self.r/'shared-capture.sh';self.tool.write_text(text)
        (self.r/'shared-capture.fragment').write_text(fragment())
        self.env={**os.environ,'PATH':str(binpath)+':/usr/bin:/bin'}

    def call(self,action,*args,**env):
        return subprocess.run(['dash',str(self.tool),action,*args],env={**self.env,**env},capture_output=True,text=True,timeout=5)

    def tearDown(self):
        self.call('stop')
        time.sleep(1.05)  # let the epoch-bound fixture guard see its removed directory
        self.tmp.cleanup()

    def assert_original(self):
        self.assertEqual(self.a.read_bytes(),self.original[0]);self.assertEqual(self.g.read_bytes(),self.original[1])
        self.assertEqual((self.proc/'mounts').read_text(),'')

    def test_start_then_stop_preserves_original_data(self):
        p=self.call('start');self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('cammy_shared',self.a.read_text());self.assertEqual(self.g.read_bytes(),self.original[1])
        self.assertEqual((self.r/'shared-capture.active').read_text(),'m105-shared-v1\n')
        self.assertEqual(self.call('stop').returncode,0);self.assert_original()

    def test_second_bind_failure_restores_first(self):
        self.assertNotEqual(self.call('start',FAIL_GLOBAL='1').returncode,0)
        self.assert_original();self.assertFalse((self.r/'shared-capture.active').exists())

    def test_busy_capture_bad_source_and_existing_ipc_refuse(self):
        status=self.proc/'asound/card0/pcm0c/sub0/status'
        status.write_text('RUNNING\n');self.assertNotEqual(self.call('start').returncode,0)
        status.write_text('closed\n');self.a.write_text('tampered')
        self.assertNotEqual(self.call('start').returncode,0)
        self.a.write_bytes(self.original[0]);(self.proc/'sysvipc/shm').write_text('1129142530 9\n')
        self.assertNotEqual(self.call('start').returncode,0);self.assert_original()

    def test_failed_unmount_keeps_recovery_evidence(self):
        self.assertEqual(self.call('start').returncode,0)
        self.assertNotEqual(self.call('stop',FAIL_UMOUNT='1').returncode,0)
        self.assertEqual((self.r/'shared-capture.state').read_text().strip(),'recovery-required')
        self.assertTrue((self.r/'shared-capture').exists())
        self.assertEqual(self.call('stop').returncode,0);self.assert_original()

    def test_restore_serializes_with_another_restorer(self):
        self.assertEqual(self.call('start').returncode,0)
        lock=self.r/'shared-capture.restore.lock';lock.mkdir()
        self.assertNotEqual(self.call('stop').returncode,0)
        self.assertTrue((self.r/'shared-capture').exists())
        lock.rmdir();self.assertEqual(self.call('stop').returncode,0);self.assert_original()

    def test_stale_guard_cannot_restore_new_epoch(self):
        self.assertEqual(self.call('start').returncode,0)
        self.assertEqual(self.call('watch','not-this-epoch').returncode,0)
        self.assertTrue((self.r/'shared-capture.active').exists())

    def test_guard_restores_on_pid_reuse(self):
        self.assertEqual(self.call('start').returncode,0)
        pid=self.proc/'12345';pid.mkdir();(pid/'comm').write_text('cammy-portal\n')
        def stat(start):return '12345 (cammy-portal) S '+'0 '*18+str(start)+' 0\n'
        (pid/'stat').write_text(stat(100));(self.r/'portal.pid').write_text('12345\n')
        until=time.monotonic()+3
        while not (self.r/'shared-capture/guard.ready').exists() and time.monotonic()<until:time.sleep(.02)
        self.assertTrue((self.r/'shared-capture/guard.ready').exists())
        (pid/'stat').write_text(stat(101))
        until=time.monotonic()+3
        while (self.r/'shared-capture').exists() and time.monotonic()<until:time.sleep(.02)
        self.assertFalse((self.r/'shared-capture.active').exists());self.assert_original()


if __name__=='__main__':unittest.main()
