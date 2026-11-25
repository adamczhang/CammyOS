"""Native provider status and manual connection checks for Cammy Control Center."""

import shlex
import shutil
import subprocess
import threading
from copy import copy
from urllib.parse import urlsplit

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from c3_client import C3Client, C3Error  # noqa: E402


_PATH = '/v1/providers'
_STATUS_TIMEOUT = 12  # A sign-in check can wait for an in-flight CLI status probe.
_TEST_TIMEOUT = 100
_PROVIDERS = {
    'codex': ('Codex', 'codex login', 'ChatGPT'),
    'claude': ('Claude Code', 'claude auth login --claudeai', 'Claude'),
}
_AUTH = {
    'subscription': 'Subscription sign-in detected',
    'api_key': 'API key in use. Subscription sign-in required.',
    'signed_out': 'Signed out',
    'unknown': 'Subscription sign-in unknown',
    'unavailable': 'Subscription sign-in unavailable',
}
_CHECK = {
    'untested': 'Not tested',
    'running': 'Testing…',
    'working': 'Working',
    'failed': 'Failed',
}


def _label(**kwargs):
    return Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, **kwargs)


def _announce(widget, message):
    if widget.get_mapped() and hasattr(widget, 'announce'):
        widget.announce(message, Gtk.AccessibleAnnouncementPriority.MEDIUM)


def _provider_data(row, provider_id):
    if (not isinstance(row, dict) or row.get('id') != provider_id
            or type(row.get('installed')) is not bool
            or not isinstance(row.get('auth'), str) or row['auth'] not in _AUTH
            or not isinstance(row.get('check'), str) or row['check'] not in _CHECK
            or not all(isinstance(row.get(key), str) for key in ('name', 'detail', 'recovery'))):
        raise ValueError('Invalid provider data')
    return row


def _status_error(exc):
    code = exc.code if isinstance(exc, C3Error) else None
    if isinstance(exc, C3Error) and exc.reason_code == 'kids_mode':
        return 'Provider controls are locked in Kids mode. Open Family to switch modes.'
    if code in (404, 405, 501):
        return 'Provider status is unavailable. Update and restart the C3 service, then refresh.'
    if code in (401, 403):
        return 'C3 denied access. Check the local C3 API token configuration, then refresh.'
    if code == 503 or isinstance(exc, OSError):
        return 'C3 did not respond. Check that the service is running, then refresh.'
    if code == 409:
        return 'C3 could not start the test. Refresh to check whether another test is running.'
    if code is not None:
        return f'C3 returned HTTP {code}. Check the service and refresh before trying again.'
    return 'C3 returned an unreadable provider reply. Check that the service is up to date, then refresh.'


class _ProviderRow(Gtk.ListBoxRow):
    def __init__(self, name, command, account, *, local=False):
        super().__init__(activatable=False, selectable=False, can_focus=False)
        self.name = name
        self.data = None
        self.testing = False
        self.test_error = ''

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5,
                          margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(content)
        header = Gtk.Box(spacing=8)
        title = _label(label=name, hexpand=True)
        title.add_css_class('heading')
        header.append(title)
        self.test = Gtk.Button(label='Test connection', valign=Gtk.Align.CENTER)
        self.test.update_property([Gtk.AccessibleProperty.LABEL], [f'Test {name} connection'])
        header.append(self.test)
        content.append(header)

        self.installation = _label()
        self.auth = _label()
        self.check = _label()
        self.detail = _label()
        self.recovery = _label(selectable=True)
        self.feedback = _label()
        self.feedback.add_css_class('error')
        for label in (self.installation, self.auth, self.check, self.detail, self.feedback, self.recovery):
            content.append(label)

        self.sign_in = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        self.sign_in.append(_label(label=f'Sign in with your {account} subscription, then refresh.'))
        terminal = shutil.which('xdg-terminal-exec') if local else None
        if terminal:
            launcher = Gtk.Button(label='Open sign-in', halign=Gtk.Align.START)
            launcher.connect('clicked', self._open_sign_in, terminal, shlex.split(command))
            self.sign_in.append(launcher)
        else:
            self.sign_in.append(_label(label='Run this command on the Control Center host.'))
        command_row = Gtk.Box(spacing=6)
        command_label = _label(label=command, selectable=True)
        command_label.add_css_class('monospace')
        command_row.append(command_label)
        copy_button = Gtk.Button(icon_name='edit-copy-symbolic', valign=Gtk.Align.CENTER)
        copy_button.add_css_class('flat')
        copy_label = f'Copy {name} sign-in command'
        copy_button.set_tooltip_text(copy_label)
        copy_button.update_property([Gtk.AccessibleProperty.LABEL], [copy_label])
        copy_button.connect('clicked', self._copy_command, command)
        command_row.append(copy_button)
        self.sign_in.append(command_row)
        content.append(self.sign_in)

    def _copy_command(self, button, command):
        button.get_clipboard().set(command)
        _announce(button, f'{self.name} sign-in command copied')

    def _open_sign_in(self, button, terminal, args):
        button.set_sensitive(False)

        def run():
            try:
                result = subprocess.run([terminal, '--', *args], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                opened = result.returncode == 0
            except OSError:
                opened = False

            def finish():
                button.set_sensitive(True)
                if not opened:
                    self.feedback.set_label('Could not open a terminal. Copy and run the sign-in command below.')
                    self.feedback.set_visible(True)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(finish)

        threading.Thread(target=run, daemon=True).start()

    def render(self, available):
        row = self.data
        installed = row is not None and row['installed']
        auth = row['auth'] if row is not None else 'unknown'
        check = row['check'] if row is not None else None
        if self.testing:
            check = 'running'
        installation = 'CLI installed' if installed else 'CLI not installed'
        self.installation.set_label(installation if row is not None else 'Installation status unknown')
        self.auth.set_label(_AUTH[auth])
        result = 'Result unavailable' if self.test_error else _CHECK.get(check, 'Status unavailable')
        self.check.set_label(f'Connection test: {result}')
        detail = row['detail'] if row is not None and not self.testing and not self.test_error else ''
        recovery = row['recovery'] if row is not None else ''
        if row is not None and not installed and not recovery:
            recovery = f'Install {self.name} on the C3 host and make it available on the service PATH, then refresh.'
        for label, message in ((self.detail, detail), (self.feedback, self.test_error), (self.recovery, recovery)):
            label.set_label(message)
            label.set_visible(bool(message))
        self.sign_in.set_visible(available and installed and auth != 'subscription')
        self.test.set_sensitive(available and installed and auth == 'subscription' and check != 'running')


class ProviderPanel(Gtk.Box):
    """Clones the C3 client's connection settings with provider-specific timeouts."""

    def __init__(self, client: C3Client):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._client = copy(client)
        self._client.timeout = max(client.timeout or 0, _STATUS_TIMEOUT)
        self._test_client = copy(client)
        self._test_client.timeout = max(client.timeout or 0, _TEST_TIMEOUT)
        self._available = False
        self._refreshing = False
        self._running_poll = None
        self._revisions = dict.fromkeys(_PROVIDERS, 0)
        self._rows = {}

        group = Adw.PreferencesGroup(title='Providers', description='Manage the accounts used by Brain.')
        self._refresh_button = Gtk.Button(label='Refresh', valign=Gtk.Align.CENTER)
        self._refresh_button.connect('clicked', lambda *_: self._refresh(manual=True))
        group.set_header_suffix(self._refresh_button)
        self.append(group)
        self._status = _label()
        self.append(self._status)
        providers = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        providers.add_css_class('boxed-list')
        self.append(providers)
        local = urlsplit(client.endpoint).hostname in ('127.0.0.1', 'localhost', '::1')
        for provider_id, config in _PROVIDERS.items():
            row = _ProviderRow(*config, local=local)
            row.test.connect('clicked', self._test, provider_id)
            providers.append(row)
            self._rows[provider_id] = row
            row.render(self._available)
        self.connect('map', lambda *_: self._refresh())
        self.connect('unmap', self._stop_running_poll)

    @staticmethod
    def _request(operation, finish):
        def run():
            try:
                result = operation()
            except Exception as exc:
                # The HTTP error body may contain service details. Use fixed UI messages.
                GLib.idle_add(finish, None, _status_error(exc))
            else:
                GLib.idle_add(finish, result, '')

        threading.Thread(target=run, daemon=True).start()

    def _refresh(self, manual=False):
        if self._refreshing:
            return
        self._stop_running_poll()
        self._refreshing = True
        self._refresh_button.set_sensitive(False)
        self._status.set_label('Refreshing sign-in status…' if manual else 'Checking provider status…')
        self._status.remove_css_class('error')
        revisions = self._revisions.copy()

        def get_status():
            path = _PATH + '?refresh=1' if manual else _PATH
            rows = self._client.request('GET', path)['providers']
            if not isinstance(rows, list):
                raise ValueError('Invalid provider list')
            by_id = {row['id']: row for row in rows}
            if len(by_id) != len(rows):
                raise ValueError('Duplicate provider')
            return {key: _provider_data(by_id[key], key) for key in _PROVIDERS}

        def finish(data, error):
            self._refreshing = False
            self._refresh_button.set_sensitive(True)
            self._available = not error
            if error:
                self._status.add_css_class('error')
                if any(row.data is not None for row in self._rows.values()):
                    error += ' Showing last known status.'
            message = error
            self._status.set_label(message)
            self._status.set_visible(bool(message))
            for key, row in self._rows.items():
                # A refresh started before a test finished must not replace its result.
                if data is not None and revisions[key] == self._revisions[key]:
                    row.data = data[key]
                    row.test_error = ''
                row.render(self._available)
            if manual or error:
                _announce(self._status, message)
            self._schedule_running_poll()
            return GLib.SOURCE_REMOVE

        self._request(get_status, finish)

    def _schedule_running_poll(self):
        if self._running_poll is not None or self._refreshing or not (self._available and self.get_mapped()):
            return
        # A test started here delivers its result over POST. Only tests observed
        # through status (or an overlapping POST) require a follow-up GET.
        if any(row.data and row.data['check'] == 'running' and not row.testing for row in self._rows.values()):
            self._running_poll = GLib.timeout_add_seconds(2, self._poll_running_test)

    def _stop_running_poll(self, *_):
        if self._running_poll is not None:
            GLib.source_remove(self._running_poll)
            self._running_poll = None

    def _poll_running_test(self):
        self._running_poll = None
        self._refresh()
        return GLib.SOURCE_REMOVE

    def _test(self, button, provider_id):
        if not button.get_sensitive():
            return
        row = self._rows[provider_id]
        row.testing = True
        row.test_error = ''
        self._revisions[provider_id] += 1
        row.render(self._available)
        _announce(row.check, f'Testing {row.name} connection')

        def probe():
            data = self._test_client.request('POST', _PATH + '/test', {'id': provider_id})
            return _provider_data(data, provider_id)

        def finish(data, error):
            self._revisions[provider_id] += 1
            row.testing = False
            if error:
                row.test_error = f'Could not confirm the connection test. {error}'
            else:
                row.data = data
            row.render(self._available)
            _announce(row.check, f'{row.name}. {row.test_error or row.check.get_label()}')
            self._schedule_running_poll()
            return GLib.SOURCE_REMOVE

        self._request(probe, finish)
