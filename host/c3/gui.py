#!/usr/bin/env python3
"""Cammy Control Center window: high-level status at a glance (GTK4 + libadwaita).

    python3 host/c3/gui.py            (system Python: it has GTK; the venv does not need it)

Every second it reads C3's local API (127.0.0.1:8792, token in
private/c3/api-token); every 10 s it also asks Cammy's portal for its state, so the window
still says whether Cammy is reachable when C3 is not running. It never takes a lease and
sends chat and voice controls through C3's authenticated API. The Brain channel
also reads resident profile state and history while its tab is open.
The Agent and Skills channels (c3/instructions_panel.py) edit the active profile's
Gemini Live Markdown; below about 520 px of chat width the channel tabs become a dropdown.

    --demo NAME     fake status (offline, asleep, listening, talking, connecting)
    --snapshot PNG  render one frame to a PNG and exit (for checks)
    --new-window    open a separate window without closing an existing editor
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'host'))
from c3_client import C3Client, C3Error  # noqa: E402
from c3.provider_panel import ProviderPanel  # noqa: E402
from c3.os_panel import OsPanel  # noqa: E402
try:
    from c3.calendar_panel import CalendarPanel  # noqa: E402
except ImportError:      # the calendar broker module is missing from this install
    CalendarPanel = None
try:
    from c3.look_back_panel import LookBackPanel  # noqa: E402
except ImportError:          # the viewer is optional; the rest of the window works without it
    LookBackPanel = None
try:
    from c3.instructions_panel import AgentPanel, InstructionsStore, SkillsPanel  # noqa: E402
except ImportError:          # optional like Look back
    InstructionsStore = None
try:
    from c3.family_panel import FamilyPanel  # noqa: E402
except ImportError:          # optional like Look back
    FamilyPanel = None
from c3.discover import DEFAULT as PORTAL_DEFAULT, cached as portal_cached  # noqa: E402


def portal_url():
    """C3's last known address for Cammy (it moves with DHCP; c3/discover.py)."""
    return portal_cached() or PORTAL_DEFAULT


SERVICE = 'cammy-c3.service'   # the user service that holds Cammy's connection (Start/Stop button)
BRAIN_PATH = '/v1/brain'
BRAIN_PROVIDERS = {'codex': 'Codex', 'claude': 'Claude'}
CHANNEL_TABS_MIN = 710   # sp of chat column below which channel tabs become a dropdown

CSS = b"""
.dot { min-width: 14px; min-height: 14px; border-radius: 7px; }
.dot.green { background: #2ec27e; } .dot.blue { background: #3584e4; }
.dot.amber { background: #e5a50a; } .dot.red { background: #e01b24; } .dot.grey { background: #9a9996; }
.headline { font-size: 20pt; font-weight: 700; }
.pill { padding: 2px 10px; border-radius: 9px; font-weight: 600; font-size: 9pt; }
.pill.green { background: alpha(#2ec27e, .18); color: @success_color; }
.pill.blue { background: alpha(#3584e4, .18); color: @accent_color; }
.pill.amber { background: alpha(#e5a50a, .2); color: @warning_color; }
.pill.red { background: alpha(#e01b24, .18); color: @error_color; }
.pill.grey { background: alpha(#9a9996, .2); }
.chat-panel { background: @window_bg_color; }
.message { border-radius: 12px; padding: 9px 12px; background: alpha(@window_fg_color, .06); }
.message.mine { background: alpha(@accent_color, .14); }
.message.event { background: transparent; padding: 3px 6px; }
.byline { font-size: 9pt; font-weight: 650; }
.clock { font-size: 9pt; opacity: .68; }
.composer { padding: 10px 14px; border-top: 1px solid alpha(@window_fg_color, .10); }
.channel-tabs > button { min-width: 0; padding-left: 12px; padding-right: 12px; }
.channel-tabs > button > label { min-width: 0; }
.mode-summary { padding: 6px 14px; font-size: 10pt; }

"""


def _get(url, token, timeout=2.0):
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token} if token else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def systemctl(*args, timeout=30):
    """Run systemctl --user on the C3 service: (returncode, first line of output)."""
    try:
        out = subprocess.run(['systemctl', '--user', *args, SERVICE], capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return out.returncode, ((out.stdout or out.stderr).strip().splitlines() or [''])[0]


def service_button(state):
    """(label, css class, sensitive, tooltip) for the Start/Stop button from systemctl is-active."""
    if state == 'active':
        return 'Stop', 'destructive-action', True, 'Stop C3: Cammy disconnects (camera, mic and voice)'
    if state in ('activating', 'reloading'):
        return 'Starting…', '', False, 'C3 is starting'
    if state == 'deactivating':
        return 'Stopping…', '', False, 'C3 is stopping'
    if state in ('inactive', 'failed'):
        return 'Start', 'suggested-action', True, 'Start C3: Cammy connects to this PC'
    return 'Start', 'suggested-action', False, 'Cannot read the C3 service state'


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ''


class Source:
    """Fetches status off the GTK thread. snapshot() -> (status or None, portal or None, entries, error)."""

    def __init__(self, demo=None):
        self.demo = demo
        self.client = C3Client(timeout=3, desktop_token_file=ROOT / 'private/c3/desktop-token')
        self.lock = threading.Lock()
        self.data = (None, None, [], 'starting')
        self.portal_at = 0.0
        self.portal = None
        self.service_at = 0.0
        self.service = 'unknown'    # systemctl is-active of SERVICE, read every 2 s

    def start(self):
        if self.demo:
            self.data = demo_data(self.demo)
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return self.data

    def _loop(self):
        entries = []
        while True:
            status, error = None, ''
            try:
                status = self.client.status()
                try:
                    entries = self.client.transcript(80).get('entries', [])
                except C3Error as exc:
                    if exc.code != 403:
                        raise
                    entries = []         # family Kids mode: the transcript is closed
            except (C3Error, OSError) as exc:
                error = str(getattr(exc, 'reason', exc))
            except ValueError:
                error = 'bad reply from C3'
            if time.monotonic() - self.portal_at > 10:
                self.portal_at = time.monotonic()
                try:
                    cfg = json.loads((ROOT / 'private/host/config.json').read_text())
                    self.portal = _get(portal_url() + '/v1/state', _read(cfg['token_file']), timeout=3)
                except (OSError, ValueError, KeyError):
                    self.portal = None
            if time.monotonic() - self.service_at > 2:
                self.service_at = time.monotonic()
                self.service = systemctl('is-active', timeout=5)[1] or 'unknown'
            with self.lock:
                self.data = (status, self.portal, entries, error)
            time.sleep(1)


def headline(status, portal):
    """(colour, title, subtitle) for the big line at the top."""
    if not status:
        if portal:
            return 'grey', 'Control Center unavailable', 'The camera is reachable. Check the local service connection.'
        return 'red', 'Control Center unavailable', 'Waiting for the local service and camera.'
    body, voice = status.get('body') or {}, status.get('voice') or {}
    if not body.get('up'):
        err = body.get('last_error') or 'connecting to Cammy'
        if 'power cycle' in err:
            if portal and portal.get('mode') != 'cammyos':
                return 'amber', 'Camera recovery needed', 'The camera is online. Open the OS tab to check its boot.'
            return 'red', 'Camera recovery needed', 'Open the OS tab to check the camera before restarting.'
        return 'amber', 'Connecting to Cammy…', 'Waiting for the camera connection.'
    family = status.get('family') or {}
    if family.get('deployment') == 'family':
        return 'green', 'Cammy connected', 'Camera and Control Center are linked.'
    state = voice.get('state')
    if state == 'connecting':
        return 'amber', 'Waking up…', 'Opening the Gemini connection'
    if state == 'asleep':
        return 'blue', 'Asleep', 'Listening locally. Speech wakes Gemini.'
    if (status.get('listener') or {}).get('speaking') and not (status.get('speaker') or {}).get('active'):
        return 'green', 'Listening', 'Cammy’s microphone detected speech'
    if (status.get('speaker') or {}).get('active'):
        return 'green', 'Cammy is talking', 'Interruptions enabled' if status.get('duplex') == 'full' else ('Listening for an interruption once echo locks' if status.get('duplex') == 'auto' else 'Half duplex · listening resumes after playback')
    return 'green', 'Awake', 'Listening for speech'


def history_row(history, look_backs=0):
    """Camera memory: frames kept on this computer's disk, in the selected brain profile's
    workspace; sent to Gemini only when Cammy looks back."""
    if history.get('error'):
        return ('Camera memory', history['error'], 'error', 'red')
    frames = int(history.get('frames') or 0)
    used, cap = history.get('bytes') or 0, history.get('capacity_bytes') or 0
    span_s = history.get('span_s')
    oldest, newest = history.get('oldest_time', history.get('oldest')), history.get('newest_time', history.get('newest'))
    if not isinstance(span_s, (int, float)) and isinstance(oldest, (int, float)) and isinstance(newest, (int, float)):
        span_s = newest - oldest
    span = ''
    if frames and isinstance(span_s, (int, float)):
        span = ' · %s' % ('%.0f min' % (span_s / 60) if span_s >= 60 else '%.0f s' % max(0, span_s))
    profile = history.get('profile_name') or history.get('profile')
    if isinstance(profile, dict):
        profile = profile.get('name') or profile.get('id')
    if not profile:
        return ('Camera memory', 'off · no brain profile selected · %d look-backs' % (look_backs or 0), 'off', 'grey')
    detail = '%s · %d frames%s · %.0f of %.0f MB on disk · %d look-backs' % (
        profile, frames, span, used / 1e6, cap / 1e6, look_backs or 0)
    fresh = history.get('fresh')
    if fresh is None and isinstance(newest, (int, float)):
        fresh = abs((time.time() if newest > 1e8 else time.monotonic()) - newest) < 5
    pill = 'paused' if history.get('recording_allowed') is False else 'recording' if frames and fresh else 'kept' if frames else 'empty'
    return ('Camera memory', detail, pill, {'recording': 'green', 'kept': 'blue'}.get(pill, 'grey'))


def rows(status, portal):
    """[(title, subtitle, pill text, pill colour)] for the status list."""
    out = []
    p = portal or {}
    cam = ('CammyOS %s · %s' % (p.get('version', '?'), p.get('face_pack', '?'))
           if portal else 'no reply from %s' % portal_url())
    if not status:
        out.append(('Cammy', cam, 'reachable' if portal else 'offline', 'green' if portal else 'red'))
        return out
    body = status.get('body') or {}
    family = (status.get('family') or {}).get('deployment') == 'family'
    link = 'lease %s · preview %s · mic %s' % (body.get('lease'), body.get('preview'), body.get('mic'))
    out.append(('Cammy', link + ('  —  ' + cam if portal else ''), 'connected' if body.get('up') else 'reconnecting',
                'green' if body.get('up') else 'amber'))
    if battery := body.get('battery'):
        level = battery.get('level')
        warning = battery.get('warning')
        if battery.get('external_power'):
            out.append(('Battery', 'USB power detected', 'plugged in', 'green'))
        elif level is None:
            out.append(('Battery', 'Stock battery reading unavailable', 'unknown', 'grey'))
        else:
            out.append(('Battery', warning or 'Running on battery', 'level %d' % level,
                        'amber' if warning else 'grey'))
    if hold := body.get('media_hold'):
        out.append(('Media preparation', hold, 'waiting', 'amber'))
    if body.get('capture_muted'):
        out.append(('Capture', 'Microphone and Look Back paused', 'muted', 'amber'))
    voice = status.get('voice') or {}
    vs = voice.get('state', '?')
    if not family:
        reply = voice.get('reply')
        silent = reply in ('timed-out', 'completed-without-audio', 'connection-ended')
        detail = '%d connections · %d wakes · resume %s' % (
            voice.get('connections', 0), voice.get('wakes', 0), 'ready' if voice.get('handle') else 'fresh next time')
        if reply:
            detail += ' · last reply ' + reply.replace('-', ' ')
        colour = {'awake': 'green', 'asleep': 'blue', 'connecting': 'amber',
                  'waiting-camera': 'amber', 'backoff': 'amber', 'failed': 'red'}.get(vs, 'grey')
        out.append(('Voice (Gemini Live)', detail, 'no audio reply' if silent else vs,
                    'amber' if silent and vs != 'failed' else colour))
        if health := status.get('voice_health'):
            reasons = ' · '.join(status.get('voice_health_reasons') or [])
            out.append(('Voice health', reasons or 'Voice checks passed', health,
                        {'ok': 'green', 'degraded': 'amber', 'failing': 'red'}.get(health, 'grey')))
    echo, duplex = status.get('echo') or {}, status.get('duplex', 'half')
    if echo and echo.get('converged') and duplex != 'half':
        hear, colour = 'full duplex · echo cut %.0f dB · delay %.0f ms' % (echo.get('erle_db') or 0, echo.get('delay_ms') or 0), 'green'
    elif echo:
        hear, colour = '%s duplex · echo canceller %s (%s)' % (duplex, 'locked' if echo.get('converged') else 'learning', echo.get('backend')), 'blue'
    else:
        hear, colour = 'half duplex · no echo canceller', 'grey'
    listener = status.get('listener') or {}
    out.append(('Hearing', hear, 'speech' if listener.get('speaking') else 'quiet', 'green' if listener.get('speaking') else colour))
    speaker = status.get('speaker') or {}
    refusal = speaker.get('failure_reason')
    speaker_detail = '%.0f s queued this run' % (speaker.get('seconds') or 0)
    if refusal:
        speaker_detail += ' · ' + refusal.replace('-', ' ')
    speaker_state = ('failing' if speaker.get('state') == 'failing' else 'talking' if speaker.get('active')
                     else 'refused' if refusal else 'quiet')
    out.append(('Speaker', speaker_detail, speaker_state,
                {'failing': 'red', 'talking': 'green', 'refused': 'amber'}.get(speaker_state, 'grey')))
    moves = status.get('moves')
    if moves:
        if moves.get('sweeping'):
            pill, colour = 'looking around', 'blue'
        elif moves.get('following'):
            pill, colour = 'following', 'green'
        else:
            pill, colour = 'still', 'grey'
        screen = 'live camera view' if moves.get('camera_view') or moves.get('following') else 'face'
        out.append(('Camera', 'face follow %s%s · screen shows the %s · %d sweeps' % (
            'on' if moves.get('following') else 'off', '' if moves.get('reported') else ' (assumed)',
            screen, moves.get('sweeps', 0)), pill, colour))
    history = (status.get('eyes') or {}).get('history')
    if isinstance(history, dict):
        out.append(history_row(history, (status.get('voice') or {}).get('look_backs', 0)))
    brain = status.get('brain') or {}
    if brain.get('backend') == 'resident':
        profile = next((p for p in brain.get('profiles', []) if p['id'] == brain.get('active_profile')), None)
        detail = profile_label(profile) if profile else 'No resident profile selected'
    elif brain.get('thread'):
        detail = 'T3 thread · %s' % brain['thread']
    else:
        detail = 'No brain configured'
    if brain.get('pending'):
        waiting = 'Resident brain requests' if brain.get('backend') == 'resident' else 'Waiting for a reply from T3'
        out.append(('Brain requests', waiting, '%d pending' % brain['pending'], 'amber'))
    configured = bool(brain.get('thread') or brain.get('active_profile'))
    out.append(('Brain', detail, 'configured' if configured else 'off', 'blue' if configured else 'grey'))
    sent = (voice.get('audio_s_sent') or 0) / 60
    up = status.get('uptime_s', 0)
    out.append(('Usage', '%.1f min sent · %d utterances · %d interruptions · up %dh %02dm' % (
        sent, voice.get('utterances', 0), voice.get('barge_ins', 0), up // 3600, up // 60 % 60), 'this run', 'grey'))
    restarts = sum((status.get('restarts') or {}).values())
    err = body.get('last_error') or ''
    if restarts or err or voice.get('errors'):
        out.append(('Problems', (err or 'none recorded')[:120], '%d restart%s' % (restarts, '' if restarts == 1 else 's'), 'amber' if restarts else 'grey'))
    return out


def column(spacing=0, margin=0):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, margin_top=margin,
                   margin_bottom=margin, margin_start=margin, margin_end=margin)


def chat_line(entry):
    who, text = entry.get('who', ''), entry.get('text', '')
    if who == 'system' and text.startswith('turn: '):
        who, text = 'input', text[6:]
    names = {'input': 'Text input', 'adam': 'Adam', 'cammy': 'Cammy', 'brain': 'Brain', 'system': 'Control Center',
             'tool': 'Tool', 'camera': 'Camera'}
    return who, names.get(who, who), text


def profile_label(profile):
    return '%s · %s' % (profile['name'], BRAIN_PROVIDERS.get(profile['provider'], profile['provider']))


def channel_menu(stack):
    """A dropdown of the stack's pages, kept in step with it; hidden until the column is narrow."""
    names = []
    menu = Gtk.DropDown(model=Gtk.StringList.new([]), visible=False, margin_top=8,
                        margin_start=14, margin_end=14, tooltip_text='Choose a channel')
    menu.update_property([Gtk.AccessibleProperty.LABEL], ['Channel'])
    syncing = [False]

    def rebuild(*_):
        pages = [stack.get_page(child) for child in _children(stack)]
        names[:] = [page.get_name() for page in pages]
        model = menu.get_model()
        syncing[0] = True
        model.splice(0, model.get_n_items(), [page.get_title() or page.get_name() for page in pages])
        syncing[0] = False
        follow()

    def follow(*_):
        name = stack.get_visible_child_name()
        syncing[0] = True
        menu.set_selected(names.index(name) if name in names else Gtk.INVALID_LIST_POSITION)
        syncing[0] = False

    def chosen(*_):
        index = menu.get_selected()
        if not syncing[0] and 0 <= index < len(names):
            stack.set_visible_child_name(names[index])

    stack.get_pages().connect('items-changed', rebuild)
    stack.connect('notify::visible-child', follow)
    menu.connect('notify::selected', chosen)
    rebuild()
    return menu


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def scroll_to_bottom(scroll):
    def after_layout(*_):
        def bottom():
            adj = scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE
        GLib.idle_add(bottom)
        return GLib.SOURCE_REMOVE
    scroll.add_tick_callback(after_layout)


class BrainPane(Gtk.Box):
    """Resident brain chat. GTK owns view state; all C3 requests run on workers."""

    def __init__(self, source, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class('chat-panel')
        self.source, self.window = source, window
        self.state = {}
        self.profiles = []
        self.entries = []
        self.drafts = {}
        self.available = False
        self.polling = False
        self.next_poll = 0
        self.generation = 0
        self.operation = ''
        self.awaiting_state = False
        self.poll_error = ''
        self.action_error = ''
        self.syncing_profile = False

        controls = column(6, 12)
        profile_row = Gtk.Box(spacing=8)
        self.profile = Gtk.DropDown(hexpand=True, model=Gtk.StringList.new(['Select a profile']))
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', lambda _, item: item.set_child(Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=34)))
        factory.connect('bind', lambda _, item: item.get_child().set_label(item.get_item().get_string()))
        self.profile.set_factory(factory)
        self.profile.connect('notify::selected', self._select)
        self.profile.update_property([Gtk.AccessibleProperty.LABEL], ['Brain profile'])
        self.add_profile = Gtk.Button(label='New profile')
        self.add_profile.connect('clicked', self._new_profile)
        profile_row.append(self.profile)
        profile_row.append(self.add_profile)
        controls.append(profile_row)
        state_row = Gtk.Box(spacing=8)
        self.indicator = Gtk.Spinner()
        self.indicator.set_visible(False)
        self.state_label = Gtk.Label(label='Checking Brain…', xalign=0, wrap=True)
        self.state_label.add_css_class('dim-label')
        state_row.append(self.indicator)
        state_row.append(self.state_label)
        controls.append(state_row)
        self.append(controls)

        self.messages = column(8, 14)
        self.history = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.history.set_child(self.messages)
        self.append(self.history)
        self.empty = Gtk.Label(wrap=True, vexpand=True, justify=Gtk.Justification.CENTER,
                               margin_start=14, margin_end=14)
        self.empty.add_css_class('dim-label')
        self.append(self.empty)

        compose = column(5)
        compose.add_css_class('composer')
        self.feedback = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, wrap=True, lines=2)
        self.feedback.add_css_class('error')
        compose.append(self.feedback)
        # Requests answered on this PC (calendar): a note only; the answer is in the Calendar tab.
        self.local_note = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.local_note.add_css_class('dim-label')
        compose.append(self.local_note)
        line = Gtk.Box(spacing=8)
        self.entry = Gtk.Entry(hexpand=True, width_chars=1, max_length=4000, placeholder_text='Message Brain…')
        self.entry.connect('changed', lambda *_: self._update_controls())
        self.entry.connect('activate', self._send)
        self.send = Gtk.Button(label='Send')
        self.send.add_css_class('suggested-action')
        self.send.connect('clicked', self._send)
        self.cancel = Gtk.Button(label='Cancel', tooltip_text='Cancel the current Brain request')
        self.cancel.connect('clicked', lambda *_: self._post('/cancel', {}, 'Cancelling…'))
        line.append(self.entry)
        line.append(self.send)
        line.append(self.cancel)
        compose.append(line)
        self.append(compose)
        self._update_controls()

    def refresh(self, immediate=False):
        if immediate:
            self.next_poll = 0
        if (not self.get_mapped() or self.window.closed or self.polling or self.operation
                or time.monotonic() < self.next_poll):
            return
        if self.source.demo:
            self._update_controls()
            return
        self.polling = True
        generation = self.generation

        def run():
            state, entries, error = {}, [], ''
            try:
                reply = self.source.client.request('GET', BRAIN_PATH)
                if not isinstance(reply, dict):
                    raise ValueError('Unexpected Brain status reply')
                state = reply
            except C3Error as exc:
                if exc.code not in (404, 405, 501):
                    error = str(exc)
            except (ValueError, OSError) as exc:
                error = str(exc)
            else:
                if state.get('backend') == 'resident' and (active := state.get('active_profile')):
                    try:
                        reply = self.source.client.request('GET', BRAIN_PATH + '/history?profile='
                                                           + quote(active, safe=''))
                        if not isinstance(reply, dict) or not isinstance(reply.get('entries'), list):
                            raise ValueError('Unexpected Brain history reply')
                        entries = reply['entries']
                    except (C3Error, ValueError, OSError) as exc:
                        entries, error = None, str(exc)
            GLib.idle_add(self._polled, generation, state, entries, error)

        threading.Thread(target=run, daemon=True).start()

    def _polled(self, generation, state, entries, error):
        self.polling = False
        if self.window.closed:
            return GLib.SOURCE_REMOVE
        if generation != self.generation:
            self.refresh()
            return GLib.SOURCE_REMOVE
        self.awaiting_state = False
        self.available = state.get('backend') == 'resident'
        self.poll_error = error
        if self.available or not error:
            self._apply_state(state, entries)
        interval = (1 if state.get('pending') else 3) if self.available else 5
        self.next_poll = time.monotonic() + interval
        self._update_controls()
        return GLib.SOURCE_REMOVE

    def _apply_state(self, state, entries):
        before, active = self.state.get('active_profile'), state.get('active_profile')
        self.state = state
        if before != active:
            if before:
                self.drafts[before] = self.entry.get_text()
            self.entry.set_text(self.drafts.get(active, ''))
        profiles = state.get('profiles', [])
        self.syncing_profile = True
        if self.profiles != profiles:
            self.profiles = profiles
            self.profile.set_model(Gtk.StringList.new(['Select a profile'] + [
                '%s · %s' % (profile_label(p), p['id']) for p in profiles]))
        self._sync_profile()
        self.syncing_profile = False
        if entries is not None or active != before:
            self._show_history(entries or [], active != before)

    def _sync_profile(self):
        active = self.state.get('active_profile')
        selected = next((i for i, p in enumerate(self.profiles, 1) if p['id'] == active), 0)
        self.profile.set_selected(selected)
        self.profile.set_tooltip_text('%s · %s' % (profile_label(self.profiles[selected - 1]), active)
                                      if selected else None)

    def _show_history(self, entries, reset=False):
        if self.entries == entries and not reset:
            return
        adj = self.history.get_vadjustment()
        at_end = reset or adj.get_upper() - adj.get_page_size() - adj.get_value() < 8
        previous = [] if reset else self.entries
        if reset:
            while child := self.messages.get_first_child():
                self.messages.remove(child)
        else:
            while previous and (not entries or previous[0].get('id') != entries[0].get('id')):
                self.messages.remove(self.messages.get_first_child())
                previous = previous[1:]
        child = self.messages.get_first_child()
        for old, new in zip(previous, entries):
            following = child.get_next_sibling()
            if old != new:
                replacement = self._message(new)
                self.messages.insert_child_after(replacement, child)
                self.messages.remove(child)
            child = following
        while child:
            following = child.get_next_sibling()
            self.messages.remove(child)
            child = following
        for entry in entries[len(previous):]:
            self.messages.append(self._message(entry))
        self.entries = entries
        if entries and at_end:
            scroll_to_bottom(self.history)

    @staticmethod
    def _message(entry):
        role = entry.get('role', '')
        name = {'user': 'You', 'assistant': 'Brain', 'system': 'Brain'}.get(role, role.capitalize())
        state = entry.get('status') or ''
        bubble = column(3)
        bubble.set_halign(Gtk.Align.END if role == 'user' else Gtk.Align.START)
        bubble.add_css_class('message')
        if role == 'user':
            bubble.add_css_class('mine')
        title = Gtk.Label(label=name, xalign=0)
        title.add_css_class('byline')
        bubble.append(title)
        if state and state not in ('complete', 'completed', 'done', 'ok', 'sent'):
            label = Gtk.Label(label=state.replace('_', ' ').capitalize(), xalign=0, wrap=True)
            label.add_css_class('error' if state in ('error', 'failed') else 'caption')
            bubble.append(label)
        if entry.get('text'):
            bubble.append(Gtk.Label(label=entry['text'], xalign=0, selectable=True, wrap=True,
                                    wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52))
        return bubble

    def _update_controls(self):
        status = self.source.snapshot()[0]
        reported = (status or {}).get('brain') or {}
        active = self.state.get('active_profile')
        pending = self.state.get('pending', 0)
        if self.operation:
            label = self.operation
        elif self.awaiting_state:
            label = 'Updating Brain…'
        elif self.available:
            if pending:
                label = 'Running · %d pending' % pending
            else:
                label = 'Ready' if active else 'Select a Brain profile'
        elif self.poll_error or not status:
            label = 'Brain unavailable'
        elif reported.get('thread'):
            label = 'T3 thread · %s' % reported['thread']
        elif self.next_poll or self.source.demo:
            label = 'Resident Brain unavailable'
        else:
            label = 'Checking Brain…'
        self.state_label.set_label(label)
        working = bool(self.operation or self.awaiting_state or (self.available and pending))
        self.indicator.set_visible(working)
        self.indicator.set_spinning(working)
        self.history.set_visible(bool(self.entries))
        self.empty.set_visible(not self.entries)
        self.empty.set_label('Messages for this profile will appear here.' if active
                             else 'Create or select a profile to chat with Brain.' if self.available
                             else 'T3 replies appear in Conversation.' if reported.get('thread')
                             else 'Brain chat is available when C3 runs a resident Brain.')
        error = self.action_error or self.poll_error
        self.feedback.set_label(error)
        self.feedback.set_visible(bool(error))
        self.feedback.set_tooltip_text(error or None)
        enabled = self.available and not self.operation and not self.awaiting_state and not self.source.demo
        self.profile.set_sensitive(enabled and bool(self.profiles) and not pending)
        self.add_profile.set_sensitive(enabled)
        self.entry.set_editable(bool(active) and not self.operation and not self.awaiting_state)
        self.send.set_sensitive(enabled and bool(active) and not pending and bool(self.entry.get_text().strip()))
        self.cancel.set_sensitive(enabled and bool(pending))

    def _select(self, dropdown, *_):
        if self.syncing_profile or not dropdown.is_sensitive():
            return
        selected = dropdown.get_selected()
        self.syncing_profile = True
        self._sync_profile()
        self.syncing_profile = False
        if 0 < selected <= len(self.profiles):
            profile_id = self.profiles[selected - 1]['id']
            if profile_id != self.state.get('active_profile'):
                self._post('/select', {'id': profile_id}, 'Selecting profile…')

    def _send(self, *_):
        if self.send.get_sensitive():
            self._post('/message', {'text': self.entry.get_text().strip()}, 'Sending…')

    def _new_profile(self, *_):
        dialog = Adw.MessageDialog(transient_for=self.window, modal=True, hide_on_close=True,
                                   heading='New Brain profile')
        form = column(8)
        name = Gtk.Entry(placeholder_text='Profile name', max_length=128)
        profile_id = Gtk.Entry(placeholder_text='Profile ID (optional)', max_length=64)
        profile_id.set_tooltip_text('An ID is generated from the name unless you enter one.')
        for field, label in ((name, 'Profile name'), (profile_id, 'Profile ID')):
            field.update_property([Gtk.AccessibleProperty.LABEL], [label])
            form.append(field)
        providers = list(BRAIN_PROVIDERS)
        provider = Adw.ComboRow(title='Provider', model=Gtk.StringList.new(list(BRAIN_PROVIDERS.values())))
        group = Adw.PreferencesGroup()
        group.add(provider)
        form.append(group)
        instructions = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                    top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
        instructions.update_property([Gtk.AccessibleProperty.LABEL], ['Instructions (optional)'])
        form.append(Gtk.Label(label='Instructions (optional)', xalign=0))
        scroll = Gtk.ScrolledWindow(min_content_height=68, has_frame=True, child=instructions)
        form.append(scroll)
        error_label = Gtk.Label(xalign=0, wrap=True, visible=False)
        error_label.add_css_class('error')
        form.append(error_label)
        dialog.set_extra_child(form)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('create', 'Create')
        dialog.set_response_appearance('create', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('create')
        dialog.set_close_response('cancel')

        def changed(*_):
            provided = profile_id.get_text().strip()
            profile_name = name.get_text().strip()
            slug = provided or re.sub('[^a-z0-9]+', '-', profile_name.lower()).strip('-')[:64].rstrip('-')
            valid = bool(re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', slug))
            dialog.set_response_enabled('create', bool(profile_name) and valid)
            invalid = not valid and bool(provided or profile_name)
            error_label.set_label('Enter an ID with lowercase letters, digits and hyphens.' if invalid else '')
            error_label.set_visible(invalid)
            return slug

        def responded(_, response):
            if response != 'create':
                dialog.destroy()
                return
            dialog.set_visible(False)
            buf = instructions.get_buffer()
            body = {'id': changed(), 'name': name.get_text().strip(), 'provider': providers[provider.get_selected()]}
            text = buf.get_text(*buf.get_bounds(), False).strip()
            if text:
                body['instructions'] = text

            def created(error):
                if error:
                    error_label.set_label(error)
                    error_label.set_visible(True)
                    dialog.present()
                else:
                    dialog.destroy()

            self._post('/profiles', body, 'Creating profile…', on_done=created)

        name.connect('changed', changed)
        profile_id.connect('changed', changed)
        dialog.connect('response', responded)
        changed()
        dialog.present()

    def _post(self, path, body, label, on_done=None):
        if self.operation or self.awaiting_state or not self.available:
            if on_done:
                GLib.idle_add(on_done, 'Brain is unavailable. Try again when it reconnects.')
            return
        self.operation = label
        self.action_error = ''
        self.generation += 1
        self._update_controls()

        def finish(error, local=None):
            if self.window.closed:
                return GLib.SOURCE_REMOVE
            self.operation = ''
            self.awaiting_state = not error and local is None
            self.action_error = error
            if path == '/message':
                text = ''
                if isinstance(local, dict):
                    text = str(local.get('message') or '')
                    if local.get('capability') == 'calendar':
                        text = ('Calendar: ' + text
                                + (' See the Calendar tab.' if local.get('status') == 'local' else ''))
                self.local_note.set_label(text)
                self.local_note.set_visible(bool(text))
            if not error and path == '/message':
                self.entry.set_text('')
            if on_done:
                on_done(error)
            self._update_controls()
            self.refresh(immediate=True)
            return GLib.SOURCE_REMOVE

        def run():
            local = None
            try:
                reply = self.source.client.request('POST', BRAIN_PATH + path, body)
                local = reply.get('local') if isinstance(reply, dict) else None
            except (C3Error, ValueError, OSError) as exc:
                error = str(exc)
            else:
                error = ''
            GLib.idle_add(finish, error, local)

        threading.Thread(target=run, daemon=True).start()


class Window(Adw.ApplicationWindow):
    def __init__(self, app, source):
        super().__init__(application=app, title='Cammy Control Center')
        self.source = source
        self.closed = False
        self.busy = False
        self.syncing_duplex = False
        self.row_widgets = {}
        self.displayed = []
        self.set_default_size(1140, 680)
        self.set_size_request(320, 390)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title='Cammy Control Center'))
        self.sidebar_button = Gtk.ToggleButton(icon_name='sidebar-show-symbolic', tooltip_text='Show status')
        header.pack_start(self.sidebar_button)
        self.power = Gtk.Button(label='Stop')
        self.power.connect('clicked', self._power_clicked)
        header.pack_end(self.power)
        self.power_busy = False
        self.split = Adw.OverlaySplitView(min_sidebar_width=288, max_sidebar_width=350,
                                          sidebar_width_fraction=0.29)
        self.split.set_sidebar(self._sidebar())
        chat = column()
        chat.add_css_class('chat-panel')
        self.channel = Gtk.Stack(vexpand=True, hhomogeneous=False, vhomogeneous=False)
        self.channel.add_titled(self._conversation(), 'conversation', 'Conversation')
        self.brain = BrainPane(source, self)
        self.channel.add_titled(self.brain, 'brain', 'Brain')
        if not source.demo:
            settings = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            provider_panel = ProviderPanel(source.client)
            for side in ('top', 'bottom', 'start', 'end'):
                provider_panel.set_property('margin-' + side, 14)
            settings.set_child(provider_panel)
            self.channel.add_titled(settings, 'providers', 'Providers')
            if LookBackPanel is not None:
                # Its own requests only; GTK unmaps it while another tab shows. Adding a page
                # never changes the visible page, so nothing here takes focus.
                self.look_back = LookBackPanel(source.client)
                self.channel.add_titled(self.look_back, 'look-back', 'Look back')
            if InstructionsStore is not None:
                # One store: drafts and replies are shared by both tabs. Requests start only
                # when a tab is shown or a button is pressed.
                self.instructions = InstructionsStore(source.client)
                self.channel.add_titled(AgentPanel(self.instructions), 'agent', 'Agent')
                self.channel.add_titled(SkillsPanel(self.instructions), 'skills', 'Skills')
            if FamilyPanel is not None:
                family = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
                family.set_child(FamilyPanel(source.client))
                self.channel.add_titled(family, 'family', 'Family')
            if CalendarPanel is not None:
                calendar = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
                calendar.set_child(CalendarPanel(source.client))
                self.channel.add_titled(calendar, 'calendar', 'Calendar')
            self.channel.add_titled(OsPanel(source), 'os', 'OS')
        self.channel.set_visible_child_name('conversation')
        self.channel.connect('notify::visible-child', lambda *_: self.brain.refresh(immediate=True))
        self.channel_tabs = Gtk.StackSwitcher(stack=self.channel, halign=Gtk.Align.CENTER, margin_top=8)
        self.channel_tabs.get_layout_manager().set_homogeneous(False)   # each tab as wide as its label
        self.channel_tabs.add_css_class('channel-tabs')
        self.channel_menu = channel_menu(self.channel)
        chat.append(self.channel_tabs)
        chat.append(self.channel_menu)
        self.mode_summary = Gtk.Label(xalign=0, wrap=True)
        self.mode_summary.add_css_class('mode-summary')
        self.mode_summary.add_css_class('dim-label')
        chat.append(self.mode_summary)
        chat.append(self.channel)
        chat_bin = Adw.BreakpointBin(child=chat, width_request=300, height_request=300)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: %dsp' % CHANNEL_TABS_MIN))
        compact.add_setter(self.channel_tabs, 'visible', False)
        compact.add_setter(self.channel_menu, 'visible', True)
        chat_bin.add_breakpoint(compact)
        self.split.set_content(chat_bin)
        self.split.bind_property('show-sidebar', self.sidebar_button, 'active',
                                 GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.split)
        self.set_content(view)
        self.sidebar_button.set_visible(False)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 730px'))
        narrow.add_setter(self.split, 'collapsed', True)
        narrow.add_setter(self.sidebar_button, 'visible', True)
        self.add_breakpoint(narrow)
        self.refresh()
        self.timer = GLib.timeout_add(1000, self.refresh)
        self.connect('close-request', self._close)

    def _show_access(self, status):
        family = (status or {}).get('family') or {}
        managed = family.get('deployment') == 'family'
        mode = 'Adult' if family.get('mode') == 'adult' else 'Kids'
        self.mode_summary.set_visible(managed)
        self.mode_summary.set_label(f'Camera in {mode} mode' if managed else '')
        self.composer.set_visible(not managed)
        self.voice_group.set_visible(not managed)
        self.wake.set_visible(not managed)
        self.sweep.set_visible(not managed)

    def _show_power(self):
        label, css, sensitive, tip = service_button('demo' if self.source.demo else self.source.service)
        if self.power_busy:
            css, sensitive = '', False
        self.power.set_label(label)
        self.power.set_tooltip_text(tip)
        self.power.set_sensitive(sensitive)
        for c in ('destructive-action', 'suggested-action'):
            self.power.remove_css_class(c)
        if css:
            self.power.add_css_class(css)

    def _power_clicked(self, *_):
        if self.power_busy or self.source.demo:
            return
        if self.source.service != 'active':
            self._power('start')
            return
        dialog = Adw.AlertDialog(heading='Stop C3?',
                                 body='Cammy disconnects from this PC: no voice, camera view or controls '
                                      'until you press Start.')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('stop', 'Stop')
        dialog.set_response_appearance('stop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda _, response: response == 'stop' and self._power('stop'))
        dialog.present(self)

    def _power(self, verb):
        self.power_busy = True
        self.source.service = 'activating' if verb == 'start' else 'deactivating'
        self._show_power()
        self.feedback.set_label('Starting C3…' if verb == 'start' else 'Stopping C3…')
        self.feedback.set_visible(True)

        def finish(code, text):
            self.power_busy = False
            self.source.service_at = 0.0          # read the real state on the next poll
            self.feedback.set_label('' if code == 0 else f'Could not {verb} C3: {text}')
            self.feedback.set_visible(code != 0)
            self._show_power()
            return False

        def run():
            code, text = systemctl(verb)
            if code == 0:
                self.source.service = systemctl('is-active', timeout=5)[1] or 'unknown'
            GLib.idle_add(finish, code, text)
        threading.Thread(target=run, daemon=True).start()

    def _close(self, *_):
        self.closed = True
        GLib.source_remove(self.timer)
        return False

    def _sidebar(self):
        side = column(12, 16)
        hero = Gtk.Box(spacing=10)
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class('dot')
        self.title = Gtk.Label(xalign=0, wrap=True)
        self.title.add_css_class('headline')
        hero.append(self.dot)
        hero.append(self.title)
        side.append(hero)
        self.sub = Gtk.Label(xalign=0, wrap=True)
        self.sub.add_css_class('dim-label')
        side.append(self.sub)

        stack = Gtk.Stack(vexpand=True, vhomogeneous=False)
        self.status_stack = stack
        side.append(Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER))
        self.activity = Adw.PreferencesGroup(title='Camera and audio')
        self.diagnostics = Adw.PreferencesGroup(title='Diagnostics')
        self.connections = Adw.PreferencesGroup(title='Connection settings')
        for name, title, group in [('activity', 'Activity', self.activity),
                                   ('connections', 'Connections', self.connections)]:
            pane = column(14)
            pane.append(group)
            if name == 'activity':
                self.sweep = Gtk.Button(label='Look around', tooltip_text='Sweep the room with Cammy’s camera')
                self.sweep.connect('clicked', lambda *_: self._request(self.source.client.look_around))
                pane.append(self.sweep)
                pane.append(self.diagnostics)
            else:
                audio = Adw.PreferencesGroup(title='Voice settings')
                self.voice_group = audio
                self.duplex = Adw.ComboRow(title='Duplex', subtitle='How Cammy listens during playback', model=Gtk.StringList.new(
                    ['Half', 'Automatic', 'Full']))
                self.duplex.connect('notify::selected', self._change_duplex)
                audio.add(self.duplex)
                pane.append(audio)
                self.wake = Gtk.Button(label='Wake voice')
                self.wake.connect('clicked', self._voice_action)
                pane.append(self.wake)
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            scroll.set_child(pane)
            stack.add_titled(scroll, name, title)
        side.append(stack)
        return side

    def _conversation(self):
        chat = column()
        chat.add_css_class('chat-panel')
        bar = Gtk.Box(halign=Gtk.Align.END, margin_start=16, margin_end=16, margin_top=8, margin_bottom=8)
        self.events = Gtk.ToggleButton(label='Show events', tooltip_text='Include tool, camera and service activity')
        self.events.connect('toggled', lambda *_: self.refresh())
        bar.append(self.events)
        chat.append(bar)
        self.messages = column(8, 14)
        self.history = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.history.set_child(self.messages)
        chat.append(self.history)
        self.empty = Gtk.Label(wrap=True, vexpand=True, justify=Gtk.Justification.CENTER,
                               margin_start=14, margin_end=14)
        self.empty.add_css_class('dim-label')
        chat.append(self.empty)
        compose = column(5)
        self.composer = compose
        compose.add_css_class('composer')
        self.feedback = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.feedback.add_css_class('caption')
        compose.append(self.feedback)
        line = Gtk.Box(spacing=8)
        self.entry = Gtk.Entry(hexpand=True, width_chars=1, max_length=4000, placeholder_text='Message Cammy…')
        self.send = Gtk.Button(label='Send', tooltip_text='Send a message to Cammy’s voice session')
        self.send.add_css_class('suggested-action')
        self.entry.connect('activate', self._send)
        self.entry.connect('changed', lambda *_: self._availability())
        self.send.connect('clicked', self._send)
        line.append(self.entry)
        line.append(self.send)
        compose.append(line)
        chat.append(compose)
        return chat

    def _availability(self):
        status = self.source.snapshot()[0]
        online = bool(status and (status.get('body') or {}).get('up'))
        managed = ((status or {}).get('family') or {}).get('deployment') == 'family'
        enabled = online and not managed and not self.busy and not self.source.demo
        self.send.set_sensitive(enabled and bool(self.entry.get_text().strip()))
        self.wake.set_sensitive(enabled)
        moves = (status or {}).get('moves') or {}
        self.sweep.set_sensitive(enabled and bool(moves) and not moves.get('sweeping'))
        self.duplex.set_sensitive(enabled and bool((status or {}).get('echo')))

        self.entry.set_editable(not self.busy)
        self.entry.set_placeholder_text('Message Cammy…' if online else 'Cammy is offline')
        self.entry.set_tooltip_text('Enter sends to Gemini Live. Cammy replies aloud.')

    def _send(self, *_):
        text = self.entry.get_text().strip()
        if self.send.get_sensitive() and text:
            self._request(lambda: self.source.client.message(text), clear_draft=True)

    def _voice_action(self, *_):
        voice = (self.source.snapshot()[0] or {}).get('voice') or {}
        path = 'wake' if voice.get('state') == 'asleep' else 'sleep'
        self._request(lambda: self.source.client.request('POST', '/v1/' + path, {}))

    def _change_duplex(self, combo, *_):
        if not combo.is_sensitive() or self.syncing_duplex or combo.get_selected() >= 3:
            return
        mode = ('half', 'auto', 'full')[combo.get_selected()]
        self._request(lambda: self.source.client.request('POST', '/v1/duplex', {'mode': mode}))

    def _request(self, operation, clear_draft=False):
        if self.busy or self.source.demo:
            return
        self.busy = True
        self._availability()
        self.feedback.set_label('Sending…')
        self.feedback.set_visible(True)

        def finish(error):
            self.busy = False
            if clear_draft and not error:
                self.entry.set_text('')
            self.feedback.set_label('Could not confirm the request. ' + error if error else '')
            self.feedback.set_visible(bool(error))
            self._availability()
            return False

        def run():
            try:
                operation()
            except (C3Error, ValueError, OSError) as exc:
                error = str(exc)
            else:
                error = ''
            GLib.idle_add(finish, error)
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _colour(widget, colour):
        for c in ('green', 'blue', 'amber', 'red', 'grey'):
            widget.remove_css_class(c)
        widget.add_css_class(colour)

    def _status_rows(self, status, portal):
        seen = set()
        endpoint = self.source.client.endpoint
        items = [('Local API', endpoint, 'online' if status else 'offline', 'green' if status else 'grey')]
        items.extend(rows(status, portal))
        for name, detail, text, colour in items:
            seen.add(name)
            if name not in self.row_widgets:
                connection = name in ('Local API', 'Cammy', 'Battery', 'Media preparation',
                                      'Voice (Gemini Live)', 'Voice health', 'Brain')
                group = self.connections if connection else self.activity
                if name in ('Usage', 'Problems'):
                    group = self.diagnostics
                row = Adw.ActionRow(title=name, subtitle_lines=2)
                pill = Gtk.Label(valign=Gtk.Align.CENTER)
                pill.add_css_class('pill')
                row.add_suffix(pill)
                group.add(row)
                self.row_widgets[name] = row, pill
            row, pill = self.row_widgets[name]
            row.set_visible(True)
            row.set_subtitle(GLib.markup_escape_text(detail))
            row.set_tooltip_text(detail)
            pill.set_label(text)
            self._colour(pill, colour)
        for name in self.row_widgets.keys() - seen:
            self.row_widgets[name][0].set_visible(False)
        self.activity.set_description(None if status else 'Waiting for Control Center.')
        self.diagnostics.set_visible(bool(status))
        self.syncing_duplex = True
        modes = ['half', 'auto', 'full']
        mode = (status or {}).get('duplex')
        self.duplex.set_selected(modes.index(mode) if mode in modes else Gtk.INVALID_LIST_POSITION)
        self.syncing_duplex = False
        asleep = ((status or {}).get('voice') or {}).get('state') == 'asleep'
        self.wake.set_label('Wake voice' if asleep else 'Put voice to sleep')

    def _bubble(self, entry):
        who, name, text = chat_line(entry)
        mine = who in ('adam', 'input')
        bubble = column(3)
        bubble.set_halign(Gtk.Align.END if mine else Gtk.Align.START)
        bubble.add_css_class('message')
        if mine:
            bubble.add_css_class('mine')
        elif who not in ('brain', 'cammy'):
            bubble.add_css_class('event')
        meta = Gtk.Box(spacing=8)
        label = Gtk.Label(label=name)
        label.add_css_class('byline')
        stamp = Gtk.Label(label=time.strftime('%H:%M', time.localtime(entry.get('t', 0))))
        stamp.add_css_class('clock')
        meta.append(label)
        meta.append(stamp)
        bubble.append(meta)
        bubble.append(Gtk.Label(label=text, xalign=0, selectable=True, wrap=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=52))
        return bubble

    def _history(self, entries, online):
        show_all = self.events.get_active()
        lines = [e for e in entries if show_all or chat_line(e)[0] in ('adam', 'input', 'cammy', 'brain')]
        self.empty.set_visible(not lines)
        self.history.set_visible(bool(lines))
        status = self.source.snapshot()[0] or {}
        family = status.get('family') or {}
        message = 'Your conversation with Cammy will appear here.' if online else 'Waiting for the Control Center service.'
        if family.get('deployment') == 'family':
            message = 'Open Brain to chat by text, or Family to play a local game.'
        self.empty.set_label(message)
        if self.displayed == lines:
            return
        adj = self.history.get_vadjustment()
        at_end = adj.get_upper() - adj.get_page_size() - adj.get_value() < 8
        # Keep existing message widgets and text selections while new turns arrive.
        previous = self.displayed
        while previous and (not lines or previous[0] != lines[0]):
            self.messages.remove(self.messages.get_first_child())
            previous = previous[1:]
        if lines[:len(previous)] != previous:
            while child := self.messages.get_first_child():
                self.messages.remove(child)
            previous = []
        for entry in lines[len(previous):]:
            self.messages.append(self._bubble(entry))
        self.displayed = lines
        if at_end:
            scroll_to_bottom(self.history)

    def refresh(self):
        status, portal, entries, error = self.source.snapshot()
        colour, title, sub = headline(status, portal)
        self._colour(self.dot, colour)
        self.title.set_label(title)
        self.sub.set_label(sub)
        self._show_access(status)
        self.sub.set_tooltip_text(error or sub)
        self._status_rows(status, portal)
        self._history(entries, bool(status))
        self._availability()
        self._show_power()
        self.brain.refresh()
        return True


def demo_data(name):
    now = time.time()
    portal = {'version': '0.7.6', 'mode': 'cammyos', 'face_pack': 'pikachu'}
    if name == 'offline':
        return None, None, [], ''
    base = {'service': 'Cammy Control Center', 'uptime_s': 7380, 'duplex': 'auto',
            'body': {'up': True, 'lease': 'held', 'preview': 'sdp-ready', 'mic': 'open', 'last_error': ''},
            'voice': {'state': 'asleep', 'connections': 6, 'wakes': 4, 'handle': True, 'audio_s_sent': 312.0,
                      'utterances': 41, 'barge_ins': 2, 'errors': 0},
            'listener': {'speaking': False}, 'echo': {'backend': 'webrtc', 'converged': True, 'erle_db': 27.0, 'delay_ms': 420.0},
            'speaker': {'active': False, 'seconds': 388.0}, 'brain': {'thread': 'cammy-brain', 'pending': 0}, 'restarts': {},
            'moves': {'following': True, 'reported': True, 'camera_view': False, 'sweeping': False, 'sweeps': 2},
            'eyes': {'restarts': 0, 'history': {'profile': 'desk', 'profile_name': 'Desk helper',
                                                 'bytes': 182_000_000, 'capacity_bytes': 250_000_000, 'frames': 4400,
                                                 'span_s': 4399.0, 'fresh': True}}}
    entries = [{'t': now - 300, 'who': 'adam', 'text': 'What do you see on my desk?'},
               {'t': now - 298, 'who': 'tool', 'text': 'look -> frame 41210 bytes'},
               {'t': now - 296, 'who': 'cammy', 'text': 'A blue mug, a keyboard and a tangle of USB cables.'}]
    if name == 'connecting':
        base['body'] = {'up': False, 'lease': 'lost', 'preview': 'stopped', 'mic': 'reopening', 'last_error': 'RuntimeError: lease lost: timed out'}
        base['restarts'] = {'body': 1}
    elif name in ('listening', 'talking'):
        base['voice']['state'] = 'awake'
        base['listener']['speaking'] = name == 'listening'
        base['speaker']['active'] = name == 'talking'
    return base, portal, entries, ''


def main():
    ap = argparse.ArgumentParser(description='Cammy Control Center status window')
    ap.add_argument('--demo', choices=('offline', 'asleep', 'listening', 'talking', 'connecting'))
    ap.add_argument('--snapshot', type=Path)
    ap.add_argument('--new-window', action='store_true')
    args = ap.parse_args()
    source = Source(args.demo)
    source.start()
    app = Adw.Application(application_id='io.cammy.ControlCenter',
                          flags=Gio.ApplicationFlags.NON_UNIQUE if args.demo or args.snapshot or args.new_window
                          else Gio.ApplicationFlags.DEFAULT_FLAGS)

    def activate(app):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gtk.Widget.get_display(Gtk.Label()), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = Window(app, source)
        if args.snapshot:
            win.set_focusable(False)
        win.present()
        if args.snapshot:
            GLib.timeout_add(700 if args.demo else 4000, lambda: (snapshot(win, args.snapshot), app.quit()))

    app.connect('activate', activate)
    app.run([sys.argv[0]])


def snapshot(win, path):
    paintable = Gtk.WidgetPaintable.new(win)
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is not None:
        win.get_renderer().render_texture(node, None).save_to_png(str(path))
    return False


if __name__ == '__main__':
    main()
