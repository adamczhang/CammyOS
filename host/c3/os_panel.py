"""GTK maintenance controls running the same bounded engine as the local CLI."""

import threading

from gi.repository import Adw, GLib, Gtk

from .os_router import Lifecycle, Request, public_error
from tools.cammy_lifecycle import ACTIONS, RECOVERY, approval_summary


LABELS = ('First device', 'Install / update', 'Repair this release', 'Roll back checked boot files')


class OsPanel(Gtk.ScrolledWindow):
    def __init__(self, source, lifecycle=None):
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.source = source
        self.lifecycle = lifecycle if lifecycle is not None else Lifecycle()
        self.busy = False
        self.proposal = None
        self.catalog = {'artifacts': [], 'receipts': []}
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=14,
                               margin_bottom=14, margin_start=14, margin_end=14)
        self.set_child(self.content)
        self.status = self._note('Running and installed versions are unconfirmed. Refresh or inspect USB.')
        self._row('Running and installed', self.status)

        controls = Gtk.Box(spacing=8)
        self.refresh = self._button(controls, 'Refresh', self._refresh)
        self.inspect = self._button(controls, 'Inspect recovery USB', self._inspect)
        self.content.append(controls)

        guide = Gtk.Expander(label='Install and recovery checklist')
        guide.set_child(self._note(
            '1. Inspect authorized USB before selecting an action. If the shell times out, reconnect the USB cable '
            'while the camera stays powered on and inspect again.\n'
            '2. Select a validated release and the matching installed receipt. Prepare the action and review the '
            'boot, card and rollback. Internal boot backups and microSD releases use separate storage.\n'
            '3. After a verified install, power the camera off and on physically and choose CammyOS once. '
            'Keep it on after Loading clears so release health can finish. Check the new boot, Wi-Fi and CCC preview.'
        ))
        self.content.append(guide)

        self.action = Gtk.DropDown.new_from_strings(LABELS)
        self.artifact = Gtk.DropDown.new_from_strings([])
        self.receipt = Gtk.DropDown.new_from_strings([])
        for title, selector in (('Action', self.action), ('Local candidate', self.artifact),
                                ('Installed or transaction receipt', self.receipt)):
            self._row(title, selector)
            selector.connect('notify::selected', self._selection_changed)

        choices = Gtk.Box(spacing=8)
        self.load = self._button(choices, 'Brain request', self._load)
        self.load.set_tooltip_text('Load a firmware request from Brain')
        self.prepare = self._button(choices, 'Prepare action', self._prepare)
        self.prepare.add_css_class('suggested-action')
        self.content.append(choices)
        self.plan = self._note(RECOVERY)
        self.content.append(self.plan)
        self.approval = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False)
        self.key = Adw.PasswordEntryRow(title='Local maintenance passphrase')
        credentials = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        credentials.add_css_class('boxed-list')
        credentials.append(self.key)
        self.approval.append(credentials)
        self.apply = self._button(self.approval, 'Approve exact action and run', self._apply)
        self.apply.add_css_class('destructive-action')
        self.content.append(self.approval)
        self.progress = self._note('')
        self.content.append(self.progress)
        self.connect('map', self._on_map)
        self.connect('unmap', self._selection_changed)
        self._controls()

    @staticmethod
    def _note(text):
        return Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)

    @staticmethod
    def _button(box, label, callback):
        button = Gtk.Button(label=label)
        button.connect('clicked', callback)
        box.append(button)
        return button

    def _row(self, title, child):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class('heading')
        box.append(label)
        box.append(child)
        self.content.append(box)

    @staticmethod
    def _selected(dropdown):
        item = dropdown.get_selected_item()
        return item.get_string() if item is not None else None

    @staticmethod
    def _set_options(dropdown, items, chosen):
        dropdown.set_model(Gtk.StringList.new(items))
        if chosen in items:
            dropdown.set_selected(items.index(chosen))

    def _selection_changed(self, *_):
        self.proposal = None
        self.key.set_text('')
        self.plan.set_label(RECOVERY)
        self._controls()

    def _controls(self):
        ready = not self.busy
        first = self.action.get_selected() == 0
        for widget in (self.refresh, self.inspect, self.action, self.artifact, self.load):
            widget.set_sensitive(ready)
        self.receipt.set_sensitive(ready and not first)
        self.prepare.set_sensitive(ready and bool(self._selected(self.artifact)) and
                                   (first or bool(self._selected(self.receipt))))
        self.approval.set_visible(ready and self.proposal is not None)

    def _work(self, label, operation, finished, *, writing=False):
        if self.busy:
            return
        self.busy = True
        self.progress.set_label(label)
        self._controls()

        def complete(result, problem):
            self.busy = False
            if problem:
                self.progress.set_label(problem)
            else:
                self.progress.set_label('')
                finished(result)
            self._controls()
            return GLib.SOURCE_REMOVE

        def update(message):
            self.progress.set_label(message)
            return GLib.SOURCE_REMOVE

        def run():
            try:
                result = operation(lambda message: GLib.idle_add(update, message))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, SystemExit) as exc:
                GLib.idle_add(complete, None, public_error(exc))
            else:
                GLib.idle_add(complete, result, None)

        threading.Thread(target=run, daemon=not writing).start()

    def _on_map(self, *_):
        self._refresh()

    def _refresh(self, *_):
        self._read_status(False)

    def _inspect(self, *_):
        self._read_status(True)

    def _read_status(self, usb):
        if self.busy:
            return
        self._selection_changed()
        portal = self.source.snapshot()[1]
        artifact = self._selected(self.artifact)
        self._work('Inspecting authorized USB…' if usb else 'Reading local and portal status…',
                   lambda _: self.lifecycle.status(portal=portal, usb=usb, artifact=artifact),
                   self._display)

    def _display(self, result):
        if self.catalog != result['local']:
            self.catalog = result['local']
            self._set_options(self.artifact, self.catalog['artifacts'], self._selected(self.artifact))
            self._set_options(self.receipt, self.catalog['receipts'], self._selected(self.receipt))
        running, installed, staged, usb = (result[k] for k in ('running', 'installed', 'on_tf', 'usb'))
        lines = []
        if running:
            lines += [f"Running version {running['version'] or 'unconfirmed'}",
                      f"Running release {running['release'] or 'unconfirmed'}",
                      f"Boot {running['boot_id']} ({running['source']})"]
        else:
            lines.append('Running version, release and boot unconfirmed')
        if installed:
            lines.append(f"Installed release {installed['release']} · selector {installed['selector']}")
            lines.append(f"Matched receipt {installed['receipt']} from boot {installed['receipt_boot']}")
        else:
            lines.append('Installed release unconfirmed by readback')
        candidate = result.get('candidate')
        if candidate:
            if 'release' in candidate:
                lines.append(f"Reviewed local candidate {candidate['version']} · {candidate['release']}")
            else:
                lines.append('Selected local candidate review failed. ' + candidate['detail'])
        lines.append(f"Selected TF release {staged['release']} · {staged['state']}" if staged
                     else 'Selected TF slot unconfirmed')
        if usb:
            if usb.get('detail'):
                lines.append(usb['detail'])
            if usb.get('rom_fel_visible'):
                lines.append('Allwinner ROM FEL visible. Stop at inspection.')
            if usb.get('authorized'):
                lines.append(f"USB {usb['model']} · {usb['usb_port']} · boot {usb['boot_id']}")
                lines.append(usb.get('baseline', 'Boot files unconfirmed'))
                if usb.get('tf_mount'):
                    lines.append(usb['tf_mount'])
                if usb.get('guard_or_authority'):
                    lines.append('Guard or authority present · ' + ', '.join(usb['guard_or_authority']))
                if usb.get('missing_setup'):
                    lines.append('Separate setup missing · ' + ', '.join(usb['missing_setup']))
        self.status.set_label('\n'.join(lines))

    def _load(self, *_):
        def loaded(request):
            if request is None:
                self.progress.set_label('No Brain firmware request is waiting.')
                return
            self.action.set_selected(ACTIONS.index(request.action))
            self._set_options(self.artifact, self.catalog['artifacts'], request.artifact)
            if request.receipt:
                self._set_options(self.receipt, self.catalog['receipts'], request.receipt)
            if (self._selected(self.artifact) != request.artifact
                    or (request.receipt and self._selected(self.receipt) != request.receipt)):
                self.progress.set_label('Refresh the catalog and load the request again.')
            else:
                self.progress.set_label('Brain request loaded. Prepare it to check the current USB device.')

        self._work('Reading Brain request…', lambda _: self.lifecycle.queued(), loaded)

    def _prepare(self, *_):
        self._selection_changed()
        action = ACTIONS[self.action.get_selected()]
        artifact = self._selected(self.artifact)
        receipt = self._selected(self.receipt) if action != 'first-device' else None

        def prepared(proposal):
            if not self.get_mapped():
                return
            self.proposal = proposal
            data = approval_summary(proposal)
            device = data['device']
            need = [key for key, kind in data['setup'].items() if kind != 'file']
            text = [f"{LABELS[ACTIONS.index(data['action'])]} · {data['artifact']}",
                    f"Version {data['version']} · release {data['release']}",
                    f"Manifest SHA-256 {data['manifest_sha256']}",
                    f"{device['model']} · USB {device['usb_port']} · serial {device['serial']}",
                    f"Boot {device['boot_id']} · transport {device['transport']}",
                    f"TF {data['staged']}"]
            if data['tf']:
                text.append(f"TF card {data['tf'][0]} · CID {data['tf'][1]}")
            if data['receipt']:
                text.append(f"Receipt {data['receipt']}")
            if need:
                text.append('Complete separate setup for ' + ', '.join(need) + ' using the owner procedure.')
            self.plan.set_label('\n'.join(text))

        self._work('Preparing a read-only USB preflight…',
                   lambda progress: self.lifecycle.prepare(Request(action, artifact, receipt), progress), prepared)

    def _apply(self, *_):
        if self.busy or self.proposal is None:
            return
        proposal = self.proposal
        key = self.key.get_text()
        self.key.set_text('')
        self.proposal = None
        self._work('Rechecking local approval…',
                   lambda progress: self.lifecycle.apply(proposal, key, progress),
                   lambda result: self.plan.set_label(f"Verified {result['job']}. {result['next']}"),
                   writing=True)
