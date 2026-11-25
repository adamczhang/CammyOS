"""Calendar tab of the Control Center: Executor connection setup and local-only results.

Connect, Test and Disconnect are explicit buttons. Every setup dependency is listed with its
real state, including the ones C3 cannot check (the Executor dashboard connection) and the
ones not built yet. Account actions also need Calendar access: Adult mode plus the
Calendar access PIN in the Family tab. Disconnect and Cancel work without it, in Kids too.
Calendar results appear only in this tab.
Calendar requests carry the panel token (private/c3/calendar-panel-token), which only this
window reads.
"""
import threading
from copy import copy
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from c3_client import ROOT, C3Error  # noqa: E402
from c3.calendar import authorize_url_ok  # noqa: E402

PATH = '/v1/calendar'
PANEL_TOKEN = ROOT / 'private/c3/calendar-panel-token'
_CONNECT_TIMEOUT = 90
_READ_TIMEOUT = 130
_STATES = {
    'connected': 'Signed in',
    'connecting': 'Waiting for the browser sign-in…',
    'reconnect_required': 'Sign-in expired or revoked: connect again',
    'disconnected': 'Not connected',
}


class CalendarApi:
    """The C3 calendar routes, with the panel token this window alone reads."""

    def __init__(self, client, token_file=PANEL_TOKEN):
        self.client = copy(client)
        self.client.timeout = max(client.timeout or 0, _READ_TIMEOUT)
        self.token_file = Path(token_file)

    def _call(self, method, path, body=None):
        try:
            token = self.token_file.read_text().strip()
        except OSError:
            raise C3Error(503, 'C3 has not made the calendar panel token yet; start C3 first.') from None
        return self.client.exchange(method, PATH + path, body, headers={'X-Cammy-Panel-Token': token})[0]

    def status(self):
        """With the panel token when C3 has made it, so Kids mode still shows this window the
        selected profile's connection for Disconnect."""
        try:
            return self._call('GET', '')
        except C3Error as exc:
            if exc.code != 503:
                raise
        return self.client.request('GET', PATH)

    def connect(self, profile, connection):
        return self._call('POST', '/connect', {'profile': profile, 'connection': connection})

    def cancel(self, profile):
        return self._call('POST', '/cancel', {'profile': profile})

    def test(self, profile):
        return self._call('POST', '/test', {'profile': profile})

    def disconnect(self, profile):
        return self._call('POST', '/disconnect', {'profile': profile})

    def query(self, profile, range_name):
        return self._call('POST', '/query', {'profile': profile, 'range': range_name})

    def result(self, profile):
        from urllib.parse import urlencode
        return self._call('GET', '/result?' + urlencode({'profile': profile}))['result']


def dependencies(status):
    """[(title, ok, detail)] for the setup checklist, from a /v1/calendar status (or None)."""
    if status is None:
        return [('Cammy Control Center', False, 'Calendar status is unavailable. Is C3 running?')]
    access = status.get('access') or {}
    broker = status.get('broker') or {}
    connection = status.get('connection') or {}
    state = connection.get('state')
    test = connection.get('last_test') or {}
    rows = [
        ('Brain profile', bool(status.get('profile')),
         status.get('profile_name') or 'Select a profile in the Brain tab. Each profile has its own connection.'),
        ('Calendar access', bool(access.get('allowed')),
         'Open for this profile.' if access.get('allowed') else
         'This C3 has no Calendar access gate. Calendar stays off.' if not access.get('integrated') else
         'Kids mode: Calendar stays off. Disconnect still works.' if status.get('adult') is False else
         'Locked. Unlock it with the Calendar access PIN in the Family tab.'),
        ('Calendar broker', bool(broker.get('running')),
         'Running on this PC; it alone holds the sign-in.' if broker.get('running') else
         'Not running. Start host/cammy_calendar_broker.py as its own service.'),
        ('Executor scoped connection', bool(connection.get('connection')),
         ('Connection %s. ' % connection['connection'] if connection.get('connection') else '')
         + 'Make it on the Executor dashboard (Connect → Scoped connections): Google Calendar, the account '
           'to use, read-only tools. C3 cannot see which account you chose.'),
        ('Sign-in', state == 'connected', _STATES.get(state, 'Unknown')
         + (' (%s)' % connection['last_error_message'] if connection.get('last_error_message') else '')),
        ('Connection test', test.get('ok') is True,
         'Working' if test.get('ok') is True else 'Failed (%s)' % test.get('code', 'unknown') if test else 'Not tested'),
    ]
    return rows


def actions(status, busy=False):
    """Which buttons are usable now."""
    status = status or {}
    access = (status.get('access') or {}).get('allowed') is True
    running = (status.get('broker') or {}).get('running') is True
    state = (status.get('connection') or {}).get('state')
    base = not busy and running and bool(status.get('profile'))
    return {
        'connect': base and access and state in ('disconnected', 'reconnect_required', None),
        'cancel': base and state == 'connecting',
        'test': base and access and state == 'connected',
        'disconnect': base and state in ('connected', 'reconnect_required', 'connecting'),
        'read': base and status.get('ready') is True,
    }


def _thread_run(work, done):
    def run():
        try:
            result, error = work(), None
        except (C3Error, ValueError, OSError, KeyError, TypeError) as exc:
            result, error = None, exc
        GLib.idle_add(lambda: done(result, error) and False)
    threading.Thread(target=run, daemon=True).start()


def _error_text(exc):
    return str(exc) if isinstance(exc, C3Error) else 'The request failed.'


class CalendarPanel(Gtk.Box):
    def __init__(self, client, *, api=None, run=None, launch=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ('top', 'bottom', 'start', 'end'):
            self.set_property('margin-' + side, 14)
        self.api = api if api is not None else CalendarApi(client)
        self.run = run or _thread_run
        self.launch = launch or self._launch_browser
        self.status = None
        self.busy = False
        self.poll = None
        self.generation = 0

        setup = Adw.PreferencesGroup(title='Executor Calendar',
                                     description='Read-only calendar for the selected Brain profile.')
        self.refresh_button = Gtk.Button(label='Refresh', valign=Gtk.Align.CENTER)
        self.refresh_button.connect('clicked', lambda *_: self.refresh())
        setup.set_header_suffix(self.refresh_button)
        self.append(setup)
        self.checklist = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.checklist.add_css_class('boxed-list')
        self.append(self.checklist)
        self.check_rows = []

        self.connection = Gtk.Entry(placeholder_text='Executor connection ID', max_length=128, hexpand=True)
        self.connection.update_property([Gtk.AccessibleProperty.LABEL], ['Executor connection ID'])
        self.append(self.connection)
        buttons = Gtk.FlowBox(column_spacing=6, row_spacing=4, selection_mode=Gtk.SelectionMode.NONE,
                              max_children_per_line=4)
        self.buttons = {}
        for key, label in (('connect', 'Connect'), ('cancel', 'Cancel sign-in'),
                           ('test', 'Test'), ('disconnect', 'Disconnect')):
            button = Gtk.Button(label=label)
            button.connect('clicked', getattr(self, '_' + key))
            buttons.append(button)
            self.buttons[key] = button
        self.append(buttons)
        self.feedback = Gtk.Label(xalign=0, wrap=True)
        self.append(self.feedback)

        reads = Adw.PreferencesGroup(title='Calendar',
                                     description='Shown only here. Not sent to Brain, Gemini, the transcript or memory.')
        self.append(reads)
        row = Gtk.FlowBox(column_spacing=6, row_spacing=4, selection_mode=Gtk.SelectionMode.NONE,
                          max_children_per_line=3)
        for key, label in (('today', 'Today'), ('tomorrow', 'Tomorrow'), ('next', 'Next event')):
            button = Gtk.Button(label=label)
            button.connect('clicked', self._read, key)
            row.append(button)
            self.buttons['read:' + key] = button
        self.append(row)
        self.result_title = Gtk.Label(xalign=0, wrap=True)
        self.result_title.add_css_class('heading')
        self.append(self.result_title)
        self.result = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.append(self.result)
        self.connect('map', lambda *_: self.refresh())
        self.connect('unmap', lambda *_: self._stop_poll())
        self._render()

    # -- requests
    def _request(self, work, done, *, busy=True):
        if busy:
            if self.busy:
                return
            self.busy = True
            self._render()
        self.generation += 1
        generation = self.generation

        def finish(result, error):
            if busy:
                self.busy = False
            done(result, error, generation == self.generation)
            self._render()
            return False
        self.run(work, finish)

    def refresh(self):
        def done(status, error, latest):
            if not latest:
                self._schedule_poll()
                return
            if error is None:
                old = self._binding()
                self.status = status
                if old != self._binding():
                    self._clear()
                profile = status.get('profile')
                if profile:
                    self._fetch_result(profile)
            elif latest:
                self.status = None
                self.feedback.set_label(_error_text(error))
            self._schedule_poll()
        self._request(self.api.status, done, busy=False)

    def _fetch_result(self, profile):
        binding = self._binding()
        def done(result, error):
            if binding != self._binding():
                return False
            if error is None and result is not None and result.get('profile') == profile:
                self._show(result)
            elif not self.busy:
                self._clear()
            return False
        self.run(lambda: self.api.result(profile), done)

    def _profile(self):
        return (self.status or {}).get('profile')

    def _binding(self):
        status = self.status or {}
        return self._profile(), status.get('selection'), (status.get('access') or {}).get('generation')

    def _connect(self, *_):
        profile, connection = self._profile(), self.connection.get_text().strip()
        if not profile or not connection:
            self.feedback.set_label('Enter the connection ID from the Executor dashboard first.')
            return

        def done(reply, error, _):
            if error is not None:
                self.feedback.set_label(_error_text(error))
            elif reply.get('profile') == profile and authorize_url_ok(reply.get('authorize_url')):
                self.feedback.set_label('Finish the sign-in in your browser; choose the same connection.')
                self.launch(reply['authorize_url'])
            self.refresh()
        self._request(lambda: self.api.connect(profile, connection), done)

    def _simple(self, call, ok_text):
        profile = self._profile()
        if not profile:
            return

        def done(_, error, __):
            self.feedback.set_label(_error_text(error) if error is not None else ok_text)
            self.refresh()
        self._request(lambda: call(profile), done)

    def _cancel(self, *_):
        self._simple(self.api.cancel, 'Sign-in cancelled.')

    def _test(self, *_):
        self._simple(self.api.test, 'Connection test passed.')

    def _disconnect(self, *_):
        self._simple(self.api.disconnect, 'Disconnected here. To end the grant everywhere, also revoke the '
                                          'connection on the Executor dashboard.')

    def _read(self, _button, range_name):
        profile = self._profile()
        if not profile:
            return

        binding = self._binding()
        def done(result, error, _):
            if binding != self._binding():
                return
            if error is not None:
                self.feedback.set_label(_error_text(error))
            elif result.get('profile') == profile:
                self.feedback.set_label('')
                self._show(result)
        self._request(lambda: self.api.query(profile, range_name), done)

    # -- view
    def _show(self, result):
        self.result_title.set_label('%s · %s' % (result.get('label', ''), result.get('profile_name', '')))
        self.result.set_label('\n'.join(result.get('lines') or []))

    def _clear(self):
        self.result_title.set_label('')
        self.result.set_label('')

    def _render(self):
        for row in self.check_rows:
            self.checklist.remove(row)
        self.check_rows = []
        for title, ok, detail in dependencies(self.status):
            row = Adw.ActionRow(title=title, subtitle=detail)
            row.set_subtitle_selectable(True)
            row.add_prefix(Gtk.Image(icon_name='emblem-ok-symbolic' if ok else 'dialog-warning-symbolic'))
            self.checklist.append(row)
            self.check_rows.append(row)
        state = actions(self.status, self.busy)
        for key, button in self.buttons.items():
            button.set_sensitive(state['read'] if key.startswith('read:') else state[key])
        connection = ((self.status or {}).get('connection') or {}).get('connection')
        if connection and not self.connection.get_text():
            self.connection.set_text(connection)
        if not self._profile() or not (self.status.get('access') or {}).get('allowed'):
            self._clear()

    def _schedule_poll(self):
        self._stop_poll()
        state = ((self.status or {}).get('connection') or {}).get('state')
        if self.get_mapped():
            self.poll = GLib.timeout_add_seconds(2 if state == 'connecting' else 10, self._poll)

    def _stop_poll(self):
        if self.poll is not None:
            GLib.source_remove(self.poll)
            self.poll = None

    def _poll(self):
        self.poll = None
        self.refresh()
        return False

    def _launch_browser(self, url):
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error:
            self.feedback.set_label('Could not open a browser. Open this address yourself:\n' + url)
