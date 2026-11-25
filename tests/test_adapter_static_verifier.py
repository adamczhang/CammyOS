"""Focused synthetic regression tests for native/adapters/verify_static_evidence.py (chunk2 AO2).

Evidence class: synthetic in-memory ELF32/ARM byte buffers only. No vendor binary
slices, no camera/ADB/device/network access, and no expectation that the full
unfinished 22-check draft verifier passes.
"""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'native' / 'adapters'))
import verify_static_evidence as vse  # noqa: E402


def _arm_ldr_lit(rd, offset):
    u = 1 if offset >= 0 else 0
    return 0xE51F0000 | (u << 23) | ((rd & 15) << 12) | (abs(offset) & 0xFFF)


def _arm_add_pc(rd, rm):
    return 0xE08F0000 | ((rd & 15) << 12) | (rm & 15)


def _arm_ldr_reg(rd, rn, rm):
    return 0xE7900000 | ((rn & 15) << 16) | ((rd & 15) << 12) | (rm & 15)


def _arm_mov_reg(rd, rm):
    return 0xE1A00000 | ((rd & 15) << 12) | (rm & 15)


def _arm_bl(va, target):
    disp = ((target - (va + 8)) // 4) & 0xFFFFFF
    return 0xEB000000 | disp


def _build_synthetic_elf(text_va=0x80E00, got_va=0x8FB130, plt_va=0x2CB70,
                         got_slot_off=0xF34, got_slot_val=0xAEF9C8,
                         mutex_sym_val=0xAEF9C8, other_sym_val=0xAEF9E0,
                         rel_dyn_entries=(), trylock_target_plt=True,
                         corrupt_got_load_reg=False):
    """Build a minimal little-endian ELF32 ARM image with .text, .plt, .got, .dynsym, .rel.dyn, .rel.plt, .ARM.exidx."""
    shstr = b'\x00.shstrtab\x00.dynstr\x00.dynsym\x00.rel.plt\x00.plt\x00.rel.dyn\x00.got\x00.ARM.exidx\x00.text\x00'
    def sh_name(s):
        return shstr.index(s.encode() + b'\x00')

    dynstr = b'\x00pthread_mutex_trylock\x00g_audio_mutex\x00pa_flag\x00ext_rel_sym\x00'
    def dyn_name(s):
        return dynstr.index(s.encode() + b'\x00')

    # .dynsym entries: 0=null, 1=pthread_mutex_trylock, 2=g_audio_mutex, 3=pa_flag, 4=ext_rel_sym
    dynsym = b''.join([
        struct.pack('<IIIBBH', 0, 0, 0, 0, 0, 0),
        struct.pack('<IIIBBH', dyn_name('pthread_mutex_trylock'), 0, 0, 0x12, 0, 0),
        struct.pack('<IIIBBH', dyn_name('g_audio_mutex'), mutex_sym_val, 24, 0x11, 0, 1),
        struct.pack('<IIIBBH', dyn_name('pa_flag'), other_sym_val, 1, 0x11, 0, 1),
        struct.pack('<IIIBBH', dyn_name('ext_rel_sym'), 0, 4, 0x11, 0, 0),
    ])

    # .rel.plt: index 0 -> sym 1 (pthread_mutex_trylock), stub at plt_va + 20
    rel_plt = struct.pack('<II', got_va + 0x10, (1 << 8) | 22)
    plt_bytes = b'\x00' * 32
    trylock_plt_va = plt_va + 20

    # .rel.dyn
    rel_dyn = b''.join(struct.pack('<II', r_off, (sym_idx << 8) | r_type)
                       for r_off, sym_idx, r_type in rel_dyn_entries)

    # .got: size 0x1000, with got_slot_val written at got_slot_off
    got_size = 0x1000
    got_buf = bytearray(got_size)
    struct.pack_into('<I', got_buf, got_slot_off, got_slot_val)
    struct.pack_into('<I', got_buf, 0xD88, other_sym_val)

    # .text at text_va (0x80E00..0x81280)
    text_size = 0x480
    text_buf = bytearray(text_size)
    def put(va, word):
        struct.pack_into('<I', text_buf, va - text_va, word & 0xFFFFFFFF)

    # Literal pool at 0x81228 and 0x81238
    lit_got_base_va = 0x81228
    lit_slot_off_va = 0x81238
    put(lit_got_base_va, (got_va - (0x80E28 + 8)) & 0xFFFFFFFF)
    put(lit_slot_off_va, got_slot_off)

    # 0x80E1C: ldr sl, [pc, #0x404] -> loads from 0x81228
    put(0x80E1C, _arm_ldr_lit(10, lit_got_base_va - (0x80E1C + 8)))
    # 0x80E28: add sl, pc, sl -> sl = got_va
    put(0x80E28, _arm_add_pc(10, 10))
    # 0x80E7C: ldr r3, [pc, #0x3B4] -> loads got_slot_off from 0x81238
    put(0x80E7C, _arm_ldr_lit(3, lit_slot_off_va - (0x80E7C + 8)))
    # 0x80E80: ldr r3, [sl, r3] (or corrupted base reg if requested)
    base_reg = 9 if corrupt_got_load_reg else 10
    put(0x80E80, _arm_ldr_reg(3, base_reg, 3))
    # 0x80E88: mov r0, r3
    put(0x80E88, _arm_mov_reg(0, 3))
    # 0x80E8C: bl pthread_mutex_trylock@plt (or wrong target)
    call_target = trylock_plt_va if trylock_target_plt else (trylock_plt_va + 12)
    put(0x80E8C, _arm_bl(0x80E8C, call_target))

    # .ARM.exidx: single function start at 0x80E0C
    exidx_va = 0x80DF0
    prel = (0x80E0C - exidx_va) & 0x7FFFFFFF
    exidx_bytes = struct.pack('<II', prel, 1)

    # Layout file offsets
    blobs = [
        ('.shstrtab', 0, shstr, 1),
        ('.dynstr', 0, dynstr, 1),
        ('.dynsym', 0, dynsym, 16),
        ('.rel.plt', 0, rel_plt, 8),
        ('.plt', plt_va, plt_bytes, 4),
        ('.rel.dyn', 0, rel_dyn, 8),
        ('.got', got_va, bytes(got_buf), 4),
        ('.ARM.exidx', exidx_va, exidx_bytes, 8),
        ('.text', text_va, bytes(text_buf), 4),
    ]
    out = bytearray(0x200)
    sec_headers = [struct.pack('<IIIIIIIIII', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    loads = []
    for name, addr, data, entsize in blobs:
        while len(out) % 4:
            out.append(0)
        off = len(out)
        out.extend(data)
        sec_headers.append(struct.pack('<IIIIIIIIII', sh_name(name), 1, 0, addr, off, len(data), 0, 0, 4, entsize))
        if addr:
            loads.append((addr, off, len(data)))

    phoff = 52
    phentsize = 32
    phnum = len(loads)
    for p_va, p_off, p_filesz in loads:
        struct.pack_into('<IIIIIIII', out, phoff + loads.index((p_va, p_off, p_filesz)) * phentsize,
                         1, p_off, p_va, p_va, p_filesz, p_filesz, 5, 4)

    shoff = len(out)
    for sh in sec_headers:
        out.extend(sh)

    # ELF32 header
    out[0:16] = b'\x7fELF\x01\x01\x01\x00' + b'\x00' * 8
    struct.pack_into('<HHIIIIIHHHHHH', out, 16,
                     2, 40, 1, text_va, phoff, shoff, 0, 52, phentsize, phnum, 40, len(sec_headers), 1)
    return bytes(out)


class AdapterStaticVerifierAO2Tests(unittest.TestCase):
    def test_got_loads_resolves_static_dynsym_got_slot_for_ao2(self):
        """Positive case: static non-PIE .got word matching .dynsym resolves g_audio_mutex and passes AO2 predicate."""
        raw = _build_synthetic_elf()
        elf = vse.Elf(raw)
        checker = vse.Checker(elf)
        ok_plt, _ = checker.bl_plt(0x80E8C, 'pthread_mutex_trylock')
        loads = elf.got_loads(0x80E0C, 0x80E90)
        self.assertTrue(ok_plt)
        self.assertEqual(loads.get(0x80E80), 'g_audio_mutex')

    def test_got_loads_prefers_explicit_rel_dyn_symbol_when_present(self):
        """Relocation entry in .rel.dyn takes precedence and is preserved alongside static .got resolution."""
        slot_va = 0x8FB130 + 0xF34
        # The static word also names g_audio_mutex: the explicit relocation must
        # win even when both decoding paths could assign a symbol to this slot.
        raw = _build_synthetic_elf(rel_dyn_entries=[(slot_va, 4, 21)])
        elf = vse.Elf(raw)
        self.assertEqual(elf.got_loads(0x80E0C, 0x80E90).get(0x80E80), 'ext_rel_sym')

    def test_negative_wrong_got_slot_value_does_not_match_g_audio_mutex(self):
        """Negative case: if the .got slot points to pa_flag or an unmapped address, AO2 predicate must fail."""
        raw_wrong_sym = _build_synthetic_elf(got_slot_val=0xAEF9E0)
        elf_wrong_sym = vse.Elf(raw_wrong_sym)
        self.assertEqual(elf_wrong_sym.got_loads(0x80E0C, 0x80E90).get(0x80E80), 'pa_flag')
        self.assertNotEqual(elf_wrong_sym.got_loads(0x80E0C, 0x80E90).get(0x80E80), 'g_audio_mutex')

        raw_unknown = _build_synthetic_elf(got_slot_val=0xDEAD0000)
        elf_unknown = vse.Elf(raw_unknown)
        self.assertIsNone(elf_unknown.got_loads(0x80E0C, 0x80E90).get(0x80E80))

    def test_negative_corrupted_got_base_or_wrong_bl_target_fails_ao2_predicate(self):
        """Negative case: broken register chain at 0x80E80 or wrong BL target at 0x80E8C fails the AO2 check."""
        raw_bad_reg = _build_synthetic_elf(corrupt_got_load_reg=True)
        elf_bad_reg = vse.Elf(raw_bad_reg)
        self.assertIsNone(elf_bad_reg.got_loads(0x80E0C, 0x80E90).get(0x80E80))

        raw_bad_bl = _build_synthetic_elf(trylock_target_plt=False)
        checker_bad_bl = vse.Checker(vse.Elf(raw_bad_bl))
        self.assertFalse(checker_bad_bl.bl_plt(0x80E8C, 'pthread_mutex_trylock')[0])


if __name__ == '__main__':
    unittest.main()
