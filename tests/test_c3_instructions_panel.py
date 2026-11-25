"""Agent and Skills tabs in the Control Center: store rules, panels and channel navigation.

GTK tests need a display: run them under an isolated Xvfb (xvfb-run), never on a desktop.
No sockets: a fake client answers, and a manual runner decides when each reply arrives.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from c3_client import C3Client, C3Error  # noqa: E402

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import GLib, Gtk  # noqa: E402
    from c3 import gui  # noqa: E402
    from c3 import instructions_panel as ip  # noqa: E402
    DISPLAY = Gtk.init_check()
except (ImportError, ValueError):
    gui = ip = None
    DISPLAY = False
needs_gi = unittest.skipUnless(ip, 'GTK bindings not installed (use the system python3)')
needs_display = unittest.skipUnless(DISPLAY, 'no display: run under xvfb-run')


def snapshot(profile='desk', agent='# Agent\n', agent_rev='a1', skills=(('tidy', 'Tidy text', 'r1'),)):
    return {'profile': profile, 'profile_name': 'Desk helper' if profile == 'desk' else profile,
            'agent': {'text': agent, 'revision': agent_rev},
            'skills': [{'id': i, 'text': t, 'revision': r} for i, t, r in skills],
            'tools': [{'name': 'look', 'description': 'Take a photo.'}]}


class FakeClient(C3Client):
    """Records requests; replies come from `replies` (a dict or an exception per path)."""

    def __init__(self):
        super().__init__(token_file=Path('/nonexistent'), timeout=3)
        self.calls = []
        self.reply = snapshot()
        self.errors = {}

    def exchange(self, method, path, body=None, raw=False):
        self.calls.append((method, path, copy.deepcopy(body)))
        if (method, path) in self.errors:
            raise self.errors[(method, path)]
        return copy.deepcopy(self.reply), {}


class Runner:
    """Holds each request until the test lets it finish, in any order."""

    def __init__(self):
        self.pending = []

    def __call__(self, work, done):
        self.pending.append((work, done))

    def finish(self, index=0):
        work, done = self.pending.pop(index)
        try:
            result = work()
        except Exception as exc:      # noqa: BLE001
            done(None, exc)
        else:
            done(result, None)

    def drain(self):
        while self.pending:
            self.finish()


def make_store():
    client = FakeClient()
    runner = Runner()
    store = ip.InstructionsStore(client, run=runner)
    store.client = client           # the store copies the client; follow the original fake
    return store, client, runner


def spin():
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


@needs_gi
class Snapshots(unittest.TestCase):
    def test_parse(self):
        snap = ip.parse_snapshot(snapshot())
        self.assertEqual(snap['agent'], {'text': '# Agent\n', 'revision': 'a1'})
        self.assertEqual(snap['skills']['tidy']['revision'], 'r1')
        self.assertEqual(snap['tools'], [{'name': 'look', 'description': 'Take a photo.'}])
        empty = ip.parse_snapshot({'profile': None, 'profile_name': None, 'tools': []})
        self.assertIsNone(empty['profile'])
        self.assertIsNone(empty['agent'])

    def test_malformed_is_refused(self):
        for bad in ([], {'profile': 3}, {'profile': 'p', 'agent': {'text': 5}},
                    {'profile': 'p', 'skills': [{'text': 'x'}]}, {'profile': 'p', 'tools': [{'description': 'x'}]},
                    {'profile': 'p', 'agent': {'text': '', 'revision': 1.5}}):
            with self.assertRaises(ValueError):
                ip.parse_snapshot(bad)


@needs_gi
class Store(unittest.TestCase):
    def setUp(self):
        self.store, self.client, self.runner = make_store()
        self.store.refresh()
        self.runner.finish()
        self.agent = ('desk', ip.AGENT, '')

    def test_save_sends_profile_text_and_base_revision(self):
        self.store.edit(self.agent, '# Agent\nBe brief.\n')
        self.client.reply = snapshot(agent='# Agent\nBe brief.\n', agent_rev='a2')
        self.assertTrue(self.store.save(self.agent))
        self.runner.finish()
        self.assertEqual(self.client.calls[-1], ('PUT', '/v1/instructions/agent',
                                                 {'profile': 'desk', 'text': '# Agent\nBe brief.\n', 'revision': 'a1'}))
        self.assertNotIn(self.agent, self.store.drafts)
        self.assertEqual(self.store.saved(self.agent), ('# Agent\nBe brief.\n', 'a2'))
        self.assertIn('saved changes apply on the next connection', self.store.notices[self.agent][0])

    def test_editing_back_to_saved_text_drops_the_draft(self):
        self.store.edit(self.agent, 'x')
        self.assertIn(self.agent, self.store.drafts)
        self.store.edit(self.agent, '# Agent\n')
        self.assertNotIn(self.agent, self.store.drafts)

    def test_older_reply_never_replaces_newer(self):
        self.store.refresh()                                   # request 2, still out
        self.store.edit(self.agent, 'mine')
        self.client.reply = snapshot(agent='mine', agent_rev='a2')
        self.store.save(self.agent)                            # request 3
        self.runner.finish(1)                                  # the save lands first
        self.client.reply = snapshot(agent='# Agent\n', agent_rev='a1')
        self.runner.finish(0)                                  # then the older refresh
        self.assertEqual(self.store.saved(self.agent), ('mine', 'a2'))

    def test_failed_save_keeps_the_draft(self):
        self.store.edit(self.agent, 'draft')
        self.client.errors[('PUT', '/v1/instructions/agent')] = C3Error(503, 'down')
        self.store.save(self.agent)
        self.runner.finish()
        self.assertEqual(self.store.drafts[self.agent].text, 'draft')
        message, is_error = self.store.notices[self.agent]
        self.assertTrue(is_error)
        self.assertIn('draft is kept', message)

    def test_conflict_keeps_draft_reloads_and_offers_replace(self):
        self.store.edit(self.agent, 'mine')
        self.client.errors[('PUT', '/v1/instructions/agent')] = C3Error(409, 'stale revision')
        self.client.reply = snapshot(agent='theirs', agent_rev='a9')
        self.store.save(self.agent)
        self.runner.finish()
        spin()                                                 # the follow-up refresh starts
        self.assertEqual(len(self.runner.pending), 1)
        self.runner.finish()
        self.assertEqual(self.client.calls[-1][:2], ('GET', '/v1/instructions'))
        self.assertEqual(self.store.drafts[self.agent].text, 'mine')
        self.assertTrue(self.store.stale(self.agent))
        del self.client.errors[('PUT', '/v1/instructions/agent')]
        self.client.reply = snapshot(agent='mine', agent_rev='a10')
        self.store.save(self.agent)                            # "Replace saved version"
        self.runner.finish()
        self.assertEqual(self.client.calls[-1][2]['revision'], 'a9')
        self.assertNotIn(self.agent, self.store.drafts)

    def test_refresh_waits_for_a_write(self):
        self.store.edit(self.agent, 'mine')
        self.store.save(self.agent)
        self.store.refresh()
        self.assertEqual(len(self.runner.pending), 1)
        self.runner.finish()
        spin()
        self.assertEqual(len(self.runner.pending), 1)          # the queued refresh, after the write

    def test_profile_switch_keeps_drafts_per_profile(self):
        self.store.edit(self.agent, 'desk draft')
        self.client.reply = snapshot(profile='kitchen', agent='kitchen text')
        self.store.refresh()
        self.runner.finish()
        self.assertFalse(self.store.save(self.agent))          # not the active profile
        self.assertIsNone(self.store.saved(self.agent))
        self.client.reply = snapshot()
        self.store.refresh()
        self.runner.finish()
        self.assertEqual(self.store.drafts[self.agent].text, 'desk draft')

    def test_new_skill_slug_and_null_revision(self):
        self.assertTrue(self.store.slug_problem('desk', 'Bad Slug'))
        self.assertTrue(self.store.slug_problem('desk', 'tidy'))     # exists
        self.assertTrue(self.store.slug_problem('desk', '-ab'))
        self.assertTrue(self.store.slug_problem('desk', 'a' * 49))
        self.assertEqual(self.store.slug_problem('desk', 'a' * 48), '')
        self.assertEqual(self.store.slug_problem('desk', 'water-plants'), '')
        key = self.store.new_skill('desk', 'water-plants')
        self.assertEqual(self.store.skill_ids('desk'), ['tidy', 'water-plants'])
        self.store.edit(key, '# Water plants\n\nCheck the soil.')
        self.client.reply = snapshot(skills=(('tidy', 'Tidy text', 'r1'), ('water-plants', '# Water plants\n\nCheck the soil.', 'w1')))
        self.store.save(key)
        self.runner.finish()
        self.assertEqual(self.client.calls[-1], ('PUT', '/v1/instructions/skills/water-plants',
                                                 {'profile': 'desk', 'text': '# Water plants\n\nCheck the soil.',
                                                  'revision': None}))
        self.assertNotIn(key, self.store.drafts)

    def test_saved_then_profile_switched_is_not_called_unsaved(self):
        self.store.edit(self.agent, 'mine')
        self.client.errors[('PUT', '/v1/instructions/agent')] = C3Error(
            409, "saved to 'desk', but the active profile changed; reload the instructions")
        self.store.save(self.agent)
        self.runner.finish()
        message = self.store.notices[self.agent][0]
        self.assertTrue(message.startswith('Saved, but'))
        spin()
        self.client.reply = snapshot(agent='mine', agent_rev='a2')   # back on desk: C3 has the text
        self.runner.finish()
        self.assertNotIn(self.agent, self.store.drafts)

    def test_read_that_raced_a_switch_reloads_once(self):
        store, client, runner = make_store()
        client.errors[('GET', '/v1/instructions')] = C3Error(409, 'the active profile changed')
        store.refresh()
        runner.finish()
        spin()
        runner.finish()                                        # the one automatic retry
        spin()
        self.assertEqual(runner.pending, [])

    def test_problems_and_no_profile_message(self):
        snap = ip.parse_snapshot({**snapshot(), 'problems': [{'id': 'big', 'revision': 'x', 'error': 'too large'}]})
        self.assertEqual(snap['problems'], ['big: too large'])
        none = ip.parse_snapshot({'profile': None, 'profile_name': None, 'agent': None, 'skills': [],
                                  'tools': [], 'message': 'Select a profile on the Brain tab.'})
        self.assertEqual(none['message'], 'Select a profile on the Brain tab.')

    def test_load_errors_read_plainly(self):
        store, client, runner = make_store()
        client.errors[('GET', '/v1/instructions')] = C3Error(404, 'Not Found')
        store.refresh()
        runner.finish()
        self.assertIn('not available on this C3 service', store.error)
        self.assertIsNone(store.snapshot)


@needs_gi
@needs_display
class Panels(unittest.TestCase):
    def setUp(self):
        gui.Adw.init()
        self.store, self.client, self.runner = make_store()
        self.agent = ip.AgentPanel(self.store)
        self.skills = ip.SkillsPanel(self.store)

    def load(self, reply):
        self.client.reply = reply
        self.store.refresh()
        self.runner.finish()

    @staticmethod
    def text(panel):
        buf = panel.view.get_buffer()
        return buf.get_text(*buf.get_bounds(), False)

    def test_no_profile_disables_both(self):
        self.load({'profile': None, 'profile_name': None, 'tools': []})
        self.assertEqual(self.agent.pages.get_visible_child_name(), 'message')
        self.assertEqual(self.agent.message.get_title(), 'No profile selected')
        self.assertEqual(self.skills.pages.get_visible_child_name(), 'message')
        self.assertFalse(self.skills.picker_row.get_sensitive())

    def test_agent_editing_and_tools_are_separate(self):
        self.load(snapshot())
        self.assertEqual(self.text(self.agent), '# Agent\n')
        self.assertFalse(self.agent.save.get_sensitive())
        self.agent.view.get_buffer().set_text('# Agent\nNew line\n')
        self.assertTrue(self.agent.save.get_sensitive())
        self.assertTrue(self.agent.discard.get_visible())
        self.assertEqual(self.agent.tools.get_label(), 'Built-in tools (1)')
        self.load(snapshot())                                  # a refresh keeps the draft on screen
        self.assertEqual(self.text(self.agent), '# Agent\nNew line\n')
        self.agent.discard.emit('clicked')
        self.assertEqual(self.text(self.agent), '# Agent\n')

    def test_switching_skills_keeps_each_draft(self):
        self.load(snapshot(skills=(('tidy', 'Tidy text', 'r1'), ('water', 'Water text', 'w1'))))
        self.assertEqual(self.text(self.skills), 'Tidy text')
        self.skills.view.get_buffer().set_text('Tidy edited')
        self.skills.picker.set_selected(1)
        self.assertEqual(self.text(self.skills), 'Water text')
        self.skills.picker.set_selected(0)
        self.assertEqual(self.text(self.skills), 'Tidy edited')
        model = self.skills.picker.get_model()
        self.assertEqual(model.get_string(0), 'tidy (edited)')

    def test_stale_draft_offers_replace(self):
        self.load(snapshot())
        self.agent.view.get_buffer().set_text('mine')
        self.load(snapshot(agent='theirs', agent_rev='a2'))
        self.assertEqual(self.text(self.agent), 'mine')
        self.assertEqual(self.agent.save.get_label(), 'Replace saved version')
        self.assertIn('changed in C3', self.agent.status.get_label())


if ip is not None:
    class OfflineClient(C3Client):
        calls = []

        def exchange(self, method, path, body=None, raw=False):
            OfflineClient.calls.append(path)
            raise C3Error(503, 'offline test')


@needs_gi
@needs_display
class ChannelTabs(unittest.TestCase):
    def test_six_tabs_conversation_default_and_dropdown_in_step(self):
        gui.Adw.init()
        app = None              # no D-Bus registration: other GTK tests in this process register one
        live = gui.Source()
        live.client = OfflineClient(token_file=Path('/nonexistent'))
        win = gui.Window(app, live)
        names = [win.channel.get_page(c).get_name() for c in gui._children(win.channel)]
        self.assertEqual(names[:2], ['conversation', 'brain'])
        self.assertIn('family', names)
        self.assertLess(names.index('agent'), names.index('skills'))
        self.assertEqual(win.channel.get_visible_child_name(), 'conversation')
        self.assertEqual(win.channel.get_page(win.channel.get_child_by_name('agent')).get_title(), 'Agent')
        menu = win.channel_menu
        self.assertEqual(menu.get_model().get_n_items(), len(names))
        self.assertEqual(menu.get_selected(), 0)
        menu.set_selected(names.index('skills'))
        self.assertEqual(win.channel.get_visible_child_name(), 'skills')
        win.channel.set_visible_child_name('brain')
        self.assertEqual(menu.get_selected(), 1)
        self.assertNotIn('/v1/instructions', OfflineClient.calls)      # nothing until a tab is shown
        live.data = ({'family': {'deployment': 'family', 'mode': 'kids'}}, None, [], '')
        win.refresh()
        self.assertIs(win.channel.get_child_by_name('brain'), win.brain)
        self.assertFalse(win.composer.get_visible())
        self.assertEqual(win.mode_summary.get_label(), 'Camera in Kids mode')
        live.data = ({'family': {'deployment': 'family', 'mode': 'adult'}}, None, [], '')
        win.refresh()
        self.assertIs(win.channel.get_child_by_name('brain'), win.brain)
        self.assertFalse(win.composer.get_visible())
        win.close()

        demo = gui.Source('asleep')
        demo.client = OfflineClient(token_file=Path('/nonexistent'))
        demo.start()
        win = gui.Window(app, demo)
        self.assertIsNone(win.channel.get_child_by_name('agent'))
        self.assertEqual(win.channel_menu.get_model().get_n_items(), 2)
        win.close()


if __name__ == '__main__':
    unittest.main()
