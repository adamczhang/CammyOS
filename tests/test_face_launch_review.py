"""GUI-profile boot review: real shell on confined synthetic trees, never hardware."""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from tools.build_face_launch_review import profile_helper,proposal,CLASS,GATE,BASE_SHA
from boot_launch.generator import HELPER_PROPOSAL_PATH
from boot_launch.model import BootLaunchFakeTree,run_relocated_s01app_branch_shell
from tests.test_boot_selection import make_synthetic_allow,make_synthetic_card,BOOT_1,BOOT_2


class FaceLaunchReviewTests(unittest.TestCase):
    def make_tree(self,root,faults=(),mode='select',klass=CLASS):
        allow=make_synthetic_allow().replace(b':status-only\n',(':'+klass+'\n').encode())
        files=make_synthetic_card(klass=klass)
        files['face.so']=files.pop('libcammy_native_status.so')
        return BootLaunchFakeTree.build(root,boot_id=BOOT_1,enabled_content='mode='+mode+'\n',
                    allow_bytes=allow,card_files=files,faults=faults)

    def test_source_scope_and_new_class_are_explicit(self):
        self.assertEqual(hashlib.sha256(HELPER_PROPOSAL_PATH.read_bytes()).hexdigest(),BASE_SHA)
        base,text=proposal()
        self.assertIn(('SELECT_GATE='+GATE).encode(),text)
        self.assertIn(b'LIBROOT=/tmp/cammy-face',text)
        self.assertIn(b'LIBNAME=face.so',text)
        self.assertEqual(sum(r['line_count'] for r in base.preserved_ranges),150)
        self.assertIn(CLASS.encode()+b') ;;',profile_helper())

    def test_selected_once_and_next_boot_stock_without_tf_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=self.make_tree(Path(tmp)/'tree');text=proposal()[1].decode()
            first=run_relocated_s01app_branch_shell(text,tree,select_gate=GATE)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertIn('candidate',str(tree.launched_entries()))
            self.assertEqual(len(tree.launched_entries()),1)
            self.assertIn('0',str(tree.candidate_core_logs()))
            tree.reboot(BOOT_2)
            calls_before=len(tree.calls())
            second=run_relocated_s01app_branch_shell(text,tree,select_gate=GATE)
            self.assertEqual(second.returncode,0,second.stderr)
            self.assertEqual(tree.launched_entries().count('candidate-stub'),1)
            self.assertEqual(tree.launched_entries()[-1],'sdvcam-stub')
            self.assertEqual(len(tree.launched_entries()),2)
            self.assertNotIn('mount ',str(tree.calls()[calls_before:]))

    def test_disabled_config_and_old_class_stay_stock(self):
        for mode,klass in [('disabled',CLASS),('select','status-only')]:
            with self.subTest(mode=mode,klass=klass),tempfile.TemporaryDirectory() as tmp:
                tree=self.make_tree(Path(tmp)/'tree',mode=mode,klass=klass)
                r=run_relocated_s01app_branch_shell(proposal()[1].decode(),tree,select_gate=GATE)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertNotIn('candidate',str(tree.launched_entries()))

    def test_detach_or_corrupt_copy_does_not_launch_candidate(self):
        for faults in [('tf-umount-fail','tf-umount-lazy-fail'),('copy-corrupt-1',),('sync-fail-1',)]:
            with self.subTest(faults=faults),tempfile.TemporaryDirectory() as tmp:
                tree=self.make_tree(Path(tmp)/'tree',faults=faults)
                r=run_relocated_s01app_branch_shell(proposal()[1].decode(),tree,select_gate=GATE)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertNotIn('candidate',str(tree.launched_entries()))
                self.assertNotIn('VIOLATION',str(tree.calls()))

    def test_selection_succeeds_without_device_rmdir_applet(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=self.make_tree(Path(tmp)/'tree')
            missing=tree.ctl/'bin/rmdir'
            missing.write_text('#!/bin/sh\nexit 127\n');missing.chmod(0o700)
            result=run_relocated_s01app_branch_shell(proposal()[1].decode(),tree,select_gate=GATE)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(tree.launched_entries(),['candidate-stub'])

    def test_empty_directory_primitive_preserves_hidden_entries_and_symlinks(self):
        helper=profile_helper().decode()
        body=helper.split('cammy_remove_empty_dir() {',1)[1].split('\n}\n',1)[0]
        function='cammy_remove_empty_dir() {'+body+'\n}\n'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ['empty','hidden','dangling']:(root/name).mkdir()
            (root/'hidden/.keep').write_text('keep')
            (root/'dangling/.link').symlink_to(root/'missing')
            (root/'dirlink').symlink_to(root/'hidden',target_is_directory=True)
            for name,expected in [('empty',0),('hidden',1),('dangling',1),('dirlink',1)]:
                result=subprocess.run(['dash','-c',function+'cammy_remove_empty_dir "$1"','sh',str(root/name)])
                self.assertEqual(result.returncode,expected,name)
            self.assertEqual((root/'hidden/.keep').read_text(),'keep')
            self.assertTrue((root/'dangling/.link').is_symlink())
            self.assertTrue((root/'dirlink').is_symlink())
