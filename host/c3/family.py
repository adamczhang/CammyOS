"""Family mode for Cammy Control Center (CCC): which C3 features are open, from Cammy's face.

Deployments are fixed when C3 starts and never change at run time:

  family            (default) The Gemini Developer API is never used: C3 does not read the
                    Gemini key, builds no client and sends no microphone, text or vision to
                    it, in Kids or Adult mode. Routes that would reach Gemini answer 503.
  adult-standalone  The original C3 with Gemini Live, for adult-only use. It needs its own
                    explicit `--deployment adult-standalone`; nothing in family mode can
                    switch to it or activate cloud.

Family modes live in CCC, not on the camera. 'kids' keeps device controls and local play;
'adult' adds Brain, providers, instructions, Look Back, transcript, live frames to agents and
the other outward routes. The mode follows the face pack CCC knows Cammy shows:

  - Pikachu and Fin and Fur: always Kids. Nothing in CCC opens Adult on them.
  - Cammy (the CCC-only pack): Adult by default, as soon as Cammy reports it or a CCC
    selection of it is confirmed. No PIN, pack grant, provider or lease check is involved.
  - An unknown pack, or no report yet since C3 started: Kids.
  - Camera offline or unreadable: the last known pack still decides, so a brief disconnect
    does not flip the mode. A Cammy grant that ended only because the face lease lapsed
    (`ccc_grant.last_end == "lease"`) is the same transport detail; a physical pick, a CCC
    "child" selection, stock camera mode or a reboot onto a Kids pack is a real change.
  - A Kids override, set from CCC and saved on disk, holds Kids on any pack until CCC resets
    it to automatic. It never opens Adult on a Kids pack.
  - An Adult hold (Adam, 2026-11-21: "everything is adult mode for the time being") is the
    file `adult-hold` in the family folder, read when C3 starts. It holds Adult on any pack
    unless a Kids override or a child face selection is in force. Delete the file and restart
    C3 to return to the face rule.

Every mode change bumps the generation and tells the listeners (C3 drops queued audio, ends
play, cancels the Brain run). Work that started under an older generation is withheld when it
finishes (see `api_middleware`).

Calendar access is separate (`CalendarAccess`): the opt-in private Executor Calendar needs the
desktop PIN on top of Adult mode, for the selected Brain profile only, and closes on Kids, a
profile change or reselection, expiry or an explicit lock, without leaving Adult mode.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

log = logging.getLogger('c3.family')

DEPLOYMENTS = ('family', 'adult-standalone')
FAMILY, STANDALONE = DEPLOYMENTS
KIDS, ADULT = 'kids', 'adult'
KIDS_PACKS = frozenset({'pikachu', 'fin-fur'})
ADULT_PACK = 'cammy'
KNOWN_PACKS = {'pikachu': 0, 'fin-fur': 1, ADULT_PACK: 2}
KIDS_CAPABILITIES = frozenset({'device', 'play', 'local_voice'})
CLOUD = 'cloud'
CALENDAR_S = 30 * 60      # Calendar access lasts at most this long per PIN unlock
PIN_MIN, PIN_MAX = 6, 12
FREE_FAILURES = 5         # wrong attempts in a row before the first wait
LOCKOUT_BASE_S = 30.0     # then 30 s, 60 s, 120 s ... per further failure
LOCKOUT_CAP_S = 3600.0


class FamilyError(Exception):
    def __init__(self, code, message, status=403, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra

    def body(self):
        return {'code': self.code, 'error': self.message, **self.extra}


def face_report(state):
    """(pack, why) from one device read. pack is 'pikachu', 'fin-fur', 'cammy', 'unknown' (a
    pack CCC does not know: Kids) or None (nothing to go on: keep the last known pack)."""
    if not isinstance(state, dict) or state.get('face_live') is False:
        return None, 'unavailable'
    pack = state.get('face_pack')
    if not isinstance(pack, str) or not pack:
        return None, 'unavailable'
    index = state.get('face_pack_index')
    if pack not in KNOWN_PACKS or (type(index) is int and index != KNOWN_PACKS[pack]):
        return 'unknown', 'unknown_pack'
    return pack, state.get('face_grant_end') if isinstance(state.get('face_grant_end'), str) else 'none'


def _private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _write_private(path, text):
    tmp = path.with_name('.%s.%s.tmp' % (path.name, secrets.token_hex(4)))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class PinStore:
    """The Calendar access PIN (scrypt) and its persistent attempt limit, in a private folder.
    It gates only the opt-in private Calendar (`CalendarAccess`), never the family mode.

    pin.json      {"v":1,"salt","hash","n","r","p"}
    limit.json    {"failures","locked_until"} (Unix s): survives restarts
    setup-code    only while no PIN exists; needed to set the first PIN
    """

    def __init__(self, folder, *, clock=time.time, scrypt_n=2 ** 15):
        self.folder = Path(folder)
        self.clock = clock
        self.n = scrypt_n
        self._lock = threading.Lock()

    @property
    def _pin(self):
        return self.folder / 'pin.json'

    @property
    def _limit(self):
        return self.folder / 'limit.json'

    @property
    def setup_code_file(self):
        return self.folder / 'setup-code'

    def has_pin(self):
        return self._read(self._pin) is not None

    def prepare(self):
        """Make the folder; with no PIN, make the setup code if it is missing."""
        with self._lock:
            _private_dir(self.folder)
            if not self.has_pin() and not self.setup_code_file.exists():
                _write_private(self.setup_code_file, secrets.token_urlsafe(18) + '\n')

    @staticmethod
    def _read(path):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _hash(self, pin, salt, n, r, p):
        return hashlib.scrypt(pin.encode(), salt=salt, n=n, r=r, p=p, maxmem=256 << 20, dklen=32)

    def limit_state(self):
        data = self._read(self._limit) or {}
        failures = data.get('failures') if type(data.get('failures')) is int else 0
        until = data.get('locked_until') if isinstance(data.get('locked_until'), (int, float)) else 0.0
        wait = min(LOCKOUT_CAP_S, until - self.clock())     # capped even if the clock went back
        return {'failures': max(0, failures), 'retry_after_s': max(0.0, round(wait, 1))}

    def _check_limit(self):
        retry = self.limit_state()['retry_after_s']
        if retry > 0:
            raise FamilyError('rate_limited', 'Too many wrong attempts. Try again in %d s.' % max(1, int(retry + 0.999)),
                              status=429, retry_after_s=retry)

    def _record(self, ok):
        failures = 0 if ok else self.limit_state()['failures'] + 1
        over = failures - FREE_FAILURES + 1     # the FREE_FAILURES-th wrong attempt starts the wait
        until = self.clock() + min(LOCKOUT_CAP_S, LOCKOUT_BASE_S * 2 ** min(over - 1, 7)) if over > 0 else 0.0
        _private_dir(self.folder)
        _write_private(self._limit, json.dumps({'failures': failures, 'locked_until': until}))

    def _attempt(self, check):
        """Count the attempt before checking it, so parallel guesses cannot all pass the limit."""
        with self._lock:
            self._check_limit()
            self._record(False)
            ok = bool(check())
            if ok:
                self._record(True)
            return ok

    def verify(self, pin):
        if not isinstance(pin, str) or len(pin) > 64:
            pin = ''

        data = self._read(self._pin)
        if data is None:
            raise FamilyError('no_pin', 'No Calendar access PIN is set. Set one in the Family tab first.', status=409)
        try:
            want, salt = bytes.fromhex(data['hash']), bytes.fromhex(data['salt'])
            n, r, p = int(data['n']), int(data['r']), int(data['p'])
        except (KeyError, ValueError, TypeError):
            raise FamilyError('pin_unreadable', 'The saved PIN is unreadable. Remove private/c3/family/pin.json '
                              'from the desktop account and set a new one.', status=409)
        return self._attempt(lambda: hmac.compare_digest(want, self._hash(pin, salt, n, r, p)))

    def set_pin(self, new_pin, *, setup_code=None, current_pin=None):
        """First PIN: needs the setup code. A change: needs the current PIN. Never unlocks."""
        if not (isinstance(new_pin, str) and new_pin.isdigit() and PIN_MIN <= len(new_pin) <= PIN_MAX
                and new_pin.isascii()):
            raise FamilyError('bad_pin', 'The PIN must be %d to %d digits.' % (PIN_MIN, PIN_MAX), status=400)
        if len(set(new_pin)) == 1:
            raise FamilyError('bad_pin', 'Use a PIN with more than one digit.', status=400)
        if self.has_pin():
            if not self.verify(current_pin if isinstance(current_pin, str) else ''):
                raise FamilyError('wrong_pin', 'The current PIN is wrong.')
        else:
            if not self.setup_code_file.exists():    # pin.json was removed to reset: a new code
                self.prepare()

            def check():
                try:
                    want = self.setup_code_file.read_text().strip()
                except OSError:
                    return False
                given = setup_code if isinstance(setup_code, str) else ''
                return bool(want) and hmac.compare_digest(want.encode(), given.strip().encode())
            if not self._attempt(check):
                raise FamilyError('wrong_setup_code', 'The setup code is wrong. The Family tab reads it from '
                                  'private/c3/family/setup-code.')
        salt = secrets.token_bytes(16)
        record = {'v': 1, 'salt': salt.hex(), 'n': self.n, 'r': 8, 'p': 1,
                  'hash': self._hash(new_pin, salt, self.n, 8, 1).hex()}
        with self._lock:
            _private_dir(self.folder)
            _write_private(self._pin, json.dumps(record))
            try:
                self.setup_code_file.unlink()
            except FileNotFoundError:
                pass
        return True


class FamilyPolicy:
    """Kids/Adult mode from the known face pack and CCC's saved Kids override.

    Thread-safe: Eyes' router thread asks it. `folder` holds mode.json ({"kids_override"});
    without a folder the override lasts until C3 stops."""

    def __init__(self, folder=None, *, deployment=FAMILY, clock=time.monotonic):
        if deployment not in DEPLOYMENTS:
            raise ValueError('deployment: ' + ' | '.join(DEPLOYMENTS))
        self.deployment = deployment
        self.folder = Path(folder) if folder is not None else None
        self.clock = clock
        self._lock = threading.RLock()
        self._listeners = []
        self.mode = KIDS
        self.generation = 0
        self.reason = 'started'
        self._pack = None           # the last known face pack; None until a known report
        self._report = None         # (monotonic, pack or None, why, selectable) of the newest read
        self._selecting = None      # 'cammy' or 'child' while a CCC selection runs
        self.kids_override = self._load_override()
        self.adult_hold = self.folder is not None and (self.folder / 'adult-hold').is_file()
        with self._lock:
            self._update()          # an override saved earlier shows as the reason at once

    @property
    def family(self):
        return self.deployment == FAMILY

    # ---- the saved override -------------------------------------------------------------
    @property
    def _mode_file(self):
        return self.folder / 'mode.json' if self.folder is not None else None

    def _load_override(self):
        path = self._mode_file
        if path is None or not path.exists():
            return False
        try:
            data = json.loads(path.read_text())
            value = data.get('kids_override') if isinstance(data, dict) else None
        except (OSError, ValueError):
            value = None
        if type(value) is not bool:
            log.warning('family: %s is unreadable; holding Kids until CCC resets it', path)
            return True             # fail closed
        return value

    def _save_override(self, on):
        if self._mode_file is None:
            return
        _private_dir(self.folder)
        _write_private(self._mode_file, json.dumps({'v': 1, 'kids_override': bool(on)}))

    def set_kids_override(self, on):
        """CCC's Kids switch. On: Kids now, on any pack, until reset. Off (Reset to automatic):
        the face pack decides again, so Adult only if Cammy shows the Cammy pack."""
        if not self.family:
            raise FamilyError('not_family', 'This C3 runs the adult-only standalone deployment.', status=409)
        on = bool(on)
        with self._lock:
            try:
                self._save_override(on)
            except OSError:
                if not on:
                    raise FamilyError('save_failed', 'The Kids setting could not be saved; Kids stays on.',
                                      status=503) from None
                log.warning('family: the Kids override could not be saved; it holds until C3 stops')
            self.kids_override = on
            event = self._update()
        self._emit(event)
        return self.snapshot()

    # ---- listeners and the mode ---------------------------------------------------------
    def add_listener(self, fn):
        """fn(event) after each mode change: {'mode','previous','reason','generation'}. Called
        on the thread that made the change, outside the lock; it must not block."""
        self._listeners.append(fn)

    def _emit(self, event):
        if event is None:
            return
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - one listener never stops another
                log.exception('family listener failed')

    def _target(self):
        if not self.family:
            return ADULT, 'standalone'
        if self._selecting == 'child':
            return KIDS, 'face_selection'
        if self.kids_override:
            return KIDS, 'kids_override'
        if self.adult_hold:
            return ADULT, 'adult_hold'
        if self._pack == ADULT_PACK:
            return ADULT, 'cammy_face'
        if self._pack in KIDS_PACKS:
            return KIDS, 'kids_face'
        return KIDS, 'unknown_face' if self._pack == 'unknown' else 'started'

    def _update(self):
        """Under the lock: apply the target. Returns the event to emit, or None."""
        mode, reason = self._target()
        self.reason = reason
        if mode == self.mode:
            return None
        previous, self.mode = self.mode, mode
        self.generation += 1
        log.info('family: %s -> %s (%s), generation %d', previous, mode, reason, self.generation)
        return {'mode': mode, 'previous': previous, 'reason': reason, 'generation': self.generation}

    def observe(self, state, *, chosen=False):
        """One device read (dict), or None when Cammy is offline or unreadable."""
        pack, why = face_report(state)
        selectable = isinstance(state, dict) and state.get('face_pack_select') is True
        with self._lock:
            self._report = (self.clock(), pack, why, selectable)
            if pack is None:
                return              # nothing new: the last known pack still decides
            if not chosen and pack in KIDS_PACKS and why == 'lease' and self._pack == ADULT_PACK:
                return              # the grant lapsed with the lease: transport, not a choice
            self._pack = pack
            event = self._update()
        self._emit(event)

    def allows(self, capability):
        """True if `capability` may run now. Fail closed: unknown names are adult capabilities."""
        if capability == CLOUD:
            return self.deployment == STANDALONE
        if not self.family or capability in KIDS_CAPABILITIES:
            return True
        return self.mode == ADULT

    # ---- CCC face selection -------------------------------------------------------------
    def begin_selection(self, pack_id):
        """A Kids selection ('child') holds Kids from now until it ends; a Cammy selection
        changes nothing until Cammy reports the Cammy pack."""
        with self._lock:
            if self._selecting is not None:
                raise FamilyError('face_selection', 'A face selection is already in progress.', status=409)
            self._selecting = 'child' if pack_id != ADULT_PACK else ADULT_PACK
            event = self._update()
        self._emit(event)

    def end_selection(self):
        with self._lock:
            self._selecting = None
            event = self._update()
        self._emit(event)

    def snapshot(self):
        with self._lock:
            report, now = self._report, self.clock()
            out = {'deployment': self.deployment, 'mode': self.mode, 'reason': self.reason,
                   'generation': self.generation, 'cloud': self.deployment == STANDALONE,
                   'selecting': self._selecting is not None}
            if not self.family:
                return out
            out['kids_override'] = self.kids_override
            out['adult_hold'] = self.adult_hold
            out['automatic'] = not self.kids_override
            out['face'] = {'pack': self._pack, 'known': self._pack is not None,
                           'reported': report[1] if report else None,
                           'available': bool(report and report[1] is not None),
                           'age_s': round(now - report[0], 1) if report else None,
                           'selectable': bool(report and report[3])}
        return out


class CalendarAccess:
    """Opt-in private Executor Calendar access: Adult mode plus the desktop PIN, for one
    selected Brain profile, for at most `access_s`.

    `profile()` returns the selection key (profile id, then anything that changes when the
    profile is reselected) or None. A grant closes, with a new generation, on Kids, any family
    mode change, a different key (another profile or the same one reselected), expiry and lock.
    Closing it never changes the family mode."""

    def __init__(self, pins, policy, *, profile=lambda: None, clock=time.monotonic, access_s=CALENDAR_S):
        self.pins = pins
        self.policy = policy
        self._profile = profile
        self.clock = clock
        self.access_s = float(access_s)
        self._lock = threading.RLock()
        self._grant = None          # {'key', 'family', 'expires'}
        self.generation = 0
        self.reason = 'started'
        policy.add_listener(self._on_family)

    def _on_family(self, event):
        with self._lock:
            if self._grant and self._grant.get('desktop'):
                return
        if event.get('mode') != ADULT:
            self.lock('kids_mode')
        else:
            self.lock('family_changed')

    def _key(self):
        try:
            key = self._profile()
        except Exception:  # noqa: BLE001 - an unreadable profile is a changed profile
            return None
        if isinstance(key, str):
            key = (key,)
        if not (isinstance(key, tuple) and key and isinstance(key[0], str) and key[0]):
            return None
        return key

    def lock(self, reason='locked'):
        """Close access now (a no-op when closed). Returns True if it was open."""
        with self._lock:
            if self._grant is None:
                return False
            self._grant = None
            self.generation += 1
            self.reason = reason
        log.info('calendar access: closed (%s), generation %d', reason, self.generation)
        return True

    def _valid(self):
        """The open grant, or None (closing a lapsed one)."""
        with self._lock:
            grant = self._grant
        if grant is None:
            return None
        if not self.policy.family or (not grant.get('desktop') and self.policy.mode != ADULT):
            reason = 'kids_mode'
        elif not grant.get('desktop') and self.policy.generation != grant['family']:
            reason = 'family_changed'
        elif self.clock() >= grant['expires']:
            reason = 'expired'
        elif self._key() != grant['key']:
            reason = 'profile_changed'
        else:
            with self._lock:
                return grant if self._grant is grant else None
        with self._lock:
            if self._grant is grant:
                self._grant = None
                self.generation += 1
                self.reason = reason
        return None

    def unlock(self, pin, profile_id=None, *, desktop=False):
        if not self.policy.family:
            raise FamilyError('not_family', 'This C3 runs the adult-only standalone deployment.', status=409)
        if not desktop and self.policy.mode != ADULT:
            raise FamilyError('kids_mode', 'Calendar access needs Adult mode: the Cammy face and no Kids '
                              'override.', status=409)
        key = self._key()
        if key is None:
            raise FamilyError('no_profile', 'Select a Brain profile first.', status=409)
        if profile_id is not None and profile_id != key[0]:
            raise FamilyError('profile_changed', 'The Brain profile changed; try again.', status=409)
        if self._valid() is not None:
            raise FamilyError('already_open', 'Calendar access is already open.', status=409)
        family = self.policy.generation
        if not self.pins.verify(pin):
            raise FamilyError('wrong_pin', 'Wrong PIN.')
        with self._lock:
            if (not desktop and (self.policy.mode != ADULT or self.policy.generation != family)) or self._key() != key:
                raise FamilyError('changed', 'Cammy or the Brain profile changed during the unlock; '
                                  'try again.', status=409)
            self.generation += 1
            self.reason = 'pin'
            self._grant = {'key': key, 'family': family, 'desktop': desktop, 'expires': self.clock() + self.access_s}
        log.info('calendar access: open, generation %d', self.generation)
        return self.snapshot(desktop=desktop)

    # ---- what the calendar service asks -------------------------------------------------
    def allows(self, profile_id):
        try:
            grant = self._valid()
            return grant is not None and isinstance(profile_id, str) and grant['key'][0] == profile_id
        except Exception:  # noqa: BLE001 - fail closed
            log.exception('calendar access: check failed')
            return False

    def grant(self, profile_id):
        """The generation to quote later to current(), or None while closed."""
        with self._lock:
            generation = self.generation
        return generation if self.allows(profile_id) and generation == self.generation else None

    def current(self, profile_id, generation):
        """True if a grant from grant() is still the open one (nothing closed or reopened since)."""
        return (type(generation) is int and generation == self.generation
                and self.allows(profile_id) and generation == self.generation)

    def snapshot(self, *, desktop=False):
        grant = self._valid() if self.policy.family else None
        if not desktop and self.policy.mode != ADULT:
            grant = None
        with self._lock:
            out = {'open': grant is not None, 'reason': self.reason, 'generation': self.generation,
                   'profile': grant['key'][0] if grant else None,
                   'expires_in_s': round(max(0.0, grant['expires'] - self.clock())) if grant else None}
        if self.pins is not None:
            out['pin_set'] = self.pins.has_pin()
            out['limit'] = self.pins.limit_state()
        if desktop:
            out['desktop'] = True
        return out


async def select_face_pack(policy, pack_id, *, apply, read_state, teardown, timeout=20.0, interval=0.5,
                           sleep=asyncio.sleep, clock=time.monotonic):
    """Change Cammy's face from CCC, then wait for a read-back that shows it.

    'child' (end the Cammy pack): Kids at once, the adult paths stopped, then the change.
    'cammy': the change first; Adult opens when Cammy reports the Cammy pack (unless the Kids
    override is on). Raises FamilyError ('unsupported', 'not_confirmed', ...)."""
    if not isinstance(pack_id, str) or pack_id not in {*KNOWN_PACKS, 'child'}:
        raise FamilyError('bad_face_pack', 'Choose Cammy or a Kids face pack.', status=400)
    if apply is None:
        raise FamilyError('unsupported', 'This firmware cannot change the face pack from CCC; '
                          'use the menu on Cammy.', status=501)
    kids = pack_id != ADULT_PACK
    policy.begin_selection(pack_id)
    try:
        if kids:
            await teardown({'mode': KIDS, 'reason': 'face_selection', 'generation': policy.generation})
        await apply(pack_id)
        deadline = clock() + timeout
        while True:
            try:
                state = await read_state()
            except Exception:  # noqa: BLE001 - offline counts as not confirmed yet
                state = None
            shown = face_report(state)[0]
            confirmed = shown in KIDS_PACKS if pack_id == 'child' else shown == pack_id
            policy.observe(state, chosen=confirmed)
            if confirmed:
                break
            if clock() >= deadline:
                raise FamilyError('not_confirmed', 'Cammy did not report the new face pack in time.', status=504)
            await sleep(interval)
    finally:
        policy.end_selection()
    return {'face_pack': shown, 'confirmed': True, 'family': policy.snapshot()}


class GatedTranscript:
    """Transcript wrapper that keeps nothing unless `allowed()` (Adult mode)."""

    def __init__(self, inner, allowed):
        self._inner, self._allowed = inner, allowed

    def add(self, who, text):
        if self._allowed():
            return self._inner.add(who, text)
        return None

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---- the API --------------------------------------------------------------------------
PUBLIC = 'public'
ROUTE_CLASSES = {
    ('GET', '/v1/status'): PUBLIC,          # redacted in kids by C3.status()
    ('GET', '/v1/family'): PUBLIC,
    ('POST', '/v1/family/mode'): PUBLIC,
    ('POST', '/v1/family/face-pack'): PUBLIC,
    ('GET', '/v1/calendar-access'): PUBLIC,
    ('POST', '/v1/calendar-access/unlock'): PUBLIC,   # refuses in Kids itself
    ('POST', '/v1/calendar-access/lock'): PUBLIC,
    ('POST', '/v1/calendar-access/pin'): PUBLIC,
    ('GET', '/v1/calendar'): PUBLIC,        # setup only, no account details while locked
    ('POST', '/v1/calendar/cancel'): PUBLIC,      # removing access works in Kids (panel token)
    ('POST', '/v1/calendar/disconnect'): PUBLIC,
    ('GET', '/v1/play'): PUBLIC,
    ('POST', '/v1/play/start'): PUBLIC,
    ('POST', '/v1/play/answer'): PUBLIC,
    ('POST', '/v1/play/stop'): PUBLIC,
    ('POST', '/v1/turn-around'): PUBLIC,
    ('POST', '/v1/follow'): PUBLIC,
    ('POST', '/v1/camera-view'): PUBLIC,
    ('POST', '/v1/duplex'): PUBLIC,
    ('POST', '/v1/talk-gate'): PUBLIC,
    ('POST', '/v1/message'): CLOUD,
    ('POST', '/v1/say'): CLOUD,
    ('POST', '/v1/sleep'): CLOUD,
    ('POST', '/v1/wake'): CLOUD,
    ('POST', '/v1/look-around'): CLOUD,     # its photos go to Gemini
    ('POST', '/v1/capture'): CLOUD,         # the result is a turn for Gemini
}


def route_class(method, path):
    """PUBLIC, CLOUD or ADULT. Anything not listed is ADULT (fail closed)."""
    return ROUTE_CLASSES.get((method.upper(), path.rstrip('/') or '/'), ADULT)


DESKTOP_ROUTES = frozenset({
    ('GET', '/v1/transcript'), ('GET', '/v1/brain'), ('GET', '/v1/brain/history'),
    ('POST', '/v1/brain/profiles'), ('POST', '/v1/brain/select'), ('POST', '/v1/brain/message'),
    ('POST', '/v1/brain/cancel'), ('GET', '/v1/providers'), ('POST', '/v1/providers/test'),
    ('GET', '/v1/instructions'), ('PUT', '/v1/instructions/agent'),
    ('GET', '/v1/preview/history'), ('GET', '/v1/preview/frame'),
    ('POST', '/v1/calendar/connect'), ('POST', '/v1/calendar/test'), ('POST', '/v1/calendar/query'),
    ('GET', '/v1/calendar/result'),
})


def desktop_route(method, path):
    parts = path.split('/')
    return ((method, path) in DESKTOP_ROUTES or
            (method == 'PUT' and len(parts) == 5 and parts[:4] == ['', 'v1', 'instructions', 'skills']) or
            (method == 'POST' and len(parts) == 6 and parts[:4] == ['', 'v1', 'brain', 'profiles']
             and parts[5] == 'message'))


def api_middleware(policy):
    from aiohttp import web

    def refuse(status, code, message):
        return web.json_response({'code': code, 'error': message, 'family': policy.snapshot()}, status=status)

    @web.middleware
    async def family_gate(request, handler):
        if not policy.family:
            return await handler(request)
        kind = route_class(request.method, request.path)
        if kind == PUBLIC:
            return await handler(request)
        if kind == CLOUD:
            return refuse(503, 'cloud_disabled', 'The family deployment never uses the Gemini Developer API.')
        if request.get('desktop') and desktop_route(request.method, request.path):
            return await handler(request)
        generation = policy.generation
        if not policy.allows(ADULT):
            return refuse(403, 'kids_mode', 'Cammy is in Kids mode. Adult controls open with the Cammy face '
                          'and no Kids override in CCC.')
        response = await handler(request)
        if policy.generation != generation or not policy.allows(ADULT):
            return refuse(403, 'kids_mode', 'Kids mode started during the request; the result was withheld.')
        return response

    return family_gate


def add_routes(app, c3, policy, body, access=None):
    """GET /v1/family, POST /v1/family/{mode,face-pack}; with `access`, /v1/calendar-access."""
    from aiohttp import web

    def failed(exc):
        return web.json_response({**exc.body(), 'family': policy.snapshot()}, status=exc.status)

    async def state(request):
        out = policy.snapshot()
        if access is not None:
            out['calendar_access'] = access.snapshot(desktop=bool(request.get('desktop')))
        return web.json_response(out)

    async def mode(request):
        data = await body(request)
        choice = data.get('mode')
        if choice not in ('kids', 'automatic'):
            return failed(FamilyError('bad_mode', 'Choose "kids" or "automatic".', status=400))
        try:
            return web.json_response(await asyncio.to_thread(policy.set_kids_override, choice == 'kids'))
        except FamilyError as exc:
            return failed(exc)

    async def face_pack(request):
        data = await body(request)
        try:
            return web.json_response(await c3.select_face_pack(data.get('id')))
        except FamilyError as exc:
            return failed(exc)

    routes = [web.get('/v1/family', state), web.post('/v1/family/mode', mode),
              web.post('/v1/family/face-pack', face_pack)]
    if access is not None:
        def access_failed(exc, request):
            return web.json_response({**exc.body(), 'calendar_access': access.snapshot(
                desktop=bool(request.get('desktop')))}, status=exc.status)

        async def access_state(request):
            return web.json_response(access.snapshot(desktop=bool(request.get('desktop'))))

        async def unlock(request):
            data = await body(request)
            profile = data.get('profile')
            try:
                if hasattr(c3, 'family_refresh_access'):
                    await c3.family_refresh_access()
                return web.json_response(await asyncio.to_thread(
                    access.unlock, data.get('pin'), profile if isinstance(profile, str) else None,
                    desktop=bool(request.get('desktop'))))
            except FamilyError as exc:
                return access_failed(exc, request)

        async def lock(request):
            access.lock('operator')
            return web.json_response(access.snapshot(desktop=bool(request.get('desktop'))))

        async def pin(request):
            data = await body(request)
            try:
                await asyncio.to_thread(access.pins.set_pin, data.get('new_pin'), setup_code=data.get('setup_code'),
                                        current_pin=data.get('current_pin'))
            except FamilyError as exc:
                return access_failed(exc, request)
            access.lock('pin_changed')
            return web.json_response({'pin_set': True, 'calendar_access': access.snapshot(
                desktop=bool(request.get('desktop')))})

        routes += [web.get('/v1/calendar-access', access_state), web.post('/v1/calendar-access/unlock', unlock),
                   web.post('/v1/calendar-access/lock', lock), web.post('/v1/calendar-access/pin', pin)]
    app.add_routes(routes)
