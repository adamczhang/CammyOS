"""Real local candidate bytes with tamper/path/authority regression checks."""
import json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from tools import rc06_build as B, rc_wifi_policy
from tools.rc06_validate import validate
ROOT=B.ROOT
class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'build/rc06').mkdir(exist_ok=True)
        cls.tmp=tempfile.TemporaryDirectory(dir=ROOT/'build/rc06');cls.directory=Path(cls.tmp.name)/'package'
        cls.report=B.build(cls.directory)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def setUp(self):self.changed=[]
    def tearDown(self):
        for p,raw,mode in reversed(self.changed):
            if p.is_symlink():p.unlink()
            p.write_bytes(raw);p.chmod(mode)
    def alter(self,name,raw,reseal=False):
        p=self.directory/name;self.changed.append((p,p.read_bytes(),p.stat().st_mode&0o777));p.write_bytes(raw)
        if reseal:
            m=self.directory/'candidate.json';report=json.loads(m.read_text());report['files'][name]={'sha256':B.sha(raw),'bytes':len(raw)}
            self.alter('candidate.json',(json.dumps(report)+'\n').encode())
    def test_complete_package_is_private_disarmed_and_versioned(self):
        result=validate(self.directory);self.assertTrue(result['ok']);self.assertFalse(result['hardware_accepted'])
        report=self.report;prefix='tf/'+report['slot']+'/runtime/'
        self.assertFalse(report['automatic_media_initialization']);self.assertFalse(report['resident_opt_in'])
        self.assertIn(b'--speaker-root', (self.directory/prefix/'startup.sh').read_bytes())
        self.assertIn(b'lineout20-from0', (self.directory/prefix/'speaker-guardian').read_bytes())
        self.assertTrue(report['speaker_pcm_api']);self.assertFalse(report['speaker_wifi_hardware_verified'])
        self.assertIn(b'--strict-control --managed-media',(self.directory/prefix/'startup.sh').read_bytes())
        self.assertIn(('cammy-portal '+B.VERSION).encode(),(self.directory/prefix/'cammy-portal').read_bytes())
        self.assertFalse(any('portal.token' in name or 'home.conf' in name for name in report['files']))
        self.assertTrue((self.directory/'desktop/build/paramface/libcammy_paramface.so').exists())
        self.assertTrue((self.directory/'desktop/downloads/stackchan-face-editor/web/dist/index.html').exists())
    def test_all_generated_shells_parse_without_executing(self):
        paths=[self.directory/'bootstrap/S01app',self.directory/'bootstrap/S99cammy-tf']
        paths+=list((self.directory/'tf'/self.report['slot']/'runtime').glob('*.sh'))
        for shell in [['dash'],['bash','--posix'],['busybox','sh']]:
            for p in paths:
                with self.subTest(shell=shell,path=p.name):subprocess.run(shell+['-n',str(p)],check=True,capture_output=True)
    def test_runtime_reads_no_unassigned_variable(self):
        # The runtime runs under set -u: rc3 aborted on boot ff533369 because the diag
        # line read NATIVE_VIDEO after the build had removed its assignment.
        import re
        text=(self.directory/('tf/'+self.report['slot']+'/runtime/startup.sh')).read_text()
        used=set(re.findall(r'\$\{?([A-Z][A-Z0-9_]*)(?![A-Z0-9_]*:-)',text))
        assigned=set(re.findall(r'(?:^|[\s;(])(?:export\s+|local\s+)?([A-Z][A-Z0-9_]*)=',text,re.M))
        assigned|=set(re.findall(r'\bfor\s+([A-Z][A-Z0-9_]*)\s',text))|set(re.findall(r'\bread\s+(?:-r\s+)?([A-Z][A-Z0-9_]*)',text))
        self.assertEqual(sorted(used-assigned-{'HOME','PATH','PPID'}),[])
    def test_runtime_runs_and_stops_the_menu(self):
        # 0.7.10: S99 stages cammy-ui in RAM (mode 700) and checks it against the
        # pinned manifest; the runtime starts it after the portal, restarts it at
        # most four times, and cleanup stops it with the other clients.
        runtime=self.directory/('tf/'+self.report['slot']+'/runtime')
        text=(runtime/'startup.sh').read_text()
        s99=(self.directory/'bootstrap/S99cammy-tf').read_text()
        ui_sha=B.sha((runtime/'cammy-ui').read_bytes())
        self.assertEqual(s99.count('  cammy_take cammy-ui 700 %s || cammy_refuse\n'%ui_sha),1)
        self.assertLess(s99.index('cammy_take cammy-ui '),s99.index('>"$GUARD"'))
        self.assertNotIn('$CARD/cammy-ui',text)
        self.assertIn('"$R/cammy-ui" --run-dir "$R"',text)
        self.assertIn('"$R/ui.pid"; do',text);self.assertIn('wpa_supplicant|busybox|cammy-ui) kill',text)
        # Loading gate: started once the payload is verified, before the stock settle.
        # S99 verified every RAM copy before it ran startup.sh; no second pass here.
        self.assertNotIn('sha256sum -c',text)
        self.assertLess(s99.index('cammy_take startup.sh '),s99.index('"$R/daemon-exec"'))
        self.assertLess(text.index('B=$R/busybox\n'),text.index('\nstart_ui\n'))
        self.assertLess(text.index('\nstart_ui\nUI_STARTS=1\n'),text.index('phase stock_settle'))
        # the settle ends at 35 s of uptime, not a fixed 30 s after the chooser
        settle=text[text.index('phase stock_settle'):text.index('phase radio_load')]
        self.assertNotIn('sleep 30',settle)
        self.assertIn('SETTLE_UNTIL=35',settle)
        self.assertEqual(text.count('\nstart_ui\n'),1)
        self.assertIn('[ "$UI_STARTS" -lt 5 ]',text)
        subprocess.run(['sh','-n',str(runtime/'startup.sh')],check=True)
        self.assertIn('  cammy-ui\n',(runtime/'SHA256SUMS').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            stripped=Path(tmp)/'cammy-ui'
            tool=ROOT/'build/toolchain/bin/arm-buildroot-linux-musleabihf-strip'
            subprocess.run([str(tool),'--strip-all','-o',str(stripped),
                            str(ROOT/'build/cammy-ui/cammy-ui-arm')],check=True)
            self.assertEqual((runtime/'cammy-ui').read_bytes(),stripped.read_bytes())
    def test_tampered_native_bytes_are_refused(self):
        name='tf/'+self.report['slot']+'/native/candidate.bin';p=self.directory/name;raw=bytearray(p.read_bytes());raw[-1]^=1
        self.alter(name,bytes(raw),True)
        with self.assertRaisesRegex(ValueError,'Native exact patch'):validate(self.directory)
    def test_resealed_speaker_worker_or_profile_is_refused(self):
        prefix='tf/'+self.report['slot']+'/runtime/'
        self.alter(prefix+'speaker-guardian',b'\x7fELFfake',True)
        with self.assertRaisesRegex(ValueError,'Binary receipt'):validate(self.directory)

    def test_resealed_launch_proposal_is_still_refused(self):
        name='bootstrap/S01app';self.alter(name,(self.directory/name).read_bytes()+b'\necho unexpected\n',True)
        with self.assertRaisesRegex(ValueError,'Launch source'):validate(self.directory)
    def test_profile_sidecar_is_refused_even_with_a_matching_receipt(self):
        slot = self.report['slot']
        sidecar = 'tf/' + slot + '/runtime/network.settings'
        path = self.directory / sidecar
        path.write_bytes(b'fictional network configuration')
        path.chmod(0o600)
        self.addCleanup(path.unlink)
        with self.assertRaisesRegex(ValueError, 'Unexpected file'):
            rc_wifi_policy.validate_tf_tree(self.directory, slot)
        receipt = json.loads((self.directory / 'candidate.json').read_text())
        receipt['files'][sidecar] = {'sha256': B.sha(path.read_bytes()), 'bytes': path.stat().st_size}
        self.alter('candidate.json', json.dumps(receipt).encode())
        with self.assertRaisesRegex(ValueError, 'portable release allowlist'):
            validate(self.directory)

    def test_profile_assignment_is_refused_in_an_expected_tf_file(self):
        name = 'tf/' + self.report['slot'] + '/runtime/dhcp.sh'
        original = (self.directory / name).read_bytes()
        self.alter(name, original + b'\n  psk="fictional-fixture"\n', True)
        with self.assertRaisesRegex(ValueError, 'Wi-Fi credential assignment'):
            validate(self.directory)
        with self.assertRaisesRegex(ValueError, 'Wi-Fi credential assignment'):
            rc_wifi_policy.validate_tf_tree(self.directory, self.report['slot'])

    def test_install_plan_cannot_redirect_a_bootstrap_file_into_the_wifi_store(self):
        filename = 'installation-plan.json'
        plan = json.loads((self.directory / filename).read_text())
        plan['internal_files'][-1]['target'] = '/etc/cammy/home.conf'
        self.alter(filename, json.dumps(plan).encode(), True)
        with self.assertRaisesRegex(ValueError, 'preserve camera-local configuration'):
            validate(self.directory)
        with self.assertRaisesRegex(ValueError, 'preserve camera-local configuration'):
            rc_wifi_policy.validate_install_plan(plan, self.report['release_id'])

    def test_bootstrap_cannot_be_armed_by_resealing_manifest(self):
        self.alter('bootstrap/native-select.enabled',b'mode=select\n',True)
        with self.assertRaisesRegex(ValueError,'Armed bootstrap'):validate(self.directory)
    def test_symlink_hardlink_executable_and_extra_member_refused(self):
        p=self.directory/'controls/m105.json';raw=p.read_bytes();self.changed.append((p,raw,0o600))
        p.unlink();p.symlink_to(self.directory/'candidate.json')
        with self.assertRaises(ValueError):validate(self.directory)
        p.unlink();p.write_bytes(raw);p.chmod(0o700)
        with self.assertRaises(ValueError):validate(self.directory)
        p.chmod(0o600);extra=self.directory/'extra';os.link(p,extra)
        try:
            with self.assertRaises(ValueError):validate(self.directory)
        finally:extra.unlink()
    def test_report_cannot_promote_hardware_evidence(self):
        report=json.loads((self.directory/'candidate.json').read_text());report['hardware_accepted']=True
        self.alter('candidate.json',json.dumps(report).encode())
        with self.assertRaisesRegex(ValueError,'Unaccepted'):validate(self.directory)
    def test_builder_refuses_overwrite_and_public_destination(self):
        with self.assertRaises(ValueError):B.build(self.directory)
        with self.assertRaises(ValueError):B.build(ROOT/'release-not-ignored')
    def test_repeated_build_has_identical_release_and_member_bytes(self):
        other=Path(self.tmp.name)/'repeat';report=B.build(other)
        self.assertEqual(report,self.report)
        self.assertEqual((other/'candidate.json').read_bytes(),(self.directory/'candidate.json').read_bytes())

class SourceReceiptTests(unittest.TestCase):
    """Build receipt inputs, checked without building or touching shared outputs."""
    def test_receipt_covers_the_native_inputs_it_used_to_omit(self):
        ids=B.source_identities()
        for name in ['src/cammy_portal.c','src/cammy_face_control.h','src/cammy_media_prepare.h','src/cammy_speaker.h',
                     'native/speaker_probe/speaker_stream.h','native/face_service/native_service.c','native/face_service/face_service.c',
                     'native/face_service/ipc_file.h','native/face_service/status_main.c','native/face_service/Makefile',
                     'native/face/cammy_face_host.h','native/face/exports.map','native/face/face_stub.S','src/boot/native_service_client.c',
                     'src/compat/arm_legacy_stat.c','build/arm/arm_legacy_stat.o','tools/build_native_face_candidate.py','native/loader/abi_audit.py']:
            with self.subTest(name=name):self.assertIn(name,ids)
        # Experimental and generated files stay out: the receipt pins what build() reads.
        for name in ['native/speaker_probe/config_inspector.c','native/face/native_face.c','native/face_service/gui_fixture.c',
                     'build/cammy-ui/ui_assets.c','build/face-service/frames.S','build/face-service/libcammy_face_service.so']:
            with self.subTest(excluded=name):self.assertNotIn(name,ids)
    def test_receipt_covers_every_project_module_the_build_imports(self):
        code=('import sys;sys.path[:0]=[".","tools"];import tools.rc06_build,tools.shared_capture_profile,tools.speaker_profile,'
              'tools.build_face_service_assets,tools.build_ui_assets;from pathlib import Path;r=Path(".").resolve()\n'
              'for m in list(sys.modules.values()):\n f=getattr(m,"__file__",None)\n'
              ' if f and Path(f).resolve().is_relative_to(r):\n'
              '  origin=Path(f).resolve().relative_to(r)\n'
              '  if "site-packages" not in origin.parts:print(origin)')
        out=subprocess.run([sys.executable,'-c',code],cwd=ROOT,check=True,capture_output=True,text=True).stdout.split()
        missing=sorted(set(out)-set(B.source_identities())-{'tools/rc06_build.py'})
        self.assertEqual(missing,[])
    def test_isolated_copy_detects_modified_and_new_native_sources(self):
        ids=B.source_identities()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ids:
                (root/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,root/name)
            self.assertEqual(B.source_identities(root),ids)
            header=root/'native/speaker_probe/speaker_stream.h';header.write_bytes(header.read_bytes()+b'\n/* changed */\n')
            changed=B.source_identities(root)
            self.assertEqual([k for k in ids if changed[k]!=ids[k]],['native/speaker_probe/speaker_stream.h'])
            # A header added to the portal later joins the receipt with no list edit.
            portal=root/'src/cammy_portal.c';(root/'src/cammy_extra.h').write_bytes(b'/* new */\n')
            portal.write_bytes(portal.read_bytes()+b'\n#include "cammy_extra.h"\n')
            self.assertIn('src/cammy_extra.h',B.source_identities(root))
            portal.write_bytes(portal.read_bytes()+b'#include "cammy_missing.h"\n')
            with self.assertRaisesRegex(ValueError,'Unresolved native include cammy_missing.h'):B.source_identities(root)
