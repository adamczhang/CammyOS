"""Real pinned renderer, parser admission and host/ARM/ASan equivalence."""
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from cammy_face import EXPRESSIONS,PRESETS,FaceRenderer,preset,preset_path,validate_face


class FaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT/'build/paramface/libcammy_paramface.so').exists():
            subprocess.run(['make','-C',str(ROOT/'native/paramface'),'host'],check=True,capture_output=True)

    def setUp(self):
        self.face=FaceRenderer();self.addCleanup(self.face.close)

    def test_all_real_presets_and_expressions_render_nonempty(self):
        for name in PRESETS:
            self.face.load(preset(name))
            for e in EXPRESSIONS:
                self.face.set_expression(e);pixels=self.face.frame(0)
                self.assertEqual(len(pixels),356*400*3)
                self.assertGreater(len(set(pixels)),1)
        self.assertFalse(self.face.state()['physical_display_verified'])

    def test_mouth_gaze_animation_and_reload_affect_real_pixels(self):
        base=self.face.frame(0)
        self.face.drive(mouth=1,gaze=(1,0));changed=self.face.frame(0)
        self.assertNotEqual(base,changed)
        self.face.drive();frames=[]
        for _ in range(45):frames.append(hashlib.sha256(self.face.frame(33.333)).digest())
        self.assertGreater(len(set(frames)),2)
        self.face.load(preset('cat'));self.assertNotEqual(changed,self.face.frame(0))

    def test_invalid_load_keeps_last_face_and_state(self):
        old=self.face.json;before=self.face.frame(0)
        bad=json.loads(old);bad['parts']['eyeL']['frames']={'open':{'w':1,'h':1,'palette':['#ffffff'],'data':'////'}}
        with self.assertRaises(ValueError):self.face.load(json.dumps(bad).encode())
        self.assertEqual(self.face.json,old)
        self.assertEqual(self.face.frame(0),before)

    def test_schema_admission_limits_duplicate_nonfinite_and_nesting(self):
        for raw in (b'[]',b'{}',b'{"version":1,"version":2,"parts":{}}',b'null',b'\xff',b'a'*65537,b'{"version":1,"parts":{"x":NaN}}',b'{"version":1,"parts":{"x":1e999}}'):
            with self.subTest(raw=raw[:80]),self.assertRaises(ValueError):validate_face(raw)
        bad=json.loads(preset('default'));bad['canvas']['width']=0
        with self.assertRaises(ValueError):validate_face(json.dumps(bad).encode())
        bad['canvas']['width']=356;bad['x']=x={}
        for _ in range(12):x['x']={};x=x['x']
        with self.assertRaises(ValueError):validate_face(json.dumps(bad).encode())

    def test_invalid_public_commands_and_c_abi_bounds(self):
        for e in ('evil',6,None):
            with self.assertRaises(ValueError):self.face.set_expression(e)
        for dt in (-1,251,float('nan'),True):
            with self.assertRaises(ValueError):self.face.frame(dt)
        for options in ({'mouth':2},{'gaze':(float('inf'),0)},{'gaze':(0,3)}):
            with self.assertRaises(ValueError):self.face.drive(**options)
        self.assertFalse(self.face.lib.cm_pf_create(-1,400))
        self.assertEqual(self.face.lib.cm_pf_expression(self.face.ctx,6),-1)
        self.assertEqual(self.face.lib.cm_pf_frame(self.face.ctx,0,self.face.pixels,1),-1)
        self.face.close();self.face.close()
        with self.assertRaises(RuntimeError):self.face.frame()

    def test_multiple_contexts_and_concurrent_requests_do_not_cross(self):
        second=FaceRenderer();self.addCleanup(second.close)
        reference=second.frame(0);errors=[]
        def run():
            try:
                for _ in range(10):self.face.frame();self.face.set_expression('happy')
            except Exception as e:errors.append(e)
        workers=[threading.Thread(target=run) for _ in range(4)]
        for w in workers:w.start()
        for w in workers:w.join()
        self.assertEqual(errors,[])
        self.assertEqual(second.frame(0),reference)

    def test_large_arc_work_is_bounded_by_viewport(self):
        # A large-radius, nearly flat arc used to iterate far outside the image.
        # The clipped version must complete and keep writes inside the canvas.
        raw=json.loads(preset('default'))
        raw['parts']['eyeL'].update(shape='arc',width=640,height=100,curve=.004,thickness=4)
        self.face.load(json.dumps(raw).encode());frame=self.face.frame(0)
        self.assertEqual(len(frame),356*400*3)
        self.assertLess(self.face.last_render_ms,1000)


class TargetTests(unittest.TestCase):
    def test_static_arm_matches_host_all_presets_at_camera_dimensions(self):
        arm=ROOT/'build/paramface/render-arm';host=ROOT/'build/paramface/render-host'
        q=ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'
        if not arm.exists() or not q.exists():self.skipTest('prepared ARM renderer and qemu required')
        for name in PRESETS:
            for expression in (0,1,5):
                args=[str(preset_path(name)),str(expression),'356','400','0']
                expected=subprocess.check_output([str(host),*args],timeout=4)
                actual=subprocess.check_output([str(q),'-cpu','cortex-a7',str(arm),*args],timeout=8)
                self.assertEqual(actual,expected,(name,expression))

    def test_clipped_shim_preserves_upstream_preset_pixels(self):
        control=ROOT/'build/paramface/render-upstream-shim'
        if not control.exists():self.skipTest('prepared upstream shim control required')
        for name in PRESETS:
            if name=='pikachu':continue  # Cammy's bounded full-panel overlay extension.
            args=[str(preset_path(name)),'1','356','400','0']
            original=subprocess.check_output([str(control),*args],timeout=5)
            clipped=subprocess.check_output([str(ROOT/'build/paramface/render-host'),*args],timeout=5)
            self.assertEqual(original,clipped,name)

    def test_asan_real_presets_and_animation(self):
        binary=ROOT/'build/paramface/render-asan'
        if not binary.exists():self.skipTest('prepared ASan renderer required')
        for name in PRESETS:
            p=subprocess.run([str(binary),str(preset_path(name)),'2','356','400','40'],capture_output=True,timeout=8)
            self.assertEqual(p.returncode,0,p.stderr.decode()[-1000:])
            self.assertNotIn(b'Sanitizer',p.stderr)
