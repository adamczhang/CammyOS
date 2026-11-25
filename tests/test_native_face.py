"""Offline face lifecycle, exact primitive/stub emulation and device-lib fixtures.

No camera, network, devices, vendor process or full native GUI is run.
"""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from tools import build_native_face_candidate as builder
from tools.native_canvas_abi import NativeCanvasABI,audit,OBJECT,PARENT,PIXELS
from tools.bridge_smoke import DEPS

ROOT=builder.ROOT
OUT=ROOT/'build/native-face'
QEMU=DEPS/'bin/qemu-arm'
ENV=dict(os.environ,LD_LIBRARY_PATH=str(DEPS/'lib/x86_64-linux-gnu'))
HAVE_UNICORN=importlib.util.find_spec('unicorn') is not None


class SurfaceFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make','-s','-C','native/face','host','arm-fixture','asan','device','binding-fixture'],
                       cwd=ROOT,check=True,capture_output=True,timeout=120)

    def test_lifecycle_native_host_sanitized_and_arm(self):
        for command in ([str(OUT/'surface-host')],[str(OUT/'surface-asan')],
                        [str(QEMU),'-cpu','cortex-a7',str(OUT/'surface-arm')]):
            with self.subTest(command=command):
                result=subprocess.run(command,check=True,capture_output=True,text=True,env=ENV,timeout=10)
                report=json.loads(result.stdout)
                self.assertEqual(report['failures'],0);self.assertEqual(report['checks'],49)
                self.assertFalse(report['hardware_displayed'])

    def test_actual_time32_library_with_synthetic_native_address_bindings(self):
        delay=json.loads((OUT/'asset.json').read_text()).get('delay_ms',3000)
        for mode in range(6):
            with self.subTest(mode=mode):
                r=subprocess.run([str(QEMU),'-L',str(ROOT/'build/native-loader/device-rootfs'),'-cpu','cortex-a7',
                    str(OUT/'binding-fixture'),str(builder.LIBRARY),str(mode),str(delay)],check=True,capture_output=True,
                    text=True,env=ENV,timeout=10)
                report=json.loads(r.stdout)
                self.assertTrue(report['binding_fixture']);self.assertFalse(report['hardware_displayed'])
                self.assertLessEqual(report['created'],1);self.assertLessEqual(report['removed'],1)

    def test_library_audit_only_libc_and_three_exports(self):
        report=builder.library_audit(builder.LIBRARY,ROOT/'build/native-loader/device-rootfs',builder.EXPORTS)
        self.assertTrue(report['pass'],report)
        self.assertEqual(report['imports'],['clock_gettime','memcmp','memset'])


@unittest.skipUnless(HAVE_UNICORN,'Unicorn verification extra required')
class NativeCanvasTests(unittest.TestCase):
    def test_audited_native_primitives(self):
        result=audit();self.assertTrue(result['pass'],result)
        self.assertEqual(len(result['checks']),11)
        self.assertFalse(result['full_gui_lifecycle_verified']);self.assertFalse(result['hardware_displayed'])

    def test_null_pixels_native_assertion_is_bounded_and_never_used(self):
        with self.assertRaisesRegex(RuntimeError,'within budget'):
            NativeCanvasABI().primitive(0x4aaa7c,(OBJECT,0,356,400,4))

    def test_unapproved_native_entrypoint_is_refused(self):
        with self.assertRaises(ValueError):NativeCanvasABI().primitive(0x318c8)

    def test_caller_parent_and_dimensions_are_native_arguments(self):
        abi=NativeCanvasABI()
        for w,h in ((1,1),(178,200),(356,400)):
            result=abi.primitive(0x4aaa7c,(OBJECT,PIXELS,w,h,4))
            self.assertEqual(struct.unpack_from('<IIIII',result['object'],84),(4,w,h,0,PIXELS))
        r=abi.primitive(0x4aaa3c,(PARENT+16,))
        self.assertEqual(r['calls'][0]['args'][:2],[0x8fad00,PARENT+16])


class FaceCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=builder.SOURCE.read_bytes()
        cls.sha='a'*64
        cls.candidate,cls.manifest=builder.patch(cls.source,cls.sha)

    def test_patch_scope_and_unchanged_media_sites(self):
        m=self.manifest
        self.assertEqual(m['stub_bytes'],440)
        self.assertEqual(len(m['changes']),5)
        self.assertEqual(len(self.candidate),len(self.source))
        allowed=set(i for r in m['changes'] for i in range(r['offset'],r['offset']+r['size']))
        self.assertTrue(all(a==b or i in allowed for i,(a,b) in enumerate(zip(self.source,self.candidate))))
        for site in (0xabf6c,0xabf78):
            # Full outside-range comparison above is authoritative for every media byte.
            self.assertEqual(self.candidate[site:site+4],self.source[site:site+4])
        self.assertFalse(m['activation_eligible']);self.assertFalse(m['status_only'])
        self.assertEqual(m['class'],'gui-face-trial-v1')

    def test_both_load_sizes_stay_in_original_mapped_pages(self):
        before=builder.Elf32(self.source).phdrs;after=builder.Elf32(self.candidate).phdrs
        for index in (3,4):
            self.assertEqual((before[index]['vaddr']+before[index]['memsz']+4095)//4096,
                             (after[index]['vaddr']+after[index]['memsz']+4095)//4096)
        self.assertEqual(after[4]['vaddr']+after[4]['memsz'],builder.SLOT+4)

    def test_deterministic_and_content_addressed(self):
        self.assertEqual(builder.patch(self.source,self.sha)[0],self.candidate)
        other,m=builder.patch(self.source,'b'*64)
        self.assertNotEqual(other,self.candidate)
        self.assertIn(('b'*64).encode(),other)
        self.assertIn(self.sha,self.manifest['library_path'])

    def test_wrong_source_or_hash_refused(self):
        with self.assertRaises(ValueError):builder.patch(self.source[:-1],self.sha)
        for sha in ('a'*63,'A'*64,'../bad','g'*64):
            with self.assertRaises(ValueError):builder.assemble(sha)

    def test_output_is_confined_and_refuses_overwrite_or_symlink(self):
        with tempfile.TemporaryDirectory(dir=OUT) as temp:
            with self.assertRaises(ValueError):builder.build(Path(temp))
            link=Path(temp)/'link';link.symlink_to(Path(temp),target_is_directory=True)
            with self.assertRaises(ValueError):builder.build(link/'result')
        with self.assertRaises(ValueError):builder.build(ROOT/'dist/face-armed')
        with self.assertRaises(ValueError):builder.build(OUT/'../../dist/face-armed')

    def test_old_status_only_selector_refuses_gui_candidate(self):
        from tools.native_trial.bundle import classify
        with tempfile.TemporaryDirectory(dir=OUT) as temp:
            candidate=Path(temp)/'face';candidate.write_bytes(self.candidate)
            report=classify(candidate,builder.SOURCE,'status-only',builder.LIBRARY)
        self.assertFalse(report['structural_ok'])
        self.assertFalse(report['activation_eligible'])
        self.assertTrue(any('outside' in x for x in report['structural_reasons']))


@unittest.skipUnless(HAVE_UNICORN,'Unicorn verification extra required')
class FaceStubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=builder.SOURCE.read_bytes()
        cls.blob,cls.labels,_=builder.assemble('a'*64)

    def run_stub(self,entry,fail=None,slot=False):
        from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_INTR
        from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,UC_ARM_REG_R8,UC_ARM_REG_R9,UC_ARM_REG_R10,UC_ARM_REG_R11,UC_ARM_REG_R12,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
        cpu=Uc(UC_ARCH_ARM,UC_MODE_ARM)
        cpu.mem_map(0x10000,0xb00000)
        cpu.mem_map(0x2000000,0x200000)
        cpu.mem_write(builder.ADDRESS,self.blob)
        STOP=0x21ffff0;SP=0x2100000;GUI=0x2000100;ATTACH=0x2000200;DETACH=0x2000300
        cpu.mem_write(builder.SLOT,struct.pack('<I',GUI if slot else 0))
        regs=[UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,UC_ARM_REG_R8,UC_ARM_REG_R9,UC_ARM_REG_R10,UC_ARM_REG_R11,UC_ARM_REG_R12]
        initial=[0x3100+i for i in range(13)]
        for r,v in zip(regs,initial):cpu.reg_write(r,v)
        cpu.reg_write(UC_ARM_REG_SP,SP);cpu.reg_write(UC_ARM_REG_LR,STOP)
        calls=[];record=[];dlsyms=[0]
        def hook(engine,pc,n,data):
            if pc==STOP:engine.emu_stop();return
            if builder.ADDRESS<=pc<builder.ADDRESS+len(self.blob):return
            values=[engine.reg_read(r) for r in regs[:4]]
            self.assertEqual(engine.reg_read(UC_ARM_REG_SP)%8,0)
            name=None;result=0
            if pc==0x2ca70:name='dlopen';result=0 if fail=='dlopen' else 1
            elif pc==0x2d574:
                name='dlsym';dlsyms[0]+=1
                sym=bytes(engine.mem_read(values[1],32)).split(b'\0')[0].decode()
                result={'cmf_attach_v1':ATTACH,'cmf_detach_v1':DETACH,'cmf_gui_v1':GUI}[sym]
                if fail==f'dlsym{dlsyms[0]}':result=0
            elif pc==ATTACH:
                name='attach';record.append(bytes(engine.mem_read(values[0],48)))
                result=1 if fail=='attach' else 0
            elif pc==DETACH:
                name='detach';self.assertEqual(values[0],1)
                self.assertEqual(struct.unpack('<I',engine.mem_read(builder.SLOT,4))[0],GUI)
            elif pc in (0xa4294,0x4a7950):
                name='original' if pc==0xa4294 else 'timer'
                self.assertEqual(values,initial[:4])
                if name=='original' and not fail:
                    self.assertEqual(struct.unpack('<I',engine.mem_read(builder.SLOT,4))[0],GUI)
                result=0xabcd
                engine.reg_write(UC_ARM_REG_R1,0xbeef)
            elif pc==GUI:
                name='gui';self.assertEqual(values[0],initial[5]);self.assertIn(values[1],(0,1))
                calls.append((name,values[1]))
            else:raise RuntimeError('Unexpected stub target '+hex(pc))
            if name!='gui':calls.append(name)
            # Deliberately clobber caller-saved scratch registers.
            for r in (UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R12):engine.reg_write(r,0xdead)
            engine.reg_write(UC_ARM_REG_R0,result)
            engine.reg_write(UC_ARM_REG_PC,engine.reg_read(UC_ARM_REG_LR))
        cpu.hook_add(UC_HOOK_CODE,hook)
        cpu.hook_add(UC_HOOK_INTR,lambda *a:(_ for _ in ()).throw(RuntimeError('Interrupt forbidden')))
        cpu.emu_start(self.labels[entry],STOP+4,count=2000,timeout=50000)
        self.assertEqual(cpu.reg_read(UC_ARM_REG_PC),STOP)
        self.assertEqual(cpu.reg_read(UC_ARM_REG_SP),SP)
        self.assertEqual([cpu.reg_read(r) for r in regs[4:12]],initial[4:12])
        self.assertEqual([cpu.reg_read(r) for r in regs[:2]],[0xabcd,0xbeef])
        return calls,record

    def test_main_attach_keeps_gui_slot_for_detach(self):
        calls,records=self.run_stub('face_main_stub')
        self.assertEqual(calls,['dlopen','dlsym','dlsym','dlsym','attach','original','detach'])
        record=records[0]
        self.assertEqual(struct.unpack_from('<III',record),(1,48,2))
        self.assertEqual(record[12:44],bytes.fromhex(builder.status.SOURCE_SHA256))
        self.assertEqual(struct.unpack_from('<I',record,44)[0],0x3100)

    def test_all_loader_refusals_preserve_stock_fallback(self):
        for failure in ('dlopen','dlsym1','dlsym2','dlsym3','attach'):
            with self.subTest(failure=failure):
                calls,_=self.run_stub('face_main_stub',fail=failure)
                self.assertEqual(calls[-1],'original');self.assertNotIn('detach',calls)

    def test_gui_zero_slot_falls_back_to_original_timer(self):
        calls,_=self.run_stub('face_gui_stub')
        self.assertEqual(calls,['timer'])

    def test_gui_published_slot_calls_begin_timer_end(self):
        calls,_=self.run_stub('face_gui_stub',slot=True)
        self.assertEqual(calls,[('gui',0),'timer',('gui',1)])
