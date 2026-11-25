"""Camera moves for C3 (0.7.4): follow me, show the camera, look around.

Stock M105 behaviour these rely on (static RE of sdvcam, 2026-11-16, not yet seen live):
- A short DOWN (zoom_out) press in the preview toggles face follow. "On" is never saved,
  so every boot starts with it off. Pan presses are ignored while it is on, and turn_180
  switches it off.
- 0.7.4 reports the flag as face status 'tracking' (true/false; null on older firmware),
  and cammy-ui shows the live view while it is on.
- Pan holds move at the stock speed with no angle, and the gimbal has end stops (about
  350 degrees), so "look around" goes to one end, sweeps to the other end looking as it
  goes, and comes back to the middle.

Follow, turn-around and the sweep share one asyncio lock (`motion`): a second motion while
one runs, or any motion while a photo or video is being taken, is refused, not queued.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

log = logging.getLogger('c3.moves')

SWEEP_S = 7.5         # one end stop to the other: about 7 s at ml_device_motor_speed 1,
                      # timed on Cammy 2026-11-16 from its live view (P15 frames); padded
LOOKS = 6             # frames taken across one sweep
ZOOM_STEPS = 30       # digital zoom index 0..30; cmd 4004 par=0 steps one out (RE 2026-11-16)
RENEW_S = 0.1         # pan holds last 300 ms on the portal; renew at 10 Hz
TOGGLE_SETTLE_S = 1.5
TURN_SETTLE_S = 2.5   # the head takes about two seconds to swing round
FOLLOW_POLL_S = 2.0   # follow state is read this often only while follow is on


class MotionRefused(RuntimeError):
    """Another motion or a capture is running; nothing was pressed."""


class Moves:
    def __init__(self, body, face, eyes, sweep_s=SWEEP_S, looks=LOOKS, clock=time.monotonic, stock=None):
        self.body, self.face, self.eyes = body, face, eyes
        self.stock = stock            # cammy_capture.Native: stock HTTP API (zoom)
        self.cammy = body.cammy
        self.sweep_s, self.looks, self.clock = sweep_s, looks, clock
        self.belief = False           # follow state when the firmware does not report it
        self.sweeping = False
        self.known = None             # last 'tracking' read from the camera
        self.motion = asyncio.Lock()  # follow, turn-around and sweep: one at a time
        self.capture_busy = lambda: False   # set by the service: a photo or video is running
        self.stats = {'follow_toggles': 0, 'sweeps': 0, 'sweep_errors': 0, 'turns': 0, 'refused': 0}

    # ---- one motion at a time ----------------------------------------------------------
    @property
    def busy(self):
        return self.sweeping or self.motion.locked()

    def refusal(self):
        """Why a motion cannot start now, or None."""
        if self.capture_busy():
            return 'a photo or video is being taken'
        if self.sweeping:
            return 'already looking around'
        if self.motion.locked():
            return 'the camera is already moving'
        return None

    @asynccontextmanager
    async def moving(self):
        """Hold the motion lock, or raise MotionRefused at once (never waits)."""
        reason = self.refusal()
        if reason:
            self.stats['refused'] += 1
            raise MotionRefused(reason)
        async with self.motion:       # free, so this does not yield before the lock is held
            yield

    async def set_follow(self, on, settle=TOGGLE_SETTLE_S):
        """follow() under the motion lock. Returns (ok, message); a refusal is (False, reason)."""
        try:
            async with self.moving():
                return await asyncio.to_thread(self.follow, on, settle)
        except MotionRefused as exc:
            return False, str(exc)

    async def turn_around(self, settle=TURN_SETTLE_S):
        """The one turn-around for the voice tool and the API: press the stock turn-180 button,
        wait for the head to swing round, and note that it switched face follow off.
        Returns (ok, message); camera errors raise."""
        try:
            async with self.moving():
                await asyncio.to_thread(self.body.turn_around)
                self.stats['turns'] += 1
                await asyncio.sleep(settle)
                self.belief = False   # stock turn_180 also switches face follow off
                if self.known:
                    self.known = False
        except MotionRefused as exc:
            return False, str(exc)
        return True, 'Turned around. Use look to see what you now face.'

    # ---- follow me ---------------------------------------------------------------------
    def tracking(self):
        """True/False from the camera, or None when this firmware does not report it."""
        try:
            value = self.cammy.face_status().get('tracking')
        except Exception:  # noqa: BLE001 - unknown, not fatal
            return None
        self.known = value if isinstance(value, bool) else None
        return self.known

    def following(self):
        known = self.tracking()
        return self.belief if known is None else known

    def follow(self, on, settle=TOGGLE_SETTLE_S):
        """Blocking. Returns (ok, message)."""
        on = bool(on)
        known = self.tracking()
        if (self.belief if known is None else known) == on:
            return True, 'face follow is already %s' % ('on' if on else 'off')
        self.cammy.input_button('zoom_out', 'tap')
        self.stats['follow_toggles'] += 1
        time.sleep(settle)
        after = self.tracking()
        if after is not None and after != on:
            return False, ('the camera did not switch face follow %s (it refuses at some aspect '
                           'ratios, and only works in the live preview)' % ('on' if on else 'off'))
        self.belief = on
        if after is None:             # older firmware: cammy-ui cannot mirror it, so C3 does
            self.face.set_view('follow', on)
        return True, 'face follow is %s' % ('on' if on else 'off')

    # ---- show the camera ---------------------------------------------------------------
    def show_camera(self, on):
        self.face.set_view('camera', bool(on))
        return 'the screen shows the %s' % ('live camera view' if on else 'face')

    # ---- look around -------------------------------------------------------------------
    async def _hold(self, button, seconds):
        end = self.clock() + seconds
        try:
            while self.clock() < end:
                await asyncio.to_thread(self.cammy.input_button, button, 'hold')
                await asyncio.sleep(RENEW_S)
        finally:
            await asyncio.to_thread(self.cammy.input_button, None, 'release')

    def zoom_out_fully(self):
        """Blocking, about 3 s: widest view for the sweep photos (Adam 2026-11-16)."""
        if self.stock is None:
            return False
        for _ in range(ZOOM_STEPS):
            self.stock.zoom_out_step()
            time.sleep(0.1)
        return True

    async def look_around(self):
        """Sweep end to end with looks. Returns a list of (label, jpeg). One motion at a time:
        raises MotionRefused while another runs or a capture is being taken."""
        async with self.moving():
            return await self._look_around()

    async def _look_around(self):
        self.sweeping = True
        frames = []
        was_following = False
        try:
            was_following = await asyncio.to_thread(self.following)
            if was_following:          # pans are ignored while face follow is on
                await asyncio.to_thread(self.follow, False)
            try:                                                # widest view first
                await asyncio.to_thread(self.zoom_out_fully)
            except Exception as exc:  # noqa: BLE001 - a narrower sweep beats none
                log.info('zoom out failed: %s', exc)
            await self._hold('left', self.sweep_s)              # to the left end stop
            async def sample(index, after, wait):
                frame = await self.eyes.look(wait=wait, after=after)
                if frame:
                    label = '%d of %d, from your far left to your far right' % (index + 1, self.looks)
                    frames.append((label, frame))

            async def sample_in_motion():
                start = self.clock()
                step = self.sweep_s / max(1, self.looks - 1)
                for index in range(1, self.looks - 1):
                    target = start + index * step
                    await asyncio.sleep(max(0, target - self.clock()))
                    await sample(index, time.time() - 0.5, min(step, 1.0))

            if self.looks:
                await sample(0, time.time() + 0.5, 5.0)
            async with asyncio.TaskGroup() as sweep:
                sweep.create_task(self._hold('right', self.sweep_s))
                sweep.create_task(sample_in_motion())
            if self.looks > 1:
                await sample(self.looks - 1, time.time() + 0.5, 5.0)
            await self._hold('left', self.sweep_s / 2)          # back to the middle
            self.stats['sweeps'] += 1
        except Exception:
            self.stats['sweep_errors'] += 1
            raise
        finally:
            self.sweeping = False
            if was_following:
                try:
                    await asyncio.to_thread(self.follow, True)
                except Exception as exc:  # noqa: BLE001
                    log.info('could not restore face follow: %s', exc)
        return frames

    async def run(self):
        """Keep the follow state fresh for status: read it once per bring-up (a recycled lease
        keeps it, a reboot clears it), then only while it is on (a button press can switch it
        off). Turning it on goes through follow(), which reads it anyway."""
        epoch = None
        while True:
            await self.body.up.wait()
            current = getattr(self.body, 'epoch', None)
            if current != epoch or self.known:   # older firmware never reports it
                epoch = current
                if not self.busy:     # follow() and the sweep read it themselves
                    await asyncio.to_thread(self.tracking)
            await asyncio.sleep(FOLLOW_POLL_S)

    @property
    def state(self):
        return {'following': self.belief if self.known is None else self.known,
                'reported': self.known is not None,
                'camera_view': 'camera' in self.face.views, 'sweeping': self.sweeping,
                'sweep_s': self.sweep_s, **self.stats}
