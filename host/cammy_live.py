"""Finite, explicit real Cammy A/V session; latest frames and PCM stay in RAM.

This adapter is independent of the reference Reachy simulator. Its timestamps
are PC receipt times, not measured sensor exposure times or end-to-end latency.
"""
import array
from collections import deque
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sdk'))
from cammy import Cammy, CammyError
from live_media_probe import describe, terminate

WIDTH, HEIGHT, FPS = 640, 360, 5
HOSTS = __import__('c3.discover', fromlist=['hosts']).hosts()   # saved address, then the camera's AP


def real_client():
    config = json.loads((ROOT / 'private/host/config.json').read_text())
    token = Path(config['token_file']).read_text().strip()
    return Cammy('http://127.0.0.1:8790', token, 'face-lab-' + secrets.token_hex(8), timeout=3)


def video_command(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'rtsp' or parsed.hostname not in HOSTS or parsed.port != 8554
            or parsed.path != '/ch00' or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('Unconfigured camera stream')
    executable = shutil.which('ffmpeg')
    if not executable:
        raise RuntimeError('ffmpeg is required')
    return [sys.executable, str(ROOT / 'host/media_child.py'), str(os.getpid()), executable,
            '-nostdin', '-hide_banner', '-loglevel', 'error', '-threads', '1',
            '-rtsp_transport', 'tcp', '-timeout', '3000000',
            '-analyzeduration', '1000000', '-probesize', '1000000', '-i', url,
            '-map', '0:v:0', '-an', '-vf', f'scale={WIDTH}:{HEIGHT},fps={FPS}',
            '-threads', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']


def boot_id(client):
    job = client.probe_start('runtime')
    deadline = time.monotonic() + 2
    while job.get('status') == 'running' and time.monotonic() < deadline:
        time.sleep(.04)
        job = client.probe_status(job['job_id'])
    row = job.get('result', {}).get('boot_id', {})
    value = row.get('value', '').strip()
    if (job.get('status') != 'completed' or not row.get('available')
            or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value)):
        raise RuntimeError('Real boot identity unavailable')
    return value


class LiveSession:
    def __init__(self, client_factory=real_client, process_factory=subprocess.Popen,
                 describe_source=describe, read_boot=boot_id, synthetic=False):
        self.client_factory, self.process_factory = client_factory, process_factory
        self.describe_source, self.read_boot = describe_source, read_boot
        self.synthetic = synthetic
        self.lock = threading.RLock()
        self.thread = None
        self.generation = 0
        self.cancel = threading.Event()
        self.phase = 'idle'
        self.error = None
        self.decoder_log = bytearray()
        self.decoder_exit_code = None
        self.startup_attempts = 0
        self.frame = None
        self.raw_frame = None
        self.pcm = deque(maxlen=50)  # at most five seconds /160kB
        self.samples = self.frames = self.nonzero_samples = 0
        self.audio_sequence = 0
        self.rms = None
        self.audio_at = self.frame_at = None
        self.boot = self.session = None
        self.release_acknowledged = False
        self.decoder_reaped = True
        self.microphone_stream_closed = True
        self.worker_threads_joined = True
        self.control_client = None

    def start(self, seconds=30):
        if type(seconds) is not int or not 5 <= seconds <= 90:
            raise ValueError('Session duration must be5..90seconds')
        with self.lock:
            if (self.thread and self.thread.is_alive() or not self.worker_threads_joined
                    or not self.decoder_reaped or not self.microphone_stream_closed):
                raise RuntimeError('A session or cleanup is already active')
            self.generation += 1
            generation = self.generation
            self.cancel = threading.Event()
            self.phase, self.error = 'starting', None
            self.decoder_exit_code = None
            self.startup_attempts = 0
            self.decoder_log.clear()
            self.frame, self.pcm = None, deque(maxlen=50)
            self.raw_frame = None
            self.frames = self.samples = self.nonzero_samples = self.audio_sequence = 0
            self.rms = self.audio_at = self.frame_at = self.boot = None
            self.session = secrets.token_hex(12)
            self.release_acknowledged = False
            self.decoder_reaped = self.microphone_stream_closed = self.worker_threads_joined = True
            self.ends = time.monotonic() + seconds
            self.requested_seconds = seconds
            self.thread = threading.Thread(target=self._run, args=(generation,), daemon=True,
                                           name='cammy-live-session')
            self.thread.start()
            return self.status()

    def _valid(self, generation):
        return generation == self.generation and not self.cancel.is_set()

    def _fail(self, message):
        with self.lock:
            if not self.cancel.is_set():
                self.error = message
            self.cancel.set()
            self.frame = None
            self.raw_frame = None
            self.pcm.clear()

    def _run(self, generation):
        client = stream = process = None
        held = False
        prepare_owned = False
        workers = []
        try:
            client = self.client_factory()
            identity = client.identity()
            if identity.get('os') != 'CammyOS' or identity.get('simulation'):
                raise RuntimeError('Real authenticated Cammy required')
            if client.state().get('owner') is not None:
                raise RuntimeError('Cammy has another controller')
            if hasattr(client, 'media_status'):
                try:
                    media = client.media_status()
                except CammyError as exc:
                    if exc.status not in (404, 501):
                        raise
                    media = {'managed': False}
                if media.get('managed') and not media.get('ready'):
                    if not self._valid(generation):
                        return
                    with self.lock:
                        self.phase = 'preparing'
                    preparation = client.prepare_media()
                    prepare_owned = preparation.get('created') is True
                    preparation_end = time.monotonic() + 90
                    while not media.get('ready'):
                        if not self._valid(generation):
                            return
                        if media.get('requires_recovery') or time.monotonic() >= preparation_end:
                            raise RuntimeError('Media preparation requires recovery')
                        self.cancel.wait(.1)
                        media = client.media_status()
                    prepare_owned = False
                    with self.lock:
                        if not self._valid(generation):
                            return
                        self.phase = 'starting'
                        self.ends = time.monotonic() + self.requested_seconds
            this_boot = self.read_boot(client)
            if not self._valid(generation):
                return
            sent = time.monotonic()
            client.acquire(ttl=30)
            held, lease_end = True, sent + 30
            with self.lock:
                if self._valid(generation):
                    self.control_client = client
            if not self._valid(generation):
                return
            # The integrated bank may have yielded to a stock screen transition
            # during preparation. Explicitly resume its idle surface under this
            # same lease; legacy cameras keep their existing media path.
            if hasattr(client, 'face_status'):
                try:
                    if client.face_status().get('available'):
                        client.face(action='idle')
                except CammyError as exc:
                    if exc.status not in (404, 501, 503):
                        raise
            result = client.preview_start()
            url = result.get('rtsp_url', 'rtsp://' + HOSTS[0] + ':8554/ch00')
            command = video_command(url)
            # The portal already owns an asynchronous RTSP readiness probe.
            # Wait for its current event session, instead of racing that probe
            # with a second desktop DESCRIBE against the stock RTSP server.
            ready_deadline = min(time.monotonic() + 5, self.ends)
            while True:
                if not self._valid(generation):
                    return
                try:
                    readiness = client.preview_status()
                    if (readiness.get('event_connected') is True
                            and type(readiness.get('commands_sent')) is int
                            and readiness['commands_sent'] > 0
                            and (readiness.get('sdp_ready') is True or readiness.get('rtsp_status') == 404)):
                        break
                    if time.monotonic() >= ready_deadline:
                        raise RuntimeError('Native live-video service not ready')
                except (OSError, ValueError):
                    if time.monotonic() >= ready_deadline:
                        raise RuntimeError('Native live-video service not ready')
                self.cancel.wait(.1)
            # First-frame readiness comes before microphone capture. Four bounded
            # retries cover stock RTSP404 startup failures without replaying any
            # camera command or restarting a native process.
            frame_deadline = min(time.monotonic()+5,self.ends)
            with self.lock:self.boot=this_boot
            for attempt in range(4):
                if not self._valid(generation):return
                with self.lock:
                    self.startup_attempts=attempt+1
                    self.decoder_log.clear();self.decoder_exit_code=None
                process=self.process_factory(command,stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                with self.lock:
                    self.decoder_reaped=False;self.worker_threads_joined=False
                video_worker=threading.Thread(target=self._video,args=(generation,process),daemon=True)
                workers.append(video_worker);video_worker.start()
                while self._valid(generation) and time.monotonic()<frame_deadline:
                    if self.frames or process.poll() is not None:break
                    self.cancel.wait(.02)
                if not self._valid(generation):return
                if self.frames:break
                code=process.poll()
                if code is None:raise RuntimeError('First video frame timed out')
                video_worker.join(timeout=.5)
                if video_worker.is_alive():raise RuntimeError('Decoder reader cleanup uncertain')
                self._record_decoder_exit(process)
                retry=code==8 and b'method DESCRIBE failed: 404' in self.decoder_log
                terminate(process);process.stdout.close()
                if process.stderr is not None:process.stderr.close()
                with self.lock:self.decoder_reaped=True
                if not retry or attempt==3 or time.monotonic()>=frame_deadline:
                    raise RuntimeError('Decoder exited')
            stream = client.open_audio_stream()
            with self.lock:
                self.microphone_stream_closed = False
                if not self._valid(generation):return
                self.phase = 'live'
                media_started = time.monotonic()
            audio_worker=threading.Thread(target=self._audio,args=(generation,stream),daemon=True)
            workers.append(audio_worker);audio_worker.start()
            last_sequence = None
            next_check = time.monotonic()
            while self._valid(generation) and time.monotonic() < self.ends:
                now = time.monotonic()
                if now >= lease_end:
                    raise RuntimeError('Local lease expired')
                if now >= next_check:
                    state = client.state()
                    sequence = state.get('event_sequence')
                    if (state.get('owner') != client.agent
                            or state.get('lease_remaining_seconds', 0) <= 0
                            or not state.get('preview_active')
                            or type(sequence) is not int
                            or last_sequence is not None and sequence < last_sequence):
                        raise RuntimeError('Stop, lease loss or body restart')
                    last_sequence = sequence
                    if lease_end - now < 12:
                        if not self._valid(generation):
                            break
                        client.renew(ttl=30)
                        if time.monotonic() >= lease_end:
                            raise RuntimeError('Renewal arrived after lease expiry')
                        lease_end = now + 30
                    next_check = now + 1
                with self.lock:
                    if (now - (self.frame_at or media_started) > 5
                            or now - (self.audio_at or media_started) > 5):
                        raise RuntimeError('Live media stalled')
                if process.poll() is not None:
                    self._record_decoder_exit(process)
                    raise RuntimeError('Decoder exited')
                self.cancel.wait(.05)
        except Exception as exc:
            # No URLs, headers or body response text in the public error surface.
            self._fail(str(exc) if type(exc) is RuntimeError else type(exc).__name__ + ': live session failed')
        finally:
            with self.lock:
                self.control_client = None
                self.cancel.set()
                self.phase = 'stopping'
                self.frame = None
                self.raw_frame = None
                self.pcm.clear()
            if prepare_owned and client is not None:
                try:
                    client.cancel_media_preparation()
                except Exception:
                    self.error = 'Preparation cancellation unacknowledged; recovery may be required'
            if process is not None:
                try:
                    terminate(process)
                    process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()
                    self.decoder_reaped = process.poll() is not None
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    self.error = 'Decoder cleanup uncertain'
            if held:
                try:
                    self.release_acknowledged = client.release().get('released') is True
                except Exception:
                    self.error = 'Owner release unacknowledged; body lease must expire'
            if stream is not None:
                try:
                    stream.close()
                    self.microphone_stream_closed = True
                except OSError:
                    self.error = 'Microphone stream cleanup uncertain'
            for worker in workers:
                worker.join(timeout=4)
            with self.lock:
                self.worker_threads_joined = all(not w.is_alive() for w in workers)
                self.phase = 'faulted' if (self.error or not self.worker_threads_joined
                                          or not self.decoder_reaped or not self.microphone_stream_closed) else 'idle'

    def _record_decoder_exit(self,process):
        with self.lock:
            self.decoder_exit_code=process.poll()
            if process.stderr is None:return
            try:
                os.set_blocking(process.stderr.fileno(),False)
                self.decoder_log.extend(os.read(process.stderr.fileno(),max(0,2048-len(self.decoder_log))))
            except (OSError,ValueError):pass

    def _video(self, generation, process):
        selector = selectors.DefaultSelector()
        buffer = bytearray()
        try:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ, 'video')
            if process.stderr is not None:
                os.set_blocking(process.stderr.fileno(), False)
                selector.register(process.stderr, selectors.EVENT_READ, 'log')
            while self._valid(generation):
                ready = selector.select(.1)
                if not ready:
                    continue
                for key, _ in ready:
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fd)
                        if key.data == 'video':
                            return
                        continue
                    if key.data == 'log':
                        with self.lock:
                            self.decoder_log.extend(data[:max(0, 2048-len(self.decoder_log))])
                        continue
                    buffer.extend(data)
                while len(buffer) >= WIDTH * HEIGHT * 3:
                    pixels = bytes(buffer[:WIDTH * HEIGHT * 3])
                    del buffer[:WIDTH * HEIGHT * 3]
                    output = io.BytesIO()
                    Image.frombytes('RGB', (WIDTH, HEIGHT), pixels).save(output, 'JPEG', quality=75)
                    with self.lock:
                        if self._valid(generation):
                            self.frames += 1
                            self.frame_at = time.monotonic()
                            utc = time.time()
                            self.frame = (output.getvalue(), self.frames, self.frame_at, utc)
                            self.raw_frame = (pixels, self.frames, self.frame_at, utc)
        except Exception:
            if self._valid(generation):
                self._fail('Video receive failed')
        finally:
            selector.close()

    def _audio(self, generation, stream):
        try:
            while self._valid(generation):
                data = stream.read(3200)
                if not data or len(data) % 2:
                    raise ValueError('PCM ended or misaligned')
                values = array.array('h', data)
                if sys.byteorder != 'little':
                    values.byteswap()
                rms = math.sqrt(sum(v * v for v in values) / len(values))
                with self.lock:
                    if self._valid(generation):
                        self.audio_sequence += 1
                        self.samples += len(values)
                        self.nonzero_samples += sum(v != 0 for v in values)
                        self.rms = 20 * math.log10(rms / 32768) if rms else None
                        self.audio_at = time.monotonic()
                        self.pcm.append((self.audio_sequence, data))
        except Exception:
            if self._valid(generation):
                self._fail('Microphone receive failed')

    def latest_frame(self):
        with self.lock:
            if self.phase != 'live' or not self.frame or time.monotonic() - self.frame[2] > 2:
                raise RuntimeError('No fresh live frame')
            return self.frame, self.session, self.boot

    def body_command(self, operation, **value):
        """Use this session's lease; never take over another media owner."""
        if operation not in ('face', 'input_button', 'face_status', 'input_status', 'play_pcm', 'speaker_status', 'speaker_cancel'):
            raise ValueError('Unsupported body command')
        with self.lock:
            generation = self.generation
            client = self.control_client
            if client is None or not self._valid(generation):
                raise RuntimeError('No current body control session')
        if operation=='play_pcm':client.renew(ttl=30)
        result = getattr(client, operation)(**value)
        with self.lock:
            if not self._valid(generation) or client is not self.control_client:
                raise RuntimeError('Body result belongs to a stopped session')
        return result

    def latest_pixels(self):
        with self.lock:
            if self.phase != 'live' or not self.raw_frame or time.monotonic() - self.raw_frame[2] > 2:
                raise RuntimeError('No fresh live pixels')
            return self.raw_frame, self.session, self.boot

    def audio_tail(self, after=0):
        with self.lock:
            if self.phase != 'live' or not self.audio_at or time.monotonic() - self.audio_at > 2:
                raise RuntimeError('No fresh live audio')
            rows = [(sequence, data) for sequence, data in self.pcm if sequence > after]
            return b''.join(data for _, data in rows), {
                'session': self.session, 'boot_id': self.boot, 'latest_sequence': self.audio_sequence,
                'first_sequence': rows[0][0] if rows else None,
                'history_gap': bool(rows and after and rows[0][0] > after + 1),
                'last_receipt_monotonic': self.audio_at, 'samples_total': self.samples}

    def stop(self, wait=False, session=None):
        with self.lock:
            if session is not None and self.session != session:
                return self.status()
            self.cancel.set()
            self.generation += 1
            self.frame = None
            self.raw_frame = None
            self.pcm.clear()
            if self.thread and self.thread.is_alive():
                self.phase = 'stopping'
            thread = self.thread
        if wait and thread:
            thread.join(timeout=15)
        return self.status()

    def status(self):
        with self.lock:
            now = time.monotonic()
            return {'phase': self.phase, 'session': self.session, 'boot_id': self.boot,
                    'source': 'synthetic fixture' if self.synthetic else 'Cammy home Wi-Fi', 'synthetic': self.synthetic,
                    'frames': self.frames, 'audio_samples': self.samples,
                    'audio_nonzero_samples': self.nonzero_samples,
                    'audio_rms_dbfs': self.rms, 'audio_sequence': self.audio_sequence,
                    'frame_age_seconds': None if self.frame_at is None else round(now-self.frame_at, 3),
                    'audio_age_seconds': None if self.audio_at is None else round(now-self.audio_at, 3),
                    'seconds_left': round(getattr(self,'requested_seconds',0) if self.phase=='preparing' else max(0,getattr(self,'ends',now)-now),1),
                    'error': self.error, 'decoder_exit_code': self.decoder_exit_code, 'startup_attempts':self.startup_attempts, 'decoder_log': self.decoder_log.decode(errors='replace'), 'release_acknowledged': self.release_acknowledged,
                    'decoder_reaped': self.decoder_reaped,
                    'microphone_stream_closed': self.microphone_stream_closed,
                    'worker_threads_joined': self.worker_threads_joined,
                    'hardware_cleanup_confirmed': False, 'tf_recording_requested': False}
