"""Read-only calendar for the selected Brain profile, answered on this PC only.

C3 never holds Executor credentials or reaches the network for the calendar: it asks the
separate calendar broker (`host/cammy_calendar_broker.py`) over its private Unix socket, with
fixed operations. Results go to the Control Center's Calendar tab (and optionally local
text-to-speech for a spoken request). They never enter a provider prompt, the Brain chat,
the transcript, Gemini or memory.

Brain requests reach this module through local intent routing (`parse_intent`): calendar
reads run here; calendar requests Cammy cannot do (changes, other days) are refused here with
a reason, never forwarded to the provider. Everything else goes to the provider unchanged.

Every account operation pins the selected profile (its ID and selection generations) and
requires `C3.calendar_access_allows(profile_id) is True`, before and after each broker call:
Calendar access is the separate opt-in PIN gate (c3/family.py `CalendarAccess`) on top of Adult
mode. When C3 also offers `calendar_access_grant(profile_id)` /
`calendar_access_current(profile_id, generation)`, the grant taken before the call must still
be current after it. Without `calendar_access_allows`, calendar access stays off. Cancelling a
sign-in and disconnecting remove access, so they need neither Adult mode nor Calendar access.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cammy_calendar_broker as broker_mod

log = logging.getLogger('c3.calendar')

RANGES = broker_mod.RANGES
LABELS = {'today': 'Today', 'tomorrow': 'Tomorrow', 'next': 'Next event'}
_TIMEOUTS = {'health': 3, 'status': 5, 'cancel_connect': 5, 'disconnect': 30, 'connect': 75,
             'test': 120, 'events': 120}
_MAX_REPLY = 256 * 1024

BLOCKED = {
    'no_profile': 'Select a Brain profile first.',
    'calendar_access_missing': 'Calendar is off: this C3 has no Calendar access gate.',
    'calendar_access_error': 'Calendar is off: Calendar access could not be checked.',
    'calendar_locked': 'Calendar access is locked for this profile. Unlock it with the Calendar '
                       'access PIN in the Family tab (Adult mode only).',
    'profile_changed': 'The Brain profile changed during the request; nothing was shown.',
    'broker_unavailable': 'The calendar broker is not running on this PC.',
    'read_only': 'Cammy can only read your calendar; it cannot add, change or remove events.',
    'unsupported_range': 'Cammy can show today, tomorrow, or your next event.',
    **broker_mod.MESSAGES,
}


ACCESS_CODES = frozenset({'calendar_access_missing', 'calendar_access_error', 'calendar_locked'})


class CalendarBlocked(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code if code in BLOCKED else 'protocol'

    @property
    def message(self) -> str:
        return BLOCKED[self.code]


# ---- local intent routing ------------------------------------------------------------------

@dataclass(frozen=True)
class Intent:
    kind: str                 # 'read' or 'blocked'
    range: str | None = None
    reason: str | None = None


_NOT_CALENDAR = re.compile(
    r"\b(notes?|minutes|summar\w*|transcripts?|recordings?|projects?|repos?|repository|code|modules?|files?|"
    r"bugs?|tests?|functions?|api|features?|implement\w*|docs?|documentation|logs?|threads?|emails?|inbox|"
    r"cron|jobs?|backups?|builds?|deploy\w*|scripts?|python|sql|database)\b")
_CAL = re.compile(r"\b(calendar|agenda)\b|\b(my|today's|tomorrow's|the day's) schedule\b|\bschedule (for|today|tomorrow)\b")
_EVENT = re.compile(r"\b(meetings?|appointments?|events?|calls?|plans)\b")
_OWNED = re.compile(r"\b(my|do i have|have i got|i have|i've got|am i|is there anything|anything on)\b")
_FREE = re.compile(r"\bam i (free|busy|booked)\b")
_ANYTHING = re.compile(r"\b(do i have|have i got|is there|got) anything (on )?(coming up|upcoming|today|tonight|"
                       r"tomorrow|this (morning|afternoon|evening)|next)\b")
_MUTATE = re.compile(
    r"\b(add|create|book|put|set up|cancel|delete|remove|move|reschedule|change|update|edit|invite|accept|"
    r"decline|rsvp|block (off|out)|clear)\b|\bschedule (a|an|me|my|the|it|this|that)\b")
_TODAY = re.compile(r"\b(today|tonight|this (morning|afternoon|evening))\b")
_TOMORROW = re.compile(r"\btomorrow\b")
_NEXT = re.compile(r"\b(next|upcoming|coming up|soonest)\b")
_OTHER = re.compile(
    r"\b(yesterday|weekend|fortnight|(next|this|last|coming) (week|month|year)|"
    r"(mon|tues|wednes|thurs|fri|satur|sun)days?|"
    r"jan(uary)?|feb(ruary)?|march|april|june|july|aug(ust)?|sept?(ember)?|oct(ober)?|nov(ember)?|dec(ember)?|"
    r"\d{1,2}(st|nd|rd|th)|\d{1,2}/\d{1,2}|in \w+ (days|weeks))\b")


def parse_intent(text: str) -> Intent | None:
    """Calendar read, calendar refusal, or None (not about Adam's calendar: forward as usual)."""
    if not isinstance(text, str) or len(text) > 400:
        return None
    t = ' '.join(text.lower().replace('’', "'").split())
    if _NOT_CALENDAR.search(t):
        return None
    today, tomorrow, other = bool(_TODAY.search(t)), bool(_TOMORROW.search(t)), bool(_OTHER.search(t))
    nxt = bool(_NEXT.search(re.sub(r"\bnext (week|month|year)\b", '', t)))
    dated = today or tomorrow or nxt or other
    calendar = bool(_CAL.search(t))
    events = bool(_EVENT.search(t)) and (dated or bool(_OWNED.search(t)) and bool(re.search(r"\b(any|anything|what|when|next)\b", t)))
    if not (calendar or events or _FREE.search(t) or _ANYTHING.search(t)):
        return None
    if _MUTATE.search(t):
        return Intent('blocked', reason='read_only')
    if other or today + tomorrow + nxt > 1:
        return Intent('blocked', reason='unsupported_range')
    return Intent('read', range='tomorrow' if tomorrow else 'next' if nxt else 'today')


# ---- broker client -------------------------------------------------------------------------

class BrokerClient:
    """One JSON line each way over the broker's private Unix socket."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else broker_mod.default_socket()

    async def call(self, op: str, **fields) -> dict:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(self.path), limit=_MAX_REPLY), 3)
        except (OSError, asyncio.TimeoutError):
            raise CalendarBlocked('broker_unavailable') from None
        try:
            writer.write(json.dumps({'op': op, **fields}).encode() + b'\n')
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), _TIMEOUTS[op])
        except (OSError, asyncio.TimeoutError, ValueError, asyncio.LimitOverrunError):
            raise CalendarBlocked('broker_unavailable') from None
        finally:
            writer.close()
        try:
            reply = json.loads(line)
        except ValueError:
            raise CalendarBlocked('broker_unavailable') from None
        if not isinstance(reply, dict):
            raise CalendarBlocked('protocol')
        if reply.get('ok') is not True:
            raise CalendarBlocked(reply.get('code') if isinstance(reply.get('code'), str) else 'protocol')
        return reply


# ---- display -------------------------------------------------------------------------------

def _clock(value: datetime) -> str:
    return value.strftime('%I:%M %p').lstrip('0')


def _event(raw) -> dict:
    """Re-validate what the broker sent: title and times only."""
    if not isinstance(raw, dict) or set(raw) != {'title', 'start', 'end', 'all_day', 'private'}:
        raise CalendarBlocked('protocol')
    title, start, end, all_day, private = (raw[k] for k in ('title', 'start', 'end', 'all_day', 'private'))
    if (not isinstance(title, str) or len(title) > 120 or not isinstance(start, str)
            or not isinstance(all_day, bool) or not isinstance(private, bool) or (end is not None and not isinstance(end, str))):
        raise CalendarBlocked('protocol')
    try:
        if all_day:
            day = datetime.fromisoformat(start).date()
            when = 'All day'
        else:
            begin = datetime.fromisoformat(start).astimezone()
            day = begin.date()
            finish = datetime.fromisoformat(end).astimezone() if end else None
            when = _clock(begin) + (' – ' + _clock(finish) if finish else '')
    except ValueError:
        raise CalendarBlocked('protocol') from None
    return {'title': 'Private event' if private else title, 'when': when, 'day': day.isoformat(),
            'all_day': all_day, 'private': private}


def _display(reply: dict, profile: str, profile_name: str) -> dict:
    events = reply.get('events')
    if reply.get('range') not in RANGES or not isinstance(events, list) or len(events) > broker_mod.MAX_EVENTS:
        raise CalendarBlocked('protocol')
    events = [_event(e) for e in events]
    label = LABELS[reply['range']]
    if not events:
        lines = ['Nothing in the next 7 days.' if reply['range'] == 'next' else 'Nothing on the calendar.']
    else:
        today = datetime.now().astimezone().date().isoformat()
        lines = [('%s%s, %s' % ('' if e['day'] == today or reply['range'] != 'next'
                                else datetime.fromisoformat(e['day']).strftime('%A '), e['when'], e['title']))
                 for e in events]
        if reply.get('truncated'):
            lines.append('More events not shown.')
    return {'profile': profile, 'profile_name': profile_name, 'range': reply['range'], 'label': label,
            'events': events, 'lines': lines, 'truncated': bool(reply.get('truncated')),
            'timezone': reply.get('timezone') if isinstance(reply.get('timezone'), str) else None,
            'fetched_at': time.time()}


def speech_text(result: dict) -> str:
    return '%s: %s' % (result['label'], ' '.join(line if line.endswith('.') else line + '.' for line in result['lines']))


class SpdSay:
    """Optional local text-to-speech through speech-dispatcher on this PC (text on stdin)."""

    def __init__(self, executable='spd-say'):
        self.executable = executable

    async def __call__(self, text: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.executable, '--wait', '--pipe-mode', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env={k: os.environ[k] for k in ('PATH', 'HOME', 'XDG_RUNTIME_DIR', 'LANG') if k in os.environ})
        try:
            await asyncio.wait_for(proc.communicate(text.encode()), 60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


# ---- service -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Pin:
    profile: str
    name: str
    generations: tuple


def panel_token(folder) -> str:
    """Second token that only the Control Center window reads: calendar data and account
    actions need it, so MCP and agent clients holding the API token get neither."""
    path = Path(folder) / 'calendar-panel-token'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_hex(24))
    return path.read_text().strip()


class CalendarService:
    def __init__(self, c3, broker: BrokerClient | None = None, *, speak=None, mic_tail_s: float = 1.6):
        self.c3 = c3
        self.broker = broker if broker is not None else BrokerClient()
        self.speak = speak
        self.mic_tail_s = mic_tail_s
        self._last: tuple[Pin, object, dict] | None = None
        self._speaking_until = 0.0
        self._speaking = 0

    # -- pin and guard
    def _current(self, selected: bool = False) -> Pin | None:
        """The pinned profile: the active one (Adult mode only), or with `selected` the selected
        resident profile whatever the mode, which only removing access may use."""
        getter = getattr(self.c3, 'selected_profile', None) if selected else None
        if not callable(getter):
            getter = getattr(self.c3, 'active_profile', None)
        profile = getter() if callable(getter) else None
        if not profile:
            return None
        brain = getattr(self.c3, 'brain', None)
        extra = getattr(self.c3, 'profile_generation', None)
        extra = extra() if callable(extra) else extra
        return Pin(profile[0], profile[1], (getattr(brain, 'generation', None), extra))

    def _pin(self, expected: str | None = None, *, selected: bool = False) -> Pin:
        pin = self._current(selected)
        if pin is None:
            raise CalendarBlocked('no_profile')
        if expected is not None and expected != pin.profile:
            raise CalendarBlocked('profile_changed')
        return pin

    def _access(self, profile: str) -> None:
        check = getattr(self.c3, 'calendar_access_allows', None)
        if not callable(check):
            raise CalendarBlocked('calendar_access_missing')
        try:
            allowed = check(profile)
        except Exception:      # noqa: BLE001 - fail closed
            raise CalendarBlocked('calendar_access_error') from None
        if allowed is not True:
            raise CalendarBlocked('calendar_locked')

    def _grant(self, profile: str):
        """Access check now; with C3's grant hooks, the generation to re-check later (else None)."""
        self._access(profile)
        take = getattr(self.c3, 'calendar_access_grant', None)
        if not (callable(take) and callable(getattr(self.c3, 'calendar_access_current', None))):
            return None
        try:
            grant = take(profile)
        except Exception:      # noqa: BLE001 - fail closed
            raise CalendarBlocked('calendar_access_error') from None
        if grant is None:
            raise CalendarBlocked('calendar_locked')
        return grant

    def _still(self, pin: Pin, grant=None, *, desktop=False) -> None:
        if self._current(selected=desktop) != pin:
            raise CalendarBlocked('profile_changed')
        self._access(pin.profile)
        if grant is not None:
            try:
                current = self.c3.calendar_access_current(pin.profile, grant)
            except Exception:  # noqa: BLE001
                current = False
            if current is not True:
                raise CalendarBlocked('calendar_locked')

    async def _guarded(self, pin: Pin, op: str, *, desktop=False, **fields) -> tuple[dict, object]:
        grant = self._grant(pin.profile)
        await self.refresh_access()
        self._still(pin, grant, desktop=desktop)
        reply = await self.broker.call(op, profile=pin.profile, **fields)
        await self.refresh_access()
        self._still(pin, grant, desktop=desktop)    # ABA: the same ID re-selected has a new generation
        return reply, grant

    async def refresh_access(self):
        refresh = getattr(self.c3, 'family_refresh_access', None)
        if refresh is not None:
            await refresh()

    # -- status and account actions (Control Center only)
    async def status(self, *, panel: bool = False, desktop: bool = False) -> dict:
        """Setup state, no events. In Kids only the Control Center window (`panel`) sees the
        selected profile and its connection state, so it can still disconnect or cancel."""
        active = self._current(selected=desktop and panel)
        pin = active or (self._current(selected=True) if panel else None)
        access = {'integrated': callable(getattr(self.c3, 'calendar_access_allows', None)), 'allowed': False}
        result = {'profile': pin.profile if pin else None, 'profile_name': pin.name if pin else None,
                  'selection': pin.generations if pin else None, 'adult': active is not None,
                  'access': access, 'broker': {'running': False}, 'connection': None,
                  'tool': broker_mod.CALENDAR_TOOL, 'mcp': broker_mod.EXECUTOR_MCP}
        if active is not None and access['integrated']:
            try:
                access['generation'] = self._grant(active.profile)
                access['allowed'] = True
            except CalendarBlocked as exc:
                access['code'] = exc.code
        try:
            await self.broker.call('health')
            result['broker']['running'] = True
            if pin is not None:
                reply = await self.broker.call('status', profile=pin.profile)
                if self._current(selected=desktop or active is None) != pin:     # setup state, not account data
                    raise CalendarBlocked('profile_changed')
                result['connection'] = {k: reply.get(k) for k in
                                        ('state', 'connection', 'last_test', 'last_error', 'last_error_message')}
        except CalendarBlocked as exc:
            result['broker']['code'] = exc.code
            result['broker']['message'] = exc.message
        result['ready'] = bool(access['allowed'] and result['connection']
                               and result['connection']['state'] == 'connected')
        return result

    async def connect(self, profile: str, connection: str, *, desktop=False) -> dict:
        pin = self._pin(profile, selected=desktop)
        try:
            broker_mod.connection_url(connection)
        except broker_mod.BrokerError:
            raise CalendarBlocked('invalid_connection') from None
        try:
            reply, _ = await self._guarded(pin, 'connect', connection=connection, desktop=desktop)
        except CalendarBlocked as exc:
            if exc.code == 'profile_changed' or exc.code in ACCESS_CODES:
                await self._quiet('cancel_connect', profile=pin.profile)
            raise
        url = reply.get('authorize_url')
        try:
            broker_mod.check_url(url)
        except broker_mod.BrokerError:
            await self._quiet('cancel_connect', profile=pin.profile)
            raise CalendarBlocked('destination_refused') from None
        return {'profile': pin.profile, 'state': 'connecting', 'authorize_url': url,
                'expires_in': reply.get('expires_in')}

    async def cancel_connect(self, profile: str) -> dict:
        """Allowed in Kids and with Calendar access locked: removing access always fails safe."""
        pin = self._pin(profile, selected=True)
        await self.broker.call('cancel_connect', profile=pin.profile)
        return {'profile': pin.profile, 'cancelled': True}

    async def test(self, profile: str, *, desktop=False) -> dict:
        pin = self._pin(profile, selected=desktop)
        reply, _ = await self._guarded(pin, 'test', desktop=desktop)
        return {'profile': pin.profile, 'test': 'working', 'connection': reply.get('connection')}

    async def disconnect(self, profile: str) -> dict:
        """Allowed in Kids and with Calendar access locked: removing access always fails safe."""
        pin = self._pin(profile, selected=True)
        if self._last and self._last[0].profile == pin.profile:
            self._last = None
        reply = await self.broker.call('disconnect', profile=pin.profile)
        return {'profile': pin.profile, 'state': 'disconnected', 'revoked': bool(reply.get('revoked'))}

    async def _quiet(self, op, **fields):
        try:
            await self.broker.call(op, **fields)
        except CalendarBlocked:
            pass

    # -- reads
    async def query(self, range_name: str, *, profile: str | None = None, speak: bool = False, desktop=False) -> dict:
        if range_name not in RANGES:
            raise CalendarBlocked('invalid_range')
        pin = self._pin(profile, selected=desktop)
        reply, grant = await self._guarded(pin, 'events', range=range_name, desktop=desktop)
        result = _display(reply, pin.profile, pin.name)
        self._still(pin, grant, desktop=desktop)
        self._last = (pin, grant, result)
        if speak and self.speak is not None:
            await self._say(pin, grant, speech_text(result))
        return result

    def last(self, profile: str, *, desktop=False) -> dict | None:
        """The newest result, only while its profile (same selection) and Calendar access last."""
        if self._last is None:
            return None
        pin, grant, result = self._last
        if pin.profile != profile:
            return None
        try:
            self._still(pin, grant, desktop=desktop)
        except CalendarBlocked:
            self._last = None
            return None
        return result

    async def _say(self, pin: Pin, grant, text: str) -> None:
        self._speaking += 1
        try:
            self._still(pin, grant)
            await self.speak(text)
        except CalendarBlocked:
            pass
        except Exception:          # noqa: BLE001 - optional output only
            log.warning('local calendar speech failed')
        finally:
            self._speaking -= 1
            self._speaking_until = time.monotonic() + self.mic_tail_s

    def mic_hold(self) -> bool:
        """True while local speech plays (and a tail after): Cammy's mic must not carry it to Gemini."""
        return self._speaking > 0 or time.monotonic() < self._speaking_until

    # -- Brain local routing
    async def route(self, text: str, *, speak: bool = False, desktop=False) -> dict | None:
        intent = parse_intent(text)
        if intent is None:
            return None
        pin = self._current(selected=desktop)
        outcome = {'id': secrets.token_hex(16), 'profile_id': pin.profile if pin else None,
                   'capability': 'calendar', 'range': intent.range}
        try:
            if intent.kind == 'blocked':
                raise CalendarBlocked(intent.reason)
            await self.query(intent.range, speak=speak, desktop=desktop)
        except CalendarBlocked as exc:
            return {**outcome, 'status': 'blocked', 'code': exc.code, 'message': exc.message}
        return {**outcome, 'status': 'local',
                'message': 'Shown in Cammy Control Center on this PC' + (' and read aloud there' if speak and self.speak else '') + '.'}

    def voice_context(self):
        """Bind locally heard audio to the current Calendar access grant before transcription."""
        try:
            pin = self._pin()
            return pin, self._grant(pin.profile)
        except CalendarBlocked:
            return None

    async def voice_request(self, text: str, context) -> str | None:
        addressed = re.match(r'^\s*(?:hey[\s,]+)?(?:cammy|cami|tammy)\b[\s,.!:]+(.+)', text, re.I)
        if addressed is None or parse_intent(addressed[1]) is None:
            return None
        try:
            self._still(*context)
        except CalendarBlocked:
            return None
        result = await self.route(addressed[1])
        if result and result['status'] == 'local':
            return 'I put your schedule in the Calendar tab.'
        return 'I could not check your schedule. Check the Calendar tab.'


def authorize_url_ok(url: str) -> bool:
    try:
        broker_mod.check_url(url)
    except broker_mod.BrokerError:
        return False
    return urllib.parse.urlsplit(url).scheme == 'https'
