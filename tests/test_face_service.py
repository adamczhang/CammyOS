"""Synthetic native GUI + real loopback portal. No camera, mounts or sensors."""
import json,os,signal,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sdk'))
from cammy import Cammy,CammyError

class FaceCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make','-s','-C',str(ROOT/'native/face_service'),'host','asan','arm','fixture','device','binding','pack-fixture'],check=True,capture_output=True)
    def test_actual_time32_library_and_reusable_native_bindings(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build/face-service') as tmp:
            command=['unshare','-rm','sh','-c','mount --bind "$1" /tmp && shift && exec "$@"','sh',tmp,
                     str(ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'),'-L',str(ROOT/'build/native-loader/device-rootfs'),
                     str(ROOT/'build/face-service/binding-fixture'),str(ROOT/'build/face-service/libcammy_face_service.so')]
            result=json.loads(subprocess.check_output(command,timeout=20))
            self.assertEqual(result['created'],5);self.assertEqual(result['removed'],5)
            self.assertEqual(result['ui_pages_mapped'],5)  # every allowed cammy-ui start, no more
            self.assertFalse(result['physical_display_verified'])
            # The CCC-only Cammy pack is built in exactly when its private inputs were present.
            assets=json.loads((ROOT/'build/face-service/assets.json').read_text())
            self.assertEqual(result['cammy_pack'],assets['packs']['cammy']['available'])
    def test_corrupt_child_chooser_falls_back_or_detaches(self):
        import struct
        out=ROOT/'build/face-service'
        bank=(out/'children.cpk').read_bytes()
        original=(out/'libcammy_face_service.so').read_bytes()
        bank_offset=original.index(bank)
        mapping=json.loads((out/'assets.json').read_text())['child_frame_map']
        for failures in (1,2):
            with self.subTest(failures=failures), tempfile.TemporaryDirectory(dir=out) as tmp:
                lib=bytearray(original)
                for logical in (24,63)[:failures]:
                    offset,length=struct.unpack_from('<II',bank,32+16*mapping[logical])
                    lib[bank_offset+offset:bank_offset+offset+length]=b'\xff'*length
                corrupt=Path(tmp)/'face.so'
                corrupt.write_bytes(lib)
                command=['unshare','-rm','sh','-c','mount --bind "$1" /tmp && shift && exec "$@"','sh',tmp,
                         str(ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'),'-L',str(ROOT/'build/native-loader/device-rootfs'),
                         str(out/'binding-fixture'),str(corrupt),str(failures)]
                subprocess.run(command,check=True,timeout=20,capture_output=True)
    def test_ipc_file_rejects_symlinks_hardlinks_modes_and_wrong_size(self):
        import ctypes
        lib=ctypes.CDLL(str(ROOT/'build/face-service/libface_core.so'))
        lib.cfs_map_file.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.POINTER(ctypes.c_void_p)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);real=root/'control';real.write_bytes(bytes(256));real.chmod(0o600)
            pointer=ctypes.c_void_p()
            self.assertEqual(lib.cfs_map_file(str(real).encode(),0,ctypes.byref(pointer)),0)
            libc=ctypes.CDLL(None);libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t];libc.munmap(pointer,256)
            link=root/'link';link.symlink_to(real)
            self.assertNotEqual(lib.cfs_map_file(str(link).encode(),0,ctypes.byref(pointer)),0)
            hard=root/'hard';os.link(real,hard)
            self.assertNotEqual(lib.cfs_map_file(str(real).encode(),0,ctypes.byref(pointer)),0);hard.unlink()
            real.chmod(0o644);self.assertNotEqual(lib.cfs_map_file(str(real).encode(),0,ctypes.byref(pointer)),0)
            real.chmod(0o600);real.write_bytes(b'bad');self.assertNotEqual(lib.cfs_map_file(str(real).encode(),0,ctypes.byref(pointer)),0)
    def test_core_host_asan_and_arm(self):
        runs=[[str(ROOT/'build/face-service/core-fixture')],[str(ROOT/'build/face-service/core-asan')],
              [str(ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'),'-cpu','cortex-a7',str(ROOT/'build/face-service/core-arm')]]
        for command in runs:
            with self.subTest(command=command):
                result=json.loads(subprocess.check_output(command,timeout=20))
                self.assertGreater(result['checks'],80);self.assertFalse(result['hardware_verified'])
    def test_exact_device_abi_and_legacy_auditor_stays_strict(self):
        from tools.build_native_face_candidate import library_audit,EXPORTS
        lib=ROOT/'build/face-service/libcammy_face_service.so';root=ROOT/'build/native-loader/device-rootfs'
        report=library_audit(lib,root,EXPORTS,profile='face-service')
        self.assertTrue(report['pass'],report['failures'])
        self.assertIn('mmap',report['imports']);self.assertNotIn('raise',report['imports'])
        self.assertFalse(library_audit(lib,root,EXPORTS)['pass'])
        with self.assertRaises(ValueError):library_audit(lib,root,EXPORTS,profile='allow-all')
    def test_immutable_bank_has_six_actual_expression_blink_pairs(self):
        import hashlib
        info=json.loads((ROOT/'build/face-service/assets.json').read_text());raw=(ROOT/'build/face-service/frames.bgra').read_bytes()
        self.assertEqual(len(raw),12*569600);self.assertEqual(hashlib.sha256(raw).hexdigest(),info['sha256'])
        for i,frame in enumerate(info['frames']):self.assertEqual(hashlib.sha256(raw[i*569600:(i+1)*569600]).hexdigest(),frame['sha256'])
        for i in [0,4,6,8]:self.assertNotEqual(info['frames'][i]['sha256'],info['frames'][i+1]['sha256'])
        # The authored happy/sleepy eyes are already closed in both poses.
        for i in [2,10]:self.assertEqual(info['frames'][i]['sha256'],info['frames'][i+1]['sha256'])
    def test_fin_and_fur_bank_has_both_fronts_sleep_and_chooser(self):
        import hashlib
        out=ROOT/'build/face-service';info=json.loads((out/'assets.json').read_text())['packs']['fin-fur']
        raw=(out/'finfur.bgra').read_bytes();F=569600
        self.assertEqual(info['turns'],2);self.assertEqual(len(raw),24*F);self.assertEqual(hashlib.sha256(raw).hexdigest(),info['sha256'])
        self.assertEqual([f['front'] for f in info['frames']],['fin']*12+['fur']*12)
        for i,frame in enumerate(info['frames']):self.assertEqual(hashlib.sha256(raw[i*F:(i+1)*F]).hexdigest(),frame['sha256'])
        sha=[f['sha256'] for f in info['frames']]
        self.assertEqual(len(set(sha)),24)  # every mood, blink and front is its own picture
        self.assertEqual(len((out/'finfur-sleep.bgra').read_bytes()),8*F);self.assertEqual(len((out/'finfur-chooser.bgra').read_bytes()),3*F)
    def test_all_child_frames_survive_packing_byte_for_byte(self):
        from tools.build_face_service_assets import CHILD_FILES
        from tools.cammy_pack import decode
        out=ROOT/'build/face-service'
        packed=(out/'children.cpk').read_bytes()
        raw=b''.join((out/name).read_bytes() for name in CHILD_FILES)
        self.assertLess(len(packed),len(raw)//5)
        info=json.loads((out/'assets.json').read_text())
        frames=decode(packed)
        mapping=info['child_frame_map']
        self.assertEqual([i for i,value in enumerate(mapping) if value is None],
                         [10,11,22,35,36,47,48,61])
        self.assertEqual(mapping[2],mapping[3])
        self.assertEqual(len(frames),55)
        for i,index in enumerate(mapping):
            if index is not None:
                self.assertEqual(frames[index],raw[i*569600:(i+1)*569600])
        with tempfile.TemporaryDirectory() as tmp:
            reference=Path(tmp)/'children.bgra'
            reference.write_bytes(b''.join(frames))
            subprocess.run([str(out/'pack-fixture'),'--children',str(out/'children.cpk'),
                            str(reference)],check=True,capture_output=True)

class UiPageBudgetTests(unittest.TestCase):
    """Audit 2026-11-17: the face replaces the mapping on each allowed cammy-ui start."""
    def test_face_map_cap_covers_every_supervised_cammy_ui_start(self):
        import re
        header=(ROOT/'native/ui/cammy_ui_page.h').read_text()
        cap=int(re.search(r'#define CUI_MAX_STARTS (\d+)u',header).group(1))
        builder=(ROOT/'tools/rc06_build.py').read_text()
        first=re.findall(r'^UI_STARTS=(\d+)$',builder,re.M);limit=re.findall(r'\[ "\$UI_STARTS" -lt (\d+) \]',builder)
        self.assertEqual(first,['1']);self.assertEqual(len(limit),1)
        self.assertEqual(int(limit[0]),cap,'supervisor start budget and face map cap differ')
        native=(ROOT/'native/face_service/native_service.c').read_text()
        self.assertIn('ui_maps>=CUI_MAX_STARTS',native);self.assertNotRegex(native,r'ui_maps>=\d')

class FacePortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make','-s','native'],cwd=ROOT,check=True,capture_output=True)
        subprocess.run(['make','-s','-C',str(ROOT/'native/face_service'),'fixture'],check=True,capture_output=True)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.control=self.root/'control';self.token='test-face-'+('a'*48)
        (self.root/'token').write_text(self.token)
        self.gui=subprocess.Popen([str(ROOT/'build/face-service/gui-fixture'),str(self.control)])
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.portal=subprocess.Popen([str(ROOT/'build/host/cammy-portal'),'--port',str(port),'--token-file',str(self.root/'token'),'--face-control-file',str(self.control)],stdout=subprocess.DEVNULL)
        self.a=Cammy(f'http://127.0.0.1:{port}',self.token,'alpha',timeout=2);self.b=Cammy(self.a.endpoint,self.token,'beta',timeout=2)
        self.wait(lambda:self.a.face_status().get('available'))
    def tearDown(self):
        for p in [self.portal,self.gui]:
            if p.poll() is None:p.terminate()
            p.wait(timeout=5)
        self.tmp.cleanup()
    def wait(self,fn,seconds=3):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            try:
                value=fn()
                if value:return value
            except (OSError,CammyError):pass
            time.sleep(.02)
        self.fail('Timed out waiting for synthetic GUI condition')
    def assert_status(self,status,fn):
        with self.assertRaises(CammyError) as ctx:fn()
        self.assertEqual(ctx.exception.status,status)
    def test_repeated_replay_hide_and_idle_need_no_restart(self):
        self.a.acquire()
        first=None
        for expression in ['happy','angry','sleepy','sad','neutral']:
            ack=self.a.face(expression,action='replay');self.assertTrue(ack['queued']);self.assertFalse(ack['gui_applied'])
            if first is None:first=ack['request_id']
            s=self.wait(lambda:(s:=self.a.face_status())['gui_applied_request_id']>=ack['request_id'] and s['expression']==['neutral','happy','angry','sad','doubt','sleepy'].index(expression) and s)
            self.assertFalse(s['physical_display_confirmed'])
        ack=self.a.face(action='hide');self.wait(lambda:self.a.face_status()['phase']==2)
        self.a.face(action='idle');self.wait(lambda:(s:=self.a.face_status())['phase']==1 and s['idle_loop'])
        self.assertGreater(self.a.face_status()['objects_created'],1)
    def test_owner_authentication_and_strict_schema(self):
        self.assert_status(409,lambda:self.a.face('happy'));self.a.acquire()
        self.assert_status(409,lambda:self.b.face('angry'))
        for raw in [{'expression':'wrong'},{'action':'reboot'},{'expression':'happy','path':'/dev/mem'}]:self.assert_status(400,lambda:self.a.request('POST','/v1/face',raw))
        self.assertTrue(self.a.capabilities()['face']['available'])
    def test_old_lease_and_reordered_face_intents_cannot_replace_current_face(self):
        self.a.acquire();old=self.a._lease_binding
        self.a.face('happy');self.a.face('sleepy')
        self.assert_status(409,lambda:self.a.request('POST','/v1/face',{'expression':'angry','sequence':1}))
        self.wait(lambda:self.a.face_status()['expression']==5)
        self.a.release();self.a.acquire()
        self.assertNotEqual(old,self.a._lease_binding)
        stale=Cammy(self.a.endpoint,self.token,'alpha');stale._boot_binding=self.a._boot_binding;stale._lease_binding=old
        self.assert_status(409,lambda:stale.face('angry'))
        self.assertEqual(self.a.state()['owner'],'alpha')
    def test_old_boot_binding_rejects_changes_before_native_dispatch(self):
        self.a.acquire();before=self.a.face_status()['last_processed_request_id']
        self.a._boot_binding='0'*32
        self.assert_status(409,lambda:self.a.face('angry'))
        self.assertEqual(self.b.face_status()['last_processed_request_id'],before)
    def test_release_and_global_stop_cancel_face_owner(self):
        self.a.acquire();ack=self.a.face('angry');self.wait(lambda:self.a.face_status()['gui_applied_request_id']>=ack['request_id'])
        self.a.release();self.wait(lambda:(s:=self.a.face_status())['idle_loop'] and s['expression']==5)  # asleep
        self.b.acquire();ack=self.b.face('happy');self.wait(lambda:self.b.face_status()['gui_applied_request_id']>=ack['request_id'])
        self.a.stop();self.wait(lambda:self.b.face_status()['idle_loop']);self.assertIsNone(self.a.state()['owner'])
    def test_dead_gui_is_unavailable_and_late_renewal_does_not_resurrect(self):
        self.a.acquire();ack=self.a.face('angry');self.wait(lambda:self.a.face_status()['gui_applied_request_id']>=ack['request_id'])
        os.kill(self.gui.pid,signal.SIGUSR1)
        self.wait(lambda:not self.a.face_status()['available'],seconds=2)
        self.assert_status(503,lambda:self.a.face('happy'))
        time.sleep(1.2);os.kill(self.gui.pid,signal.SIGUSR1)
        self.wait(lambda:self.a.face_status()['available']);self.wait(lambda:self.a.face_status()['idle_loop'])
    def test_screen_change_yields_until_explicit_show(self):
        self.a.acquire();self.a.face('happy');self.wait(lambda:self.a.face_status()['expression']==1)
        os.kill(self.gui.pid,signal.SIGUSR2);self.wait(lambda:self.a.face_status()['suspended'])
        time.sleep(.35);self.assertTrue(self.a.face_status()['suspended'])
        self.a.face('happy',action='replay');self.wait(lambda:not self.a.face_status()['suspended'])
    def test_wrong_permissions_or_dead_instance_never_claim_available(self):
        self.gui.terminate();self.gui.wait(timeout=5);self.wait(lambda:not self.a.face_status()['available'])
        self.assertFalse(self.a.capabilities()['face']['available'])
