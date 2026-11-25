"""Bounded, USB-only adapter for the checked CammyOS installer."""

import os
import re
import shlex
import socket
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

from . import rc_install as rc

SYS_USB = Path('/sys/bus/usb/devices')
USB_SERIAL = '20080411'
SERVER = ('127.0.0.1', 5038)
USB_PORT = re.compile(r'[0-9]+-[0-9]+(?:\.[0-9]+)*')
MAX_PAYLOAD = 64 * 1024 * 1024


class Blocked(RuntimeError):
    """A fixed, operator-facing maintenance failure."""


def rom_usb_visible(root=SYS_USB):
    """Inspect host enumeration only. Do not open a ROM recovery session."""
    try:
        return any((p / 'idVendor').read_text().strip() == '1f3a'
                   and (p / 'idProduct').read_text().strip() == 'efe8'
                   for p in root.iterdir() if (p / 'idVendor').is_file())
    except OSError:
        return False


def _inventory():
    with socket.create_connection(SERVER, timeout=2) as client:
        with client.makefile('rb') as stream:
            request = b'host:devices-l'
            client.sendall(f'{len(request):04x}'.encode() + request)
            if stream.read(4) != b'OKAY':
                raise Blocked('The local maintenance ADB server could not list transports.')
            size = int(stream.read(4), 16)
            if size > 8192:
                raise Blocked('Too many maintenance ADB transports.')
            return stream.read(size).decode('ascii').splitlines()


class UsbCamera:
    """Once bound, no operation can follow ADB to a different transport or boot."""

    def __init__(self, *, budget=900):
        self.deadline = time.monotonic() + budget
        self.approved_tf = None
        self.transport = self._transport()
        self.boot = self.boot_id()
        if not rc.UUID.fullmatch(self.boot):
            raise Blocked('USB shell did not return a valid Linux boot ID.')

    @staticmethod
    def _transport():
        try:
            lines = _inventory()
        except (OSError, ValueError, UnicodeError) as exc:
            raise Blocked('Start the documented local USB maintenance setup and refresh. '
                          'The project ADB server on localhost:5038 is unavailable.') from exc
        devices = [line.split() for line in lines if line.split()]
        matches = [fields for fields in devices if fields[0] == USB_SERIAL
                   or any(field.startswith('usb:') for field in fields[2:])]
        if len(matches) == 1:
            if len(matches[0]) < 2:
                raise Blocked('The maintenance ADB inventory is incomplete.')
            serial, state, *attrs = matches[0]
            fields = dict(item.split(':', 1) for item in attrs if ':' in item)
            port = fields.get('usb', '').removeprefix('usb:')
            number = fields.get('transport_id', '')
            if (serial == USB_SERIAL and state == 'device' and USB_PORT.fullmatch(port)
                    and re.fullmatch(r'[0-9]{1,10}', number)):
                return serial, port, number
        raise Blocked('Connect exactly one already-authorized maintenance camera over USB. '
                      'TCP ADB, ambiguous devices and unauthorized transports are excluded.')

    def _adb(self, *args, timeout=20):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Blocked('The maintenance time limit expired. Inspect the USB state before retrying.')
        if self._transport() != self.transport:
            raise Blocked('The authorized USB transport changed. Inspect and prepare a new request.')
        try:
            out = subprocess.run([str(rc.ADB), '-H', SERVER[0], '-P', str(SERVER[1]), '-t',
                                  self.transport[2], *args], env=rc.ENV, capture_output=True,
                                 timeout=min(timeout, remaining), check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Blocked('A bounded USB operation failed. Check the cable, maintenance access and TF card.') from exc
        return rc.ANSI.sub('', out.stdout.decode(errors='replace'))

    def shell(self, command, *, timeout=20):
        output = self._adb('shell', command + '; echo "__cammy_rc=$?"', timeout=timeout)
        body, marker, code = output.rpartition('__cammy_rc=')
        if not marker or code.strip() != '0':
            raise Blocked('The authorized USB shell could not complete a maintenance check.')
        return body

    def overlay_free_bytes(self, candidate):
        self.assert_boot()
        free_bytes = rc.overlay_free_bytes(self.shell, candidate)
        self.assert_boot()
        return free_bytes

    def boot_id(self):
        return self.shell('cat /proc/sys/kernel/random/boot_id').strip()

    def assert_boot(self):
        if self.boot_id() != self.boot:
            raise Blocked('The device rebooted or changed. Inspect and prepare a new request.')

    def identity(self):
        # Device tree IDs are read-only. The exact native app and stock hook
        # provenance are checked separately by lifecycle preflight.
        info = self.shell("grep '^Uid:' /proc/self/status && tr '\\000' '\\n' </proc/device-tree/compatible").splitlines()
        if not info or info[0].split() != ['Uid:', '0', '0', '0', '0'] or 'allwinner,v853' not in info[1:]:
            raise Blocked('Authorized root maintenance shell and verified V853 device tree are required.')
        self.assert_boot()
        return {'model': 'AT-M105 / V853', 'serial': self.transport[0],
                'usb_port': self.transport[1], 'transport': self.transport[2], 'boot_id': self.boot}

    def kinds(self, paths):
        if not paths:
            return {}
        q = ' '.join(map(shlex.quote, paths))
        out = self.shell('for p in %s; do if [ -L "$p" ]; then k=link; elif [ -f "$p" ]; then k=file; '
                         'elif [ -d "$p" ]; then k=dir; elif [ -e "$p" ]; then k=other; else k=absent; fi; '
                         'echo "$k $p"; done' % q)
        got = dict(line.split(' ', 1)[::-1] for line in out.splitlines() if ' ' in line)
        return {p: got.get(p, 'unknown') for p in paths}

    def _parents(self, paths):
        parents = {str(p) for name in paths for p in PurePosixPath(name).parents if str(p) != '/'}
        if any(kind != 'dir' for kind in self.kinds(sorted(parents)).values()):
            raise Blocked('Maintenance paths must have real directory parents.')

    def read(self, path, *, limit=128 * 1024):
        self._parents([path])
        if self.kinds([path])[path] != 'file':
            raise Blocked('A maintenance read target is missing or is not a regular file.')
        size = self.shell('wc -c < ' + shlex.quote(path), timeout=40).strip()
        if not size.isascii() or not size.isdecimal() or int(size) > limit:
            raise Blocked('A maintenance read target exceeds its bounded size.')
        with tempfile.TemporaryDirectory(prefix='cammy-usb-') as tmp:
            local = Path(tmp) / 'file'
            self._adb('pull', path, str(local), timeout=90)
            if local.stat().st_size != int(size):
                raise Blocked('USB file size changed during readback.')
            return local.read_bytes()

    def listdir(self, path):
        if self.kinds([path])[path] != 'dir':
            raise Blocked('A maintenance state directory changed.')
        names = self.shell('ls -A1 ' + shlex.quote(path)).splitlines()
        if len(names) > (4096 if path == rc.CFG else 64) or len(set(names)) != len(names) or any(not rc.NAME.fullmatch(n) for n in names):
            raise Blocked('Maintenance state contains unexpected entries. Preserve it for manual inspection.')
        return names

    def hashes(self, base, names, _unused_busybox):
        # Check the whole slot and hash copies on the PC. Even an unexpected
        # optional enable-file must not change the reviewed trial's behavior.
        expected = set(names)
        if expected != rc.rc_wifi_policy.TF_MEMBERS or set(self.listdir(base)) != {'native', 'runtime'}:
            raise Blocked('The TF slot has unexpected members.')
        for folder in ('native', 'runtime'):
            members = {name.split('/', 1)[1] for name in expected if name.startswith(folder + '/')}
            if members != set(self.listdir(base + '/' + folder)):
                raise Blocked('The TF slot is missing members or contains additional files.')
        return {name: rc.sha(self.read(base + '/' + name, limit=MAX_PAYLOAD)) for name in names}

    def tf_mount(self):
        mount = self.shell('cat /proc/mounts').splitlines()
        matches = [line.split() for line in mount if len(line.split()) >= 4
                   and line.split()[1] == '/mnt/extsd']
        if (len(matches) != 1 or not re.fullmatch(r'/dev/mmcblk0(?:p1)?', matches[0][0])
                or matches[0][2] not in {'vfat', 'msdos'} or 'rw' not in matches[0][3].split(',')):
            raise Blocked('Insert the camera-formatted TF card and confirm it is mounted read/write.')
        return matches[0][0]

    def tf_identity(self):
        mount = self.tf_mount()
        cid = self.shell('cat /sys/block/mmcblk0/device/cid').strip().lower()
        if not rc.HEX32.fullmatch(cid):
            raise Blocked('The TF card does not expose a verifiable card identity.')
        return mount, cid

    def _before_write(self, paths):
        self.assert_boot()
        if self.approved_tf is not None and self.tf_identity() != self.approved_tf:
            raise Blocked('The TF card changed after approval.')
        self._parents(paths)

    def push_file(self, local, path):
        self._before_write([path])
        if self.kinds([path])[path] != 'absent':
            raise Blocked('A staging target already exists. Inspect the interrupted transaction.')
        self._adb('push', str(local), path, timeout=90)
        self.shell('sync')

    def write(self, path, data, mode):
        with tempfile.TemporaryDirectory(prefix='cammy-usb-') as tmp:
            local = Path(tmp) / 'file'
            local.write_bytes(data)
            self.push_file(local, path)
        self._before_write([path])
        self.shell('chmod %s %s && sync' % (mode, shlex.quote(path)))

    def rename(self, src, dst):
        self._before_write([src, dst])
        qsrc, qdst = map(shlex.quote, (src, dst))
        self.shell(f'[ ! -L {qsrc} ] && [ ! -L {qdst} ] && [ ! -d {qdst} ] && mv -f {qsrc} {qdst} && sync')

    def rename_new(self, src, dst):
        self._before_write([src, dst])
        qsrc, qdst = map(shlex.quote, (src, dst))
        self.shell(f'[ ! -L {qsrc} ] && [ ! -L {qdst} ] && [ ! -e {qdst} ] && mv {qsrc} {qdst} && sync')

    def mkdir(self, path):
        self._before_write([path])
        qpath = shlex.quote(path)
        command = f'mkdir {qpath}'
        if not path.startswith('/mnt/extsd/'):
            command += f' && chmod 700 {qpath}'
        self.shell(command + ' && sync')

    def remove(self, path):
        self._before_write([path])
        qpath = shlex.quote(path)
        self.shell(f'[ ! -L {qpath} ] && [ ! -d {qpath} ] && rm -f {qpath} && sync')

    def archive_md5(self, path):
        self.assert_boot()
        self._parents([path])
        return rc.archive_md5(self.shell, path)

    def archive_remove(self, path, md5, boot):
        self._before_write([path])
        rc.remove_archive_entry(self.shell, path, md5, boot)
