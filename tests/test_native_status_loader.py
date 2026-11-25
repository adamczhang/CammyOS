"""WP2A status-only loader: builder guards, device-ABI audit, qemu-arm proofs.

Everything here is local and offline. Device tests run the synthetic host or the
stub fixture under qemu-arm against the SAVED camera libc (never sdvcam), inside a
private user+mount namespace whose /tmp is a build directory, so the exact baked
library path /tmp/cammy-native-status/<sha>/libcammy_native_status.so is used
without touching the build host's /tmp. No camera, ADB or network.

Prerequisites (no download/install happens in tests): build/toolchain from the
top-level `make arm`, the verified sysroot from native/loader/fetch_musl_headers.sh,
the saved firmware rootfs, qemu-arm, and unshare(1).
SPDX-License-Identifier: MIT
"""
import errno
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/native-loader'
ROOTFS = OUT / 'device-rootfs'
NS_TMP = OUT / 'ns-tmp'
WORK = ROOT / 'private/ownership-program/wp2a/test-work'
QEMU = shutil.which('qemu-arm') or str(ROOT.parent / 'Cammy Bot/tools/build-deps/usr/bin/qemu-arm')
TOOL = ROOT / 'build/toolchain/bin/arm-buildroot-linux-musleabihf-'
RELEASE = OUT / 'release/libcammy_native_status.so'
HOOKS = OUT / 'hooks/libcammy_native_status.so'
TIME64 = OUT / 'time64-control/libcammy_native_status.so'
SYNTH = OUT / 'arm/synth_host'
FIXTURE = OUT / 'arm/stub_fixture'
SOCK = NS_TMP / 'cammy-bridge/bridge.sock'
EXPORTS = ['cammy_native_attach_v1', 'cammy_native_detach_v1']
HOOK_EXPORTS = EXPORTS + ['cammy_native_test_stall', 'cammy_native_test_state']

sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'native/loader'))
import build_native_status_candidate as builder  # noqa: E402
import abi_audit  # noqa: E402
from elfinfo import Elf32  # noqa: E402

_spec = importlib.util.spec_from_file_location('cammy_bridge_client', ROOT / 'native/bridge/cammy_bridge_client.py')
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)

SOURCE = builder.DEFAULT_SOURCE
HAVE_SOURCE = SOURCE.is_file()
HAVE_DEVICE = all(p.exists() for p in [TOOL.with_name(TOOL.name + 'gcc'), OUT / 'sysroot-1.1.24/include/bits/alltypes.h',
                                       Path(QEMU)]) and HAVE_SOURCE and shutil.which('unshare') is not None
BUILT = False


def build_all():
    global BUILT
    if BUILT:
        return
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['make', '-s', '-C', str(ROOT / 'native/loader'), 'device', 'time64-control'], check=True,
                   capture_output=True, timeout=300)
    BUILT = True


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reset_ns_tmp():
    if NS_TMP.exists():
        shutil.rmtree(NS_TMP)
    NS_TMP.mkdir(parents=True, mode=0o700)
    NS_TMP.chmod(0o700)


def stage(library, sha=None):
    """Copy a library to the baked path under the namespace /tmp; returns that /tmp path."""
    sha = sha or sha256(library)
    d = NS_TMP / 'cammy-native-status' / sha
    d.mkdir(parents=True, mode=0o700)
    shutil.copyfile(library, d / 'libcammy_native_status.so')
    (d / 'libcammy_native_status.so').chmod(0o500)
    return f'/tmp/cammy-native-status/{sha}/libcammy_native_status.so'


def ns_qemu(args, strace=False):
    inner = [QEMU, '-L', str(ROOTFS), '-cpu', 'cortex-a7'] + (['-strace'] if strace else []) + [str(a) for a in args]
    return ['unshare', '-rm', 'sh', '-c', 'mount --bind "$1" /tmp && shift && exec "$@"', 'sh', str(NS_TMP)] + inner


class Proc:
    """A driven host process: JSON events on stdout, one-line commands on stdin."""

    def __init__(self, argv, stderr_path=None):
        self.err_file = open(stderr_path, 'wb') if stderr_path else None
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.err_file or subprocess.PIPE)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.p.stdout, selectors.EVENT_READ)
        self.buf = b''
        self.events = []

    def event(self, name, timeout=15):
        deadline = time.monotonic() + timeout
        while True:
            while b'\n' in self.buf:
                line, self.buf = self.buf.split(b'\n', 1)
                ev = json.loads(line)
                self.events.append(ev)
                if ev.get('event') == name:
                    return ev
            left = deadline - time.monotonic()
            if left <= 0 or not self.sel.select(left):
                raise AssertionError(f'timeout waiting for {name}; events={self.events}')
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                err = b'' if self.err_file else self.p.stderr.read()
                raise AssertionError(f'EOF waiting for {name}; events={self.events}; stderr={err[-2000:]!r}')
            self.buf += chunk

    def send(self, line):
        self.p.stdin.write(line.encode() + b'\n')
        self.p.stdin.flush()

    def finish(self, timeout=15):
        try:
            self.p.stdin.close()
        except BrokenPipeError:
            pass
        rc = self.p.wait(timeout=timeout)
        if self.err_file:
            self.err_file.close()
        else:
            self.p.stderr.close()
        self.p.stdout.close()
        return rc

    def kill(self):
        if self.p.poll() is None:
            self.p.kill()
        return self.finish()


def caps_ok(test, caps):
    by = {c['id']: c for c in caps}
    test.assertEqual(by['status_ipc']['state'], 'available')
    physical = [c for c in caps if c['physical']]
    test.assertEqual(len(physical), 6)
    for c in physical:
        test.assertEqual(c['state'], 'unavailable', c)
        test.assertEqual(c['reason'], 'not_in_protocol_v1', c)


@unittest.skipUnless(HAVE_SOURCE, 'saved sdvcam not present')
class BuilderGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = SOURCE.read_bytes()
        cls.lib_sha = 'ab' * 32

    def test_exact_source_required(self):
        bad = bytearray(self.original)
        bad[0x100000] ^= 1
        with self.assertRaisesRegex(ValueError, 'hash'):
            builder.patch_image(bytes(bad), self.lib_sha)

    def _guard_fails(self, mutate, message):
        bad = bytearray(self.original)
        mutate(bad)
        with mock.patch.object(builder, 'SOURCE_SHA256', hashlib.sha256(bad).hexdigest()):
            with self.assertRaisesRegex(ValueError, message):
                builder.check_source(bytes(bad))

    def test_deeper_guards_fire(self):
        self._guard_fails(lambda b: b.__setitem__(builder.ORIGINAL_LOAD_SIZE + 0x40, 1), 'cave')
        self._guard_fails(lambda b: b.__setitem__(slice(0x21DB0, 0x21DB4), b'\0\0\0\0'), 'Call site')
        self._guard_fails(lambda b: b.__setitem__(0x94298, 0), '0xA4294')
        self._guard_fails(lambda b: b.__setitem__(builder.DLOPEN_PLT - 0x10000 + 8, 0), 'PLT')
        self._guard_fails(lambda b: b.__setitem__(52 + 32 * 3 + 16, 0), 'Program header')

    def test_patch_scope_and_manifest(self):
        patched, m = builder.patch_image(self.original, self.lib_sha)
        diff = [i for i, (a, b) in enumerate(zip(self.original, patched)) if a != b]
        ranges = [(int(r['offset'], 16), r['bytes']) for r in m['allowed_modified_ranges']]
        self.assertTrue(all(any(s <= i < s + n for s, n in ranges) for i in diff))
        self.assertEqual(len(ranges), 3)
        self.assertFalse(m['deployed'] or m['hardware_deployed'] or m['tf_release_included'])
        self.assertEqual(m['media_event_modifications'], 0)
        # Pristine media sites and the media trampoline range stay untouched.
        for va in (0xAC5D0, 0xAC638):
            self.assertEqual(patched[va - 0x10000:va - 0x10000 + 4], self.original[va - 0x10000:va - 0x10000 + 4])
        self.assertFalse(any(patched[0x8D2E1C:0x8D2E40]))
        e = Elf32(patched)
        self.assertEqual(e.phdrs[3]['filesz'], int(m['text_load_size']['to'], 16))
        self.assertLessEqual(e.phdrs[3]['filesz'], builder.LAST_TEXT_PAGE_END)
        self.assertEqual(e.e_entry, 0x334A8)
        self.assertIn(f'/tmp/cammy-native-status/{self.lib_sha}/libcammy_native_status.so'.encode(), patched)

    def test_stub_matches_gnu_as(self):
        gcc = TOOL.with_name(TOOL.name + 'gcc')
        if not gcc.exists():
            self.skipTest('ARM toolchain absent')
        work = OUT / 'stubcheck'
        work.mkdir(parents=True, exist_ok=True)
        path = f'/tmp/cammy-native-status/{self.lib_sha}/libcammy_native_status.so'
        subprocess.run([str(gcc), '-c', '-x', 'assembler-with-cpp', f'-DLIB_PATH="{path}"',
                        str(ROOT / 'native/loader/status_stub.S'), '-o', str(work / 'stub.o')], check=True)
        subprocess.run([str(TOOL.with_name(TOOL.name + 'ld')), f'-Ttext={builder.STUB_ADDRESS:#x}', '-e',
                        'status_stub', '-o', str(work / 'stub.elf'), str(work / 'stub.o')], check=True)
        subprocess.run([str(TOOL.with_name(TOOL.name + 'objcopy')), '-O', 'binary', '-j', '.text',
                        str(work / 'stub.elf'), str(work / 'stub.bin')], check=True)
        blob, _ = builder.build_stub(self.lib_sha)
        self.assertEqual(blob, (work / 'stub.bin').read_bytes())

    def test_cli_private_mode_0600_and_no_overwrite(self):
        WORK.mkdir(parents=True, exist_ok=True)
        lib = WORK / 'fake-lib.so'
        lib.write_bytes(b'not a real library; only hashed')
        out = WORK / 'cli-candidate'
        for p in (out, out.with_suffix('.json')):
            p.unlink(missing_ok=True)
        cmd = [sys.executable, str(ROOT / 'tools/build_native_status_candidate.py'), '--library', str(lib),
               '--output', str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(out.with_suffix('.json').stat().st_mode), 0o600)
        self.assertFalse(json.loads(out.with_suffix('.json').read_text())['deployed'])
        self.assertNotEqual(subprocess.run(cmd, capture_output=True, timeout=60).returncode, 0)
        bad = subprocess.run(cmd[:-1] + [str(OUT / 'outside')], capture_output=True, timeout=60)
        self.assertNotEqual(bad.returncode, 0)
        out.unlink()
        out.with_suffix('.json').unlink()
        lib.unlink()


@unittest.skipUnless(HAVE_DEVICE, 'device toolchain/sysroot/qemu/rootfs not prepared')
class AbiAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_all()

    def test_release_audit(self):
        r = abi_audit.audit(RELEASE, ROOTFS, EXPORTS)
        self.assertTrue(r['pass'], r['failures'])
        self.assertEqual(r['needed'], ['libc.so'])
        self.assertEqual(set(r['time_bearing_imports']), {'clock_gettime', 'lstat', 'fstat'})
        self.assertNotIn('dlclose', r['imports'])
        self.assertTrue(r['first_definer_all_libc'])

    def test_hooks_audit(self):
        r = abi_audit.audit(HOOKS, ROOTFS, HOOK_EXPORTS)
        self.assertTrue(r['pass'], r['failures'])

    def test_time64_negative_control_fails_audit(self):
        r = abi_audit.audit(TIME64, ROOTFS, EXPORTS)
        self.assertFalse(r['pass'])
        self.assertTrue(any('time64' in f for f in r['failures']), r['failures'])

    def test_interposition_check_is_real(self):
        order = abi_audit.load_order(ROOTFS, Elf32((ROOTFS / 'usr/bin/sdvcam').read_bytes()))
        first = next(n for n, e in order if 'rint' in e.exported())
        self.assertEqual(first, 'libawnn.so')  # an import of rint would be flagged

    def test_release_has_no_vfp_or_neon_instructions(self):
        d = subprocess.run([str(TOOL.with_name(TOOL.name + 'objdump')), '-d', str(RELEASE)], capture_output=True,
                           text=True, check=True).stdout
        bad = [l for l in d.splitlines() if '\t' in l and l.split('\t')[-1].split(' ')[0].startswith('v')]
        self.assertEqual(bad, [])


@unittest.skipUnless(HAVE_DEVICE, 'device toolchain/sysroot/qemu/rootfs not prepared')
class DeviceQemuTests(unittest.TestCase):
    """qemu-arm, saved camera libc, synthetic host; never sdvcam."""

    @classmethod
    def setUpClass(cls):
        build_all()

    def setUp(self):
        reset_ns_tmp()

    def host(self, library_path, scenario, strace_to=None):
        return Proc(ns_qemu([SYNTH, library_path, scenario], strace=bool(strace_to)), stderr_path=strace_to)

    def test_abi_probe_against_saved_libc(self):
        p = self.host('none', 'abi-probe')
        ev = p.event('abi-probe')
        self.assertEqual(p.finish(), 0)
        self.assertEqual(ev['fails'], 0, ev)
        self.assertEqual((ev['sizeof_time_t'], ev['sizeof_timespec'], ev['sizeof_stat']), (4, 8, 104))

    def test_time64_library_refused_cleanly(self):
        path = stage(TIME64)
        p = self.host(path, 'serve')
        ev = p.event('dlopen')
        self.assertEqual(p.finish(), 4)
        self.assertFalse(ev['ok'])
        self.assertIn('time64', ev['error'])
        self.assertFalse(SOCK.exists())

    def test_missing_library_refused(self):
        p = self.host('/tmp/cammy-native-status/none/libcammy_native_status.so', 'serve')
        self.assertFalse(p.event('dlopen')['ok'])
        self.assertEqual(p.finish(), 4)

    def test_serve_status_signals_cloexec_detach(self):
        path = stage(RELEASE)
        p = self.host(path, 'serve')
        dl = p.event('dlopen')
        self.assertEqual((dl['ok'], dl['hooks'], dl['core_exported']), (True, 0, 0))
        at = p.event('attached')
        self.assertEqual(at['rc'], 0)
        self.assertEqual(at['mask_restored'], 1)
        self.assertEqual(at['new_fds_without_cloexec'], 0)
        self.assertGreaterEqual(at['new_fds'], 4)  # lock, listener, wake pipe
        self.assertTrue(SOCK.exists())
        with client.BridgeClient(SOCK) as c:
            hello = c.call(client.HELLO)
            self.assertEqual((hello['status'], hello['backend_kind']), (0, 'native'))
            st = c.call(client.STATUS)['status_payload']
            self.assertEqual((st['backend_kind'], st['backend_state'], st['backend_reason']), ('native', 'ready', 'none'))
            self.assertFalse(st['hardware_actions_enabled'])
            caps_ok(self, c.call(client.CAPABILITIES)['capabilities'])
            p.send('check-fds')
            fds = p.event('fds')
            self.assertEqual(fds['new_fds_without_cloexec'], 0)
            sig = p.event('signals')
            self.assertEqual(sig, {'event': 'signals', 'handled_while_main_blocked': 0, 'pending': 1,
                                   'handled_after': 1, 'on_main_thread': 1})
            self.assertEqual(p.event('second-attach')['rc'], -errno.EALREADY)
        p.send('close')
        det = p.event('detached')
        self.assertLess(det['us'], 250000)
        self.assertLess(det['second_us'], 5000)
        self.assertEqual(det['fds_left'], 0)
        self.assertFalse(SOCK.exists())  # joined: close unlinked our socket
        self.assertEqual(p.finish(), 0)

    def test_rejected_records(self):
        for what, rc in [('null', -errno.EINVAL), ('abi', -errno.EPROTO), ('size', -errno.EPROTO),
                         ('profile', -errno.EPROTO), ('hash', -errno.EPROTO)]:
            reset_ns_tmp()
            p = self.host(stage(RELEASE), 'reject-' + what)
            ev = p.event('rejected')
            self.assertEqual(p.finish(), 0)
            self.assertEqual((ev['rc'], ev['retry_rc'], ev['new_fds']), (rc, -errno.EALREADY, 0), what)
            self.assertFalse((NS_TMP / 'cammy-bridge').exists(), what)

    def test_detach_before_attach_is_noop(self):
        p = self.host(stage(HOOKS), 'detach-first')
        at = p.event('attached')
        st = p.event('detached')
        self.assertEqual(p.finish(), 0)
        self.assertEqual(at['rc'], 0)
        self.assertEqual((st['detach_result'], st['thread_done'], st['handle_pinned'], st['phase']), (1, 1, 0, 3))
        self.assertFalse(SOCK.exists())

    def test_detach_timeout_abandons_and_pins_state(self):
        p = self.host(stage(HOOKS), 'stall')
        self.assertEqual(p.event('attached')['rc'], 0)
        p.event('stall-armed')
        c = client.BridgeClient(SOCK, timeout=10)
        result = {}

        def query():
            try:
                result['status'] = c.call(client.STATUS)
            except Exception as e:  # noqa: BLE001 - reported below
                result['error'] = repr(e)
        t = threading.Thread(target=query)
        t.start()
        time.sleep(0.5)  # the bridge thread is now blocked inside the status callback
        p.send('detach')
        d = p.event('detach-returned')
        self.assertGreaterEqual(d['us'], 195000)
        self.assertLess(d['us'], 1000000)
        s = p.event('after-detach')
        self.assertEqual((s['detach_result'], s['phase'], s['thread_done'], s['handle_pinned']), (2, 3, 0, 1))
        self.assertTrue(SOCK.exists())  # abandoned: never unlinked or closed
        p.send('release')
        self.assertEqual(p.event('released')['thread_done_seen'], 1)
        f = p.event('final')
        self.assertEqual((f['thread_done'], f['handle_pinned'], f['detach_result']), (1, 1, 2))
        t.join(10)
        c.close()
        self.assertIn('status', result, result)
        sp = result['status']['status_payload']
        # The late thread finished its step against pinned state after MainModule's analogue was freed.
        self.assertEqual((sp['backend_state'], sp['backend_reason']), ('unavailable', 'native_exiting'))
        p.send('exit')
        self.assertEqual(p.finish(), 0)

    def test_fatal_step_error_is_bridge_failure_not_exit(self):
        p = self.host(stage(HOOKS), 'emfile')
        self.assertEqual(p.event('attached')['rc'], 0)
        armed = p.event('emfile-armed')
        self.assertEqual(armed['setrlimit'], 0)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            s.settimeout(3)
            s.connect(str(SOCK))
        except OSError:
            s = None
        p.send('check')
        st = p.event('after-failure')
        self.assertEqual((st['phase'], st['step_failures'], st['thread_done'], st['handle_pinned']), (4, 1, 1, 0))
        self.assertFalse(SOCK.exists())  # the failed bridge released its listener and socket
        self.assertEqual(p.event('still-running')['failed_seen'], 1)
        if s:  # a peer queued in the closed listener's backlog sees EOF or a reset
            s.settimeout(3)
            try:
                self.assertEqual(s.recv(256), b'')
            except ConnectionResetError:
                pass
            s.close()
        p.send('close')
        d = p.event('detached')
        self.assertEqual(d['detach_result'], 1)
        self.assertEqual(p.event('fds')['fds_left'], 0)
        self.assertEqual(p.finish(), 0)

    def test_kill_while_serving_then_stale_recovery(self):
        path = stage(RELEASE)
        p = self.host(path, 'kill-wait')
        self.assertEqual(p.event('attached')['rc'], 0)
        c = client.BridgeClient(SOCK, timeout=5)
        self.assertEqual(c.call(client.STATUS)['status_payload']['backend_state'], 'ready')
        p.p.send_signal(signal.SIGKILL)
        self.assertEqual(c.recv_raw(), b'')  # peer sees EOF
        c.close()
        p.finish()
        self.assertTrue(SOCK.exists())  # stale path left behind
        q = self.host(path, 'serve')
        self.assertEqual(q.event('attached')['rc'], 0)  # next attach reclaims it
        with client.BridgeClient(SOCK) as c2:
            self.assertEqual(c2.call(client.HELLO)['status'], 0)
            q.send('check-fds')
            q.event('second-attach')
        q.send('close')
        q.event('detached')
        self.assertEqual(q.finish(), 0)

    def test_exit_without_detach(self):
        p = self.host(stage(RELEASE), 'exit-no-detach')
        self.assertEqual(p.event('attached')['rc'], 0)
        c = client.BridgeClient(SOCK, timeout=5)
        self.assertEqual(c.call(client.HELLO)['status'], 0)
        p.send('exit')
        self.assertEqual(p.finish(), 0)  # atexit/fini with the bridge thread live: clean exit
        self.assertEqual(c.recv_raw(), b'')
        c.close()

    def test_syscalls_of_library_under_strace(self):
        WORK.mkdir(parents=True, exist_ok=True)
        log = WORK / 'serve.strace'
        p = self.host(stage(RELEASE), 'serve', strace_to=log)
        self.assertEqual(p.event('attached')['rc'], 0)
        with client.BridgeClient(SOCK) as c:
            c.call(client.STATUS)
            c.call(client.CAPABILITIES)
            p.send('check-fds')
            p.event('second-attach')
        p.send('close')
        p.event('detached')
        self.assertEqual(p.finish(), 0)
        # qemu prints "<tid> call(args) = ret"; records of concurrent threads can share a
        # physical line, so split on every "<tid> name(" boundary.
        records = re.findall(r'(\d{2,}) ([a-z_0-9]+)\((.*?)(?=\d{2,} [a-z_0-9]+\(|$)', log.read_text(errors='replace'),
                             flags=re.M)
        main_pid = int(records[0][0])  # the process starts single-threaded
        by_tid, window, inside = {}, [], False
        for tid_s, call, rest in records:
            tid, line = int(tid_s), f'{tid_s} {call}({rest}'
            if tid == main_pid and call == 'access' and '/cammy-marker/' in rest:
                inside = '-begin' in rest
                continue
            if inside and tid == main_pid:
                window.append((call, line))
            by_tid.setdefault(tid, []).append((call, line))
        bridge = [x for tid, calls in by_tid.items() if tid != main_pid for x in calls]
        self.assertTrue(bridge, 'bridge thread syscalls not captured')
        forbidden = {'ioctl', 'execve', 'kill', 'tgkill', 'tkill', 'reboot', 'mount', 'umount2', 'fork', 'vfork',
                     'rt_sigaction', 'sigaction', 'exit_group', 'ptrace', 'init_module', 'delete_module'}
        for call, line in bridge + window:
            self.assertNotIn(call, forbidden, line)
            if call in ('open', 'openat'):
                self.assertTrue('/proc/sys/kernel/random/boot_id' in line or '/tmp/cammy-bridge/' in line, line)
            self.assertNotIn('/dev/', line)
            self.assertNotIn('/sys/', line.replace('/proc/sys/kernel/random/boot_id', ''))
        self.assertIn('clone', {c for c, _ in window})  # exactly the bridge thread is created in attach
        self.assertEqual(sum(1 for c, _ in window if c == 'clone'), 1)


@unittest.skipUnless(HAVE_DEVICE, 'device toolchain/sysroot/qemu/rootfs not prepared')
class StubFixtureTests(unittest.TestCase):
    """The candidate's exact patched bytes at their native addresses under qemu-arm."""

    @classmethod
    def setUpClass(cls):
        build_all()
        cls.lib_sha = sha256(RELEASE)
        patched, cls.manifest = builder.patch_image(SOURCE.read_bytes(), cls.lib_sha)
        WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
        cls.candidate = WORK / 'fixture-candidate'
        cls.candidate.unlink(missing_ok=True)
        cls.candidate.write_bytes(patched)
        cls.candidate.chmod(0o600)

    def setUp(self):
        reset_ns_tmp()

    def test_inert_cases(self):
        path = f'/tmp/cammy-native-status/{self.lib_sha}/libcammy_native_status.so'
        r = subprocess.run(ns_qemu([FIXTURE, self.candidate, 'inert', path]), capture_output=True, text=True, timeout=60)
        cases = [json.loads(l) for l in r.stdout.splitlines() if l.startswith('{')]
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(cases), 7)
        self.assertTrue(all(c['pass'] for c in cases), cases)
        for c in cases:
            attached = c['name'].startswith('success')
            self.assertEqual(c['detach_calls'], 1 if attached else 0, c)
            self.assertEqual(c['orig_sp_delta'], 32 if attached else 0, c)
            if not attached:
                self.assertEqual(c['orig_lr'], 0x31DB4, c)

    def _real(self, staged):
        p = Proc(ns_qemu([FIXTURE, self.candidate, 'real']))
        p.event('original-running')
        if staged:
            with client.BridgeClient(SOCK) as c:
                st = c.call(client.STATUS)['status_payload']
                self.assertEqual((st['backend_kind'], st['backend_state']), ('native', 'ready'))
                caps_ok(self, c.call(client.CAPABILITIES)['capabilities'])
        else:
            self.assertFalse(SOCK.exists())
        p.send('close')
        ev = p.event('real')
        self.assertEqual(p.finish(), 0)
        self.assertTrue(ev['pass'], ev)
        return ev

    def test_real_dlopen_attach_serve_detach(self):
        stage(RELEASE)
        ev = self._real(True)
        self.assertTrue(ev['attached_path'])
        self.assertEqual(ev['orig_sp_delta'], 32)
        self.assertFalse(SOCK.exists())  # the real detach joined and unlinked

    def test_real_fallback_when_library_absent(self):
        ev = self._real(False)
        self.assertFalse(ev['attached_path'])
        self.assertEqual((ev['orig_lr'], ev['orig_sp_delta']), (0x31DB4, 0))

    def test_real_fallback_when_time64_library_staged(self):
        stage(TIME64, sha=self.lib_sha)  # wrong bytes at the baked path
        ev = self._real(False)
        self.assertFalse(ev['attached_path'])


@unittest.skipUnless(shutil.which(os.environ.get('CC', 'cc')), 'no host C compiler')
class HostSanitizerTests(unittest.TestCase):
    """Same library logic on the build host with ASan/UBSan and TSan (not an ABI proof)."""

    @classmethod
    def setUpClass(cls):
        subprocess.run(['make', '-s', '-C', str(ROOT / 'native/loader'), 'host-asan', 'host-tsan'], check=True,
                       capture_output=True, timeout=300)

    def run_variant(self, variant):
        lib = OUT / f'host-{variant}/libcammy_native_status.so'
        host = OUT / f'host-{variant}/synth_host'
        sock = OUT / f'hrun-{variant}/bridge.sock'
        env = {**os.environ, 'TSAN_OPTIONS': 'halt_on_error=1 exitcode=66', 'ASAN_OPTIONS': 'detect_leaks=0'}

        def proc(scenario, extra_tsan=''):
            e = {**env, 'TSAN_OPTIONS': env['TSAN_OPTIONS'] + extra_tsan}
            return subprocess.Popen([str(host), str(lib), scenario], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=e)
        # serve
        p = Proc.__new__(Proc)
        p.err_file, p.p = None, proc('serve')
        p.sel = selectors.DefaultSelector()
        p.sel.register(p.p.stdout, selectors.EVENT_READ)
        p.buf, p.events = b'', []
        self.assertEqual(p.event('attached')['rc'], 0)
        with client.BridgeClient(sock) as c:
            self.assertEqual(c.call(client.STATUS)['status_payload']['backend_state'], 'ready')
            p.send('check-fds')
            p.event('second-attach')
        p.send('close')
        p.event('state')
        self.assertEqual(p.finish(), 0, p.events)
        # stall -> abandon -> late completion against pinned state
        p = Proc.__new__(Proc)
        # The abandoned thread is deliberately never joined or detached (pinned until
        # process death): TSan's "thread leak" is the designed outcome; races still fail.
        p.err_file, p.p = None, proc('stall', ' report_thread_leaks=0')
        p.sel = selectors.DefaultSelector()
        p.sel.register(p.p.stdout, selectors.EVENT_READ)
        p.buf, p.events = b'', []
        p.event('stall-armed')
        c = client.BridgeClient(sock, timeout=10)
        out = {}
        t = threading.Thread(target=lambda: out.setdefault('r', c.call(client.STATUS)))
        t.start()
        time.sleep(0.3)
        p.send('detach')
        self.assertEqual(p.event('after-detach')['detach_result'], 2)
        p.send('release')
        p.event('final')
        t.join(10)
        c.close()
        self.assertEqual(out['r']['status_payload']['backend_reason'], 'native_exiting')
        p.send('exit')
        self.assertEqual(p.finish(), 0, p.events)
        # emfile -> bridge failure while the host keeps running
        for scenario, cmds in [('emfile', None), ('detach-first', None), ('reject-hash', None)]:
            q = subprocess.Popen([str(host), str(lib), scenario], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=env)
            if scenario == 'emfile':
                time.sleep(0.3)
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                    s.connect(str(sock))
                except OSError:
                    s = None
                q.stdin.write(b'check\nclose\n')
                q.stdin.flush()
            o, e = q.communicate(timeout=30)
            self.assertEqual(q.returncode, 0, (scenario, o, e[-3000:]))
            if scenario == 'emfile':
                states = [json.loads(l) for l in o.splitlines() if b'after-failure' in l]
                self.assertEqual(states[0]['phase'], 4, o)
                if s:
                    s.close()

    def test_asan_ubsan(self):
        self.run_variant('asan')

    def test_tsan(self):
        self.run_variant('tsan')


if __name__ == '__main__':
    unittest.main()
