"""C3 body: everything Cammy Control Center does on the camera, under one lease.

One `Cammy` portal client owns the only control lease. `Body.run()` supervises it for
hours: prepare native media (once per boot), take the lease, start the preview (Cammy's
mic needs the preview event session), then renew every 15 s and check the preview every
10 s. A failed renewal is retried; once the lease is older than 45 s without a successful
renewal, or the preview fails, everything is brought up again with backoff (1, 2, 5, 10,
30 s). Nothing in the body ends the service: losses are logged and repaired.

Parts that ride on the lease:
- `mic()`: Cammy's microphone as 3200-byte chunks (16 kHz S16LE mono, 100 ms), reopened
  after the portal's 600 s stream cap, a stall or a bring-up.
- `Speaker`: one portal speaker job per reply, appended gaplessly; hands every PCM block
  to Echo (the reference) just before posting it.
- `Face`: connected intents (hidden, speaking, listening, looking, waiting, settled).
- `Eyes`: the preview's single RTSP decoder (1 fps, native size) whenever the body is up,
  awake or not; `look()` waits up to 4 s for a fresh frame and `history()` reads back the
  selected Brain profile's on-disk frames (host/c3/profile_preview_router.py), or a
  memory-only ring when no profile gate is configured (host/c3/preview_history.py).
- captures: `host/cammy_capture.py` (stock camera API, port 8082) with face hide hooks.
"""
import asyncio
import logging
import math
import os
import signal
import subprocess
import sys
import threading
import time
from http.client import HTTPException
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
ROOT = HOST.parent
for p in (str(ROOT / 'sdk'), str(HOST)):
    if p not in sys.path:
        sys.path.insert(0, p)
from cammy import Cammy, CammyError  # noqa: E402
import cammy_voicefx  # noqa: E402
from cammy_capture import CaptureError, PHOTO, VIDEO  # noqa: E402
from cammy_vision import VisionError  # noqa: E402
from .preview_history import HISTORY_BYTES, Frame, FrameRing, JpegStream  # noqa: E402

log = logging.getLogger('c3.body')
CHUNK = 3200                     # 100 ms of 16 kHz S16 mono
LEASE_TTL, RENEW_S, LEASE_STALE_S = 60, 15, 45
SUSPECT_PROBE_MIN_S = 30  # a symptom (mic stall, decoder exit) renews at once, at most this often
PREVIEW_CHECK_S = 10
CAPTURE_POLL_ACTIVE_S = 0.5
CAPTURE_POLL_IDLE_S = 2.0
BODY_TICK_S = 0.5
BATTERY_POLL_S = 300
# After a camera reboot, media preparation (a stock write) waits this long and for 5 s of a
# steady stock mode. C3 first reaches a rebooted camera at about 55 s of uptime, when its
# portal starts, and the stock app settled by 17 s (chooser timeline), so 45 s here only kept
# Cammy offline until 121 s of uptime (2026-11-24, boot aa2cfb6e).
MEDIA_GUARD_S = 10
STOCK_MODE_STABLE_S = 5
# A stock mode other than video or photo (2026-11-24: mode 1, left by a stock boot) that
# stays put this long, not recording, is switched to video like photo mode is. Before,
# C3 waited on it forever and Cammy's face never came.
ODD_MODE_SWITCH_S = 30
STOCK_MODE_POLL_S = 1
MEDIA_BOOT_CHECK_S = 5
STOCK_MODE_SWITCH_TIMEOUT_S = 8
PORTAL_TIMEOUT_S = 10
MIC_STREAM_CAP_S = 600
MIC_REOPEN_S = 0.3  # let the portal reap the previous capture child
MIC_STREAMED_S = 3.0  # a stream that lasted this long worked; its end is not a failure
BACKOFF = (1, 2, 5, 10, 30)
CAPTURE_SETTLE_S = 2  # after C3's own capture, before the decoder reconnects
PIECE_BYTES = 4 * 32000        # speaker: 4 s of 16 kHz mono per portal call (limit 5 s)
MODE_SETTLE_S = 20             # after a stock mode switch, before media preparation
FRAME_DIR = Path('/run/user/%d/cammy-c3' % os.getuid())
SPEAKER_BUSY_S = 16  # maximum wait for an earlier piece or job to clear
SPEAKER_JOB_GRACE_S = 5
SPEAKER_FIRST_CHECK_S = 0.5   # one status read this long after a job starts (catches refusals)
SPEAKER_END_EARLY_S = 0.3     # then none until this long before the audio's expected end
SPEAKER_POLL_S = 0.2
SPEAKER_TAIL_S = 1.3  # includes room/mic echo delay and margin for guardian startup
SPEAKER_FINISH_MARGIN_S = 0.25
SPEAKER_MIN_BYTES = 1600  # portal accepts at least 50 ms per piece
SPEAKER_FAILURE_LIMIT = 3
RUNNING_SPEAKER_PHASES = ('running', 'queued', 'starting', 'playing', 'cancelling')
LINK_LOG_INTERVAL_S = 10
SPEAKER_RETRY = ('queue full', 'already active', 'stream closed', 'stream limit')  # portal 409s that clear
PREVIEW_OK = ('sdp-ready', 'event-connected', 'checking-sdp', 'waiting-for-listener', 'event-connecting')

TRANSPORT_ERRORS = (OSError, HTTPException)
PORTAL_ERRORS = (CammyError, *TRANSPORT_ERRORS)


def _stale_boot(exc):
    return isinstance(exc, CammyError) and exc.status == 409 and str(exc) == 'stale camera boot binding'


class LeaseLost(RuntimeError):
    def __init__(self, cause, *, binding_lost=True):
        super().__init__('lease lost: %s' % cause)
        self.binding_lost = binding_lost


async def _finish_thread_call(func, *args, interrupt=None, discard=None):
    """On cancellation, let the worker finish before its resources are cleaned up."""
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if interrupt is not None:
            interrupt()
        try:
            result = await task
        except Exception:  # the caller has already been cancelled
            pass
        else:
            if discard is not None:
                await asyncio.to_thread(discard, result)
        raise


def _die_with_parent():
    import ctypes
    ctypes.CDLL('libc.so.6', use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


class Body:
    def __init__(self, portal, token, label='Cammy Control Center', client=None, clock=time.monotonic, stock=None):
        self.cammy = client or Cammy(portal, token, agent='c3', timeout=PORTAL_TIMEOUT_S, label=label)
        self.clock = clock
        self.stock = stock              # cammy_capture.Native: the stock camera API (mode before prepare)
        self.up = asyncio.Event()       # lease held and preview running
        self.epoch = 0                  # bumps on every bring-up; parts restart on change
        self.state = {'lease': 'none', 'preview': 'stopped', 'mic': 'closed', 'bringups': 0,
                      'lease_losses': 0, 'renew_failures': 0, 'mic_reopens': 0, 'last_error': '',
                      'battery': None, 'media_hold': None}
        self.mic_samples = 0
        self.last_mic_chunk = None
        self._renewed = 0.0
        self._owns_lease = False
        self._loss = None
        self._stopping = threading.Event()
        self.rtsp = None
        self.capture_transition = lambda: False
        self.conversation_active = lambda: False
        self._battery_due = -math.inf
        self._sampled_bind = False
        self._media_guard_until = None
        self.on_rebind = lambda: None   # a new boot starts in a fresh stock mode
        self.on_mic_reset = lambda: None
        self.capture = (0, True)  # sequence and permission; zero is legacy firmware
        self.last_state = None    # (clock, dict): the newest /v1/state reply, about 2 s old while up
        self._suspect = None      # a symptom asking _watch to renew now
        self._probed = -math.inf

    def _keep_state(self, status):
        if isinstance(status, dict):
            self.last_state = (self.clock(), status)
        return status

    def suspect(self, reason):
        """A part saw a symptom of a dead camera (mic stall, decoder exit): renew the lease now
        instead of waiting up to LEASE_STALE_S. A transport failure of that renewal is a loss.
        Rate-limited, so a flaky link causes at most one extra bring-up per SUSPECT_PROBE_MIN_S."""
        if self.up.is_set() and self._suspect is None and self.clock() - self._probed >= SUSPECT_PROBE_MIN_S:
            self._suspect = reason

    def _capture_status(self, status):
        previous = self.capture
        seq = status.get('capture_sequence')
        if type(seq) is int and 2 <= seq <= 0xffff_ffff:
            self.capture = (seq, not (seq & 1))
        elif self.capture[0]:
            self.capture = (self.capture[0], False)
        self.state['capture_muted'] = not self.capture[1]
        if self.up.is_set() and self.capture != previous:
            self.last_mic_chunk = None
            self.on_mic_reset()

    # ---- lease + preview supervisor -------------------------------------------------
    def _pause(self, seconds=0):
        if self._stopping.wait(seconds):
            raise InterruptedError('body is stopping')

    def _refresh_battery(self):
        read = getattr(self.stock, 'battery', None)
        self._battery_due = self.clock() + BATTERY_POLL_S
        if read is None:
            return
        try:
            battery = read()
        except (OSError, HTTPException, ValueError, VisionError, CaptureError) as exc:
            battery = {'level': None, 'external_power': None, 'warning': None}
            log.info('stock battery unavailable: %s', type(exc).__name__)
        else:
            report = log.warning if battery['warning'] else log.info
            report('stock battery: level %s, external power %s%s', battery['level'],
                   battery['external_power'], ' (low)' if battery['warning'] else '')
        self.state['battery'] = dict(battery, checked_at=time.time())

    def _hold_media(self, reason):
        if self.state['media_hold'] != reason:
            log.info('media preparation deferred: %s', reason)
            self.state['media_hold'] = reason

    def _prepare_media(self):
        """rc3+ leaves native media uninitialized until asked, once per boot; the portal
        accepts the request only while no lease is held."""
        lock = getattr(self.stock, 'mode_lock', None)
        if self.capture_transition() or (lock and not lock.acquire(blocking=False)):
            raise RuntimeError('stock mode owned by camera capture')
        try:
            self._prepare_media_locked()
        finally:
            if lock:
                lock.release()

    def _prepare_media_locked(self):
        try:
            status = self.cammy.media_status()
        except CammyError as exc:
            if exc.status in (404, 501):  # the optional endpoint is absent on older portals
                return
            raise
        if status.get('ready') or status.get('phase') in ('ready', 'prepared'):
            self.state['media_hold'] = None
            self._media_guard_until = None
            return
        if status.get('phase') in ('failed', 'cancelled', 'requires-recovery') or status.get('requires_recovery'):
            # one try per boot by design (docs/STATUS.md): only a power cycle clears it
            raise RuntimeError('media %s: Cammy needs a power cycle' % status.get('phase'))
        if status.get('phase') in ('unprepared', 'refused'):
            # 'refused' (0.7.4): the preflight declined before touching the camera; retry
            self._await_safe_stock_mode()
            self._pause()
            log.info('requesting native media preparation: %s', status.get('phase'))
            self.cammy.prepare_media()
            self.state['media_hold'] = None
            self._media_guard_until = None
        deadline = self.clock() + 90
        while self.clock() < deadline:
            self._pause()
            status = self.cammy.media_status()
            if status.get('ready') or status.get('phase') in ('ready', 'prepared'):
                return
            if status.get('phase') in ('failed', 'cancelled', 'requires-recovery') or status.get('requires_recovery'):
                raise RuntimeError('media preparation failed: %s' % status.get('phase'))
            if status.get('phase') == 'refused':
                raise RuntimeError('media preparation refused by the camera; retrying')
            self._pause(1)
        raise RuntimeError('media preparation timed out')

    def _await_safe_stock_mode(self):
        """Wait for a guarded boot or an unusual mode to settle before any stock write."""
        if self.stock is None:
            # Without a stock mode reader, a rebooted device cannot be proven safe.
            if self._media_guard_until is not None:
                raise RuntimeError('stock mode unavailable after reboot; media preparation waits')
            return
        stable_mode, stable_since = None, None
        odd_mode, odd_since = None, None
        next_boot_check = self.clock() + MEDIA_BOOT_CHECK_S
        while True:
            self._pause()
            now = self.clock()
            if self._media_guard_until is not None and now >= next_boot_check:
                self.cammy.state()  # existing boot binding detects another reset during the hold
                next_boot_check = now + MEDIA_BOOT_CHECK_S
            if self.stock is not None and now >= self._battery_due:
                self._refresh_battery()
            mode, recording = self.stock.state()
            if recording or mode in (VIDEO, PHOTO):
                odd_mode = None
            elif mode != odd_mode:
                odd_mode, odd_since = mode, now
            elif now - odd_since >= ODD_MODE_SWITCH_S:
                break
            if recording or mode not in (VIDEO, PHOTO):
                stable_mode = None
                reason = 'stock camera recording' if recording else 'stock mode %s outside video/photo' % mode
                if self._media_guard_until is None:
                    self._media_guard_until = now + MEDIA_GUARD_S
                self._hold_media(reason)
            elif self._media_guard_until is None:
                break
            else:
                if mode != stable_mode:
                    stable_mode, stable_since = mode, now
                if now >= self._media_guard_until and now - stable_since >= STOCK_MODE_STABLE_S:
                    break
            self._pause(STOCK_MODE_POLL_S)
        if mode == VIDEO:
            return
        log.info('stock camera in %s mode; switching to video mode before preparing media',
                 'photo' if mode == PHOTO else 'unexpected %s' % mode)
        self._pause()
        # Verify boot and ownership before using the unbound stock write.
        if self.cammy.state().get('owner') is not None:
            raise RuntimeError('media preparation waits for the camera lease to clear')
        self.stock.set_mode(VIDEO)
        deadline = self.clock() + STOCK_MODE_SWITCH_TIMEOUT_S
        while self.clock() < deadline:
            self._pause(BODY_TICK_S)
            if self.stock.state() == (VIDEO, False):
                # Switching mode restarts the stock event server (:9999).
                self._pause(MODE_SETTLE_S)
                if self.stock.state() == (VIDEO, False):
                    return
                break
        raise RuntimeError('the stock camera did not settle in video mode')

    def _bring_up(self):
        state = self._keep_state(self.cammy.state())
        if not self._sampled_bind or self.clock() >= self._battery_due:
            self._refresh_battery()
            self._sampled_bind = True
        self._capture_status(state)
        owner = state.get('owner')
        if owner is None:
            self._prepare_media()
        self._pause()
        started = self.clock()
        self.cammy.acquire(ttl=LEASE_TTL)
        self._owns_lease = True
        self._renewed = started
        self.state['lease'] = 'held'
        self._pause()
        self.rtsp = self.cammy.preview_start()['rtsp_url']
        deadline = self.clock() + 20
        while self.clock() < deadline:
            self._pause()
            status = self.cammy.preview_status()
            self._check_preview(status)
            if status.get('sdp_ready'):
                self.state['preview'] = 'ready'
                return
            self._pause(0.5)
        raise RuntimeError('preview did not become ready')

    def _check_preview(self, status):
        phase = status.get('phase')
        self.state['preview'] = phase or '?'
        if not status.get('preview_active', True) or phase not in PREVIEW_OK:
            reason = status.get('failure_reason')
            raise RuntimeError('preview %s%s' % (phase, ': ' + reason if reason else ''))

    def _clear_up(self):
        was_up = self.up.is_set()
        self.up.clear()
        self._suspect = None          # a symptom of the old link needs no probe of the next one
        self.last_mic_chunk = None
        self.rtsp = None
        self.state.update(preview='stopped', mic='closed')
        if was_up:
            self.on_mic_reset()

    def _release_lease(self):
        if not self._owns_lease:
            return
        self._owns_lease = False
        # This single portal operation cancels speaker, input, face, audio and preview.
        try:
            self.cammy.release()
        except Exception as exc:  # best effort; the portal also expires the lease
            if _stale_boot(exc):
                raise
            log.debug('lease release failed: %s', exc)

    async def run(self):
        failures = 0
        boot_retry = True
        last_up_log = last_down_log = -math.inf
        logged_error = None
        self._stopping.clear()
        try:
            while True:
                up_since = None
                try:
                    await _finish_thread_call(self._bring_up, interrupt=self._stopping.set)
                    self.epoch += 1
                    self.state.update(bringups=self.state['bringups'] + 1, last_error='')
                    self.up.set()
                    up_since = self.clock()
                    if logged_error is not None or up_since - last_up_log >= LINK_LOG_INTERVAL_S:
                        log.info('up (epoch %d)', self.epoch)
                        last_up_log, logged_error = up_since, None
                    self._sampled_bind = False  # sample again at the next bring-up
                    await self._watch()
                except Exception as exc:  # every camera loss is repaired within this task
                    # A brief successful preview does not reset the backoff during flapping.
                    if up_since is not None and self.clock() - up_since >= LEASE_STALE_S:
                        failures, boot_retry = 0, True
                    self._clear_up()
                    self.state['lease'] = 'lost'
                    error = '%s: %s' % (type(exc).__name__, str(exc)[:160])
                    now = self.clock()
                    if error != logged_error and now - last_down_log >= LINK_LOG_INTERVAL_S:
                        log.warning('down: %s', error)
                        last_down_log, logged_error = now, error
                    self.state['last_error'] = error
                    lost = isinstance(exc, LeaseLost) or _stale_boot(exc)
                    if lost:
                        self.state['lease_losses'] += self._owns_lease
                        self._owns_lease = False
                        if isinstance(exc, LeaseLost) and exc.binding_lost:
                            self._forget_lease()
                    elif isinstance(exc, TRANSPORT_ERRORS):
                        # Ownership is uncertain on an outage. Keep the SDK nonce for
                        # reacquisition on this boot and let the portal expire it.
                        self._owns_lease = False
                    else:
                        try:
                            await _finish_thread_call(self._release_lease)
                        except CammyError as stale:
                            exc = stale
                    self._loss = None
                    if _stale_boot(exc):
                        self.rebind()
                        if boot_retry:
                            boot_retry = False
                            continue
                    await asyncio.sleep(BACKOFF[min(failures, len(BACKOFF) - 1)])
                    failures += 1
        finally:
            self._clear_up()

    def _forget_lease(self):
        if hasattr(self.cammy, '_lease_binding'):
            self.cammy._lease_binding = None
            self.cammy._lease_active = False

    def rebind(self):
        """Clear the SDK's cached identity before discovery of the new boot."""
        self._forget_lease()
        if hasattr(self.cammy, '_boot_binding'):
            self.cammy._boot_binding = None
        if hasattr(self.cammy, '_speaker_peak'):
            self.cammy._speaker_peak = None
        self.capture = (0, True)
        self.last_state = None
        self._sampled_bind = False
        self.state['battery'] = None
        self._media_guard_until = self.clock() + MEDIA_GUARD_S
        self._hold_media('camera rebooted; waiting for stable stock mode')
        self.on_rebind()
        self.state['rebinds'] = self.state.get('rebinds', 0) + 1
        log.info('camera rebooted: binding to the new boot')

    def _current(self, epoch):
        return self.up.is_set() and self.epoch == epoch

    def _report_loss(self, exc, epoch):
        """An owner-scoped operation can detect a lost binding between renewals."""
        if not self._current(epoch):
            return
        if _stale_boot(exc):
            self._loss = exc
        elif isinstance(exc, CammyError) and (
            exc.status in (401, 403) or exc.status == 409 and str(exc) in (
                'active control lease required for X-Agent-ID', 'current control lease binding required'
            )
        ):
            self._loss = LeaseLost(exc)
        if self._loss is not None:
            self._clear_up()

    def _check_loss(self):
        if self._loss is not None:
            raise self._loss

    def recycle_lease(self, epoch):
        if self._current(epoch) and self._loss is None:
            # Ordinary Body.run recovery releases our lease, then acquires a new one.
            self._loss = RuntimeError('speaker jobs repeatedly failed')
            self._clear_up()

    async def _watch(self):
        next_renew = self._renewed + RENEW_S
        next_check = self.clock() + PREVIEW_CHECK_S
        last_state_poll = -math.inf
        while True:
            self._check_loss()
            now = self.clock()
            probing, self._suspect = self._suspect, None
            if probing is not None:
                self._probed = now
                next_renew = now
                log.info('probing the camera: %s', probing)
            interval = CAPTURE_POLL_ACTIVE_S if self.conversation_active() else CAPTURE_POLL_IDLE_S
            if self.capture[0] and now - last_state_poll >= interval:
                last_state_poll = now
                try:
                    status = self._keep_state(await asyncio.to_thread(self.cammy.state))
                except PORTAL_ERRORS as exc:
                    # One failed poll says nothing about capture permission: keep the last
                    # known state. The camera itself cuts audio on a capture-mode change.
                    if _stale_boot(exc):
                        raise
                else:
                    self._capture_status(status)
            now = self.clock()
            if now >= next_renew:
                try:
                    await _finish_thread_call(self.cammy.renew, LEASE_TTL)
                except PORTAL_ERRORS as exc:
                    self.state['renew_failures'] += 1
                    if _stale_boot(exc):
                        raise
                    if isinstance(exc, CammyError) and exc.status in (401, 403, 409):
                        raise LeaseLost(exc) from exc
                    if probing is not None and isinstance(exc, TRANSPORT_ERRORS):
                        raise LeaseLost('%s; renew failed: %s' % (probing, exc), binding_lost=False) from exc
                    if self.clock() - self._renewed >= LEASE_STALE_S:
                        raise LeaseLost(exc, binding_lost=False) from exc
                    next_renew = self.clock() + 3
                    await asyncio.sleep(1)
                    continue
                self._renewed, next_renew = now, now + RENEW_S
                self._check_loss()
                now = self.clock()
            if now >= next_check and not self.capture_transition():
                next_check = now + PREVIEW_CHECK_S
                try:
                    status = await asyncio.to_thread(self.cammy.preview_status)
                except PORTAL_ERRORS as exc:
                    if _stale_boot(exc):
                        raise
                    if isinstance(exc, CammyError) and exc.status in (401, 403):
                        raise LeaseLost(exc) from exc
                else:
                    self._check_loss()
                    self._check_preview(status)
            if self.stock is not None and self.clock() >= self._battery_due:
                await _finish_thread_call(self._refresh_battery)
            await asyncio.sleep(BODY_TICK_S)

    def release(self):
        """Service exit: give back our lease and stop the media it owns."""
        if not isinstance(self._loss, LeaseLost) and not _stale_boot(self._loss):
            try:
                self._release_lease()
            except CammyError:  # a stale boot has already discarded the lease
                pass
        else:
            self._owns_lease = False
        self.state['lease'] = 'released'

    def turn_around(self):
        """Stock turn-180 button: toggles between facing Adam and facing forward."""
        self.cammy.input_button('turn_180', 'tap')

    # ---- microphone ----------------------------------------------------------------
    @staticmethod
    def _close_mic(stream):
        try:
            stream.close()
        except TRANSPORT_ERRORS as exc:
            log.debug('mic close failed: %s', exc)

    def _read_mic(self, stream):
        # HTTPResponse.read() can wait indefinitely for a full chunk if the socket
        # keeps delivering small reads before its inactivity timeout.
        read = getattr(stream, 'read1', stream.read)
        deadline = self.clock() + PORTAL_TIMEOUT_S
        chunk = bytearray()
        while len(chunk) < CHUNK:
            data = read(CHUNK - len(chunk))
            if not data:
                break
            chunk.extend(data)
            if len(chunk) < CHUNK and self.clock() >= deadline:
                raise TimeoutError('no complete microphone chunk before deadline')
        return bytes(chunk)

    async def mic(self):
        """Endless 100 ms chunks of Cammy's mic. Never raises for camera trouble."""
        failures, epoch = 0, None
        while True:
            await self.up.wait()
            capture = self.capture
            if not capture[1]:
                self.state['mic'] = 'muted'
                await asyncio.sleep(0.25)
                continue
            if epoch != self.epoch:
                failures, epoch = 0, self.epoch
            try:
                stream = await _finish_thread_call(self.cammy.open_audio_stream, discard=self._close_mic)
            except PORTAL_ERRORS as exc:
                self._report_loss(exc, epoch)
                if self.capture != capture or str(exc) == 'physical capture mute is on':
                    await asyncio.sleep(0.25)
                    continue
                if self._current(epoch):
                    failures += 1
                    self.state['mic'] = 'reopening'
                    if failures in (1, 5) or failures % 30 == 0:
                        log.warning('mic will not open (%d): %s', failures, exc)
                    await asyncio.sleep(BACKOFF[min(failures - 1, len(BACKOFF) - 1)])
                continue
            opened = self.clock()
            clean_eof = False
            try:
                if self._current(epoch) and self.capture == capture:
                    self.state['mic'] = 'open'
                while self._current(epoch) and self.capture == capture:
                    try:
                        chunk = await _finish_thread_call(self._read_mic, stream)
                    except TRANSPORT_ERRORS as exc:
                        log.warning('mic stalled: %s', exc)
                        self.suspect('mic stalled')
                        break
                    if not self._current(epoch) or self.capture != capture:
                        break
                    if len(chunk) < CHUNK:
                        clean_eof = True
                        break
                    self.mic_samples += CHUNK // 2
                    self.last_mic_chunk = self.clock()
                    yield chunk
            finally:
                self.on_mic_reset()
                await asyncio.to_thread(self._close_mic, stream)
                self.state['mic'] = 'closed'
            if not self._current(epoch) or self.capture != capture:
                continue
            self.state['mic'] = 'reopening'
            self.state['mic_reopens'] += 1
            duration = self.clock() - opened
            log.info('mic stream ended after %.0f s; reopening', duration)
            if duration >= MIC_STREAMED_S:
                # It streamed: the 600 s cap, or the portal's 1 s 'slow-reader' cut when Wi-Fi
                # queues back up under video and reply audio. Reopen at once; the old backoff
                # climbed to 30 s over a few such cuts and left Cammy deaf (2026-11-23).
                failures = 0
                await asyncio.sleep(MIC_REOPEN_S)
            else:
                failures += 1
                await asyncio.sleep(BACKOFF[min(failures - 1, len(BACKOFF) - 1)])


class Speaker:
    """Streams each reply to Cammy as one gapless portal job (first block opens it, the rest
    append). `voicefx.Stream` keeps filters, compressor and gain continuous across blocks.
    `muted()` is true while a job plays plus `tail` seconds (half-duplex fallback)."""

    def __init__(self, body, level='normal', tail=SPEAKER_TAIL_S, echo=None, loop=None, moves=None):
        self.body, self.cammy, self.level, self.tail = body, body.cammy, level, tail
        self.echo = echo
        self.loop = loop
        self.moves = moves
        self.sending = False
        self.queue = asyncio.Queue()
        self.busy_until = 0.0
        self.active = False
        self.epoch = body.epoch
        self.generation = 0
        self.chunks = 0
        self.seconds = 0.0
        self.chain = None
        self.last_outcome = None
        self.failure_reason = None
        self.consecutive_failures = 0
        self.last_finished = None
        self.lease_recycles = 0
        self.jobs = 0
        self.job_started = None   # clock time the current job's first piece was accepted
        self.status_reads = 0
        self._dropped = None

    @property
    def failing(self):
        return self.consecutive_failures >= SPEAKER_FAILURE_LIMIT

    def muted(self):
        return self.active or self.sending or not self.queue.empty() or self.body.clock() < self.busy_until

    def say(self, pcm24, end=False):
        """Queue a block of 24 kHz reply audio; end=True closes the reply (adds the tail)."""
        self.queue.put_nowait((self.generation, self.body.epoch, pcm24, end))

    def flush(self):
        """Barge-in or interruption: drop queued audio, cancel the playing job."""
        self.generation += 1
        while not self.queue.empty():
            self.queue.get_nowait()
        self.chain = None
        if self.echo is not None:
            self.echo.cancel()
        if self.active and self.body._current(self.epoch):
            asyncio.get_running_loop().run_in_executor(None, self._cancel, self.epoch)

    def _cancel(self, epoch):
        if not self.body._current(epoch):
            return
        try:
            self.cammy.speaker_cancel()
        except Exception:  # noqa: BLE001
            pass

    save_dir = None          # set by the debug API: replies go to WAV files there
    _raw = _sent = b''

    def _save_reply(self):
        import wave
        stamp = time.strftime('%H%M%S')
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name, data, rate in (('raw', self._raw, 24000), ('sent', self._sent, 16000)):
                with wave.open(str(self.save_dir / ('reply-%s-%s.wav' % (stamp, name))), 'wb') as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(bytes(data))
        except OSError as exc:
            log.info('could not save the reply: %s', exc)
        self._raw = self._sent = b''

    def _send_blocking(self, pcm, new_job, epoch, generation):
        if not self.body._current(epoch) or generation != self.generation:
            return False
        if self.echo is not None and self.loop is not None:
            self.loop.call_soon_threadsafe(self.echo.play, pcm, new_job)
        deadline = time.monotonic() + SPEAKER_BUSY_S
        while self.body._current(epoch) and generation == self.generation:
            try:
                self.cammy.play_pcm(pcm, append=not new_job)
                return True
            except CammyError as exc:
                # 'stream closed' (the guardian's feeder closed the job first: nothing queued) and
                # 'stream limit' (the job's shared 60 s total) clear when that job ends; the
                # retried append then opens a new job.
                if exc.status != 409 or not any(msg in str(exc) for msg in SPEAKER_RETRY):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('speaker stayed busy: %s' % exc) from exc
                time.sleep(min(0.2, remaining))
                if time.monotonic() >= deadline:
                    raise RuntimeError('speaker stayed busy: %s' % exc) from exc
        return False

    def _clear_job(self):
        self.active = False
        self.busy_until = 0.0
        self.chain = None
        if self.echo is not None:
            self.echo.cancel()

    def _failed(self, epoch, outcome, reason):
        self.last_outcome, self.failure_reason = outcome, reason
        self.consecutive_failures += 1
        log.warning('speaker %s (%d consecutive failures): %s', outcome, self.consecutive_failures, reason)
        if self.consecutive_failures % SPEAKER_FAILURE_LIMIT == 0 and self.body._current(epoch):
            self.lease_recycles += 1
            self.body.recycle_lease(epoch)

    async def run(self):
        while True:
            generation, epoch, pcm24, end = await self.queue.get()
            while ((self.sending or self.moves and self.moves.sweeping)
                   and self.body._current(epoch) and generation == self.generation):
                await asyncio.sleep(0.1)
            if not self.body._current(epoch) or generation != self.generation:
                continue
            if self._dropped == (epoch, generation):
                if end:
                    self._dropped = None
                continue
            if self.epoch != epoch:
                self._clear_job()
                self.epoch = epoch
            if self.chain is None:
                self.chain = cammy_voicefx.Stream(self.level)
            pcm = self.chain.feed(pcm24, end=end)
            if self.save_dir is not None:          # debug: keep this reply as Gemini sent it and as played
                self._raw += pcm24
                self._sent += pcm
                if end:
                    self._save_reply()
            if end:
                self.chain = None
            if not pcm:
                continue
            try:
                # A reply starting while the last one plays appends to its job.
                self.sending = True
                for offset in range(0, len(pcm), PIECE_BYTES):
                    piece = pcm[offset:offset + PIECE_BYTES]
                    if len(piece) < SPEAKER_MIN_BYTES:
                        piece += bytes(SPEAKER_MIN_BYTES - len(piece))
                    new_job = not self.active
                    sent = await _finish_thread_call(self._send_blocking, piece, new_job, epoch, generation)
                    if not sent or not self.body._current(epoch) or generation != self.generation:
                        break
                    self.active = True
                    now = self.body.clock()
                    if new_job:
                        self.jobs += 1
                        self.job_started = now
                    start = now if new_job else max(now, self.busy_until - self.tail)
                    self.busy_until = start + len(piece) / 32000 + self.tail
                    self.chunks += 1
                    self.seconds += len(piece) / 32000
            except Exception as exc:  # one lost reply is logged, never fatal
                self.body._report_loss(exc, epoch)
                self._failed(epoch, 'send-failed', str(exc) or type(exc).__name__)
                if self.active and self.body._current(epoch):
                    await _finish_thread_call(self._cancel, epoch)
                self._clear_job()
                self._dropped = (epoch, generation) if not end else None
            finally:
                self.sending = False

    def _status_due(self, job, now):
        """Read the job's phase once SPEAKER_FIRST_CHECK_S after it starts, then not until
        SPEAKER_END_EARLY_S before its audio should end (busy_until less the tail; appends move
        it), then every tick. The length is known from the PCM, so the middle needs no polls."""
        started = self.job_started if self.job_started is not None else -math.inf
        if job[1] < 1:
            return now >= started + SPEAKER_FIRST_CHECK_S
        return now >= self.busy_until - self.tail - SPEAKER_END_EARLY_S

    async def watch(self):
        """While a job is active, read its phase as _status_due schedules; when it ends, keep
        the half-duplex tail. A job that outlives its audio by SPEAKER_JOB_GRACE_S is stuck."""
        job = None                    # [job key, status reads of that job]
        while True:
            await asyncio.sleep(SPEAKER_POLL_S)
            if not self.body._current(self.epoch):
                if self.active or self.chain is not None:
                    self._clear_job()
                continue
            if not self.active or self.sending:
                continue
            key = (self.epoch, self.jobs)
            if job is None or job[0] != key:
                job = [key, 0]
            now = self.body.clock()
            if not self._status_due(job, now) and now < self.busy_until + SPEAKER_JOB_GRACE_S:
                continue
            epoch = self.epoch
            generation = self.generation
            chunks = self.chunks
            remaining = self.busy_until + SPEAKER_JOB_GRACE_S - now
            status = {}
            if remaining > 0:
                job[1] += 1
                self.status_reads += 1
                try:
                    status = await asyncio.wait_for(asyncio.to_thread(self.cammy.speaker_status), remaining)
                except PORTAL_ERRORS as exc:
                    self.body._report_loss(exc, epoch)
            if (self.epoch != epoch or self.generation != generation or not self.body._current(epoch)
                    or self.sending or chunks != self.chunks):
                continue
            now = self.body.clock()
            phase = status.get('phase')
            if phase and phase not in RUNNING_SPEAKER_PHASES:
                self.active = False
                self.last_outcome = phase
                if phase == 'finished':
                    # Keep the existing deadline. Allow brief settling if the
                    # guardian finished later than the queued-duration estimate.
                    self.busy_until = max(self.busy_until, now + min(self.tail, SPEAKER_FINISH_MARGIN_S))
                    self.last_finished = now
                    self.failure_reason = None
                    self.consecutive_failures = 0
                else:
                    self.busy_until = now + self.tail
                if phase not in ('finished', 'cancelled'):
                    if self.chain is not None:
                        self.chain = None
                        self._dropped = (epoch, self.generation)
                    if self.echo is not None:
                        self.echo.cancel()
                    reason = status.get('reason')
                    self._failed(epoch, phase, reason if reason and reason != 'none' else phase)
            elif now >= self.busy_until + SPEAKER_JOB_GRACE_S:
                self.sending = True
                try:
                    await _finish_thread_call(self._cancel, epoch)
                finally:
                    if self.chain is not None and generation == self.generation:
                        self._dropped = (epoch, generation)
                    self._clear_job()
                    self.sending = False
                self._failed(epoch, 'stuck', 'job-stuck')


FACE_TICK_S = 0.3
FACE_HOLD_S = 0.8     # a calm pose (listening, waiting, looking, neutral) holds this long
FACE_URGENT = ('hide', 'speaking')   # hiding and the speaking loop never wait for the hold


def face_intent(*, hidden=False, speaking=False, listening=False, looking=False, waiting=False):
    """The one face Cammy should show now, from local state only (no model call).
    Priority: hidden view > speaker playback > listener gate > look > reply pending > settled."""
    if hidden:
        return 'hide'
    if speaking:
        return 'speaking'
    if listening:
        return 'listening'
    if looking:
        return 'looking'
    if waiting:
        return 'waiting'
    return 'neutral'


class Face:
    """Show an awake face while connected, including during voice idle.

    The firmware plays the sleep loop when it releases or times out the face lease.
    """
    POSES = {'listening': 'happy', 'looking': 'doubt', 'waiting': 'doubt'}   # intent -> mood

    def __init__(self, body, clock=time.monotonic):
        self.body, self.cammy = body, body.cammy
        self.clock = clock
        self.shown = None
        self.command_lock = threading.Lock()
        self.changed_at = 0.0
        self.intent = None           # the intent the shown command was sent for
        self.epoch = -1
        self.looking_until = 0.0
        self.views = set()            # reasons to show the live view: capture, camera, follow
        self.available = None
        self.commands = 0

    def want(self, speaker, voice=None):
        listener = getattr(voice, 'listener', None)
        waiting = getattr(voice, 'waiting', None)
        return face_intent(
            hidden=self.hidden, speaking=bool(speaker.active),
            listening=bool(getattr(listener, 'speaking', False)),
            looking=self.clock() < self.looking_until,
            waiting=bool(waiting()) if callable(waiting) else False)

    def _command(self, want):
        if want == 'speaking':
            if self.speaking_animation:
                return (None, 'speak')
            return ('happy', 'show')
        if want == 'neutral':
            return (None, 'idle')
        if want == 'hide':
            return (None, want)
        return (self.POSES.get(want, want), 'show')

    def _set(self, want, epoch):
        with self.command_lock:
            return self._set_locked('hide' if 'capture' in self.views else want, epoch)

    def _set_locked(self, want, epoch):
        if self.epoch != epoch:     # the portal restarts the face with the lease
            try:
                status = self.cammy.face_status()
            except CammyError as exc:
                if exc.status not in (404, 501):
                    raise
                status = {}
            self.available = bool(status.get('available'))
            self.speaking_animation = bool(status.get('speaking_animation'))
            self.epoch, self.shown, self.intent = epoch, None, None
        if not self.body._current(epoch) or not self.available:
            return
        command = self._command(want)
        if command == self.shown:
            self.intent = want      # e.g. listening -> speaking fallback: the same happy pose
            return
        now = self.clock()
        if (self.shown is not None and want not in FACE_URGENT and self.intent not in FACE_URGENT
                and now - self.changed_at < FACE_HOLD_S):
            return                  # a flicker of the gate or the reply flag does not churn the LCD
        self.cammy.face(*command)
        self.commands += 1
        self.shown, self.intent, self.changed_at = command, want, now

    async def run(self, speaker, voice=None):
        failures, epoch = 0, None
        while True:
            await self.body.up.wait()
            if epoch != self.body.epoch:
                failures, epoch = 0, self.body.epoch
            want = self.want(speaker, voice)
            try:
                await asyncio.to_thread(self._set, want, epoch)
            except PORTAL_ERRORS as exc:
                self.body._report_loss(exc, epoch)
                if failures == 0:
                    log.info('face %s refused: %s', want, exc)
                failures += 1
                await asyncio.sleep(BACKOFF[min(failures - 1, len(BACKOFF) - 1)])
            else:
                failures = 0
                await asyncio.sleep(FACE_TICK_S)

    @property
    def hidden(self):
        return bool(self.views)

    def set_view(self, reason, on):
        """Show the stock live view instead of the face while any reason holds."""
        (self.views.add if on else self.views.discard)(reason)

    # capture hooks (host/cammy_capture.py)
    def hide_face(self):
        # Capture is the only view that must establish a GUI barrier before a
        # stock mode write. Serialize it with the normal face command loop.
        from cammy_capture import CaptureError
        with self.command_lock:
            self.set_view('capture', True)
            try:
                epoch = self.body.epoch
                queued = self.cammy.face(action='hide').get('request_id')
                if type(queued) is not int:
                    raise CaptureError('face hide request was not acknowledged')
                self.shown = (None, 'hide')
                deadline = self.clock() + 3.0
                while self.clock() < deadline and self.body._current(epoch):
                    status = self.cammy.face_status()
                    # 2 = hidden; 3 = suspended across a stock change, which removes the canvas first.
                    if status.get('phase') in (2, 3) and status.get('gui_applied_request_id', 0) >= queued:
                        return
                    time.sleep(0.05)
            except PORTAL_ERRORS as exc:
                raise CaptureError('face hide could not be confirmed') from exc
        raise CaptureError('face hide could not be confirmed')

    def show_face(self):
        with self.command_lock:
            self.set_view('capture', False)


EYES_TICK_S = 0.5            # supervisor period
EYES_FIRST_FRAME_S = 15.0    # RTSP connect + first keyframe before a new decoder counts as stalled
EYES_STALL_S = 5.0           # at 1 fps: five missing frames
EYES_MIN_SPACING_S = 0.5     # frames closer than this are a catch-up burst; their times would lie
EYES_HEALTHY_FRAMES = 5      # a decoder that delivered this many frames resets the backoff
EYES_FRESH_S = 4.0           # look()'s default freshness
EYES_MARK = 'cammy-c3-eyes'  # in the decoder's command line, so a leaked one can be recognised
EYES_READ = 65536


EYES_FRAME_MAX_DISTANCE_S = 2.0   # history_frame(): a stored frame farther than this is a gap


class Eyes:
    """The preview's single RTSP decoder: one ffmpeg, 1 fps at native size, JPEGs on a pipe.

    It runs whenever the body is up (lease held, preview ready), independent of Voice. The
    decoder's PID is recorded and a leaked one (from a crashed service) is killed before a new
    one starts, since the stock RTSP server serves one client. A stalled or exited decoder is
    restarted with backoff (1, 2, 5, 10, 30 s); a new epoch restarts it without backoff.

    Live frames: Eyes holds exactly one decoded frame in memory, the newest accepted one.
    `newest()` / `look()` return it only while it came from the current decoder in the current
    body epoch and the body is up, so a disconnect, a bring-up or a restart ends freshness at once.

    History has two modes:
    - Profile mode (production; `configure_profile_history(active_profile)`): every accepted
      frame is handed, from the decoder's reader thread and outside the Eyes lock, to a
      `ProfilePreviewRouter` that writes it to the selected Brain profile's workspace (250 MB on
      disk, kept across disconnects and service restarts) and drops it when no profile is
      selected. No frames are kept in RAM besides the live one, and there is no memory fallback:
      without a router (stopped, or it failed to open) the history is empty. `history()` and
      `history_frame()` read the disk (call them in a worker thread); the router pins each read
      to one profile binding and returns nothing if the selection changed during it.
      `history_status` reads only the router's in-memory counters.
    - Memory mode (no profile gate configured; tests and tools): the legacy in-process
      `FrameRing` of at most 250,000,000 JPEG bytes, dropped by `stop()`.

    `stop()` is the service's exit: it stops the decoder and closes the router (flushing its
    writer) or drops the memory ring.

    `wanted` is kept for status compatibility only; it no longer gates the decoder.
    """

    def __init__(self, body, capacity_bytes=HISTORY_BYTES, clock=time.monotonic, wall=time.time,
                 frame_dir=None, command=None):
        self.body = body
        self.wanted = False
        self.clock, self.wall = clock, wall
        self.dir = Path(frame_dir) if frame_dir is not None else FRAME_DIR
        self.dir.mkdir(mode=0o700, exist_ok=True)
        self.frame = self.dir / 'latest.jpg'        # the old grabber's output: removed, never written
        self.pid_file = self.dir / 'grabber.pid'
        self.command = command or self.ffmpeg_command
        self.capacity_bytes = capacity_bytes
        self.ring = FrameRing(capacity_bytes)      # memory mode only; None in profile mode
        self.proc = None
        self.restarts = 0
        self.stats = {'starts': 0, 'exits': 0, 'stalls': 0, 'start_errors': 0, 'burst_drops': 0,
                      'discarded_bytes': 0, 'accepted': 0, 'submit_errors': 0}
        self._lock = threading.Lock()
        self._gen = 0              # decoder generation; frames of an older one are ignored
        self._live = None          # (generation, body epoch) of the decoder whose frames are live
        self._capture = (0, True)
        self._latest = None        # the newest accepted Frame: the only decoded frame Eyes keeps
        self._started = 0.0        # monotonic start of the current decoder
        self._last = None          # monotonic time of its newest accepted frame
        self._gen_frames = 0
        self._failures = 0         # consecutive failed decoders, for the backoff
        # C3's own photo/video capture drives the stock camera API (cammy_capture), which
        # switches the camera out of video mode and ends the RTSP stream without moving the
        # portal's capture_sequence. The service sets this to the capture's busy flag; while
        # it is true the decoder is halted as a pause, not a failure.
        self.paused = lambda: False
        self._was_paused = False
        self._next_start = 0.0
        self._reader = None
        self._profile_mode = False  # once set, never falls back to the memory ring
        self.history_recording = lambda: True
        self._router = None
        self._stopped = False
        self.history_error = ''
        self.last_error = ''

    # ---- profile history --------------------------------------------------------------
    def configure_profile_history(self, active_profile, router_factory=None):
        """Persist history only for the selected profile: `active_profile()` returns
        (id, name, workspace Path) or None. Switches Eyes to profile mode for good: the memory
        ring is dropped and never used again, even if the router cannot be opened."""
        if not callable(active_profile):
            raise TypeError('active_profile must be callable')
        with self._lock:
            old, ring = self._router, self.ring
            self._router, self.ring, self._profile_mode = None, None, True
            stopped = self._stopped
        if ring is not None:
            ring.clear()
        if old is not None:
            self._close_router(old)
        if stopped:
            return
        router, error = None, ''
        try:
            if router_factory is None:
                from .profile_preview_router import ProfilePreviewRouter as router_factory
            router = router_factory(active_profile, self.capacity_bytes)
        except Exception as exc:  # noqa: BLE001 - no history beats a crashed service
            error = 'profile history unavailable (%s)' % type(exc).__name__
            log.warning('eyes: %s: %s', error, str(exc)[:160])
        with self._lock:
            self.history_error = error
            if not self._stopped:
                self._router, router = router, None
        if router is not None:          # stop() ran meanwhile
            self._close_router(router)

    @staticmethod
    def _close_router(router):
        try:
            router.close()
        except Exception as exc:  # noqa: BLE001 - exit continues regardless
            log.warning('eyes: closing the history router failed: %s', str(exc)[:160])

    def _history_source(self):
        with self._lock:
            return self._profile_mode, self._router, self.ring

    @property
    def profile_mode(self):
        return self._profile_mode

    @staticmethod
    def ffmpeg_command(rtsp):
        # select (not fps=1): it only drops frames, so a stall never yields synthetic duplicates.
        return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                # video only: '-an' alone still SETUPs the AAC track and pulls it over Wi-Fi
                '-rtsp_transport', 'tcp', '-allowed_media_types', 'video',
                '-timeout', '10000000', '-fflags', 'nobuffer', '-threads', '1', '-i', rtsp,
                '-an', '-sn', '-dn', '-vf', "select='isnan(prev_selected_t)+gte(t-prev_selected_t,0.99)'",
                '-fps_mode', 'passthrough', '-q:v', '5', '-metadata', 'title=' + EYES_MARK,
                '-f', 'image2pipe', '-c:v', 'mjpeg', 'pipe:1']

    # ---- the decoder process ----------------------------------------------------------
    def _kill_stale(self):
        try:
            pid = int(self.pid_file.read_text())
            cmdline = Path('/proc/%d/cmdline' % pid).read_bytes()
        except (OSError, ValueError):
            return
        if b'ffmpeg' in cmdline and (EYES_MARK.encode() in cmdline or str(self.frame).encode() in cmdline):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def _start(self, epoch):
        rtsp = self.body.rtsp
        if not rtsp:
            raise OSError('no preview stream yet')
        self._kill_stale()
        proc = subprocess.Popen(self.command(rtsp), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                preexec_fn=_die_with_parent)
        self.pid_file.write_text(str(proc.pid))
        with self._lock:
            self._gen += 1
            gen = self._gen
            self.proc, self._live = proc, (gen, epoch)
            self._capture = getattr(self.body, 'capture', (0, True))
            self._started, self._last, self._gen_frames = self.clock(), None, 0
            self.stats['starts'] += 1
            reader = self._reader = threading.Thread(target=self._read, args=(proc, gen),
                                                     name='eyes-reader-%d' % gen, daemon=True)
        reader.start()

    def _read(self, proc, gen):
        stream = JpegStream()
        try:
            fd = proc.stdout.fileno()
            while True:
                data = os.read(fd, EYES_READ)
                if not data:
                    break
                for jpeg in stream.feed(data):
                    self._ingest(gen, jpeg)
        except (OSError, ValueError):
            pass                      # the pipe closed under us: the decoder is gone
        finally:
            with self._lock:
                self.stats['discarded_bytes'] += stream.discarded + len(stream.buf)
            try:
                proc.stdout.close()
            except OSError:
                pass

    def _ingest(self, gen, jpeg):
        """One complete JPEG from decoder `gen`; True if it was accepted (live, and stored)."""
        mono, wall = self.clock(), self.wall()
        frame = Frame(wall, mono, jpeg, gen)
        with self._lock:
            if (self._live is None or self._live[0] != gen or not self._capture[1]
                    or self._capture != getattr(self.body, 'capture', (0, True))):
                return False          # a halted or superseded decoder
            if self._last is not None and mono - self._last < EYES_MIN_SPACING_S:
                self.stats['burst_drops'] += 1
                return False
            if not self._profile_mode and not self.ring.append(frame):
                return False
            self._latest = frame
            self._last = mono
            self._gen_frames += 1
            self.stats['accepted'] += 1
            if self._gen_frames >= EYES_HEALTHY_FRAMES:
                self._failures = 0
            router = self._router
        if router is not None:
            # Outside the Eyes lock, on this reader thread: the router picks the profile and
            # queues the write; live frames never wait on the disk.
            try:
                if self.history_recording():
                    router.submit(frame)
            except Exception as exc:  # noqa: BLE001 - a store fault never stops the decoder
                with self._lock:
                    self.stats['submit_errors'] += 1
                log.warning('eyes: history submit failed: %s', str(exc)[:160])
        return True

    def _halt(self):
        """Stop the decoder and end live freshness; the history stays."""
        with self._lock:
            proc, reader = self.proc, self._reader
            self.proc = self._reader = self._live = None
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(3)
        if reader is not None and reader is not threading.current_thread():
            reader.join(3)
        self.pid_file.unlink(missing_ok=True)

    def stop(self):
        """Service exit: stop the decoder, then close the profile router (its writer flushes and
        the disk history stays for the next start) or drop the memory ring. Idempotent."""
        self._halt()
        with self._lock:
            self._stopped = True
            router, ring = self._router, self.ring
            self._router, self._latest = None, None
        if ring is not None:
            ring.clear()
        if router is not None:
            self._close_router(router)
        self.frame.unlink(missing_ok=True)

    def _suspect_body(self):
        suspect = getattr(self.body, 'suspect', None)
        if callable(suspect):
            suspect('eyes: ' + self.last_error)

    def _fail(self, now, reason):
        self.restarts += 1
        self._failures += 1
        self.last_error = reason
        self._next_start = now + BACKOFF[min(self._failures, len(BACKOFF)) - 1]

    async def step(self):
        """One supervisor pass; returns what it did (for logs and tests)."""
        body, now = self.body, self.clock()
        capture = getattr(body, 'capture', (0, True))
        with self._lock:
            proc, live, last, started = self.proc, self._live, self._last, self._started
        if not (body.up.is_set() and body.rtsp and capture[1]):
            if proc is not None:
                await asyncio.to_thread(self._halt)
                return 'halted'
            return 'idle'
        if self.paused():
            if not self._was_paused:
                self._was_paused = True
                log.info('eyes: paused for a capture')
            if proc is not None:
                await asyncio.to_thread(self._halt)
            return 'paused'
        if self._was_paused:
            self._was_paused = False
            self._failures = 0
            self._next_start = now + CAPTURE_SETTLE_S     # the camera returns to video mode
            log.info('eyes: capture done; resuming')
        if proc is not None:
            if live is None or live[1] != body.epoch or capture != self._capture:
                self.restarts += 1
                await asyncio.to_thread(self._halt)
                self._next_start = now                      # a new bring-up: no backoff
            elif proc.poll() is not None:
                self.stats['exits'] += 1
                self._fail(now, 'decoder exited with %s' % proc.returncode)
                log.warning('eyes: %s; restarting in %.0f s', self.last_error, self._next_start - now)
                self._suspect_body()
                await asyncio.to_thread(self._halt)
                return 'exited'
            elif now - (started if last is None else last) > (EYES_FIRST_FRAME_S if last is None else EYES_STALL_S):
                self.stats['stalls'] += 1
                self._fail(now, 'no frame for %.0f s' % (now - (started if last is None else last)))
                log.warning('eyes: %s; restarting in %.0f s', self.last_error, self._next_start - now)
                self._suspect_body()
                await asyncio.to_thread(self._halt)
                return 'stalled'
            else:
                return 'running'
        if now < self._next_start:
            return 'backoff'
        try:
            # On the loop's own thread, not a worker: PR_SET_PDEATHSIG fires when the thread
            # that forked the child exits, and this decoder lives for hours.
            self._start(body.epoch)
        except (OSError, subprocess.SubprocessError) as exc:
            self.stats['start_errors'] += 1
            self._fail(now, str(exc)[:160])
            log.warning('eyes: %s', exc)
            return 'error'
        return 'started'

    async def run(self):
        # Resolve the selected profile at startup even if the camera is offline.
        _, router, _ = self._history_source()
        if router is not None:
            router.stats()  # starts the router's background open and eviction
        while True:
            await asyncio.sleep(EYES_TICK_S)
            await self.step()

    # ---- live frames ------------------------------------------------------------------
    def _live_newest(self):
        body = self.body
        with self._lock:
            live, frame = self._live, self._latest
            capture = self._capture
        if (live is None or not body.up.is_set() or body.epoch != live[1] or not capture[1]
                or capture != getattr(body, 'capture', (0, True))):
            return None
        return frame if frame is not None and frame.generation == live[0] else None

    def newest(self, max_age=EYES_FRESH_S, after=0.0):
        """The newest live frame if it is at most max_age s old and arrived at or after
        `after` (time.time()); else None."""
        frame = self._live_newest()
        if frame is not None and self.clock() - frame.mono <= max_age and frame.wall >= after:
            return frame.data
        return None

    async def look(self, wait=EYES_FRESH_S, after=0.0):
        """Newest frame; with after (time.time()), only one grabbed since then."""
        deadline = time.monotonic() + wait
        while True:
            frame = self.newest(after=after)
            if frame or time.monotonic() >= deadline:
                return frame
            await asyncio.sleep(0.25)

    # ---- history ----------------------------------------------------------------------
    def history(self, seconds_ago: float, duration_s: float, limit: int) -> list[tuple[float, bytes]]:
        """Up to `limit` frames, oldest first, as (wall time, JPEG bytes), spread evenly in time
        over the inclusive interval that starts `seconds_ago` s in the past and runs
        `duration_s` s toward the present (clipped at now). Frames survive camera outages,
        so a window may hold frames of an earlier connection; a gap is simply empty.
        Profile mode reads the selected profile's disk store: call it in a worker thread."""
        profile_mode, router, ring = self._history_source()
        if profile_mode:
            if router is None:
                return []
            return list(router.query(seconds_ago, duration_s, limit, now_wall=self.wall()))
        return [(f.wall, f.data) for f in ring.query(seconds_ago, duration_s, limit, self.clock())]

    def history_frame(self, timestamp, max_distance_s=EYES_FRAME_MAX_DISTANCE_S):
        """The stored frame nearest `timestamp` (Unix seconds) as (wall, JPEG bytes), or None
        when nothing lies within `max_distance_s`. Profile mode reads the disk: worker thread."""
        profile_mode, router, ring = self._history_source()
        if profile_mode:
            if router is None:
                return None
            return router.nearest(timestamp, max_distance_s)
        best = min(ring.window(-math.inf, math.inf), key=lambda f: abs(f.wall - timestamp), default=None)
        if best is None or abs(best.wall - timestamp) > max_distance_s:
            return None
        return best.wall, best.data

    @property
    def history_status(self):
        """JSON-ready stats of the history and the decoder. In-memory counters only, never the
        disk, so the C3 status can read it on the event loop."""
        now, now_wall = self.clock(), self.wall()
        profile_mode, router, ring = self._history_source()
        live = self._live_newest()
        with self._lock:
            proc, live_key, last = self.proc, self._live, self._last
            decoder = dict(self.stats, running=proc is not None and proc.poll() is None,
                           pid=proc.pid if proc is not None else None, generation=self._gen,
                           epoch=live_key[1] if live_key else None, restarts=self.restarts,
                           failures=self._failures, last_error=self.last_error,
                           restart_in_s=round(max(0.0, self._next_start - now), 3) if proc is None else 0.0,
                           last_frame_age_s=round(now - last, 3) if last is not None else None)
            stopped, error = self._stopped, self.history_error
        age = (lambda f: round(now - f.mono, 3) if f is not None else None)
        if profile_mode:
            if router is not None:
                stats = dict(router.stats())
                coverage = stats.get('coverage') if isinstance(stats.get('coverage'), dict) else {}
                stats.update(bytes=int(coverage.get('bytes') or 0), frames=int(coverage.get('frames') or 0),
                             oldest=coverage.get('oldest'), newest=coverage.get('newest'))
            else:
                stats = {'profile': None, 'profile_name': None, 'bytes': 0,
                         'capacity_bytes': self.capacity_bytes, 'frames': 0, 'oldest': None, 'newest': None,
                         'error': error or ('stopped' if stopped else 'no history router')}
            oldest, newest = stats.get('oldest'), stats.get('newest')
            wall_age = (lambda t: round(max(0.0, now_wall - t), 3) if isinstance(t, (int, float)) else None)
            history = dict(stats, mode='profile', oldest_time=oldest, newest_time=newest,
                           oldest_age_s=wall_age(oldest), newest_age_s=wall_age(newest),
                           span_s=round(newest - oldest, 3) if wall_age(oldest) is not None
                           and wall_age(newest) is not None else 0.0)
        else:
            stats = ring.stats()
            oldest, newest = stats.pop('oldest'), stats.pop('newest')
            history = dict(stats, mode='memory', profile=None, profile_name=None,
                           oldest_time=oldest.wall if oldest else None, newest_time=newest.wall if newest else None,
                           oldest_age_s=age(oldest), newest_age_s=age(newest),
                           span_s=round(newest.mono - oldest.mono, 3) if oldest else 0.0)
        return dict(history, live=live is not None, live_age_s=age(live),
                    fresh=live is not None and now - live.mono <= EYES_FRESH_S,
                    wanted=self.wanted, decoder=decoder)
