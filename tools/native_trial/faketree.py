"""FAKE camera root for selector tests. Nothing here touches a real device.

A FakeTree is an ordinary temporary directory laid out like the camera paths
the selector proposal uses (``etc/cammy``, ``usr/bin/sdvcam``, ``proc/mounts``,
``run``...). ``mount``/``umount``/``sync``/``cp`` are emulated in Python; the
same functions back both the Python reference model (via :class:`TreeEnv`) and
the PATH stubs used when the relocated shell proposal runs under ``dash``, so
the two implementations see identical fake semantics.

Every path argument must stay inside the fake root; anything else is recorded
as a VIOLATION and refused. Power loss is emulated by :class:`PowerLoss` (Python)
or SIGKILL of the hook shell (stub), followed by :meth:`FakeTree.reboot`, which
keeps only synced internal state.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
CARD_DIR = 'CammyOS-native'
STUBS = ('mount', 'umount', 'sync', 'cp')
UPTIME = '12.34 10.00\n'


class PowerLoss(Exception):
    """Emulated power loss at a primitive operation."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeTree:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).resolve()
        self.ctl = self.root / '.fake'

    # ---- layout helpers -------------------------------------------------
    def p(self, logical: str) -> str:
        """Relocated absolute path for a camera path such as /usr/bin/sdvcam."""
        assert logical.startswith('/')
        return str(self.root) + logical

    def path(self, logical: str) -> Path:
        return Path(self.p(logical))

    @property
    def state_dir(self) -> Path:
        return self.path('/etc/cammy/native-select')

    @property
    def card(self) -> Path:
        return self.ctl / 'card'

    def faults(self) -> set[str]:
        try:
            return set((self.ctl / 'faults').read_text().split())
        except FileNotFoundError:
            return set()

    def set_faults(self, *names: str) -> None:
        (self.ctl / 'faults').write_text(''.join(f'{n}\n' for n in names))

    def log(self, line: str) -> None:
        with open(self.ctl / 'calls.log', 'a') as handle:
            handle.write(line.replace(str(self.root), '<R>') + '\n')

    def calls(self) -> list[str]:
        try:
            return (self.ctl / 'calls.log').read_text().splitlines()
        except FileNotFoundError:
            return []

    def _inside(self, value: str) -> bool:
        try:
            resolved = Path(os.path.realpath(value))
        except OSError:
            return False
        return (resolved == self.root or self.root in resolved.parents) and not (
            resolved == self.ctl or self.ctl in resolved.parents)

    def _violation(self, what: str) -> int:
        self.log(f'VIOLATION {what}')
        return 97

    def mounts(self) -> list[str]:
        return self.path('/proc/mounts').read_text().splitlines()

    def _write_mounts(self, lines: list[str]) -> None:
        self.path('/proc/mounts').write_text(''.join(f'{line}\n' for line in lines))

    def mounted_at(self, target: str) -> bool:
        return any(len(f) > 1 and f[1] == target for f in (line.split() for line in self.mounts()))

    def _counter(self, name: str) -> int:
        path = self.ctl / name
        value = int(path.read_text()) + 1 if path.exists() else 1
        path.write_text(str(value))
        return value

    # ---- emulated primitives (shared by model and stubs) ----------------
    def mount(self, argv: list[str]) -> int:
        self.log('mount ' + ' '.join(argv))
        faults = self.faults()
        if len(argv) == 4 and argv[:2] == ['-o', 'bind']:
            src, dst = argv[2], argv[3]
            if not (self._inside(src) and self._inside(dst)):
                return self._violation('bind outside root')
            if 'bind-fail' in faults or self.mounted_at(dst) or not os.path.isfile(src):
                return 1
            (self.ctl / 'bind-saved').write_bytes(Path(dst).read_bytes())
            (self.ctl / 'bind-mode').write_text(oct(os.stat(dst).st_mode & 0o7777))
            (self.ctl / 'bind-target').write_text(dst)
            os.unlink(dst)
            if 'bind-wrong-content' in faults or 'bind-foreign-exec' in faults:
                # The mount shows some other file (models unexplained mount behaviour).
                Path(dst).write_bytes(Path(src).read_bytes() + b'BAD')
                os.chmod(dst, 0o755 if 'bind-foreign-exec' in faults else os.stat(src).st_mode & 0o7777)
            else:
                # A real file bind mount shares the source inode: same bytes, same mode.
                os.link(src, dst)
            opts = 'rw,noexec' if 'bind-noexec' in faults else 'rw'
            self._write_mounts(self.mounts() + [f'tmpfs {dst} tmpfs {opts} 0 0'])
            return 0
        if len(argv) == 6 and argv[:3] == ['-t', 'vfat', '-o']:
            opts, dev, mnt = argv[3:]
            if not (self._inside(dev) and self._inside(mnt)):
                return self._violation('vfat mount outside root')
            if 'ro' not in opts.split(','):
                return self._violation('tf mount not read-only')
            if not os.path.exists(dev) or not os.path.isdir(mnt) or self.mounted_at(mnt):
                return 1
            if 'tf-mount-fail' in faults or not self.card.is_dir():
                return 1
            shutil.copytree(self.card, mnt, dirs_exist_ok=True, symlinks=True)
            self._write_mounts(self.mounts() + [f'{dev} {mnt} vfat {opts} 0 0'])
            return 0
        return 2

    def umount(self, argv: list[str]) -> int:
        self.log('umount ' + ' '.join(argv))
        lazy = argv[:1] == ['-l']
        if len(argv) != (2 if lazy else 1):
            return 2
        target = argv[-1]
        if not self._inside(target):
            return self._violation('umount outside root')
        if not self.mounted_at(target):
            return 1
        faults = self.faults()
        bind_target = self.ctl / 'bind-target'
        if bind_target.exists() and bind_target.read_text() == target:
            if 'app-umount-fail' in faults:
                return 1
            os.unlink(target)
            Path(target).write_bytes((self.ctl / 'bind-saved').read_bytes())
            os.chmod(target, int((self.ctl / 'bind-mode').read_text(), 8))
            (self.ctl / 'bind-saved').unlink()
            (self.ctl / 'bind-mode').unlink()
            bind_target.unlink()
        else:
            if ('tf-umount-lazy-fail' if lazy else 'tf-umount-fail') in faults:
                return 1
            if _tree_digest(Path(target)) != _tree_digest(self.card):
                self.log('VIOLATION tf contents changed while mounted')
            for child in Path(target).iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        self._write_mounts([line for line in self.mounts()
                            if not (len(line.split()) > 1 and line.split()[1] == target)])
        return 0

    def sync(self, argv: list[str] | None = None) -> int:
        count = self._counter('sync-count')
        self.log('sync')
        faults = self.faults()
        if f'powerloss-at-sync-{count}' in faults:
            raise PowerLoss(f'sync {count}')
        if 'corrupt-on-sync' in faults:
            synced = self._synced()
            for name, path in self._state_files().items():
                if synced.get(name) != _sha(path.read_bytes()):
                    data = path.read_bytes()
                    path.write_bytes(data[:len(data) // 2])
        self._record_synced()
        return 0

    def cp(self, argv: list[str]) -> int:
        self.log('cp ' + ' '.join(argv))
        if len(argv) != 2:
            return 2
        src, dst = argv
        if not (self._inside(src) and self._inside(dst)):
            return self._violation('cp outside root')
        count = self._counter('cp-count')
        faults = self.faults()
        if {'copy-fail', f'copy-fail-{count}'} & faults or not os.path.isfile(src):
            return 1
        data = Path(src).read_bytes()
        if {'copy-corrupt', f'copy-corrupt-{count}'} & faults and data:
            data = data[:-1] + bytes([data[-1] ^ 1])
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        return 0

    # ---- durability bookkeeping -----------------------------------------
    def _state_files(self) -> dict[str, Path]:
        if not self.state_dir.is_dir():
            return {}
        return {p.name: p for p in self.state_dir.iterdir() if p.is_file() and not p.is_symlink()}

    def _synced(self) -> dict[str, str]:
        try:
            return json.loads((self.ctl / 'synced.json').read_text())
        except FileNotFoundError:
            return {}

    def _record_synced(self) -> None:
        (self.ctl / 'synced.json').write_text(json.dumps(
            {name: _sha(path.read_bytes()) for name, path in self._state_files().items()}))

    def reboot(self, boot_id: str, unsynced: str = 'lose', faults: tuple[str, ...] = ()) -> None:
        """Emulate a power cycle: RAM, mounts and unsynced internal writes go."""
        assert unsynced in ('lose', 'keep', 'truncate')
        synced = self._synced()
        for name, path in self._state_files().items():
            if synced.get(name) == _sha(path.read_bytes()):
                continue
            if unsynced == 'lose':
                path.unlink()
            elif unsynced == 'truncate':
                data = path.read_bytes()
                path.write_bytes(data[:len(data) // 2])
        for ram in (self.path('/run'), self.path('/tmp')):
            for child in ram.iterdir():
                if child.is_dir() and not child.is_symlink():
                    child.chmod(0o700)
                    shutil.rmtree(child)
                else:
                    child.unlink()
        if (self.ctl / 'bind-saved').exists():
            target = Path((self.ctl / 'bind-target').read_text())
            target.unlink()
            target.write_bytes((self.ctl / 'bind-saved').read_bytes())
            target.chmod(int((self.ctl / 'bind-mode').read_text(), 8))
            for name in ('bind-saved', 'bind-mode', 'bind-target'):
                (self.ctl / name).unlink()
        self._write_mounts((self.ctl / 'mounts-base').read_text().splitlines())
        self.path('/proc/sys/kernel/random/boot_id').write_text(boot_id + '\n')
        (self.ctl / 'sync-count').unlink(missing_ok=True)
        (self.ctl / 'cp-count').unlink(missing_ok=True)
        self.set_faults(*faults)
        self._record_synced()

    # ---- fixture construction --------------------------------------------
    @classmethod
    def build(cls, root, *, boot_id: str, source: bytes, s01app: bytes, allow: bytes | None,
              mode: str | None = 'select', card: dict[str, bytes] | None = None,
              devices: tuple[str, ...] = ('mmcblk0p1',), mem_available_kb: int = 60000,
              run_tmpfs: bool = True, tmp_tmpfs: bool = True, extra_mounts: tuple[str, ...] = (),
              state: dict[str, bytes] | None = None) -> 'FakeTree':
        tree = cls(root)
        tree.ctl.mkdir(parents=True)
        for directory in ('/etc/cammy', '/etc/init.d', '/usr/bin', '/run', '/tmp', '/proc/sys/kernel/random',
                          '/dev', '/mnt/extsd'):
            tree.path(directory).mkdir(parents=True, exist_ok=True)
        if mode is not None:
            tree.path('/etc/cammy/native-select.enabled').write_text(f'mode={mode}\n' if '=' not in mode else mode)
        if allow is not None:
            tree.path('/etc/cammy/native-select.allow').write_bytes(allow)
        if state is not None:
            tree.state_dir.mkdir()
            for name, data in state.items():
                (tree.state_dir / name).write_bytes(data)
        tree.path('/etc/init.d/S01app').write_bytes(s01app)
        tree.path('/usr/bin/sdvcam').write_bytes(source)
        tree.path('/usr/bin/sdvcam').chmod(0o755)
        lines = ['rootfs / rootfs rw 0 0', f'proc {tree.p("/proc")} proc rw 0 0']
        if run_tmpfs:
            lines.append(f'tmpfs {tree.p("/run")} tmpfs rw,nosuid,nodev 0 0')
        if tmp_tmpfs:
            lines.append(f'tmpfs {tree.p("/tmp")} tmpfs rw,nosuid,nodev 0 0')
        lines += [line.replace('<R>', str(tree.root)) for line in extra_mounts]
        (tree.ctl / 'mounts-base').write_text(''.join(f'{line}\n' for line in lines))
        tree._write_mounts(lines)
        tree.path('/proc/meminfo').write_text(
            f'MemTotal:         120000 kB\nMemFree:           20000 kB\nMemAvailable:   {mem_available_kb:>8} kB\n')
        tree.path('/proc/uptime').write_text(UPTIME)
        tree.path('/proc/sys/kernel/random/boot_id').write_text(boot_id + '\n')
        for device in devices:
            tree.path(f'/dev/{device}').write_bytes(b'')
        if card is not None:
            (tree.card / CARD_DIR).mkdir(parents=True)
            for name, data in card.items():
                (tree.card / CARD_DIR / name).write_bytes(data)
        bindir = tree.ctl / 'bin'
        bindir.mkdir()
        for name in STUBS:
            stub = bindir / name
            stub.write_text('#!/bin/sh\nexec "%s" -I -c \'import sys; sys.path.insert(0, sys.argv[1]); '
                            'from native_trial.faketree import stub_main; sys.exit(stub_main(sys.argv[2:]))\' '
                            '"%s" "%s" %s "$@"\n' % (sys.executable, TOOLS_DIR, tree.root, name))
            stub.chmod(0o700)
        tree._record_synced()
        return tree

    def snapshot(self) -> dict:
        """Normalized observable state for model/shell comparison."""
        def text(logical):
            path = self.path(logical)
            return path.read_bytes().decode('latin-1') if path.is_file() else None
        state = {name: path.read_bytes().decode('latin-1') for name, path in sorted(self._state_files().items())}
        run = sorted(str(p.relative_to(self.root)) for ram in ('/run', '/tmp') for p in self.path(ram).rglob('*'))
        return {
            'state': state,
            'status': text('/run/cammy-select/status'),
            'run': run,
            'app_sha256': _sha(self.path('/usr/bin/sdvcam').read_bytes()),
            'mounts': [line.replace(str(self.root), '<R>') for line in self.mounts()],
            'calls': self.calls(),
        }


def _tree_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    if directory.is_dir():
        for path in sorted(directory.rglob('*')):
            digest.update(str(path.relative_to(directory)).encode())
            if path.is_file() and not path.is_symlink():
                digest.update(path.read_bytes())
    return digest.hexdigest()


class TreeEnv:
    """Adapter giving the Python selector model the shell's primitive view."""

    def __init__(self, tree: FakeTree, powerloss_at_op: int | None = None):
        self.tree = tree
        self.powerloss_at_op = powerloss_at_op
        self.ops: list[str] = []

    def tick(self, op: str) -> None:
        self.ops.append(op)
        if self.powerloss_at_op is not None and len(self.ops) == self.powerloss_at_op:
            raise PowerLoss(op)

    p = property(lambda self: self.tree.p)

    @staticmethod
    def read(path: str) -> bytes | None:
        try:
            return Path(path).read_bytes() if os.path.isfile(path) else None
        except OSError:
            return None

    exists = staticmethod(os.path.exists)
    isfile = staticmethod(os.path.isfile)
    islink = staticmethod(os.path.islink)
    isdir = staticmethod(os.path.isdir)

    @staticmethod
    def glob(directory: str, prefix: str = '') -> list[str]:
        try:
            names = os.listdir(directory)
        except OSError:
            return []
        return [f'{directory}/{n}' for n in sorted(names) if not n.startswith('.') and n.startswith(prefix)]

    def tools_ok(self) -> bool:
        """Mirror of the proposal's primitive self-test (fault: tools-broken)."""
        return 'tools-broken' not in self.tree.faults()

    def awk_ok(self) -> bool:
        """Fault ``awk-broken`` mirrors a missing awk in the shell's PATH."""
        return 'awk-broken' not in self.tree.faults()

    @staticmethod
    def executable(path: str) -> bool:
        """Mirror of ``[ -x path ]``."""
        return os.path.isfile(path) and os.access(path, os.X_OK)

    def md5(self, path: str) -> str:
        data = self.read(path)
        return '' if data is None else hashlib.md5(data).hexdigest()

    def size(self, path: str) -> str:
        data = self.read(path)
        return '' if data is None else str(len(data))

    def mkdir(self, path: str) -> bool:
        self.tick('mkdir')
        try:
            os.mkdir(path, 0o700)
            return True
        except OSError:
            return False

    def mkdir_p(self, path: str) -> bool:
        self.tick('mkdir')
        try:
            os.makedirs(path, 0o700, exist_ok=True)
            return True
        except OSError:
            return False

    def write_exclusive(self, path: str, data: bytes) -> bool:
        self.tick('create')
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            return False
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        return True

    def write_status(self, path: str, data: bytes) -> None:
        self.tick('status')
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
        except OSError:
            pass

    def rm(self, path: str) -> None:
        self.tick('rm')
        try:
            os.unlink(path)
        except OSError:
            pass

    def rmdir(self, path: str) -> None:
        self.tick('rmdir')
        try:
            os.rmdir(path)
        except OSError:
            pass

    def chmod(self, path: str, mode: int) -> bool:
        self.tick('chmod')
        try:
            os.chmod(path, mode)
            return True
        except OSError:
            return False

    def mount(self, *argv: str) -> int:
        self.tick('mount')
        return self.tree.mount(list(argv))

    def umount(self, *argv: str) -> int:
        self.tick('umount')
        return self.tree.umount(list(argv))

    def sync(self) -> None:
        self.tick('sync')
        self.tree.sync()

    def cp(self, src: str, dst: str) -> int:
        self.tick('cp')
        return self.tree.cp([src, dst])

    def mounts(self) -> list[str]:
        return self.tree.mounts()

    def uptime(self) -> str:
        data = self.read(self.p('/proc/uptime')) or b''
        return data.decode('latin-1').split('\n')[0].split(' ')[0]


def stub_main(argv: list[str]) -> int:
    """Entry point of the PATH stubs: ``<root> <command> args...``."""
    root, command, *args = argv
    tree = FakeTree(root)
    if command not in STUBS or not tree.ctl.is_dir():
        return 99
    try:
        return getattr(tree, command)(args)
    except PowerLoss:
        os.kill(os.getppid(), signal.SIGKILL)
        return 137
