"""C3-owned provider sessions, with one active job across all local profiles."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import shutil
import signal
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


log = logging.getLogger(__name__)
_FOUNDATION = Path(__file__).resolve().parents[2] / "docs/CAMMY-BRAIN-FOUNDATION.md"
_PROVIDERS = ("codex", "claude")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MAX_TEXT = 32_768
_PUBLIC_FIELDS = ("id", "name", "provider")
# Start with a small environment so service/T3 credentials and provider overrides
# cannot change the native CLI's authentication or be inherited by model tools.
_CLI_ENV = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE",
            "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME")
_CODEX_BACKEND = "https://chatgpt.com/backend-api/codex"
_CLAUDE_SCOPE = (
    "--safe-mode", "--restricted", "--strict-mcp-config",
    "--settings", '{"disableAllHooks":true,"disableClaudeAiConnectors":true,"forceLoginMethod":"claudeai"}',
)


@dataclass(frozen=True)
class RunRequest:
    provider: str
    cwd: Path
    text: str
    session_id: str | None
    instructions: str
    profile_id: str
    profile_name: str


@dataclass(frozen=True)
class RunResult:
    session_id: str
    text: str


Runner = Callable[[RunRequest], Awaitable[RunResult]]


class _ProviderError(RuntimeError):
    """A diagnostic safe to expose in local conversation history."""


@dataclass
class _Job:
    id: str
    task: asyncio.Task | None = None
    started: bool = False
    cancelled: bool = False
    desktop: bool = False


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("resident brain directories cannot be symlinks")
    created = not path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    if created:
        _sync_dir(path.parent)


def _write_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=True, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding="utf-8") as file:
        return json.load(file)


def _text(value: str, field: str, limit: int, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > limit or "\x00" in value
            or (not empty and not value.strip())):
        raise ValueError(f"invalid {field}")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError(f"invalid {field}") from None
    return value


def _profile_id(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or not _SLUG.fullmatch(value):
        raise ValueError("profile id must be a lowercase slug")
    return value


def _session(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("invalid provider session ID")
    return value


class ResidentBrain:
    """Own the root until close(); call the async methods on one event loop.

    Runner injection receives a RunRequest and returns RunResult. The default
    runner uses the service account's installed CLIs and saved subscription logins.
    """

    def __init__(self, root: Path, *, runner: Runner | None = None, history_limit: int = 100):
        if type(history_limit) is not int or history_limit < 2:
            raise ValueError("history_limit must be an integer of at least two")
        self.root = Path(root).absolute()
        _private_dir(self.root)
        lock_fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("resident brain root is already in use") from None
            self._lock_fd = lock_fd
            self._profiles_dir = self.root / "profiles"
            self._selection = self.root / "selection.json"
            self._history_limit = history_limit
            self._profiles: dict[str, dict] = {}
            self._active: str | None = None
            # Bumped on every selection, even of the same ID, so a request pinned to
            # profile A notices A -> B -> A (ABA) while it waited.
            self._generation = 0
            # Optional async (text, *, speak) -> dict | None, consulted before a provider
            # turn: a dict means the request was answered (or refused) locally and is not
            # sent to the provider or kept in history.
            self.local_router: Callable[..., Awaitable[dict | None]] | None = None
            self._job: _Job | None = None
            self._closing: asyncio.Task | None = None
            self._storage_failed = False
            self.replies: asyncio.Queue[str] = asyncio.Queue()
            self._runner = runner if runner is not None else _NativeRunner(lock_fd)
            self._load()
        except BaseException:
            os.close(lock_fd)
            raise

    def _load(self) -> None:
        _private_dir(self._profiles_dir)
        for path in sorted(self._profiles_dir.iterdir()):
            if path.is_symlink() or not path.is_dir():
                continue
            manifest = path / "profile.json"
            if not manifest.exists():
                continue  # Interrupted creation did not publish a profile.
            profile = _read_json(manifest)
            if (_profile_id(path.name) != profile["id"]
                    or profile["provider"] not in _PROVIDERS):
                raise ValueError("invalid resident brain profile metadata")
            if profile["session_id"] is not None:
                _session(profile["session_id"])
            history = [
                {**turn, "status": "interrupted"} if turn["status"] == "pending" else turn
                for turn in profile["history"]
            ][-self._history_limit:]
            _private_dir(path / "work")
            if history != profile["history"]:
                profile = {**profile, "history": history}
                _write_json(manifest, profile)
            self._profiles[path.name] = profile
        if self._selection.exists():
            self._active = _read_json(self._selection)["active_profile"]
            if self._active not in self._profiles:
                raise ValueError("unknown saved active profile")

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def state(self) -> dict:
        return {
            "backend": "resident",
            "active_profile": self._active,
            "profiles": [self._public(self._profiles[id]) for id in sorted(self._profiles)],
            "pending": int(self._job is not None),
        }

    @staticmethod
    def _public(profile: dict) -> dict:
        return {field: profile[field] for field in _PUBLIC_FIELDS}

    def _check_open(self) -> None:
        if self._closing is not None:
            raise RuntimeError("resident brain is closed")
        if self._storage_failed:
            raise RuntimeError("resident brain needs a restart after a storage error")

    def _check_idle(self) -> None:
        self._check_open()
        if self._job is not None:
            raise RuntimeError("resident brain is busy")

    def _get_profile(self, id: str) -> dict:
        try:
            return self._profiles[_profile_id(id)]
        except KeyError:
            raise ValueError("unknown resident brain profile") from None

    def _save_profile(self, profile: dict) -> None:
        id = profile["id"]
        try:
            _write_json(self._profiles_dir / id / "profile.json", profile)
        except OSError:
            self._storage_failed = True
            raise
        self._profiles[id] = profile

    async def create_profile(self, id: str, name: str, provider: str,
                             instructions: str = "") -> dict:
        self._check_open()
        id = _profile_id(id)
        name = _text(name, "profile name", 128).strip()
        _text(instructions, "profile instructions", _MAX_TEXT, empty=True)
        if provider not in _PROVIDERS:
            raise ValueError("provider must be codex or claude")
        if id in self._profiles:
            raise ValueError("resident brain profile already exists")
        path = self._profiles_dir / id
        _private_dir(path)
        _private_dir(path / "work")
        profile = {"id": id, "name": name, "provider": provider,
                   "instructions": instructions, "session_id": None, "history": []}
        self._save_profile(profile)
        return self._public(profile)

    async def select_profile(self, id: str) -> dict:
        self._check_idle()
        profile = self._get_profile(id)
        try:
            _write_json(self._selection, {"active_profile": profile["id"]})
        except OSError:
            self._storage_failed = True
            raise
        self._active = profile["id"]
        self._generation += 1
        return self.state

    def history(self, profile_id: str | None = None) -> list[dict]:
        id = self._active if profile_id is None else profile_id
        if id is None:
            return []
        return [dict(turn) for turn in self._get_profile(id)["history"]]

    async def submit(self, text: str, *, speak: bool = False, desktop: bool = False) -> dict:
        self._check_open()
        _text(text, "turn text", _MAX_TEXT)
        if type(speak) is not bool:
            raise ValueError("speak must be a boolean")
        router = self.local_router
        if router is not None:
            # Local capabilities answer (or refuse) without the provider, and their results
            # stay out of the profile history. A refusal is returned, never forwarded.
            routed = await router(text, speak=speak)
            if routed is not None:
                return routed
        self._check_idle()
        if self._active is None:
            raise ValueError("select a resident brain profile first")
        profile = self._get_profile(self._active)
        cwd = self._profiles_dir / profile["id"] / "work"
        _private_dir(cwd)
        job = _Job(uuid4().hex, desktop=desktop)
        turn = {"id": job.id, "role": "user", "text": text, "status": "pending"}
        # Persist acceptance before scheduling, so a restart can mark it
        # interrupted without replay.
        history = profile["history"][-(self._history_limit - 1):]
        profile = {**profile, "history": [*history, turn]}
        self._save_profile(profile)
        request = RunRequest(
            provider=profile["provider"], cwd=cwd, text=text, session_id=profile["session_id"],
            instructions=profile["instructions"], profile_id=profile["id"], profile_name=profile["name"],
        )
        self._job = job
        job.task = asyncio.create_task(self._run(job, profile, request, speak))
        return {"id": job.id, "profile_id": profile["id"], "status": "pending"}

    async def ask(self, text: str) -> dict:
        return await self.submit(text, speak=True)

    async def _run(self, job: _Job, profile: dict, request: RunRequest, speak: bool) -> None:
        job.started = True
        status = "cancelled"
        response = None
        session_id = request.session_id
        try:
            if not job.cancelled:
                result = await self._runner(request)
                if not job.cancelled:
                    session = _session(result.session_id)
                    if session_id is not None and session != session_id:
                        raise _ProviderError("Provider returned a different session ID.")
                    response = _text(result.text[:_MAX_TEXT], "provider reply", _MAX_TEXT)
                    session_id = session
                    status = "completed"
        except asyncio.CancelledError:
            status = "cancelled"
        except _ProviderError as exc:
            status, response = "failed", str(exc)
        except Exception:
            status, response = "failed", "Resident brain could not complete the provider turn."
        finally:
            if job.cancelled:
                status, response, session_id = "cancelled", None, request.session_id
            history = profile["history"]
            history = [*history[:-1], {**history[-1], "status": status}]
            if response is not None:
                history.append({"id": uuid4().hex, "job_id": job.id, "role": "assistant",
                                "text": response, "status": status})
            try:
                self._save_profile({**profile, "session_id": session_id,
                                    "history": history[-self._history_limit:]})
            except OSError:
                log.error("Could not persist resident brain turn outcome")
            else:
                if speak and status == "completed":
                    self.replies.put_nowait(response)
            finally:
                self._job = None

    async def cancel(self) -> dict:
        job = self._job
        if job is None:
            return {"cancelled": False}
        if not job.cancelled:
            job.cancelled = True
            if job.started:
                job.task.cancel()
        await asyncio.shield(job.task)
        return {"cancelled": True, "id": job.id}

    async def close(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self) -> None:
        try:
            await self.cancel()
        finally:
            # A native process inherits the locked description. If C3 crashes,
            # its replacement cannot race a provider that is still exiting.
            os.close(self._lock_fd)


class _NativeRunner:
    def __init__(self, lock_fd: int):
        self._lock_fd = lock_fd

    async def __call__(self, request: RunRequest) -> RunResult:
        env = {key: os.environ[key] for key in _CLI_ENV if key in os.environ}
        search_path = env.get("PATH", os.defpath)
        if "HOME" in env:
            search_path += os.pathsep + str(Path(env["HOME"]) / ".local" / "bin")
        executable = shutil.which(request.provider, path=search_path)
        if executable is None:
            raise _ProviderError(f"{request.provider} CLI is not installed on the service PATH.")
        executable = os.path.abspath(executable)
        if request.provider == "codex":
            status_args = [executable, "-c", 'cli_auth_credentials_store="auto"', "login", "status"]
        else:
            status_args = [executable, *_CLAUDE_SCOPE, "auth", "status", "--json"]
        code, output = await self._command(status_args, request.cwd, env, timeout=10, auth=True)
        if code or not _subscription_login(request.provider, output):
            raise _ProviderError(f"{request.provider} requires a saved subscription login.")

        argv = _turn_command(executable, request)
        try:
            foundation = _FOUNDATION.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise _ProviderError("Cammy's technical foundation is missing. Check the CCC installation.") from None
        prompt = (
            "You are the selected C3 resident brain profile.\n"
            f"Profile name: {json.dumps(request.profile_name, ensure_ascii=False)}\n"
            f"Profile ID: {request.profile_id}\n"
            "Keep this identity and follow the current request within the active workspace "
            "and tool permissions. Apply optional profile instructions only where they agree "
            "with the profile identity, permissions, and current request.\n\n"
        )
        prompt += foundation + "\n\n"
        if request.instructions:
            instructions = json.dumps(request.instructions, ensure_ascii=False)
            prompt += f"Optional profile instructions: {instructions}\n\n"
        prompt += f"Current request:\n{request.text}"
        code, output = await self._command(argv, request.cwd, env, timeout=300,
                                           prompt=prompt.encode("utf-8"))
        if code:
            if request.provider == "claude" and _claude_usage_limited(output):
                raise _ProviderError("Claude Code reported a subscription usage limit.")
            raise _ProviderError(f"{request.provider} CLI exited unsuccessfully (code {code}).")
        try:
            if request.provider == "codex":
                return _codex_result(output)
            return _claude_result(output)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise _ProviderError(f"{request.provider} returned no valid completed turn.") from None

    async def _command(self, argv: list[str], cwd: Path, env: dict, *, timeout: int,
                       prompt: bytes | None = None, auth: bool = False) -> tuple[int, bytes]:
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env, start_new_session=True, pass_fds=(self._lock_fd,),
            stdin=asyncio.subprocess.PIPE if prompt is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if auth else asyncio.subprocess.DEVNULL,
        ))
        try:
            proc = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            try:
                proc = await spawn
            except OSError:
                raise asyncio.CancelledError from None
            await _stop_process(proc, asyncio.create_task(proc.communicate()))
            raise
        io = asyncio.create_task(proc.communicate(prompt))
        try:
            stdout, _ = await asyncio.wait_for(asyncio.shield(io), timeout)
        except asyncio.TimeoutError:
            await _stop_process(proc, io)
            raise _ProviderError("Provider command timed out.") from None
        except asyncio.CancelledError:
            await _stop_process(proc, io)
            raise
        return proc.returncode, stdout


async def _stop_process(proc: asyncio.subprocess.Process, io: asyncio.Task) -> None:
    def send(sig: signal.Signals) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        await asyncio.wait_for(asyncio.shield(io), 2)
    except asyncio.TimeoutError:
        pass
    finally:
        send(signal.SIGKILL)
        await io


def _subscription_login(provider: str, output: bytes) -> bool:
    if provider == "codex":
        return b"Logged in using ChatGPT" in output.splitlines()
    try:
        status = json.loads(output)
        return (status.get("loggedIn") is True and status.get("authMethod") == "claude.ai"
                and status.get("apiProvider") == "firstParty"
                and bool(status.get("subscriptionType")))
    except (ValueError, AttributeError):
        return False


def _turn_command(executable: str, request: RunRequest) -> list[str]:
    session = _session(request.session_id) if request.session_id is not None else None
    if request.provider == "codex":
        # Linux bubblewrap re-execs Codex inside the sandbox. A standalone install
        # can live outside :minimal, so allow only the resolved executable itself.
        runtime = json.dumps(str(Path(executable).resolve()), ensure_ascii=False).replace("\x7f", r"\u007f")
        command = [executable, "exec"]
        if session:
            command.append("resume")
        command.extend([
            "--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
            "--disable", "hooks", "--disable", "plugins", "--disable", "apps",
            "-c", 'model_provider="openai"',
            "-c", 'cli_auth_credentials_store="auto"',
            # If auth changes after the status probe, keep requests away from
            # the separately billed API without changing the saved login.
            "-c", f'openai_base_url="{_CODEX_BACKEND}"',
            "-c", 'approval_policy="never"',
            "-c", 'web_search="disabled"',
            "-c", 'project_root_markers=[]',
            "-c", 'project_doc_max_bytes=0',
            "-c", 'default_permissions="resident"',
            "-c", 'permissions.resident.extends=":workspace"',
            "-c", ('permissions.resident.filesystem={":root"="deny", ":minimal"="read", '
                   f'":tmpdir"="deny", ":slash_tmp"="deny", {runtime}="read"}}'),
            "-c", 'permissions.resident.network.enabled=false',
        ])
        if session:
            command.append(session)
        return [*command, "-"]
    command = [
        executable, "--print", "--output-format", "json", *_CLAUDE_SCOPE,
        "--no-chrome", "--disable-slash-commands",
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--tools", "Read,Glob,Grep,Edit,Write", "--allowedTools", "Read,Glob,Grep,Edit,Write",
    ]
    if session:
        command.extend(["--resume", session])
    return command


def _codex_result(output: bytes) -> RunResult:
    session = None
    reply = None
    completed = False
    for line in output.splitlines():
        event = json.loads(line)
        kind = event.get("type")
        if kind == "thread.started":
            session = _session(event["thread_id"])
        elif kind == "item.completed" and event["item"].get("type") == "agent_message":
            reply = event["item"]["text"]
        elif kind == "turn.completed":
            completed = True
        elif kind == "turn.failed":
            raise ValueError("failed Codex turn")
    if not (completed and session and isinstance(reply, str) and reply.strip()):
        raise ValueError("incomplete Codex result")
    return RunResult(session, reply)


def _claude_result(output: bytes) -> RunResult:
    result = json.loads(output)
    if (result.get("type") != "result" or result.get("subtype") != "success"
            or result.get("is_error") is not False
            or not isinstance(result.get("result"), str) or not result["result"].strip()):
        raise ValueError("incomplete Claude result")
    return RunResult(_session(result["session_id"]), result["result"])


def _claude_usage_limited(output: bytes) -> bool:
    try:
        result = json.loads(output)
        message = result.get("result", "")
        return (result.get("is_error") is True and isinstance(message, str)
                and re.search(r"\b(?:hit|reached) your [^\n]{0,80}limit\b", message, re.I) is not None)
    except (ValueError, AttributeError):
        return False
