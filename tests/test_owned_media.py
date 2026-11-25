"""WP3 owned-media core: lifecycle, ownership and late-completion tests.

Every test drives native/media/media_fixture.c (the C core plus the explicit
fake backend) through a line script with an injected clock. Nothing here
touches a camera, socket, device node or vendor binary. Passing shows the
supervision logic only: real native cleanup, lock ownership and frame delivery
are NOT proven by these fake-backend results.

Set CAMMY_MEDIA_FIXTURE to a JSON command list (e.g. a qemu-arm prefix) to run
the same suite against another build; see native/media/run_arm_tests.py.
SPDX-License-Identifier: MIT
"""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST_FIXTURE = ROOT / 'build/native-media-owned/host/media-fixture'


def fixture_command():
    env = os.environ.get('CAMMY_MEDIA_FIXTURE')
    if env:
        return json.loads(env)
    subprocess.run(['make', '-s', '-C', str(ROOT / 'native/media'), 'host'], check=True,
                   stdout=subprocess.DEVNULL)
    return [str(HOST_FIXTURE)]


CMD = None


def run(*lines):
    """Run a script; return parsed result lines as (tag, dict)."""
    global CMD
    if CMD is None:
        CMD = fixture_command()
    proc = subprocess.run(CMD, input='\n'.join(lines) + '\n', capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f'fixture exit {proc.returncode}: {proc.stdout[-400:]} {proc.stderr[-400:]}')
    out = []
    for raw in proc.stdout.splitlines():
        tag, *rest = raw.split()
        fields = dict(item.split('=', 1) for item in rest if '=' in item)
        if tag == 'status':
            # Honesty invariant on every status line, whatever the scenario.
            assert fields['video'] == 'unavailable', raw
            assert 'video_physical' not in fields['flags'], raw
        out.append((tag, fields))
    return out


def flags(status):
    return set(status['flags'].split(',')) - {'-'}


def last(results, tag):
    return [f for t, f in results if t == tag][-1]


def tagged(results, tag):
    return [f for t, f in results if t == tag]


OWN = ['init kind=fake', 'acquire ctl=1 rid=1 lease=10000']
ACTIVE = OWN + ['start ctl=1 rid=2 gen=1', 'step', 'complete start ok', 'step']


class StatesAndReadiness(unittest.TestCase):
    def test_accept_queue_dispatch_active_are_separate(self):
        r = run(*OWN, 'start ctl=1 rid=2 gen=1', 'status', 'step', 'status',
                'event listener', 'step', 'status', 'complete start ok', 'step', 'status',
                'event sdp', 'step', 'status', 'event frame', 'step', 'status')
        reply = tagged(r, 'reply')[1]
        self.assertEqual((reply['st'], reply['phase'], reply['sess']), ('accepted', 'queued', '1'))
        st = tagged(r, 'status')
        self.assertEqual(st[0]['phase'], 'queued')
        self.assertEqual(st[0]['dispatched'], '0')       # accepted is not dispatched
        self.assertEqual(st[1]['phase'], 'start_dispatched')
        self.assertEqual(flags(st[2]), set())             # listener before start completion not reported
        self.assertEqual(st[3]['phase'], 'active')
        self.assertEqual(flags(st[3]), {'listener'})      # active + listener, no SDP, no frame
        self.assertEqual(flags(st[4]), {'listener', 'sdp'})
        self.assertEqual(flags(st[5]), {'listener', 'sdp', 'frame_fresh', 'synthetic'})
        self.assertEqual((st[5]['video'], st[5]['vr']), ('unavailable', 'fake_backend'))

    def test_frame_freshness_window_and_synthetic_label(self):
        r = run(*ACTIVE, 'event frame synthetic=0', 'step', 'status', 'adv 2000', 'status', 'adv 1', 'status',
                'event frame at=+500', 'step', 'status')
        st = tagged(r, 'status')
        self.assertIn('frame_fresh', flags(st[0]))
        self.assertIn('synthetic', flags(st[0]))  # a fake frame is labeled synthetic even if it claims otherwise
        self.assertIn('frame_fresh', flags(st[1]))
        self.assertNotIn('frame_fresh', flags(st[2]))
        self.assertIn('frame_fresh', flags(st[3]))  # future timestamp clamped to now, not trusted beyond it

    def test_happy_stop_reports_ack_then_confirmed_cleanup(self):
        r = run(*ACTIVE, 'stop ctl=1 rid=3 gen=1', 'status', 'step', 'status', 'complete cleanup ok', 'step',
                'status', 'fake')
        reply = tagged(r, 'reply')[-1]
        self.assertEqual(reply['st'], 'stop_queued')
        st = tagged(r, 'status')
        self.assertEqual(st[0]['phase'], 'stop_queued')
        self.assertNotIn('last_cleanup_done', flags(st[0]))
        self.assertEqual(st[1]['phase'], 'cleanup_dispatched')
        self.assertEqual((st[2]['phase'], st[2]['end'], st[2]['owner']), ('none', 'stopped', 'held'))
        self.assertIn('last_cleanup_done', flags(st[2]))
        fake = last(r, 'fake')
        self.assertEqual((fake['running'], fake['effects_cleaned']), ('0', '1'))


class Controllers(unittest.TestCase):
    def test_conflicting_and_duplicate_controllers(self):
        r = run(*OWN, 'acquire ctl=2 rid=1', 'acquire ctl=1 rid=2', 'start ctl=2 rid=2 gen=1',
                'start ctl=1 rid=3 gen=1', 'start ctl=1 rid=4 gen=1', 'step 3', 'status')
        rep = tagged(r, 'reply')
        self.assertEqual(rep[1]['st'], 'owned_by_other')
        self.assertEqual(rep[1]['gen'], '0')             # generation is not disclosed to a non-holder
        self.assertEqual(rep[2]['st'], 'already_owner')
        self.assertEqual(rep[3]['st'], 'not_owner')
        self.assertEqual(rep[4]['st'], 'accepted')
        self.assertEqual(rep[5]['st'], 'already_started')
        self.assertEqual(last(r, 'step')['starts'], '1')  # duplicate produced no second dispatch

    def test_replayed_and_stale_start_rejected(self):
        r = run(*OWN, 'start ctl=1 rid=5 gen=1', 'stop ctl=1 rid=6 gen=1', 'start ctl=1 rid=5 gen=1',
                'start ctl=1 rid=4 gen=1', 'release ctl=1 rid=7 gen=1', 'acquire ctl=1 rid=8',
                'start ctl=1 rid=9 gen=1', 'step 2', 'status')
        rep = tagged(r, 'reply')
        self.assertEqual([x['st'] for x in rep[1:]], ['accepted', 'stop_not_started', 'replayed', 'replayed', 'ok',
                                                      'ok', 'stale_generation'])
        self.assertEqual(rep[6]['gen'], '2')
        st = last(r, 'status')
        self.assertEqual((st['dispatched'], st['replays'], st['stale_gen']), ('0', '2', '1'))

    def test_renew_and_lease_expiry(self):
        r = run('init kind=fake', 'acquire ctl=1 rid=1 lease=1000', 'adv 900', 'renew ctl=1 rid=2 gen=1 lease=1000',
                'adv 900', 'status', 'renew ctl=2 rid=1 gen=1 lease=1000', 'adv 100', 'step', 'status',
                'start ctl=1 rid=3 gen=1')
        st = tagged(r, 'status')
        self.assertEqual(st[0]['owner'], 'held')
        self.assertEqual(tagged(r, 'reply')[2]['st'], 'not_owner')
        self.assertEqual((st[1]['owner'], st[1]['oreason']), ('free', 'lease_expired'))
        self.assertEqual(tagged(r, 'reply')[3]['st'], 'stale_generation')


class BeforeDispatch(unittest.TestCase):
    def test_request_deadline_expires_before_dispatch(self):
        r = run(*OWN, 'start ctl=1 rid=2 gen=1 dl=+100', 'adv 100', 'step', 'status')
        st = last(r, 'status')
        self.assertEqual((st['phase'], st['end'], st['dispatched'], st['cancelled']),
                         ('none', 'expired_before_dispatch', '0', '1'))
        self.assertEqual(last(r, 'step')['starts'], '0')
        self.assertIn('last_cleanup_done', flags(st))   # never dispatched: nothing to clean

    def test_lease_expiry_before_dispatch(self):
        r = run('init kind=fake', 'acquire ctl=1 rid=1 lease=1000', 'start ctl=1 rid=2 gen=1 dl=+5000',
                'adv 1000', 'step', 'status')
        st = last(r, 'status')
        self.assertEqual((st['owner'], st['end'], st['dispatched']), ('free', 'lease_expired', '0'))
        self.assertEqual(last(r, 'step')['starts'], '0')

    def test_stop_before_dispatch_never_executes(self):
        r = run(*OWN, 'start ctl=1 rid=2 gen=1', 'stop ctl=1 rid=3 gen=1', 'step 5', 'status',
                'start ctl=1 rid=4 gen=1', 'step', 'status')
        self.assertEqual(tagged(r, 'reply')[2]['st'], 'stop_not_started')
        self.assertEqual(tagged(r, 'step')[0]['starts'], '0')
        self.assertEqual(tagged(r, 'status')[0]['end'], 'stopped_before_dispatch')
        self.assertEqual(tagged(r, 'status')[1]['phase'], 'start_dispatched')   # a fresh session works
        self.assertEqual(tagged(r, 'status')[1]['sess'], '2')

    def test_host_loss_cancels_queued_start(self):
        r = run(*OWN, 'start ctl=1 rid=2 gen=1', 'lost ctl=1', 'step 3', 'status', 'acquire ctl=2 rid=1')
        st = last(r, 'status')
        self.assertEqual((st['owner'], st['oreason'], st['end'], st['dispatched']),
                         ('free', 'controller_lost', 'controller_lost', '0'))
        self.assertEqual(last(r, 'reply')['st'], 'ok')
        self.assertEqual(last(r, 'reply')['gen'], '2')

    def test_clock_regression_does_not_reopen_deadline(self):
        r = run(*OWN, 'start ctl=1 rid=2 gen=1 dl=+100', 'now 1200', 'status', 'now 900', 'step', 'status')
        st = last(r, 'status')
        self.assertEqual((st['now'], st['end'], st['dispatched']), ('1200', 'expired_before_dispatch', '0'))


class InFlight(unittest.TestCase):
    def test_host_loss_while_active_drains_before_new_owner(self):
        r = run(*ACTIVE, 'lost ctl=1', 'acquire ctl=2 rid=1', 'step', 'status', 'complete cleanup ok', 'step',
                'status', 'acquire ctl=2 rid=2', 'start ctl=1 rid=3 gen=1')
        rep = tagged(r, 'reply')
        self.assertEqual(rep[2]['st'], 'cleanup_pending')
        st = tagged(r, 'status')
        self.assertEqual((st[0]['owner'], st[0]['phase']), ('draining', 'cleanup_dispatched'))
        self.assertEqual((st[1]['owner'], st[1]['end']), ('free', 'controller_lost'))
        self.assertEqual((rep[3]['st'], rep[3]['gen']), ('ok', '2'))
        self.assertEqual(rep[4]['st'], 'stale_generation')

    def test_native_stall_stays_uncertain_and_late_success_needs_new_cleanup(self):
        r = run('init kind=fake start_to=1000', 'acquire ctl=1 rid=1 lease=30000', 'start ctl=1 rid=2 gen=1',
                'step', 'adv 999', 'step', 'status', 'adv 1', 'step', 'status', 'start ctl=1 rid=3 gen=1',
                'acquire ctl=2 rid=1', 'step', 'complete cleanup ok', 'step', 'status',
                'complete start ok tok=1', 'fake', 'step', 'status', 'step', 'status', 'complete cleanup ok', 'step',
                'status', 'fake')
        st = tagged(r, 'status')
        self.assertEqual(st[0]['phase'], 'start_dispatched')
        self.assertEqual(st[1]['phase'], 'cleanup_dispatched')     # timeout → cleanup issued, effect unknown
        self.assertEqual(st[1]['start_timeouts'], '1')
        self.assertIn('start_unresolved', flags(st[1]))
        rep = tagged(r, 'reply')
        self.assertEqual((rep[2]['st'], rep[3]['st']), ('cleanup_pending', 'owned_by_other'))
        # Cleanup ack while the start is unresolved is not final.
        self.assertEqual(st[2]['phase'], 'cleanup_uncertain')
        self.assertNotIn('last_cleanup_done', flags(st[2]))
        self.assertEqual(tagged(r, 'fake')[0]['running'], '1')     # the late start had a real (fake) effect
        self.assertEqual(st[3]['phase'], 'cleanup_dispatched')     # a second cleanup is required
        self.assertEqual(st[3]['late'], '1')
        self.assertEqual(st[3]['cleanups'], '2')
        self.assertEqual((st[5]['phase'], st[5]['end']), ('none', 'start_timeout'))
        self.assertIn('last_cleanup_done', flags(st[5]))
        self.assertEqual(last(r, 'fake')['running'], '0')

    def test_late_start_after_stop_is_cleaned_not_activated(self):
        r = run(*OWN, 'start ctl=1 rid=2 gen=1', 'step', 'stop ctl=1 rid=3 gen=1', 'step 3', 'status',
                'event listener', 'complete start ok', 'step', 'status', 'complete cleanup ok', 'step', 'status',
                'fake')
        self.assertEqual(tagged(r, 'reply')[-1]['st'], 'stop_queued')
        st = tagged(r, 'status')
        self.assertEqual((st[0]['phase'], st[0]['cleanups']), ('start_dispatched', '0'))
        self.assertIn('stop_requested', flags(st[0]))
        self.assertEqual(st[1]['phase'], 'cleanup_dispatched')
        self.assertNotIn('listener', flags(st[1]))
        self.assertEqual(st[1]['ev_ignored'], '1')
        self.assertEqual(st[2]['end'], 'stopped')
        self.assertEqual(last(r, 'fake')['effects_cleaned'], '1')

    def test_fifo_late_start_before_cleanup_ack_needs_one_cleanup(self):
        r = run('init kind=fake start_to=500', 'acquire ctl=1 rid=1 lease=30000', 'start ctl=1 rid=2 gen=1', 'step',
                'adv 500', 'step', 'complete start ok tok=1', 'complete cleanup ok', 'step', 'status')
        st = last(r, 'status')
        self.assertEqual((st['phase'], st['cleanups'], st['late']), ('none', '1', '1'))
        self.assertIn('last_cleanup_done', flags(st))

    def test_lease_expiry_while_dispatched(self):
        r = run('init kind=fake', 'acquire ctl=1 rid=1 lease=1000', 'start ctl=1 rid=2 gen=1', 'step', 'adv 1000',
                'step', 'status', 'complete start ok', 'step', 'status', 'complete cleanup ok', 'step', 'status')
        st = tagged(r, 'status')
        self.assertEqual((st[0]['owner'], st[0]['phase']), ('draining', 'start_dispatched'))
        self.assertEqual(st[1]['phase'], 'cleanup_dispatched')
        self.assertEqual((st[2]['owner'], st[2]['end']), ('free', 'lease_expired'))

    def test_start_failure_variants(self):
        failed = run(*OWN, 'start ctl=1 rid=2 gen=1', 'step', 'complete start fail', 'step', 'status')
        st = last(failed, 'status')
        self.assertEqual((st['phase'], st['cleanups']), ('cleanup_dispatched', '1'))   # partial effects possible
        bogus = run(*OWN, 'start ctl=1 rid=2 gen=1', 'step', 'complete start bogus', 'step', 'status')
        self.assertEqual(last(bogus, 'status')['phase'], 'cleanup_dispatched')
        none = run(*OWN, 'start ctl=1 rid=2 gen=1', 'step', 'complete start noeffect', 'step', 'status')
        st = last(none, 'status')
        self.assertEqual((st['phase'], st['end'], st['cleanups']), ('none', 'start_no_effect', '0'))

    def test_submit_refused_has_no_effect(self):
        r = run(*OWN, 'fake refuse_start=1', 'start ctl=1 rid=2 gen=1', 'step', 'status')
        st = last(r, 'status')
        self.assertEqual((st['phase'], st['end'], st['dispatched']), ('none', 'submit_refused', '0'))


class Cleanup(unittest.TestCase):
    def test_cleanup_failure_retries_then_faults(self):
        r = run('init kind=fake attempts=2 retry=500', 'acquire ctl=1 rid=1 lease=30000', 'start ctl=1 rid=2 gen=1',
                'step', 'complete start ok', 'step', 'stop ctl=1 rid=3 gen=1', 'step', 'complete cleanup fail',
                'step', 'status', 'adv 499', 'step', 'status', 'adv 1', 'step', 'complete cleanup fail', 'step',
                'status', 'acquire ctl=2 rid=1', 'start ctl=1 rid=4 gen=1', 'req_status ctl=0 rid=1 inst=0 boot=zero dl=0',
                'fake')
        st = tagged(r, 'status')
        self.assertEqual((st[0]['phase'], st[0]['cleanup_fail']), ('stop_queued', '1'))
        self.assertEqual(st[1]['cleanups'], '1')                    # retry waits for the delay
        self.assertEqual((st[2]['phase'], st[2]['owner'], st[2]['oreason']), ('faulted', 'faulted', 'cleanup_failed'))
        self.assertNotIn('last_cleanup_done', flags(st[2]))
        rep = tagged(r, 'reply')
        self.assertEqual([x['st'] for x in rep[-3:]], ['faulted', 'faulted', 'ok'])
        self.assertEqual(last(r, 'fake')['running'], '1')          # effect still present: honestly faulted

    def test_cleanup_refused_counts_as_failure(self):
        r = run('init kind=fake attempts=1', *OWN[1:], 'start ctl=1 rid=2 gen=1', 'step', 'complete start ok',
                'step', 'fake refuse_cleanup=1', 'stop ctl=1 rid=3 gen=1', 'step', 'status')
        self.assertEqual(last(r, 'status')['phase'], 'faulted')

    def test_cleanup_timeout_blocks_new_owner_until_late_ack(self):
        r = run('init kind=fake cleanup_to=1000', 'acquire ctl=1 rid=1 lease=2000', 'start ctl=1 rid=2 gen=1',
                'step', 'complete start ok', 'step', 'stop ctl=1 rid=3 gen=1', 'step', 'adv 1000', 'step 5',
                'status', 'adv 5000', 'step', 'acquire ctl=2 rid=1', 'complete cleanup ok', 'step', 'status',
                'acquire ctl=2 rid=2')
        st = tagged(r, 'status')
        self.assertEqual(st[0]['phase'], 'cleanup_uncertain')
        self.assertTrue({'cleanup_unresolved', 'cleanup_overdue'} <= flags(st[0]))
        self.assertEqual(st[0]['cleanups'], '1')                    # no second cleanup while one is unresolved
        rep = tagged(r, 'reply')
        self.assertEqual(rep[3]['st'], 'cleanup_pending')          # lease expired, but cleanup unconfirmed
        # Lease also expired meanwhile: only the late cleanup ack frees the owner slot.
        self.assertEqual((st[1]['phase'], st[1]['owner'], st[1]['late'], st[1]['end']), ('none', 'free', '1', 'stopped'))
        self.assertEqual((rep[4]['st'], rep[4]['gen']), ('ok', '2'))

    def test_release_while_active(self):
        r = run(*ACTIVE, 'release ctl=1 rid=3 gen=1', 'step', 'status', 'complete cleanup ok', 'step', 'status')
        rep = last(r, 'reply')
        self.assertEqual((rep['st'], rep['owner'], rep['gen']), ('stop_queued', 'draining', '1'))
        st = tagged(r, 'status')
        self.assertEqual((st[0]['owner'], st[0]['phase']), ('draining', 'cleanup_dispatched'))
        self.assertEqual((st[1]['owner'], st[1]['end']), ('free', 'released'))


class RecoveryAndStale(unittest.TestCase):
    def test_reconnect_recovery_rejects_old_generation_and_tokens(self):
        r = run(*ACTIVE, 'lost ctl=1', 'step', 'complete cleanup ok', 'step', 'acquire ctl=7 rid=1',
                'start ctl=7 rid=2 gen=2', 'step', 'status', 'complete start ok tok=prev', 'event frame tok=prev',
                'stop ctl=1 rid=9 gen=1', 'step', 'status', 'complete start ok', 'step', 'status')
        st = tagged(r, 'status')
        self.assertEqual((st[0]['phase'], st[0]['gen'], st[0]['sess']), ('start_dispatched', '2', '2'))
        self.assertEqual((st[1]['phase'], st[1]['stale_comp'], st[1]['ev_ignored']), ('start_dispatched', '1', '1'))
        self.assertEqual(tagged(r, 'reply')[-1]['st'], 'stale_generation')
        self.assertEqual(st[2]['phase'], 'active')

    def test_stale_events_and_completions_are_ignored(self):
        r = run(*ACTIVE, 'event listener tok=99', 'complete cleanup ok tok=99', 'complete start ok', 'step', 'status')
        st = last(r, 'status')
        self.assertEqual((st['phase'], st['ev_ignored'], st['stale_comp']), ('active', '1', '2'))
        self.assertNotIn('listener', flags(st))

    def test_new_instance_rejects_old_instance_requests(self):
        r = run('init kind=fake inst=0x2222', 'acquire ctl=1 rid=1 inst=0x1111', 'acquire ctl=1 rid=2')
        rep = tagged(r, 'reply')
        self.assertEqual((rep[0]['st'], rep[0]['field']), ('stale_instance', 'instance'))
        self.assertEqual(rep[1]['st'], 'ok')


class Backends(unittest.TestCase):
    def test_startup_with_native_backend_absent(self):
        r = run('init kind=native', 'status', 'acquire ctl=1 rid=1', 'start ctl=1 rid=2 gen=1', 'step 3',
                'req_status ctl=0 rid=1 inst=0 boot=zero dl=0', 'status')
        st = tagged(r, 'status')
        self.assertEqual((st[0]['bk'], st[0]['bs'], st[0]['br'], st[0]['vr']),
                         ('native', 'unavailable', 'native_backend_absent', 'native_backend_absent'))
        self.assertEqual([x['st'] for x in tagged(r, 'reply')], ['backend_unavailable', 'backend_unavailable', 'ok'])
        self.assertEqual((st[1]['dispatched'], st[1]['owner'], st[1]['phase']), ('0', 'free', 'none'))

    def test_native_and_malformed_tables_refused(self):
        for kind, reason in (('native_table', 'serialization_unproven'), ('mislabel', 'table_rejected'),
                             ('badabi', 'table_rejected'), ('fake fake_init=1', 'init_failed')):
            r = run(f'init kind={kind}', 'status', 'acquire ctl=1 rid=1')
            st = last(r, 'status')
            self.assertEqual((st['bs'], st['br']), ('unavailable', reason), kind)
            self.assertEqual(last(r, 'reply')['st'], 'backend_unavailable', kind)


class Binding(unittest.TestCase):
    def test_typed_request_binding_rejections(self):
        cases = [
            ('acquire ctl=1 rid=1 inst=0', 'stale_instance', 'instance'),
            ('acquire ctl=1 rid=1 boot=other', 'boot_mismatch', 'boot'),
            ('acquire ctl=1 rid=1 boot=zero', 'boot_mismatch', 'boot'),
            ('acquire ctl=1 rid=1 dl=0', 'bad_deadline', 'deadline'),
            ('acquire ctl=1 rid=1 dl=1000', 'expired', 'deadline'),
            ('acquire ctl=1 rid=1 dl=+60001', 'bad_deadline', 'deadline'),
            ('acquire ctl=1 rid=0', 'bad_request_id', 'request_id'),
            ('acquire ctl=1 rid=1 res=1', 'bad_field', 'reserved'),
            ('acquire ctl=0 rid=1', 'bad_field', 'controller'),
            ('acquire ctl=1 rid=1 gen=5', 'bad_field', 'generation'),
            ('acquire ctl=1 rid=1 lease=999', 'bad_field', 'lease'),
            ('acquire ctl=1 rid=1 lease=60001', 'bad_field', 'lease'),
            ('stop ctl=1 rid=1 gen=1 profile=1', 'bad_field', 'profile'),
            ('start ctl=1 rid=1 gen=1 lease=5000', 'bad_field', 'lease'),
            ('rawkind k=9 ctl=1 rid=1', 'unknown_kind', 'kind'),
            ('rawkind k=0 ctl=1 rid=1', 'unknown_kind', 'kind'),
        ]
        for line, status, field in cases:
            r = run('init kind=fake', line, 'step', 'status')
            rep = last(r, 'reply')
            self.assertEqual((rep['st'], rep['field']), (status, field), line)
            self.assertEqual((last(r, 'status')['owner'], last(r, 'status')['dispatched']), ('free', '0'), line)

    def test_start_profile_and_status_rules(self):
        r = run(*OWN, 'start ctl=1 rid=2 gen=1 profile=2', 'req_status ctl=0 rid=1 inst=0 boot=zero dl=0',
                'req_status ctl=0 rid=1 inst=0 boot=zero dl=+100', 'req_status ctl=5 rid=1 inst=0x1111 boot=cur dl=+100')
        rep = tagged(r, 'reply')
        self.assertEqual((rep[1]['st'], rep[1]['field']), ('bad_profile', 'profile'))
        self.assertEqual(rep[2]['st'], 'ok')
        self.assertEqual((rep[3]['st'], rep[3]['field']), ('bad_deadline', 'deadline'))   # clock domain needs boot
        self.assertEqual((rep[4]['st'], rep[4]['gen']), ('ok', '0'))


class Mailbox(unittest.TestCase):
    def test_bounded_mailbox_overflow_is_counted(self):
        r = run(*ACTIVE, 'flood 40', 'step', 'status')
        self.assertEqual(last(r, 'flood'), {'posted': '16', 'dropped': '24'})
        st = last(r, 'status')
        self.assertEqual((st['overflow'], st['phase']), ('24', 'active'))

    def test_concurrent_posting_threads(self):
        r = run(*ACTIVE, 'stress threads=4 per=3000', 'status')
        s = last(r, 'stress')
        self.assertEqual(s['balanced'], '1', s)
        self.assertEqual(last(r, 'status')['phase'], 'active')


if __name__ == '__main__':
    unittest.main()
