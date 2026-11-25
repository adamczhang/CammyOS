import unittest
from native.audio.audit_native_profile import WORDS,checks,sdk_layout_compatible
from tests.test_speaker_static_evidence import bl


class Fixture:
    plt={'pthread_cond_wait':0x2D1F0}
    def __init__(self):
        self.words={**WORDS,0x24D8F4:0x005E5234,0x24DB64:0x005E4F78,0x24DD14:0x005E4D94}
        self.strings={self.words[0x24D8F4]+0x24D74C+8+477:'AW_MPI_AO_StartChn',
                      self.words[0x24DB64]+0x24D97C+8+496:'AW_MPI_AO_StopChn',
                      self.words[0x24DD14]+0x24DBEC+8+514:'AW_MPI_AO_SendFrame'}
        for a,t in ((0x24DAB4,0x130440),(0x13047C,0x2D1F0),(0x6572C,0x24D934),(0x65734,0x24D710)):
            self.words[a]=bl(a,t)
        self.globals={0x8028C:'pa_flag',0x802A8:'g_play_state'}
    def word(self,a):return self.words[a]
    def cstr(self,a):return self.strings.get(a)
    def got_loads(self,lo,hi):
        assert (lo,hi)==(0x8027C,0x802D0)
        return self.globals


class NativeAudioProfileTests(unittest.TestCase):
    def test_positive_slice(self):self.assertTrue(all(checks(Fixture()).values()))
    def test_each_instruction_or_reference_change_detected(self):
        for address in Fixture().words:
            with self.subTest(address=hex(address)):
                f=Fixture();f.words[address]=0xE1A00000
                self.assertFalse(all(checks(f).values()))
    def test_names_and_pa_global_are_exact(self):
        f=Fixture();f.strings={a:'incorrect' for a in f.strings}
        self.assertFalse(checks(f)['native_names'])
        f=Fixture();f.globals[0x802A8]='unrelated'
        self.assertFalse(checks(f)['pa_timer_globals'])
    def test_public_header_is_incompatible(self):
        self.assertFalse(sdk_layout_compatible({'mpAddr':12,'first_word':'samplerate'}))
        self.assertFalse(sdk_layout_compatible({'mpAddr':8,'first_word':'samplerate'}))
        self.assertTrue(sdk_layout_compatible({'mpAddr':8,'first_word':'bitwidth'}))
