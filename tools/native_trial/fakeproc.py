"""FAKE process table, native control, clock and attempt store for handoff tests.

These fakes never create or signal processes. ``FakeWorld`` is a scripted list
of Proc rows that the fake control mutates; ``FakeClock`` advances only when the
state machine sleeps.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .handoff import Identity, Proc


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.hooks: list = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds
        for hook in list(self.hooks):
            hook(self.t)


@dataclass
class FakeWorld:
    procs: list = field(default_factory=list)
    fake: bool = True

    def processes(self) -> list[Proc]:
        return list(self.procs)

    def remove(self, pid: int) -> None:
        self.procs = [p for p in self.procs if p.pid != pid]

    def add(self, proc: Proc) -> None:
        self.procs.append(proc)


class FakeControl:
    """Scriptable fake native control. ``behaviour`` keys select fault paths."""

    def __init__(self, world: FakeWorld, clock: FakeClock, source_md5: str, candidate_md5: str, **behaviour):
        self.world, self.clock = world, clock
        self.source_md5, self.candidate_md5 = source_md5, candidate_md5
        self.b = behaviour
        self.calls: list[str] = []
        self.next_pid = 900
        self.ready_at = None

    def _spawn(self, md5: str, holds=frozenset({'/dev/watchdog', '/dev/video0'})) -> Identity:
        self.next_pid += 1
        proc = Proc(self.next_pid, int(self.clock.now() * 100) + 5000, md5, 'sdvcam', frozenset(holds))
        self.world.add(proc)
        return Identity(proc.pid, proc.starttime, proc.exe_md5)

    def request_stock_stop(self, ident: Identity) -> None:
        self.calls.append('request_stock_stop')
        if self.b.get('stock_never_exits'):
            return
        self.world.remove(ident.pid)
        if self.b.get('pid_reused'):
            self.world.add(Proc(ident.pid, ident.starttime + 777, 'f' * 32, 'udhcpc'))
        if self.b.get('stock_respawns'):
            self._spawn(self.source_md5)
        if self.b.get('resource_leak'):
            self.world.add(Proc(4242, 1, 'e' * 32, 'helper', frozenset({'/dev/video0'})))

    def release_acknowledged(self, ident: Identity) -> bool:
        return not self.b.get('no_release_ack')

    def launch_candidate(self) -> Identity:
        self.calls.append('launch_candidate')
        ident = self._spawn('0' * 32 if self.b.get('wrong_candidate') else self.candidate_md5)
        if self.b.get('candidate_crashes'):
            self.world.remove(ident.pid)
        self.ready_at = self.clock.now() + self.b.get('ready_after', 1.0)
        return ident

    def candidate_ready(self, ident: Identity) -> bool:
        return not self.b.get('candidate_never_ready') and self.clock.now() >= self.ready_at

    def stop_candidate(self, ident: Identity) -> None:
        self.calls.append('stop_candidate')
        if not self.b.get('candidate_stuck'):
            self.world.remove(ident.pid)

    def launch_stock(self) -> Identity:
        self.calls.append('launch_stock')
        return self._spawn(self.source_md5)

    def stock_ready(self, ident: Identity) -> bool:
        return not self.b.get('restore_never_ready')


class FakeAttemptStore:
    """Durable-claim semantics: write, flush, read back; faults simulate media."""

    def __init__(self, arm: dict | None, **faults):
        self._arm = arm
        self.faults = faults
        self.claims: dict[str, dict] = {}
        self.nonces: set[str] = set()

    def arm(self) -> dict | None:
        return dict(self._arm) if self._arm else None

    def nonce_used(self, nonce: str) -> bool:
        return nonce in self.nonces

    def claimed(self, attempt: str) -> bool:
        return attempt in self.claims

    def claim(self, attempt: str, boot_id: str, nonce: str) -> bool:
        if self.faults.get('write_fails'):
            return False
        record = {'attempt': attempt, 'boot': boot_id, 'nonce': nonce}
        if self.faults.get('partial_write'):
            record = {'attempt': attempt}
        self.claims[attempt] = record
        self.nonces.add(nonce)
        if self.faults.get('readback_mismatch') or record.get('boot') != boot_id:
            return False  # consumed but not confirmed: refuse, never re-arm implicitly
        return True

    def reboot(self) -> None:
        """A new boot: the arm is never honoured again for a claimed attempt."""
        if self._arm and self._arm.get('attempt') in self.claims:
            self._arm = None
