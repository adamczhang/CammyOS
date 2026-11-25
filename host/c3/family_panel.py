"""Family tab of the Control Center: Kids/Adult mode, Calendar access, and local play.

The mode follows Cammy's face: Pikachu and Fin and Fur are always Kids; the Cammy face is
Adult unless the Kids override is on. "Kids mode" sets that override (saved in C3) and
"Reset to automatic" clears it; neither needs a PIN.

Calendar access is the one PIN-gated control: the opt-in private Executor Calendar for the
selected Brain profile, in Adult mode only. The first PIN needs the setup code, which this panel
reads from private/c3/family/setup-code (0600, the desktop account only), so the API token
alone can neither set nor use the PIN. PINs are cleared from the entries after each attempt and
never stored by the panel.
"""
import threading
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from c3_client import C3Error  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SETUP_CODE = ROOT / 'private/c3/family/setup-code'
POLL_MS = 2000
PACKS = {'cammy': 'Cammy', 'pikachu': 'Pikachu', 'fin-fur': 'Fin and Fur', 'unknown': 'an unknown face'}
REASONS = {
    'started': 'waiting for Cammy to report its face',
    'cammy_face': 'Cammy face',
    'kids_face': 'Kids face',
    'unknown_face': 'unknown face',
    'kids_override': 'Kids mode set in CCC',
    'adult_hold': 'Adult hold',
    'face_selection': 'face is changing',
}
ACCESS = {
    'started': 'Locked',
    'pin': 'Open',
    'operator': 'Locked from this panel',
    'locked': 'Locked',
    'pin_changed': 'Locked: the PIN changed',
    'expired': 'Locked: the time ran out',
    'profile_changed': 'Locked: the Brain profile changed',
    'kids_mode': 'Locked: Kids mode',
    'family_changed': 'Locked: the family mode changed',
}


def mode_text(state):
    """The Mode row subtitle for a /v1/family state."""
    if state.get('deployment') != 'family':
        return 'Adult-only standalone deployment (no family mode)'
    adult = state.get('mode') == 'adult'
    text = '%s · %s' % ('Adult' if adult else 'Kids', REASONS.get(state.get('reason'), state.get('reason') or ''))
    return text + (' · automatic' if not state.get('kids_override') else '')


def face_text(state):
    """The Cammy row subtitle: the known face, and whether Cammy is answering now."""
    face = state.get('face') or {}
    pack = face.get('pack')
    if pack is None:
        return 'No face reported yet: Kids until Cammy reports one'
    text = 'Shows %s' % PACKS.get(pack, pack)
    if not face.get('available'):
        text += ' (last known; Cammy is not answering, so the mode is kept)'
    elif face.get('reported') != pack:
        text = 'Displays %s · %s selected' % (PACKS.get(face['reported'], face['reported']), PACKS.get(pack, pack))
    return text


def access_text(access, adult):
    if not access:
        return 'Not available in this C3'
    if access.get('open'):
        left = access.get('expires_in_s') or 0
        return 'Open for profile %s · %d min left' % (access.get('profile'), max(1, round(left / 60)))
    if not adult and not access.get('desktop'):
        return 'Locked: Kids mode'
    return ACCESS.get(access.get('reason'), 'Locked')


def _setup_code():
    try:
        return SETUP_CODE.read_text().strip()
    except OSError:
        return ''


class FamilyPanel(Gtk.Box):
    def __init__(self, client):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ('top', 'bottom', 'start', 'end'):
            self.set_property('margin-' + side, 14)
        self.client = client
        self.state = None
        self.access = None
        self.play = None
        self.busy = False
        self.polling = False

        mode = Adw.PreferencesGroup(title='Family mode',
                                     description='Pikachu and Fin and Fur are always Kids. The Cammy face is '
                                                 'Adult unless Kids mode is set here.')
        self.mode_row = Adw.ActionRow(title='Mode', subtitle='Checking…')
        self.device_row = Adw.ActionRow(title='Cammy', subtitle='')
        self.device_row.set_subtitle_lines(3)
        mode.add(self.mode_row)
        mode.add(self.device_row)
        self.append(mode)
        modes = Gtk.Box(spacing=8, homogeneous=True)
        self.kids_mode_button = Gtk.Button(label='Kids mode')
        self.kids_mode_button.connect('clicked', lambda *_: self._call('POST', '/v1/family/mode', {'mode': 'kids'}))
        self.automatic_button = Gtk.Button(label='Use face pack', tooltip_text='Reset the Kids override')
        self.automatic_button.connect('clicked',
                                      lambda *_: self._call('POST', '/v1/family/mode', {'mode': 'automatic'}))
        modes.append(self.kids_mode_button)
        modes.append(self.automatic_button)
        self.append(modes)
        faces = Gtk.Box(spacing=8, homogeneous=True)
        self.cammy_button = Gtk.Button(label='Cammy face', sensitive=False)
        self.kids_button = Gtk.Button(label='Kids face', sensitive=False)
        for button, pack in ((self.cammy_button, 'cammy'), (self.kids_button, 'child')):
            button.connect('clicked', lambda _button, pack=pack:
                           self._call('POST', '/v1/family/face-pack', {'id': pack}))
            faces.append(button)
        self.append(faces)
        self.message = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.message.add_css_class('dim-label')
        self.append(self.message)

        access = Adw.PreferencesGroup(title='Calendar access',
                                      description='Unlock the selected Brain profile’s private Calendar on this PC.')
        self.access_row = Adw.ActionRow(title='Private Calendar', subtitle='Checking…')
        self.access_row.set_subtitle_lines(2)
        access.add(self.access_row)
        self.pin = Adw.PasswordEntryRow(title='Calendar access PIN')
        self.pin.connect('entry-activated', self._unlock)
        access.add(self.pin)
        self.pin_expander = Adw.ExpanderRow(title='Set or change the Calendar access PIN', subtitle='6 to 12 digits')
        self.current_pin = Adw.PasswordEntryRow(title='Current PIN')
        self.new_pin = Adw.PasswordEntryRow(title='New PIN')
        self.again_pin = Adw.PasswordEntryRow(title='New PIN again')
        for row in (self.current_pin, self.new_pin, self.again_pin):
            self.pin_expander.add_row(row)
        save = Gtk.Button(label='Save PIN', halign=Gtk.Align.END, margin_top=6, margin_bottom=6, margin_end=6)
        save.connect('clicked', self._save_pin)
        save_row = Adw.PreferencesRow(activatable=False)
        save_row.set_child(save)
        self.pin_expander.add_row(save_row)
        access.add(self.pin_expander)
        self.append(access)
        buttons = Gtk.Box(spacing=8, homogeneous=True)
        self.unlock_button = Gtk.Button(label='Unlock', tooltip_text='Unlock Calendar access')
        self.unlock_button.add_css_class('suggested-action')
        self.unlock_button.connect('clicked', self._unlock)
        self.lock_button = Gtk.Button(label='Lock', tooltip_text='Lock Calendar access')
        self.lock_button.connect('clicked', lambda *_: self._call('POST', '/v1/calendar-access/lock', {}))
        buttons.append(self.unlock_button)
        buttons.append(self.lock_button)
        self.append(buttons)

        games = Adw.PreferencesGroup(title='Local play', description='Games on this PC only; nothing goes online.')
        self.games = Gtk.StringList.new([])
        self.game_ids = []
        self.game_row = Adw.ComboRow(title='Game', model=self.games)
        self.answer = Adw.EntryRow(title='Answer')
        self.answer.connect('entry-activated', self._answer)
        self.reply_row = Adw.ActionRow(title='Cammy says', subtitle='')
        self.reply_row.set_subtitle_lines(4)
        for row in (self.game_row, self.answer, self.reply_row):
            games.add(row)
        self.append(games)
        play_buttons = Gtk.Box(spacing=8, homogeneous=True)
        start = Gtk.Button(label='Start')
        start.connect('clicked', self._start)
        stop = Gtk.Button(label='Stop')
        stop.connect('clicked', lambda *_: self._call('POST', '/v1/play/stop', {}, play=True))
        play_buttons.append(start)
        play_buttons.append(stop)
        self.append(play_buttons)

        self.connect('map', lambda *_: self.refresh())
        GLib.timeout_add(POLL_MS, self.refresh)

    # ---- requests (off the GTK thread) ------------------------------------------------
    def _call(self, method, path, body=None, play=False, after=None):
        if self.busy:
            return
        self.busy = True

        def work():
            result, error = None, ''
            try:
                result = self.client.request(method, path, body)
            except C3Error as exc:
                error = str(exc)
            except (OSError, ValueError):
                error = 'C3 did not answer'
            GLib.idle_add(self._done, path, result, error, play, after)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, path, result, error, play, after):
        self.busy = False
        if play and isinstance(result, dict):
            self._show_play(result)
        elif isinstance(result, dict):
            if isinstance(result.get('calendar_access'), dict):
                self.access = result['calendar_access']
            elif path.startswith('/v1/calendar-access') and 'open' in result:
                self.access = result
            family = result if 'mode' in result else result.get('family')
            if isinstance(family, dict):
                self._show(family)
            elif self.state is not None:      # a Calendar access reply: redraw with it
                self._show({k: v for k, v in self.state.items() if k != 'calendar_access'})
        self.message.set_text(error)
        if after is not None:
            after(result, error)
        return False

    def refresh(self):
        if self.get_root() is None:       # the window is gone: stop polling
            return False
        if self.busy or self.polling or not self.get_mapped():
            return True
        self.polling = True

        def work():
            family = play = None
            try:
                family = self.client.request('GET', '/v1/family')
                play = self.client.request('GET', '/v1/play')
            except (C3Error, OSError, ValueError):
                pass
            GLib.idle_add(self._polled, family, play)
        threading.Thread(target=work, daemon=True).start()
        return True

    def _polled(self, family, play):
        self.polling = False
        if isinstance(family, dict):
            self._show(family)
        else:
            self.mode_row.set_subtitle('C3 is not answering (family mode unknown)')
            self.access_row.set_subtitle('C3 is not answering')
        if isinstance(play, dict):
            self._show_play(play)
        return False

    # ---- view -------------------------------------------------------------------------
    def _show(self, state):
        self.state = state
        if isinstance(state.get('calendar_access'), dict):
            self.access = state['calendar_access']
        family = state.get('deployment') == 'family'
        self.mode_row.set_subtitle(mode_text(state))
        if not family:
            for button in (self.kids_mode_button, self.automatic_button, self.cammy_button, self.kids_button,
                           self.unlock_button, self.lock_button):
                button.set_sensitive(False)
            return
        adult = state.get('mode') == 'adult'
        override = bool(state.get('kids_override'))
        self.device_row.set_subtitle(face_text(state))
        self.kids_mode_button.set_sensitive(not override)
        self.automatic_button.set_sensitive(override)
        face = state.get('face') or {}
        can_pick = bool(face.get('selectable')) and not state.get('selecting')
        self.cammy_button.set_sensitive(can_pick and face.get('reported') != 'cammy')
        self.kids_button.set_sensitive(can_pick and (face.get('pack') == 'cammy' or face.get('reported') == 'cammy'))
        access = self.access or {}
        self.access_row.set_subtitle(access_text(self.access, adult))
        limit = access.get('limit') or {}
        wait = limit.get('retry_after_s') or 0
        pin_set = bool(access.get('pin_set'))
        self.unlock_button.set_sensitive((adult or access.get('desktop') is True)
                                         and pin_set and wait <= 0 and not access.get('open'))
        self.lock_button.set_sensitive(bool(access.get('open')))
        self.current_pin.set_visible(pin_set)
        self.pin_expander.set_subtitle('6 to 12 digits' if pin_set else
                                       'No PIN yet: set one (uses the setup code on this PC)')
        if wait > 0:
            self.message.set_text('Too many wrong PIN attempts: wait %d s.' % round(wait))

    def _show_play(self, play):
        self.play = play
        ids = [g['id'] for g in play.get('games', [])]
        if ids != self.game_ids:
            self.game_ids = ids
            self.games.splice(0, self.games.get_n_items(), [g['name'] for g in play['games']])
        self.reply_row.set_subtitle(play.get('reply') or ('Pick a game and press Start' if not play.get('game') else ''))

    # ---- actions ----------------------------------------------------------------------
    def _unlock(self, *_):
        pin = self.pin.get_text()
        self.pin.set_text('')
        if pin:
            self._call('POST', '/v1/calendar-access/unlock', {'pin': pin})

    def _save_pin(self, *_):
        new, again, current = self.new_pin.get_text(), self.again_pin.get_text(), self.current_pin.get_text()
        for row in (self.new_pin, self.again_pin, self.current_pin):
            row.set_text('')
        if new != again:
            self.message.set_text('The new PINs do not match.')
            return
        body = {'new_pin': new}
        if (self.access or {}).get('pin_set'):
            body['current_pin'] = current
        else:
            body['setup_code'] = _setup_code()
        self._call('POST', '/v1/calendar-access/pin', body,
                   after=lambda result, error: error or self.message.set_text(
                       'PIN saved. Calendar access stays locked until you unlock it.'))

    def _start(self, *_):
        index = self.game_row.get_selected()
        if 0 <= index < len(self.game_ids):
            self._call('POST', '/v1/play/start', {'game': self.game_ids[index]}, play=True)

    def _answer(self, *_):
        text = self.answer.get_text().strip()
        self.answer.set_text('')
        if text:
            self._call('POST', '/v1/play/answer', {'text': text[:200]}, play=True)
