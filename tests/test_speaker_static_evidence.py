"""Instruction-only fixtures; contains no saved vendor binary bytes beyond words
needed to encode the stated branch/register assertions."""
import unittest
from native.audio.verify_speaker_path import POLL, branch_target, checks


def bl(address, target):
    return 0xEB000000 | (((target - address - 8) // 4) & 0xFFFFFF)


class FakeElf:
    plt = {'pthread_mutex_trylock': 0x2CB84, 'pthread_mutex_unlock': 0x2CA94,
           'usleep': 0x2D2C8}

    def __init__(self):
        self.words = {**POLL, 0x64DAC: 0xE3520C01, 0x64DB0: 0x03A03001,
                      0x64DB4: 0x058030D8, 0x64DB8: 0x0AFFFFF8,
                      0x656C8: 0xE30A3C44, 0x656CC: 0xE58D3034, 0x81150: 0xE59D000C}
        for a, target in ((0x80E8C, 0x2CB84), (0x81050, 0x655EC),
                          (0x656D8, 0x24DBAC), (0x656F4, 0x24F2BC),
                          (0x65724, 0x24F2BC), (0x6572C, 0x24D934),
                          (0x65734, 0x24D710), (0x81154, 0x2CA94)):
            self.words[a] = bl(a, target)
        self.mutex = 'g_audio_mutex'

    def word(self, address):
        return self.words[address]

    def got_loads(self, lo, hi):
        assert (lo, hi) == (0x80E0C, 0x80E90)
        return {0x80E80: self.mutex}


class SpeakerEvidenceTests(unittest.TestCase):
    def test_positive_fixture(self):
        self.assertTrue(all(checks(FakeElf()).values()))

    def test_every_instruction_mutation_detected(self):
        for address in FakeElf().words:
            with self.subTest(address=hex(address)):
                elf = FakeElf()
                elf.words[address] = 0xE1A00000  # ARM nop
                self.assertFalse(all(checks(elf).values()))

    def test_wrong_mutex_rejected(self):
        elf = FakeElf()
        elf.mutex = 'unrelated'
        self.assertFalse(checks(elf)['speaker_mutex_load'])

    def test_call_decoder_rejects_plain_branch(self):
        self.assertIsNone(branch_target(0xEA000000, 0x1000))
        self.assertEqual(branch_target(bl(0x81050, 0x655EC), 0x81050), 0x655EC)
