"""Take a picture or a video with Cammy, Adam's way (2026-11-16).

Asked to take a picture or a video, Cammy puts the stock camera in photo or video mode
and hides its face, so the screen shows the stock live view. Adam frames the shot and
presses the red record button himself (for video: once to start, once to stop). The new
file on the TF card is the result: Cammy's face comes back, the file is copied to the PC
(private/captures/<date>/) and Gemini gets the picture, or a few frames of the clip.

Cammy's captures do not stay in the stock media viewer (Adam, 2026-11-16): once the PC copy
is checked, that one file is deleted through the stock app's own delete (cmd 4003), which
also drops its viewer entry and thumbnails. Pictures and videos Adam takes himself, in
Normal mode or otherwise, are never touched: only the file that appeared during this
capture is deleted, and only after it was copied and read back on the PC.

Everything goes through the stock camera's own HTTP API on port 8082 (mode 4 = photo,
0 = video; listing and download of /mnt/extsd/DCIM/100MEDIA/, docs/NATIVE-PHOTO-SNAPSHOTS.md)
and the portal's reversible face hide. No native code, no input grabbing: the record
button keeps its stock behaviour.
"""
import io
import json
import logging
import math
import re
import subprocess
import time
import threading
import urllib.request
from datetime import datetime
from uuid import uuid4
from pathlib import Path

from cammy_vision import DIRECTORY, MODE_NAMES, NativePhoto, VisionError, _Listing, jpeg_payload

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)
CAPTURES = ROOT / 'private/captures'
PHOTO, VIDEO = 4, 0
BATTERY_STATUS_PATH = '/api/getdeviceinfo/?custom=1&cmd=2009'
BATTERY_EXTERNAL_POWER = 5
BATTERY_LOW_LEVEL = 1
MEDIA = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.([Jj][Pp][Ee]?[Gg]|[Mm][Pp]4)')
MAX_VIDEO = 4 * 1024 ** 3      # a 4K clip is ~4 MB/s; 4 GB is ~16 minutes
FOR_GEMINI = 1024              # longest side of the copies Gemini sees
PHOTO_WAIT_S = 60.0            # how long the photo live view waits for Adam's press
LIST_EVERY_S = 3.0             # media listing period while waiting (state is read every poll)


class CaptureError(RuntimeError):
    pass


class _MediaListing(_Listing):
    """The 100MEDIA listing: photos and clips (the vision listing only takes JPEGs)."""

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href') or ''
        name = href[len(DIRECTORY):] if href.startswith(DIRECTORY) else href
        if '..' not in name and '/' not in name and MEDIA.fullmatch(name):
            self.names.add(name)


class Native:
    """Stock camera HTTP API: mode, recording state, media listing and downloads."""

    def __init__(self, endpoint):
        self.photo = NativePhoto(endpoint)
        self.mode_lock = threading.Lock()

    def state(self):
        """(mode, recording). Unlike NativePhoto.status, recording is reported, not refused."""
        mode = json.loads(self.photo._read('/api/getdeviceinfo/?custom=1&cmd=2008'))
        rec = json.loads(self.photo._read('/api/getdeviceinfo/?custom=1&cmd=2005'))
        number, status = mode.get('current_mode'), rec.get('RecodStatus')
        if type(number) is not int or type(status) is not int:
            raise CaptureError('camera status unreadable')
        return number, status != 0

    def battery(self):
        """Read stock battery level. Level 5 denotes detected USB power."""
        report = json.loads(self.photo._read(BATTERY_STATUS_PATH))
        level = report.get('level') if isinstance(report, dict) else None
        if type(level) is not int or not 0 <= level <= BATTERY_EXTERNAL_POWER:
            raise CaptureError('camera battery level unreadable')
        external_power = level == BATTERY_EXTERNAL_POWER
        return {'level': level, 'external_power': external_power,
                'warning': 'Low battery on battery power' if level <= BATTERY_LOW_LEVEL else None}

    def zoom_out_step(self):
        """One digital zoom step out (cmd 4004 par=0; floor 1x). Zoom is a sensor crop with
        index 0..30 and no absolute setter (RE 2026-11-16), so 30 steps reach 1x from anywhere."""
        self.photo._post(4004, 0)

    def set_mode(self, value):
        self.photo.mode(value)

    def stop_recording(self):
        self.photo.shutter()   # cmd 1100 toggles record start/stop in video mode

    def files(self):
        parser = _MediaListing()
        parser.feed(self.photo._read(DIRECTORY, limit=512 * 1024).decode('utf-8', errors='strict'))
        return set(parser.names)

    def delete(self, name):
        """Stock delete of one 100MEDIA file (IOTYPE_CDR_DELET_ONE_FILE): removes the file, its
        .thumb sidecars and its media-viewer row. Its reply is always Value 0, so the caller
        checks the listing. The handler takes everything after the last '=' as the path,
        undecoded: MEDIA names contain neither '=' nor anything that needs quoting."""
        if not MEDIA.fullmatch(name):
            raise CaptureError('invalid media name')
        self.photo._post(4003, DIRECTORY + name)

    def download(self, name, dest, limit=MAX_VIDEO):
        if not MEDIA.fullmatch(name):
            raise CaptureError('invalid media name')
        part = dest.with_name(dest.name + '.part')
        request = urllib.request.Request(self.photo.origin + DIRECTORY + name)
        size = 0
        budget = time.monotonic() + 120.0
        try:
            with self.photo.opener.open(request, timeout=15) as reply, open(part, 'xb') as out:
                while True:
                    if time.monotonic() >= budget:
                        raise CaptureError('media download timed out')
                    chunk = reply.read1(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise CaptureError('file larger than %d bytes' % limit)
                    out.write(chunk)
            part.rename(dest)
        finally:
            part.unlink(missing_ok=True)
        return size


class Capture:
    """One capture at a time. hooks: hide_face() / show_face() (may be no-ops)."""

    def __init__(self, native, hooks, out_dir=CAPTURES, clock=time.monotonic, sleep=time.sleep,
                 photo_wait=PHOTO_WAIT_S, start_wait=120.0, max_video=600.0, poll=1.0, keep_on_card=False,
                 cooldown=30.0):
        self.native, self.hooks, self.out_dir = native, hooks, Path(out_dir)
        self.clock, self.sleep, self.poll = clock, sleep, poll
        self.photo_wait, self.start_wait, self.max_video = photo_wait, start_wait, max_video
        self.keep_on_card = keep_on_card
        self.busy = False
        self.lock = getattr(native, 'mode_lock', None) or threading.Lock()
        self.cooldown = cooldown
        self.next_capture = 0.0
        self.restore_uncertain = False

    def blocked(self):
        if self.restore_uncertain:
            return 'capture halted: stock mode restore was not confirmed'
        if self.busy:
            return 'already taking one'
        if self.clock() < self.next_capture:
            return 'camera capture cooling down'
        return None

    def _remove_from_card(self, name):
        """True once the file is gone from the stock listing."""
        try:
            self.native.delete(name)
            deadline = self.clock() + 5.0
            while self.clock() < deadline:
                if name not in self.native.files():
                    return True
                self.sleep(self.poll)
        except (OSError, ValueError, VisionError, CaptureError):
            pass
        return False

    def _wait_mode(self, want, timeout=6.0):
        deadline = self.clock() + timeout
        stable = False
        while self.clock() < deadline:
            try:
                correct = self.native.state() == (want, False)
            except (OSError, ValueError, VisionError, CaptureError):
                correct = False
            if correct and stable:
                return
            stable = correct
            self.sleep(self.poll)
        raise CaptureError('the camera did not switch to %s mode' % ('photo' if want == PHOTO else 'video'))

    def _wait_file(self, kind, before):
        """Name of the new file once Adam has pressed the record button (and, for video,
        pressed it again), or None on timeout."""
        suffix = ('.jpg', '.jpeg') if kind == 'photo' else ('.mp4',)
        started = None
        listed_at, was_recording = -math.inf, False
        deadline = self.clock() + (self.photo_wait if kind == 'photo' else self.start_wait)
        while self.clock() < (deadline if started is None else started + self.max_video + 30):
            mode, recording = self.native.state()
            if mode != (PHOTO if kind == 'photo' else VIDEO):
                raise CaptureError('stock mode changed during capture')
            if kind == 'video':
                if recording and started is None:
                    started = self.clock()
                if started is not None and recording and self.clock() - started > self.max_video:
                    self.native.stop_recording()   # cap a forgotten recording
            # The listing is up to 512 KB of HTML from sdvcam's single-core web server: read it
            # every LIST_EVERY_S, and at once when a recording stops. Listing it with two state
            # reads every 0.5 s for a 2-minute wait ended in an sdvcam watchdog reset
            # (2026-11-24 05:58, boot aa2cfb6e).
            now = self.clock()
            if not recording and (was_recording or now - listed_at >= LIST_EVERY_S):
                listed_at = now
                new = sorted(n for n in self.native.files() - before if n.lower().endswith(suffix))
                if new:
                    return new[-1]
            was_recording = recording
            self.sleep(self.poll)
        return None

    def run(self, kind):
        """Blocking. Returns dict(kind, name, path, seconds, gemini=[jpeg bytes, ...]) or
        dict(kind, error=...)."""
        if kind not in ('photo', 'video'):
            raise ValueError(kind)
        if not self.lock.acquire(blocking=False):
            return dict(kind=kind, error='already taking one')
        if reason := self.blocked():
            self.lock.release()
            return dict(kind=kind, error=reason)
        self.busy = True
        try:
            return self._run(kind)
        finally:
            self.next_capture = self.clock() + self.cooldown
            self.busy = False
            self.lock.release()

    def _run(self, kind):
        want = PHOTO if kind == 'photo' else VIDEO
        t0 = last = self.clock()

        def phase(name):
            nonlocal last
            now = self.clock()
            log.info('capture %s phase=%s elapsed=%.2fs total=%.2fs', kind, name, now - last, now - t0)
            last = now

        try:
            mode, recording = self.native.state()
            if recording:
                raise CaptureError('the camera is already recording')
            if mode not in MODE_NAMES:
                raise CaptureError('cannot restore the current stock mode')
            before = self.native.files()
            phase('preflight')
            try:
                self.hooks.hide_face()
                phase('face-hidden')
                try:
                    if mode != want:
                        self.native.set_mode(want)
                        self._wait_mode(want)
                    phase('mode-ready')
                    name = self._wait_file(kind, before)
                    phase('shutter')
                finally:
                    self.restore_uncertain = True
                    try:
                        current, recording = self.native.state()
                        if recording:
                            raise CaptureError('camera still recording')
                        if current != mode:
                            self.native.set_mode(mode)
                        self._wait_mode(mode)
                    except (OSError, ValueError, VisionError, CaptureError):
                        raise CaptureError('capture halted: stock mode restore was not confirmed') from None
                    self.restore_uncertain = False
                    phase('restored')
            finally:
                if not self.restore_uncertain:
                    self.hooks.show_face()
            if not name:
                return dict(kind=kind, error='no %s was taken (timed out)' % kind)
            day = self.out_dir / datetime.now().strftime('%Y-%m-%d')
            day.mkdir(parents=True, exist_ok=True)
            original = Path(name)
            path = day / f'{original.stem}-{uuid4().hex}{original.suffix}'
            self.native.download(name, path)
            phase('downloaded')
            if kind == 'photo':
                try:   # drop the M105's 16-byte vendor footer when present
                    path.write_bytes(jpeg_payload(path.read_bytes())[0])
                except VisionError:
                    pass   # unknown framing: keep the file exactly as the camera wrote it
                gemini = [shrink(path.read_bytes())]
                readable = bool(gemini[0])
            else:
                gemini = clip_frames(path)
                readable = bool(gemini)
            on_card = True
            if not self.keep_on_card and readable and path.stat().st_size > 0:
                on_card = not self._remove_from_card(name)
            phase('processed')
            return dict(kind=kind, name=name, path=str(path), seconds=round(self.clock() - t0, 1),
                        size=path.stat().st_size, gemini=gemini, on_card=on_card,
                        duration=clip_duration(path) if kind == 'video' else None)
        except (OSError, ValueError, VisionError, CaptureError) as exc:
            return dict(kind=kind, error=str(exc)[:160])
        finally:
            phase('finished')


def shrink(jpeg, side=FOR_GEMINI):
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg))
    im.thumbnail((side, side))
    out = io.BytesIO()
    im.convert('RGB').save(out, 'JPEG', quality=85)
    return out.getvalue()


def clip_duration(path):
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                              str(path)], capture_output=True, text=True, timeout=30)
        return round(float(out.stdout.strip()), 1)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def clip_frames(path, count=4, side=768):
    """count frames spread over the clip, as JPEGs for Gemini."""
    duration = clip_duration(path) or 0.0
    frames = []
    for i in range(count):
        at = duration * (i + 0.5) / count
        out = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', '%.2f' % at, '-i', str(path),
                              '-frames:v', '1', '-vf', 'scale=%d:-2' % side, '-q:v', '5', '-f', 'image2pipe',
                              '-vcodec', 'mjpeg', '-'], capture_output=True, timeout=60)
        if out.returncode == 0 and out.stdout.startswith(b'\xff\xd8'):
            frames.append(out.stdout)
    return frames


def gemini_text(result):
    """The message that goes to Gemini with the media."""
    if result.get('error'):
        return '[Camera] The %s failed: %s.' % (result['kind'], result['error'])
    if result['kind'] == 'photo':
        return ('[Camera] Adam took this photo with Cammy\'s camera (saved on the PC as %s). '
                'Say briefly what it shows.' % Path(result['path']).name)
    return ('[Camera] Adam recorded a %s-second video with Cammy\'s camera (saved on the PC as %s). '
            'Stock mode was restored. These are %d frames from it, in order. Say briefly what happens in it.'
            % (result.get('duration') or '?', Path(result['path']).name, len(result['gemini'])))

