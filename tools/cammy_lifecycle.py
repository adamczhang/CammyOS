"""CammyOS lifecycle commands. Run with python3 -m tools.cammy_lifecycle."""

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time

from . import rc_install as rc
from .lifecycle_usb import Blocked, MAX_PAYLOAD, UsbCamera, rom_usb_visible
from .native_trial.policy import STOCK_S01APP_SHA256, STOCK_SDVCAM_SHA256

ROOT = rc.ROOT
LAB = os.environ.get('CAMMY_WORK_MOUNT', '/')  # set it to refuse writes without that mount
PRIVATE = ROOT / 'private/rc06'
ARTIFACT = re.compile(r'candidate-[a-z0-9][a-z0-9.-]{0,79}')
RECEIPT = re.compile(r'(?:install|repair|job)-[a-z0-9][a-z0-9.-]{0,79}')
SHA = re.compile(r'[0-9a-f]{64}')
MEMBER = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._-]{0,127}')
VERSION = re.compile(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?:-[a-z0-9.]{1,20})?')
TXID = re.compile(r'[0-9]{8}-[0-9]{6}')
ACTIONS = ('first-device', 'install', 'repair', 'rollback')
TARGETS = (*rc.PROGRAM_ORDER, rc.SELECTOR)
APP = '/usr/bin/sdvcam'
# Reviewed by rc06_validate against each release's source tree. Later edits
# invalidate its source closure. These pins attest those exact bytes only.
REVIEWED = {
    '4277930019624366f385acd7': '9cfc6141ebf5ecbae3a4271fc7d91c65bf9c1ceccaf12c82863dfea3b6dbb5f1',
    '13cd0706ec90c678c1693cc0': 'c687e99a63b19fdb5769bfca1fb9f28dbd1d85f056c60f90b282d31b3f7a1d13',
    '0ecf29d3cb261c4fba0c3f05': '0ed7f0f37f1f8b7369c50cf2deae995778b9d9eba63fa81f9820e53ef0e6cbd4',
    '4ca3e7fe06c4b759a3d23b94': 'e234724cf8cbc0ad50df7ef70ab707fa631df33619facd3c5b2a89ab6ec5a35e',
    'ee30b3be023956132ead3e72': 'b6c6d16042b4916fb046026286defc15be56bd4244cf1992f994ae4526ecdc7d',
    '0efcdabcad33c1f71ccea696': '70527d597d50266d830fd30dd0bda0edda66187839fc18c69d5c672e1000a794',
    '0a61f4d1f0130d8b0f7aea33': '512641db81b54f75b524d4383e3cd43875573aa75749a73a1750d4a9a84ed18d',
    '2046ba794d3f074608166f12': '76a62791dfadfbf6774a49fc03877379be3e6706d073f1654315c39c083169d5',
    '94f01001cc87525856513891': '0d341a9f8fa451715d431709fbbbf040dbec50b132cdc1edcb89d9f805155bc7',
    'fd35d99a2891d3f3a554c1a3': 'f336ce00b367a2ebd18c8d3b5da24bc103109e396d9646b32b7801bf4ca58060',
    'ad2071d9f89de853b87ef996': '7b06668fe7b081dbc2c637641747f46df404160ab9e30250cd6b00309ca583e7',  # 0.9.5
    '0fe35a5dd6a66baa78e3c5bf': '990f99676b0f91494dece2ec26198cb403363d157d5161d2c4a7a37f0115f55e',  # 0.9.6
    '935a63f4ba232a5a777ce056': '6816f083e30d6f318a2c5593e9c4fdc5b9749e9ab46f481e3ee2b89d04eea317',  # 0.9.7
    '25d174b6da65e41912d48cda': 'b401f26682f621dc0c6769b119d2e169b180382f3e69d441e0416c50752f21a6',  # 0.9.8
    '2ba6ccbe0fecbab6daabccc7': '1b81cd32e72165aa298159076a83e1e4517ff00a4e9e073a01922a7f0620bd25',  # 0.9.9
    '5d14bdd1510333800e01e534': '0c45b12c41c8a20f7e49100ed4fbc56a4d0ff96c5f8cc827d21919c904a0a048',  # 0.9.10
    '9345405e6fec5b2532d5892b': 'deb5a6f0adbb62a61695f1a7399b1fef2e6e0d4e4d772692a94d8ed549cf4483',  # 0.9.11
    'f895a29aaa17cd14f2ddf3c6': 'ee51b5be4b8c4817cc9e0f9d1621a7d6cce5242070e2cad4674d7ce7a3e48706',  # 0.9.12
}
MANUAL_BOOT = ('Power the camera off and on with its physical power button, then choose CammyOS. '
               'Leave it on after the face appears. Refresh and inspect USB to confirm the new boot and release. '
               'Wait for release health and the startup guard to clear. A second restart without rearming selects stock.')
RECOVERY = ('Use the owner-authorized maintenance USB procedure, connect directly, and choose Charging '
            'if prompted. Stock AT-M105 may offer read-only inspection. Ensure the camera has a '
            'camera-formatted TF card. If only ROM FEL appears, stop at inspection.')
CONFIG = {
    'wifi profiles': '/etc/cammy/home.conf',
    'fallback AP': '/etc/cammy/ap.conf',
    'portal credential': '/etc/cammy/portal.token',
    'early quiet-start hook': '/etc/init.d/S00cammy-power',
    'quiet-start selection': '/etc/cammy/quiet-start.enabled',
}


def _mounted():
    if subprocess.run(['/usr/bin/findmnt', '--mountpoint', LAB], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, timeout=3).returncode:
        raise Blocked('LAB is not mounted. Mount it before writing lifecycle state.')


def _dir(path, *, create=False, private=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    mode = path.lstat().st_mode
    if not stat.S_ISDIR(mode) or (private and (path.stat().st_uid != os.getuid() or mode & 0o077)):
        raise Blocked('A maintenance directory is invalid or is not private to the local operator.')
    return path


def _read(path, limit=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as file:
        st = os.fstat(file.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
            raise Blocked('A maintenance file has an unexpected type, link or size.')
        raw = file.read(limit + 1)
        if len(raw) > limit:
            raise Blocked('A maintenance file exceeds its bounded size.')
        return raw


def _json(path):
    data = json.loads(_read(path))
    if not isinstance(data, dict):
        raise Blocked('A maintenance record has an invalid format.')
    return data


def _publish(path, value, *, exclusive=False):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.lifecycle-')
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file, sort_keys=True, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        if exclusive:
            os.link(temp, path)
            os.unlink(temp)
        else:
            os.replace(temp, path)
        _sync_dir(path.parent)
    finally:
        Path(temp).unlink(missing_ok=True)


def _sync_dir(path):
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _digest(value):
    return rc.sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def _hashes(data):
    return {name: rc.sha(raw) if raw is not None else None for name, raw in data.items()}


def _boot(value):
    if not isinstance(value, str):
        return None
    return value.replace('-', '') if rc.UUID.fullmatch(value) or rc.HEX32.fullmatch(value) else None


def _candidate_order(name):
    numbers = re.match(r'candidate-([0-9]+)\.([0-9]+)\.([0-9]+)', name)
    return (tuple(map(int, numbers.groups())) if numbers else (), name)


def portal_report(value):
    """Accept only the public boot/version fields of the existing CCC observation."""
    value = value if isinstance(value, dict) else {}
    boot, version = _boot(value.get('boot_id')), value.get('version')
    if boot and isinstance(version, str) and VERSION.fullmatch(version):
        return {'boot_id': boot, 'version': version, 'release': None, 'source': 'Cammy portal'}
    return None


@dataclass(frozen=True)
class Request:
    action: str
    artifact: str
    receipt: str | None = None

    def __post_init__(self):
        if self.action not in ACTIONS or not ARTIFACT.fullmatch(self.artifact):
            raise Blocked('Choose a listed artifact and a supported maintenance action.')
        if self.receipt is not None and not RECEIPT.fullmatch(self.receipt):
            raise Blocked('Choose a listed receipt.')
        if (self.action == 'first-device') == (self.receipt is not None):
            raise Blocked('First device uses exact stock provenance. Other actions require a checked receipt.')


@dataclass(frozen=True)
class Proposal:
    request: Request
    identity: dict
    release: str
    version: str
    artifact_sha256: str
    fingerprint: str
    expires: float
    summary: dict
    nonce: str


class Lifecycle:
    def __init__(self, root=PRIVATE, *, mount_check=_mounted, camera=UsbCamera):
        self.root = root
        self.state = root / 'lifecycle'
        self.mount_check = mount_check
        self.camera = camera

    def _root(self):
        _dir(self.root.parent, private=True)
        return _dir(self.root)

    def _state(self):
        self.mount_check()
        _dir(self.root.parent, create=True, private=True)
        _dir(self.root, create=True)
        return _dir(self.state, create=True, private=True)

    @contextmanager
    def _lock(self):
        try:
            with rc.maintenance_lock(self._state()):
                yield
        except rc.MaintenanceLockError as exc:
            raise Blocked(str(exc)) from exc

    def enroll(self, key):
        if not isinstance(key, str) or not 12 <= len(key) <= 256:
            raise Blocked('Use a maintenance passphrase of 12 to 256 characters.')
        with self._lock():
            salt = secrets.token_bytes(16)
            try:
                _publish(self.state / 'operator.json', {
                    'salt': salt.hex(),
                    'hash': hashlib.scrypt(key.encode(), salt=salt, n=16384, r=8, p=1).hex(),
                }, exclusive=True)
            except FileExistsError as exc:
                raise Blocked('Local maintenance authority is already enrolled.') from exc

    def _operator(self, key):
        try:
            saved = _json(_dir(self.state, private=True) / 'operator.json')
        except FileNotFoundError as exc:
            raise Blocked('Enroll the separate local maintenance passphrase from the project terminal first.') from exc
        if (not isinstance(key, str) or not 1 <= len(key) <= 256
                or not re.fullmatch(r'[0-9a-f]{32}', saved.get('salt', ''))
                or not re.fullmatch(r'[0-9a-f]{128}', saved.get('hash', ''))):
            raise Blocked('Local maintenance approval failed.')
        actual = hashlib.scrypt(key.encode(), salt=bytes.fromhex(saved['salt']), n=16384, r=8, p=1)
        if not hmac.compare_digest(actual.hex(), saved['hash']):
            raise Blocked('Local maintenance approval failed.')

    def catalog(self):
        try:
            root = self._root()
        except FileNotFoundError:
            return {'artifacts': [], 'receipts': []}
        artifacts = sorted((p.name for p in root.iterdir() if ARTIFACT.fullmatch(p.name)
                            and p.is_dir() and not p.is_symlink()), key=_candidate_order, reverse=True)[:64]
        receipts = {p.name for p in root.iterdir() if RECEIPT.fullmatch(p.name)
                    and not p.name.startswith('job-') and p.is_dir() and not p.is_symlink()
                    and (p / 'installed.json').is_file()}
        jobs = self.state / 'jobs'
        if self.state.is_dir() and not self.state.is_symlink() and jobs.is_dir() and not jobs.is_symlink():
            receipts.update(p.name for p in jobs.iterdir() if RECEIPT.fullmatch(p.name)
                            and p.name.startswith('job-') and p.is_dir() and not p.is_symlink()
                            and (p / 'transaction.json').is_file())
        return {'artifacts': artifacts, 'receipts': sorted(receipts, reverse=True)[:64]}

    def _artifact(self, name, snapshot=None):
        if not ARTIFACT.fullmatch(name):
            raise Blocked('Choose a listed candidate directory.')
        folder = _dir(self._root() / name, private=True)
        manifest = _read(folder / 'candidate.json')
        report = json.loads(manifest)
        release, version = report['release_id'], report['version']
        if (not rc.HEX24.fullmatch(release) or not VERSION.fullmatch(version)
                or not 1 <= len(report['files']) <= 2048):
            raise Blocked('The artifact manifest is invalid.')
        if snapshot is not None:
            snapshot.mkdir(mode=0o700)
            (snapshot / 'candidate.json').write_bytes(manifest)
            (snapshot / 'candidate.json').chmod(0o600)
        total = 0
        for name, expected in report['files'].items():
            member = PurePosixPath(name)
            if (not member.parts or member.is_absolute() or '..' in member.parts
                    or member.as_posix() != name or any(not MEMBER.fullmatch(p) for p in member.parts)):
                raise Blocked('The artifact contains an invalid member path.')
            parent = folder
            for part in member.parts[:-1]:
                parent = _dir(parent / part)
            raw = _read(parent / member.name, MAX_PAYLOAD)
            total += len(raw)
            if total > 256 * 1024 * 1024 or expected != {'sha256': rc.sha(raw), 'bytes': len(raw)}:
                raise Blocked('The artifact payload differs from its bounded manifest.')
            if snapshot is not None:
                output = snapshot / name
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                output.write_bytes(raw)
                output.chmod(0o600)
        digest = rc.sha(manifest)
        if REVIEWED.get(release) != digest:
            try:
                from . import rc06_validate
            except ImportError as exc:
                raise Blocked('A complete CammyOS project checkout is required to validate this candidate.') from exc
            rc06_validate.validate(snapshot or folder)
        return release, version, digest

    def review(self, name):
        release, version, digest = self._artifact(name)
        return {'release': release, 'version': version, 'sha256': digest}

    def _receipt_dir(self, name):
        if not RECEIPT.fullmatch(name):
            raise Blocked('Choose a listed receipt.')
        if name.startswith('job-'):
            base = _dir(_dir(self.state, private=True) / 'jobs', private=True)
        else:
            base = self._root()
        return _dir(base / name, private=True)

    def _receipt(self, name):
        path = self._receipt_dir(name)
        record = _json(path / 'installed.json')
        point = self._checkpoint(path)
        hashes = record.get('after_sha256')
        token = record.get('transaction', '')
        if (not rc.HEX24.fullmatch(record.get('release', '')) or point.get('release') != record['release']
                or not TXID.fullmatch(token) or not _boot(record.get('boot_id'))
                or point.get('boot_id') != record['boot_id'] or not isinstance(hashes, dict)
                or set(hashes) != set(TARGETS) or any(not SHA.fullmatch(value) for value in hashes.values())
                or hashes[rc.SELECTOR] != rc.sha(rc.SELECT)):
            raise Blocked('Installed receipt and checkpoint do not match.')
        return record

    def _checkpoint(self, path):
        try:
            return _json(path / 'checkpoint.json')
        except FileNotFoundError:
            # The recorded 7cc5a07 stock repair used Transaction directly. Its
            # durable journal contains the original boot and before-image hashes.
            if path.name != 'repair-0.7.11-d7877c50':
                raise
            journal = _json(path / 'transaction.json')
            if (journal.get('format') != rc.JOURNAL_FORMAT or journal.get('status') != 'selected'
                    or journal.get('txid') != '20261117-205947'
                    or not self._stock(journal['before'])):
                raise Blocked('The historical stock repair record differs.')
            return {'release': journal['release'], 'boot_id': journal['start_boot_id'],
                    'before_sha256': journal['before']}

    def _rollback(self, candidate, name):
        source = self._receipt_dir(name)
        journal = _json(source / 'transaction.json')
        txid = journal.get('txid', '')
        previous = journal.get('before')
        if (journal.get('format') != rc.JOURNAL_FORMAT or journal.get('release') != candidate.release
                or journal.get('version') != candidate.report['version'] or not TXID.fullmatch(txid)
                or not _boot(journal.get('start_boot_id'))
                or journal.get('status') not in {'started', 'verified', 'selected', 'rolling-back'}
                or journal.get('archive_dir') != rc.CFG + '/native-select.archived-' + txid
                or journal.get('rollback_dir') != rc.CFG + '/rollback-' + txid
                or not isinstance(previous, dict) or set(previous) != set(TARGETS)
                or any(value is not None and not SHA.fullmatch(value) for value in previous.values())):
            raise Blocked('The rollback journal is invalid or belongs to another artifact.')
        point = self._checkpoint(source)
        if (point.get('release') != candidate.release
                or point.get('boot_id') != journal['start_boot_id']
                or point.get('before_sha256') != previous):
            raise Blocked('The rollback journal and preflight checkpoint disagree.')
        if journal['status'] == 'selected':
            installed = self._receipt(name)
            if installed['transaction'] != txid or installed['after_sha256'] != _hashes(candidate.after):
                raise Blocked('The installed receipt does not match this rollback artifact.')
        folder = _dir(_dir(source / ('txn-' + txid)) / 'before', private=True)
        backups = {}
        for path, digest in previous.items():
            if digest is not None:
                filename = path.strip('/').replace('/', '_') + '.' + digest[:12]
                raw = _read(folder / filename)
                if rc.sha(raw) != digest:
                    raise Blocked('A required before-image did not verify.')
                backups[filename] = raw
        if not isinstance(journal.get('archived'), dict) or not isinstance(journal.get('events'), list):
            raise Blocked('The rollback journal has invalid archive metadata.')
        return journal, backups

    @staticmethod
    def _targets(cam):
        kinds = cam.kinds(TARGETS)
        if any(kind not in {'file', 'absent'} for kind in kinds.values()):
            raise Blocked('One or more boot targets has an unexpected file type.')
        return {name: cam.read(name) if kinds[name] == 'file' else None for name in TARGETS}

    def status(self, *, portal=None, usb=False, artifact=None):
        running = portal_report(portal)
        result = {'running': running, 'installed': None, 'on_tf': None, 'usb': None,
                  'local': self.catalog(), 'candidate': None, 'next': RECOVERY}
        if artifact:
            try:
                result['candidate'] = self.review(artifact)
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, SystemExit) as exc:
                result['candidate'] = {'detail': public_error(exc)}
        if not usb:
            return result
        result['usb'] = {'rom_fel_visible': rom_usb_visible(), 'authorized': False}
        try:
            cam = self.camera()
            identity = cam.identity()
            targets = self._targets(cam)
            hashes = _hashes(targets)
            result['usb'].update(identity, authorized=True)
            result['usb']['baseline'] = ('exact stock S01app, Cammy hooks absent'
                                         if self._stock(hashes) else 'Cammy or other boot targets')
            if cam.kinds([rc.RUNTIME_CONF])[rc.RUNTIME_CONF] == 'file':
                # The runtime writes a separate release marker. Only join it to a
                # portal version from the same boot, never to a local candidate.
                conf = cam.read(rc.RUNTIME_CONF)
                matches = re.findall(rb'(?m)^release_id=([0-9a-f]{24})$', conf)
                versions = re.findall(rb'(?m)^version=([a-z0-9.-]{1,32})$', conf)
                if conf.startswith(b'format=cammy-native-release/1\n') and len(matches) == 1:
                    version = versions[0].decode() if len(versions) == 1 else ''
                    version = version if VERSION.fullmatch(version) else None
                    if not running or running['boot_id'] != _boot(identity['boot_id']):
                        running = {'boot_id': _boot(identity['boot_id']), 'version': version,
                                   'source': 'USB runtime marker'}
                    elif version and running['version'] != version:
                        raise Blocked('Portal and USB report different versions for the same boot.')
                    else:
                        running['source'] = 'Cammy portal and USB runtime marker'
                    running['release'] = matches[0].decode()
                    result['running'] = running
            for name in result['local']['receipts']:
                try:
                    record = self._receipt(name)
                except (Blocked, OSError, ValueError, TypeError, KeyError):
                    continue
                if all(hashes[path] == record['after_sha256'][path] for path in rc.PROGRAM_ORDER):
                    selector = targets[rc.SELECTOR]
                    result['installed'] = {'release': record['release'], 'receipt': name,
                                           'receipt_boot': record['boot_id'],
                                           'selector': {rc.SELECT: 'select', rc.DISABLED: 'disabled',
                                                        b'mode=observe\n': 'observe'}.get(selector, 'unknown')}
                    rollback = rc.receipt_rollback(record)
                    if rollback:
                        result['installed']['nor_rollback'] = cam.kinds([rollback])[rollback]
                    break
            guard = cam.kinds([rc.PENDING, rc.CFG + '/runtime-health-fault',
                               *(rc.CFG + '/' + name for name, _, _ in rc.AUTHORITY)])
            result['usb']['guard_or_authority'] = [Path(path).name for path, kind in guard.items() if kind != 'absent']
            result['usb']['missing_setup'] = [key for key, kind in self._config(cam).items() if kind != 'file']
            try:
                cam.tf_mount()
                result['usb']['tf_mount'] = 'camera TF mounted read/write'
            except Blocked:
                result['usb']['tf_mount'] = 'camera TF mount unavailable'
            local = result['candidate']
            if local and 'release' in local:
                dest = '/mnt/extsd/CammyOS-releases/' + local['release']
                observed = (cam.kinds([dest])[dest] if result['usb']['tf_mount'] == 'camera TF mounted read/write'
                            else 'card unavailable')
                result['on_tf'] = {**local, 'state': {'absent': 'absent', 'dir': 'present, hashes unchecked',
                                                     'card unavailable': observed}.get(observed, 'unexpected type')}
            cam.assert_boot()
        except (Blocked, OSError, RuntimeError, ValueError, TypeError, KeyError, SystemExit) as exc:
            result['usb']['detail'] = public_error(exc)
            # No partial survey may be mistaken for a verified boot observation.
            result['installed'] = result['on_tf'] = None
            result['running'] = portal_report(portal)
        return result

    @staticmethod
    def _stock(hashes):
        return hashes == {path: STOCK_S01APP_SHA256 if path == TARGETS[0] else None for path in TARGETS}

    @staticmethod
    def _config(cam):
        observed = cam.kinds(CONFIG.values())
        return {key: observed[path] for key, path in CONFIG.items()}

    def _survey(self, cam, request, candidate, progress):
        identity = cam.identity()
        if rc.sha(cam.read(APP, limit=MAX_PAYLOAD)) != STOCK_SDVCAM_SHA256:
            raise Blocked('The stock sdvcam binary does not match the reviewed AT-M105 provenance.')
        progress('Verified authorized USB, boot identity and exact stock application.')
        replacing = None
        source = None
        prune = None
        if request.receipt:
            source = (self._rollback(candidate, request.receipt) if request.action == 'rollback'
                      else self._receipt(request.receipt))
        tx = rc.Transaction(candidate, cam, out=lambda _: None, archive_root=self.root)
        survey = tx.survey()
        if survey['boot_id'] != identity['boot_id']:
            raise Blocked('The boot changed during preflight.')
        if survey['records']:
            raise Blocked('Resident, advance or retry authority is present. Use the separate reviewed retirement procedure.')
        if request.action != 'rollback' and (survey['pending'] is not None or survey['health_fault']):
                raise Blocked('A startup guard or runtime fault is present. Leave the camera powered on and preserve '
                              'the records. Check the selector decision and boot ID before approving recovery.')
        hashes = _hashes(survey['targets'])
        if request.action == 'rollback':
            journal, backups = source
            for path, before in journal['before'].items():
                allowed = {before, rc.sha(candidate.after[path])}
                if path == rc.SELECTOR:
                    allowed.add(rc.sha(rc.DISABLED))
                if hashes[path] not in allowed:
                    raise Blocked('The boot targets do not belong to this rollback transaction.')
            review = {'journal': _digest(journal), 'backups': _hashes(backups)}
            tf = None
            staged = 'not required for rollback'
        else:
            stock = self._stock(hashes)
            if request.action != 'install' and survey['targets'][rc.SELECTOR] == rc.DISABLED:
                stock = self._stock({**hashes, rc.SELECTOR: None})
            if request.action == 'first-device' and not stock:
                raise Blocked('First-device setup requires exact stock S01app, absent Cammy hooks and stock selection.')
            if request.action == 'repair' and source['release'] != candidate.release:
                raise Blocked('Repair requires an installed receipt for the same exact release.')
            if request.action == 'repair' and source['after_sha256'] != _hashes(candidate.after):
                raise Blocked('The receipt does not match the candidate boot files.')
            if request.action == 'install' or (request.action == 'repair' and not stock):
                if not all(hashes[path] == source['after_sha256'][path] for path in rc.PROGRAM_ORDER):
                    raise Blocked('The current boot files do not match the installed receipt. Inspect recovery.')
                if survey['targets'][rc.SELECTOR] not in {rc.SELECT, rc.DISABLED}:
                    raise Blocked('The current selector is outside the supported install state.')
            if stock and (survey['state'] or survey['runtime_release']):
                raise Blocked('Stock boot targets coexist with Cammy runtime or claims. Inspect recovery before proceeding.')
            # Supply exact observed hashes only after every target above matches the
            # stock or receipt baseline. The reviewed plan expects a legacy S99
            # on the upgrade path, while a factory stock unit has no S99.
            replacing = {'release': source['release'] if source and not stock else None,
                         'after_sha256': hashes}
            tx.replacing = replacing
            if source and not stock:
                replacing.update({key: source[key] for key in ('transaction', 'on_camera_rollback', 'recovery') if key in source})
            _, checked = tx.preflight(require_staged=False)
            prune = checked['prune']
            tf = cam.tf_identity()
            parent = str(PurePosixPath(candidate.slot_remote).parent)
            kinds = cam.kinds([parent, candidate.slot_remote])
            if kinds[parent] not in {'dir', 'absent'} or kinds[candidate.slot_remote] not in {'dir', 'absent'}:
                raise Blocked('The TF release destination is not a safe directory.')
            if kinds[candidate.slot_remote] == 'dir':
                if cam.hashes(candidate.slot_remote, sorted(candidate.files()), None) != candidate.files():
                    raise Blocked('The existing TF slot differs from the reviewed artifact. Preserve it for inspection.')
                progress('Verified all existing TF release members.')
                staged = 'verified on the TF card'
            else:
                staged = 'will be copied and verified after approval'
            review = _digest(source) if source else 'exact factory stock'
        config = self._config(cam)
        cam.assert_boot()
        if tf is not None and cam.tf_identity() != tf:
            raise Blocked('The TF card changed during preflight.')
        evidence = {'identity': identity, 'before_sha256': hashes, 'evidence': review, 'tf': tf,
                    'staged': staged, 'runtime_release': survey['runtime_release'],
                    'state': _hashes(survey['state']), 'residue': survey['residue'],
                    'pending': rc.sha(survey['pending']) if survey['pending'] is not None else None,
                    'health_fault': survey['health_fault'], 'setup': config, 'prune': prune}
        return evidence, replacing, source

    def prepare(self, request, progress=lambda _: None):
        progress('Checking the local candidate and receipt provenance.')
        release, version, digest = self._artifact(request.artifact)
        # Candidate needs a journal path even during a read-only survey.
        with tempfile.TemporaryDirectory(prefix='cammy-preflight-') as tmp:
            candidate = rc.Candidate(self._root() / request.artifact, work=tmp)
            if candidate.release != release:
                raise Blocked('The selected artifact changed.')
            evidence, _, _ = self._survey(self.camera(), request, candidate, progress)
        if rc.sha(_read(self._root() / request.artifact / 'candidate.json')) != digest:
            raise Blocked('The selected artifact changed.')
        summary = {k: evidence[k] for k in ('before_sha256', 'tf', 'staged', 'runtime_release', 'setup', 'prune')}
        return Proposal(request, evidence['identity'], release, version, digest,
                        _digest(evidence), time.monotonic() + 300, summary, secrets.token_hex(10))

    def _stage(self, cam, candidate, nonce, progress):
        final = candidate.slot_remote
        parent = str(PurePosixPath(final).parent)
        wanted = candidate.files()
        if cam.kinds([final])[final] == 'absent':
            partial = final + '.partial-' + nonce
            if cam.kinds([parent])[parent] == 'absent':
                cam.mkdir(parent)
            cam.mkdir(partial)
            for folder in ('native', 'runtime'):
                cam.mkdir(partial + '/' + folder)
            for index, name in enumerate(sorted(wanted), 1):
                cam.push_file(candidate.slot_local / name, partial + '/' + name)
                progress(f'Copied TF member {index}/{len(wanted)}.')
            if cam.hashes(partial, sorted(wanted), None) != wanted:
                raise Blocked('The copied TF slot did not verify. The incomplete slot was retained for inspection.')
            if cam.kinds([final])[final] != 'absent':
                raise Blocked('The final TF slot appeared while staging. Inspect before proceeding.')
            cam.rename_new(partial, final)
        if cam.hashes(final, sorted(wanted), None) != wanted:
            raise Blocked('The final TF slot did not verify.')
        progress('Verified the complete TF slot. Checking the boot targets before the transaction.')

    def apply(self, proposal, key, progress=lambda _: None):
        if not isinstance(proposal, Proposal) or time.monotonic() > proposal.expires:
            raise Blocked('The approval expired. Prepare a fresh USB inspection.')
        with self._lock():
            self._operator(key)
            self._apply_locked(proposal, progress)
        return {'job': 'job-' + proposal.nonce, 'action': proposal.request.action,
                'release': proposal.release, 'next': MANUAL_BOOT if proposal.request.action != 'rollback'
                else 'Power off and turn on physically. Verify that the stock startup path opens.'}

    @staticmethod
    def _checked_survey(live, evidence):
        return (live['boot_id'] == evidence['identity']['boot_id']
                and _hashes(live['targets']) == evidence['before_sha256']
                and not live['records']
                and live['health_fault'] == evidence['health_fault']
                and live['residue'] == evidence['residue']
                and _hashes(live['state']) == evidence['state']
                and live['runtime_release'] == evidence['runtime_release']
                and (rc.sha(live['pending']) if live['pending'] is not None else None) == evidence['pending'])

    def _apply_locked(self, proposal, progress):
        if not re.fullmatch(r'[0-9a-f]{20}', proposal.nonce):
            raise Blocked('The local approval token is invalid.')
        jobs = _dir(self.state / 'jobs', create=True, private=True)
        job = jobs / ('job-' + proposal.nonce)
        try:
            job.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise Blocked('This approval has already been used. Inspect the existing transaction.') from exc
        request = proposal.request
        try:
            _publish(job / 'request.json', asdict(request))
            with tempfile.TemporaryDirectory(dir=job, prefix='.artifact-') as tmp:
                snapshot = Path(tmp) / 'candidate'
                progress('Revalidating the approved artifact and live USB preflight.')
                release, version, digest = self._artifact(request.artifact, snapshot=snapshot)
                if (release, version, digest) != (proposal.release, proposal.version, proposal.artifact_sha256):
                    raise Blocked('The artifact changed after approval.')
                candidate = rc.Candidate(snapshot, work=job)
                cam = self.camera()
                evidence, replacing, source = self._survey(cam, request, candidate, progress)
                if evidence['identity'] != proposal.identity or _digest(evidence) != proposal.fingerprint:
                    raise Blocked('Boot, USB, TF card, receipt or preflight state changed after approval.')
                point = {'release': release, 'boot_id': proposal.identity['boot_id'],
                         'before_sha256': evidence['before_sha256'], 'action': request.action,
                         'artifact_sha256': digest, 'identity': proposal.identity, 'tf': evidence['tf']}
                class ApprovedTransaction(rc.Transaction):
                    def begin(tx, live):
                        if not self._checked_survey(live, evidence) or live.get('prune') != evidence['prune']:
                            raise Blocked('Camera state changed immediately before writing.')
                        cam.assert_boot()
                        super().begin(live)

                if request.action == 'rollback':
                    journal, backups = source
                    _publish(job / 'rollback-preflight.json', point)
                    folder = job / ('txn-' + journal['txid']) / 'before'
                    folder.parent.mkdir(mode=0o700)
                    folder.mkdir(mode=0o700)
                    for name, data in backups.items():
                        with (folder / name).open('xb') as file:
                            os.fchmod(file.fileno(), 0o600)
                            file.write(data)
                            file.flush()
                            os.fsync(file.fileno())
                    _sync_dir(folder)
                    _sync_dir(folder.parent)
                    _publish(job / 'transaction.json', journal)
                    progress('Restoring checked before-images with the selector disabled.')
                    ApprovedTransaction(candidate, cam, out=lambda _: None).rollback()
                else:
                    _publish(job / 'checkpoint.json', point)
                    cam.approved_tf = evidence['tf']
                    self._stage(cam, candidate, proposal.nonce, progress)
                    tx = ApprovedTransaction(candidate, cam, replacing, out=progress, archive_root=self.root)
                    _, live = tx.preflight(measure_space=False)
                    if not self._checked_survey(live, evidence) or cam.tf_identity() != cam.approved_tf:
                        raise Blocked('Camera state changed during staging. The boot transaction was stopped.')
                    kinds = cam.kinds([rc.CFG, '/etc/init.d'])
                    if kinds['/etc/init.d'] != 'dir' or kinds[rc.CFG] not in {'dir', 'absent'}:
                        raise Blocked('The writable boot paths are not safe directories.')
                    if kinds[rc.CFG] == 'absent':
                        cam.mkdir(rc.CFG)
                    progress('Backing up and changing the checked boot files. Selection is the final write.')
                    tx.install()
                after = _hashes(self._targets(cam))
                expected = ({**journal['before'], rc.SELECTOR: rc.sha(rc.DISABLED)}
                            if request.action == 'rollback' else _hashes(candidate.after))
                cam.assert_boot()
                if after != expected:
                    raise Blocked('Final boot-file readback differs. Inspect the journal before restarting.')
                _publish(job / 'after.json', {'boot_id': proposal.identity['boot_id'], 'sha256': after})
            _publish(job / 'result.json', {'status': 'verified', 'release': release})
        except (Blocked, OSError, RuntimeError, ValueError, TypeError, KeyError, SystemExit) as exc:
            reason = public_error(exc)
            _publish(job / 'result.json', {'status': 'stopped', 'detail': reason})
            raise Blocked(f'{reason} Inspect {job.name} before retrying.') from exc

    def review_request(self, request):
        review = self.review(request.artifact)
        if request.receipt:
            if request.action == 'rollback':
                self._receipt_dir(request.receipt)
            else:
                self._receipt(request.receipt)
        return review

    def queue(self, request, allowed=lambda: True):
        """Write typed local intent only. No approval material or hardware I/O."""
        review = self.review_request(request)
        with self._lock():
            if not allowed():
                raise Blocked('The active Adult Brain changed. Start a new request.')
            _publish(self.state / 'request.json', {'request': asdict(request), 'review': review})
        return review

    def queued(self):
        self._root()
        try:
            data = _json(_dir(self.state, private=True) / 'request.json')
        except FileNotFoundError:
            return None
        request = Request(**data['request'])
        if data['review'] != self.review(request.artifact):
            raise Blocked('The queued artifact changed. Prepare a fresh request.')
        return request


def public_error(exc):
    if isinstance(exc, Blocked):
        return str(exc)
    if 'No space left on device' in str(exc):
        return ('A storage write ran out of space. Inspect the job to identify internal flash or microSD. '
                'Keep the journal and verified rollback. Leave the camera powered on while recovering space.')
    # Installer/validator errors can include raw tool output, device paths and
    # local paths. The UI and CLI publish only controlled operational messages.
    if isinstance(exc, SystemExit):
        return 'The checked installer refused the operation. Inspect USB state and the saved local receipt.'
    return 'A maintenance check failed. Verify the private artifact, receipt, and authorized USB setup.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('enroll', help='set the separate local maintenance passphrase')
    sub.add_parser('catalog')
    for command in ('status', 'inspect-recovery'):
        status = sub.add_parser(command)
        status.add_argument('--artifact')
        if command == 'status':
            status.add_argument('--offline', action='store_true', help='list local evidence without probing USB')
    for command in ('prepare', 'apply'):
        action = sub.add_parser(command)
        action.add_argument('action', choices=ACTIONS)
        action.add_argument('artifact')
        action.add_argument('--receipt')
    args = parser.parse_args()
    lifecycle = Lifecycle()
    try:
        if args.command == 'enroll':
            if not os.isatty(0):
                raise Blocked('Enrollment requires an interactive local terminal.')
            key = getpass.getpass('New local maintenance passphrase: ')
            if key != getpass.getpass('Confirm local maintenance passphrase: '):
                raise Blocked('Passphrases differed.')
            lifecycle.enroll(key)
            result = {'status': 'enrolled'}
        elif args.command == 'catalog':
            result = lifecycle.catalog()
        elif args.command in {'status', 'inspect-recovery'}:
            result = lifecycle.status(usb=not getattr(args, 'offline', False), artifact=args.artifact)
        else:
            request = Request(args.action, args.artifact, args.receipt)
            def progress(message):
                print(message, file=sys.stderr, flush=True)

            if args.command == 'apply' and not os.isatty(0):
                raise Blocked('Applying a lifecycle action requires an interactive local terminal.')
            proposal = lifecycle.prepare(request, progress)
            result = approval_summary(proposal)
            if args.command == 'apply':
                print(json.dumps(result, indent=2), flush=True)
                if input('Type APPLY to approve this exact action, USB device and artifact: ') != 'APPLY':
                    raise Blocked('Local approval was canceled.')
                result = lifecycle.apply(proposal, getpass.getpass('Local maintenance passphrase: '), progress)
        print(json.dumps(result, indent=2))
    except (Blocked, OSError, RuntimeError, ValueError, TypeError, KeyError, SystemExit) as exc:
        parser.exit(2, public_error(exc) + '\n')


def approval_summary(proposal):
    return {'action': proposal.request.action, 'artifact': proposal.request.artifact,
            'receipt': proposal.request.receipt, 'version': proposal.version,
            'release': proposal.release, 'manifest_sha256': proposal.artifact_sha256,
            'device': proposal.identity, **proposal.summary}


if __name__ == '__main__':
    main()
