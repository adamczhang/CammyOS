"""Offline refusal/invariant tests for the RAM-only synthetic-host smoke runner.
No ADB/device/network. Saved-libc execution is a separate `bridge_smoke local` step.
"""
import copy
import secrets
import shutil
import subprocess
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock
import uuid

from tools import bridge_smoke as s

RAM='/tmp/cammy-smoke-'+'a'*24
BOOT='01234567-89ab-cdef-0123-456789abcdef'

def statline(pid=42,start='123',comm='smoke_host'):
    return f'{pid} ({comm}) '+' '.join(['S']+['0']*18+[start]+['0']*5)

def response_events():
    payloads=[struct.pack('<HHHH6I16s',1,0,4,4,64,256,10000,60000,14,8,b'wp2-0.1.0'),
              struct.pack('<4H7Q',2,1,0,1,*([0]*7)),
              struct.pack('<HHI',7,8,0)+b''.join(struct.pack('<HBBB3s',i, int(i==1),0 if i==1 else 3, int(i!=1),b'\0'*3) for i in range(1,8))]
    frames=[]
    for cmd,p in enumerate(payloads,1):
        h=struct.pack('<4sHHHHIHHIQ16sQQ',b'CBRS',1,0,64,cmd,cmd,0,2,len(p),9,uuid.UUID(BOOT).bytes,1000,0)
        frames.append({'event':'response','command':cmd,'hex':(h+p).hex()})
    return frames

def snapshot_raw():
    hashes=dict(s.PINNED)
    hashes.update({p:'a'*64 for p in s.HASH_PATHS if p not in hashes})
    return f'''BOOT={BOOT}
KERNEL=Linux test 4.9.191
CMDLINE=test
--ROOT--
Uid:\t0\t0\t0\t0
Gid:\t0\t0\t0\t0
--MOUNTS--
tmpfs /tmp tmpfs rw,relatime 0 0
/dev/root /rom squashfs ro,relatime 0 0
--TMP--
100 drwxrwxrwt 9 0 0 320 Nov 14 17:00 /tmp
--HASHES--
'''+''.join(f'{h}  {p}\n' for p,h in hashes.items())+f'''--NATIVE--
STAT={statline(778,'166','sdvcam')}
EXE=/usr/bin/sdvcam
WD=/dev/watchdog
WDINFO=pos: 0
flags: 02400001
mnt_id: 14
WDNODE=3178 l-wx------ 1 0 0 64 Nov 14 17:00 /proc/778/fd/3 -> /dev/watchdog
--NATIVE-MAPS--
1000-2000 r-xp 0 1:1 1 /usr/bin/sdvcam
--SELECT--
ABSENT:/run/cammy-select
ABSENT:/data/app_md5
--END--
'''

class ProtocolTests(unittest.TestCase):
    def test_actual_layout_accepts_only_status_native_identity(self):
        self.assertEqual(len(s.validate_responses(response_events(),BOOT)),3)

    def mutate(self,frame,offset,fmt,value):
        events=response_events(); b=bytearray.fromhex(events[frame]['hex']); struct.pack_into(fmt,b,offset,value)
        events[frame]['hex']=b.hex(); return events
    def test_wrong_boot_refused(self):
        with self.assertRaisesRegex(ValueError,'boot mismatch'): s.validate_responses(response_events(),str(uuid.uuid4()))
    def test_headers_identity_and_owner_refused(self):
        for off,fmt,value in [(16,'<H',1),(18,'<H',1),(24,'<Q',10),(56,'<Q',1),(12,'<I',99),(4,'<H',2)]:
            with self.subTest(offset=off),self.assertRaises(ValueError): s.validate_responses(self.mutate(1,off,fmt,value),BOOT)
    def test_hardware_flags_refused(self):
        with self.assertRaisesRegex(ValueError,'hardware'): s.validate_responses(self.mutate(1,70,'<H',3))
    def test_every_physical_capability_clamped(self):
        for i in range(1,7):
            with self.subTest(cap=i+1),self.assertRaisesRegex(ValueError,'physical'):
                s.validate_responses(self.mutate(2,64+8+i*8+2,'<B',1))
    def test_incomplete_and_oversize_frames_refused(self):
        with self.assertRaises(ValueError): s.validate_responses(response_events()[:2])
        frames=response_events();frames[0]['hex']+='00'
        with self.assertRaises(ValueError): s.validate_responses(frames)
    def test_abandoned_or_leaked_state_never_passes(self):
        events=[{'event':'identity','caller':'SYNTHETIC','time_t':4,'stat_size':104,'load_us':1},
                {'event':'map','line':'1000 r-xp '+RAM+'/libcammy_native_status.so'},
                {'event':'attach','rc':0,'us':2}, {'event':'serving','non_cloexec':0,'tasks':2}]+response_events()+[
                {'event':'detach','pass':True,'us':3,'socket_absent':True,'fd_set_equal':True,'fds_before':3,'fds_after':3,'tasks_before':1,'tasks_after':1}]
        s.validate_events(events,RAM+'/libcammy_native_status.so',BOOT)
        for key,value in [('socket_absent',False),('fd_set_equal',False),('fds_after',4),('tasks_after',2),('pass',False)]:
            bad=copy.deepcopy(events);bad[-1][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): s.validate_events(bad,RAM+'/libcammy_native_status.so',BOOT)

class IdentityTests(unittest.TestCase):
    def snapshot(self,raw=None):
        reader=Mock();reader.shell.return_value=(raw or snapshot_raw()).encode();return s.snapshot(reader)
    def test_process_identity_handles_parentheses(self):
        self.assertEqual(s.proc_identity(statline(comm='with (parens)'))['starttime'],'123')
    def test_expected_stock_snapshot(self):
        self.assertEqual(self.snapshot()['native'],{'pid':778,'comm':'sdvcam','starttime':'166'})
    def test_ram_mount_and_ancestor_refusals(self):
        for old,new in [('tmpfs /tmp tmpfs rw','disk /tmp ext4 rw'),('rw,relatime','rw,noexec'),
                        ('--TMP--','tmpfs /tmp/cammy-bridge tmpfs rw 0 0\n--TMP--'),
                        ('100 drwxrwxrwt','100 lrwxrwxrwx')]:
            with self.subTest(value=new),self.assertRaises(ValueError): self.snapshot(snapshot_raw().replace(old,new))
    def test_stock_hash_root_watchdog_and_candidate_refusals(self):
        for old,new in [(s.PINNED['/usr/bin/sdvcam'],'f'*64),('Uid:\t0\t0\t0\t0','Uid:\t1\t1\t1\t1'),
                        ('WD=/dev/watchdog','WD=/dev/null'),('ABSENT:/run/cammy-select','PRESENT:/run/cammy-select'),
                        ('1000-2000 r-xp 0 1:1 1 /usr/bin/sdvcam','1000-2000 r-xp libcammy_native_status.so')]:
            with self.subTest(value=new),self.assertRaises(ValueError): self.snapshot(snapshot_raw().replace(old,new))
    def test_every_preservation_field_checked(self):
        before=self.snapshot()
        for key in ('boot','native','hashes','kernel','cmdline','wd_info','mounts','native_maps','selection'):
            after=copy.deepcopy(before);after[key]='CHANGED'
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,key): s.unchanged(before,after)
    def test_no_persistent_or_traversal_path(self):
        for path in ['/tmp','/tmp/cammy-bridge','/tmp/cammy-smoke-'+('a'*24)+'/../bad','/etc/init.d/S01app','/mnt/extsd/test']:
            with self.subTest(path=path),self.assertRaises(ValueError): s.ram_path(path)
    def test_home_real_gateway_gated(self):
        self.addCleanup(setattr, s, 'discover', s.discover)
        s.discover = Mock(portal=Mock(return_value='http://192.0.2.53:8787'))
        good={'connected':True,'endpoint':'http://192.0.2.53:8787','sensing_started_by_gateway':False,'offline':False,'simulation':False,
              'identity':{'os':'CammyOS','portal_version':'0.3.0','image_marker_present':False,'profile':'m105-stock-fork','kernel_replaced':False},'/v1/state':{'owner':None,'preview_active':False,'sensing_default':'off'},
              '/v1/preview/status':{'phase':'idle','preview_active':False,'event_connected':False}}
        s.validate_gateway(good)
        for k,v in [('simulation',True),('endpoint','http://other:8787'),('sensing_started_by_gateway',True),('identity',{}),('/v1/state',{'owner':'foreign','preview_active':False,'sensing_default':'off'})]:
            with self.subTest(key=k),self.assertRaises(ValueError): s.validate_gateway(dict(good,**{k:v}))

class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.reader=Mock();self.t=s.Trial(self.reader,Path('/unused'),RAM)
        self.meta=f'123 drwx------ 2 0 0 40 Nov 14 17:00 {RAM}\n'
        self.t.owned[RAM]=s.owned_directory(self.meta,RAM)
        self.t.journal=Mock()
    def test_wrong_directory_metadata(self):
        for old,new in [('drwx------','lrwx------'),('drwx------','drwxr-xr-x'),('2 0 0','2 1 0'),(RAM,'/tmp/other')]:
            with self.subTest(value=new),self.assertRaises(ValueError): s.owned_directory(self.meta.replace(old,new),RAM)
    def test_cleanup_only_owned_directory_after_exit(self):
        self.reader.shell.side_effect=[b'',self.meta.encode(),(self.t.token+'\n').encode(),b'.owner\nsmoke_host\n',self.meta.encode(),(self.t.token+'\n').encode(),b'REMOVED\n']
        self.assertTrue(self.t.cleanup()[RAM]['absent'])
        commands=[c.args[0] for c in self.reader.shell.call_args_list]
        self.assertEqual([c for c in commands if c.startswith('rm ')],[f'rm -r {RAM}; if [ ! -e {RAM} ] && [ ! -L {RAM} ]; then printf "REMOVED\\n"; fi'])
    def test_live_process_never_deleted(self):
        self.reader.shell.return_value=b'LIVE:/proc/42/exe\n'
        with self.assertRaisesRegex(ValueError,'still running'): self.t.cleanup()
        self.assertFalse(any(c.args[0].startswith('rm ') for c in self.reader.shell.call_args_list))
    def test_inode_or_token_change_never_deleted(self):
        for meta,token in [(self.meta.replace('123','124'),self.t.token),(self.meta,'foreign')]:
            self.reader.reset_mock();self.reader.shell.side_effect=[b'',meta.encode(),token.encode()]
            with self.assertRaises(ValueError): self.t.cleanup()
            self.assertFalse(any(c.args[0].startswith('rm ') for c in self.reader.shell.call_args_list))
    def test_foreign_members_never_deleted(self):
        self.reader.shell.side_effect=[b'',self.meta.encode(),self.t.token.encode(),b'.owner\nforeign.sock\n']
        with self.assertRaisesRegex(ValueError,'unexpected RAM member'): self.t.cleanup()
        self.assertFalse(any(c.args[0].startswith('rm ') for c in self.reader.shell.call_args_list))
    def test_no_arbitrary_create_or_push(self):
        with self.assertRaises(ValueError): self.t.create('/etc/init.d/S01app')
        with self.assertRaises(ValueError): self.t.push(Path('/unused'),'libc.so')
        self.reader.shell.assert_not_called()
    def test_existing_directory_not_reclaimed(self):
        self.reader.shell.return_value=b''
        self.t.owned={}
        with self.assertRaises(ValueError): self.t.create(s.SOCKET_DIR)
        self.assertFalse(self.t.owned)
        command=self.reader.shell.call_args.args[0]
        self.assertIn('[ ! -e /tmp/cammy-bridge ]',command)
        self.assertNotIn('rm ',command)
        self.assertNotIn('mkdir -p',command)
    def test_emergency_stop_refuses_reused_pid(self):
        self.reader.shell.side_effect=[self.meta.encode(),self.t.token.encode(),('42\n'+statline()+'\n').encode(),statline(start='456').encode()]
        self.t.stop_owned_if_running(BOOT,'a'*64)
        self.assertFalse(any('kill -' in c.args[0] for c in self.reader.shell.call_args_list))
    def test_emergency_stop_requires_executable_hash(self):
        self.reader.shell.side_effect=[self.meta.encode(),self.t.token.encode(),('42\n'+statline()+'\n').encode(),statline().encode(),b'bad /proc/42/exe\n']
        with self.assertRaisesRegex(ValueError,'hash changed'): self.t.stop_owned_if_running(BOOT,'a'*64)
        self.assertFalse(any('kill -' in c.args[0] for c in self.reader.shell.call_args_list))

class LaunchTests(unittest.TestCase):
    def test_core_limit_failure_prevents_host_and_pid_files(self):
        ram=Path('/tmp/cammy-smoke-'+secrets.token_hex(12))
        ram.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree,ram)
        host=ram/'smoke_host'
        host.write_text('#!/bin/sh\n: >"'+str(ram/'executed')+'"\n')
        host.chmod(0o700)
        result=subprocess.run(['/bin/dash','-c','ulimit() { return 1; }; '+s.launch_command(str(ram))],
                              capture_output=True,timeout=3)
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'SMOKE_CORE_LIMIT_REFUSED',result.stdout)
        self.assertEqual(set(p.name for p in ram.iterdir()),{'smoke_host'})

if __name__ == '__main__': unittest.main()
