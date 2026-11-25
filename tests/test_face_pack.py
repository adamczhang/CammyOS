"""CCC-only Cammy face pack (0.7.11): packed bank, decoder and the portal grant contract.
Synthetic art and a synthetic GUI only; the real atlas in private/ is never needed."""
import json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'sdk'))
from tools import cammy_pack as P
from tools.native_image_abi import bgra_preview
from cammy import Cammy,CammyError

def synthetic(folder,size=1254):
    """A 3 x 3 atlas and a sleep tile in the real layout: flat shapes on a dark gradient."""
    from PIL import Image,ImageDraw
    cell=size//3
    def face(draw,x,y,seed):
        ink=(128,246,204)
        draw.ellipse((x+cell*0.25,y+cell*0.3,x+cell*0.35,y+cell*0.5),fill=ink)
        draw.ellipse((x+cell*0.65,y+cell*0.3,x+cell*0.75,y+cell*0.5),fill=ink)
        draw.arc((x+cell*0.35,y+cell*(0.55+0.02*seed),x+cell*0.65,y+cell*0.75),20+seed*10,160,fill=ink,width=8)
    atlas=Image.new('RGB',(size,size));d=ImageDraw.Draw(atlas)
    for y in range(size):d.line((0,y,size,y),fill=(5,5,28+y*20//size))
    for i in range(9):face(d,(i%3)*cell,(i//3)*cell,i)
    sleep=Image.new('RGB',(size,size));d=ImageDraw.Draw(sleep)
    for y in range(size):d.line((0,y,size,y),fill=(5,4,32+y*20//size))
    d.text((size*0.3,size*0.45),'z z',fill=(128,246,204))
    atlas.save(folder/'atlas.png');sleep.save(folder/'sleep.png')
    return folder/'atlas.png',folder/'sleep.png'

class PackBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make','-s','-C',str(ROOT/'native/face_service'),'pack-fixture'],check=True,capture_output=True)
        cls.tmp=tempfile.TemporaryDirectory();cls.dir=Path(cls.tmp.name)
        cls.atlas,cls.sleep=synthetic(cls.dir)
        cls.report=P.build(cls.dir,cls.atlas,cls.sleep);cls.blob=(cls.dir/'cammy.cpk').read_bytes()
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_bank_is_lossless_bounded_and_complete(self):
        self.assertTrue(self.report['available']);self.assertEqual(self.report['frames'],list(P.FRAMES));self.assertEqual(len(P.FRAMES),16)
        self.assertLessEqual(len(self.blob),P.BUDGET)
        want=[bgra_preview(im.tobytes()) for im in P.frames(self.atlas,self.sleep)]
        self.assertEqual(P.decode(self.blob),want)
    def test_cells_fill_the_panel_and_the_sleep_tile_is_the_loop_base(self):
        cells,base=P.load(self.atlas,self.sleep)
        self.assertEqual(sorted(cells),sorted(P.CELLS));self.assertTrue(all(c.size==(356,400) for c in cells.values()));self.assertEqual(base.size,(356,400))
        frames=P.frames(self.atlas,self.sleep)
        self.assertEqual(frames[P.FRAMES.index('blink')].tobytes(),cells['blink'].tobytes())  # blink stays its own tile
        self.assertNotEqual(frames[P.FRAMES.index('sleep-0')].tobytes(),cells['sleepy'].tobytes())
        for name in ('talk-closed','talk-open'):  # balloon only in the lower half
            a=frames[P.FRAMES.index(name)].tobytes();b=cells['speech-'+name[5:]].tobytes()
            first=next(i for i in range(len(a)) if a[i]!=b[i]);self.assertGreaterEqual(first//(356*3),200)
    def test_native_decoder_matches_and_survives_mutations(self):
        ref=self.dir/'ref.bgra';ref.write_bytes(b''.join(P.decode(self.blob)))
        for name in ('pack-fixture','pack-asan'):
            with self.subTest(name=name):
                out=json.loads(subprocess.check_output([str(ROOT/'build/face-service'/name),str(self.dir/'cammy.cpk'),str(ref)],timeout=300))
                self.assertEqual(out['frames_checked'],33);self.assertGreater(out['rejected'],0);self.assertGreater(out['mutated_frames_decoded'],0)
    def test_missing_inputs_build_an_unavailable_bank(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=P.build(Path(tmp),Path(tmp)/'none.png',Path(tmp)/'none.png');self.assertFalse(r['available'])
            out=json.loads(subprocess.check_output([str(ROOT/'build/face-service/pack-fixture'),'--reject',str(Path(tmp)/'cammy.cpk')]))
            self.assertTrue(out['rejected'])
    def test_bad_atlas_is_refused(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            bad=Path(tmp)/'bad.png';Image.new('RGB',(1000,1253)).save(bad)
            with self.assertRaises(ValueError):P.load(bad,self.sleep)

class FacePackPortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make','-s','native'],cwd=ROOT,check=True,capture_output=True)
        subprocess.run(['make','-s','-C',str(ROOT/'native/face_service'),'fixture'],check=True,capture_output=True)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.control=self.root/'control'
        self.token='test-pack-'+'b'*48;(self.root/'token').write_text(self.token);self.packfile=self.root/'face-pack'
        self.gui=subprocess.Popen([str(ROOT/'build/face-service/gui-fixture'),str(self.control)])
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.portal=subprocess.Popen([str(ROOT/'build/host/cammy-portal'),'--port',str(port),'--token-file',str(self.root/'token'),'--face-control-file',str(self.control)],
                                     stdout=subprocess.DEVNULL,env=dict(os.environ,CAMMY_FACE_PACK_FILE=str(self.packfile)))
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
        self.fail('timed out')
    def status(self,code,fn):
        with self.assertRaises(CammyError) as ctx:fn()
        self.assertEqual(ctx.exception.status,code)
    def shown(self,name,end=None):
        return self.wait(lambda:(r:=self.a.face_pack_status())['pack']['shown']==name and (end is None or r['pack']['ccc_grant']['last_end']==end) and r)
    def test_readback_contract(self):
        r=self.a.face_pack_status()
        self.assertEqual(r['boot_id'],self.a.state()['boot_id']);self.assertIsNone(r['link']['owner']);self.assertFalse(r['link']['face_lease'])
        p=r['pack']
        self.assertEqual((p['shown'],p['boot_choice'],p['ccc_id']),('pikachu','pikachu','cammy'))
        self.assertEqual(p['ids'],['pikachu','fin-fur','cammy']);self.assertEqual(p['child_ids'],['pikachu','fin-fur'])
        self.assertTrue(p['ccc_available']);self.assertFalse(p['ccc_grant']['held']);self.assertTrue(p['physical_can_select_ccc']);self.assertIs(p['authorization'],False)
        face=self.a.capabilities()['face'];self.assertEqual((face['ccc_pack'],face['ccc_pack_control']),('cammy','/v1/face/pack'));self.assertTrue(face['ccc_pack_available'])
        self.assertEqual(self.a.state()['face_pack_shown'],'pikachu');self.assertEqual(self.a.face_status()['pack']['shown'],'pikachu')
        self.status(401,Cammy(self.a.endpoint,'x'*58,'alpha',timeout=2).face_pack_status)
    def test_boot_choice_reports_any_saved_pack(self):
        for raw,want,boot in [('cammy\n','cammy','cammy'),('fin-fur\n','fin-fur','fin-fur'),('eevee\n','eevee','pikachu')]:
            self.packfile.write_text(raw);self.assertEqual(self.a.state()['face_pack'],want);self.assertEqual(self.a.face_pack_status()['pack']['boot_choice'],boot)
    def test_grant_needs_owner_lease_and_shown_face(self):
        self.status(409,lambda:self.a.face_pack('cammy'))
        self.a.acquire();self.status(409,lambda:self.a.face_pack('cammy'))
        self.status(409,lambda:self.b.request('POST','/v1/face/pack',{'pack':'cammy','sequence':99}))
        for body in [{'pack':'cammy'},{'pack':'adult','sequence':50},{'pack':'cammy','sequence':51,'x':1},{'sequence':52}]:
            self.status(400,lambda b=body:self.a.request('POST','/v1/face/pack',b))
        self.a.face('happy');ack=self.a.face_pack('cammy');self.assertTrue(ack['queued']);self.assertIs(ack['authorization'],False)
        r=self.shown('cammy');self.assertTrue(r['pack']['ccc_grant']['held']);self.assertTrue(r['link']['face_lease']);self.assertEqual(r['link']['owner'],'alpha')
        self.status(409,lambda:self.a.request('POST','/v1/face/pack',{'pack':'child','sequence':1}))
    def test_renewals_keep_it_and_ccc_ends_it(self):
        self.a.acquire();self.a.face('neutral');self.a.face_pack('cammy');self.shown('cammy')
        time.sleep(1.2);self.assertEqual(self.a.face_pack_status()['pack']['shown'],'cammy')
        self.a.face('sad');time.sleep(.3);self.assertEqual(self.a.face_pack_status()['pack']['shown'],'cammy')
        self.a.face_pack('child');r=self.shown('pikachu','ccc');self.assertFalse(r['pack']['ccc_grant']['requested'])
    def test_physical_pick_ends_it_and_renewals_cannot_revive_it(self):
        self.a.acquire();self.a.face('neutral');self.a.face_pack('cammy');self.shown('cammy')
        Path(str(self.control)+'.pick').touch();self.shown('pikachu','physical')
        time.sleep(1.2);r=self.a.face_pack_status()['pack'];self.assertEqual(r['shown'],'pikachu');self.assertFalse(r['ccc_grant']['held'])
        self.a.face('happy');time.sleep(.3);self.assertEqual(self.a.face_pack_status()['pack']['shown'],'pikachu')
        self.a.face_pack('cammy');self.shown('cammy')
    def test_release_and_expiry_end_it(self):
        self.a.acquire();self.a.face('neutral');self.a.face_pack('cammy');self.shown('cammy')
        self.a.release();r=self.shown('cammy','lease')
        self.assertFalse(r['pack']['ccc_grant']['held']);self.assertFalse(r['link']['face_lease'])
        self.a.acquire(ttl=1);self.a.face('neutral');self.a.face_pack('cammy')
        self.wait(lambda:self.a.face_pack_status()['pack']['ccc_grant']['held'])
        r=self.shown('cammy','lease');self.assertFalse(r['pack']['ccc_grant']['held'])
        self.wait(lambda:self.a.face_pack_status()['link']['owner'] is None)
        self.b.acquire();self.b.face('neutral');self.b.face_pack('cammy');self.shown('cammy')

if __name__=='__main__':unittest.main()
