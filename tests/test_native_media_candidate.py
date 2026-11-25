"""Offline-only exact-firmware patch guards. Vendor bytes are never committed."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_candidate',ROOT/'tools/build_native_media_candidate.py')
candidate=importlib.util.module_from_spec(spec);spec.loader.exec_module(candidate)
SOURCE=ROOT.parent/'Cammy Bot/M105 investigation/work/firmware-rootfs/usr/bin/sdvcam'


class BranchTests(unittest.TestCase):
    def test_signed_branch_range_alignment_and_link_bit(self):
        for source,target in [(0xAC5D0,0xAC46C),(0xAC638,0x8E2E20),(0x8E2E30,0xADD60)]:
            self.assertEqual(candidate.branch_target(source,candidate.branch(source,target)),target)
        for source,target in [(1,4),(0,3),(0,1<<26)]:
            with self.assertRaises(ValueError):candidate.branch(source,target)
        with self.assertRaises(ValueError):candidate.branch_target(0,0xEB000000)


@unittest.skipUnless(SOURCE.is_file(),'Private preserved exact vendor firmware required')
class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.original=SOURCE.read_bytes()

    def test_changes_are_restricted_and_original_remains_intact(self):
        before=hashlib.sha256(self.original).hexdigest()
        result,report=candidate.patch_image(self.original)
        self.assertEqual(SOURCE.read_bytes(),self.original)
        self.assertEqual(hashlib.sha256(self.original).hexdigest(),before)
        ranges=[(int(r['offset'],16),r['bytes']) for r in report['allowed_modified_ranges']]
        differences=[i for i,(a,b) in enumerate(zip(self.original,result)) if a!=b]
        self.assertTrue(differences);self.assertEqual(len(result),len(self.original))
        self.assertTrue(all(any(start<=i<start+size for start,size in ranges) for i in differences))
        self.assertFalse(report['deployed'])
        # Writable LOAD header and entry point remain unchanged.
        self.assertEqual(result[180:212],self.original[180:212])
        self.assertEqual(result[24:28],self.original[24:28])

    def test_wrong_build_and_repatch_are_rejected(self):
        changed=bytearray(self.original);changed[-1]^=1
        with self.assertRaisesRegex(ValueError,'hash'):candidate.patch_image(changed)
        patched,_=candidate.patch_image(self.original)
        with self.assertRaisesRegex(ValueError,'hash'):candidate.patch_image(patched)

    def test_lifecycle_targets_and_tail_cleanup_preserve_layout(self):
        result,_=candidate.patch_image(self.original)
        for source,target in [(candidate.CONNECT_TAIL,candidate.MEDIA_INIT_BODY),
                              (candidate.DISCONNECT_TAIL,candidate.TRAMPOLINE_ADDRESS)]:
            instruction=struct.unpack_from('<I',result,source-candidate.TEXT_BASE)[0]
            self.assertEqual(candidate.branch_target(source,instruction),target)
        words=struct.unpack_from('<6I',result,candidate.TRAMPOLINE_OFFSET)
        self.assertEqual(words[:4],(0xE3A00000,0xE5C40124,0xE2840004,0xE59FE000))
        self.assertEqual(candidate.branch_target(candidate.TRAMPOLINE_ADDRESS+16,words[4]),candidate.MEDIA_DESTROY)
        self.assertEqual(words[5],candidate.RESUME)
        size=struct.unpack_from('<I',result,candidate.LOAD_HEADER+16)[0]
        self.assertEqual(size,candidate.TRAMPOLINE_OFFSET+24)
        self.assertLess(size,0x8D3030)
