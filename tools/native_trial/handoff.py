"""Late-handoff state machine (currently ineligible) and selected-boot verification.

There is NO real backend in this module. :class:`UnavailableNativeControl` is
the only control shipped and every method raises :class:`BackendUnavailable`.
Tests inject fakes from :mod:`native_trial.fakeproc`. With the current
:data:`CURRENT_CONTRACT` every preflight refuses before any durable write or
control call, so even a mis-wired caller cannot reach a stop or launch.

The machine encodes the failure policy of docs/GUARDED-TF-LAUNCH-PLAN.md:
fresh boot/nonce/lease/candidate-bound request; durable claim before any stop;
identity = (pid, start time, exe md5), never a name or PID file; no second
native app while ownership is ambiguous; one restore attempt, only after
confirmed release; independent overall deadline; recovery-required otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

from . import BackendUnavailable

HARDWARE_RESOURCES = ('/dev/watchdog', '/dev/video0', '/dev/video4', '/dev/video8', '/dev/cedar_dev',
                      '/dev/disp', '/dev/ion', '/dev/ttyS3')
NATIVE_COMM = 'sdvcam'


class State(str, Enum):
    DISARMED = 'DISARMED'
    PREFLIGHT = 'PREFLIGHT'
    ARMED = 'ARMED'
    CLAIMED = 'CLAIMED'
    STOCK_STOPPING = 'STOCK_STOPPING'
    STOCK_RELEASED = 'STOCK_RELEASED'
    CANDIDATE_STARTING = 'CANDIDATE_STARTING'
    READY = 'READY'
    RESTORING_STOCK = 'RESTORING_STOCK'
    FINISHED = 'FINISHED'
    REFUSED = 'REFUSED'
    RECOVERY_REQUIRED = 'RECOVERY_REQUIRED'


@dataclass(frozen=True)
class Proc:
    pid: int
    starttime: int
    exe_md5: str
    comm: str
    holds: frozenset = frozenset()


@dataclass(frozen=True)
class Identity:
    pid: int
    starttime: int
    exe_md5: str


class ProcessView(Protocol):
    def processes(self) -> list[Proc]: ...


class NativeControl(Protocol):
    def request_stock_stop(self, ident: Identity) -> None: ...
    def release_acknowledged(self, ident: Identity) -> bool: ...
    def launch_candidate(self) -> Identity: ...
    def candidate_ready(self, ident: Identity) -> bool: ...
    def stop_candidate(self, ident: Identity) -> None: ...
    def launch_stock(self) -> Identity: ...
    def stock_ready(self, ident: Identity) -> bool: ...


class UnavailableNativeControl:
    """The only shipped control. Real stop/exec/install backends do not exist."""

    def _refuse(self, *args, **kwargs):
        raise BackendUnavailable('native process control is unavailable (late-handoff gates L1-L5 open)')

    request_stock_stop = release_acknowledged = launch_candidate = candidate_ready = _refuse
    stop_candidate = launch_stock = stock_ready = _refuse


@dataclass(frozen=True)
class Contract:
    process_only_shutdown: bool = False
    watchdog_semantics: bool = False
    radio_usb_tf_continuity: bool = False
    stock_restore_profile: bool = False
    resource_release_ack: bool = False

    def unmet(self) -> list[str]:
        return [f'gate:{name}' for name, ok in vars(self).items() if not ok]


CURRENT_CONTRACT = Contract()  # every late-handoff gate open (2026-11-14)


@dataclass(frozen=True)
class Budgets:
    stop: float = 0.0
    ready: float = 0.0
    candidate_stop: float = 0.0
    restore: float = 0.0
    overall: float = 120.0
    poll: float = 0.5

    def unset(self) -> list[str]:
        missing = [f'budget:{n}' for n in ('stop', 'ready', 'candidate_stop', 'restore') if getattr(self, n) <= 0]
        if self.overall <= 0 or self.overall > 120 or self.poll <= 0:
            missing.append('budget:overall')
        elif self.stop + self.ready + self.candidate_stop + self.restore > self.overall:
            missing.append('budget:sum-exceeds-overall')
        return missing


@dataclass(frozen=True)
class Request:
    boot_id: str
    nonce: str
    attempt: str
    candidate_md5: str
    lease_owner: str
    lease_generation: int


@dataclass
class Outcome:
    state: State
    reasons: list[str]
    trace: list[str] = field(default_factory=list)
    restore_attempted: bool = False


def identify(view: ProcessView, expected_md5: str) -> tuple[Identity | None, str]:
    """Exactly one native app with the expected executable and sole resource ownership."""
    procs = view.processes()
    natives = [p for p in procs if p.comm == NATIVE_COMM or p.exe_md5 == expected_md5]
    if not natives:
        return None, 'no-native-app'
    if len(natives) > 1:
        return None, 'competing-native-apps'
    native = natives[0]
    if native.exe_md5 != expected_md5:
        return None, 'unexpected-executable'
    if any(p.pid != native.pid and p.holds & set(HARDWARE_RESOURCES) for p in procs):
        return None, 'competing-resource-owner'
    return Identity(native.pid, native.starttime, native.exe_md5), 'ok'


def alive(view: ProcessView, ident: Identity) -> bool:
    """True only for the same process; a reused PID is a different process."""
    return any((p.pid, p.starttime, p.exe_md5) == (ident.pid, ident.starttime, ident.exe_md5)
               for p in view.processes())


def resources_free(view: ProcessView) -> bool:
    return not any(p.holds & set(HARDWARE_RESOURCES) for p in view.processes())


class Handoff:
    def __init__(self, *, store, view: ProcessView, clock, boot_id: str, source_md5: str,
                 candidate_md5: str, lease_valid: Callable[[str, int], bool],
                 abort_requested: Callable[[], str | None],
                 control: NativeControl | None = None, contract: Contract = CURRENT_CONTRACT,
                 budgets: Budgets = Budgets()):
        self.store, self.view, self.clock = store, view, clock
        self.boot_id, self.source_md5, self.candidate_md5 = boot_id, source_md5, candidate_md5
        self.lease_valid, self.abort_requested = lease_valid, abort_requested
        self.control = control if control is not None else UnavailableNativeControl()
        self.contract, self.budgets = contract, budgets
        self.state = State.DISARMED
        self.trace: list[str] = []
        self.restore_attempted = False

    def _go(self, state: State, note: str = '') -> None:
        self.state = state
        self.trace.append(f'{state.value}{": " + note if note else ""}')

    def _end(self, state: State, *reasons: str) -> Outcome:
        self._go(state, ','.join(reasons))
        return Outcome(state, list(reasons), self.trace, self.restore_attempted)

    def preflight(self) -> list[str]:
        reasons = self.contract.unmet() + self.budgets.unset()
        if isinstance(self.control, UnavailableNativeControl):
            reasons.append('native-backend-unavailable')
        if not reasons:
            _, why = identify(self.view, self.source_md5)
            if why != 'ok':
                reasons.append(why)
        return reasons

    def _wait(self, predicate, budget: float, deadline: float) -> str:
        """'ok', 'timeout' or the abort reason. Never waits past either limit."""
        limit = min(self.clock.now() + budget, deadline)
        while True:
            if predicate():
                return 'ok'
            if self.clock.now() >= limit:
                return 'overall-deadline' if self.clock.now() >= deadline else 'timeout'
            self.clock.sleep(self.budgets.poll)

    def run(self, request: Request) -> Outcome:
        self._go(State.PREFLIGHT)
        reasons = self.preflight()
        if reasons:
            return self._end(State.REFUSED, *reasons)
        arm = self.store.arm()
        checks = [
            (arm is not None and arm.get('attempt') == request.attempt, 'arm-missing-or-different'),
            (request.boot_id == self.boot_id, 'stale-boot'),
            (arm is not None and arm.get('candidate_md5') == request.candidate_md5 == self.candidate_md5,
             'candidate-mismatch'),
            (not self.store.nonce_used(request.nonce), 'nonce-replayed'),
            (not self.store.claimed(request.attempt), 'attempt-replayed'),
            (self.lease_valid(request.lease_owner, request.lease_generation), 'lease-invalid'),
        ]
        failed = [why for ok, why in checks if not ok]
        if failed:
            return self._end(State.REFUSED, *failed)
        self._go(State.ARMED)
        if not self.store.claim(request.attempt, self.boot_id, request.nonce):
            return self._end(State.REFUSED, 'claim-not-durable')
        self._go(State.CLAIMED)
        deadline = self.clock.now() + self.budgets.overall

        stock, why = identify(self.view, self.source_md5)
        if stock is None:
            return self._end(State.REFUSED, why)
        self._go(State.STOCK_STOPPING, f'pid={stock.pid}')
        self.control.request_stock_stop(stock)
        got = self._wait(lambda: not alive(self.view, stock) and resources_free(self.view)
                         and self.control.release_acknowledged(stock), self.budgets.stop, deadline)
        others = [p for p in self.view.processes() if p.comm == NATIVE_COMM and
                  (p.pid, p.starttime) != (stock.pid, stock.starttime)]
        if got != 'ok':
            reasons = [f'stock-stop-{got}'] + (['native-app-present-after-stop'] if others else [])
            return self._end(State.RECOVERY_REQUIRED, *reasons)
        if others:
            return self._end(State.RECOVERY_REQUIRED, 'native-app-present-after-stop')
        self._go(State.STOCK_RELEASED)

        self._go(State.CANDIDATE_STARTING)
        cand = self.control.launch_candidate()
        if cand.exe_md5 != self.candidate_md5:
            return self._cleanup(cand, deadline, 'candidate-identity-mismatch')
        got = self._wait(lambda: self.abort_requested() is not None or not alive(self.view, cand)
                         or self.control.candidate_ready(cand), self.budgets.ready, deadline)
        if got != 'ok':
            return self._cleanup(cand, deadline, f'candidate-ready-{got}')
        if (abort := self.abort_requested()) is not None:
            return self._cleanup(cand, deadline, abort)
        if not alive(self.view, cand):
            return self._cleanup(cand, deadline, 'candidate-exited')
        self._go(State.READY)
        got = self._wait(lambda: self.abort_requested() is not None or not alive(self.view, cand),
                         float('inf'), deadline)
        reason = self.abort_requested() or ('candidate-exited' if not alive(self.view, cand) else got)
        return self._cleanup(cand, deadline, reason)

    def _cleanup(self, cand: Identity, deadline: float, reason: str) -> Outcome:
        self.trace.append(f'cleanup: {reason}')
        if alive(self.view, cand):
            self.control.stop_candidate(cand)
        # Release must be proven even when the overall deadline has passed; give
        # the candidate stop budget, but never start anything after a timeout.
        got = self._wait(lambda: not alive(self.view, cand) and resources_free(self.view),
                         self.budgets.candidate_stop, max(deadline, self.clock.now() + self.budgets.candidate_stop))
        others = [p for p in self.view.processes() if p.comm == NATIVE_COMM or p.exe_md5 == self.candidate_md5]
        if got != 'ok' or others:
            return self._end(State.RECOVERY_REQUIRED, reason, 'candidate-release-unconfirmed')
        if self.restore_attempted:  # pragma: no cover - structural guard
            return self._end(State.RECOVERY_REQUIRED, reason, 'restore-already-attempted')
        self.restore_attempted = True
        self._go(State.RESTORING_STOCK)
        stock = self.control.launch_stock()
        if stock.exe_md5 != self.source_md5:
            return self._end(State.RECOVERY_REQUIRED, reason, 'restore-identity-mismatch')
        got = self._wait(lambda: alive(self.view, stock) and self.control.stock_ready(stock),
                         self.budgets.restore, max(deadline, self.clock.now() + self.budgets.restore))
        if got != 'ok':
            return self._end(State.RECOVERY_REQUIRED, reason, f'restore-{got}')
        return self._end(State.FINISHED, reason, 'stock-restored')


def verify_selected_boot(view: ProcessView, status: dict, boot_id: str, candidate_md5: str,
                         source_md5: str, library_loaded: bool | None = None) -> dict:
    """Read-only acceptance view of an early-selected boot (fake data in tests).

    Requires the RAM status of this boot to say candidate/selected, exactly one
    native app whose executable is the candidate and which holds /dev/watchdog,
    and no other holder of native hardware resources. A living PID is not
    acceptance on its own: LCD, Wi-Fi, portal and bridge checks remain manual.
    Identity is by executable BYTES (exe_md5 of /proc/<pid>/exe), never by name:
    a bind overlay keeps the /usr/bin/sdvcam path and comm. An unknown library
    mapping (None) fails. Full stage-5 acceptance is tools/native_acceptance.
    """
    reasons = []
    if status.get('decision') != 'candidate' or status.get('reason') != 'selected':
        reasons.append('selector-did-not-select')
    if status.get('boot') != boot_id:
        reasons.append('status-from-another-boot')
    ident, why = identify(view, candidate_md5)
    if ident is None:
        reasons.append(why)
    else:
        native = next(p for p in view.processes() if p.pid == ident.pid)
        if '/dev/watchdog' not in native.holds:
            reasons.append('watchdog-not-held-by-selected-app')
    if any(p.exe_md5 == source_md5 for p in view.processes()):
        reasons.append('stock-app-also-running')
    if library_loaded is False:
        reasons.append('bridge-library-not-mapped')
    elif library_loaded is not True:
        # Missing evidence is not success (WP5): the mapping must be observed.
        reasons.append('bridge-library-mapping-unproven')
    return {'ok': not reasons, 'reasons': reasons, 'identity': ident.__dict__ if ident else None,
            'manual_checks': ['LCD normal view', 'home Wi-Fi unchanged', 'portal reachable',
                              'bridge hello/status only', 'portal hardware capabilities still unavailable'],
            'evidence_class': 'fake' if getattr(view, 'fake', False) else 'unspecified'}
