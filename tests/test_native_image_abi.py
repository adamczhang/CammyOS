"""No camera: bounded actual instruction emulation against saved stock input."""
import importlib.util
import struct
import unittest
from tools.native_image_abi import NativeImageABI,audit,bgra_preview,PIXELS

ENV=importlib.util.find_spec('unicorn') is not None


@unittest.skipUnless(ENV,'Unicorn2.1.4 verification extra required')
class NativeImageABITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.abi=NativeImageABI()
    def test_exact_stock_metadata_pointer_and_depth_primitives(self):
        report=audit()
        self.assertTrue(report['pass'],report)
        self.assertFalse(report['full_LVGL_ABI_verified'])
        self.assertFalse(report['display_written'])
    def test_unknown_and_filesystem_code_paths_are_not_emulated(self):
        with self.assertRaises(ValueError):self.abi.call(0x31d00)
        with self.assertRaises(RuntimeError):self.abi.image_info(b'A:/face.bin\0')
    def test_native_decoder_accepts_absurd_dims_so_our_bounds_are_required(self):
        value=self.abi.image_info(struct.pack('<IIIII',4,0x7fffffff,0x7fffffff,0,PIXELS))
        self.assertEqual(value['return'],1)
        self.assertEqual(value['header'][1:],[0x7fffffff,0x7fffffff])
        with self.assertRaises(ValueError):bgra_preview(b'',0x7fffffff,0x7fffffff)
    def test_bad_format_and_null_pixels_are_refused_by_native(self):
        self.assertEqual(self.abi.image_info(struct.pack('<III',0,356,400))['return'],0)
        self.assertEqual(self.abi.variable_open(data_address=0)['return'],0)


class PixelPackingTests(unittest.TestCase):
    def test_rgb_to_bgra_matches_reported_little_endian_fb_fields(self):
        self.assertEqual(bgra_preview(bytes([1,2,3,40,50,60]),2,1),bytes([3,2,1,255,60,50,40,255]))
        with self.assertRaises(ValueError):bgra_preview(b'\0'*3,357,1)
        with self.assertRaises(ValueError):bgra_preview(b'\0'*2,1,1)
