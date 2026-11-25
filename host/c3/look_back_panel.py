"""Look Back: scrub the local camera history C3 keeps for the active profile.

A self-contained GTK4 panel for its own Control Center tab: `LookBackPanel(client)`.

- Talks only to the C3 service the given `C3Client` points at, and only when that endpoint is
  this computer or the local network: GET /v1/preview/history and GET /v1/preview/frame. No
  proxies, no redirects (a 3xx is an error, so the bearer token never follows it elsewhere), no
  cloud service. The panel itself reads no files and writes nothing; the client supplies its
  own API token.
- All HTTP runs on background threads. Scrubbing is debounced, at most one frame request is in
  flight and the newest wanted time wins. Replies from an earlier profile are dropped, and a
  profile change clears the picture before anything of the new profile is shown.
- Opens on the newest frame. "Follow latest" (off by default) keeps showing the newest frame.
  Polls run only while the panel is on screen; the panel never takes keyboard focus by itself.
"""
import ipaddress
import json
import math
import threading
import time
import urllib.error
import urllib.request
from copy import copy
from urllib.parse import quote, urlsplit

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from c3_client import C3Client, C3Error  # noqa: E402

HISTORY_PATH = '/v1/preview/history'
FRAME_PATH = '/v1/preview/frame'
_TIMEOUT = 8               # seconds per request; the API is local
_MAX_JSON = 64_000         # a history reply is a few hundred bytes
_MAX_FRAME = 16_000_000    # matches preview_history.MAX_FRAME_BYTES
_DEBOUNCE_MS = 180         # scrub settle time before a frame request
_POLL_S = 5                # coverage refresh while on screen
_FOLLOW_POLL_S = 1         # while following the newest frame
_STALE_S = 15              # newest frame older than this: not recording now
_REOPEN_LATEST_S = 60      # hidden longer than this: reopen on the newest frame
_FULL = 0.95               # share of capacity at which old frames are being overwritten
_STEPS = ((1, '1 s'), (5, '5 s'), (30, '30 s'), (120, '2 min'))
_DEFAULT_STEP = 1


class _Refused(Exception):
    """The client's endpoint is not on this computer or the local network."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # urllib then raises HTTPError for the 3xx


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _local_endpoint(endpoint):
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname
    except ValueError:
        return False
    if parts.scheme not in ('http', 'https') or not host:
        return False
    if host == 'localhost':
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _get(client, path, limit):
    """GET path from C3. Returns (headers, body). Errors carry no service text."""
    if not _local_endpoint(client.endpoint):
        raise _Refused()
    token = client._token()  # C3Error(503) when C3 is not set up
    desktop = getattr(client, 'desktop_headers', lambda: {})()
    req = urllib.request.Request(client.endpoint.rstrip('/') + path, method='GET',
                                 headers={**desktop, 'Authorization': 'Bearer ' + token})
    try:
        with _OPENER.open(req, timeout=client.timeout) as reply:
            body = reply.read(limit + 1)
            if len(body) > limit:
                raise ValueError('reply too large')
            return reply.headers, body
    except urllib.error.HTTPError as exc:
        exc.close()  # the body may hold service detail; the UI uses fixed messages
        raise C3Error(exc.code, '') from None
    except (urllib.error.URLError, OSError):
        raise C3Error(503, '') from None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _parse_history(data):
    if not isinstance(data, dict):
        raise ValueError('history is not an object')
    profile, name = data.get('profile'), data.get('profile_name')
    if profile is not None and (not isinstance(profile, str) or not profile):
        raise ValueError('bad profile')
    if name is not None and not isinstance(name, str):
        raise ValueError('bad profile name')
    used, cap, frames = data.get('bytes'), data.get('capacity_bytes'), data.get('frames')
    if not (_count(used) and _count(cap) and cap > 0 and _count(frames)):
        raise ValueError('bad counts')
    oldest, newest = data.get('oldest'), data.get('newest')
    if frames:
        if not (_number(oldest) and _number(newest) and 0 < oldest <= newest):
            raise ValueError('bad coverage')
    else:
        oldest = newest = None
    return {'profile': profile, 'name': (name or '').strip() or profile, 'bytes': used,
            'recording_allowed': data.get('recording_allowed') is not False,
            'capacity': cap, 'frames': frames, 'oldest': oldest, 'newest': newest}


def _decode_frame(headers, body, profile):
    if headers.get('X-Cammy-Profile') != profile:
        raise C3Error(409, '')  # the frame belongs to another profile
    stamp = float(headers.get('X-Cammy-Frame-Time') or 'nan')
    if not (math.isfinite(stamp) and stamp > 0):
        raise ValueError('bad frame time')
    if not body.startswith(b'\xff\xd8'):
        raise ValueError('not a JPEG')
    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(body))
    return {'time': stamp, 'size': len(body), 'width': texture.get_width(),
            'height': texture.get_height(), 'texture': texture}


def _error_text(exc, what):
    if isinstance(exc, _Refused):
        return ('Look Back connects only to a C3 service on this computer or the local network. '
                'Check the C3 endpoint setting.')
    code = exc.code if isinstance(exc, C3Error) else None
    if code in (404, 405, 501) and what == 'history':
        return 'Look Back is not available on this C3 service. Update and restart C3, then refresh.'
    if code in (401, 403):
        return 'C3 denied access. Check the local C3 API token configuration, then refresh.'
    if code == 503:
        return 'C3 did not respond. Check that the service is running; Look Back keeps retrying.'
    if code is not None:
        return f'C3 returned HTTP {code}. Check the service, then refresh.'
    return 'C3 returned an unreadable Look Back reply. Check that the service is up to date.'


def _clock(stamp, now=None):
    local = time.localtime(stamp)
    text = time.strftime('%I:%M:%S %p', local).lstrip('0')
    if local[:3] != time.localtime(now if now is not None else time.time())[:3]:
        text = time.strftime('%b %d, ', local) + text
    return text


def _span(seconds):
    seconds = max(0.0, seconds)
    if seconds < 60:
        return '%.0f s' % seconds
    if seconds < 3600:
        return '%d min %02d s' % divmod(int(seconds), 60) if seconds < 600 else '%.0f min' % (seconds / 60)
    return '%d h %02d min' % divmod(int(seconds) // 60, 60)


def _ago(stamp, now):
    age = now - stamp
    return 'just now' if age < 2 else _span(age) + ' ago'


def _megabytes(count):
    return '%.0f MB' % (count / 1e6) if count >= 10e6 else '%.1f MB' % (count / 1e6)


def _label(**kwargs):
    return Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, **kwargs)


def _announce(widget, message):
    if widget.get_mapped() and hasattr(widget, 'announce'):
        widget.announce(message, Gtk.AccessibleAnnouncementPriority.MEDIUM)


def _button(label, icon, tooltip):
    button = Gtk.Button(focus_on_click=False, tooltip_text=tooltip)
    content = Adw.ButtonContent(label=label, icon_name=icon, use_underline=False)
    button.set_child(content)
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    return button


class LookBackPanel(Gtk.Box):
    """Clones the client's connection settings; never changes the client passed in."""

    def __init__(self, client: C3Client):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=18,
                         margin_bottom=18, margin_start=18, margin_end=18)
        self._client = copy(client)
        self._client.timeout = min(client.timeout or _TIMEOUT, _TIMEOUT)

        self._hist = None            # last good history (parsed)
        self._hist_error = ''
        self._hist_busy = False
        self._hist_again = False
        self._profile = None         # active profile id, from history
        self._gen = 0                # bumped on every profile change; older replies are dropped
        self._jump_latest = True     # next history reply selects and loads the newest frame
        self._overwriting = False
        self._selected = None        # the time the timeline points at
        self._shown = None           # the frame in the picture
        self._frame_error = ''
        self._frame_busy = False
        self._want = None            # (gen, profile, at) waiting for the in-flight request
        self._follow = False
        self._debounce = None
        self._poll = None
        self._poll_every = None
        self._hidden_at = None
        self._quiet = False          # guards programmatic timeline / toggle changes

        header = Gtk.Box(spacing=8)
        title = _label(label='Look Back', hexpand=True)
        title.add_css_class('title-2')
        header.append(title)
        self._refresh_button = _button('Refresh', 'view-refresh-symbolic', 'Refresh camera memory')
        self._refresh_button.connect('clicked', lambda *_: self.refresh(manual=True))
        header.append(self._refresh_button)
        self.append(header)

        self._profile_label = _label()
        self._profile_label.add_css_class('heading')
        self.append(self._profile_label)
        coverage = Gtk.Box(spacing=10)
        self._coverage = _label(hexpand=True)
        self._coverage.add_css_class('dim-label')
        coverage.append(self._coverage)
        self._fill = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER)
        self._fill.set_size_request(72, -1)
        for offset in ('low', 'high', 'full'):  # one neutral fill, not GTK's warning colours
            self._fill.remove_offset_value(offset)
        self._fill.update_property([Gtk.AccessibleProperty.LABEL], ['Camera memory used'])
        coverage.append(self._fill)
        self.append(coverage)

        self._notice = _label()
        self.append(self._notice)

        self._picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN,
                                    hexpand=True, vexpand=True)
        self._picture.set_size_request(220, 160)
        self._spinner = Adw.Spinner(halign=Gtk.Align.END, valign=Gtk.Align.START,
                                    margin_top=8, margin_end=8, visible=False)
        self._spinner.set_size_request(24, 24)
        overlay = Gtk.Overlay(child=self._picture)
        overlay.add_overlay(self._spinner)
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER,
                        margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        self._empty_icon = Gtk.Image(pixel_size=48)
        self._empty_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._empty_title.add_css_class('title-3')
        self._empty_description = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                            justify=Gtk.Justification.CENTER, max_width_chars=60)
        self._empty_description.add_css_class('dim-label')
        for widget in (self._empty_icon, self._empty_title, self._empty_description):
            empty.append(widget)
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, vexpand=True)
        self._stack.add_named(overlay, 'frame')
        self._stack.add_named(empty, 'empty')
        frame = Gtk.Frame(child=self._stack, vexpand=True)
        self.append(frame)

        self._frame_info = _label()
        self.append(self._frame_info)

        self._timeline = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False,
                                   hexpand=True, focus_on_click=False)
        self._timeline.set_adjustment(Gtk.Adjustment(lower=0, upper=0, step_increment=1, page_increment=10))
        self._timeline.update_property([Gtk.AccessibleProperty.LABEL], ['Camera memory timeline'])
        self._timeline.connect('value-changed', self._scrubbed)
        self.append(self._timeline)
        ends = Gtk.Box(spacing=8)
        self._oldest_label = _label(hexpand=True)
        self._newest_label = Gtk.Label(xalign=1)
        for label in (self._oldest_label, self._newest_label):
            label.add_css_class('caption')
            label.add_css_class('dim-label')
            ends.append(label)
        self.append(ends)

        controls = Gtk.FlowBox(column_spacing=6, row_spacing=4, max_children_per_line=6,
                               selection_mode=Gtk.SelectionMode.NONE, halign=Gtk.Align.CENTER)
        controls.set_focusable(False)
        self._controls = controls
        self._oldest_button = _button('Oldest', 'go-first-symbolic', 'Go to the oldest kept frame')
        self._back_button = _button('Back', 'go-previous-symbolic', 'Step back in time')
        self._step = Gtk.DropDown.new_from_strings([label for _, label in _STEPS])
        self._step.set_selected(_DEFAULT_STEP)
        self._step.set_focus_on_click(False)
        self._step.set_tooltip_text('Step size')
        self._step.update_property([Gtk.AccessibleProperty.LABEL], ['Step size'])
        self._forward_button = _button('Forward', 'go-next-symbolic', 'Step forward in time')
        self._latest_button = _button('Latest', 'go-last-symbolic', 'Go to the newest frame')
        self._follow_button = Gtk.ToggleButton(label='Follow latest', focus_on_click=False,
                                               tooltip_text='Keep showing the newest frame')
        self._oldest_button.connect('clicked', lambda *_: self._go_oldest())
        self._back_button.connect('clicked', lambda *_: self._step_by(-1))
        self._forward_button.connect('clicked', lambda *_: self._step_by(1))
        self._latest_button.connect('clicked', lambda *_: self.show_latest())
        self._follow_button.connect('toggled', self._follow_toggled)
        for widget in (self._oldest_button, self._back_button, self._step, self._forward_button,
                       self._latest_button, self._follow_button):
            controls.append(widget)
        self.append(controls)

        self.connect('map', self._mapped)
        self.connect('unmap', self._unmapped)
        self._render()

    # ---- public ------------------------------------------------------------------------------
    def refresh(self, manual=False):
        """Fetch coverage and the active profile; a no-op while a refresh is running."""
        if self._hist_busy:
            self._hist_again = True
            return
        self._hist_busy = True
        self._render()
        client = self._client

        def finish(data, exc):
            self._hist_busy = False
            self._history_arrived(data, exc, manual)
            if self._hist_again:
                self._hist_again = False
                self.refresh()
            return GLib.SOURCE_REMOVE

        self._run(lambda: _parse_history(json.loads(_get(client, HISTORY_PATH, _MAX_JSON)[1] or b'{}')), finish)

    def show_latest(self):
        """Select and load the newest frame (after a fresh coverage check)."""
        self._jump_latest = True
        self.refresh()

    def set_follow_latest(self, on):
        if self._follow_button.get_active() != bool(on):
            self._follow_button.set_active(bool(on))

    # ---- lifecycle -----------------------------------------------------------------------------
    def _mapped(self, *_):
        if self._hidden_at is not None and time.monotonic() - self._hidden_at > _REOPEN_LATEST_S:
            self._jump_latest = True
        self._hidden_at = None
        self._schedule_poll()
        self.refresh()

    def _unmapped(self, *_):
        self._hidden_at = time.monotonic()
        self._cancel_debounce()
        self._stop_poll()

    def _schedule_poll(self):
        every = _FOLLOW_POLL_S if self._follow else _POLL_S
        if self._poll is not None and self._poll_every == every:
            return
        self._stop_poll()
        self._poll_every = every
        self._poll = GLib.timeout_add_seconds(every, self._poll_tick)

    def _stop_poll(self):
        if self._poll is not None:
            GLib.source_remove(self._poll)
            self._poll = None

    def _poll_tick(self):
        if not self.get_mapped():
            self._poll = None
            return GLib.SOURCE_REMOVE
        if not self._hist_busy:
            self.refresh()
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _run(operation, finish):
        def work():
            try:
                result = operation()
            except Exception as exc:  # noqa: BLE001 - mapped to fixed UI text on the main loop
                GLib.idle_add(finish, None, exc)
            else:
                GLib.idle_add(finish, result, None)

        threading.Thread(target=work, name='look-back', daemon=True).start()

    # ---- history -------------------------------------------------------------------------------
    def _history_arrived(self, data, exc, manual):
        if exc is not None:
            message = _error_text(exc, 'history')
            if message != self._hist_error or manual:
                _announce(self._notice, message)
            self._hist_error = message
            self._render()
            return
        self._hist_error = ''
        if data['profile'] != self._profile:
            self._profile_changed(data)
        old = self._hist
        if old is not None and data['frames'] == 0 and old['frames']:
            self._clear_frame()  # The active profile no longer has buffered frames.
        if data['frames'] and data['bytes'] >= _FULL * data['capacity']:
            self._overwriting = self._overwriting or bool(
                old and old['frames'] and data['oldest'] > old['oldest'])
        else:
            self._overwriting = False
        self._hist = data
        self._set_range()
        if self._profile and data['frames']:
            if self._jump_latest:
                self._jump_latest = False
                self._select(data['newest'])
                self._request_frame(data['newest'])
            elif self._follow and (self._shown is None or data['newest'] > self._shown['time'] + 0.01):
                self._select(data['newest'])
                self._request_frame(data['newest'])
        elif manual:
            _announce(self._notice, 'Camera memory refreshed')
        self._render()

    def _profile_changed(self, data):
        self._gen += 1
        self._profile = data['profile']
        self._clear_frame()
        self._cancel_debounce()
        self._want = None
        self._selected = None
        self._overwriting = False
        self._hist = None
        self._jump_latest = True
        name = data['name']
        _announce(self._profile_label, f'Look Back now shows {name}' if name else 'No active profile')

    def _clear_frame(self):
        self._shown = None
        self._frame_error = ''
        self._picture.set_paintable(None)
        self._picture.set_alternative_text(None)

    def _set_range(self):
        hist = self._hist
        adjustment = self._timeline.get_adjustment()
        self._quiet = True
        try:
            if hist and hist['frames']:
                lower, upper = hist['oldest'], hist['newest']
                value = self._selected if self._selected is not None else upper
                adjustment.configure(min(max(value, lower), upper), lower, upper, 1, 10, 0)
            else:
                adjustment.configure(0, 0, 0, 1, 10, 0)
        finally:
            self._quiet = False

    # ---- timeline and navigation ------------------------------------------------------------
    def _select(self, at):
        self._selected = at
        self._quiet = True
        try:
            self._timeline.set_value(at)
        finally:
            self._quiet = False

    def _scrubbed(self, scale):
        if self._quiet or not self._usable():
            return
        self.set_follow_latest(False)
        self._selected = scale.get_value()
        self._cancel_debounce()
        self._debounce = GLib.timeout_add(_DEBOUNCE_MS, self._settled)
        self._render()

    def _settled(self):
        self._debounce = None
        if self._selected is not None:
            self._request_frame(self._selected)
        return GLib.SOURCE_REMOVE

    def _cancel_debounce(self):
        if self._debounce is not None:
            GLib.source_remove(self._debounce)
            self._debounce = None

    def _base(self):
        if self._shown is not None and not self._frame_error:
            return self._shown['time']
        return self._selected

    def _step_by(self, direction):
        hist, base = self._hist, self._base()
        if not self._usable() or base is None:
            return
        self.set_follow_latest(False)
        self._cancel_debounce()
        at = min(max(base + direction * _STEPS[self._step.get_selected()][0], hist['oldest']), hist['newest'])
        self._select(at)
        self._request_frame(at)

    def _go_oldest(self):
        if not self._usable():
            return
        self.set_follow_latest(False)
        self._cancel_debounce()
        self._select(self._hist['oldest'])
        self._request_frame(self._hist['oldest'])

    def _follow_toggled(self, button):
        self._follow = button.get_active()
        if self.get_mapped():
            self._schedule_poll()
        if self._follow:
            self._cancel_debounce()
            self.show_latest()
        self._render()

    def _usable(self):
        return bool(self._profile and self._hist and self._hist['frames'])

    # ---- frames --------------------------------------------------------------------------------
    def _request_frame(self, at):
        if not self._profile:
            return
        self._want = (self._gen, self._profile, float(at))
        if not self._frame_busy:
            self._start_frame()
        self._render()

    def _start_frame(self):
        gen, profile, at = self._want
        self._want = None
        self._frame_busy = True
        path = '%s?profile=%s&at=%.3f' % (FRAME_PATH, quote(profile, safe=''), at)
        client = self._client

        def finish(frame, exc):
            self._frame_busy = False
            superseded = self._want is not None
            if gen == self._gen and profile == self._profile:  # else: an earlier profile; drop it
                self._frame_arrived(frame, exc, at, superseded)
            if self._want is not None:
                if self._want[0] == self._gen:
                    self._start_frame()
                else:
                    self._want = None
            self._render()
            return GLib.SOURCE_REMOVE

        self._run(lambda: _decode_frame(*_get(client, path, _MAX_FRAME), profile), finish)

    def _frame_arrived(self, frame, exc, at, superseded):
        if exc is None:
            self._shown = frame
            self._frame_error = ''
            self._picture.set_paintable(frame['texture'])
            self._picture.set_alternative_text('Camera frame from ' + _clock(frame['time']))
            return
        if superseded:
            return  # a newer request is on its way; its outcome is what counts
        code = exc.code if isinstance(exc, C3Error) else None
        if code == 404:
            self._clear_frame()
            self._frame_error = (f'No frame kept at {_clock(at)}. It was overwritten, or the camera '
                                 'was not recording then.')
            self.refresh()
        elif code == 409:
            self._clear_frame()
            self._frame_error = 'The active profile changed. Loading its camera memory…'
            self._jump_latest = True
            self.refresh()
        else:
            message = _error_text(exc, 'frame')
            self._frame_error = message + (' Showing the last frame loaded.' if self._shown else '')
        _announce(self._notice, self._frame_error)

    # ---- view ----------------------------------------------------------------------------------
    def _render(self):
        now = time.time()
        hist = self._hist
        name = hist['name'] if hist else None
        if self._profile:
            self._profile_label.set_label(f'Active profile: {name or self._profile}')
            self._profile_label.set_tooltip_text(f'Profile ID: {self._profile}')
        else:
            self._profile_label.set_label('Active profile: none' if hist or not self._hist_busy
                                          else 'Active profile: loading…')
            self._profile_label.set_tooltip_text(None)

        if hist and hist['frames']:
            share = hist['bytes'] / hist['capacity']
            self._coverage.set_label('%s frames · %s kept · %s of %s used (%.0f%%)' % (
                f"{hist['frames']:,}", _span(hist['newest'] - hist['oldest']), _megabytes(hist['bytes']),
                _megabytes(hist['capacity']), 100 * share))
            self._fill.set_value(min(1.0, share))
            self._oldest_label.set_label('Oldest ' + _clock(hist['oldest'], now))
            stale = now - hist['newest'] > _STALE_S
            detail = 'paused' if not hist.get('recording_allowed', True) else _ago(hist['newest'], now) if stale else 'recording'
            self._newest_label.set_label('Newest %s (%s)' % (_clock(hist['newest'], now), detail))
        else:
            self._coverage.set_label('Camera history is stored locally for this profile' if hist else '')
            self._fill.set_value(hist['bytes'] / hist['capacity'] if hist else 0)
            self._oldest_label.set_label('')
            self._newest_label.set_label('')
            stale = False
        self._fill.set_visible(hist is not None)

        notices, level = [], 'warning'
        if self._hist_error:
            notices.append(self._hist_error + (' Showing the last known camera memory.' if hist else ''))
            level = 'error'
        if self._frame_error and self._shown is not None:
            notices.append(self._frame_error)
            level = 'error'
        if hist and hist['frames'] and not hist.get('recording_allowed', True):
            notices.append('New frames are paused while Cammy is in Kids mode.')
        elif hist and hist['frames'] and stale:
            notices.append('Not recording now: the newest frame is %s old.' % _span(now - hist['newest']))
        if hist and hist['frames'] and self._shown is not None and self._shown['time'] < hist['oldest']:
            notices.append('The frame on screen has since been overwritten in camera memory.')
        if self._overwriting:
            notices.append('Camera memory is full: the oldest frames are overwritten as new ones arrive.')
        self._notice.set_label(' '.join(notices))
        self._notice.set_visible(bool(notices))
        for css in ('warning', 'error'):
            self._notice.remove_css_class(css)
        self._notice.add_css_class(level)

        self._render_frame(now, hist)

        usable = self._usable()
        self._controls.set_visible(bool(hist and hist['frames']))
        base = self._base()
        for widget in (self._timeline, self._oldest_button, self._latest_button, self._follow_button, self._step):
            widget.set_sensitive(usable)
        self._back_button.set_sensitive(usable and base is not None and base > hist['oldest'] + 0.001)
        self._forward_button.set_sensitive(usable and base is not None and base < hist['newest'] - 0.001)
        self._refresh_button.set_sensitive(not self._hist_busy)

    def _render_frame(self, now, hist):
        loading = self._frame_busy or self._want is not None or self._debounce is not None
        self._spinner.set_visible(loading)
        shown = self._shown
        if shown is not None and hist is not None:
            self._stack.set_visible_child_name('frame')
            info = 'Frame from %s · %s · %.0f KB · %d × %d' % (
                _clock(shown['time'], now), _ago(shown['time'], now), shown['size'] / 1000,
                shown['width'], shown['height'])
            if self._selected is not None and abs(self._selected - shown['time']) > 1.5 and not loading:
                info += ' · nearest to ' + _clock(self._selected, now)
            elif loading and self._selected is not None:
                info += ' · loading ' + _clock(self._selected, now) + '…'
            self._frame_info.set_label(info)
            return
        if self._selected is not None and loading:
            self._frame_info.set_label('Loading the frame from %s…' % _clock(self._selected, now))
        else:
            self._frame_info.set_label('')
        self._stack.set_visible_child_name('empty')
        if hist is None and self._hist_error:
            self._placeholder('camera-disabled-symbolic', 'Camera memory unavailable', self._hist_error)
        elif hist is None:
            self._placeholder('camera-photo-symbolic', 'Loading camera memory…', '')
        elif not self._profile:
            self._placeholder('avatar-default-symbolic', 'No active profile',
                              'Choose or create a profile in Brain. Look Back shows the camera '
                              'memory kept for the active profile.')
        elif not hist['frames']:
            description = ('New frames are paused in Kids mode.' if not hist.get('recording_allowed', True) else
                           f"Frames appear here as Cammy records preview history for {hist['name']}.")
            self._placeholder('camera-photo-symbolic', 'Nothing kept yet', description)
        elif self._frame_error:
            self._placeholder('image-missing-symbolic', 'No frame here', self._frame_error)
        else:
            self._placeholder('camera-photo-symbolic', 'Loading frame…', '')

    def _placeholder(self, icon, title, description):
        self._empty_icon.set_from_icon_name(icon)
        self._empty_title.set_label(title)
        self._empty_description.set_label(description)
        self._empty_description.set_visible(bool(description))
