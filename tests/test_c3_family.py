"""Family mode and Calendar access (c3/family.py), local play (c3/local_play.py) and their C3 wiring.

Offline: fake clocks, temp folders, a fake portal state; no camera, Gemini or provider runs.
"""
import asyncio
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))

from c3 import family as F  # noqa: E402
from c3 import local_play as P  # noqa: E402

try:                         # aiohttp and the Gemini SDK live in .venv-live
    from aiohttp import ClientTimeout
    from aiohttp.test_utils import TestClient, TestServer
    from c3 import api, listener, provider_health, service
    from c3.resident_brain import ResidentBrain
except ImportError:
    api = None
needs_live = unittest.skipUnless(api, 'aiohttp/google-genai not installed (use .venv-live)')

PIN = '482913'
CAMMY = {'face_pack': 'cammy', 'face_pack_index': 2, 'boot_id': 'boot-a', 'version': '0.8.0', 'face_live': True,
         'face_grant_end': 'none', 'owner': 'test-owner', 'lease_remaining_seconds': 10, 'face_pack_granted': True}
PIKACHU = dict(CAMMY, face_pack='pikachu', face_pack_index=0, face_pack_granted=False)
FIN_FUR = dict(CAMMY, face_pack='fin-fur', face_pack_index=1, face_pack_granted=False)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def pins(folder, wall=None):
    return F.PinStore(folder, clock=wall or Clock(1_700_000_000.0), scrypt_n=2 ** 10)


def ready_store(folder, wall=None):
    store = pins(folder, wall)
    store.prepare()
    store.set_pin(PIN, setup_code=store.setup_code_file.read_text())
    return store


class Pins(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'family'
        self.wall = Clock(1_700_000_000.0)

    def test_first_pin_needs_the_private_setup_code_and_never_unlocks(self):
        store = pins(self.dir, self.wall)
        store.prepare()
        code = store.setup_code_file
        self.assertEqual(stat.S_IMODE(code.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)
        for bad in (None, '', 'guess'):
            with self.assertRaises(F.FamilyError) as cm:
                store.set_pin(PIN, setup_code=bad)
            self.assertEqual(cm.exception.code, 'wrong_setup_code')
        self.assertFalse(store.has_pin())
        store.set_pin(PIN, setup_code=code.read_text())
        self.assertTrue(store.has_pin())
        self.assertFalse(code.exists())                    # used once
        self.assertEqual(stat.S_IMODE((self.dir / 'pin.json').stat().st_mode), 0o600)
        self.assertNotIn(PIN, (self.dir / 'pin.json').read_text())
        self.assertTrue(store.verify(PIN))

    def test_weak_pins_are_refused_and_a_change_needs_the_current_pin(self):
        store = ready_store(self.dir, self.wall)
        for bad in ('12345', '1234567890123', '111111', '12a456', '١٢٣٤٥٦', 123456):
            with self.subTest(bad=bad), self.assertRaises(F.FamilyError) as cm:
                store.set_pin(bad, current_pin=PIN)
            self.assertEqual(cm.exception.code, 'bad_pin')
        with self.assertRaises(F.FamilyError) as cm:
            store.set_pin('246810', current_pin='000000')
        self.assertEqual(cm.exception.code, 'wrong_pin')
        with self.assertRaises(F.FamilyError):
            store.set_pin('246810', setup_code='anything')   # a setup code means nothing after setup
        store.set_pin('246810', current_pin=PIN)
        self.assertTrue(store.verify('246810'))
        self.assertFalse(store.verify(PIN))

    def test_rate_limit_counts_every_attempt_and_survives_a_restart(self):
        store = ready_store(self.dir, self.wall)
        for _ in range(F.FREE_FAILURES):
            self.assertFalse(store.verify('000000'))
        with self.assertRaises(F.FamilyError) as cm:          # locked: even the right PIN waits
            store.verify(PIN)
        self.assertEqual((cm.exception.code, cm.exception.status), ('rate_limited', 429))
        restarted = pins(self.dir, self.wall)                  # a new C3 process
        with self.assertRaises(F.FamilyError):
            restarted.verify(PIN)
        self.wall.t += F.LOCKOUT_BASE_S + 1
        self.assertFalse(restarted.verify('000000'))           # 7th failure: the wait doubles
        self.wall.t += F.LOCKOUT_BASE_S + 1
        with self.assertRaises(F.FamilyError):
            restarted.verify(PIN)
        self.wall.t += F.LOCKOUT_BASE_S + 1
        self.assertTrue(restarted.verify(PIN))
        self.assertEqual(restarted.limit_state(), {'failures': 0, 'retry_after_s': 0.0})

    def test_removing_the_pin_makes_a_new_setup_code(self):
        store = ready_store(self.dir, self.wall)
        (self.dir / 'pin.json').unlink()
        with self.assertRaises(F.FamilyError):
            store.set_pin(PIN, setup_code='')
        store.set_pin(PIN, setup_code=store.setup_code_file.read_text())
        self.assertTrue(store.verify(PIN))

    def test_the_lockout_is_capped(self):
        store = ready_store(self.dir, self.wall)
        for _ in range(40):
            try:
                store.verify('000000')
            except F.FamilyError:
                self.wall.t += F.LOCKOUT_CAP_S
        self.assertLessEqual(store.limit_state()['retry_after_s'], F.LOCKOUT_CAP_S)
        self.wall.t -= 10 * F.LOCKOUT_CAP_S                    # clock went back
        self.assertLessEqual(store.limit_state()['retry_after_s'], F.LOCKOUT_CAP_S)


class FaceReport(unittest.TestCase):
    def test_known_packs_unknown_packs_and_nothing_to_go_on(self):
        self.assertEqual(F.face_report(CAMMY), ('cammy', 'none'))
        self.assertEqual(F.face_report(PIKACHU), ('pikachu', 'none'))
        self.assertEqual(F.face_report(FIN_FUR), ('fin-fur', 'none'))
        self.assertEqual(F.face_report(dict(PIKACHU, face_grant_end='lease')), ('pikachu', 'lease'))
        self.assertEqual(F.face_report({'face_pack': 'pikachu'})[0], 'pikachu')      # older firmware: no index
        for state in (dict(CAMMY, face_pack='robot'), dict(CAMMY, face_pack='unknown'), dict(CAMMY, face_pack_index=1)):
            self.assertEqual(F.face_report(state), ('unknown', 'unknown_pack'))
        for state in (None, 'x', {}, {'version': '0.7.0'},
                      dict(CAMMY, face_live=False), dict(CAMMY, face_pack='')):
            self.assertEqual(F.face_report(state), (None, 'unavailable'), state)

    def test_kids_packs_are_pikachu_and_fin_and_fur(self):
        self.assertEqual(F.KIDS_PACKS, {'pikachu', 'fin-fur'})
        self.assertEqual(F.KNOWN_PACKS['cammy'], 2)


class Policy(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.events = []
        self.policy = self.make()

    def make(self):
        policy = F.FamilyPolicy(self.dir / 'family', clock=Clock())
        policy.add_listener(self.events.append)
        return policy

    def test_kids_until_a_known_report_and_cloud_never_in_family(self):
        p = self.policy
        self.assertEqual((p.mode, p.reason, p.generation), (F.KIDS, 'started', 0))
        for cap in ('brain', 'look_back', 'transcript', 'memory', 'instructions', 'providers', 'outward',
                    'anything-new'):
            self.assertFalse(p.allows(cap), cap)
        for cap in F.KIDS_CAPABILITIES:
            self.assertTrue(p.allows(cap), cap)
        for state in (None, {'version': '0.7.0'}, dict(CAMMY, face_live=False)):   # offline, unknown: still Kids
            p.observe(state)
            self.assertEqual(p.mode, F.KIDS)
        p.observe(dict(CAMMY))
        self.assertTrue(p.allows('brain'))
        self.assertFalse(p.allows(F.CLOUD))                   # Adult in family mode: still no Gemini

    def test_the_three_packs(self):
        p = self.policy
        for state, mode, reason in ((CAMMY, F.ADULT, 'cammy_face'), (PIKACHU, F.KIDS, 'kids_face'),
                                    (CAMMY, F.ADULT, 'cammy_face'), (FIN_FUR, F.KIDS, 'kids_face'),
                                    (PIKACHU, F.KIDS, 'kids_face')):
            p.observe(dict(state))
            self.assertEqual((p.mode, p.reason), (mode, reason), state['face_pack'])
        self.assertEqual([e['mode'] for e in self.events], ['adult', 'kids', 'adult', 'kids'])
        self.assertEqual(p.generation, 4)                    # Pikachu to Fin and Fur is no change

    def test_cammy_is_adult_at_once_with_no_pin_grant_or_lease(self):
        state = dict(CAMMY, owner=None, lease_remaining_seconds=0, face_grant_end='lease')   # sleep art kept
        state.pop('face_pack_granted', None)
        self.policy.observe(state)
        self.assertEqual(self.policy.mode, F.ADULT)
        self.assertEqual(self.policy.snapshot()['face']['pack'], 'cammy')

    def test_adult_hold_file_holds_adult_on_any_pack_but_not_over_kids_override(self):
        (self.dir / 'family').mkdir(parents=True, exist_ok=True)
        (self.dir / 'family' / 'adult-hold').write_text('')
        p = self.make()
        self.assertEqual((p.mode, p.reason), (F.ADULT, 'adult_hold'))
        for state in (PIKACHU, FIN_FUR, dict(CAMMY, face_pack='robot'), None):
            p.observe(dict(state) if state else state)
            self.assertEqual((p.mode, p.reason), (F.ADULT, 'adult_hold'))
        self.assertTrue(p.snapshot()['adult_hold'])
        p.set_kids_override(True)
        self.assertEqual((p.mode, p.reason), (F.KIDS, 'kids_override'))
        p.set_kids_override(False)
        self.assertEqual(p.mode, F.ADULT)
        self.assertFalse(self.policy.adult_hold)              # made before the file: face rule

    def test_an_unknown_pack_is_kids_and_closes_adult(self):
        self.policy.observe(dict(CAMMY))
        self.policy.observe(dict(CAMMY, face_pack='robot'))
        self.assertEqual((self.policy.mode, self.policy.reason), (F.KIDS, 'unknown_face'))

    def test_a_brief_disconnect_keeps_the_last_known_mode(self):
        p = self.policy
        p.observe(dict(CAMMY))
        generation = p.generation
        for state in (None, dict(CAMMY, face_live=False),
                      dict(PIKACHU, face_grant_end='lease')):       # the grant lapsed with the lease
            p.observe(state)
            self.assertEqual((p.mode, p.generation), (F.ADULT, generation), state)
        snap = p.snapshot()['face']
        self.assertEqual((snap['pack'], snap['reported'], snap['available']), ('cammy', 'pikachu', True))
        p.observe(None)
        self.assertFalse(p.snapshot()['face']['available'])
        p.observe(PIKACHU)                       # Kids also stays Kids through a disconnect
        p.observe(None)
        self.assertEqual(p.mode, F.KIDS)

    def test_a_physical_pick_ccc_end_camera_mode_or_reboot_is_a_real_change(self):
        for end in ('physical', 'ccc', 'camera', 'none', None):
            with self.subTest(end):
                self.policy.observe(dict(CAMMY))
                self.policy.observe(dict(FIN_FUR, face_grant_end=end))
                self.assertEqual(self.policy.mode, F.KIDS)

    def test_a_lease_end_never_opens_adult_from_kids_or_unknown(self):
        for first in (None, PIKACHU, dict(CAMMY, face_pack='robot')):
            with self.subTest(first=first):
                p = F.FamilyPolicy(None)
                p.observe(first)
                p.observe(dict(PIKACHU, face_grant_end='lease'))
                self.assertEqual(p.mode, F.KIDS)

    def test_kids_override_holds_kids_on_any_pack_and_is_saved(self):
        p = self.policy
        p.observe(dict(CAMMY))
        self.events.clear()
        snap = p.set_kids_override(True)
        self.assertEqual((snap['mode'], snap['reason'], snap['kids_override'], snap['automatic']),
                         ('kids', 'kids_override', True, False))
        self.assertEqual([e['mode'] for e in self.events], ['kids'])
        for state in (PIKACHU, CAMMY, FIN_FUR, CAMMY):          # the face changes; the override stays
            p.observe(dict(state))
            self.assertEqual(p.mode, F.KIDS)
        mode_file = self.dir / 'family' / 'mode.json'
        self.assertEqual(stat.S_IMODE(mode_file.stat().st_mode), 0o600)
        again = self.make()                                      # C3 restarts
        self.assertTrue(again.kids_override)
        again.observe(dict(CAMMY))
        self.assertEqual((again.mode, again.reason), (F.KIDS, 'kids_override'))
        snap = again.set_kids_override(False)                    # Reset to automatic
        self.assertEqual((snap['mode'], snap['reason'], snap['automatic']), ('adult', 'cammy_face', True))
        self.assertFalse(self.make().kids_override)

    def test_reset_to_automatic_never_opens_adult_on_a_kids_or_unknown_pack(self):
        for state in (None, PIKACHU, FIN_FUR, dict(CAMMY, face_pack='robot')):
            with self.subTest(state=state):
                p = self.make()
                p.observe(state)
                p.set_kids_override(True)
                p.set_kids_override(False)
                self.assertEqual(p.mode, F.KIDS)

    def test_an_unreadable_saved_override_fails_closed_and_an_unsaved_reset_changes_nothing(self):
        folder = self.dir / 'family'
        folder.mkdir()
        (folder / 'mode.json').write_text('{not json')
        p = self.make()
        p.observe(dict(CAMMY))
        self.assertEqual((p.kids_override, p.mode), (True, F.KIDS))
        with patch.object(F, '_write_private', side_effect=OSError('disk')), self.assertRaises(F.FamilyError) as cm:
            p.set_kids_override(False)
        self.assertEqual((cm.exception.code, p.mode), ('save_failed', F.KIDS))
        with patch.object(F, '_write_private', side_effect=OSError('disk')):
            p.set_kids_override(True)                           # turning Kids on still works
        self.assertTrue(p.kids_override)

    def test_standalone_deployment_is_not_family(self):
        p = F.FamilyPolicy(None, deployment=F.STANDALONE)
        self.assertTrue(p.allows(F.CLOUD))
        self.assertTrue(p.allows('brain'))
        self.assertEqual(p.mode, F.ADULT)
        with self.assertRaises(F.FamilyError):
            p.set_kids_override(True)
        self.assertEqual(p.snapshot()['deployment'], 'adult-standalone')
        with self.assertRaises(ValueError):
            F.FamilyPolicy(None, deployment='cloud-kids')

    def test_snapshot_has_no_pin_state(self):
        self.policy.observe(dict(CAMMY))
        snap = self.policy.snapshot()
        for key in ('pin_set', 'limit', 'expires_in_s', 'device'):
            self.assertNotIn(key, snap)


class CalendarAccessTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clock = Clock()
        self.key = ('desk', 1)
        self.policy = F.FamilyPolicy(self.dir / 'family')
        self.access = F.CalendarAccess(ready_store(self.dir / 'pins'), self.policy, profile=lambda: self.key,
                                       clock=self.clock, access_s=600)
        self.policy.observe(dict(CAMMY))

    def test_needs_adult_mode_a_profile_and_the_pin(self):
        a = self.access
        self.assertFalse(a.allows('desk'))
        with self.assertRaises(F.FamilyError) as cm:
            a.unlock('000000')
        self.assertEqual(cm.exception.code, 'wrong_pin')
        self.policy.observe(dict(PIKACHU))
        with self.assertRaises(F.FamilyError) as cm:
            a.unlock(PIN)
        self.assertEqual(cm.exception.code, 'kids_mode')
        self.policy.observe(dict(CAMMY))
        self.key = None
        with self.assertRaises(F.FamilyError) as cm:
            a.unlock(PIN)
        self.assertEqual(cm.exception.code, 'no_profile')
        self.key = ('desk', 1)
        with self.assertRaises(F.FamilyError) as cm:
            a.unlock(PIN, 'garage')
        self.assertEqual(cm.exception.code, 'profile_changed')
        snap = a.unlock(PIN, 'desk')
        self.assertEqual((snap['open'], snap['profile'], snap['expires_in_s']), (True, 'desk', 600))
        self.assertTrue(a.allows('desk'))
        for other in ('garage', '', None, 7, ['desk']):
            self.assertFalse(a.allows(other), other)
        with self.assertRaises(F.FamilyError) as cm:
            a.unlock(PIN)
        self.assertEqual(cm.exception.code, 'already_open')
        self.assertNotIn(PIN, repr(snap))

    def test_each_close_has_a_new_generation_and_adult_mode_stays(self):
        changes = {
            'kids_mode': lambda: self.policy.observe(dict(FIN_FUR)),
            'kids_mode ': lambda: self.policy.set_kids_override(True),
            'profile_changed': lambda: setattr(self, 'key', ('garage', 2)),
            'profile_changed ': lambda: setattr(self, 'key', ('desk', 3)),       # same profile reselected
            'expired': lambda: setattr(self.clock, 't', self.clock.t + 601),
            'operator': lambda: self.access.lock('operator'),
        }
        for reason, change in changes.items():
            with self.subTest(reason):
                self.key = ('desk', 1)
                self.policy.set_kids_override(False)
                self.policy.observe(dict(CAMMY))
                self.access.unlock(PIN)
                grant = self.access.grant('desk')
                self.assertTrue(self.access.current('desk', grant))
                family = (self.policy.mode, self.policy.generation)
                change()
                self.assertFalse(self.access.allows('desk'))
                self.assertFalse(self.access.current('desk', grant))
                self.assertEqual(self.access.snapshot()['reason'], reason.strip())
                if not reason.startswith('kids'):
                    self.assertEqual((self.policy.mode, self.policy.generation), family)   # Adult kept

    def test_aba_mode_and_profile_never_revive_an_old_grant(self):
        a = self.access
        a.unlock(PIN)
        grant = a.grant('desk')
        self.policy.observe(dict(PIKACHU))                    # Adult -> Kids -> Adult
        self.policy.observe(dict(CAMMY))
        self.assertFalse(a.current('desk', grant))
        self.assertFalse(a.allows('desk'))                    # reopening needs the PIN again
        a.unlock(PIN)
        grant = a.grant('desk')
        self.key = ('garage', 2)
        self.key = ('desk', 3)                                # desk -> garage -> desk, unobserved
        self.assertFalse(a.current('desk', grant))
        a.unlock(PIN)
        grant = a.grant('desk')
        a.lock()
        a.unlock(PIN)
        self.assertFalse(a.current('desk', grant))
        self.assertFalse(a.current('desk', True))

    def test_fails_closed_on_errors_and_outside_family(self):
        self.access.unlock(PIN)
        self.access._profile = Mock(side_effect=RuntimeError('brain blip'))
        self.assertFalse(self.access.allows('desk'))
        standalone = F.FamilyPolicy(None, deployment=F.STANDALONE)
        access = F.CalendarAccess(ready_store(self.dir / 'other'), standalone, profile=lambda: ('desk', 1))
        self.assertFalse(access.allows('desk'))
        with self.assertRaises(F.FamilyError) as cm:
            access.unlock(PIN)
        self.assertEqual(cm.exception.code, 'not_family')

    def test_a_change_during_the_pin_check_opens_nothing(self):
        def verify(pin):
            self.policy.observe(dict(PIKACHU))
            self.policy.observe(dict(CAMMY))
            return True
        self.access.pins.verify = verify
        with self.assertRaises(F.FamilyError) as cm:
            self.access.unlock(PIN)
        self.assertEqual(cm.exception.code, 'changed')
        self.assertFalse(self.access.allows('desk'))


class FaceSelection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.policy = F.FamilyPolicy(None)
        self.policy.observe(dict(CAMMY))
        self.order = []
        self.reads = []

    async def teardown(self, event):
        self.order.append(('teardown', self.policy.mode))
        self.policy.observe(dict(CAMMY))                  # a watch read before the change: still Kids
        self.assertEqual(self.policy.mode, F.KIDS)

    async def apply(self, pack):
        self.order.append(('apply', pack, self.policy.mode))

    async def read(self):
        return self.reads.pop(0) if self.reads else dict(PIKACHU, face_grant_end='ccc')

    async def test_a_kids_choice_closes_adult_and_stops_paths_before_the_change(self):
        self.reads = [dict(CAMMY), None, dict(FIN_FUR, face_grant_end='ccc')]
        result = await F.select_face_pack(self.policy, 'child', apply=self.apply, read_state=self.read,
                                          teardown=self.teardown, sleep=AsyncMock())
        self.assertEqual(self.order, [('teardown', 'kids'), ('apply', 'child', 'kids')])
        self.assertEqual((result['confirmed'], result['face_pack'], self.policy.mode), (True, 'fin-fur', 'kids'))
        self.assertFalse(self.policy.snapshot()['selecting'])

    async def test_choosing_cammy_opens_adult_at_once_without_a_pin(self):
        self.policy.observe(dict(PIKACHU))
        self.reads = [dict(PIKACHU), dict(CAMMY)]
        result = await F.select_face_pack(self.policy, 'cammy', apply=self.apply, read_state=self.read,
                                          teardown=self.teardown, sleep=AsyncMock())
        self.assertEqual(self.order, [('apply', 'cammy', 'kids')])                # no teardown first
        self.assertEqual((result['family']['mode'], self.policy.allows('brain')), ('adult', True))

    async def test_choosing_cammy_with_the_kids_override_stays_kids(self):
        self.policy.set_kids_override(True)
        self.reads = [dict(CAMMY)]
        result = await F.select_face_pack(self.policy, 'cammy', apply=self.apply, read_state=self.read,
                                          teardown=self.teardown, sleep=AsyncMock())
        self.assertEqual(result['family']['mode'], 'kids')

    async def test_no_read_back_is_not_confirmed(self):
        clock = Clock()

        async def sleep(_):
            clock.t += 5
        self.policy.observe(dict(PIKACHU))
        self.reads = [None] * 10
        with self.assertRaises(F.FamilyError) as cm:
            await F.select_face_pack(self.policy, 'cammy', apply=self.apply, read_state=self.read,
                                     teardown=self.teardown, sleep=sleep, timeout=12, clock=clock)
        self.assertEqual(cm.exception.code, 'not_confirmed')
        self.assertEqual(self.policy.mode, 'kids')
        self.assertFalse(self.policy.snapshot()['selecting'])

    async def test_unsupported_firmware_bad_ids_and_overlap_change_nothing(self):
        for pack, apply, code in (('fin-fur', None, 'unsupported'), ('../etc', self.apply, 'bad_face_pack')):
            with self.subTest(code), self.assertRaises(F.FamilyError) as cm:
                await F.select_face_pack(self.policy, pack, apply=apply, read_state=self.read, teardown=self.teardown)
            self.assertEqual(cm.exception.code, code)
        self.assertEqual((self.order, self.policy.mode), ([], 'adult'))
        self.policy.begin_selection('cammy')
        with self.assertRaises(F.FamilyError) as cm:
            await F.select_face_pack(self.policy, 'child', apply=self.apply, read_state=self.read,
                                     teardown=self.teardown)
        self.assertEqual(cm.exception.code, 'face_selection')
        self.policy.end_selection()


class GatedTranscriptTests(unittest.TestCase):
    def test_writes_only_when_allowed(self):
        inner = Mock(count=3)
        allowed = [False]
        t = F.GatedTranscript(inner, lambda: allowed[0])
        t.add('system', 'follow True')
        inner.add.assert_not_called()
        allowed[0] = True
        t.add('system', 'x')
        inner.add.assert_called_once_with('system', 'x')
        self.assertEqual(t.count, 3)


class RouteClasses(unittest.TestCase):
    def test_cloud_routes_and_fail_closed_default(self):
        for path in ('/v1/message', '/v1/say', '/v1/sleep', '/v1/wake', '/v1/look-around', '/v1/capture'):
            self.assertEqual(F.route_class('POST', path), F.CLOUD, path)
        for method, path in (('GET', '/v1/transcript'), ('POST', '/v1/look'), ('GET', '/v1/preview/history'),
                             ('GET', '/v1/brain'), ('POST', '/v1/brain/message'), ('GET', '/v1/providers'),
                             ('PUT', '/v1/instructions/agent'), ('POST', '/v1/inject'), ('GET', '/v1/new-thing'),
                             ('POST', '/v1/family/unlock'), ('POST', '/v1/family/lock'), ('DELETE', '/v1/status'),
                             ('POST', '/v1/calendar/query'), ('POST', '/v1/calendar/connect'),
                             ('POST', '/v1/calendar/test'), ('GET', '/v1/calendar/result')):
            self.assertEqual(F.route_class(method, path), F.ADULT, path)
        self.assertEqual(F.route_class('POST', '/v1/follow/'), F.PUBLIC)
        for method, path in (('GET', '/v1/family'), ('POST', '/v1/family/mode'), ('POST', '/v1/family/face-pack'),
                             ('GET', '/v1/calendar-access'), ('POST', '/v1/calendar-access/unlock'),
                             ('POST', '/v1/calendar-access/lock'), ('POST', '/v1/calendar-access/pin'),
                             ('POST', '/v1/calendar/cancel'), ('POST', '/v1/calendar/disconnect')):
            self.assertEqual(F.route_class(method, path), F.PUBLIC, path)


class Play(unittest.TestCase):
    def test_games_run_and_old_sessions_are_dropped(self):
        play = P.LocalPlay(rng=P.random.Random(4))
        out = play.start('guess')
        secret = play.game.secret
        self.assertIn('one to ten', out['reply'])
        self.assertEqual(play.answer('is it %s' % ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
                                                    'eight', 'nine', 'ten'][secret])['done'], True)
        session = play.session
        play.start('rps')
        self.assertEqual(play.answer('seven', session=session)['reply'], 'Rock, paper or scissors?')  # late: dropped
        reply = play.answer('scissors please')['reply']
        self.assertTrue('beats' in reply or 'both picked' in reply, reply)
        self.assertEqual(play.game.round, 1)
        play.reset('family change')
        self.assertEqual(play.state()['game'], None)
        with self.assertRaises(ValueError):
            play.start('chat-with-cloud')

    def test_sums_and_parsing(self):
        play = P.LocalPlay(rng=P.random.Random(1))
        play.start('sums')
        for _ in range(P.Sums.QUESTIONS):
            play.answer(str(play.game.a + play.game.b))
        self.assertIn('5 out of 5', play.reply)
        self.assertEqual(P.number_in('I think FOUR'), 4)
        self.assertIsNone(P.move_in('rock or paper'))

    def test_voice_speech_is_dropped_after_a_reset(self):
        speaker = Mock()
        jobs = []

        class Job:
            def __init__(self, value):
                self.value, self.cancelled = value, False
                self.thread = SimpleNamespace(join=lambda: None)
                jobs.append(self)

            def poll(self):
                return (self.value, None)

            def cancel(self):
                self.cancelled = True

        speech = SimpleNamespace(synthesize=lambda text: Job(b'\x01\x00' * 10),
                                 transcribe=lambda pcm: Job(b'{"text": "five"}'))

        async def scenario():
            voice = P.LocalVoice(speaker, speech=speech)
            play = P.LocalPlay(voice=voice, rng=P.random.Random(2))
            play.start('guess')
            play.reset('adult unlocked')              # before the speech job finishes
            await asyncio.sleep(0.05)
            speaker.say.assert_not_called()
            self.assertTrue(jobs[0].cancelled)
            play.start('guess')
            await asyncio.sleep(0.05)
            speaker.say.assert_called_once()
            Ev = SimpleNamespace
            voice.on_speech(Ev(kind='start', pcm=b'\0' * 12000), play)
            voice.on_speech(Ev(kind='end'), play)
            play.reset('lock')                        # transcription arrives after the reset
            await asyncio.sleep(0.05)
            self.assertIsNone(play.kind)
        asyncio.run(scenario())


class FakeEyes:
    wanted, restarts = False, 0

    def __init__(self):
        self.look = AsyncMock(return_value=b'JPEG')
        self.run = AsyncMock()
        self.stop = Mock()

    def configure_profile_history(self, getter):
        self.getter = getter

    def history_status(self):
        return {'profile': None, 'frames': 0}


@needs_live
class FamilyService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(service, 'DATA', self.data))
        self.credentials = self.enterContext(patch.object(service, 'credentials', return_value=('portal', None)))
        self.gemini = self.enterContext(patch.object(service.genai, 'Client', side_effect=AssertionError('cloud')))
        self.state = dict(CAMMY)
        up = asyncio.Event()
        up.set()
        self.body = Mock(up=up, state={'lease': 'held'}, epoch=1)
        self.body._current.side_effect = lambda epoch: self.body.up.is_set() and epoch == self.body.epoch
        self.body.cammy.agent = CAMMY['owner']
        self.body.cammy._boot_binding = CAMMY['boot_id']
        self.body.cammy.face_pack_status.side_effect = lambda: {
            'boot_id': self.state['boot_id'], 'version': self.state['version'],
            'link': {'owner': self.state['owner'], 'lease_remaining_seconds': 10, 'face_lease': True},
            'pack': {'live': True, 'shown': self.state['face_pack'], 'ccc_available': self.state.get('ccc_available', True),
                     'ccc_grant': {'held': self.state['face_pack_granted'], 'last_end': self.state['face_grant_end']}},
        }
        self.enterContext(patch.object(service.bodymod, 'Body', return_value=self.body))
        self.enterContext(patch.object(service.cammy_capture, 'Native', return_value=None))
        self.enterContext(patch.object(service.bodymod, 'Face'))
        self.eyes = FakeEyes()
        self.enterContext(patch.object(service.bodymod, 'Eyes', return_value=self.eyes))
        self.enterContext(patch.object(listener, 'Listener', return_value=Mock(stats={}, speaking=False)))
        self.enterContext(patch.object(provider_health, 'ProviderHealth', return_value=Mock()))
        self.enterContext(patch.object(service.voicemod, 'Voice', side_effect=AssertionError('Gemini voice')))
        self.enterContext(patch.object(service, 'ResidentVoice', side_effect=AssertionError('Gemini voice')))
        owner = ResidentBrain(self.data / 'brain')
        await owner.create_profile('desk', 'Desk', 'codex')
        await owner.select_profile('desk')
        await owner.close()
        args = SimpleNamespace(brain='resident', duplex='off', portal='test', sweep=1, level='normal', tail=1.6,
                               model='model', voice='voice', idle=45, api_port=0, greet=True, minutes=0)
        self.c3 = service.C3(args)                   # no deployment named: family
        self.addAsyncCleanup(self.c3.brain.close)
        self.c3.calendar_access.pins = ready_store(self.data / 'family')
        self.auth = {'Authorization': 'Bearer secret'}

    async def client(self, desktop=None):
        client = TestClient(TestServer(api.make_app(self.c3, 'secret', desktop)), timeout=ClientTimeout(total=3))
        self.addAsyncCleanup(client.close)
        await client.start_server()
        return client

    async def test_family_reads_no_gemini_key_builds_no_client_and_follows_the_face(self):
        c3 = self.c3
        self.assertEqual(c3.deployment, 'family')
        self.credentials.assert_called_once_with(cloud=False)
        self.assertIsNone(c3.client)
        self.gemini.assert_not_called()
        self.assertIsNone(c3.active_profile())                 # first start: Kids until a known report
        self.assertEqual(self.eyes.getter()[0], 'desk')
        self.assertFalse(self.eyes.history_recording())
        status = c3.status()
        for key in ('brain', 'memory', 'transcript_entries'):
            self.assertNotIn(key, status)
        self.assertNotIn('history', status['eyes'])
        self.assertEqual(status['family']['mode'], 'kids')
        c3.transcript.add('system', 'kids line')
        self.assertEqual(c3.transcript.count, 0)
        self.assertEqual(c3.selected_profile()[0], 'desk')

        await c3.family_refresh_access()                       # Cammy reports the Cammy face: Adult, no PIN
        self.assertEqual(c3.family.mode, 'adult')
        self.assertEqual(c3.active_profile()[0], 'desk')
        self.assertTrue(self.eyes.history_recording())
        self.assertIn('brain', c3.status())
        self.assertFalse(c3.calendar_access_allows('desk'))     # Calendar is a separate opt-in
        await asyncio.to_thread(c3.calendar_access.unlock, PIN)
        self.assertTrue(c3.calendar_access_allows('desk'))
        self.assertFalse(c3.calendar_access_allows('garage'))
        grant = c3.calendar_access_grant('desk')

        self.body.up.clear()                                   # brief disconnect: nothing flips
        self.assertIsNone(await c3.read_device_state())
        await c3.family_refresh_access()
        self.body.up.set()
        self.state = dict(CAMMY, owner='another-client')        # another client holds the lease
        self.assertIsNotNone(await c3.read_device_state())
        self.body.epoch += 1                                   # reconnect on the same boot
        self.state = dict(PIKACHU, face_grant_end='lease')     # the grant lapsed with the lease
        await c3.family_refresh_access()
        self.assertEqual(c3.family.mode, 'adult')
        self.assertTrue(c3.calendar_access_current('desk', grant))

        self.state = dict(CAMMY, boot_id='another-boot')       # not the camera C3 is bound to
        self.assertIsNone(await c3.read_device_state())

        self.state = dict(CAMMY)
        await c3.brain.select_profile('desk')                  # reselected: Calendar closes, Adult stays
        self.assertFalse(c3.calendar_access_current('desk', grant))
        self.assertEqual(c3.family.mode, 'adult')
        await asyncio.to_thread(c3.calendar_access.unlock, PIN)
        self.state = dict(FIN_FUR, face_grant_end='physical')  # the physical menu changed it
        await c3.family_refresh_access()
        self.assertEqual(c3.family.mode, 'kids')
        self.assertFalse(c3.calendar_access_allows('desk'))
        self.assertIsNone(c3.active_profile())

    async def test_older_firmware_reports_its_kids_pack_and_never_opens_adult(self):
        self.body.cammy.face_pack_status.side_effect = service.bodymod.CammyError(404, 'not here')
        self.body.cammy.state.return_value = {'boot_id': CAMMY['boot_id'], 'version': '0.7.0',
                                              'face_pack': 'pikachu', 'face_pack_shown': 'pikachu'}
        state = await self.c3.read_device_state()
        self.assertEqual((state['face_pack'], state['face_pack_select']), ('pikachu', False))
        await self.c3.family_refresh_access()
        self.assertEqual((self.c3.family.mode, self.c3.family.reason), ('kids', 'kids_face'))

    async def test_family_cli_rejects_t3_and_defaults_to_family(self):
        with self.assertRaises(ValueError):
            service.C3(SimpleNamespace(brain='t3', duplex='off', portal='t', deployment='family'))
        with patch.object(service, 'setup_logging'), patch.object(service.asyncio, 'run') as run, \
                patch.object(service, 'C3') as made, self.assertRaises(SystemExit):
            service.main(['--brain', 't3'])
        run.assert_not_called()
        made.assert_not_called()

    async def test_run_starts_no_gemini_voice_and_teardown_cancels_the_brain(self):
        c3 = self.c3
        loop = asyncio.get_running_loop()
        handlers, runner = {}, SimpleNamespace(cleanup=AsyncMock())

        async def start_worker(name, *_):
            if name == 'moves':
                handlers[service.signal.SIGINT]()

        with patch.object(service.bodymod, 'Speaker', return_value=Mock(active=False, chunks=0, seconds=0)), \
             patch.object(service.api, 'serve', new=AsyncMock(return_value=runner)), \
             patch.object(service, 'supervise', side_effect=start_worker) as supervise, \
             patch.object(loop, 'add_signal_handler', side_effect=handlers.__setitem__):
            await asyncio.wait_for(c3.run(), timeout=3)
        workers = [call.args[0] for call in supervise.await_args_list]
        self.assertIn('family-watch', workers)
        self.assertFalse({'voice', 'hear', 'brain-in'} & set(workers))      # no mic leaves; no Gemini
        self.assertIsNone(c3.voice)
        self.assertIsNotNone(c3.play)
        self.assertTrue((self.data / 'family/pin.json').exists())

        job = SimpleNamespace(cancelled=False)
        c3.brain._job = job
        c3.brain.cancel = AsyncMock()
        c3.brain.replies.put_nowait('late adult answer')
        c3.inject += b'\0' * 64
        c3.play.start('guess')
        await c3.family_teardown({'reason': 'device_offline'})
        c3.brain.cancel.assert_awaited_once()
        self.assertTrue(c3.brain.replies.empty())
        self.assertEqual((bytes(c3.inject), c3.play.kind), (b'', None))
        c3.brain._job = None

    async def test_api_kids_adult_cloud_late_results_and_calendar_access(self):
        c3 = self.c3
        c3.play = P.LocalPlay()
        self.state = dict(PIKACHU)
        client = await self.client()
        self.assertEqual((await client.get('/v1/family')).status, 401)            # token still required
        for method, path in (('GET', '/v1/transcript'), ('POST', '/v1/look'), ('GET', '/v1/brain'),
                             ('GET', '/v1/preview/history'), ('GET', '/v1/instructions'), ('GET', '/v1/providers'),
                             ('GET', '/v1/no-such-route'), ('POST', '/v1/family/unlock')):
            with self.subTest(path=path):
                reply = await client.request(method, path, headers=self.auth)
                self.assertEqual(reply.status, 403)
                self.assertEqual((await reply.json())['code'], 'kids_mode')
        self.eyes.look.assert_not_awaited()
        play = await client.post('/v1/play/start', json={'game': 'sums'}, headers=self.auth)
        self.assertEqual(play.status, 200)
        reply = await client.post('/v1/calendar-access/unlock', json={'pin': PIN}, headers=self.auth)
        self.assertEqual((reply.status, (await reply.json())['code']), (409, 'kids_mode'))
        self.assertEqual((await client.post('/v1/calendar-access/lock', headers=self.auth)).status, 200)

        self.state = dict(CAMMY)
        await c3.family_refresh_access()                       # the Cammy face: Adult with no PIN
        family = await (await client.get('/v1/family', headers=self.auth)).json()
        self.assertEqual((family['mode'], family['reason'], family['automatic']), ('adult', 'cammy_face', True))
        self.assertEqual(family['calendar_access']['open'], False)
        self.assertEqual((await client.get('/v1/transcript', headers=self.auth)).status, 200)
        for path in ('/v1/message', '/v1/say', '/v1/wake'):                  # Adult: still no Gemini
            reply = await client.post(path, json={'text': 'hi'}, headers=self.auth)
            self.assertEqual((reply.status, (await reply.json())['code']), (503, 'cloud_disabled'))

        # Calendar access: the bearer token alone never opens it; locking it keeps Adult.
        for body in ({}, {'pin': None}, {'pin': '000000'}):
            reply = await client.post('/v1/calendar-access/unlock', json=body, headers=self.auth)
            self.assertEqual((reply.status, (await reply.json())['code']), (403, 'wrong_pin'))
        reply = await client.post('/v1/calendar-access/unlock', json={'pin': PIN, 'profile': 'desk'}, headers=self.auth)
        self.assertEqual((reply.status, (await reply.json())['profile']), (200, 'desk'))
        reply = await client.post('/v1/calendar-access/lock', headers=self.auth)
        self.assertEqual((await reply.json())['open'], False)
        self.assertEqual(c3.family.mode, 'adult')

        for body in ({'mode': 'adult'}, {}):
            reply = await client.post('/v1/family/mode', json=body, headers=self.auth)
            self.assertEqual((reply.status, (await reply.json())['code']), (400, 'bad_mode'))
        reply = await client.post('/v1/family/mode', json={'mode': 'kids'}, headers=self.auth)
        self.assertEqual(((await reply.json())['mode'], (await reply.json())['kids_override']), ('kids', True))
        self.assertEqual((await client.get('/v1/transcript', headers=self.auth)).status, 403)
        reply = await client.post('/v1/family/mode', json={'mode': 'automatic'}, headers=self.auth)
        self.assertEqual((await reply.json())['mode'], 'adult')

        async def slow_look(wait):
            c3.family.set_kids_override(True)          # Kids starts while the frame is on its way
            return b'JPEG'
        self.eyes.look = slow_look
        reply = await client.post('/v1/look', headers=self.auth)
        self.assertEqual(reply.status, 403)
        self.assertIn('withheld', (await reply.json())['error'])

        self.state['ccc_available'] = False
        reply = await client.post('/v1/family/face-pack', json={'id': 'cammy'}, headers=self.auth)
        self.assertEqual((reply.status, (await reply.json())['code']), (501, 'unsupported'))  # stock portal

    async def test_desktop_stays_available_in_kids_without_enabling_camera_actions(self):
        self.state = dict(PIKACHU)
        client = await self.client(desktop='desktop-secret')
        headers = {**self.auth, 'X-Cammy-Desktop-Token': 'desktop-secret'}
        forged = {**self.auth, 'X-Cammy-Desktop-Token': 'wrong'}
        self.eyes.history_status = lambda: {'profile': 'desk', 'frames': 0, 'capacity_bytes': 250_000_000}
        for path in ('/v1/brain', '/v1/instructions', '/v1/transcript', '/v1/preview/history'):
            self.assertEqual((await client.get(path, headers=headers)).status, 200, path)
            self.assertEqual((await client.get(path, headers=forged)).status, 403, path)
        status = await (await client.get('/v1/status', headers=headers)).json()
        self.assertEqual(status['family']['mode'], 'kids')
        self.assertIn('brain', status)
        self.assertFalse(status['eyes']['history']['recording_allowed'])
        self.assertEqual((await client.post('/v1/look', headers=headers)).status, 403)
        self.assertEqual((await client.post('/v1/wake', headers=headers)).status, 503)
        response = await client.post('/v1/calendar-access/unlock', json={'pin': PIN}, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['profile'], 'desk')
        anonymous_access = await (await client.get('/v1/calendar-access', headers=self.auth)).json()
        self.assertIsNone(anonymous_access['profile'])
        self.assertFalse(anonymous_access['open'])

if __name__ == '__main__':
    unittest.main()
