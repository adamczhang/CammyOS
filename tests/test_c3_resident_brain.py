"""C3 resident brain lifecycle and native CLI contracts with no provider calls."""

import asyncio
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from c3 import resident_brain as brainmod  # noqa: E402


CODEX_ID = str(UUID(int=1))
CLAUDE_ID = str(UUID(int=2))
CLAUDE_LOGIN = b'{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"max"}'


def codex_json(text="final", *, final_event="turn.completed"):
    events = [
        {"type": "thread.started", "thread_id": CODEX_ID},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "working"}},
        {"type": "item.completed", "item": {"type": "command_execution", "text": "tool output"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {"type": final_event},
    ]
    return "\n".join(map(json.dumps, events)).encode()


def claude_json(text="final", *, is_error=False):
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error,
                       "session_id": CLAUDE_ID, "result": text}).encode()


async def idle(brain):
    async def wait():
        while brain.state["pending"]:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 3)


class ResidentBrainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "brain"
        self.calls = []
        self.brain = self.open()

    def open(self, **options):
        brain = brainmod.ResidentBrain(self.root, runner=self.run_provider, **options)
        self.addAsyncCleanup(brain.close)
        return brain

    async def run_provider(self, request):
        self.calls.append(request)
        if request.text == "provider failure":
            raise RuntimeError("private provider diagnostic")
        if request.text == "wrong session":
            return brainmod.RunResult(CLAUDE_ID, "incorrect session")
        session = CODEX_ID if request.provider == "codex" else CLAUDE_ID
        return brainmod.RunResult(session, f"{request.provider}: {request.text}")

    async def test_profiles_are_private_and_resume_independently_after_restart(self):
        brain = self.brain
        self.assertEqual(brain.state, {"backend": "resident", "active_profile": None,
                                       "profiles": [], "pending": 0})
        self.assertEqual(await brain.create_profile("desk", "Desk", "codex", "Desk notes"),
                         {"id": "desk", "name": "Desk", "provider": "codex"})
        await brain.create_profile("other", "Other", "claude", "Other notes")
        for profile in ("desk", "other"):
            await brain.select_profile(profile)
            job = await brain.submit("hello")
            self.assertEqual(brain.state["pending"], 1)
            self.assertEqual(brain.history()[-1],
                             {"id": job["id"], "role": "user", "text": "hello", "status": "pending"})
            await idle(brain)
            self.assertEqual(brain.history()[-1]["job_id"], job["id"])
            self.assertEqual(brain.history()[-1]["status"], "completed")
        self.assertTrue(brain.replies.empty())
        self.assertEqual([p.session_id for p in self.calls], [None, None])
        self.assertEqual([(p.profile_id, p.profile_name) for p in self.calls],
                         [("desk", "Desk"), ("other", "Other")])
        self.assertEqual([p.instructions for p in self.calls], ["Desk notes", "Other notes"])
        self.assertEqual([p.cwd for p in self.calls],
                         [self.root / "profiles" / p / "work" for p in ("desk", "other")])
        self.calls[0].cwd.joinpath("desk-only.txt").touch()
        self.assertFalse(self.calls[1].cwd.joinpath("desk-only.txt").exists())
        await brain.close()

        brain = self.open()
        self.assertEqual(brain.state["active_profile"], "other")
        self.assertTrue(brain.replies.empty())
        for profile, session, provider in (("other", CLAUDE_ID, "claude"),
                                           ("desk", CODEX_ID, "codex")):
            await brain.select_profile(profile)
            self.assertEqual(brain.history()[-1]["text"], f"{provider}: hello")
            await brain.ask("reply out loud")
            await idle(brain)
            self.assertEqual(self.calls[-1].session_id, session)
            self.assertEqual(self.calls[-1].profile_id, profile)
            self.assertEqual(self.calls[-1].profile_name, profile.title())
            self.assertEqual(brain.replies.get_nowait(), f"{provider}: reply out loud")
            self.assertTrue(brain.replies.empty())
        for path in [self.root, *self.root.rglob("*")]:
            if path.name != "desk-only.txt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0, path)

    async def test_failure_and_wrong_session_are_terminal_and_never_spoken(self):
        brain = self.brain
        for provider, session in (("codex", CODEX_ID), ("claude", CLAUDE_ID)):
            await brain.create_profile(provider, provider, provider)
            await brain.select_profile(provider)
            await brain.submit("start")
            await idle(brain)
            await brain.ask("provider failure")
            await idle(brain)
            self.assertEqual([turn["status"] for turn in brain.history()[-2:]], ["failed", "failed"])
            self.assertNotIn("private provider diagnostic", brain.history()[-1]["text"])
            self.assertTrue(brain.replies.empty())
            self.assertEqual(self.calls[-1].session_id, session)
            await brain.submit("retry explicitly")
            await idle(brain)
            self.assertEqual(self.calls[-1].session_id, session)
        await brain.select_profile("codex")
        await brain.ask("wrong session")
        await idle(brain)
        self.assertEqual(brain.history()[-1]["status"], "failed")
        self.assertTrue(brain.replies.empty())
        await brain.submit("resume original")
        await idle(brain)
        self.assertEqual(self.calls[-1].session_id, CODEX_ID)

    async def test_cancel_before_launch_and_during_work(self):
        brain = self.brain
        await brain.create_profile("desk", "Desk", "codex")
        await brain.select_profile("desk")
        job = await brain.ask("cancel before launch")
        self.assertEqual(await brain.cancel(), {"cancelled": True, "id": job["id"]})
        self.assertEqual(self.calls, [])
        self.assertEqual(brain.history()[-1]["status"], "cancelled")
        await brain.close()

        started, cleaned = asyncio.Event(), asyncio.Event()

        async def runner(request):
            try:
                started.set()
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        brain = brainmod.ResidentBrain(self.root, runner=runner)
        self.addAsyncCleanup(brain.close)
        job = await brain.ask("running")
        await asyncio.wait_for(started.wait(), 3)
        with self.assertRaises(RuntimeError):
            await brain.submit("second job")
        with self.assertRaises(RuntimeError):
            await brain.select_profile("desk")
        with self.assertRaises(RuntimeError):
            self.open()
        await brain.close()
        self.assertTrue(cleaned.is_set())
        self.assertFalse((await brain.cancel())["cancelled"])
        self.assertEqual(brain.history()[-1]["id"], job["id"])
        self.assertEqual(brain.history()[-1]["status"], "cancelled")
        self.assertTrue(brain.replies.empty())
        self.assertEqual(self.open().state["pending"], 0)

    async def test_crash_recovery_marks_accepted_turn_and_never_replays(self):
        await self.brain.close()
        # Exit without running the task that was scheduled after acceptance.
        script = """import asyncio, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from c3.resident_brain import ResidentBrain, RunResult
async def main():
    async def runner(request):
        return RunResult(sys.argv[3], "finished")
    brain = ResidentBrain(Path(sys.argv[2]), runner=runner)
    await brain.create_profile("desk", "Desk", "codex")
    await brain.select_profile("desk")
    await brain.submit("previous")
    while brain.state["pending"]:
        await asyncio.sleep(0)
    await brain.ask("interrupted")
    os._exit(0)
asyncio.run(main())
"""
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(ROOT / "host"),
                                                    str(self.root), CODEX_ID, env=env)
        self.assertEqual(await asyncio.wait_for(proc.wait(), 3), 0)
        brain = self.open()
        self.assertEqual(brain.history()[-1]["status"], "interrupted")
        self.assertEqual(brain.history()[-1]["text"], "interrupted")
        self.assertEqual(brain.state["pending"], 0)
        self.assertTrue(brain.replies.empty())
        self.assertEqual(self.calls, [])
        await brain.submit("new turn")
        await idle(brain)
        self.assertEqual([(r.text, r.session_id) for r in self.calls], [("new turn", CODEX_ID)])

    async def test_validation_and_history_bounds(self):
        brain = self.brain
        self.assertEqual(brain.history(), [])
        with self.assertRaises(ValueError):
            await brain.submit("no selection")
        for id in ("../desk", "two words", "Desk", "a--b", "", None):
            with self.subTest(id=id), self.assertRaises(ValueError):
                await brain.create_profile(id, "Desk", "codex")
        with self.assertRaises(ValueError):
            await brain.create_profile("desk", "Desk", "other")
        await brain.create_profile("desk", " Desk ", "codex")
        with self.assertRaises(ValueError):
            await brain.create_profile("desk", "Same ID", "claude")
        with self.assertRaises(ValueError):
            await brain.select_profile("unknown")
        with self.assertRaises(ValueError):
            brain.history("unknown")
        await brain.select_profile("desk")
        for value in ("", "   ", None, "\x00", "\udcff", "a" * 32_769):
            with self.assertRaises(ValueError):
                await brain.submit(value)
        await brain.close()
        brain = self.open(history_limit=4)
        for text in ("one", "two", "three"):
            await brain.submit(text)
            await idle(brain)
        self.assertEqual([t["text"] for t in brain.history()],
                         ["two", "codex: two", "three", "codex: three"])
        brain.history()[0]["text"] = "modified by caller"
        brain.state["profiles"][0]["name"] = "modified by caller"
        await brain.close()
        brain = self.open()
        self.assertEqual(brain.history()[0]["text"], "two")
        self.assertEqual(brain.state["profiles"][0]["name"], "Desk")


class NativeRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_adapters_use_saved_login_stdin_and_exact_resume(self):
        for provider, login, session, reply in (
                ("codex", b"Logged in using ChatGPT\n", CODEX_ID, codex_json()),
                ("claude", CLAUDE_LOGIN, CLAUDE_ID, claude_json())):
            with self.subTest(provider=provider):
                runner = brainmod._NativeRunner(-1)
                command = AsyncMock(side_effect=[(0, login), (0, reply), (0, login), (0, reply)])
                cwd = Path("/local/desk/work")
                with (patch.object(runner, "_command", command),
                      patch.object(brainmod.shutil, "which", return_value=f"/bin/{provider}"),
                      patch.dict(os.environ, {"OPENAI_API_KEY": "dummy", "CODEX_HOME": "/custom",
                                              "ANTHROPIC_API_KEY": "dummy", "CLAUDE_CODE_OAUTH_TOKEN": "dummy",
                                              "CLAUDE_CONFIG_DIR": "/custom", "CLAUDE_CODE_USE_BEDROCK": "1"})):
                    for resume in (None, session):
                        instructions = "" if resume else "Desk preferences"
                        request = brainmod.RunRequest(provider, cwd, "--version", resume,
                                                      instructions, "desk", 'Desk "One"')
                        self.assertEqual(await runner(request), brainmod.RunResult(session, "final"))
                auth, initial, _, followup = command.call_args_list
                self.assertEqual(auth.args[1], cwd)
                self.assertTrue(auth.kwargs["auth"])
                self.assertEqual(initial.args[1], cwd)
                self.assertEqual(followup.args[1], cwd)
                self.assertNotIn(session, initial.args[0])
                self.assertIn(session, followup.args[0])
                self.assertNotIn("--last", followup.args[0])
                self.assertNotIn("--continue", followup.args[0])
                self.assertNotIn("--version", initial.args[0])
                for turn in (initial, followup):
                    prompt = turn.kwargs["prompt"].decode()
                    self.assertIn('Profile name: ' + json.dumps('Desk "One"'), prompt)
                    self.assertIn("Profile ID: desk\n", prompt)
                    self.assertIn("profile identity, permissions, and current request", prompt)
                    self.assertIn("# Cammy technical foundation", prompt)
                    self.assertIn("/os request ACTION CANDIDATE [RECEIPT]", prompt)
                    self.assertTrue(prompt.endswith("Current request:\n--version"))
                self.assertIn(b'Optional profile instructions: "Desk preferences"', initial.kwargs["prompt"])
                self.assertNotIn(b"Optional profile instructions:", followup.kwargs["prompt"])
                self.assertFalse(any(key in initial.args[2] for key in
                                     ("OPENAI_API_KEY", "CODEX_HOME", "ANTHROPIC_API_KEY",
                                      "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
                                      "CLAUDE_CODE_USE_BEDROCK")))
                self.assertFalse(any("bypass" in arg or "danger" in arg for arg in followup.args[0]))
                for args in (initial.args[0], followup.args[0]):
                    if provider == "codex":
                        self.assertIn('default_permissions="resident"', args)
                        self.assertIn('permissions.resident.network.enabled=false', args)
                        self.assertIn('web_search="disabled"', args)
                        self.assertEqual(args[args.index("apps") - 1], "--disable")
                    else:
                        self.assertIn("--restricted", args)
                        self.assertIn("--strict-mcp-config", args)
                        self.assertNotIn("--add-dir", args)
                        self.assertEqual(args[args.index("--tools") + 1], "Read,Glob,Grep,Edit,Write")

    async def test_rejects_api_login_and_failed_or_incomplete_results(self):
        for provider, api_status, login, failure in (
                ("codex", b"Logged in using an API key", b"Logged in using ChatGPT",
                 codex_json(final_event="turn.failed")),
                ("claude", b'{"loggedIn":true,"authMethod":"apiKey"}', CLAUDE_LOGIN,
                 claude_json(is_error=True))):
            request = brainmod.RunRequest(provider, Path("/local/desk/work"), "test", None, "", "desk", "Desk")
            runner = brainmod._NativeRunner(-1)
            for outputs, expected in (([(0, api_status)], "subscription login"),
                                      ([(0, login), (0, failure)], "no valid completed turn"),
                                      ([(0, login), (1, b"sensitive output")], "code 1")):
                with self.subTest(provider=provider, expected=expected):
                    command = AsyncMock(side_effect=outputs)
                    with (patch.object(runner, "_command", command),
                          patch.object(brainmod.shutil, "which", return_value=f"/bin/{provider}"),
                          self.assertRaisesRegex(RuntimeError, expected)):
                        await runner(request)
                    self.assertEqual(command.await_count, len(outputs))

    async def test_exposes_claude_usage_limit_without_copying_provider_output(self):
        runner = brainmod._NativeRunner(-1)
        cwd = Path("/local/desk/work")
        request = brainmod.RunRequest("claude", cwd, "test", None, "", "desk", "Desk")
        output = claude_json("You've hit your 5-hour limit. Some account details.", is_error=True)
        with (patch.object(runner, "_command", AsyncMock(side_effect=[(0, CLAUDE_LOGIN), (1, output)])),
              patch.object(brainmod.shutil, "which", return_value="/bin/claude"),
              self.assertRaisesRegex(RuntimeError, "^Claude Code reported a subscription usage limit.$")):
            await runner(request)

    async def test_native_command_cancellation_reaps_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "pid"
            fd = os.open(root / "lock", os.O_CREAT | os.O_RDWR, 0o600)
            self.addCleanup(os.close, fd)
            runner = brainmod._NativeRunner(fd)
            script = "import os, time; open('pid', 'w').write(str(os.getpid())); time.sleep(60)"
            command = asyncio.create_task(runner._command(
                [sys.executable, "-c", script], root, {}, timeout=10))

            async def wait_for_pid():
                while not pid_path.exists() or not pid_path.stat().st_size:
                    await asyncio.sleep(.01)
                return int(pid_path.read_text())

            try:
                pid = await asyncio.wait_for(wait_for_pid(), 3)
            finally:
                command.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(command, 5)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


class CodexSandboxTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("C3_CODEX_SANDBOX_TEST_DIR"),
                         "set C3_CODEX_SANDBOX_TEST_DIR to run the installed Codex Linux sandbox")
    def test_own_workspace_is_writable_and_outside_is_denied(self):
        executable = shutil.which("codex")
        self.assertIsNotNone(executable)
        with tempfile.TemporaryDirectory(dir=os.environ["C3_CODEX_SANDBOX_TEST_DIR"]) as directory:
            root = Path(directory).resolve()
            work = root / "profiles" / "desk" / "work"
            sibling = root / "profiles" / "other" / "work" / "neighbor.txt"
            home = root / "home"
            credential = home / ".codex" / "auth.json"
            for path in (work, sibling.parent, credential.parent):
                path.mkdir(parents=True)
            (work / "fixture.txt").write_text("invented input")
            sibling.write_text("invented neighbor")
            credential.write_text('{"fixture": true}')

            request = brainmod.RunRequest("codex", work, "test", None, "", "desk", "Desk")
            turn = brainmod._turn_command(executable, request)
            overrides = [turn[i + 1] for i, arg in enumerate(turn) if arg == "-c"]
            config = [arg for override in overrides for arg in ("-c", override)]
            script = """import socket, sys
from pathlib import Path

assert Path('fixture.txt').read_text() == 'invented input'
output = Path('created.txt')
output.write_text('invented output')
assert output.read_text() == 'invented output'
for path in map(Path, sys.argv[1:3]):
    for mode in ('r', 'r+'):
        try:
            with path.open(mode):
                pass
        except (FileNotFoundError, PermissionError):
            continue
        raise AssertionError(f'{path.name} unexpectedly opened in {mode} mode')
try:
    with socket.create_connection(('127.0.0.1', int(sys.argv[3])), timeout=1):
        pass
except OSError:
    pass
else:
    raise AssertionError('reachable host loopback listener')
"""
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                result = subprocess.run(
                    [executable, "sandbox", "--permission-profile", "resident", "--cd", str(work),
                     *config, "--", "/usr/bin/python3", "-c", script, str(sibling), str(credential),
                     str(listener.getsockname()[1])],
                    cwd=work, env={"HOME": str(home), "CODEX_HOME": str(credential.parent), "PATH": os.defpath},
                    capture_output=True, text=True, timeout=15,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((work / "created.txt").read_text(), "invented output")
            self.assertEqual(sibling.read_text(), "invented neighbor")
            self.assertEqual(credential.read_text(), '{"fixture": true}')


if __name__ == "__main__":
    unittest.main()
