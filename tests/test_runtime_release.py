"""TF release integrity tests; all writes are in temporary PC directories."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
package=module('runtime_package',ROOT/'scripts/package-runtime.py')
stager=module('runtime_stage',ROOT/'tools/stage_tf_release.py')


class RuntimeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        sources=['VERSION','src/cammy_portal.c','LICENSE','scripts/tf-runtime-startup.sh',
            'scripts/home-dhcp.sh','scripts/shared-capture.sh','scripts/native-media-start.sh','build/arm/native-service-client','build/arm/cammy-portal','build/arm/busybox-dhcp',
            'build/arm/wpa_supplicant','build/arm/wpa_cli','build/arm/daemon-exec',
            'build/busybox-dhcp-src/LICENSE','build/wpa_supplicant-2.12/COPYING']
        for name in sources:
            if not (ROOT/name).exists():self.skipTest('Prepared ARM runtime inputs are required')
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,path)
        self.archive=package.build(self.root)

    def test_reproducible_payload_and_local_stage(self):
        first=self.archive.read_bytes();package.build(self.root)
        self.assertEqual(first,self.archive.read_bytes())
        files=stager.verify(self.archive)
        self.assertEqual(files['RELEASE'].decode().strip(),(ROOT/'VERSION').read_text().strip())
        self.assertNotIn('portal.token',files)
        self.assertNotIn('home.conf',files)
        self.assertNotIn('enable-shared-capture',files)
        self.assertNotIn('enable-native-video',files)
        self.assertIn('native-service-client',files)
        self.assertIn('native-media-start.sh',files)
        self.assertIn('shared-capture.fragment',files)
        self.assertIn('shared-capture.sh',files)
        dest=self.root/'staged/CammyOS'
        stager.stage(self.archive,dest)
        self.assertEqual((dest/'cammy-portal').read_bytes(),files['cammy-portal'])
        with self.assertRaises(FileExistsError):stager.stage(self.archive,dest)
        self.assertEqual((dest/'RELEASE').read_text(),(ROOT/'VERSION').read_text())

    def test_corrupt_payload_and_extra_members_are_rejected(self):
        files=stager.verify(self.archive)
        for name,value in [('cammy-portal',b'corruption'),('portal.token',b'not-allowed')]:
            altered=dict(files);altered[name]=value
            archive=self.root/(name+'.tar.gz')
            with tarfile.open(archive,'w:gz') as tar:
                for key,data in altered.items():
                    info=tarfile.TarInfo('CammyOS/'+key);info.size=len(data)
                    tar.addfile(info,io.BytesIO(data))
            with self.assertRaises(ValueError):stager.verify(archive)

    def test_links_and_traversal_are_rejected_before_stage(self):
        for name,kind in [('CammyOS/../../escape',tarfile.REGTYPE),('CammyOS/link',tarfile.SYMTYPE)]:
            archive=self.root/'unsafe.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                info=tarfile.TarInfo(name);info.type=kind;info.linkname='/etc/passwd';tar.addfile(info)
            dest=self.root/'unsafe-stage'
            with self.assertRaises(ValueError):stager.stage(archive,dest)
            self.assertFalse(dest.exists())


class SharedStartupTests(unittest.TestCase):
    def test_profile_success_failure_and_disabled_control_plane(self):
        source=(ROOT/'scripts/tf-runtime-startup.sh').read_text()
        start=source.index('start_portal() {')
        function=source[start:source.index('\n}\n',start)+3]
        for enabled,success in ((False,True),(True,True),(True,False)):
            with self.subTest(enabled=enabled,success=success),tempfile.TemporaryDirectory() as tmp:
                r=Path(tmp);out=r/'args'
                portal=r/'cammy-portal'
                portal.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >"'+str(out)+'"\n');portal.chmod(0o700)
                helper=r/'shared-capture.sh'
                helper.write_text('if [ "$1" = start ]; then exit '+('0' if success else '1')+'; fi\n')
                command='R="'+str(r)+'"\nSHARED_AUDIO='+str(int(enabled))+'\n'+function+'\nstart_portal\n'
                result=subprocess.run(['dash','-c',command],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                args=out.read_text().splitlines()
                self.assertEqual('--shared-audio' in args,enabled and success)
                self.assertIn('--stock-preview',args)
                self.assertIn('--pid-file',args)


@unittest.skipUnless(shutil.which('cc'),'C compiler required')
class DiagnosticEncodingTests(unittest.TestCase):
    def test_untrusted_text_is_escaped_bounded_and_missing_is_null(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);harness=root/'diagnostic.c';binary=root/'diagnostic'
            harness.write_text('#define main portal_main\n#include "'+str(ROOT/'src/cammy_portal.c')+'"\n#undef main\n'
                'int main(int argc,char **argv) { if(argc!=2)return 2;char out[1600]; diagnostic_text(argv[1],out,sizeof(out));puts(out);return 0;}\n')
            subprocess.run(['cc','-std=c11',str(harness),'-o',str(binary)],check=True,capture_output=True)
            data=root/'data';data.write_bytes(b'"quoted"\\line\n\t'+bytes(range(256)))
            reply=subprocess.check_output([str(binary),str(data)])
            self.assertLess(len(reply),1600)
            self.assertTrue(json.loads(reply).startswith('"quoted"\\line\n\t'))
            missing=subprocess.check_output([str(binary),str(root/'missing')])
            self.assertIsNone(json.loads(missing))
