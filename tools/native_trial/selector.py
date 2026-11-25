"""Python reference model of native/activation/S00cammy-select.proposal.

The camera would run the shell proposal; this model exists so every branch can
be fault-injected exhaustively (including power loss at every primitive) and
checked for equivalence against the relocated shell under ``dash``. It only
ever runs against a :class:`native_trial.faketree.TreeEnv`. There is no real
environment adapter, by design.

Decisions: ``candidate`` (bind-mounted for this boot), ``stock``,
``recovery-required`` (stock S01app will still run; operator review needed),
``noop`` (hook already ran this boot) and ``silent`` (not enabled; no output).

``select_gate`` mirrors the proposal's SELECT_GATE. The shipped value is
``blocked`` (activation ineligible after the WP5 review): ``mode=select`` is
refused with ``select-blocked`` before payload staging, card access, claim writes or mount actions.
RAM scratch/self-test and diagnostic status writes can precede refusal.
Tests pass ``open`` only to exercise the blocked design's remaining logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import records as R

PROPOSAL = 'native/activation/S00cammy-select.proposal'

# Every reason the proposal can print, for documentation and tests.
REASONS = (
    'disabled', 'run-not-tmpfs', 'boot-id-invalid', 'state-invalid', 'already-ran-this-boot',
    'observe-write-failed', 'post-attempt-boot', 'attempt-limit', 'allowlist-invalid',
    's01app-mismatch', 'already-bound', 'source-mismatch', 'vfat-premounted', 'tf-unavailable',
    'arm-missing', 'arm-invalid', 'attempt-replayed', 'unacknowledged-attempt',
    'candidate-not-allowlisted', 'class-ineligible', 'candidate-mismatch', 'memory-low',
    'copy-failed', 'copy-mismatch', 'tf-detach-failed', 'observe-only', 'claim-failed',
    'bind-failed', 'selected', 'bind-verify-failed', 'bind-unrestorable', 'not-enabled',
    'lib-dir-not-tmpfs', 'lib-dir-exists', 'library-mismatch', 'library-copy-failed',
    # WP5 review additions.
    'select-blocked', 'tools-unverified', 'run-noexec', 'lib-dir-noexec', 'app-already-running',
    'bind-unrestorable-executable',
)
SHIPPED_SELECT_GATE = 'blocked'
_EARLY_REASONS = ('disabled', 'run-not-tmpfs', 'run-noexec', 'app-already-running')


@dataclass(frozen=True)
class Result:
    decision: str
    reason: str
    attempt: str = 'none'

    def line(self) -> str:
        if self.decision == 'silent':
            return ''
        if self.decision in ('noop',):
            return f'cammy-select: decision=noop reason={self.reason} attempt={self.attempt}'
        if self.reason in _EARLY_REASONS:
            return f'cammy-select: decision=stock reason={self.reason}'
        return f'cammy-select: decision={self.decision} reason={self.reason} attempt={self.attempt}'


class _Finish(Exception):
    def __init__(self, result: Result):
        self.result = result


_VFAT = re.compile(r'(?<![A-Za-z0-9_])vfat(?![A-Za-z0-9_])')


class Selector:
    def __init__(self, env, select_gate: str = SHIPPED_SELECT_GATE):
        self.env = env
        self.select_gate = select_gate
        p = env.p
        self.CFG = p('/etc/cammy')
        self.STATE = p('/etc/cammy/native-select')
        self.S01 = p('/etc/init.d/S01app')
        self.APP = p('/usr/bin/sdvcam')
        self.RUNROOT = p('/run')
        self.RUNDIR = p('/run/cammy-select')
        self.PROCFS = p('/proc')
        self.DEVDIR = p('/dev')
        self.TMPROOT = p('/tmp')
        self.LIBROOT = p(R.LIBRARY_ROOT)
        self.libdir = ''
        self.lib_made = False
        self.run_ok = False
        self.tfmnt = ''
        self.boot = ''
        self.mode = ''
        self.t0 = ''

    # ---- shell helper mirrors --------------------------------------------
    def top_mount(self, mountpoint: str) -> str:
        """Mirror of awk '$2 == m { t = $3 " " $4 } END { print t }'."""
        top = ''
        if not self.env.awk_ok():
            return top  # a missing/broken awk prints nothing -> "not tmpfs" -> stock
        for line in self.env.mounts():
            fields = line.split()
            if len(fields) > 1 and fields[1] == mountpoint:
                top = (fields[2] if len(fields) > 2 else '') + ' ' + (fields[3] if len(fields) > 3 else '')
        return top

    @staticmethod
    def _ram_state(top: str) -> str:
        """Mirror of case "tmpfs "*noexec*) / "tmpfs "*) / *)."""
        if top.startswith('tmpfs ') and 'noexec' in top[len('tmpfs '):]:
            return 'noexec'
        return 'ok' if top.startswith('tmpfs ') else 'not-tmpfs'

    def bound_ok(self, c_md5: str, lib_copy: str, l_md5: str) -> bool:
        if not self.mounted_at(self.APP) or 'noexec' in self.top_mount(self.APP):
            return False
        return self.env.md5(self.APP) == c_md5 and self.env.md5(lib_copy) == l_md5

    def mounted_at(self, target: str) -> bool:
        for line in self.env.mounts():
            fields = line.split()
            if len(fields) > 1 and fields[1] == target:
                return True
        return False

    def vfat_mounted(self) -> bool:
        return any(_VFAT.search(line) for line in self.env.mounts())

    def record_ok(self, path: str, keys) -> bool:
        if not self.env.isfile(path) or self.env.islink(path):
            return False
        return R.record_ok(self.env.read(path), tuple(keys))

    def get(self, key: str, path: str) -> str:
        return R.get(self.env.read(path), key)

    def write_record(self, path: str, text: str) -> bool:
        data = (text + '\n').encode('latin-1')
        if not self.env.write_exclusive(path, data):
            return False
        self.env.sync()
        got = self.env.read(path)
        return got is not None and got.decode('latin-1').rstrip('\n') == text

    def detach_tf(self) -> bool:
        if not self.tfmnt:
            return True
        if self.env.umount(self.tfmnt) != 0:
            self.env.umount('-l', self.tfmnt)
        if self.mounted_at(self.tfmnt) or self.vfat_mounted():
            return False
        self.tfmnt = ''
        return True

    def finish(self, decision: str, reason: str, attempt: str = '') -> None:
        if not self.detach_tf():
            if decision == 'candidate':
                self.env.umount(self.APP)
            decision, reason = 'recovery-required', 'tf-detach-failed'
        if decision != 'candidate':
            self.env.rm(f'{self.RUNDIR}/sdvcam')
            if self.lib_made:
                self.env.rm(f'{self.libdir}/{R.LIBRARY_NAME}')
                self.env.rmdir(self.libdir)
        if self.run_ok:
            text = (f'format={R.STATUS_FORMAT}\nmode={self.mode or "none"}\ndecision={decision}\n'
                    f'reason={reason}\nattempt={attempt or "none"}\nboot={self.boot or "unknown"}\n'
                    f't_start={self.t0 or "unknown"}\nt_end={self.env.uptime()}\n')
            self.env.write_status(f'{self.RUNDIR}/status', text.encode('latin-1'))
        raise _Finish(Result(decision, reason, attempt or 'none'))

    # ---- main --------------------------------------------------------------
    def run(self, argument: str = 'start') -> Result:
        try:
            self._run(argument)
        except _Finish as done:
            return done.result
        raise AssertionError('selector fell through')  # pragma: no cover

    def _run(self, argument: str) -> None:
        env = self.env
        if argument not in ('start', 'boot'):
            raise _Finish(Result('silent', 'usage'))
        enabled = f'{self.CFG}/native-select.enabled'
        if not env.isfile(enabled):
            raise _Finish(Result('silent', 'not-enabled'))
        mode = (env.read(enabled) or b'').decode('latin-1').rstrip('\n')
        if mode == 'mode=observe':
            self.mode = 'observe'
        elif mode == 'mode=select':
            self.mode = 'select'
        else:
            raise _Finish(Result('stock', 'disabled'))
        self.t0 = env.uptime()
        run_state = self._ram_state(self.top_mount(self.RUNROOT))
        if run_state != 'ok':
            raise _Finish(Result('stock', 'run-noexec' if run_state == 'noexec' else 'run-not-tmpfs'))
        for name in env.glob(self.PROCFS):
            base = name.rsplit('/', 1)[1]
            comm = f'{name}/comm'
            if base[:1].isdigit() and env.isfile(comm) and \
                    (env.read(comm) or b'').decode('latin-1').rstrip('\n') == 'sdvcam':
                raise _Finish(Result('stock', 'app-already-running'))
        if env.isdir(self.RUNDIR) or env.mkdir(self.RUNDIR):
            self.run_ok = True
        self.boot = (env.read(f'{self.PROCFS}/sys/kernel/random/boot_id') or b'').decode('latin-1').rstrip('\n')
        if not R.is_uuid(self.boot):
            self.finish('stock', 'boot-id-invalid')
        if not (self.run_ok and env.tools_ok()):
            self.finish('stock', 'tools-unverified')
        if self.mode == 'select' and self.select_gate != 'open':
            self.finish('stock', 'select-blocked')

        # Internal one-shot state.
        for f in env.glob(self.STATE):
            name = f.rsplit('/', 1)[1]
            if not (name.startswith('claimed-') or name.startswith('observed-')):
                self.finish('stock', 'state-invalid')
            if not R.is_hex32(name.split('-', 1)[1]):
                self.finish('stock', 'state-invalid')
            if not env.isfile(f) or env.islink(f):
                self.finish('stock', 'state-invalid')
        claims = [f for f in env.glob(self.STATE, 'claimed-') if env.exists(f)]
        for f in claims:
            if self.get('boot', f) == self.boot:
                raise _Finish(Result('noop', 'already-ran-this-boot', f.rsplit('/claimed-', 1)[1]))
        new = False
        for f in claims:
            attempt = f.rsplit('/claimed-', 1)[1]
            if env.exists(f'{self.STATE}/observed-{attempt}'):
                continue
            if not self.write_record(f'{self.STATE}/observed-{attempt}',
                                     f'format={R.OBSERVED_FORMAT}\nattempt={attempt}\nboot={self.boot}'):
                self.finish('stock', 'observe-write-failed', attempt)
            new = True
        if new:
            self.finish('stock', 'post-attempt-boot')
        if len(claims) >= R.MAX_ATTEMPTS:
            self.finish('stock', 'attempt-limit')

        # Internal allowlist.
        al = f'{self.CFG}/native-select.allow'
        if not self.record_ok(al, R.ALLOW_KEYS) or self.get('format', al) != R.ALLOW_FORMAT:
            self.finish('stock', 'allowlist-invalid')
        s01_md5, src_md5, src_size = (self.get(k, al) for k in ('s01app_md5', 'source_md5', 'source_size'))
        ac = self.get('candidate', al)
        a_md5 = ac.split(':', 1)[0]
        rest = ac.split(':', 1)[1] if ':' in ac else ac
        a_size = rest.split(':', 1)[0]
        a_class = rest.split(':', 1)[1] if ':' in rest else rest
        ab = self.get('library', al)
        al_sha = ab.split(':', 1)[0]
        abr = ab.split(':', 1)[1] if ':' in ab else ab
        al_md5 = abr.split(':', 1)[0]
        al_size = abr.split(':', 1)[1] if ':' in abr else abr
        if not (R.is_hex32(s01_md5) and R.is_hex32(src_md5) and R.is_size(src_size)):
            self.finish('stock', 'allowlist-invalid')
        if not (R.is_hex32(a_md5) and R.is_size(a_size) and a_md5 != src_md5):
            self.finish('stock', 'allowlist-invalid')
        if not (R.is_hex64(al_sha) and R.is_hex32(al_md5) and R.is_size(al_size)):
            self.finish('stock', 'allowlist-invalid')
        if a_class not in R.KNOWN_ALLOW_CLASSES:
            self.finish('stock', 'allowlist-invalid')

        if env.md5(self.S01) != s01_md5:
            self.finish('stock', 's01app-mismatch')
        if self.mounted_at(self.APP):
            self.finish('stock', 'already-bound')
        if not (env.size(self.APP) == src_size and env.md5(self.APP) == src_md5):
            self.finish('stock', 'source-mismatch')

        # RAM destinations.
        tmp_state = self._ram_state(self.top_mount(self.TMPROOT))
        if tmp_state == 'noexec':
            self.finish('stock', 'lib-dir-noexec')
        if tmp_state != 'ok':
            self.finish('stock', 'lib-dir-not-tmpfs')
        self.libdir = f'{self.LIBROOT}/{al_sha}'
        if env.exists(self.LIBROOT) or env.islink(self.LIBROOT):
            if not (env.isdir(self.LIBROOT) and not env.islink(self.LIBROOT)):
                self.finish('stock', 'lib-dir-exists')
        if env.exists(self.libdir) or env.islink(self.libdir):
            self.finish('stock', 'lib-dir-exists')

        # TF card: private read-only mount, detached before S01app.
        if self.vfat_mounted():
            self.finish('stock', 'vfat-premounted')
        if not env.mkdir_p(f'{self.RUNDIR}/tf'):
            self.finish('stock', 'tf-unavailable')
        for dev in (f'{self.DEVDIR}/mmcblk0p1', f'{self.DEVDIR}/mmcblk0'):
            if not env.exists(dev):
                continue
            if env.mount('-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec', dev, f'{self.RUNDIR}/tf') == 0:
                self.tfmnt = f'{self.RUNDIR}/tf'
                break
            if self.mounted_at(f'{self.RUNDIR}/tf'):
                self.tfmnt = f'{self.RUNDIR}/tf'
                self.finish('stock', 'tf-unavailable')
        if not self.tfmnt:
            self.finish('stock', 'tf-unavailable')
        d = f'{self.tfmnt}/CammyOS-native'

        arm = f'{d}/arm'
        if not env.exists(arm):
            self.finish('stock', 'arm-missing')
        if not self.record_ok(arm, R.ARM_KEYS):
            self.finish('stock', 'arm-invalid')
        att, c_md5, c_size, c_class, l_sha, l_md5, l_size = (
            self.get(k, arm) for k in ('attempt', 'md5', 'size', 'class', 'lib_sha256', 'lib_md5', 'lib_size'))
        if not (self.get('format', arm) == R.ARM_FORMAT and R.is_hex32(att)):
            self.finish('stock', 'arm-invalid')
        if not (self.get('candidate', arm) == R.CANDIDATE_NAME and R.is_hex32(c_md5) and R.is_size(c_size)):
            self.finish('stock', 'arm-invalid', att)
        if not (R.is_hex64(l_sha) and R.is_hex32(l_md5) and R.is_size(l_size)):
            self.finish('stock', 'arm-invalid', att)
        if env.exists(f'{self.STATE}/claimed-{att}'):
            self.finish('stock', 'attempt-replayed', att)

        for f in claims:
            attempt = f.rsplit('/claimed-', 1)[1]
            ack, obs = f'{d}/ack-{attempt}', f'{self.STATE}/observed-{attempt}'
            if not self.record_ok(ack, R.ACK_KEYS) or not self.record_ok(obs, R.OBSERVED_KEYS):
                self.finish('stock', 'unacknowledged-attempt', att)
            ob = self.get('boot', obs)
            if not (self.get('format', ack) == R.ACK_FORMAT and self.get('attempt', ack) == attempt):
                self.finish('stock', 'unacknowledged-attempt', att)
            if not (self.get('format', obs) == R.OBSERVED_FORMAT and self.get('attempt', obs) == attempt):
                self.finish('stock', 'unacknowledged-attempt', att)
            if not (R.is_uuid(ob) and self.get('observed_boot', ack) == ob):
                self.finish('stock', 'unacknowledged-attempt', att)
            if self.get('outcome', ack) not in R.ACK_OUTCOMES:
                self.finish('stock', 'unacknowledged-attempt', att)

        if not (c_md5 == a_md5 and c_size == a_size and c_class == a_class):
            self.finish('stock', 'candidate-not-allowlisted', att)
        if not (l_sha == al_sha and l_md5 == al_md5 and l_size == al_size):
            self.finish('stock', 'candidate-not-allowlisted', att)
        if c_class not in R.SELECTABLE_CLASSES:
            self.finish('stock', 'class-ineligible', att)
        cand = f'{d}/{R.CANDIDATE_NAME}'
        if not (env.isfile(cand) and not env.islink(cand) and env.size(cand) == c_size and env.md5(cand) == c_md5):
            self.finish('stock', 'candidate-mismatch', att)
        lib = f'{d}/{R.LIBRARY_NAME}'
        if not (env.isfile(lib) and not env.islink(lib) and env.size(lib) == l_size and env.md5(lib) == l_md5):
            self.finish('stock', 'library-mismatch', att)
        avail = '\n'.join(line.split()[1] for line in
                          (env.read(f'{self.PROCFS}/meminfo') or b'').decode('latin-1').splitlines()
                          if len(line.split()) > 1 and line.split()[0] == 'MemAvailable:')
        if not avail or not avail.isdigit() or not avail.isascii():
            self.finish('stock', 'memory-low', att)
        if not (len(avail) <= 9 and int(avail) >= (int(c_size) + int(l_size)) // 1024 * 3 + 1024):
            self.finish('stock', 'memory-low', att)

        copy = f'{self.RUNDIR}/sdvcam'
        env.rm(copy)
        if env.cp(cand, copy) != 0:
            self.finish('stock', 'copy-failed', att)
        if not (env.size(copy) == c_size and env.md5(copy) == c_md5):
            self.finish('stock', 'copy-mismatch', att)
        lib_copy = f'{self.libdir}/{R.LIBRARY_NAME}'
        if not (env.isdir(self.LIBROOT) or env.mkdir(self.LIBROOT)):
            self.finish('stock', 'library-copy-failed', att)
        if not env.mkdir(self.libdir):
            self.finish('stock', 'library-copy-failed', att)
        self.lib_made = True
        if env.cp(lib, lib_copy) != 0:
            self.finish('stock', 'library-copy-failed', att)
        if not (env.size(lib_copy) == l_size and env.md5(lib_copy) == l_md5):
            self.finish('stock', 'library-copy-failed', att)
        if not self.detach_tf():
            self.finish('recovery-required', 'tf-detach-failed', att)

        if self.mode != 'select':
            self.finish('stock', 'observe-only', att)

        if not (env.isdir(self.STATE) or env.mkdir(self.STATE)):
            self.finish('stock', 'claim-failed', att)
        if not self.write_record(f'{self.STATE}/claimed-{att}',
                                 f'format={R.CLAIM_FORMAT}\nattempt={att}\nboot={self.boot}\nmd5={c_md5}'):
            self.finish('stock', 'claim-failed', att)

        # Exec-bit commit (see the proposal).
        if not (env.chmod(copy, 0o444) and env.chmod(lib_copy, 0o444)):
            self.finish('stock', 'bind-failed', att)
        env.mount('-o', 'bind', copy, self.APP)
        if self.bound_ok(c_md5, lib_copy, l_md5):
            if env.chmod(copy, 0o555) and env.executable(self.APP) and self.bound_ok(c_md5, lib_copy, l_md5):
                self.finish('candidate', 'selected', att)
        env.chmod(copy, 0o444)
        if not self.mounted_at(self.APP) and env.md5(self.APP) == src_md5:
            self.finish('stock', 'bind-failed', att)
        env.umount(self.APP)
        if not self.mounted_at(self.APP) and env.md5(self.APP) == src_md5:
            self.finish('stock', 'bind-verify-failed', att)
        if env.executable(self.APP):
            self.finish('recovery-required', 'bind-unrestorable-executable', att)
        self.finish('recovery-required', 'bind-unrestorable', att)


def run_selector(env, argument: str = 'start', select_gate: str = SHIPPED_SELECT_GATE) -> Result:
    return Selector(env, select_gate).run(argument)
