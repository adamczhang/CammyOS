"""Family tab (c3/family_panel.py) with a fake GTK: mode, Kids override and Calendar access.

Offline: no display, no C3; requests go to a recording client and run synchronously.
"""
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
sys.path.insert(0, str(ROOT))

from tests.test_c3_calendar import fake_gi  # noqa: E402


class Client:
    def __init__(self):
        self.calls = []
        self.replies = {}

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self.replies.get(path, {})


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def family(mode='kids', reason='started', pack=None, available=True, override=False, selectable=True, **extra):
    return {'deployment': 'family', 'mode': mode, 'reason': reason, 'generation': 1, 'cloud': False,
            'selecting': False, 'kids_override': override, 'automatic': not override,
            'face': {'pack': pack, 'known': pack is not None, 'reported': pack if available else None,
                     'available': available, 'age_s': 0.5, 'selectable': selectable}, **extra}


def access(open_=False, reason='started', pin_set=True, wait=0, profile=None):
    return {'open': open_, 'reason': reason, 'generation': 0, 'profile': profile,
            'expires_in_s': 1200 if open_ else None, 'pin_set': pin_set,
            'limit': {'failures': 0, 'retry_after_s': wait}}


class FamilyPanelTests(unittest.TestCase):
    def setUp(self):
        modules = fake_gi()
        repo = modules['gi.repository']
        repo.Gtk.StringList = type('StringList', (), {'new': staticmethod(lambda items: repo.Gtk.Box())})
        repo.GLib.idle_add = staticmethod(lambda fn, *args: fn(*args))
        with patch.dict(sys.modules, modules):
            sys.modules.pop('c3.family_panel', None)
            self.mod = import_module('c3.family_panel')
        self.enterContext(patch.object(self.mod.threading, 'Thread', SyncThread))
        self.client = Client()
        self.panel = self.mod.FamilyPanel(self.client)

    def show(self, state):
        self.panel._show(state)
        return self.panel

    def sensitive(self, button):
        return button.props.get('sensitive')

    def test_mode_text_for_the_three_packs_override_and_unknown(self):
        cases = [
            (family('adult', 'cammy_face', 'cammy'), 'Adult · Cammy face · automatic', 'Shows Cammy'),
            (family('kids', 'kids_face', 'pikachu'), 'Kids · Kids face · automatic', 'Shows Pikachu'),
            (family('kids', 'kids_face', 'fin-fur'), 'Kids · Kids face · automatic', 'Shows Fin and Fur'),
            (family('kids', 'kids_override', 'cammy', override=True), 'Kids · Kids mode set in CCC', 'Shows Cammy'),
            (family('kids', 'started'), 'Kids · waiting for Cammy to report its face · automatic',
             'No face reported yet: Kids until Cammy reports one'),
            (family('kids', 'unknown_face', 'unknown'), 'Kids · unknown face · automatic', 'Shows an unknown face'),
        ]
        for state, mode, face in cases:
            with self.subTest(mode):
                panel = self.show(state)
                self.assertEqual(panel.mode_row.props['subtitle'], mode)
                self.assertEqual(panel.device_row.props['subtitle'], face)

    def test_a_disconnect_says_the_last_known_face_is_kept(self):
        panel = self.show(family('adult', 'cammy_face', 'cammy', available=False))
        self.assertIn('last known', panel.device_row.props['subtitle'])
        self.assertTrue(panel.mode_row.props['subtitle'].startswith('Adult'))

    def test_kids_mode_and_reset_to_automatic_buttons(self):
        panel = self.show(family('adult', 'cammy_face', 'cammy'))
        self.assertEqual((self.sensitive(panel.kids_mode_button), self.sensitive(panel.automatic_button)),
                         (True, False))
        self.client.replies['/v1/family/mode'] = family('kids', 'kids_override', 'cammy', override=True)
        panel.kids_mode_button.emit('clicked')
        self.assertEqual(self.client.calls[-1], ('POST', '/v1/family/mode', {'mode': 'kids'}))
        self.assertEqual((self.sensitive(panel.kids_mode_button), self.sensitive(panel.automatic_button)),
                         (False, True))
        self.client.replies['/v1/family/mode'] = family('adult', 'cammy_face', 'cammy')
        panel.automatic_button.emit('clicked')
        self.assertEqual(self.client.calls[-1], ('POST', '/v1/family/mode', {'mode': 'automatic'}))
        self.assertTrue(panel.mode_row.props['subtitle'].startswith('Adult'))

    def test_face_buttons_follow_the_known_pack(self):
        panel = self.show(family('adult', 'cammy_face', 'cammy'))
        self.assertEqual((self.sensitive(panel.cammy_button), self.sensitive(panel.kids_button)), (False, True))
        panel = self.show(family('kids', 'kids_face', 'pikachu'))
        self.assertEqual((self.sensitive(panel.cammy_button), self.sensitive(panel.kids_button)), (True, False))
        panel = self.show(family('kids', 'kids_face', 'pikachu', selectable=False))
        self.assertFalse(self.sensitive(panel.cammy_button))
        after_lease = family('adult', 'cammy_face', 'cammy')
        after_lease['face']['reported'] = 'pikachu'
        panel = self.show(after_lease)
        self.assertTrue(self.sensitive(panel.cammy_button))
        self.assertTrue(self.sensitive(panel.kids_button))

    def test_calendar_access_is_the_only_pin_control_and_needs_adult(self):
        panel = self.show(family('kids', 'kids_face', 'pikachu', calendar_access=access()))
        self.assertFalse(self.sensitive(panel.unlock_button))
        self.assertEqual(panel.access_row.props['subtitle'], 'Locked: Kids mode')
        self.assertEqual(panel.pin.props.get('title'), 'Calendar access PIN')
        self.assertEqual(panel.unlock_button.props['label'], 'Unlock')
        self.assertTrue(self.sensitive(self.show(family('kids', 'kids_face', 'pikachu',
                                                        calendar_access={**access(), 'desktop': True})).unlock_button))
        panel = self.show(family('adult', 'cammy_face', 'cammy', calendar_access=access()))
        self.assertTrue(self.sensitive(panel.unlock_button))
        self.assertFalse(self.sensitive(panel.lock_button))
        panel.pin.set_text('482913')
        self.client.replies['/v1/calendar-access/unlock'] = access(True, 'pin', profile='desk')
        panel._unlock()
        self.assertEqual(self.client.calls[-1], ('POST', '/v1/calendar-access/unlock', {'pin': '482913'}))
        self.assertEqual(panel.pin.get_text(), '')                     # never kept
        self.assertEqual(panel.access_row.props['subtitle'], 'Open for profile desk · 20 min left')
        self.assertTrue(self.sensitive(panel.lock_button))
        self.assertTrue(panel.mode_row.props['subtitle'].startswith('Adult'))    # unchanged by Calendar
        self.client.replies['/v1/calendar-access/lock'] = access(False, 'operator')
        panel.lock_button.emit('clicked')
        self.assertEqual(self.client.calls[-1][1], '/v1/calendar-access/lock')
        self.assertEqual(panel.access_row.props['subtitle'], 'Locked from this panel')
        self.assertTrue(panel.mode_row.props['subtitle'].startswith('Adult'))

    def test_wrong_pin_wait_and_first_pin(self):
        panel = self.show(family('adult', 'cammy_face', 'cammy', calendar_access=access(wait=30)))
        self.assertFalse(self.sensitive(panel.unlock_button))
        self.assertIn('wait 30 s', panel.message.props['text'])
        panel = self.show(family('adult', 'cammy_face', 'cammy', calendar_access=access(pin_set=False)))
        self.assertFalse(self.sensitive(panel.unlock_button))
        self.assertIn('setup code', panel.pin_expander.props['subtitle'])
        with patch.object(self.mod, '_setup_code', return_value='code'):
            panel.new_pin.set_text('482913')
            panel.again_pin.set_text('482913')
            panel._save_pin()
        self.assertEqual(self.client.calls[-1],
                         ('POST', '/v1/calendar-access/pin', {'new_pin': '482913', 'setup_code': 'code'}))

    def test_standalone_disables_family_controls(self):
        panel = self.show({'deployment': 'adult-standalone', 'mode': 'adult'})
        for button in (panel.kids_mode_button, panel.automatic_button, panel.unlock_button, panel.cammy_button):
            self.assertFalse(self.sensitive(button))


if __name__ == '__main__':
    unittest.main()
