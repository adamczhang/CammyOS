#!/usr/bin/env python3
"""Checked install transaction for a built CammyOS candidate (docs/RC06-INSTALL-REVIEW.md).

Phases:
  check     read-only preflight: camera identity, before-hashes, authority records and
            residue. Writes only a PC checkpoint; never writes to the camera.
  stage-tf  copy tf/CammyOS-releases/<id> to the card as <id>.partial, verify every file on
            the camera with the slot's own busybox, then rename into place
  install   one-shot replacement transaction (journal: private/rc06/install-<version>/
            transaction.json). Crash-consistent and resumable by running it again:
              0. archive obsolete NOR files on LAB, prune, then measure free space
              1. selector -> mode=disabled (stock) before changing live boot files
              2. retain only the factory recovery with --stock-only, otherwise copy original targets to NOR
              3. PC copies, then synced renames into native-select.archived-<txid>/ of:
                 state-dir claims, startup.pending (--clear-latch only) and the stale
                 authority records (resident, advance, retry opt-in, retry-used ledger)
              4. S01app, allow, then S99cammy-tf: staged beside the target, verified, renamed
              5. verify every target, the archive, the rollback copies and the full TF slot
              6. selector -> mode=select, the last write
            New resident/retry authority is never written here: the trial is one-shot until
            tools/rc_resident.py apply grants it explicitly after acceptance.
  rollback  restore the journal's original S99, allow and S01app from the PC copies with
            the selector left at mode=disabled (stock). Archived records stay archived.

  prune     report obsolete NOR archives. Use --apply and the current --replacing
            receipt to archive on LAB and remove them without installing.

Pruning touches only obsolete rollback and selector archives. Live records, credentials
and profiles are outside its write allowlist.
Never reboots, restarts sdvcam, flashes NOR or retries a boot. Adam power-cycles by hand.

Usage: tools/rc_install.py <candidate-dir> check|stage-tf|install|rollback [--diag]
       [--replacing <previous install-*/installed.json>] [--clear-latch]
       [--retire-authority <release_id named by the existing authority records>]
       tools/rc_install.py prune [--replacing <current installed.json>] [--apply]
--clear-latch moves /etc/cammy/startup.pending into the archive (PC copy first); use it only
with Adam's explicit approval for that trial. --retire-authority is the operator's explicit
acknowledgement that the named release loses its resident/advance/retry authority.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__:
    from . import rc_wifi_policy
    from .native_trial.policy import STOCK_S01APP_SHA256
else:
    import rc_wifi_policy
    from native_trial.policy import STOCK_S01APP_SHA256

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT.parent / 'Cammy Bot/tools/build-deps/usr'
ADB = DEPS / 'lib/android-sdk/platform-tools/adb'
ENV = {'LD_LIBRARY_PATH': '%s:%s' % (DEPS / 'lib/x86_64-linux-gnu/android', DEPS / 'lib/x86_64-linux-gnu'),
       'PATH': '/usr/bin:/bin'}
SERIAL = ['-P', '5038', '-s', '20080411']
CFG = '/etc/cammy'
STATE = CFG + '/native-select'
FAULT = CFG + '/runtime-health-fault'
OVERLAY_RESERVE = 32 * 1024
PENDING = CFG + '/startup.pending'
SELECTOR = CFG + '/native-select.enabled'
RUNTIME_CONF = '/tmp/cammy-runtime/native-release.conf'
DISABLED, SELECT = b'mode=disabled\n', b'mode=select\n'
SELECTOR_MODES = {b'mode=disabled', b'mode=observe', b'mode=select'}
# Program targets in write order: the S99 hook that starts the TF runtime goes last, after
# the authority its health check reads has been archived.
PROGRAM_ORDER = ('/etc/init.d/S01app', '/etc/cammy/native-select.allow', '/etc/init.d/S99cammy-tf')
HEX24, HEX32 = re.compile(r'[0-9a-f]{24}'), re.compile(r'[0-9a-f]{32}')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
# Authority records in /etc/cammy, in archive order: the retry opt-in and advance before the
# resident record they depend on, the retry ledger last. Exact keys, in order (health.sh and
# selector-retry.sh compare whole records).
AUTHORITY = (
    ('native-select.retry', 'cammy-power-retry/1', ('format', 'release_id', 'candidate_md5')),
    ('native-select.advance', 'cammy-advance/1', ('format', 'release_id', 'boot')),
    ('native-select.resident', 'cammy-resident/1', ('format', 'release_id')),
    ('native-select.crash-used', 'cammy-crash-retry-used/1',
     ('format', 'release_id', 'candidate_md5', 'failed_boot', 'retry_boot')),
    ('native-select.retry-used', 'cammy-power-retry-used/1', ('format', 'attempt', 'failed_boot', 'retry_boot')),
)
FIELDS = {'release_id': HEX24, 'candidate_md5': HEX32, 'attempt': HEX32,
          'boot': UUID, 'failed_boot': UUID, 'retry_boot': UUID}
JOURNAL_FORMAT = 'cammy-install-txn/1'
TXID = re.compile(r'[0-9]{8}-[0-9]{6}')
ARCHIVE_RECORD = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\.archived-[A-Za-z0-9][A-Za-z0-9._-]*')
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def adb(*args, timeout=120):
    out = subprocess.run([str(ADB), *SERIAL, *args], env=ENV, capture_output=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError('adb %s failed: %s' % (args[0], (out.stderr or out.stdout).decode()[-300:]))
    return ANSI.sub('', out.stdout.decode(errors='replace'))


def sh(cmd, timeout=120):
    # adb shell exit codes are unreliable on this camera: print a marker instead.
    out = adb('shell', cmd + '; echo "__rc=$?"', timeout=timeout)
    body, _, rc = out.rpartition('__rc=')
    if rc.strip() != '0':
        raise RuntimeError('camera command failed (%s): %s\n%s' % (rc.strip(), cmd[:160], body[-400:]))
    return body


def pull(path):
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / 'f'
        adb('pull', path, str(local))
        return local.read_bytes()


def remote_exists(path):
    return sh('[ -e %s ] && echo yes || echo no' % path).strip().endswith('yes')


def log(msg):
    print(msg, flush=True)


def abort(msg):
    raise SystemExit('ABORT: ' + msg)


def overlay_free_bytes(shell, candidate):
    """Run the exact reviewed read-only statfs probe from the inert TF slot."""
    member = rc_wifi_policy.TF_SPACE_PROBE
    local = candidate.slot_local / member
    if not local.is_file():
        abort('NOR space readback requires a candidate with the statfs probe')
    binary = local.read_bytes()
    if candidate.report['files'].get('tf/' + candidate.report['slot'] + '/' + member, {}).get('sha256') != sha(binary):
        abort('local NOR space probe does not match the candidate manifest')
    path = shlex.quote(candidate.slot_remote + '/' + member)
    digest = hashlib.md5(binary).hexdigest()  # stock has md5sum, but no sha256sum or stat
    value = shell('p=%s; [ -f "$p" ] && [ ! -L "$p" ] && '
                  '[ "$(md5sum "$p" | cut -c1-32)" = %s ] && "$p" space' % (path, digest),
                  timeout=10).strip()
    if not re.fullmatch(r'[0-9]{1,9}', value):
        abort('NOR space probe returned no usable free-space measurement')
    return int(value)


class AdbCamera:
    """The camera over ADB. Reads are side-effect free; every write is synced."""

    def boot_id(self):
        return sh('cat /proc/sys/kernel/random/boot_id').strip()

    def kinds(self, paths):
        q = ' '.join(shlex.quote(p) for p in paths)
        out = sh('for p in %s; do if [ -L "$p" ]; then k=link; elif [ -f "$p" ]; then k=file; '
                 'elif [ -d "$p" ]; then k=dir; elif [ -e "$p" ]; then k=other; else k=absent; fi; '
                 'echo "$k $p"; done' % q)
        got = dict(line.split(' ', 1)[::-1] for line in out.splitlines() if ' ' in line)
        return {p: got.get(p, 'unknown') for p in paths}

    def read(self, path):
        return pull(path)

    def listdir(self, path):
        return sh('ls -A1 %s' % shlex.quote(path)).splitlines()

    def archive_md5(self, path):
        return archive_md5(sh, path)

    def archive_remove(self, path, md5, boot):
        remove_archive_entry(sh, path, md5, boot)

    def hashes(self, base, names, busybox):
        out = sh('cd %s && %s sha256sum %s' % (shlex.quote(base), busybox, ' '.join(map(shlex.quote, names))),
                 timeout=300)
        return {line.split()[1]: line.split()[0] for line in out.splitlines() if len(line.split()) == 2}

    def overlay_free_bytes(self, candidate):
        return overlay_free_bytes(sh, candidate)

    def info(self):
        return {'mem_available_kb': int(sh('grep MemAvailable /proc/meminfo').split()[1]),
                'sdvcam_pid': sh('pidof sdvcam || true').strip()}

    def write(self, path, data, mode):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'f'
            f.write_bytes(data)
            adb('push', str(f), path)
        sh('chmod %s %s && sync' % (mode, shlex.quote(path)))

    def rename(self, src, dst):
        # Stock BusyBox mv has no -T: refuse a directory destination.
        sh('[ ! -d {1} ] && mv -f {0} {1} && sync'.format(shlex.quote(src), shlex.quote(dst)))

    def mkdir(self, path):
        sh('mkdir {0} && chmod 700 {0} && sync'.format(shlex.quote(path)))

    def remove(self, path):
        sh('[ ! -d {0} ] && rm -f {0} && sync'.format(shlex.quote(path)))


def archive_md5(shell, path):
    q = shlex.quote(path)
    parent = shlex.quote(str(Path(path).parent))
    digest = shell(f'[ ! -L {CFG} ] && [ ! -L {parent} ] && [ ! -L {q} ] && [ -f {q} ] && md5sum {q}').split()
    if len(digest) != 2 or not HEX32.fullmatch(digest[0]) or digest[1] != path:
        abort('Invalid camera MD5 readback for %s' % path)
    return digest[0]


def remove_archive_entry(shell, path, md5, boot):
    q = shlex.quote(path)
    parent = shlex.quote(str(Path(path).parent))
    guard = (f'[ "$(cat /proc/sys/kernel/random/boot_id)" = {shlex.quote(boot)} ] && '
             f'[ ! -L {CFG} ] && [ ! -L {parent} ] && [ ! -L {q} ]')
    if md5 is not None:
        shell(f'{guard} && [ -f {q} ] && '
              f'[ "$(md5sum {q} | cut -c1-32)" = {shlex.quote(md5)} ] && rm -f {q} && sync')
    else:
        # Stock has no rmdir. Refuse to recurse on a directory with any entries.
        shell(f'{guard} && [ -d {q} ] && (for p in {q}/* {q}/.[!.]* {q}/..?*; do '
              f'if [ -e "$p" ] || [ -L "$p" ]; then exit 1; fi; done) && rm -r {q} && sync')


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_write(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)


class MaintenanceLockError(RuntimeError):
    pass


@contextmanager
def maintenance_lock(folder):
    """Serialize the CLI and lifecycle tools against the same local journals."""
    path = folder / 'lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise MaintenanceLockError('The maintenance lock is invalid.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MaintenanceLockError('Another maintenance operation is already running.') from exc
        yield
    finally:
        os.close(fd)


def receipt_rollback(receipt):
    if not receipt:
        return None
    path = receipt.get('on_camera_rollback')
    txid = receipt.get('transaction')
    if path is None and txid is None:
        return None
    if not isinstance(txid, str) or not TXID.fullmatch(txid):
        abort('Invalid transaction ID in the installed receipt')
    if receipt.get('recovery') == 'factory-stock' and path is None:
        return None
    expected = CFG + '/rollback-' + txid
    if path is not None and path != expected:
        abort('Invalid rollback path in the installed receipt')
    return expected


def archive_kind(name):
    if not NAME.fullmatch(name) or name.partition('.archived-')[0].casefold() in rc_wifi_policy.PRIVATE_NAMES:
        return None
    if name.startswith(('rollback-', 'native-select.archived-')):
        return 'dir'
    if ARCHIVE_RECORD.fullmatch(name):
        return 'file'
    return None


def prune_inventory(cam, root, replacing=None, journal=None):
    """Read-only plan. Include every explicit path reference in unfinished journals."""
    protected = {}

    def keep(path, reason):
        if isinstance(path, str) and path.startswith(CFG + '/'):
            # A journal entry may name a file inside an archive directory.
            top_level = path[len(CFG) + 1:].split('/', 1)[0]
            protected.setdefault(CFG + '/' + top_level, reason)

    keep(receipt_rollback(replacing), 'installed receipt')
    # Rolling back the current install restores the records it archived.
    if isinstance(replacing, dict) and receipt_rollback(replacing) is not None:
        keep(replacing.get('archive_dir'), 'installed receipt')
    if journal:
        keep(journal['rollback_dir'], 'current transaction')
        keep(journal['archive_dir'], 'current transaction')
        keep(journal.get('replacing_rollback'), 'current transaction')
    legacy_before = []
    for path in sorted(root.rglob('transaction.json')):
        if path.relative_to(root).parts[0].startswith('flash-archive-'):
            continue
        saved = json.loads(path.read_text())
        if saved.get('format') != JOURNAL_FORMAT:
            abort('Unexpected install journal format at %s' % path)
        if saved.get('status') in ('selected', 'rolled-back'):
            continue
        if saved.get('previous_release') and 'replacing_rollback' not in saved:
            legacy_before.append((saved['previous_release'], saved['before']))
        for key in ('rollback_dir', 'archive_dir', 'replacing_rollback'):
            keep(saved.get(key), 'unfinished journal')
        for dest, entry in saved['archived'].items():
            keep(dest, 'unfinished journal')
            keep(entry.get('from'), 'unfinished journal')

    if legacy_before:
        for path in sorted(root.rglob('installed.json')):
            if path.relative_to(root).parts[0].startswith('flash-archive-'):
                continue
            receipt = json.loads(path.read_text())
            if any(receipt.get('release') == release and all(
                    receipt['after_sha256'].get(target) == before.get(target) for target in PROGRAM_ORDER)
                   for release, before in legacy_before):
                keep(receipt_rollback(receipt), 'prior receipt of unfinished journal')

    kind = cam.kinds([CFG])[CFG]
    if kind == 'absent':
        return {'protected': protected, 'obsolete': {}}
    if kind != 'dir':
        abort('Pruning requires a real %s directory' % CFG)
    obsolete = {}
    for name in sorted(cam.listdir(CFG)):
        expected = archive_kind(name)
        path = CFG + '/' + name
        if expected is None or path in protected:
            continue
        if cam.kinds([path])[path] != expected:
            abort('Expected an archive %s at %s' % (expected, path))
        if name.startswith('rollback-'):
            stock = path + '/S01app'
            stock_kind = cam.kinds([stock])[stock]
            if stock_kind not in ('file', 'absent'):
                abort('Unexpected S01app file type in %s' % path)
            if stock_kind == 'file' and sha(cam.read(stock)) == STOCK_S01APP_SHA256:
                keep(path, 'factory S01app')
                continue
        obsolete[path] = expected
    return {'protected': protected, 'obsolete': obsolete}


def archive_item(cam, root, path, kind, boot):
    """Publish a durable receipt for the complete item before unlinking any part."""
    if str(Path(path).parent) != CFG or archive_kind(Path(path).name) != kind:
        abort('Path is outside the pruning allowlist')
    if cam.kinds([path])[path] != kind:
        abort('Archive item changed during pruning: %s' % path)
    members = [path]
    if kind == 'dir':
        names = cam.listdir(path)
        if (len(set(names)) != len(names) or any(not NAME.fullmatch(n) or
                n.partition('.archived-')[0].casefold() in rc_wifi_policy.PRIVATE_NAMES for n in names)):
            abort('Unexpected archive directory contents at %s' % path)
        members = [path + '/' + name for name in sorted(names)]
    contents = {}
    receipt = {'format': 'cammy-flash-archive/1', 'path': path, 'kind': kind, 'files': {}}
    kinds = cam.kinds(members) if members else {}
    for member in members:
        member_kind = kinds[member]
        if member_kind != 'file':
            abort('Expected a regular archive member in %s' % path)
        data = cam.read(member)
        digest = cam.archive_md5(member)
        if hashlib.md5(data).hexdigest() != digest:
            abort('Camera MD5 did not match the PC copy of %s' % member)
        contents[member] = data
        receipt['files'][member] = {'md5': digest, 'sha256': sha(data), 'bytes': len(data)}

    if path.startswith(CFG + '/rollback-') and sha(contents.get(path + '/S01app', b'')) == STOCK_S01APP_SHA256:
        abort('Factory rollback appeared during pruning at %s' % path)
    record = (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode()
    item = root / Path(path).name / sha(record)
    files = item / 'files'
    for folder in (root, item.parent, item, files):
        folder.mkdir(mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(folder.lstat().st_mode) or folder.stat().st_mode & 0o077:
            abort('PC flash archive directory is not private')
        sync_dir(folder.parent)
    for member, data in contents.items():
        local = files / Path(member).name
        durable_write(local, data)
        if sha(local.read_bytes()) != receipt['files'][member]['sha256']:
            abort('PC flash archive copy did not verify')
    durable_write(item / 'receipt.json', record)

    for member, entry in receipt['files'].items():
        cam.archive_remove(member, entry['md5'], boot)
    if kind == 'dir':
        cam.archive_remove(path, None, boot)


def prune(cam, root, replacing=None, *, apply=False, txid=None, inventory=None, expected_boot=None, out=log):
    boot = cam.boot_id()
    if not UUID.fullmatch(boot):
        abort('Cannot identify the boot for NOR pruning')
    if expected_boot is not None and boot != expected_boot:
        abort('Camera rebooted before NOR pruning')
    if apply and inventory is None:
        if receipt_rollback(replacing) is None and not (replacing and replacing.get('recovery') == 'factory-stock'
                and TXID.fullmatch(replacing.get('transaction', ''))):
            abort('Maintenance pruning requires --replacing with the current installed receipt')
        for target in PROGRAM_ORDER:
            if (cam.kinds([target])[target] != 'file'
                    or sha(cam.read(target)) != replacing['after_sha256'].get(target)):
                abort('The current boot files do not match the installed receipt')
    plan = inventory if inventory is not None else prune_inventory(cam, root, replacing)
    for path, reason in sorted(plan['protected'].items()):
        out('  keep %s (%s)' % (path, reason))
    archive = root / ('flash-archive-' + (txid or time.strftime('%Y%m%d-%H%M%S')))
    for path, kind in plan['obsolete'].items():
        out('  %s %s' % ('archive and prune' if apply else 'would archive and prune', path))
        if apply:
            archive_item(cam, archive, path, kind, boot)
    return plan


class Candidate:
    def __init__(self, directory, work=None):
        self.dir = Path(directory).resolve()
        self.report = json.loads((self.dir / 'candidate.json').read_text())
        self.plan = json.loads((self.dir / 'installation-plan.json').read_text())
        self.release = self.report['release_id']
        slot = rc_wifi_policy.release_slot(self.release)
        rc_wifi_policy.validate_members(self.report['files'], slot)
        rc_wifi_policy.validate_install_plan(self.plan, self.release)
        rc_wifi_policy.validate_tf_tree(self.dir, slot)
        self.slot_local = self.dir / 'tf' / slot
        self.slot_remote = self.plan['tf_destination']
        self.busybox = self.slot_remote + '/runtime/busybox'
        self.work = Path(work) if work else ROOT / 'private/rc06' / ('install-%s' % self.report['version'])
        self.work.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.targets = {item['target']: item for item in self.plan['internal_files']}
        self.after = {}
        for target, item in self.targets.items():
            data = SELECT if target == SELECTOR else (self.dir / item['source']).read_bytes()
            if target != SELECTOR and item.get('after_sha256') and sha(data) != item['after_sha256']:
                raise SystemExit('ABORT: package file %s does not match the plan' % item['source'])
            self.after[target] = data
        if (self.dir / 'templates/native-select.enabled.select').read_bytes() != SELECT:
            raise SystemExit('ABORT: selector template is not mode=select')

    def files(self):
        return {str(p.relative_to(self.slot_local)): sha(p.read_bytes())
                for p in sorted(self.slot_local.rglob('*')) if p.is_file()}

    def remote_hashes(self, base, names):
        out = sh('cd %s && %s sha256sum %s' % (base, self.busybox, ' '.join(names)), timeout=300)
        return {line.split()[1]: line.split()[0] for line in out.splitlines() if len(line.split()) == 2}


def parse_record(name, data, fmt, keys):
    """Strict parse of one authority record; anything unexpected is unsafe state."""
    bad = 'malformed %s record; resolve it by hand before installing' % name
    if len(data) > 512:
        abort(bad)
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError:
        abort(bad)
    text = text[:-1] if text.endswith('\n') else text
    pairs = [line.partition('=') for line in text.split('\n')]
    if [k for k, _, _ in pairs] != list(keys) or any(not eq or not v for _, eq, v in pairs):
        abort(bad)
    record = {k: v for k, _, v in pairs}
    if record['format'] != fmt or any(k in FIELDS and not FIELDS[k].fullmatch(v) for k, v in record.items()):
        abort(bad)
    return record


class Transaction:
    def __init__(self, c, cam, replacing=None, clear_latch=False, retire_authority=None, out=log, *, archive_root=None, stock_only=False):
        self.c, self.cam, self.out = c, cam, out
        self.replacing = replacing
        self.archive_root = archive_root if archive_root is not None else c.work.parent
        self.clear_latch = clear_latch
        self.retire = retire_authority
        self.journal_path = c.work / 'transaction.json'
        self.journal = json.loads(self.journal_path.read_text()) if self.journal_path.exists() else None
        if self.journal and (self.journal.get('format') != JOURNAL_FORMAT or self.journal.get('release') != c.release):
            abort('%s belongs to another transaction' % self.journal_path)
        self.stock_only = stock_only or any(
            record and record.get('recovery') == 'factory-stock' for record in (self.journal, replacing))
        self.writing = False
        self.allowed = set()

    # ---- read-only survey -------------------------------------------------
    def survey(self):
        c, cam = self.c, self.cam
        authority = [CFG + '/' + name for name, _, _ in AUTHORITY]
        staged = [t + '.rc-new' for t in c.targets]
        kinds = cam.kinds([*c.targets, *staged, *authority, STATE, PENDING, FAULT,
                           RUNTIME_CONF, c.slot_remote])
        s = {'boot_id': cam.boot_id(), 'kinds': kinds, 'targets': {}, 'records': {}, 'state': {},
             'residue': [p for p in staged if kinds[p] != 'absent'], 'pending': None,
             'health_fault': kinds[FAULT] != 'absent', 'fault_record': None,
             'slot_on_card': kinds[c.slot_remote] == 'dir', 'runtime_release': None}
        for t in c.targets:
            if kinds[t] not in ('file', 'absent'):
                abort('%s is a %s, not a regular file' % (t, kinds[t]))
            s['targets'][t] = cam.read(t) if kinds[t] == 'file' else None
        for path in authority:
            if kinds[path] != 'absent':
                if kinds[path] != 'file':
                    abort('%s is a %s; resolve it by hand' % (path, kinds[path]))
                s['records'][path] = cam.read(path)
        if kinds[PENDING] != 'absent':
            if kinds[PENDING] != 'file':
                abort('%s is a %s' % (PENDING, kinds[PENDING]))
            s['pending'] = cam.read(PENDING)
        if s['health_fault']:
            if kinds[FAULT] != 'file':
                abort('runtime-health-fault is not a regular file')
            s['fault_record'] = cam.read(FAULT)
        if kinds[STATE] not in ('dir', 'absent'):
            abort('%s is a %s, not a private directory' % (STATE, kinds[STATE]))
        names = cam.listdir(STATE) if kinds[STATE] == 'dir' else []
        if names:
            entry_kinds = cam.kinds([STATE + '/' + n for n in names])
            for n in names:
                if not NAME.fullmatch(n) or entry_kinds[STATE + '/' + n] != 'file':
                    abort('unexpected state entry %r; resolve it by hand' % n)
                s['state'][STATE + '/' + n] = cam.read(STATE + '/' + n)
        if kinds[RUNTIME_CONF] == 'file':
            conf = dict(line.split('=', 1) for line in cam.read(RUNTIME_CONF).decode(errors='replace').splitlines()
                        if '=' in line)
            if HEX24.fullmatch(conf.get('release_id', '')):
                s['runtime_release'] = conf['release_id']
        return s

    def previous_release(self):
        rel = (self.replacing or {}).get('release')
        if rel is not None and not HEX24.fullmatch(str(rel)):
            abort('--replacing names an invalid release')
        return rel

    def check_authority(self, s):
        """Authority records present now. All must be well formed, name one previous release
        and be retired explicitly by the operator (--retire-authority <that release>)."""
        found = {CFG + '/' + name: parse_record(name, s['records'][CFG + '/' + name], fmt, keys)
                 for name, fmt, keys in AUTHORITY if CFG + '/' + name in s['records']}
        if not found:
            return found
        named = {r['release_id'] for r in found.values() if 'release_id' in r}
        if self.c.release in named:
            abort('authority for candidate %s predates its install (mixed policy)' % self.c.release)
        if len(named) > 1:
            abort('mixed policy: authority records name %s' % ', '.join(sorted(named)))
        known = {r for r in (self.previous_release(), s['runtime_release']) if r and r != self.c.release}
        if named and known - named:
            abort('mixed policy: records name %s but the camera/--replacing name %s'
                  % (', '.join(named), ', '.join(sorted(known))))
        owner = (named or known or {None}).pop()
        if owner is None:
            abort('cannot attribute %s to a release; pass --replacing' % ', '.join(sorted(found)))
        if self.retire != owner:
            abort('stale authority of release %s (%s); re-run with --retire-authority %s after Adam approves'
                  % (owner, ', '.join(Path(p).name for p in found), owner))
        return found

    def factory_boot(self):
        for name in sorted(self.cam.listdir(CFG)):
            if not name.startswith('rollback-') or archive_kind(name) != 'dir':
                continue
            directory = CFG + '/' + name
            if self.cam.kinds([directory])[directory] != 'dir':
                continue
            path = directory + '/S01app'
            if self.cam.kinds([path])[path] == 'file':
                data = self.cam.read(path)
                if sha(data) == STOCK_S01APP_SHA256:
                    return data
        abort('Stock-only recovery requires a verified factory S01app on NOR')

    def preflight(self, *, require_staged=True, measure_space=True):
        """Read-only. Returns (mode, survey) with mode fresh, resume or done."""
        c, s, j = self.c, self.survey(), self.journal
        status = j and j['status']
        if status == 'rolled-back':
            abort('transaction %s was rolled back; move %s aside to start a new one' % (j['txid'], self.journal_path))
        if status == 'rolling-back':
            abort('rollback is incomplete; run rollback again')
        mode = 'fresh' if not j else 'done' if status == 'selected' else 'resume'
        for t, data in s['targets'].items():
            got = data and sha(data)
            if mode == 'fresh':
                want = (self.replacing['after_sha256'].get(t) if self.replacing
                        else c.targets[t].get('before_sha256'))
                if want and got != want:
                    abort('%s is %s, plan expects %s' % (t, (got or 'absent')[:12], want[:12]))
                if t == SELECTOR and data is not None and data.rstrip(b'\n') not in SELECTOR_MODES:
                    abort('selector content is not a known mode')
            else:
                ok = {j['before'][t], sha(c.after[t])} | ({sha(DISABLED)} if t == SELECTOR else set())
                if got not in ok:
                    abort('%s is %s: neither the original nor the candidate' % (t, (got or 'absent')[:12]))
        if mode == 'fresh' and s['residue']:
            abort('staging residue from an earlier run: %s' % ', '.join(s['residue']))
        if (s['pending'] is not None or s['health_fault']) and not self.clear_latch:
            abort('startup.pending or runtime-health-fault is set; get approval before archiving it (--clear-latch)')
        self.check_authority(s)
        if self.stock_only:
            self.factory_boot()
            if j and j.get('recovery') != 'factory-stock' and (
                    mode != 'resume' or j['archived'] or s['residue'] or
                    any((data and sha(data)) != j['before'][path] for path, data in s['targets'].items()) or
                    self.cam.kinds([j['rollback_dir']])[j['rollback_dir']] != 'absent'):
                abort('Cannot switch a transaction to stock-only recovery after boot writes begin')
        if mode == 'done' and not self.installed(s):
            abort('journal says selected but the camera differs; run rollback or inspect by hand')
        if require_staged and mode != 'done' and not s['slot_on_card']:
            abort('run stage-tf first')
        if mode != 'done':
            s['prune'] = prune_inventory(self.cam, self.archive_root, self.replacing, self.journal)
            if measure_space and s['slot_on_card']:
                self.check_overlay_space(s, pending_prune=bool(s['prune']['obsolete']))
        return mode, s

    def check_overlay_space(self, survey, *, pending_prune=False):
        def blocks(data):
            return (len(data) + 4095) // 4096 * 4096 if data is not None else 0

        needed = OVERLAY_RESERVE + sum(blocks(data) for path, data in self.c.after.items()
                                       if survey['targets'][path] != data)
        for target, current in ({} if self.stock_only else survey['targets']).items():
            if not self.journal:
                needed += blocks(current)
                continue
            previous = self.before_bytes(target)
            backup = self.journal['rollback_dir'] + '/' + Path(target).name
            if previous is not None and (self.cam.kinds([backup])[backup] != 'file' or self.cam.read(backup) != previous):
                needed += blocks(previous)
        available = self.cam.overlay_free_bytes(self.c)
        if available < needed and not pending_prune:
            abort('NOR /overlay has %d bytes free and this transaction needs %d, including reserve'
                  % (available, needed))
        survey['overlay_free_bytes'], survey['overlay_required_bytes'] = available, needed

    def installed(self, s):
        return (all(s['targets'][t] == self.c.after[t] for t in self.c.targets)
                and not s['records'] and not s['state'] and s['pending'] is None and not s['residue'])

    # ---- guarded camera writes ------------------------------------------
    def _guard(self, *paths):
        if not self.writing:
            raise RuntimeError('camera write outside the install transaction')
        for p in paths:
            if Path(p).name in rc_wifi_policy.PRIVATE_NAMES or not (
                    p in self.allowed or any(p.startswith(d + '/') for d in (self.archive, self.rollback_dir))):
                raise RuntimeError('camera write outside the allowlist: %s' % p)

    def put(self, data, target, mode):
        """Stage beside the target, verify, then rename over it (atomic on the overlay)."""
        staged = target + '.rc-new'
        self._guard(staged, target)
        self.cam.write(staged, data, mode)
        if self.cam.read(staged) != data:
            abort('staged %s hash mismatch' % target)
        self.cam.rename(staged, target)
        if self.cam.read(target) != data:
            abort('%s differs after rename' % target)
        self.out('  wrote %s (%s)' % (target, sha(data)[:12]))

    def move(self, src, dst, data):
        self._guard(src, dst)
        if self.cam.kinds([dst])[dst] != 'absent':
            abort('archive destination %s already exists' % dst)
        self.cam.rename(src, dst)
        if self.cam.kinds([src])[src] != 'absent' or self.cam.read(dst) != data:
            abort('archive of %s did not verify' % src)
        self.out('  archived %s -> %s' % (src, dst))

    def ensure_dir(self, path):
        kind = self.cam.kinds([path])[path]
        if kind == 'absent':
            self._guard(path + '/.')
            self.cam.mkdir(path)
        elif kind != 'dir':
            abort('%s is a %s' % (path, kind))

    # ---- journal ---------------------------------------------------------
    def save_journal(self, status=None):
        if status:
            self.journal['status'] = status
            self.journal.setdefault('events', []).append([time.strftime('%Y-%m-%dT%H:%M:%S%z'), status])
        durable_write(self.journal_path, (json.dumps(self.journal, indent=1, sort_keys=True) + '\n').encode())

    def pc_copy(self, kind, path, data):
        """Keep a PC copy of anything about to move or be overwritten, keyed by hash."""
        folder = self.c.work / ('txn-' + self.journal['txid']) / kind
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        local = folder / ('%s.%s' % (path.strip('/').replace('/', '_'), sha(data)[:12]))
        sync_dir(folder.parent.parent)
        sync_dir(folder.parent)
        durable_write(local, data)
        if sha(local.read_bytes()) != sha(data):
            abort('PC copy of %s did not verify' % path)
        return str(local)

    def begin(self, s):
        if not self.journal:
            txid = time.strftime('%Y%m%d-%H%M%S')
            self.journal = {'format': JOURNAL_FORMAT, 'txid': txid, 'release': self.c.release,
                            'version': self.c.report['version'], 'previous_release': self.previous_release(),
                            'start_boot_id': s['boot_id'], 'archive_dir': CFG + '/native-select.archived-' + txid,
                            'rollback_dir': CFG + '/rollback-' + txid, 'before': {}, 'archived': {}}
            previous = receipt_rollback(self.replacing)
            if previous is not None:
                self.journal['replacing_rollback'] = previous
            for t, data in s['targets'].items():
                self.journal['before'][t] = data and sha(data)
                if data is not None:
                    self.pc_copy('before', t, data)
            for path in (self.journal['archive_dir'], self.journal['rollback_dir']):
                if self.cam.kinds([path])[path] != 'absent':
                    abort('%s already exists' % path)
            if self.stock_only:
                self.journal['recovery'] = 'factory-stock'
            self.save_journal('started')
        if self.stock_only and self.journal.get('recovery') != 'factory-stock':
            self.journal['recovery'] = 'factory-stock'
            self.save_journal()
        self.archive, self.rollback_dir = self.journal['archive_dir'], self.journal['rollback_dir']
        self.allowed = {*self.c.targets, *(t + '.rc-new' for t in self.c.targets), PENDING, FAULT, *s['state'],
                        *(CFG + '/' + name for name, _, _ in AUTHORITY)}
        self.writing = True

    def before_bytes(self, target):
        want = self.journal['before'][target]
        if want is None:
            return None
        local = self.c.work / ('txn-' + self.journal['txid']) / 'before' / (
            '%s.%s' % (target.strip('/').replace('/', '_'), want[:12]))
        data = local.read_bytes()
        if sha(data) != want:
            abort('PC copy %s does not match the journal' % local)
        return data

    def clean_residue(self, s):
        for staged in s['residue']:
            self._guard(staged)
            self.cam.remove(staged)
            self.out('  removed staging residue %s' % staged)

    # ---- phases ----------------------------------------------------------
    def install(self):
        mode, s = self.preflight(measure_space=False)
        if mode == 'done':
            self.out('install already complete (transaction %s); camera verified' % self.journal['txid'])
            return self.journal
        # Apply pruning before checking headroom and before touching live boot files.
        # Journal the fresh transaction first when pruning is needed so its receipt
        # and protected set have a stable transaction identity across retries.
        if s['prune']['obsolete']:
            self.begin(s)
            prune(self.cam, self.archive_root, apply=True, txid=self.journal['txid'],
                  inventory=s['prune'], expected_boot=s['boot_id'], out=self.out)
            self.check_overlay_space(s)
        else:
            self.check_overlay_space(s)
            self.begin(s)
        self.out('install %s transaction %s (%s)' % (self.c.release, self.journal['txid'], mode))
        self.clean_residue(s)
        # 1. Stock first: no later crash point can select a half-installed release.
        if s['targets'][SELECTOR] != DISABLED:
            self.put(DISABLED, SELECTOR, self.c.targets[SELECTOR]['mode'])
        # Historical CammyOS bytes already have a verified copy in the LAB journal.
        if not self.stock_only:
            self.ensure_dir(self.rollback_dir)
            for t in self.c.targets:
                data = self.before_bytes(t)
                copy = self.rollback_dir + '/' + Path(t).name
                if data is not None and (self.cam.kinds([copy])[copy] != 'file' or self.cam.read(copy) != data):
                    self.put(data, copy, '0600')
        # 3. Evidence and stale authority, PC copy first, then one synced rename each.
        self.ensure_dir(self.archive)
        moves = sorted(s['state'].items())
        if s['pending'] is not None:
            moves.append((PENDING, s['pending']))
        if s['fault_record'] is not None:
            moves.append((FAULT, s['fault_record']))
        moves += [(CFG + '/' + name, s['records'][CFG + '/' + name])
                  for name, _, _ in AUTHORITY if CFG + '/' + name in s['records']]
        for src, data in moves:
            # A record that reappears after a mid-transaction boot keeps both copies.
            # An entry journaled before a crash, but not yet moved, keeps its destination.
            dst = self.archive + '/' + Path(src).name
            entry = self.journal['archived'].get(dst)
            if entry and not (entry['from'] == src and entry['sha256'] == sha(data)
                              and self.cam.kinds([dst])[dst] == 'absent'):
                dst += '.' + sha(data)[:12]
            self.journal['archived'][dst] = {'from': src, 'sha256': sha(data), 'pc': self.pc_copy('archived', src, data)}
            self.save_journal()
            self.move(src, dst, data)
        # 4. Program files; S99 (which starts the TF runtime and its health check) last.
        for t in PROGRAM_ORDER:
            if s['targets'][t] != self.c.after[t]:
                self.put(self.c.after[t], t, self.c.targets[t]['mode'])
        # 5. Everything reviewed must verify before the selector may change.
        self.verify()
        self.save_journal('verified')
        # 6. The only write that lets the next boot select the candidate.
        self.put(SELECT, SELECTOR, self.c.targets[SELECTOR]['mode'])
        s = self.survey()
        if not self.installed(s):
            abort('camera changed after selection')
        result = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'boot_id': s['boot_id'], 'release': self.c.release,
                  'previous_release': self.journal['previous_release'], 'transaction': self.journal['txid'],
                  'after_sha256': {t: sha(d) for t, d in s['targets'].items()}, 'selector': SELECT.decode().strip(),
                  'state_dir_after': sorted(s['state']), 'archive_dir': self.archive,
                  'archived': sorted(self.journal['archived']), 'on_camera_rollback': None if self.stock_only else self.rollback_dir,
                  'authority_after': 'none: one-shot until tools/rc_resident.py apply after acceptance'}
        if self.stock_only:
            result['recovery'] = 'factory-stock'
        durable_write(self.c.work / 'installed.json', (json.dumps(result, indent=1) + '\n').encode())
        self.save_journal('selected')
        self.out('install OK: selector mode=select, authority none (one-shot), archived %d record(s); '
                 'ready for one manual power cycle' % len(self.journal['archived']))
        self.prune_after(result)
        return result

    def prune_after(self, result):
        """Keep only factory stock and the rollback this install just wrote.

        The pre-install prune had to protect the replaced receipt's rollback in case
        the install stopped part-way. Once the new receipt is durable that older
        rollback is obsolete. Best effort: the install is already complete and
        verified, and a later prune or install retries anything left."""
        try:
            prune(self.cam, self.archive_root, result, apply=True, txid=self.journal['txid'] + '-after',
                  expected_boot=result['boot_id'], out=self.out)
        except (SystemExit, Exception) as exc:  # noqa: BLE001 - never fail a completed install
            self.out('post-install prune skipped (%s); rerun tools/rc_install.py prune --apply' % (exc,))

    def verify(self):
        c, s = self.c, self.survey()
        bad = [t for t in PROGRAM_ORDER if s['targets'][t] != c.after[t]]
        if s['targets'][SELECTOR] != DISABLED:
            bad.append(SELECTOR)
        if bad or s['records'] or s['state'] or s['pending'] is not None or s['residue']:
            abort('verify before select failed: targets %s, records %s, state %s, pending %s, residue %s'
                  % (bad, sorted(s['records']), sorted(s['state']), s['pending'] is not None, s['residue']))
        for dst, item in self.journal['archived'].items():
            if self.cam.kinds([dst])[dst] != 'file' or sha(self.cam.read(dst)) != item['sha256']:
                abort('archived %s does not verify' % dst)
        if self.stock_only:
            self.factory_boot()
        for t in c.targets:
            data = self.before_bytes(t)
            copy = self.rollback_dir + '/' + Path(t).name
            if not self.stock_only and data is not None and (self.cam.kinds([copy])[copy] != 'file' or self.cam.read(copy) != data):
                abort('rollback copy of %s does not verify' % t)
        want = c.files()
        got = self.cam.hashes(c.slot_remote, sorted(want), c.busybox)
        if any(got.get(n) != want[n] for n in want):
            abort('TF slot differs on card: %s' % sorted(n for n in want if got.get(n) != want[n])[:5])
        self.out('  verified %d targets, %d archived records, recovery and %d TF files'
                 % (len(c.targets), len(self.journal['archived']), len(want)))

    def rollback(self):
        if not self.journal:
            abort('no transaction journal at %s' % self.journal_path)
        s = self.survey()
        recovery = ({t: self.factory_boot() if t == PROGRAM_ORDER[0] else None for t in PROGRAM_ORDER}
                    if self.stock_only else {t: self.before_bytes(t) for t in PROGRAM_ORDER})
        for t, data in s['targets'].items():
            ok = {self.journal['before'][t], sha(self.c.after[t]), sha(DISABLED)}
            if t in recovery:
                ok.add(recovery[t] and sha(recovery[t]))
            if (data and sha(data)) not in ok:
                abort('%s is neither the original nor the candidate; inspect by hand' % t)
        self.begin(s)
        self.save_journal('rolling-back')
        self.out('rollback of transaction %s' % self.journal['txid'])
        self.clean_residue(s)
        if s['targets'][SELECTOR] != DISABLED:
            self.put(DISABLED, SELECTOR, self.c.targets[SELECTOR]['mode'])
        for t in reversed(PROGRAM_ORDER):
            data = recovery[t]
            if data is None and s['targets'][t] is not None:
                if self.stock_only:
                    self.pc_copy('archived', t, s['targets'][t])
                    self._guard(t)
                    self.cam.remove(t)
                else:
                    self.ensure_dir(self.archive)
                    self.move(t, '%s/%s.rolled-back' % (self.archive, Path(t).name), s['targets'][t])
            elif data is not None and s['targets'][t] != data:
                self.put(data, t, self.c.targets[t]['mode'])
        s = self.survey()
        bad = [t for t in PROGRAM_ORDER if s['targets'][t] != recovery[t]]
        if bad or s['targets'][SELECTOR] != DISABLED:
            abort('rollback did not verify: %s' % (bad or [SELECTOR]))
        self.save_journal('rolled-back')
        result = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'boot_id': s['boot_id'],
                  'transaction': self.journal['txid'], 'selector': DISABLED.decode().strip(),
                  'restored': list(PROGRAM_ORDER), 'archive_dir': self.archive,
                  'archived_kept': sorted(self.journal['archived'])}
        (self.c.work / 'rolled-back.json').write_text(json.dumps(result, indent=1) + '\n')
        self.out('recovery OK: factory boot files restored, selector disabled' if self.stock_only else
                 'rollback OK: original S01app/allow/S99 restored, selector mode=disabled (stock); '
                 'archived records kept in %s' % self.archive)
        return result


def check(c, cam=None, replacing=None, clear_latch=False, retire_authority=None, *, stock_only=False):
    """Read-only preflight. Writes only the PC checkpoint."""
    cam = cam or AdbCamera()
    tx = Transaction(c, cam, replacing, clear_latch, retire_authority, stock_only=stock_only)
    mode, s = tx.preflight(require_staged=False)
    point = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'boot_id': s['boot_id'], 'release': c.release,
             'mode': mode, 'before_sha256': {t: d and sha(d) for t, d in s['targets'].items()},
             'state_dir': sorted(Path(p).name for p in s['state']), 'startup_pending': s['pending'] is not None,
             'authority': sorted(Path(p).name for p in s['records']), 'runtime_release': s['runtime_release'],
             'runtime_health_fault': s['health_fault'], 'slot_on_card': s['slot_on_card'], **cam.info()}
    if mode != 'done':
        point['prune'] = s['prune']
        if s['prune']['obsolete']:
            log('Install will verify and prune %d obsolete NOR archives before measuring headroom.'
                % len(s['prune']['obsolete']))
    point.update({key: s[key] for key in ('overlay_free_bytes', 'overlay_required_bytes') if key in s})
    (c.work / 'checkpoint.json').write_text(json.dumps(point, indent=1) + '\n')
    log('check OK (%s): boot %s, targets match, state dir %s, startup.pending=%s, authority to retire %s, '
        'runtime-health-fault=%s, slot on card=%s' % (mode, s['boot_id'][:8], point['state_dir'] or 'empty',
                                                     point['startup_pending'], point['authority'] or 'none',
                                                     s['health_fault'], s['slot_on_card']))
    return point


def stage_tf(c, diag):
    want = c.files()
    final, partial = c.slot_remote, c.slot_remote + '.partial'
    if remote_exists(final):
        log('slot already on card; verifying')
    else:
        if remote_exists(partial):
            sh('rm -rf %s' % partial)
        sh('mkdir -p %s' % partial)
        for top in sorted(p.name for p in c.slot_local.iterdir()):
            adb('push', str(c.slot_local / top), partial + '/', timeout=600)
        sh('sync')
        c.busybox = partial + '/runtime/busybox'
        got = c.remote_hashes(partial, sorted(want))
        bad = sorted(n for n in want if got.get(n) != want[n])
        if bad:
            raise SystemExit('ABORT: %d files differ on card after copy: %s' % (len(bad), bad[:5]))
        sh('mv %s %s && sync' % (partial, final))
        c.busybox = final + '/runtime/busybox'
    got = c.remote_hashes(final, sorted(want))
    bad = sorted(n for n in want if got.get(n) != want[n])
    if bad:
        raise SystemExit('ABORT: slot on card differs: %s' % bad[:5])
    if diag:
        sh("printf 'cammy-diag-v1\\n' > %s/runtime/enable-diag && sync" % final)
    log('stage-tf OK: %d files verified on card at %s%s' % (len(want), final, ' (+enable-diag)' if diag else ''))


def load_replacing(path):
    """Previous installed.json; its release comes from the record or its checkpoint."""
    data = json.loads(path.read_text())
    if 'release' not in data and (path.parent / 'checkpoint.json').exists():
        data['release'] = json.loads((path.parent / 'checkpoint.json').read_text()).get('release')
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('candidate')
    ap.add_argument('phase', nargs='?', choices=['check', 'stage-tf', 'install', 'rollback', 'prune'])
    ap.add_argument('--apply', action='store_true', help='prune: archive and remove obsolete files')
    ap.add_argument('--diag', action='store_true', help='stage-tf: enable TF reset diagnostics in the slot')
    ap.add_argument('--replacing', type=Path, help='installed.json of the candidate now on the camera')
    ap.add_argument('--stock-only', action='store_true', help='keep prior boot files on LAB and recover to factory stock')
    ap.add_argument('--clear-latch', action='store_true', help='archive startup.pending (needs Adam)')
    ap.add_argument('--retire-authority', metavar='RELEASE', help='archive that release\'s authority records')
    args = ap.parse_args()
    phase = args.phase or ('prune' if args.candidate == 'prune' else None)
    if phase is None:
        ap.error('a candidate and phase are required for install commands')
    if args.stock_only and phase not in ('check', 'install'):
        ap.error('--stock-only applies to check and install; recovery reads the saved policy')
    if args.apply and phase != 'prune':
        ap.error('--apply is only valid for prune')
    if phase == 'prune' and args.apply and args.replacing is None:
        ap.error('prune --apply requires --replacing with the current installed receipt')
    if subprocess.run(['findmnt', os.environ.get('CAMMY_WORK_MOUNT', '/')], capture_output=True).returncode:
        sys.exit('LAB drive not mounted')
    root = ROOT / 'private/rc06'
    replacing = load_replacing(args.replacing) if args.replacing else None
    if phase == 'prune' and not args.apply:
        if replacing is None:
            log('Pass --replacing with the current installed receipt to include its rollback in the protected set.')
        return prune(AdbCamera(), root, replacing)
    if phase in ('check', 'stage-tf'):
        c = Candidate(args.candidate)
        if phase == 'stage-tf':
            return stage_tf(c, args.diag)
        return check(c, None, replacing, args.clear_latch, args.retire_authority, stock_only=args.stock_only)
    state = root / 'lifecycle'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat.S_ISDIR(state.lstat().st_mode) or state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        abort('The local maintenance directory is not private')
    with maintenance_lock(state):
        if phase == 'prune':
            return prune(AdbCamera(), root, replacing, apply=True)
        c = Candidate(args.candidate)
        tx = Transaction(c, AdbCamera(), replacing, args.clear_latch, args.retire_authority, stock_only=args.stock_only)
        return tx.install() if phase == 'install' else tx.rollback()


if __name__ == '__main__':
    main()
