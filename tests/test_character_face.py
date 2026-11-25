"""Real ParamFace character asset admission, visibility and existing controls."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from cammy_face import FaceRenderer,preset,preset_path,EXPRESSIONS,validate_face


class CharacterFaceTests(unittest.TestCase):
    def setUp(self):
        self.renderer=FaceRenderer()
        self.addCleanup(self.renderer.close)
        self.renderer.load(preset('pikachu'))
        self.renderer.drive(mouth=0,gaze=(0,0))

    def pixels_in(self,frame,box):
        x0,y0,x1,y1=box
        return [tuple(frame[(y*356+x)*3:(y*356+x)*3+3])
                for y in range(y0,y1) for x in range(x0,x1)]

    def test_asset_uses_existing_schema_and_is_reproducible(self):
        # Generator imports Pillow only when invoked; source asset has no runtime dependency.
        from tools.build_character_face import build
        generated=validate_face(json.dumps(build()).encode())
        self.assertEqual(generated,preset('pikachu'))
        self.assertLess(len(generated),65536)
        self.assertEqual(json.loads(generated)['version'],2)
        self.assertEqual(preset_path('pikachu'),ROOT/'assets/faces/pikachu.json')
        self.assertFalse(self.renderer.state()['physical_display_verified'])

    def test_features_stay_visible_for_every_expression(self):
        # Catches accidental absolute expression coordinates: upstream numbers are deltas.
        for expression in EXPRESSIONS:
            with self.subTest(expression=expression):
                self.renderer.set_expression(expression)
                frame=self.renderer.frame(0)
                for box in [(65,205,137,269),(220,205,292,269),(125,305,231,355)]:
                    values=self.pixels_in(frame,box)
                    self.assertGreater(sum(max(p)<85 for p in values),15)
                if expression not in ('sleepy','happy'):
                    values=self.pixels_in(frame,(65,205,137,269))
                    self.assertGreater(sum(min(p)>180 for p in values),12)

    def test_gaze_and_mouth_drive_affect_only_expected_regions(self):
        before=self.renderer.frame(0)
        self.renderer.drive(mouth=1,gaze=(0,0));opened=self.renderer.frame(0)
        self.assertNotEqual(self.pixels_in(before,(125,295,232,360)),self.pixels_in(opened,(125,295,232,360)))
        self.assertEqual(self.pixels_in(before,(0,0,356,160)),self.pixels_in(opened,(0,0,356,160)))
        self.renderer.drive(mouth=0,gaze=(1,-1));looked=self.renderer.frame(0)
        self.assertNotEqual(self.pixels_in(before,(60,195,296,278)),self.pixels_in(looked,(60,195,296,278)))

    def test_blink_changes_eyes_with_closed_mouth_and_fixed_gaze(self):
        before=self.renderer.frame(0)
        for _ in range(17):after=self.renderer.frame(50)
        self.assertNotEqual(self.pixels_in(before,(65,205,137,269)),self.pixels_in(after,(65,205,137,269)))
        self.assertEqual(self.pixels_in(before,(0,0,356,150)),self.pixels_in(after,(0,0,356,150)))

    def test_red_cheeks_have_a_skin_margin_inside_the_head(self):
        frame=self.renderer.frame(0);background=tuple(frame[:3]);reds=[]
        for y in range(270,345):
            for x in list(range(25,115))+list(range(245,331)):
                p=tuple(frame[(y*356+x)*3:(y*356+x)*3+3])
                if p[0]>150 and p[1]<110 and p[2]<90:reds.append((x,y))
        self.assertGreater(len(reds),1500)
        for x,y in reds[::8]:
            neighborhood=self.pixels_in(frame,(x-6,y-6,x+7,y+7))
            self.assertNotIn(background,neighborhood,'red cheek touches the outside of the head')

    def test_full_panel_overlay_is_an_explicit_bounded_extension(self):
        document=json.loads(preset('pikachu'))
        self.assertEqual((document['overlay']['frames']['open']['w'],document['overlay']['frames']['open']['h']),(356,400))
        before=self.renderer.frame(0)
        for key,value in [('w',357),('h',401)]:
            bad=json.loads(preset('pikachu'));bad['overlay']['frames']['open'][key]=value
            with self.assertRaises(ValueError):self.renderer.load(json.dumps(bad).encode())
            self.assertEqual(self.renderer.frame(0),before)
        control=ROOT/'build/paramface/render-upstream-shim'
        result=subprocess.run([str(control),str(preset_path('pikachu')),'0','356','400','0'],capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0)  # Upstream's overlay bound is80x60.

    def test_angry_sparks_and_sleepy_zzz_are_visible(self):
        neutral=self.renderer.frame(0)
        self.renderer.set_expression('angry');angry=self.renderer.frame(0)
        self.assertNotEqual(self.pixels_in(neutral,(133,158,184,209)),self.pixels_in(angry,(133,158,184,209)))
        self.renderer.set_expression('sleepy');sleepy=self.renderer.frame(0)
        white=lambda frame:sum(min(p)>180 for p in self.pixels_in(frame,(211,163,261,214)))
        self.assertGreater(white(sleepy),white(neutral)+30)

    def test_host_and_arm_match_each_character_expression(self):
        q=ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'
        arm=ROOT/'build/paramface/render-arm'
        self.assertTrue(q.is_file());self.assertTrue(arm.is_file())
        for expression in range(6):
            args=[str(preset_path('pikachu')),str(expression),'356','400','0']
            host=subprocess.check_output([str(ROOT/'build/paramface/render-host'),*args],timeout=5)
            actual=subprocess.check_output([str(q),'-cpu','cortex-a7',str(arm),*args],timeout=10)
            self.assertEqual(hashlib.sha256(host).digest(),hashlib.sha256(actual).digest())
