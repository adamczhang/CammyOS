"""Python reference model and safe fake-tree harness for Chunk 4/5 boot-launch.

Mirrors native/activation/launch_selected_or_stock.proposal and the transformed
S01app backup/firstboot/launch decision block (stock lines 78-119) without
touching hardware, real mounts, or host MMIO/sysfs paths.

Safety rules enforced:
- Every file/mount/command operation in BootLaunchFakeTree is confined to the
  temporary fake root; any path outside the fake root records a VIOLATION and
  fails closed.
- The relocated shell harness executes under dash (never busybox ash) with PATH
  stubs intercepting cp/rm/du/df/gettimems/md5sum/awk/tail/mount/umount/sync/chmod
  and synthetic non-vendor stock and candidate stub executables.
- Relocation is explicit test-only code that rewrites ONLY the marked header
  blocks on a synthetic copy inside the temporary fixture and marks the fixture
  descriptor synthetic/activation_eligible=False. Neither the helper nor the
  generator accepts any environment variable override for paths or hashes.
- Reuses tools/native_trial/records.py as a read-only dependency for record
  formats, key ordering, and bounds.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from native_trial import records as R

from .generator import (
    HELPER_PROPOSAL_PATH,
    STOCK_SDVCAM_MD5,
    STOCK_SDVCAM_SIZE,
)

TOOLS_DIR = Path(__file__).resolve().parents[1]
DASH = shutil.which('dash')
UPTIME = '12.34 10.00\n'
SYNTHETIC_INIT_BYTES = b'#!/bin/sh\n# Synthetic init identity fixture; never executed.\n'

INFLIGHT_FORMAT = 'cammy-boot-launch-inflight/1'
INFLIGHT_KEYS = ('format', 'boot', 'mode')

RELOCATABLE_PATHS = (
    'INITFILE',
    'CFG',
    'STATE',
    'APP',
    'RUNROOT',
    'RUNDIR',
    'PROCFS',
    'DEVDIR',
    'EXTSD',
    'OVERLAY',
    'TMPROOT',
    'LIBROOT',
    'BRIDGEDIR',
    'BLOCKTEST',
)
STUB_COMMANDS = (
    'cp',
    'rm',
    'du',
    'df',
    'gettimems',
    'md5sum',
    'awk',
    'tail',
    'mount',
    'umount',
    'sync',
    'chmod',
)

SYNTHETIC_STOCK_BYTES = (
    b'#!/bin/sh\n'
    b'echo "sdvcam-stub" >> .fake/launched.log\n'
    b'exit 0\n'
)

SYNTHETIC_CANDIDATE_BYTES = (
    b'#!/bin/sh\n'
    b'echo "candidate-stub" >> .fake/launched.log\n'
    b'echo "candidate-core=$(ulimit -c)" >> .fake/candidate-core.log\n'
    b'exit 0\n'
)

SYNTHETIC_LIBRARY_BYTES = b'CAMMY-SYNTHETIC-STATUS-LIB-V1\n'


class PowerLoss(RuntimeError):
    """Simulated abrupt power loss or interruption at a numbered boundary."""


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_uuid(value: str) -> bool:
    return R.is_uuid(value)


def inflight_record(boot: str, mode: str) -> bytes:
    if not R.is_uuid(boot) or mode not in ('observe', 'select'):
        raise R.RecordError('inflight values invalid')
    return R.render(INFLIGHT_KEYS, {'format': INFLIGHT_FORMAT, 'boot': boot, 'mode': mode})


def _tree_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    if directory.is_dir():
        for path in sorted(directory.rglob('*')):
            digest.update(str(path.relative_to(directory)).encode())
            if path.is_file() and not path.is_symlink():
                digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class HelperDecision:
    prepare_decision: str
    prepare_reason: str
    mode: str
    backup_eligible: int
    launch_attempted: bool
    launch_started: bool
    launch_decision: str
    launch_reason: str
    attempt: str = 'none'


@dataclass(frozen=True)
class S01AppBranchOutcome:
    branch: str
    launch_site_stock_line: int
    helper: HelperDecision
    backup_written: bool
    backup_removed: bool
    firstboot_cleared: bool
    isp_cleared: bool
    app_started_count: int


class BootLaunchFakeTree:
    """Isolated temporary directory fixture emulating the camera paths used by S01app launch."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).resolve()
        self.ctl = self.root / '.fake'
        self.card = self.ctl / 'card'
        self.state_dir = self.root / 'etc/cammy/native-select'

    def p(self, logical: str) -> str:
        assert logical.startswith('/'), logical
        return str(self.root) + logical

    def path(self, logical: str) -> Path:
        return Path(self.p(logical))

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

    def launched_entries(self) -> list[str]:
        try:
            return (self.ctl / 'launched.log').read_text().splitlines()
        except FileNotFoundError:
            return []

    def launched_Count(self) -> int:
        return len(self.launched_entries())

    def candidate_core_logs(self) -> list[str]:
        try:
            return (self.ctl / 'candidate-core.log').read_text().splitlines()
        except FileNotFoundError:
            return []

    def inside(self, value: str) -> bool:
        try:
            resolved = Path(os.path.realpath(value))
        except OSError:
            return False
        return (resolved == self.root or self.root in resolved.parents) and not (
            resolved == self.ctl or self.ctl in resolved.parents
        )

    def violation(self, what: str) -> int:
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

    def _state_files(self) -> dict[str, Path]:
        if not self.state_dir.is_dir() or self.state_dir.is_symlink():
            return {}
        out: dict[str, Path] = {}
        for p in sorted(self.state_dir.iterdir()):
            if p.is_file() and not p.is_symlink():
                out[p.name] = p
        return out

    def _synced(self) -> dict[str, str]:
        try:
            return json.loads((self.ctl / 'synced.json').read_text())
        except FileNotFoundError:
            return {}

    def _record_synced(self) -> None:
        (self.ctl / 'synced.json').write_text(
            json.dumps({name: _sha256(path.read_bytes()) for name, path in self._state_files().items()})
        )

    def sync_op(self) -> int:
        count = self._counter('sync-count')
        self.log('sync')
        faults = self.faults()
        if f'powerloss-at-sync-{count}' in faults:
            raise PowerLoss(f'sync {count}')
        if {'sync-fail', f'sync-fail-{count}'} & faults:
            return 1
        if {'corrupt-on-sync', f'corrupt-on-sync-{count}'} & faults:
            synced = self._synced()
            for name, path in self._state_files().items():
                if synced.get(name) != _sha256(path.read_bytes()):
                    data = path.read_bytes()
                    path.write_bytes(data[: max(1, len(data) // 2)])
        self._record_synced()
        return 0

    def mount_op(self, argv: list[str]) -> int:
        self.log('mount ' + ' '.join(argv))
        faults = self.faults()
        count = self._counter('mount-count')
        if f'powerloss-at-mount-{count}' in faults:
            raise PowerLoss(f'mount {count}')
        if len(argv) == 6 and argv[:3] == ['-t', 'vfat', '-o']:
            opts, dev, mnt = argv[3:]
            if not (self.inside(dev) and self.inside(mnt)):
                return self.violation(f'vfat mount outside root: {dev} -> {mnt}')
            if 'ro' not in opts.split(','):
                return self.violation(f'tf mount not read-only: {opts}')
            if not os.path.exists(dev) or not os.path.isdir(mnt) or self.mounted_at(mnt):
                return 1
            if 'tf-mount-partial' in faults:
                self._write_mounts(self.mounts() + [f'{dev} {mnt} vfat {opts} 0 0'])
                return 1
            if 'tf-mount-fail' in faults or not self.card.is_dir():
                return 1
            shutil.copytree(self.card, mnt, dirs_exist_ok=True, symlinks=True)
            self._write_mounts(self.mounts() + [f'{dev} {mnt} vfat {opts} 0 0'])
            return 0
        return self.violation(f'disallowed mount argv: {argv}')

    def umount_op(self, argv: list[str]) -> int:
        self.log('umount ' + ' '.join(argv))
        lazy = argv[:1] == ['-l']
        if len(argv) != (2 if lazy else 1):
            return 2
        target = argv[-1]
        if not self.inside(target):
            return self.violation(f'umount outside root: {target}')
        if not self.mounted_at(target):
            return 1
        faults = self.faults()
        if ('tf-umount-lazy-fail' if lazy else 'tf-umount-fail') in faults:
            return 1
        if self.card.is_dir() and _tree_digest(Path(target)) != _tree_digest(self.card):
            self.log('VIOLATION tf contents changed while mounted')
        for child in Path(target).iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        self._write_mounts([
            line for line in self.mounts()
            if not (len(line.split()) > 1 and line.split()[1] == target)
        ])
        return 0

    def cp_op(self, argv: list[str]) -> int:
        if len(argv) != 2:
            return 2
        src, dst = argv
        if not (self.inside(src) and self.inside(dst)):
            return self.violation(f'cp outside root: {src} -> {dst}')
        self.log(f'cp {src} {dst}')
        count = self._counter('cp-count')
        faults = self.faults()
        if f'powerloss-at-cp-{count}' in faults:
            raise PowerLoss(f'cp {count}')
        if {'cp-fail', 'copy-fail', f'copy-fail-{count}'} & faults or not os.path.isfile(src):
            return 1
        data = Path(src).read_bytes()
        if {'copy-corrupt', f'copy-corrupt-{count}'} & faults and data:
            data = data[:-1] + bytes([data[-1] ^ 1])
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        return 0

    def chmod_op(self, argv: list[str]) -> int:
        if len(argv) != 2:
            return 2
        mode_str, target = argv
        if not self.inside(target):
            return self.violation(f'chmod outside root: {target}')
        self.log(f'chmod {mode_str} {target}')
        count = self._counter('chmod-count')
        faults = self.faults()
        if {'chmod-fail', f'chmod-fail-{count}'} & faults:
            return 1
        try:
            os.chmod(target, int(mode_str, 8))
            return 0
        except OSError:
            return 1

    def reboot(
        self,
        boot_id: str,
        *,
        unsynced: str = 'lose',
        card_present: bool | None = None,
        faults: tuple[str, ...] = (),
    ) -> None:
        """Emulate a power cycle: RAM, private mounts, and unsynced internal writes vanish."""
        assert unsynced in ('lose', 'keep', 'truncate')
        synced = self._synced()
        for name, path in list(self._state_files().items()):
            if synced.get(name) == _sha256(path.read_bytes()):
                continue
            if unsynced == 'lose':
                path.unlink()
            elif unsynced == 'truncate':
                data = path.read_bytes()
                path.write_bytes(data[: max(1, len(data) // 2)])

        for ram in (self.path('/run'), self.path('/tmp')):
            if not ram.is_dir():
                continue
            for child in list(ram.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    for sub in sorted(child.rglob('*'), reverse=True):
                        if sub.is_dir() and not sub.is_symlink():
                            sub.chmod(0o700)
                    child.chmod(0o700)
                    shutil.rmtree(child)
                else:
                    child.unlink()

        base_mounts = (self.ctl / 'mounts-base').read_text().splitlines()
        self._write_mounts(base_mounts)
        self.path('/proc/sys/kernel/random/boot_id').write_text(boot_id + '\n')
        for counter in ('sync-count', 'cp-count', 'mount-count', 'chmod-count'):
            (self.ctl / counter).unlink(missing_ok=True)

        if card_present is False:
            for dev in ('mmcblk0', 'mmcblk0p1'):
                self.path(f'/dev/{dev}').unlink(missing_ok=True)
            if self.card.is_dir():
                shutil.rmtree(self.card)
        elif card_present is True:
            self.path('/dev/mmcblk0').write_bytes(b'')

        self.set_faults(*faults)
        self._record_synced()

    @classmethod
    def build(
        cls,
        root: str | os.PathLike,
        *,
        boot_id: str = '00000001-0000-4000-8000-000000000001',
        enabled_content: str | bytes | None = None,
        allow_bytes: bytes | None = None,
        state_files: dict[str, bytes] | None = None,
        card_files: dict[str, bytes] | None = None,
        devices: tuple[str, ...] = ('mmcblk0',),
        mem_available_kb: int = 421304,
        run_tmpfs: bool = True,
        run_noexec: bool = False,
        tmp_tmpfs: bool = True,
        tmp_noexec: bool = False,
        extsd_vfat: bool = False,
        unrelated_vfat_mount: str | None = None,
        private_tf_mounted: bool = False,
        private_tf_dir_exists: bool = False,
        private_sdvcam_residue: bytes | None = None,
        firstboot: bool = False,
        existing_card_sdvcam: bytes | None = None,
        existing_data_app_md5: str | None = None,
        isp_files: tuple[str, ...] = ('isp_param.bin',),
        df_free_kb: int = 64000,
        du_app_kb: int = 11070,
        running_comm: str | None = None,
        stock_app_matches: bool = True,
        stock_app_executable: bool = True,
        candidate_stub_bytes: bytes = SYNTHETIC_CANDIDATE_BYTES,
        faults: tuple[str, ...] = (),
    ) -> BootLaunchFakeTree:
        tree = cls(root)
        if tree.root.exists():
            shutil.rmtree(tree.root)
        for directory in (
            '/etc/cammy',
            '/etc/init.d',
            '/usr/bin',
            '/run',
            '/tmp',
            '/proc/sys/kernel/random',
            '/dev',
            '/mnt/extsd',
            '/data',
            '/overlay',
        ):
            tree.path(directory).mkdir(parents=True, exist_ok=True)
        tree.ctl.mkdir(parents=True, exist_ok=True)
        tree.path('/etc/init.d/S01app').write_bytes(SYNTHETIC_INIT_BYTES)

        # Synthetic non-vendor stub executable for /usr/bin/sdvcam.
        app_path = tree.path('/usr/bin/sdvcam')
        fixture_app_bytes = SYNTHETIC_STOCK_BYTES
        stub_script = fixture_app_bytes
        if not stock_app_matches:
            stub_script = fixture_app_bytes + b'# synthetic mismatch padding\n'
        app_path.write_bytes(stub_script)
        app_path.chmod(0o755 if stock_app_executable else 0o644)

        # Record synthetic fixture identity (explicitly non-deployable).
        (tree.ctl / 'synthetic-identities.json').write_text(
            json.dumps(
                {
                    'synthetic_fixture': True,
                    'activation_eligible': False,
                    'synthetic_stock_md5': _md5(fixture_app_bytes),
                    'synthetic_stock_size': len(fixture_app_bytes),
                    'synthetic_candidate_md5': _md5(candidate_stub_bytes),
                    'synthetic_candidate_size': len(candidate_stub_bytes),
                    'synthetic_library_sha256': _sha256(SYNTHETIC_LIBRARY_BYTES),
                    'synthetic_library_md5': _md5(SYNTHETIC_LIBRARY_BYTES),
                    'synthetic_library_size': len(SYNTHETIC_LIBRARY_BYTES),
                    'df_free_kb': df_free_kb,
                    'du_app_kb': du_app_kb,
                },
                indent=2,
                sort_keys=True,
            )
            + '\n'
        )

        if enabled_content is not None:
            cfg_file = tree.path('/etc/cammy/native-select.enabled')
            if isinstance(enabled_content, bytes):
                cfg_file.write_bytes(enabled_content)
            else:
                cfg_file.write_text(enabled_content)

        if allow_bytes is not None:
            allow_file = tree.path('/etc/cammy/native-select.allow')
            allow_file.write_bytes(allow_bytes)

        if state_files is not None:
            tree.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name, data in state_files.items():
                (tree.state_dir / name).write_bytes(data)

        for dev in devices:
            tree.path(f'/dev/{dev}').write_bytes(b'')

        if card_files is not None:
            card_native = tree.card / 'CammyOS-native'
            card_native.mkdir(parents=True, exist_ok=True)
            for name, data in card_files.items():
                target = card_native / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)

        mount_lines = ['rootfs / rootfs rw 0 0', f'proc {tree.p("/proc")} proc rw 0 0']
        if run_tmpfs:
            opts = 'rw,nosuid,nodev,noexec' if run_noexec else 'rw,nosuid,nodev'
            mount_lines.append(f'tmpfs {tree.p("/run")} tmpfs {opts} 0 0')
        if tmp_tmpfs:
            opts = 'rw,nosuid,nodev,noexec' if tmp_noexec else 'rw,nosuid,nodev'
            mount_lines.append(f'tmpfs {tree.p("/tmp")} tmpfs {opts} 0 0')
        if extsd_vfat:
            mount_lines.append(f'/dev/mmcblk0 {tree.p("/mnt/extsd")} vfat rw,relatime 0 0')
        if unrelated_vfat_mount:
            target = tree.p(unrelated_vfat_mount)
            Path(target).mkdir(parents=True, exist_ok=True)
            mount_lines.append(f'/dev/mmcblk0p2 {target} vfat rw,relatime 0 0')
        if private_tf_mounted:
            tf_dir = tree.path('/run/cammy-select/tf')
            tf_dir.mkdir(parents=True, exist_ok=True)
            mount_lines.append(f'/dev/mmcblk0 {tf_dir} vfat ro,nosuid,nodev,noexec 0 0')
        elif private_tf_dir_exists:
            tree.path('/run/cammy-select/tf').mkdir(parents=True, exist_ok=True)

        if private_sdvcam_residue is not None:
            res = tree.path('/run/cammy-select/sdvcam')
            res.parent.mkdir(parents=True, exist_ok=True)
            res.write_bytes(private_sdvcam_residue)

        if tree.path('/run/cammy-select').is_dir():
            tree.path('/run/cammy-select').chmod(0o700)

        (tree.ctl / 'mounts-base').write_text(''.join(f'{line}\n' for line in mount_lines))
        tree._write_mounts(mount_lines)
        tree.path('/proc/meminfo').write_text(
            f'MemTotal:         120000 kB\nMemFree:           20000 kB\nMemAvailable:   {mem_available_kb:>8} kB\n'
        )
        tree.path('/proc/uptime').write_text(UPTIME)
        tree.path('/proc/sys/kernel/random/boot_id').write_text(boot_id + '\n')

        if running_comm is not None:
            proc_dir = tree.path('/proc/4242')
            proc_dir.mkdir(parents=True, exist_ok=True)
            (proc_dir / 'comm').write_text(running_comm + '\n')

        if firstboot:
            tree.path('/overlay/.firstboot').write_bytes(b'1\n')
            for isp_name in isp_files:
                (tree.path('/mnt/extsd') / isp_name).write_bytes(b'ISP')

        if existing_card_sdvcam is not None:
            tree.path('/mnt/extsd/.sdvcam').write_bytes(existing_card_sdvcam)

        if existing_data_app_md5 is not None:
            tree.path('/data/app_md5').write_text(existing_data_app_md5 + '\n')

        if faults:
            tree.set_faults(*faults)

        bindir = tree.ctl / 'bin'
        bindir.mkdir(parents=True, exist_ok=True)
        for name in STUB_COMMANDS:
            stub = bindir / name
            stub.write_text(
                '#!/bin/sh\n'
                f'exec "{sys.executable}" -I -c '
                '\'import sys; sys.path.insert(0, sys.argv[1]); '
                'from boot_launch.model import stub_main; sys.exit(stub_main(sys.argv[2:]))\' '
                f'"{TOOLS_DIR}" "{tree.root}" {name} "$@"\n'
            )
            stub.chmod(0o700)

        tree._record_synced()
        return tree

    def synthetic_stock_identity(self) -> tuple[str, int]:
        meta = json.loads((self.ctl / 'synthetic-identities.json').read_text())
        return meta['synthetic_stock_md5'], int(meta['synthetic_stock_size'])

    def snapshot(self) -> dict:
        def read_text(logical: str) -> str | None:
            p = self.path(logical)
            if not p.is_file():
                return None
            return p.read_bytes().decode('latin-1').replace(str(self.root), '<R>')

        state_dir = self.path('/etc/cammy/native-select')
        state_files = {}
        if state_dir.is_dir() and not state_dir.is_symlink():
            for child in sorted(state_dir.iterdir()):
                if child.is_file() and not child.is_symlink():
                    state_files[child.name] = child.read_bytes().decode('latin-1').replace(str(self.root), '<R>')

        isp_remaining = sorted(
            p.name for p in self.path('/mnt/extsd').glob('isp*.bin')
        )
        staged_sdvcam = self.path('/run/cammy-select/sdvcam')
        staged_lib_files = sorted(
            str(p.relative_to(self.root))
            for p in self.path('/tmp').rglob('libcammy_native_status.so')
            if p.is_file()
        )
        return {
            'status': read_text('/run/cammy-select/status'),
            'stamp': read_text('/run/cammy-select/launch.stamp'),
            'state_files': state_files,
            'staged_sdvcam_md5': (
                _md5(staged_sdvcam.read_bytes()) if staged_sdvcam.is_file() else None
            ),
            'staged_sdvcam_mode': (
                oct(staged_sdvcam.stat().st_mode & 0o777) if staged_sdvcam.is_file() else None
            ),
            'staged_lib_files': staged_lib_files,
            'bridge_dir_exists': self.path('/tmp/cammy-bridge').is_dir(),
            'firstboot_exists': self.path('/overlay/.firstboot').exists(),
            'card_sdvcam_md5': (
                _md5(self.path('/mnt/extsd/.sdvcam').read_bytes())
                if self.path('/mnt/extsd/.sdvcam').is_file()
                else None
            ),
            'data_app_md5': (
                read_text('/data/app_md5').strip()
                if self.path('/data/app_md5').is_file()
                else None
            ),
            'isp_remaining': isp_remaining,
            'launched_entries': self.launched_entries(),
            'launched_count': self.launched_Count(),
            'candidate_core_logs': self.candidate_core_logs(),
            'violations': [c for c in self.calls() if 'VIOLATION' in c],
        }


class BootLaunchEnv:
    """Primitive view over BootLaunchFakeTree for the Python reference model."""

    def __init__(self, tree: BootLaunchFakeTree):
        self.tree = tree

    @property
    def p(self):
        return self.tree.p

    def faults(self) -> set[str]:
        return self.tree.faults()

    def top_mount(self, mountpoint: str) -> str:
        if 'awk-broken' in self.faults():
            return ''
        top = ''
        for line in self.tree.mounts():
            fields = line.split()
            if len(fields) > 1 and fields[1] == mountpoint:
                top = (fields[2] if len(fields) > 2 else '') + ' ' + (fields[3] if len(fields) > 3 else '')
        return top

    def mounted_at(self, mountpoint: str) -> bool:
        if 'awk-broken' in self.faults():
            return False
        for line in self.tree.mounts():
            fields = line.split()
            if len(fields) > 1 and fields[1] == mountpoint:
                return True
        return False

    def vfat_mounted(self) -> bool:
        for line in self.tree.mounts():
            if re.search(r'\bvfat\b', line):
                return True
        return False

    def tf_mount_ok(self, mountpoint: str) -> bool:
        if 'awk-broken' in self.faults():
            return False
        top = None
        for line in self.tree.mounts():
            fields = line.split()
            if len(fields) >= 3 and fields[1] == mountpoint:
                top = fields
        dev0 = self.p('/dev/mmcblk0')
        dev1 = self.p('/dev/mmcblk0p1')
        return (
            top is not None
            and top[2] == 'vfat'
            and top[0] in ('/dev/mmcblk0', '/dev/mmcblk0p1', dev0, dev1)
        )

    def submount_under(self, prefix: str) -> bool:
        if 'awk-broken' in self.faults():
            return False
        for line in self.tree.mounts():
            fields = line.split()
            if len(fields) > 1 and fields[1].startswith(prefix):
                return True
        return False

    def tools_ok(self) -> bool:
        faults = self.faults()
        if any(f in faults for f in ('tools-broken', 'md5-broken', 'tail-broken', 'awk-broken')):
            return False
        return True

    def native_owner_running(self, procfs: str) -> bool:
        try:
            entries = sorted(os.listdir(procfs))
        except OSError:
            return False
        for name in entries:
            if not name[:1].isdigit():
                continue
            comm = Path(procfs) / name / 'comm'
            if comm.is_file():
                try:
                    if comm.read_text().rstrip('\n') == 'sdvcam':
                        return True
                except OSError:
                    pass
        return False

    def md5of(self, path: str) -> str:
        if 'md5-broken' in self.faults():
            return ''
        p = Path(path)
        if not p.is_file() or p.is_symlink():
            return ''
        return _md5(p.read_bytes())

    def fsize(self, path: str) -> str:
        p = Path(path)
        if not p.is_file() or p.is_symlink():
            return ''
        return str(len(p.read_bytes()))

    def write_exclusive(self, path: str, content: str) -> bool:
        if 'stamp-write-fail' in self.faults():
            return False
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            return False
        data = (content + '\n').encode('latin-1')
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        try:
            return Path(path).read_bytes() == data
        except OSError:
            return False

    def write_record(self, path: str, content: str) -> bool:
        faults = self.faults()
        name = Path(path).name
        if name == 'inflight' and 'inflight-write-fail' in faults:
            return False
        if name.startswith('claimed-') and 'claim-write-fail' in faults:
            return False
        if name.startswith('observed-') and 'observe-write-fail' in faults:
            return False
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            return False
        data = (content + '\n').encode('latin-1')
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        if self.tree.sync_op() != 0:
            return False
        try:
            return Path(path).read_bytes() == data
        except OSError:
            return False

    def remove_inflight(self, state_dir: str) -> bool:
        inflight = Path(f'{state_dir}/inflight')
        if not inflight.exists() and not inflight.is_symlink():
            return True
        if 'inflight-clear-fail' not in self.faults():
            try:
                inflight.unlink()
            except OSError:
                pass
        if self.tree.sync_op() != 0:
            return False
        return not inflight.exists() and not inflight.is_symlink()

    def disable_core_zero(self) -> bool:
        return 'core-limit-fail' not in self.faults()


def _private_staging_residue(env: BootLaunchEnv, rundir: str) -> bool:
    if env.mounted_at(f'{rundir}/tf'):
        return True
    if env.submount_under(f'{rundir}/'):
        return True
    tf_path = Path(f'{rundir}/tf')
    if tf_path.exists() or tf_path.is_symlink():
        return True
    sdv_path = Path(f'{rundir}/sdvcam')
    if sdv_path.exists() or sdv_path.is_symlink():
        return True
    return False


def _verify_stock_app(env: BootLaunchEnv, app: str, expected_md5: str, expected_size: int) -> bool:
    p = Path(app)
    if not p.is_file() or p.is_symlink() or not os.access(p, os.X_OK):
        return False
    if env.fsize(app) != str(expected_size):
        return False
    if env.md5of(app) != expected_md5:
        return False
    return True


def _private_dir_ok(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    st = path.stat()
    return (st.st_mode & 0o7777) == 0o700 and st.st_uid == os.getuid() and st.st_gid == os.getgid()


def _file_mode_ok(path: Path, expected_mode: int) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    st = path.stat()
    return (st.st_mode & 0o7777) == expected_mode and st.st_uid == os.getuid() and st.st_gid == os.getgid()


def run_helper_model(
    env: BootLaunchEnv,
    *,
    select_gate: str = 'blocked',
    stock_md5: str | None = None,
    stock_size: int | None = None,
    call_launch: bool = True,
) -> HelperDecision:
    """Run the Python reference model of cammy_prepare_launch (+ optional cammy_launch_selected_or_stock)."""
    if stock_md5 is None or stock_size is None:
        stock_md5, stock_size = env.tree.synthetic_stock_identity()

    p = env.p
    CFG = p('/etc/cammy')
    STATE = p('/etc/cammy/native-select')
    APP = p('/usr/bin/sdvcam')
    RUNROOT = p('/run')
    RUNDIR = p('/run/cammy-select')
    PROCFS = p('/proc')
    DEVDIR = p('/dev')
    EXTSD = p('/mnt/extsd')
    OVERLAY = p('/overlay')
    TMPROOT = p('/tmp')
    LIBROOT = p('/tmp/cammy-native-status')
    BRIDGEDIR = p('/tmp/cammy-bridge')

    t0 = '12.34'
    stamp_path = Path(f'{RUNDIR}/launch.stamp')
    status_path = Path(f'{RUNDIR}/status')

    state_ctx = {
        'tfmnt': '',
        'tf_owned': False,
        'stage_owned': False,
        'libdir': '',
        'lib_made': False,
        'cand_md5': '',
        'cand_size': '',
        'lib_sha': '',
        'lib_md5': '',
        'lib_size': '',
    }

    def detach_tf() -> bool:
        if not state_ctx['tf_owned']:
            return True
        tf_dir = Path(f'{RUNDIR}/tf')
        if state_ctx['tfmnt'] or env.mounted_at(f'{RUNDIR}/tf'):
            if env.tree.umount_op([f'{RUNDIR}/tf']) != 0:
                env.tree.umount_op(['-l', f'{RUNDIR}/tf'])
            if env.mounted_at(f'{RUNDIR}/tf'):
                return False
            state_ctx['tfmnt'] = ''
        if tf_dir.is_dir() and not tf_dir.is_symlink():
            try:
                tf_dir.rmdir()
            except OSError:
                return False
        state_ctx['tf_owned'] = False
        return not tf_dir.exists() and not tf_dir.is_symlink()

    def clean_staged_ram() -> None:
        if state_ctx['stage_owned']:
            sdv = Path(f'{RUNDIR}/sdvcam')
            if sdv.exists() or sdv.is_symlink():
                try:
                    sdv.unlink()
                except OSError:
                    pass
            state_ctx['stage_owned'] = False
        if state_ctx['lib_made'] and state_ctx['libdir']:
            lib_file = Path(f"{state_ctx['libdir']}/{R.LIBRARY_NAME}")
            if lib_file.exists() or lib_file.is_symlink():
                try:
                    lib_file.unlink()
                except OSError:
                    pass
            try:
                Path(state_ctx['libdir']).rmdir()
            except OSError:
                pass
            state_ctx['lib_made'] = False

    def verify_staged_candidate() -> bool:
        if not (state_ctx['cand_md5'] and state_ctx['cand_size'] and state_ctx['lib_sha'] and state_ctx['lib_md5'] and state_ctx['lib_size']):
            return False
        top_run = env.top_mount(RUNROOT)
        if not top_run.startswith('tmpfs ') or 'noexec' in top_run[len('tmpfs '):]:
            return False
        top_tmp = env.top_mount(TMPROOT)
        if not top_tmp.startswith('tmpfs ') or 'noexec' in top_tmp[len('tmpfs '):]:
            return False
        for d in (Path(RUNDIR), Path(LIBROOT), Path(state_ctx['libdir']), Path(BRIDGEDIR)):
            if not _private_dir_ok(d):
                return False
        sdv_p = Path(f'{RUNDIR}/sdvcam')
        if not _file_mode_ok(sdv_p, 0o500) or not os.access(sdv_p, os.X_OK):
            return False
        if env.fsize(str(sdv_p)) != state_ctx['cand_size'] or env.md5of(str(sdv_p)) != state_ctx['cand_md5']:
            return False
        lib_p = Path(f"{state_ctx['libdir']}/{R.LIBRARY_NAME}")
        if not _file_mode_ok(lib_p, 0o444):
            return False
        if env.fsize(str(lib_p)) != state_ctx['lib_size'] or env.md5of(str(lib_p)) != state_ctx['lib_md5']:
            return False
        return True

    def finish_prepare(decision: str, reason: str, mode: str, boot: str, run_ok: bool, attempt: str = 'none') -> tuple[str, str, str, int, str, bool, str]:
        if not detach_tf():
            clean_staged_ram()
            decision = 'recovery-required'
            reason = 'tf-detach-failed'
        if decision != 'candidate':
            clean_staged_ram()
        if decision == 'stock' and not _verify_stock_app(env, APP, stock_md5, stock_size):
            decision = 'recovery-required'
            reason = 'stock-source-mismatch'
        backup = 0
        if decision == 'stock' and run_ok:
            if env.tf_mount_ok(EXTSD) and not _private_staging_residue(env, RUNDIR):
                backup = 1
        if run_ok and not (status_path.exists() or status_path.is_symlink()):
            status_text = (
                f'format=cammy-boot-launch-status/1\n'
                f'select_gate={select_gate}\n'
                f'mode={mode}\n'
                f'decision={decision}\n'
                f'reason={reason}\n'
                f'attempt={attempt}\n'
                f'boot={boot}\n'
                f'backup_eligible={backup}\n'
                f't_start={t0}\n'
                f't_end={t0}\n'
            )
            status_path.write_bytes(status_text.encode('latin-1'))
        return decision, reason, mode, backup, boot, run_ok, attempt

    def prepare_one_shot(mode: str, boot: str) -> tuple[str, str, str, int, str, bool, str]:
        firstboot_p = Path(f'{OVERLAY}/.firstboot')
        if firstboot_p.exists() or firstboot_p.is_symlink():
            return finish_prepare('stock', 'firstboot-active', mode, boot, True)
        if _private_staging_residue(env, RUNDIR):
            return finish_prepare('stock', 'staging-residue', mode, boot, True)
        if env.mounted_at(APP):
            return finish_prepare('stock', 'already-bound', mode, boot, True)

        state_p = Path(STATE)
        if state_p.is_symlink() or (state_p.exists() and not state_p.is_dir()):
            return finish_prepare('stock', 'state-invalid', mode, boot, True)
        if not state_p.is_dir():
            try:
                state_p.mkdir(mode=0o700)
            except OSError:
                return finish_prepare('stock', 'state-unavailable', mode, boot, True)
        if not _private_dir_ok(state_p):
            return finish_prepare('stock', 'state-not-private', mode, boot, True)

        has_inflight = False
        claim_ids: list[str] = []
        for sf in sorted(state_p.iterdir(), key=lambda x: x.name):
            if not sf.is_file() or sf.is_symlink():
                return finish_prepare('stock', 'state-invalid', mode, boot, True)
            raw = sf.read_bytes()
            sb = sf.name
            if sb == 'inflight':
                has_inflight = True
                if not R.record_ok(raw, INFLIGHT_KEYS) or R.get(raw, 'format') != INFLIGHT_FORMAT:
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
                if not R.is_uuid(R.get(raw, 'boot')) or R.get(raw, 'mode') not in ('observe', 'select'):
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
            elif sb.startswith('claimed-'):
                cid = sb[len('claimed-'):]
                if not R.is_hex32(cid) or not R.record_ok(raw, R.CLAIM_KEYS):
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
                if R.get(raw, 'format') != R.CLAIM_FORMAT or R.get(raw, 'attempt') != cid:
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
                if not (R.is_uuid(R.get(raw, 'boot')) and R.is_hex32(R.get(raw, 'md5'))):
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
                claim_ids.append(cid)
            elif sb.startswith('observed-'):
                oid = sb[len('observed-'):]
                if not R.is_hex32(oid) or not R.record_ok(raw, R.OBSERVED_KEYS):
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
                if R.get(raw, 'format') != R.OBSERVED_FORMAT or R.get(raw, 'attempt') != oid:
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
                if not R.is_uuid(R.get(raw, 'boot')):
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
                claim_peer = state_p / f'claimed-{oid}'
                if not claim_peer.is_file() or claim_peer.is_symlink():
                    return finish_prepare('stock', 'state-invalid', mode, boot, True)
            else:
                return finish_prepare('stock', 'state-invalid', mode, boot, True)

        for cid in claim_ids:
            raw = (state_p / f'claimed-{cid}').read_bytes()
            if R.get(raw, 'boot') == boot:
                return finish_prepare('stock', 'already-claimed-this-boot', mode, boot, True, cid)

        new_obs = False
        last_obs_id = 'none'
        for cid in claim_ids:
            obs_p = state_p / f'observed-{cid}'
            if obs_p.exists() or obs_p.is_symlink():
                if R.get(obs_p.read_bytes(), 'boot') == boot:
                    new_obs = True
                    last_obs_id = cid
                continue
            obs_content = f'format={R.OBSERVED_FORMAT}\nattempt={cid}\nboot={boot}'
            if not env.write_record(str(obs_p), obs_content):
                return finish_prepare('stock', 'observe-write-failed', mode, boot, True, cid)
            new_obs = True
            last_obs_id = cid
        if new_obs:
            return finish_prepare('stock', 'post-attempt-boot', mode, boot, True, last_obs_id)

        if has_inflight:
            inf_raw = (state_p / 'inflight').read_bytes()
            if R.get(inf_raw, 'boot') == boot:
                return finish_prepare('stock', 'inflight-same-boot', mode, boot, True)
            return finish_prepare('stock', 'prior-inflight', mode, boot, True)

        if len(claim_ids) >= R.MAX_ATTEMPTS:
            return finish_prepare('stock', 'attempt-limit', mode, boot, True)

        al_p = Path(f'{CFG}/native-select.allow')
        al_raw = al_p.read_bytes() if (al_p.is_file() and not al_p.is_symlink()) else None
        if not R.record_ok(al_raw, R.ALLOW_KEYS) or R.get(al_raw, 'format') != R.ALLOW_FORMAT:
            return finish_prepare('stock', 'allowlist-invalid', mode, boot, True)
        s01_md5 = R.get(al_raw, 's01app_md5')
        src_md5 = R.get(al_raw, 'source_md5')
        src_size = R.get(al_raw, 'source_size')
        cand_parts = R.get(al_raw, 'candidate').split(':', 2)
        lib_parts = R.get(al_raw, 'library').split(':', 2)
        if len(cand_parts) != 3 or len(lib_parts) != 3:
            return finish_prepare('stock', 'allowlist-invalid', mode, boot, True)
        a_md5, a_size, a_class = cand_parts
        al_sha, al_md5, al_size = lib_parts
        if not (R.is_hex32(s01_md5) and R.is_hex32(src_md5) and R.is_size(src_size)):
            return finish_prepare('stock', 'allowlist-invalid', mode, boot, True)
        if not (R.is_hex32(a_md5) and R.is_size(a_size) and a_md5 != src_md5):
            return finish_prepare('stock', 'allowlist-invalid', mode, boot, True)
        if not (R.is_hex64(al_sha) and R.is_hex32(al_md5) and R.is_size(al_size)):
            return finish_prepare('stock', 'allowlist-invalid', mode, boot, True)
        if a_class not in R.KNOWN_ALLOW_CLASSES:
            return finish_prepare('stock', 'allowlist-invalid', mode, boot, True)
        if src_md5 != stock_md5 or src_size != str(stock_size):
            return finish_prepare('stock', 'source-mismatch', mode, boot, True)
        if env.md5of(p('/etc/init.d/S01app')) != s01_md5:
            return finish_prepare('stock', 's01app-mismatch', mode, boot, True)

        top_tmp = env.top_mount(TMPROOT)
        if top_tmp.startswith('tmpfs ') and 'noexec' in top_tmp[len('tmpfs '):]:
            return finish_prepare('stock', 'lib-dir-noexec', mode, boot, True)
        if not top_tmp.startswith('tmpfs '):
            return finish_prepare('stock', 'lib-dir-not-tmpfs', mode, boot, True)

        state_ctx['libdir'] = f'{LIBROOT}/{al_sha}'
        libroot_p = Path(LIBROOT)
        if libroot_p.exists() or libroot_p.is_symlink():
            if not _private_dir_ok(libroot_p):
                return finish_prepare('stock', 'lib-dir-exists', mode, boot, True)
        libdir_p = Path(state_ctx['libdir'])
        if libdir_p.exists() or libdir_p.is_symlink():
            return finish_prepare('stock', 'lib-dir-exists', mode, boot, True)
        bridgedir_p = Path(BRIDGEDIR)
        if bridgedir_p.exists() or bridgedir_p.is_symlink():
            if not _private_dir_ok(bridgedir_p):
                return finish_prepare('stock', 'bridge-dir-invalid', mode, boot, True)
        if env.vfat_mounted():
            return finish_prepare('stock', 'vfat-premounted', mode, boot, True)

        inf_content = f'format={INFLIGHT_FORMAT}\nboot={boot}\nmode={mode}'
        if not env.write_record(f'{STATE}/inflight', inf_content):
            return finish_prepare('stock', 'inflight-write-failed', mode, boot, True)

        tf_dir = Path(f'{RUNDIR}/tf')
        try:
            tf_dir.mkdir(mode=0o700)
            state_ctx['tf_owned'] = True
        except OSError:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True)
            return finish_prepare('stock', 'tf-unavailable', mode, boot, True)
        if not _private_dir_ok(tf_dir):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True)
            return finish_prepare('stock', 'tf-unavailable', mode, boot, True)

        for tfdev in (f'{DEVDIR}/mmcblk0p1', f'{DEVDIR}/mmcblk0'):
            if not Path(tfdev).is_file():
                continue
            if env.tree.mount_op(['-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec', tfdev, f'{RUNDIR}/tf']) == 0:
                state_ctx['tfmnt'] = f'{RUNDIR}/tf'
                break
            if env.mounted_at(f'{RUNDIR}/tf'):
                state_ctx['tfmnt'] = f'{RUNDIR}/tf'
                if not env.remove_inflight(STATE):
                    return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True)
                return finish_prepare('stock', 'tf-unavailable', mode, boot, True)
        if not state_ctx['tfmnt']:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True)
            return finish_prepare('stock', 'tf-unavailable', mode, boot, True)

        tf_native = Path(f"{state_ctx['tfmnt']}/CammyOS-native")
        arm_p = tf_native / 'arm'
        if not arm_p.exists() and not arm_p.is_symlink():
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True)
            return finish_prepare('stock', 'arm-missing', mode, boot, True)
        arm_raw = arm_p.read_bytes() if (arm_p.is_file() and not arm_p.is_symlink()) else None
        if not R.record_ok(arm_raw, R.ARM_KEYS):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True)
            return finish_prepare('stock', 'arm-invalid', mode, boot, True)
        att = R.get(arm_raw, 'attempt')
        c_md5 = R.get(arm_raw, 'md5')
        c_size = R.get(arm_raw, 'size')
        c_class = R.get(arm_raw, 'class')
        l_sha = R.get(arm_raw, 'lib_sha256')
        l_md5 = R.get(arm_raw, 'lib_md5')
        l_size = R.get(arm_raw, 'lib_size')
        if R.get(arm_raw, 'format') != R.ARM_FORMAT or not R.is_hex32(att):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True)
            return finish_prepare('stock', 'arm-invalid', mode, boot, True)
        if R.get(arm_raw, 'candidate') != R.CANDIDATE_NAME or not (R.is_hex32(c_md5) and R.is_size(c_size)):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'arm-invalid', mode, boot, True, att)
        if not (R.is_hex64(l_sha) and R.is_hex32(l_md5) and R.is_size(l_size)):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'arm-invalid', mode, boot, True, att)
        if (state_p / f'claimed-{att}').exists() or (state_p / f'claimed-{att}').is_symlink():
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'attempt-replayed', mode, boot, True, att)

        for cid in claim_ids:
            ack_p = tf_native / f'ack-{cid}'
            obs_p = state_p / f'observed-{cid}'
            ack_raw = ack_p.read_bytes() if (ack_p.is_file() and not ack_p.is_symlink()) else None
            obs_raw = obs_p.read_bytes() if (obs_p.is_file() and not obs_p.is_symlink()) else None
            if not R.record_ok(ack_raw, R.ACK_KEYS) or not R.record_ok(obs_raw, R.OBSERVED_KEYS):
                if not env.remove_inflight(STATE):
                    return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
                return finish_prepare('stock', 'unacknowledged-attempt', mode, boot, True, att)
            ob = R.get(obs_raw, 'boot')
            if R.get(ack_raw, 'format') != R.ACK_FORMAT or R.get(ack_raw, 'attempt') != cid:
                if not env.remove_inflight(STATE):
                    return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
                return finish_prepare('stock', 'unacknowledged-attempt', mode, boot, True, att)
            if R.get(obs_raw, 'format') != R.OBSERVED_FORMAT or R.get(obs_raw, 'attempt') != cid:
                if not env.remove_inflight(STATE):
                    return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
                return finish_prepare('stock', 'unacknowledged-attempt', mode, boot, True, att)
            if not R.is_uuid(ob) or R.get(ack_raw, 'observed_boot') != ob:
                if not env.remove_inflight(STATE):
                    return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
                return finish_prepare('stock', 'unacknowledged-attempt', mode, boot, True, att)
            if R.get(ack_raw, 'outcome') not in R.ACK_OUTCOMES:
                if not env.remove_inflight(STATE):
                    return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
                return finish_prepare('stock', 'unacknowledged-attempt', mode, boot, True, att)

        if (c_md5, c_size, c_class) != (a_md5, a_size, a_class) or (l_sha, l_md5, l_size) != (al_sha, al_md5, al_size):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'candidate-not-allowlisted', mode, boot, True, att)
        if c_class not in R.SELECTABLE_CLASSES:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'class-ineligible', mode, boot, True, att)

        cand_p = tf_native / R.CANDIDATE_NAME
        if not cand_p.is_file() or cand_p.is_symlink() or env.fsize(str(cand_p)) != c_size or env.md5of(str(cand_p)) != c_md5:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'candidate-mismatch', mode, boot, True, att)

        lib_p = tf_native / R.LIBRARY_NAME
        if not lib_p.is_file() or lib_p.is_symlink() or env.fsize(str(lib_p)) != l_size or env.md5of(str(lib_p)) != l_md5:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'library-mismatch', mode, boot, True, att)

        mem_p = Path(f'{PROCFS}/meminfo')
        avail_vals = [
            line.split()[1]
            for line in (mem_p.read_text() if mem_p.is_file() else '').splitlines()
            if len(line.split()) > 1 and line.split()[0] == 'MemAvailable:'
        ]
        avail = '\n'.join(avail_vals)
        if not avail or not avail.isdigit() or not avail.isascii() or len(avail) > 9:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'memory-low', mode, boot, True, att)
        if int(avail) < (int(c_size) + int(l_size)) // 1024 * 3 + 1024:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'memory-low', mode, boot, True, att)

        state_ctx['stage_owned'] = True
        sdv_copy = Path(f'{RUNDIR}/sdvcam')
        sdv_copy.unlink(missing_ok=True)
        if env.tree.cp_op([str(cand_p), str(sdv_copy)]) != 0:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'copy-failed', mode, boot, True, att)
        if (
            env.tree.chmod_op(['0500', str(sdv_copy)]) != 0
            or not _file_mode_ok(sdv_copy, 0o500)
            or not os.access(sdv_copy, os.X_OK)
            or env.fsize(str(sdv_copy)) != c_size
            or env.md5of(str(sdv_copy)) != c_md5
        ):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'copy-mismatch', mode, boot, True, att)

        if not libroot_p.is_dir():
            try:
                libroot_p.mkdir(mode=0o700)
            except OSError:
                if not env.remove_inflight(STATE):
                    return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
                return finish_prepare('stock', 'library-copy-failed', mode, boot, True, att)
        if not _private_dir_ok(libroot_p):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'library-copy-failed', mode, boot, True, att)
        try:
            libdir_p.mkdir(mode=0o700)
        except OSError:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'library-copy-failed', mode, boot, True, att)
        state_ctx['lib_made'] = True
        if not _private_dir_ok(libdir_p):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'library-copy-failed', mode, boot, True, att)

        lib_copy = libdir_p / R.LIBRARY_NAME
        if env.tree.cp_op([str(lib_p), str(lib_copy)]) != 0:
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'library-copy-failed', mode, boot, True, att)
        if (
            env.tree.chmod_op(['0444', str(lib_copy)]) != 0
            or not _file_mode_ok(lib_copy, 0o444)
            or env.fsize(str(lib_copy)) != l_size
            or env.md5of(str(lib_copy)) != l_md5
        ):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'library-copy-failed', mode, boot, True, att)

        if not bridgedir_p.is_dir():
            try:
                bridgedir_p.mkdir(mode=0o700)
            except OSError:
                if not env.remove_inflight(STATE):
                    return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
                return finish_prepare('stock', 'bridge-dir-invalid', mode, boot, True, att)
        if not _private_dir_ok(bridgedir_p):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'bridge-dir-invalid', mode, boot, True, att)

        if not detach_tf():
            return finish_prepare('recovery-required', 'tf-detach-failed', mode, boot, True, att)

        if mode != 'select':
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'observe-only', mode, boot, True, att)

        if not env.disable_core_zero():
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'core-limit-failed', mode, boot, True, att)

        claim_content = f'format={R.CLAIM_FORMAT}\nattempt={att}\nboot={boot}\nmd5={c_md5}'
        if not env.write_record(f'{STATE}/claimed-{att}', claim_content):
            if not env.remove_inflight(STATE):
                return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)
            return finish_prepare('stock', 'claim-failed', mode, boot, True, att)
        if not env.remove_inflight(STATE):
            return finish_prepare('stock', 'inflight-clear-failed', mode, boot, True, att)

        state_ctx['cand_md5'] = c_md5
        state_ctx['cand_size'] = c_size
        state_ctx['lib_sha'] = l_sha
        state_ctx['lib_md5'] = l_md5
        state_ctx['lib_size'] = l_size

        if not verify_staged_candidate():
            return finish_prepare('stock', 'candidate-stage-invalid', mode, boot, True, att)

        return finish_prepare('candidate', 'selected', mode, boot, True, att)

    # 1. Existing per-boot launch stamp check
    if stamp_path.exists() or stamp_path.is_symlink():
        prep = ('noop', 'launch-stamp-exists', 'none', 0, 'unknown', False, 'none')
    elif env.native_owner_running(PROCFS):
        prep = ('noop', 'app-already-running', 'none', 0, 'unknown', False, 'none')
    else:
        top_run = env.top_mount(RUNROOT)
        if top_run.startswith('tmpfs ') and 'noexec' in top_run[len('tmpfs '):]:
            prep = ('recovery-required', 'run-noexec', 'none', 0, 'unknown', False, 'none')
        elif not top_run.startswith('tmpfs '):
            prep = ('recovery-required', 'run-not-tmpfs', 'none', 0, 'unknown', False, 'none')
        else:
            rundir_p = Path(RUNDIR)
            if rundir_p.is_symlink() or (rundir_p.exists() and not rundir_p.is_dir()):
                prep = ('recovery-required', 'rundir-invalid', 'none', 0, 'unknown', False, 'none')
            else:
                try:
                    rundir_p.mkdir(mode=0o700, exist_ok=True)
                    run_ok = True
                except OSError:
                    run_ok = False
                if not run_ok:
                    prep = ('recovery-required', 'rundir-unavailable', 'none', 0, 'unknown', False, 'none')
                elif not _private_dir_ok(rundir_p):
                    prep = ('recovery-required', 'rundir-not-private', 'none', 0, 'unknown', False, 'none')
                else:
                    cfg_p = Path(f'{CFG}/native-select.enabled')
                    if not cfg_p.exists() and not cfg_p.is_symlink():
                        mode, cfg_reason = 'absent', 'not-enabled'
                    elif not cfg_p.is_file() or cfg_p.is_symlink():
                        mode, cfg_reason = 'invalid', 'config-invalid'
                    else:
                        raw_mode = cfg_p.read_bytes().decode('latin-1').rstrip('\n')
                        if raw_mode == 'mode=disabled':
                            mode, cfg_reason = 'disabled', 'disabled'
                        elif raw_mode == 'mode=observe':
                            mode, cfg_reason = 'observe', 'select-gate-blocked'
                        elif raw_mode == 'mode=select':
                            mode, cfg_reason = 'select', 'select-gate-blocked'
                        else:
                            mode, cfg_reason = 'invalid', 'config-invalid'

                    if not env.tools_ok():
                        prep = finish_prepare('recovery-required', 'tools-unverified', mode, 'unknown', True)
                    else:
                        boot_p = Path(f'{PROCFS}/sys/kernel/random/boot_id')
                        boot = boot_p.read_bytes().decode('latin-1').rstrip('\n') if boot_p.is_file() else ''
                        if not _is_uuid(boot):
                            prep = finish_prepare('recovery-required', 'boot-id-invalid', mode, 'unknown', True)
                        elif select_gate not in ('blocked', 'chunk5-fixture-open'):
                            prep = finish_prepare('recovery-required', 'select-gate-tampered', mode, boot, True)
                        elif not _verify_stock_app(env, APP, stock_md5, stock_size):
                            prep = finish_prepare('recovery-required', 'stock-source-mismatch', mode, boot, True)
                        elif select_gate == 'chunk5-fixture-open' and mode in ('observe', 'select'):
                            prep = prepare_one_shot(mode, boot)
                        elif _private_staging_residue(env, RUNDIR):
                            prep = finish_prepare('stock', f'{cfg_reason}-staging-residue', mode, boot, True)
                        else:
                            prep = finish_prepare('stock', cfg_reason, mode, boot, True)

    p_dec, p_reason, p_mode, p_backup, p_boot, p_run_ok, p_att = prep
    if not call_launch:
        return HelperDecision(
            prepare_decision=p_dec,
            prepare_reason=p_reason,
            mode=p_mode,
            backup_eligible=p_backup,
            launch_attempted=False,
            launch_started=False,
            launch_decision=p_dec,
            launch_reason=p_reason,
            attempt=p_att,
        )

    return _invoke_launch_after_prepare(
        env,
        HelperDecision(p_dec, p_reason, p_mode, p_backup, False, False, p_dec, p_reason, p_att),
        stock_md5,
        stock_size,
        state_ctx=state_ctx,
    )


def run_s01app_branch_model(
    env: BootLaunchEnv,
    *,
    select_gate: str = 'blocked',
) -> S01AppBranchOutcome:
    """Model the transformed S01app backup/firstboot/launch block (stock lines 78-119)."""
    stock_md5, stock_size = env.tree.synthetic_stock_identity()
    meta = json.loads((env.tree.ctl / 'synthetic-identities.json').read_text())
    du_kb = int(meta['du_app_kb'])
    df_kb = int(meta['df_free_kb'])

    p = env.p
    APP = p('/usr/bin/sdvcam')
    EXTSD = p('/mnt/extsd')
    FIRSTBOOT = Path(p('/overlay/.firstboot'))
    CARD_SDVCAM = Path(f'{EXTSD}/.sdvcam')
    DATA_MD5 = Path(p('/data/app_md5'))

    prep = run_helper_model(
        env,
        select_gate=select_gate,
        stock_md5=stock_md5,
        stock_size=stock_size,
        call_launch=False,
    )

    backup_written = False
    backup_removed = False
    firstboot_cleared = False
    isp_cleared = False
    branch = 'no-eligible-backup-launch-only'
    site_line = 118

    if prep.backup_eligible == 1:
        if FIRSTBOOT.exists():
            branch = 'firstboot-backup'
            site_line = 91
            if du_kb <= df_kb:
                DATA_MD5.write_text(_md5(Path(APP).read_bytes()) + '\n')
                if 'cp-fail' in env.faults():
                    if CARD_SDVCAM.exists():
                        CARD_SDVCAM.unlink()
                else:
                    shutil.copyfile(APP, CARD_SDVCAM)
                    backup_written = True
            launched = _invoke_launch_after_prepare(env, prep, stock_md5, stock_size)
            if FIRSTBOOT.exists():
                FIRSTBOOT.unlink()
                firstboot_cleared = True
            for isp in Path(EXTSD).glob('isp*.bin'):
                isp.unlink()
                isp_cleared = True
        else:
            if CARD_SDVCAM.exists():
                card_md5 = _md5(CARD_SDVCAM.read_bytes())
                recorded_md5 = DATA_MD5.read_text().strip() if DATA_MD5.is_file() else ''
                if card_md5 != recorded_md5:
                    branch = 'existing-backup-md5-mismatch'
                    site_line = 99
                    CARD_SDVCAM.unlink()
                    backup_removed = True
                    launched = _invoke_launch_after_prepare(env, prep, stock_md5, stock_size)
                else:
                    branch = 'existing-backup-md5-match'
                    site_line = 101
                    launched = _invoke_launch_after_prepare(env, prep, stock_md5, stock_size)
            else:
                branch = 'normal-backup-populate'
                site_line = 114
                if du_kb <= df_kb:
                    if 'cp-fail' not in env.faults():
                        shutil.copyfile(APP, CARD_SDVCAM)
                        backup_written = True
                    DATA_MD5.write_text(_md5(Path(APP).read_bytes()) + '\n')
                launched = _invoke_launch_after_prepare(env, prep, stock_md5, stock_size)
    else:
        branch = 'no-eligible-backup-launch-only'
        site_line = 118
        launched = _invoke_launch_after_prepare(env, prep, stock_md5, stock_size)

    return S01AppBranchOutcome(
        branch=branch,
        launch_site_stock_line=site_line,
        helper=launched,
        backup_written=backup_written,
        backup_removed=backup_removed,
        firstboot_cleared=firstboot_cleared,
        isp_cleared=isp_cleared,
        app_started_count=env.tree.launched_Count(),
    )


def _invoke_launch_after_prepare(
    env: BootLaunchEnv,
    prep: HelperDecision,
    stock_md5: str,
    stock_size: int,
    *,
    state_ctx: dict | None = None,
) -> HelperDecision:
    p = env.p
    APP = p('/usr/bin/sdvcam')
    RUNROOT = p('/run')
    RUNDIR = p('/run/cammy-select')
    PROCFS = p('/proc')
    TMPROOT = p('/tmp')
    LIBROOT = p('/tmp/cammy-native-status')
    BRIDGEDIR = p('/tmp/cammy-bridge')
    stamp_path = Path(f'{RUNDIR}/launch.stamp')
    status_path = Path(f'{RUNDIR}/status')
    boot = 'unknown'
    if status_path.is_file():
        for line in status_path.read_text().splitlines():
            if line.startswith('boot='):
                boot = line.partition('=')[2]

    if prep.prepare_decision not in ('stock', 'candidate'):
        return HelperDecision(
            prep.prepare_decision,
            prep.prepare_reason,
            prep.mode,
            prep.backup_eligible,
            True,
            False,
            prep.prepare_decision,
            prep.prepare_reason,
            prep.attempt,
        )
    rundir_p = Path(RUNDIR)
    if not rundir_p.is_dir():
        return HelperDecision(
            prep.prepare_decision,
            prep.prepare_reason,
            prep.mode,
            prep.backup_eligible,
            True,
            False,
            'recovery-required',
            'rundir-unavailable',
            prep.attempt,
        )
    if not _private_dir_ok(rundir_p):
        return HelperDecision(
            'recovery-required',
            'rundir-not-private',
            prep.mode,
            prep.backup_eligible,
            True,
            False,
            'recovery-required',
            'rundir-not-private',
            prep.attempt,
        )
    if stamp_path.exists() or stamp_path.is_symlink():
        return HelperDecision(
            prep.prepare_decision,
            prep.prepare_reason,
            prep.mode,
            prep.backup_eligible,
            True,
            False,
            'noop',
            'launch-stamp-exists',
            prep.attempt,
        )
    if env.native_owner_running(PROCFS):
        return HelperDecision(
            prep.prepare_decision,
            prep.prepare_reason,
            prep.mode,
            prep.backup_eligible,
            True,
            False,
            'noop',
            'app-already-running',
            prep.attempt,
        )

    launch_target = prep.prepare_decision
    launch_reason = prep.prepare_reason

    def clean_ram_on_launch_fault() -> None:
        sdv = Path(f'{RUNDIR}/sdvcam')
        sdv.unlink(missing_ok=True)
        for lib_file in Path(LIBROOT).rglob(R.LIBRARY_NAME) if Path(LIBROOT).is_dir() else ():
            lib_file.unlink(missing_ok=True)
            try:
                lib_file.parent.rmdir()
            except OSError:
                pass

    if launch_target == 'candidate':
        meta = json.loads((env.tree.ctl / 'synthetic-identities.json').read_text())
        c_md5 = (state_ctx or {}).get('cand_md5') or meta.get('synthetic_candidate_md5', '')
        c_size = (state_ctx or {}).get('cand_size') or str(meta.get('synthetic_candidate_size', ''))
        l_sha = (state_ctx or {}).get('lib_sha') or meta.get('synthetic_library_sha256', '')
        l_md5 = (state_ctx or {}).get('lib_md5') or meta.get('synthetic_library_md5', '')
        l_size = (state_ctx or {}).get('lib_size') or str(meta.get('synthetic_library_size', ''))
        libdir_p = Path(f'{LIBROOT}/{l_sha}')
        sdv_p = Path(f'{RUNDIR}/sdvcam')
        lib_p = libdir_p / R.LIBRARY_NAME

        top_run = env.top_mount(RUNROOT)
        top_tmp = env.top_mount(TMPROOT)
        stage_ok = (
            top_run.startswith('tmpfs ')
            and 'noexec' not in top_run[len('tmpfs '):]
            and top_tmp.startswith('tmpfs ')
            and 'noexec' not in top_tmp[len('tmpfs '):]
            and _private_dir_ok(Path(RUNDIR))
            and _private_dir_ok(Path(LIBROOT))
            and _private_dir_ok(libdir_p)
            and _private_dir_ok(Path(BRIDGEDIR))
            and _file_mode_ok(sdv_p, 0o500)
            and os.access(sdv_p, os.X_OK)
            and env.fsize(str(sdv_p)) == str(c_size)
            and env.md5of(str(sdv_p)) == c_md5
            and _file_mode_ok(lib_p, 0o444)
            and env.fsize(str(lib_p)) == str(l_size)
            and env.md5of(str(lib_p)) == l_md5
        )
        if not stage_ok:
            clean_ram_on_launch_fault()
            if _verify_stock_app(env, APP, stock_md5, stock_size):
                launch_target = 'stock'
                launch_reason = 'candidate-stage-invalid'
            else:
                return HelperDecision(
                    'recovery-required',
                    'stock-source-mismatch',
                    prep.mode,
                    prep.backup_eligible,
                    True,
                    False,
                    'recovery-required',
                    'stock-source-mismatch',
                    prep.attempt,
                )
        elif not env.disable_core_zero():
            clean_ram_on_launch_fault()
            if _verify_stock_app(env, APP, stock_md5, stock_size):
                launch_target = 'stock'
                launch_reason = 'core-limit-failed'
            else:
                return HelperDecision(
                    'recovery-required',
                    'stock-source-mismatch',
                    prep.mode,
                    prep.backup_eligible,
                    True,
                    False,
                    'recovery-required',
                    'stock-source-mismatch',
                    prep.attempt,
                )
        else:
            stamp_body = (
                f'format=cammy-boot-launch-stamp/1\n'
                f'boot={boot}\n'
                f'target=candidate\n'
                f'attempt={prep.attempt}\n'
                f'app={RUNDIR}/sdvcam\n'
                f'md5={c_md5}\n'
                f'size={c_size}\n'
                f'lib_sha256={l_sha}\n'
                f'reason={launch_reason}'
            )
            if not env.write_exclusive(str(stamp_path), stamp_body):
                clean_ram_on_launch_fault()
                return HelperDecision(
                    'recovery-required',
                    'launch-stamp-failed',
                    prep.mode,
                    prep.backup_eligible,
                    True,
                    False,
                    'recovery-required',
                    'launch-stamp-failed',
                    prep.attempt,
                )
            subprocess.run(
                [DASH, '-c', f'ulimit -c 0 && exec "{RUNDIR}/sdvcam"'],
                check=False,
                cwd=env.tree.root,
            )
            return HelperDecision(
                prep.prepare_decision,
                prep.prepare_reason,
                prep.mode,
                prep.backup_eligible,
                True,
                True,
                'candidate',
                launch_reason,
                prep.attempt,
            )

    if not _verify_stock_app(env, APP, stock_md5, stock_size):
        return HelperDecision(
            'recovery-required',
            'stock-source-mismatch',
            prep.mode,
            prep.backup_eligible,
            True,
            False,
            'recovery-required',
            'stock-source-mismatch',
            prep.attempt,
        )

    stamp_body = (
        f'format=cammy-boot-launch-stamp/1\n'
        f'boot={boot}\n'
        f'target=stock\n'
        f'attempt={prep.attempt}\n'
        f'app={APP}\n'
        f'md5={stock_md5}\n'
        f'size={stock_size}\n'
        f'reason={launch_reason}'
    )
    if not env.write_exclusive(str(stamp_path), stamp_body):
        return HelperDecision(
            'recovery-required',
            'launch-stamp-failed',
            prep.mode,
            prep.backup_eligible,
            True,
            False,
            'recovery-required',
            'launch-stamp-failed',
            prep.attempt,
        )

    subprocess.run([APP], check=True, cwd=env.tree.root)
    return HelperDecision(
        prep.prepare_decision,
        prep.prepare_reason,
        prep.mode,
        prep.backup_eligible,
        True,
        True,
        'stock',
        launch_reason,
        prep.attempt,
    )


# ---- Test-only relocation of the helper & transformed S01app branch block ----

def relocate_helper_for_test(
    helper_text: str,
    tree: BootLaunchFakeTree,
    *,
    select_gate: str = 'blocked',
) -> str:
    """Rewrite only the marked relocatable-paths, stock-identity and gate blocks for a synthetic fixture."""
    stock_md5, stock_size = tree.synthetic_stock_identity()
    out: list[str] = []
    in_paths = False
    in_ident = False
    in_gate = False
    for line in helper_text.splitlines(keepends=True):
        if line.startswith('# --- relocatable paths'):
            in_paths = True
        elif line.startswith('# --- end relocatable paths'):
            in_paths = False
        elif line.startswith('# --- stock runtime identity'):
            in_ident = True
        elif line.startswith('# --- end stock runtime identity'):
            in_ident = False
        elif line.startswith('# --- review gate'):
            in_gate = True
        elif line.startswith('# --- end review gate'):
            in_gate = False
        elif in_paths:
            name, _, val = line.rstrip('\n').partition('=')
            assert name in RELOCATABLE_PATHS, line
            if name == 'BLOCKTEST':
                line = 'BLOCKTEST=-f\n'
            else:
                assert val.startswith('/'), line
                line = f'{name}={tree.root}{val}\n'
        elif in_ident:
            name, _, _ = line.rstrip('\n').partition('=')
            if name == 'STOCK_SDVCAM_MD5':
                line = f'STOCK_SDVCAM_MD5={stock_md5}\n'
            elif name == 'STOCK_SDVCAM_SIZE':
                line = f'STOCK_SDVCAM_SIZE={stock_size}\n'
            elif name == 'REQUIRED_UID':
                line = f'REQUIRED_UID={os.getuid()}\n'
            elif name == 'REQUIRED_GID':
                line = f'REQUIRED_GID={os.getgid()}\n'
        elif in_gate:
            if line.startswith('SELECT_GATE='):
                line = f'SELECT_GATE={select_gate}\n'
        out.append(line)
    return ''.join(out)


def extract_and_relocate_s01app_branch_harness(
    proposal_text: str,
    tree: BootLaunchFakeTree,
    *,
    select_gate: str = 'blocked',
) -> str:
    """Extract the embedded helper + transformed S01app backup/launch block (stock lines 78-119)
    and relocate its paths to ``tree.root`` so it can execute safely under ``dash`` without
    running stock S01app's MMIO/power/sysctl lines.
    """
    lines = proposal_text.splitlines(keepends=True)
    begin_idx = next(i for i, l in enumerate(lines) if l.startswith('# --- begin cammy boot-launch helper'))
    end_idx = next(i for i, l in enumerate(lines) if l.startswith('# --- end cammy boot-launch helper'))
    helper_block = ''.join(lines[begin_idx : end_idx + 1])
    relocated_helper = relocate_helper_for_test(helper_block, tree, select_gate=select_gate)

    fault_overrides = ''
    faults = tree.faults()
    if 'core-limit-fail' in faults:
        fault_overrides += 'cammy_disable_core_zero() { return 1; }\n'
    if 'stamp-write-fail' in faults:
        fault_overrides += 'cammy_write_exclusive() { return 1; }\n'
    if 'inflight-write-fail' in faults:
        fault_overrides += 'cammy_write_record() { case "$1" in */inflight) return 1 ;; esac; (set -C && printf "%s\\n" "$2" >"$1") 2>/dev/null && sync && [ "$(cat "$1" 2>/dev/null)" = "$2" ]; }\n'
    if 'claim-write-fail' in faults:
        fault_overrides += 'cammy_write_record() { case "$1" in */claimed-*) return 1 ;; esac; (set -C && printf "%s\\n" "$2" >"$1") 2>/dev/null && sync && [ "$(cat "$1" 2>/dev/null)" = "$2" ]; }\n'
    if 'observe-write-fail' in faults:
        fault_overrides += 'cammy_write_record() { case "$1" in */observed-*) return 1 ;; esac; (set -C && printf "%s\\n" "$2" >"$1") 2>/dev/null && sync && [ "$(cat "$1" 2>/dev/null)" = "$2" ]; }\n'
    if 'inflight-clear-fail' in faults:
        fault_overrides += 'cammy_remove_inflight() { sync; return 1; }\n'

    prep_idx = next(i for i, l in enumerate(lines) if l == '    cammy_prepare_launch\n')
    webapp_idx = next(i for i, l in enumerate(lines) if '# /usr/bin/webapp &' in l)
    branch_block = ''.join(lines[prep_idx:webapp_idx])

    for cam_path in ('/usr/bin/gettimems', '/usr/bin/sdvcam', '/overlay/.firstboot', '/mnt/extsd', '/data/app_md5'):
        branch_block = branch_block.replace(cam_path, f'{tree.root}{cam_path}')

    return (
        '#!/bin/sh\n'
        '# Synthetic test harness: embedded helper + transformed S01app lines 78-119 only.\n'
        '# Never executes MMIO/power/sysctl lines.\n'
        + relocated_helper
        + fault_overrides
        + '\nrun_s01_branch() {\n'
        + branch_block
        + '    wait\n'
        + '}\nrun_s01_branch\n'
    )


def run_relocated_s01app_branch_shell(
    proposal_text: str,
    tree: BootLaunchFakeTree,
    *,
    select_gate: str = 'blocked',
) -> subprocess.CompletedProcess:
    """Execute the relocated helper + S01app branch block under dash with confined PATH stubs."""
    assert DASH is not None, 'dash is required'
    script_path = tree.ctl / 's01_branch_harness.sh'
    script_path.write_text(
        extract_and_relocate_s01app_branch_harness(proposal_text, tree, select_gate=select_gate)
    )
    env = {'PATH': f'{tree.ctl / "bin"}:/usr/bin:/bin', 'LC_ALL': 'C'}
    return subprocess.run(
        [DASH, str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        cwd=tree.root,
    )


def stub_main(argv: list[str]) -> int:
    """Entry point for confined PATH command stubs during dash execution."""
    root_str, cmd, *args = argv
    tree = BootLaunchFakeTree(root_str)
    faults = tree.faults()
    meta = json.loads((tree.ctl / 'synthetic-identities.json').read_text())

    try:
        if cmd == 'gettimems':
            tree.log('gettimems ' + ' '.join(args))
            return 0

        if cmd == 'du':
            target = args[-1] if args else ''
            if not tree.inside(target):
                return tree.violation(f'du outside root: {target}')
            sys.stdout.write(f'{meta["du_app_kb"]}\t{target}\n')
            return 0

        if cmd == 'df':
            target = args[-1] if args else ''
            if not tree.inside(target):
                return tree.violation(f'df outside root: {target}')
            sys.stdout.write(
                'Filesystem           1K-blocks      Used Available Use% Mounted on\n'
                f'/dev/mmcblk0            128000     64000     {meta["df_free_kb"]}  50% {target}\n'
            )
            return 0

        if cmd == 'cp':
            return tree.cp_op(args)

        if cmd == 'mount':
            return tree.mount_op(args)

        if cmd == 'umount':
            return tree.umount_op(args)

        if cmd == 'sync':
            return tree.sync_op()

        if cmd == 'chmod':
            return tree.chmod_op(args)

        if cmd == 'rm':
            flags = [a for a in args if a.startswith('-')]
            targets = [a for a in args if not a.startswith('-')]
            force = any('f' in f for f in flags)
            rc = 0
            for t in targets:
                parent = str(Path(t).parent)
                if not (tree.inside(t) or tree.inside(parent)):
                    return tree.violation(f'rm outside root: {t}')
                try:
                    if Path(t).is_dir() and not Path(t).is_symlink() and any('r' in f for f in flags):
                        # Empty-directory cleanup only; never recursively delete
                        # fixture contents on behalf of a broken shell guard.
                        os.rmdir(t)
                    else:
                        os.unlink(t)
                except FileNotFoundError:
                    if not force:
                        rc = 1
                except OSError:
                    rc = 1
            return rc

        if cmd == 'md5sum':
            if 'md5-broken' in faults or 'tools-broken' in faults:
                sys.stdout.write('00000000000000000000000000000000  broken\n')
                return 0
            if not args or not tree.inside(args[0]):
                return tree.violation(f'md5sum outside root: {args}')
            p = Path(args[0])
            if not p.is_file():
                return 1
            sys.stdout.write(f'{_md5(p.read_bytes())}  {args[0]}\n')
            return 0

        if cmd == 'tail':
            if 'tail-broken' in faults or 'tools-broken' in faults:
                return 1
            os.execv('/usr/bin/tail', ['/usr/bin/tail', *args])

        if cmd == 'awk':
            if 'awk-broken' in faults:
                return 1
            os.execv('/usr/bin/awk', ['/usr/bin/awk', *args])
    except PowerLoss:
        os.kill(os.getppid(), signal.SIGKILL)
        return 137

    return 99
