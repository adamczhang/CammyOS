"""host/cammy_persona.py: the face pack decides who Gemini is on Cammy."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import cammy_persona as P  # noqa: E402


class PersonaTests(unittest.TestCase):
    def test_pikachu_introduces_itself_on_cammyos(self):
        text = P.persona('pikachu')
        self.assertIn("answer exactly: \"Hi, I'm Pikachu on CammyOS!\"", text)
        self.assertIn('pack ID pikachu', text)

    def test_fin_and_fur_introduce_themselves_together(self):
        text = P.persona('fin-fur')
        self.assertIn("answer exactly: \"Hi, we're Fin and Fur on CammyOS!\"", text)
        self.assertIn('pack ID fin-fur', text)
        self.assertIn('axolotl', text)
        self.assertIn('tabby cat', text)

    def test_unknown_or_missing_pack_falls_back_to_the_default(self):
        for reported in (None, '', 'eevee', 'PIKACHU'):
            with self.subTest(reported=reported):
                self.assertIn("I'm Cammy on CammyOS!", P.persona(reported))
                self.assertNotIn('Pika!', P.persona(reported))
                self.assertEqual(P.pack(reported)[0], P.DEFAULT_PACK)
                self.assertEqual(P.persona(reported), P.persona(P.DEFAULT_PACK))

    def test_every_pack_has_name_intro_and_style(self):
        for pid, p in P.PACKS.items():
            self.assertTrue(p['name'] and p['who'] and p['style'] and 'CammyOS' in p['intro'], pid)


if __name__ == '__main__':
    unittest.main()
