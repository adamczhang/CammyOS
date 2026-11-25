"""Adversarial host/ARM fixture tests for the WP2 status bridge (no hardware).

Default: builds and runs the host binary. To run the same suite against the ARM
binary under local user-mode emulation set CAMMY_BRIDGE_COMMAND and
CAMMY_BRIDGE_SELFTEST to JSON argv prefixes (see native/bridge/run_arm_tests.py).
A passing run proves protocol and lifecycle behavior of the fake/absent backends
only; it is not evidence of camera, native-app or hardware readiness.
SPDX-License-Identifier: MIT
"""
import array
import errno
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'native/bridge'))
import cammy_bridge_client as cb  # noqa: E402

BRIDGE = None
SELFTEST = None
HOST_BOOT_ID = cb.read_boot_id()


def setUpModule():
    global BRIDGE, SELFTEST
    BRIDGE = json.loads(os.environ.get('CAMMY_BRIDGE_COMMAND', '[]'))
    SELFTEST = json.loads(os.environ.get('CAMMY_BRIDGE_SELFTEST', '[]'))
    if not BRIDGE or not SELFTEST:
        subprocess.run(['make', '-s', '-C', str(ROOT / 'native/bridge'), 'host'], check=True,
                       stdout=subprocess.DEVNULL)
        BRIDGE = BRIDGE or [str(ROOT / 'build/native-bridge/host/cammy-bridge')]
        SELFTEST = SELFTEST or [str(ROOT / 'build/native-bridge/host/bridge-selftest')]


class Bridge:
    """One bridge process with a private socket directory."""

    def __init__(self, *extra, backend='fake', sock=None, start=True):
        self.tmp = tempfile.TemporaryDirectory(prefix='cbr')
        os.chmod(self.tmp.name, 0o700)
        self.dir = Path(self.tmp.name) / 'run'
        self.sock = sock or self.dir / 'bridge.sock'
        self.args = ['--socket', str(self.sock), '--backend', backend, *extra]
        self.proc = None
        self.ready = None
        if start:
            self.start()

    def spawn(self, args=None):
        return subprocess.Popen(BRIDGE + (self.args if args is None else args), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

    def start(self, timeout=10):
        self.proc = self.spawn()
        self.ready = read_event(self.proc, timeout)
        if not self.ready or self.ready.get('event') != 'ready':
            self.proc.kill()
            raise AssertionError(f'bridge not ready: {self.proc.stderr.read()!r}')
        return self.ready

    def client(self, timeout=3.0):
        return cb.BridgeClient(self.sock, timeout)

    def status(self):
        with self.client() as c:
            return c.call(cb.STATUS)['status_payload']

    def stop(self, sig=signal.SIGTERM, timeout=10):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(sig)
            self.proc.wait(timeout)
        return self.proc.returncode if self.proc else None

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(5)
        if self.proc:
            self.proc.stdout.close()
            self.proc.stderr.close()
        self.tmp.cleanup()


def read_event(proc, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], max(0.0, deadline - time.monotonic()))
        if r:
            line = proc.stdout.readline()
            return json.loads(line) if line else None
    return None


def wait_for(predicate, timeout=3.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def raw_connect(path, timeout=3.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
    except OSError:
        s.close()
        raise
    return s


def expect_closed(testcase, s):
    """The bridge closed this connection: EOF or reset, never another reply."""
    try:
        testcase.assertEqual(s.recv(cb.MAX_FRAME + 1), b'')
    except ConnectionResetError:
        pass


class FakeBridgeProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = Bridge('--idle-timeout-ms', '5000')

    @classmethod
    def tearDownClass(cls):
        cls.bridge.close()

    def call(self, command, **fields):
        with self.bridge.client() as c:
            return c.call(command, **fields)

    def rejected(self, frame_fields, status, field=None, closing=False):
        with self.bridge.client() as c:
            r = c.call(**frame_fields)
            self.assertEqual(r['status_name'], status)
            self.assertEqual(r['error']['field_offset'], field)
            self.assertEqual(r['error']['closing'], closing)
            if not closing:  # well-framed rejections keep the connection usable
                self.assertEqual(c.call(cb.STATUS)['status'], 0)
            return r

    def test_hello_reports_identity_bounds_and_fake_label(self):
        r = self.call(cb.HELLO, request_id=77)
        self.assertEqual((r['status'], r['command'], r['request_id']), (0, cb.HELLO, 77))
        self.assertEqual(r['backend_kind'], 'fake')
        self.assertEqual(r['instance'], int(self.bridge.ready['instance'], 16))
        self.assertNotEqual(r['instance'], 0)
        self.assertEqual(r['owner_generation'], 0)
        if HOST_BOOT_ID:
            self.assertEqual(r['boot_id'], HOST_BOOT_ID)
        h = r['hello']
        self.assertEqual((h['proto_major'], h['proto_minor_max']), (1, 0))
        self.assertEqual((h['request_size'], h['max_frame']), (64, 256))
        self.assertEqual(h['command_mask'], (1 << 1) | (1 << 2) | (1 << 3))
        self.assertEqual(h['bridge_version'], 'wp2-0.1.0')
        self.assertEqual(h['max_clients'], 4)
        self.assertEqual(h['idle_timeout_ms'], 5000)

    def test_status_is_labeled_fake_with_no_hardware_or_owner(self):
        a = self.call(cb.STATUS)
        s = a['status_payload']
        self.assertEqual(a['backend_kind'], 'fake')
        self.assertEqual((s['backend_kind'], s['backend_state'], s['backend_reason']),
                         ('fake', 'ready', 'fake_backend'))
        self.assertFalse(s['hardware_actions_enabled'])
        self.assertFalse(s['owner_present'])
        self.assertEqual(s['boot_id_available'], HOST_BOOT_ID is not None)
        self.assertEqual(s['active_clients'], 1)
        time.sleep(0.05)
        b = self.call(cb.STATUS)
        self.assertGreater(b['status_payload']['uptime_ms'], s['uptime_ms'])
        self.assertGreater(b['mono_now_ms'], a['mono_now_ms'])

    def test_fake_never_advertises_physical_capabilities(self):
        caps = self.call(cb.CAPABILITIES)['capabilities']
        self.assertEqual([c['id'] for c in caps], list(cb.CAP_IDS.values()))
        ipc, physical = caps[0], caps[1:]
        self.assertEqual((ipc['state'], ipc['physical']), ('available', False))
        for cap in physical:
            self.assertTrue(cap['physical'])
            self.assertEqual((cap['state'], cap['reason']), ('unavailable', 'fake_backend'), cap)

    def test_mutating_command_range_is_rejected(self):
        for command in range(0x10, 0x20):
            self.rejected({'command': command}, 'mutation_disabled', field=10)

    def test_unknown_commands_are_rejected(self):
        for command in (0, 4, 5, 0x0F, 0x20, 0x7FFF, 0xFFFF):
            self.rejected({'command': command}, 'unknown_command', field=10)

    def test_framing_errors_reply_then_close(self):
        good = cb.encode_request(cb.STATUS, 1)
        cases = [(b'C' * 7, 'bad_length'), (b'XXXX' + good[4:], 'bad_magic'), (good[:63], 'bad_length'),
                 (good + b'\0', 'bad_length'), (good + b'\0' * 192, 'bad_length'),
                 (good + b'\0' * 193, 'too_large'), (good * 64, 'too_large'),
                 (cb.encode_request(cb.STATUS, 1, header_size=72), 'bad_length')]
        for frame, name in cases:
            with self.subTest(name=name, length=len(frame)), raw_connect(self.bridge.sock) as s:
                s.send(frame)
                r = cb.decode_response(s.recv(cb.MAX_FRAME + 1))
                self.assertEqual(r['status_name'], name)
                self.assertTrue(r['error']['closing'])
                if name == 'bad_magic':
                    self.assertEqual((r['request_id'], r['command']), (0, 0))
                expect_closed(self, s)
        with raw_connect(self.bridge.sock) as s:  # empty packet is treated as end of stream
            s.send(b'')
            expect_closed(self, s)
        self.assertEqual(self.call(cb.HELLO)['status'], 0)

    def test_version_policy_is_must_understand(self):
        for fields, offset in (({'major': 0}, 4), ({'major': 2}, 4), ({'major': 0xFFFF}, 4), ({'minor': 1}, 6)):
            r = self.rejected({'command': cb.STATUS, **fields}, 'unsupported_version', field=offset)
            self.assertEqual((r['error']['supported_major'], r['error']['supported_minor']), (1, 0))
        with self.bridge.client() as c:  # version is judged before length: a future layout is not "malformed"
            c.send_raw(cb.encode_request(cb.STATUS, 1, major=2) + b'\0' * 16)
            r = cb.decode_response(c.recv_raw())
            self.assertEqual((r['status_name'], r['error']['closing']), ('unsupported_version', False))

    def test_unknown_fields_are_rejected_with_offset(self):
        for bit in (0, 7, 31):
            self.rejected({'command': cb.STATUS, 'flags': 1 << bit}, 'unknown_field', field=16)
        self.rejected({'command': cb.STATUS, 'reserved': 1}, 'unknown_field', field=20)
        self.rejected({'command': cb.STATUS, 'owner_generation': 1}, 'owner_unsupported', field=56)
        self.rejected({'command': cb.STATUS, 'owner_generation': 1 << 63}, 'owner_unsupported', field=56)

    def test_request_ids_are_nonzero_and_strictly_increasing_per_connection(self):
        with self.bridge.client() as c, self.bridge.client() as other:
            self.assertEqual(c.call(cb.STATUS, request_id=0)['status_name'], 'bad_request_id')
            self.assertEqual(c.call(cb.STATUS, request_id=5)['status'], 0)
            dup = c.call(cb.STATUS, request_id=5)
            self.assertEqual((dup['status_name'], dup['error']['field_offset']), ('bad_request_id', 12))
            self.assertEqual(c.call(cb.STATUS, request_id=4)['status_name'], 'bad_request_id')
            # a rejected request still consumes its ID
            self.assertEqual(c.call(cb.STATUS, request_id=6, flags=1)['status_name'], 'unknown_field')
            self.assertEqual(c.call(cb.STATUS, request_id=6)['status_name'], 'bad_request_id')
            self.assertEqual(c.call(cb.STATUS, request_id=0xFFFFFFFF)['status'], 0)
            self.assertEqual(c.call(cb.STATUS, request_id=0xFFFFFFFF)['status_name'], 'bad_request_id')
            self.assertEqual(other.call(cb.STATUS, request_id=5)['status'], 0)  # IDs are per connection

    def test_identity_binding(self):
        instance = int(self.bridge.ready['instance'], 16)
        self.assertEqual(self.call(cb.STATUS, expect_instance=instance)['status'], 0)
        r = self.rejected({'command': cb.STATUS, 'expect_instance': instance ^ 1}, 'stale_instance', field=24)
        self.assertEqual(r['instance'], instance)
        if HOST_BOOT_ID is None:
            self.skipTest('host boot id unavailable')
        self.assertEqual(self.call(cb.STATUS, boot_id=HOST_BOOT_ID)['status'], 0)
        wrong = bytes([HOST_BOOT_ID[0] ^ 0xFF]) + HOST_BOOT_ID[1:]
        self.rejected({'command': cb.STATUS, 'boot_id': wrong}, 'boot_mismatch', field=32)

    def test_deadline_policy(self):
        now = cb.monotonic_ms()
        self.rejected({'command': cb.STATUS, 'deadline_mono_ms': now + 5000}, 'clock_domain', field=48)
        if HOST_BOOT_ID is None:
            self.skipTest('host boot id unavailable')
        bid = {'command': cb.STATUS, 'boot_id': HOST_BOOT_ID}
        self.rejected({**bid, 'deadline_mono_ms': 1}, 'expired', field=48)
        self.rejected({**bid, 'deadline_mono_ms': cb.monotonic_ms() - 1}, 'expired', field=48)
        self.rejected({**bid, 'deadline_mono_ms': cb.monotonic_ms() + 120000}, 'bad_deadline', field=48)
        self.rejected({**bid, 'deadline_mono_ms': 0xFFFFFFFFFFFFFFFF}, 'bad_deadline', field=48)
        self.assertEqual(self.call(cb.STATUS, boot_id=HOST_BOOT_ID,
                                   deadline_mono_ms=cb.monotonic_ms() + 5000)['status'], 0)

    def test_error_budget_closes_abusive_connection(self):
        with self.bridge.client() as c:
            for i in range(1, 9):
                r = c.call(0x99, request_id=i)
                self.assertEqual(r['status_name'], 'unknown_command')
                self.assertEqual(r['error']['errors_remaining'], 8 - i)
                self.assertEqual(r['error']['closing'], i == 8)
            expect_closed(self, c.sock)

    def test_pipelined_requests_answered_in_order(self):
        with self.bridge.client() as c:
            commands = [cb.HELLO, cb.STATUS, 0x10, cb.CAPABILITIES, cb.STATUS, 0x55]
            for i, command in enumerate(commands, 1):
                c.send_raw(cb.encode_request(command, i))
            replies = [cb.decode_response(c.recv_raw()) for _ in commands]
            self.assertEqual([r['request_id'] for r in replies], list(range(1, 7)))
            self.assertEqual([r['status_name'] for r in replies],
                             ['ok', 'ok', 'mutation_disabled', 'ok', 'ok', 'unknown_command'])

    def test_passed_descriptors_are_rejected_and_not_retained(self):
        rfd, wfd = os.pipe()
        try:
            with raw_connect(self.bridge.sock) as s:
                s.sendmsg([cb.encode_request(cb.STATUS, 1)],
                          [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [wfd]))])
                os.close(wfd)
                wfd = -1
                r = cb.decode_response(s.recv(cb.MAX_FRAME + 1))
                self.assertEqual((r['status_name'], r['error']['closing']), ('bad_ancillary', True))
                expect_closed(self, s)
            # if the bridge held the passed write end, this read would block
            ready, _, _ = select.select([rfd], [], [], 3)
            self.assertTrue(ready)
            self.assertEqual(os.read(rfd, 1), b'')
        finally:
            os.close(rfd)
            if wfd >= 0:
                os.close(wfd)

    def test_stream_and_datagram_clients_cannot_connect(self):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with socket.socket(socket.AF_UNIX, kind) as s, self.assertRaises(OSError) as e:
                s.connect(str(self.bridge.sock))
            self.assertIn(e.exception.errno, (errno.EPROTOTYPE, errno.ECONNREFUSED))

    def test_concurrent_callers_get_only_their_own_replies(self):
        errors = []

        def worker(n):
            try:
                with self.bridge.client() as c:
                    for i in range(1, 31):
                        rid = n * 1000 + i
                        r = c.call(cb.STATUS if i % 2 else cb.HELLO, request_id=rid)
                        if r['request_id'] != rid or r['status'] != 0:
                            errors.append((n, rid, r['request_id'], r['status_name']))
            except Exception as exc:  # report, do not hang the suite
                errors.append((n, repr(exc)))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(1, 4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])

    def test_peer_disconnect_mid_request_is_cleaned_up(self):
        with self.bridge.client() as monitor:
            before = monitor.call(cb.STATUS)['status_payload']
            backlog_full = 0
            for _ in range(40):
                while True:
                    try:
                        s = raw_connect(self.bridge.sock)
                        break
                    except BlockingIOError:  # bounded listen backlog: a caller must back off
                        backlog_full += 1
                        self.assertLess(backlog_full, 2000)
                        time.sleep(0.005)
                with s:
                    try:
                        s.send(cb.encode_request(cb.STATUS, 1))
                    except BrokenPipeError:
                        # A full bridge can send BUSY and close immediately,
                        # before this peer gets scheduled to send its request.
                        # Accept only that proven rejection, not an arbitrary
                        # broken pipe or a vanished server. Counters below still
                        # account for every one of the forty connections.
                        reply = cb.decode_response(s.recv(cb.MAX_FRAME + 1))
                        self.assertEqual((reply['status_name'], reply['error']['closing']), ('busy', True))
                    # Accepted peers close without reading the normal reply.
            after = wait_for(lambda: (lambda st: st if st['active_clients'] == 1 else None)(
                monitor.call(cb.STATUS)['status_payload']))
        self.assertIsNotNone(after, 'connections leaked')
        accepted = after['connections_accepted'] - before['connections_accepted']
        busy = after['rejected_busy'] - before['rejected_busy']
        self.assertEqual(accepted + busy, 40)  # a burst beyond max_clients may be told BUSY, never hang
        self.assertGreater(accepted, 0)
        self.assertEqual(after['peer_disconnects'] - before['peer_disconnects'], accepted)


class BoundedResourceTests(unittest.TestCase):
    def setUp(self):
        self.bridges = []

    def tearDown(self):
        for b in self.bridges:
            b.close()

    def make(self, *args, **kw):
        b = Bridge(*args, **kw)
        self.bridges.append(b)
        return b

    def test_connection_limit_replies_busy_and_slots_are_reused(self):
        b = self.make('--max-clients', '2')
        held = [b.client(), b.client()]
        for c in held:
            self.assertEqual(c.call(cb.HELLO)['status'], 0)
        with raw_connect(b.sock) as extra:
            r = cb.decode_response(extra.recv(cb.MAX_FRAME + 1))
            self.assertEqual((r['status_name'], r['error']['closing']), ('busy', True))
            expect_closed(self, extra)
        held.pop().close()
        freed = wait_for(lambda: held[0].call(cb.STATUS)['status_payload']['active_clients'] == 1)
        self.assertTrue(freed, 'slot not released after disconnect')
        status = b.status()  # the freed slot is reusable
        self.assertEqual((status['rejected_busy'], status['active_clients']), (1, 2))
        held[0].close()

    def test_idle_connection_is_closed_and_frees_slot(self):
        b = self.make('--max-clients', '1', '--idle-timeout-ms', '300')
        with b.client() as idle:
            self.assertEqual(idle.call(cb.HELLO)['status'], 0)
            started = time.monotonic()
            idle.sock.settimeout(5)
            expect_closed(self, idle.sock)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.25)
            self.assertLess(elapsed, 3.0)
        status = b.status()
        self.assertEqual((status['idle_disconnects'], status['rejected_busy']), (1, 0))

    def test_slow_reader_is_disconnected_without_blocking_others(self):
        b = self.make()
        slow = raw_connect(b.sock)
        slow.setblocking(False)
        sent, rid, deadline = 0, 1, time.monotonic() + 10
        while time.monotonic() < deadline:  # never read; keep the bridge answering until it gives up
            try:
                slow.send(cb.encode_request(cb.STATUS, rid))
                sent += 1
                rid += 1
            except BlockingIOError:
                time.sleep(0.005)
            except (BrokenPipeError, ConnectionResetError):
                break
        st = wait_for(lambda: (lambda s: s if s['slow_disconnects'] >= 1 else None)(b.status()), timeout=5)
        self.assertIsNotNone(st, f'slow reader not disconnected after {sent} requests')
        slow.setblocking(True)
        slow.settimeout(3)
        replies = 0
        try:
            while slow.recv(cb.MAX_FRAME + 1):
                replies += 1
        except ConnectionResetError:
            pass
        slow.close()
        self.assertLess(replies, sent)  # bounded: the bridge stopped answering once the reader stalled
        self.assertEqual(b.status()['active_clients'], 1)

    def test_native_backend_is_unavailable_without_reviewed_table(self):
        b = self.make(backend='native')
        self.assertEqual(b.ready['backend'], 'native')
        with b.client() as c:
            hello = c.call(cb.HELLO)
            st = c.call(cb.STATUS)
            caps = c.call(cb.CAPABILITIES)
        self.assertEqual((hello['status'], hello['backend_kind']), (0, 'native'))
        s = st['status_payload']
        self.assertEqual((s['backend_kind'], s['backend_state'], s['backend_reason']),
                         ('native', 'unavailable', 'native_table_absent'))
        self.assertFalse(s['hardware_actions_enabled'])
        for cap in caps['capabilities'][1:]:
            self.assertEqual((cap['state'], cap['reason']), ('unavailable', 'native_table_absent'))

    def test_unauthorized_peer_uid_is_denied_without_identity(self):
        b = self.make('--allow-uid', str(os.geteuid() + 1))
        with raw_connect(b.sock) as s:  # the bridge replies at accept time, before any request
            r = cb.decode_response(s.recv(cb.MAX_FRAME + 1))
            self.assertEqual((r['status_name'], r['error']['closing']), ('peer_denied', True))
            self.assertEqual((r['instance'], r['boot_id'], r['backend_kind'], r['mono_now_ms']),
                             (0, b'\0' * 16, 'none', 0))
            expect_closed(self, s)

    def test_core_selftest_with_misbehaving_tables(self):
        with tempfile.TemporaryDirectory(prefix='cbs') as d:
            r = subprocess.run(SELFTEST + [d], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r'bridge-selftest: \d+ checks, 0 failures')


class SocketLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.bridges = []

    def tearDown(self):
        for b in self.bridges:
            b.close()

    def make(self, *args, **kw):
        b = Bridge(*args, **kw)
        self.bridges.append(b)
        return b

    def test_private_socket_and_clean_sigterm(self):
        b = self.make()
        st = os.lstat(b.sock)
        self.assertTrue(stat.S_ISSOCK(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.lstat(b.dir).st_mode), 0o700)
        self.assertEqual(b.stop(), 0)
        self.assertEqual(read_event(b.proc, 2), {'event': 'stopped', 'code': 0})
        self.assertFalse(os.path.lexists(b.sock))
        self.assertTrue(os.path.exists(str(b.sock) + '.lock'))  # lock file is intentionally persistent

    def test_second_instance_is_refused_and_first_unaffected(self):
        b = self.make()
        second = b.spawn()
        try:
            self.assertEqual(second.wait(10), 4)
            self.assertIn(b'already_running', second.stderr.read())
        finally:
            second.stdout.close()
            second.stderr.close()
        with b.client() as c:
            self.assertEqual(c.call(cb.HELLO)['instance'], int(b.ready['instance'], 16))

    def test_stale_socket_after_crash_is_reclaimed(self):
        b = self.make()
        first = b.ready['instance']
        b.stop(signal.SIGKILL)
        self.assertTrue(os.path.lexists(b.sock))
        b.proc.stdout.close()
        b.proc.stderr.close()
        b.start()
        self.assertNotEqual(b.ready['instance'], first)
        with b.client() as c:
            self.assertEqual(c.call(cb.STATUS)['status'], 0)

    def test_replacement_socket_is_not_unlinked_on_exit(self):
        b = self.make()
        os.unlink(b.sock)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as other:
            other.bind(str(b.sock))
            self.assertEqual(b.stop(), 0)
            self.assertTrue(stat.S_ISSOCK(os.lstat(b.sock).st_mode))

    def test_unsafe_directory_and_occupied_paths_are_refused(self):
        b = self.make(start=False)
        b.dir.mkdir(mode=0o700)

        def run(args=None):
            p = b.spawn(args)
            try:
                return p.wait(10), p.stderr.read()
            finally:
                p.stdout.close()
                p.stderr.close()

        os.chmod(b.dir, 0o750)
        self.assertEqual(run()[0], 3)
        os.chmod(b.dir, 0o700)
        b.sock.write_text('keep me')
        self.assertEqual(run()[0], 5)
        self.assertEqual(b.sock.read_text(), 'keep me')
        b.sock.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as live:
            live.bind(str(b.sock))
            live.listen(1)
            self.assertEqual(run()[0], 5)  # a live foreign listener is never unlinked
            self.assertTrue(os.path.lexists(b.sock))
        os.unlink(b.sock)
        link = Path(b.tmp.name) / 'link'
        link.symlink_to(b.dir)
        self.assertEqual(run(['--socket', str(link / 'bridge.sock'), '--backend', 'fake'])[0], 3)
        sock = str(b.sock)
        for args in (['--socket', 'relative.sock', '--backend', 'fake'],
                     ['--socket', '/' + 'x' * 120, '--backend', 'fake'],
                     ['--socket', '/bridge.sock', '--backend', 'fake'],
                     ['--socket', sock],
                     ['--socket', sock, '--backend', 'vendor'],
                     ['--socket', sock, '--backend', 'fake', '--max-clients', '0'],
                     ['--socket', sock, '--backend', 'fake', '--max-clients', '9'],
                     ['--socket', sock, '--backend', 'fake', '--idle-timeout-ms', '99'],
                     ['--socket', sock, '--backend', 'fake', '--idle-timeout-ms', '60001'],
                     ['--socket', sock, '--backend', 'fake', '--allow-uid', '-1'],
                     ['--socket', sock, '--backend', 'fake', '--exec', '/bin/sh']):
            with self.subTest(args=args):
                self.assertEqual(run(args)[0], 2)
        self.assertFalse(os.path.lexists(b.sock))

    def test_boot_id_source_must_be_a_regular_uuid_file(self):
        tmp = Path(tempfile.mkdtemp(prefix='cbb'))
        try:
            fifo = tmp / 'fifo'
            os.mkfifo(fifo)
            garbage = tmp / 'garbage'
            garbage.write_text('not-a-uuid\n')
            zero = tmp / 'zero'
            zero.write_text('00000000-0000-0000-0000-000000000000\n')
            for source in (fifo, Path('/dev/null'), garbage, zero, tmp / 'missing'):
                with self.subTest(source=source):
                    b = self.make('--boot-id-file', str(source))  # a FIFO would hang a naive open()
                    s = b.status()
                    self.assertFalse(s['boot_id_available'])
                    with b.client() as c:
                        r = c.call(cb.STATUS, boot_id=b'\x01' * 16, deadline_mono_ms=cb.monotonic_ms() + 1000)
                        self.assertEqual(r['status_name'], 'boot_mismatch')
                        r = c.call(cb.STATUS, deadline_mono_ms=cb.monotonic_ms() + 1000)
                        self.assertEqual(r['status_name'], 'clock_domain')
            good = tmp / 'good'
            good.write_text('01234567-89AB-cdef-0123-456789abcdef\n')
            b = self.make('--boot-id-file', str(good))
            with b.client() as c:
                r = c.call(cb.HELLO)
                self.assertEqual(r['boot_id'].hex(), '0123456789abcdef0123456789abcdef')
                ok = c.call(cb.STATUS, boot_id=r['boot_id'], deadline_mono_ms=cb.monotonic_ms() + 1000)
                self.assertEqual(ok['status'], 0)
        finally:
            for p in tmp.iterdir():
                p.unlink()
            tmp.rmdir()


if __name__ == '__main__':
    unittest.main()
