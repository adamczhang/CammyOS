"""Agent and Skills: edit the active profile's Gemini Live instructions from the Control Center.

Two GTK4 panels for two Control Center tabs share one `InstructionsStore(client)`:

- `AgentPanel(store)`: the profile's agent instructions (Markdown) plus the read-only list of
  built-in tools, whose descriptions come from Cammy's code and are not editable here.
- `SkillsPanel(store)`: pick a skill, edit its Markdown, or start a new one from a slug.

C3 API (all through the given `C3Client`, on background threads):

    GET /v1/instructions -> {profile, profile_name, agent: {text, revision},
                             skills: [{id, text, revision}], tools: [{name, description}]}
    PUT /v1/instructions/agent        {profile, text, revision}
    PUT /v1/instructions/skills/{id}  {profile, text, revision}     (revision null: new skill)

Writes return the updated snapshot. With no profile, `profile` is null and both panels are
disabled. A stale revision or a profile change is refused with 409.

Unsaved text is kept as a draft per (profile, document) in the store: it survives refreshes,
failed saves, switching skills or tabs, and profile changes (it comes back when that profile is
active again). Replies are applied in request order only, so an older snapshot never replaces a
newer one. Saved edits are read when Cammy next connects to Gemini Live; the conversation that
is already open keeps the instructions it started with.
"""
import re
import threading
import time
from copy import copy
from urllib.parse import quote

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from c3_client import C3Error  # noqa: E402

PATH = '/v1/instructions'
_TIMEOUT = 8            # seconds per request; the API is local
_FRESH_S = 5            # opening a tab refreshes when the last reply is older than this
_SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?')    # as c3/instructions.py SLUG
_SLUG_MAX = 48
APPLIES = 'When Gemini Live is enabled, saved changes apply on the next connection.'
AGENT = 'agent'
SKILL = 'skill'


# ---- reply parsing ------------------------------------------------------------------------

def _revision(value):
    if value is None or isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    raise ValueError('revision')


def _text(value):
    if not isinstance(value, str):
        raise ValueError('text')
    return value


def parse_snapshot(data):
    """Validate a GET/PUT reply. Raises ValueError on anything malformed."""
    if not isinstance(data, dict):
        raise ValueError('snapshot')
    profile = data.get('profile')
    if profile is not None and (not isinstance(profile, str) or not profile):
        raise ValueError('profile')
    name = data.get('profile_name')
    tools = []
    for tool in data.get('tools') or []:
        if not isinstance(tool, dict) or not isinstance(tool.get('name'), str):
            raise ValueError('tool')
        description = tool.get('description') or ''
        tools.append({'name': tool['name'], 'description': _text(description)})
    problems = []
    for problem in data.get('problems') or []:
        if isinstance(problem, dict) and isinstance(problem.get('error'), str):
            problems.append('%s: %s' % (problem.get('id') or '?', problem['error']))
    message = data.get('message')
    snap = {'profile': profile, 'profile_name': name if isinstance(name, str) and name else profile,
            'agent': None, 'skills': {}, 'tools': tools, 'problems': problems,
            'message': message if isinstance(message, str) else ''}
    if profile is None:
        return snap
    agent = data.get('agent') or {}
    if not isinstance(agent, dict):
        raise ValueError('agent')
    snap['agent'] = {'text': _text(agent.get('text') or ''), 'revision': _revision(agent.get('revision'))}
    for skill in data.get('skills') or []:
        if not isinstance(skill, dict) or not isinstance(skill.get('id'), str) or not skill['id']:
            raise ValueError('skill')
        snap['skills'][skill['id']] = {'text': _text(skill.get('text') or ''),
                                       'revision': _revision(skill.get('revision'))}
    return snap


def error_text(exc, writing):
    code = exc.code if isinstance(exc, C3Error) else None
    detail = str(exc).strip()[:160] if isinstance(exc, C3Error) else ''
    kept = ' Your draft is kept.' if writing else ''
    if code == 409 and not writing:
        return 'The active profile changed while loading. Reload to see the new profile.'
    if code == 409 and detail.startswith('saved to'):
        return ('Saved, but the active profile changed at the same moment. '
                'Showing the profile that is active now.')
    if code == 409:
        return ('Not saved: this text or the active profile changed in C3 since it was loaded'
                + (' (' + detail + ')' if detail else '') + '. Your draft is kept; showing what C3 has now.')
    if code in (404, 405, 501):
        return ('Agent and skill instructions are not available on this C3 service. '
                'Update and restart C3, then refresh.' + kept)
    if code in (401, 403):
        return 'C3 denied access. Check the local C3 API token configuration.' + kept
    if code == 503:
        return ('Not saved: ' if writing else '') + 'C3 is not running or could not read the profile ' \
            'instructions. Check the service and its log.' + kept
    if code in (400, 413, 422):
        return 'Not saved: C3 refused the text' + (': ' + detail if detail else '.') + kept
    if code is not None:
        return f'C3 returned HTTP {code}.' + kept
    return 'C3 returned an unreadable instructions reply. Check that C3 is up to date.' + kept


def _thread_run(work, done):
    """Run work() on a daemon thread; done(result, exc) on the GTK main loop."""
    def body():
        try:
            result = work()
        except Exception as exc:     # noqa: BLE001 - reported in the panel, never raised in GTK
            GLib.idle_add(lambda error=exc: done(None, error) and False)
        else:
            GLib.idle_add(lambda: done(result, None) and False)
    threading.Thread(target=body, name='instructions', daemon=True).start()


# ---- shared state -------------------------------------------------------------------------

class Draft:
    __slots__ = ('text', 'base', 'new')

    def __init__(self, text, base, new=False):
        self.text, self.base, self.new = text, base, new


class InstructionsStore(GObject.Object):
    """Snapshot, drafts and requests shared by the Agent and Skills tabs. GTK thread only."""

    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, client, run=None):
        super().__init__()
        self.client = copy(client)
        self.client.timeout = min(client.timeout or _TIMEOUT, _TIMEOUT)
        self.run = run or _thread_run
        self.snapshot = None           # last applied, parsed reply
        self.error = ''                # last load error ('' once a load succeeds)
        self.loading = False
        self.again = False             # refresh wanted after the current request
        self.saving = None             # draft key being written
        self.seq = 0                   # request counter
        self.applied = 0               # seq of the reply on screen; older replies are dropped
        self.fetched_at = 0.0
        self.drafts = {}               # (profile, kind, id) -> Draft
        self.notices = {}              # (profile, kind, id) -> (message, is_error)
        self.selected = {}             # profile -> selected skill id
        self.retried = False           # one automatic reload after a read raced a profile switch

    # -- reading --
    @property
    def profile(self):
        return (self.snapshot or {}).get('profile')

    def saved(self, key):
        """(text, revision) C3 holds for key, or None if it has no such document."""
        profile, kind, ident = key
        snap = self.snapshot
        if not snap or snap['profile'] != profile:
            return None
        doc = snap['agent'] if kind == AGENT else snap['skills'].get(ident)
        return (doc['text'], doc['revision']) if doc else None

    def stale(self, key):
        """True when C3's copy moved on since the draft was started (a save would be refused)."""
        draft = self.drafts.get(key)
        saved = self.saved(key)
        if draft is None or saved is None:
            return False
        return draft.new or saved[1] != draft.base

    def skill_ids(self, profile):
        ids = list((self.snapshot or {}).get('skills', {})) if profile == self.profile else []
        ids += sorted(k[2] for k in self.drafts if k[0] == profile and k[1] == SKILL and k[2] not in ids)
        return ids

    # -- editing --
    def edit(self, key, text):
        """Record the editor's text for key; drop the draft once it matches C3 again."""
        draft = self.drafts.get(key)
        saved = self.saved(key)
        if draft is not None and draft.new:
            draft.text = text
        elif saved is not None and draft is None and text != saved[0]:
            self.drafts[key] = Draft(text, saved[1])
        elif draft is not None:
            if saved is not None and text == saved[0] and saved[1] == draft.base:
                del self.drafts[key]
            else:
                draft.text = text
        else:
            return
        self.notices.pop(key, None)
        self.emit('changed')

    def discard(self, key):
        self.drafts.pop(key, None)
        self.notices.pop(key, None)
        self.emit('changed')

    def new_skill(self, profile, ident):
        key = (profile, SKILL, ident)
        title = ident.replace('-', ' ').capitalize()
        self.drafts[key] = Draft(f'# {title}\n\n', None, new=True)
        self.selected[profile] = ident
        self.notices.pop(key, None)
        self.emit('changed')
        return key

    def slug_problem(self, profile, ident):
        if not ident:
            return 'Enter an ID.'
        if len(ident) > _SLUG_MAX or not _SLUG.fullmatch(ident):
            return 'Use lowercase letters, digits and hyphens, starting and ending with a letter or digit (at most 48).'
        if ident in self.skill_ids(profile):
            return 'A skill with this ID already exists.'
        return ''

    # -- requests --
    def refresh(self, if_older_than=None):
        if if_older_than is not None and self.snapshot and time.monotonic() - self.fetched_at < if_older_than:
            return
        if self.loading or self.saving:
            self.again = True
            return
        self.loading = True
        self.again = False
        self.seq += 1
        seq = self.seq
        self.emit('changed')
        self.run(lambda: parse_snapshot(self.client.request('GET', PATH)),
                 lambda snap, exc: self._loaded(seq, snap, exc))

    def _loaded(self, seq, snap, exc):
        self.loading = False
        if seq > self.applied:
            if exc is None:
                self._apply(seq, snap)
                self.retried = False
            else:
                self.error = error_text(exc, writing=False)
                if isinstance(exc, C3Error) and exc.code == 409 and not self.retried:
                    self.retried = self.again = True
        self._next()
        self.emit('changed')

    def save(self, key):
        draft = self.drafts.get(key)
        profile, kind, ident = key
        if draft is None or self.saving or profile is None or profile != self.profile:
            return False
        saved = self.saved(key)
        # A stale draft is only saved over C3's newer copy when the panel says so (the Save
        # button reads "Replace saved version"); otherwise base is the revision it started from.
        revision = saved[1] if saved is not None and self.stale(key) else (None if draft.new else draft.base)
        text = draft.text
        path = PATH + '/agent' if kind == AGENT else PATH + '/skills/' + quote(ident, safe='')
        body = {'profile': profile, 'text': text, 'revision': revision}
        self.saving = key
        self.seq += 1
        seq = self.seq
        self.notices[key] = ('Saving…', False)
        self.emit('changed')
        self.run(lambda: parse_snapshot(self.client.request('PUT', path, body)),
                 lambda snap, exc: self._saved(seq, key, text, snap, exc))
        return True

    def _saved(self, seq, key, text, snap, exc):
        self.saving = None
        if exc is None:
            if seq > self.applied:
                self._apply(seq, snap)
            draft = self.drafts.get(key)
            now = self.saved(key)
            if draft is not None and now is not None:
                if draft.text == text:
                    del self.drafts[key]
                else:                  # typed while saving: keep the newer text on the new revision
                    draft.base, draft.new = now[1], False
            self.notices[key] = ('Saved. ' + APPLIES, False)
        else:
            self.notices[key] = (error_text(exc, writing=True), True)
            if isinstance(exc, C3Error) and exc.code == 409:
                self.again = True
        self._next()
        self.emit('changed')

    def _apply(self, seq, snap):
        self.applied = seq
        self.snapshot = snap
        self.error = ''
        self.fetched_at = time.monotonic()
        # A draft whose text C3 now holds is no longer unsaved (saved here or elsewhere).
        for key in [k for k in self.drafts if k[0] == snap['profile'] and k != self.saving]:
            saved = self.saved(key)
            if saved is not None and saved[0] == self.drafts[key].text:
                del self.drafts[key]

    def _next(self):
        if self.again and not self.loading and not self.saving:
            GLib.idle_add(lambda: self.refresh() and False)
            self.again = False


# ---- views ----------------------------------------------------------------------------------

def _label(css=None, **kwargs):
    label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, **kwargs)
    for c in (css or '').split():
        label.add_css_class(c)
    return label


class _EditorPanel(Gtk.Box):
    """Common layout: header, explanation, [selector], editor, status line, actions."""

    kind = AGENT
    title = ''
    intro = ''
    doc_label = ''

    def __init__(self, store):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12,
                         margin_bottom=12, margin_start=14, margin_end=14)
        self.store = store
        self._quiet = False           # guards programmatic buffer / selector changes
        self._shown = None            # key whose text is in the buffer

        head = Gtk.Box(spacing=8)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        heading = _label('heading', label=self.title)
        titles.append(heading)
        self.profile_label = _label('caption dim-label')
        titles.append(self.profile_label)
        head.append(titles)
        self.reload = Gtk.Button(icon_name='view-refresh-symbolic', valign=Gtk.Align.START,
                                 tooltip_text='Reload from C3 (unsaved drafts are kept)')
        self.reload.update_property([Gtk.AccessibleProperty.LABEL], ['Reload from C3'])
        self.reload.add_css_class('flat')
        self.reload.connect('clicked', lambda *_: store.refresh())
        head.append(self.reload)
        self.append(head)
        self.append(_label('caption dim-label', label=self.intro + ' ' + APPLIES))
        self.problems = _label('caption warning', visible=False, selectable=True)
        self.append(self.problems)
        self._build_top()

        self.pages = Gtk.Stack(vexpand=True, vhomogeneous=False)
        self.message = Adw.StatusPage(icon_name='document-edit-symbolic', vexpand=True)
        self.message.add_css_class('compact')
        self.pages.add_named(self.message, 'message')
        editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8,
                                 bottom_margin=8, left_margin=8, right_margin=8, accepts_tab=False)
        self.view.update_property([Gtk.AccessibleProperty.LABEL], [self.doc_label])
        self.view.get_buffer().connect('changed', self._typed)
        scroll = Gtk.ScrolledWindow(vexpand=True, has_frame=True, min_content_height=120,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.view)
        editor_box.append(scroll)
        self.status = _label('caption')
        editor_box.append(self.status)
        self.saved_version = Gtk.Expander(label='View saved version')
        self.saved_text = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                      wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8)
        self.saved_version.set_child(Gtk.ScrolledWindow(min_content_height=95,
                                                        hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                        child=self.saved_text))
        editor_box.append(self.saved_version)
        # Wraps instead of overflowing when both buttons do not fit a narrow window.
        actions = Adw.WrapBox(child_spacing=8, line_spacing=6, align=1.0)
        self.discard = Gtk.Button(label='Discard draft',
                                  tooltip_text='Drop unsaved changes and show the version saved in C3')
        self.discard.connect('clicked', lambda *_: self._discard())
        self.save = Gtk.Button(label='Save')
        self.save.add_css_class('suggested-action')
        self.save.connect('clicked', lambda *_: self.key() and store.save(self.key()))
        actions.append(self.discard)
        actions.append(self.save)
        editor_box.append(actions)
        self.pages.add_named(editor_box, 'editor')
        self.append(self.pages)
        self._build_bottom()

        self.connect('map', lambda *_: store.refresh(if_older_than=_FRESH_S))
        store.connect('changed', lambda *_: self.sync())
        self.sync()

    def _build_top(self):
        pass

    def _build_bottom(self):
        pass

    def key(self):
        profile = self.store.profile
        return (profile, AGENT, '') if profile else None

    def _typed(self, buf):
        if self._quiet or self._shown is None:
            return
        self.store.edit(self._shown, buf.get_text(*buf.get_bounds(), False))

    def _discard(self):
        if self.key():
            self.store.discard(self.key())

    def _set_text(self, text):
        buf = self.view.get_buffer()
        if buf.get_text(*buf.get_bounds(), False) != text:
            self._quiet = True
            buf.set_text(text)
            buf.place_cursor(buf.get_start_iter())
            self._quiet = False

    def _empty(self, title, description, icon='document-edit-symbolic'):
        self.message.set_title(title)
        self.message.set_description(description)
        self.message.set_icon_name(icon)
        self.pages.set_visible_child_name('message')
        self._shown = None

    def sync(self):
        store = self.store
        snap = store.snapshot
        self.reload.set_sensitive(not store.loading)
        self.problems.set_visible(bool(snap and snap['profile'] and snap['problems']))
        if snap is None:
            self.profile_label.set_label('')
            if store.error:
                self._empty('Instructions unavailable', store.error, 'dialog-warning-symbolic')
            else:
                self._empty('Loading instructions…', 'Reading the active profile from C3.')
            return
        if snap['profile'] is None:
            self.profile_label.set_label('No profile selected')
            self._empty('No profile selected', snap['message'] or
                        'Select or create a profile in the Brain tab to edit its agent and skills.')
            return
        self.profile_label.set_label('Profile: ' + (snap['profile_name'] or snap['profile']))
        self.problems.set_label('C3 is not using: ' + '; '.join(snap['problems']) if snap['problems'] else '')
        self.problems.set_visible(bool(snap['problems']))
        self._sync_doc()

    def _sync_doc(self):
        store = self.store
        key = self.key()
        if key is None:
            return
        self.pages.set_visible_child_name('editor')
        draft = store.drafts.get(key)
        saved = store.saved(key)
        if self._shown != key:
            self.saved_version.set_expanded(False)
        self._shown = key
        self._set_text(draft.text if draft is not None else saved[0] if saved else '')
        saving = store.saving is not None
        stale = store.stale(key)
        self.saved_version.set_visible(stale and saved is not None)
        if stale and saved is not None:
            buf = self.saved_text.get_buffer()
            if buf.get_text(*buf.get_bounds(), False) != saved[0]:
                buf.set_text(saved[0])
        else:
            self.saved_version.set_expanded(False)
        self.view.set_editable(store.saving != key)
        self.save.set_label('Replace saved version' if stale else 'Save')
        self.save.set_sensitive(draft is not None and not saving and store.profile == key[0]
                                and not (draft.new and not draft.text.strip()))
        self.discard.set_visible(draft is not None)
        self.discard.set_sensitive(store.saving != key)
        notice, is_error = store.notices.get(key, ('', False))
        if store.error and not notice:
            notice, is_error = store.error, True
        if stale and store.saving != key:
            changed = ('A skill with this ID now exists in C3.' if draft.new
                       else 'This changed in C3 since you started editing.')
            notice = (notice + ' ' if is_error and notice else '') + changed + \
                ' Your draft is kept. Review the saved version before replacing it.'
            is_error = True
        elif not notice and draft is not None:
            notice = 'New skill, not saved yet.' if draft.new else 'Unsaved changes.'
        self.status.set_label(notice)
        self.status.set_visible(bool(notice))
        for css in ('error', 'dim-label'):
            self.status.remove_css_class(css)
        self.status.add_css_class('error' if is_error else 'dim-label')


class AgentPanel(_EditorPanel):
    kind = AGENT
    title = 'Agent instructions'
    intro = 'The profile’s Markdown instructions for Cammy’s Gemini Live agent.'
    doc_label = 'Agent instructions'

    def _build_bottom(self):
        self.tools = Gtk.Expander(label='Built-in tools')
        self.tools.update_property([Gtk.AccessibleProperty.LABEL], ['Built-in tools'])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        box.append(_label('caption dim-label', label=(
            'Built into Cammy’s code, not instructions: Gemini sees these names and descriptions '
            'as written here and they cannot be edited. Your instructions can say when to use them.')))
        self.tool_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.tool_list)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                    max_content_height=220, child=box)
        self.tools.set_child(scroll)
        self.append(self.tools)
        self._tool_rows = None

    def sync(self):
        super().sync()
        tools = (self.store.snapshot or {}).get('tools') or []
        self.tools.set_visible(bool(tools))
        self.tools.set_label(f'Built-in tools ({len(tools)})')
        if tools == self._tool_rows:
            return
        self._tool_rows = tools
        while child := self.tool_list.get_first_child():
            self.tool_list.remove(child)
        for tool in tools:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            name = _label('monospace heading', label=tool['name'], selectable=True)
            row.append(name)
            row.append(_label('dim-label', label=tool['description'] or 'No description.', selectable=True))
            self.tool_list.append(row)


class SkillsPanel(_EditorPanel):
    kind = SKILL
    title = 'Skills'
    intro = 'Markdown instructions Cammy can follow for particular tasks; they are not code tools.'
    doc_label = 'Skill text'

    def _build_top(self):
        row = Gtk.Box(spacing=8)
        self.ids = []
        self.picker = Gtk.DropDown(hexpand=True, model=Gtk.StringList.new([]))
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', lambda _, item: item.set_child(Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)))
        factory.connect('bind', lambda _, item: item.get_child().set_label(item.get_item().get_string()))
        self.picker.set_factory(factory)
        self.picker.update_property([Gtk.AccessibleProperty.LABEL], ['Skill'])
        self.picker.connect('notify::selected', self._picked)
        self.add_skill = Gtk.Button(label='New skill', tooltip_text='Start a new skill from an ID (slug)')
        self.add_skill.connect('clicked', lambda *_: self._ask_new())
        row.append(self.picker)
        row.append(self.add_skill)
        self.picker_row = row
        self.append(row)

    def key(self):
        profile = self.store.profile
        ident = self.store.selected.get(profile)
        return (profile, SKILL, ident) if profile and ident else None

    def _labels(self, profile):
        out = []
        for ident in self.ids:
            key = (profile, SKILL, ident)
            draft = self.store.drafts.get(key)
            mark = ' (new)' if draft and draft.new else ' (edited)' if draft else ''
            if draft and not draft.new and self.store.saved(key) is None:
                mark = ' (not in C3)'
            out.append(ident + mark)
        return out

    def sync(self):
        store = self.store
        profile = store.profile
        ready = store.snapshot is not None and profile is not None
        self.picker_row.set_sensitive(ready)
        self.ids = store.skill_ids(profile) if ready else []
        if ready and store.selected.get(profile) not in self.ids:
            if self.ids:
                store.selected[profile] = self.ids[0]
            else:
                store.selected.pop(profile, None)
        labels = self._labels(profile) if ready else []
        model = self.picker.get_model()
        current = [model.get_string(i) for i in range(model.get_n_items())]
        self._quiet = True
        if labels != current:
            model.splice(0, len(current), labels)
        want = self.ids.index(store.selected[profile]) if ready and profile in store.selected else None
        self.picker.set_selected(Gtk.INVALID_LIST_POSITION if want is None else want)
        self._quiet = False
        self.picker.set_visible(bool(self.ids))
        super().sync()
        if ready and not self.ids:
            self._empty('No skills yet', 'Choose New skill to write one for this profile.')

    def _picked(self, *_):
        if self._quiet:
            return
        index = self.picker.get_selected()
        if 0 <= index < len(self.ids) and self.store.profile:
            self.store.selected[self.store.profile] = self.ids[index]
            self.sync()

    def _ask_new(self):
        profile = self.store.profile
        if not profile:
            return
        dialog = Adw.AlertDialog(heading='New skill',
                                 body='The ID names the skill file. Use lowercase letters, digits and hyphens.')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        entry = Gtk.Entry(placeholder_text='e.g. tidy-desk', max_length=_SLUG_MAX, activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ['Skill ID'])
        problem = _label('caption error', visible=False)
        box.append(entry)
        box.append(problem)
        dialog.set_extra_child(box)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('create', 'Create')
        dialog.set_response_appearance('create', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('create')
        dialog.set_close_response('cancel')

        def changed(*_):
            text = entry.get_text().strip()
            issue = self.store.slug_problem(profile, text)
            dialog.set_response_enabled('create', not issue)
            problem.set_label(issue if text else '')
            problem.set_visible(bool(text and issue))

        def responded(_, response):
            ident = entry.get_text().strip()
            if response == 'create' and not self.store.slug_problem(profile, ident) and profile == self.store.profile:
                self.store.new_skill(profile, ident)
                self.view.grab_focus()

        entry.connect('changed', changed)
        dialog.connect('response', responded)
        changed()
        dialog.present(self.get_root())
        self._dialog = dialog
