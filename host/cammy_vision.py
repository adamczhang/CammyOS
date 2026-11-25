"""Experimental, explicit native-photo snapshots; no patched firmware or raw device I/O.

The native app owns capture. A transaction temporarily selects its photo mode,
requests ONE shutter action, reads a newly-created JPEG, then restores its mode.
Only the authenticated gateway exposes this backend; it is disabled by default.
The operator must leave camera controls and the phone app alone during capture.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import io
import ipaddress
import json
import re
import secrets
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DIRECTORY = '/mnt/extsd/DCIM/100MEDIA/'
MAX_IMAGE = 24 * 1024 * 1024
MAX_AGE = 10
MODE_NAMES = {0: 'NormalRecordeMode', 4: 'NormalCaptureMode'}
LEASE_EVENTS = {'session.acquired', 'session.released', 'session.expired',
                'session.renewed', 'stop.requested'}


class VisionError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Cancelled(VisionError):
    def __init__(self):
        super().__init__(409, 'Snapshot cancelled or lease changed')


def utc():
    return datetime.now(timezone.utc).isoformat()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise VisionError(502, 'Camera redirect refused')


class _Listing(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.names = set()

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href', '')
        parsed = urllib.parse.urlsplit(href)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return
        path = urllib.parse.unquote(parsed.path)
        name = path[len(DIRECTORY):] if path.startswith(DIRECTORY) else path
        if ('..' not in name and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.[Jj][Pp][Ee]?[Gg]', name)):
            self.names.add(name)


def jpeg_payload(raw):
    """Remove only the observed M105 16-byte length-bound native footer."""
    data = raw
    stripped = 0
    if not raw.endswith(b'\xff\xd9'):
        if len(raw) < 20 or struct.unpack('<I', raw[-4:])[0] != len(raw) - 16 or raw[-18:-16] != b'\xff\xd9':
            raise VisionError(502, 'Unrecognized or incomplete native JPEG framing')
        data, stripped = raw[:-16], 16
    return data, {'camera_file_bytes': len(raw), 'camera_file_sha256': hashlib.sha256(raw).hexdigest(),
                  'stripped_footer_bytes': stripped}


def jpeg_info(data):
    if len(data) > MAX_IMAGE or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise VisionError(502, 'JPEG is incomplete or oversized')
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or image.width * image.height > 70_000_000:
                raise ValueError('image bounds')
            size = image.size
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise VisionError(502, 'Invalid camera JPEG') from exc
    return {'width': size[0], 'height': size[1], 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest()}


class NativePhoto:
    synthetic = False

    def __init__(self, body_endpoint):
        endpoint = urllib.parse.urlsplit(body_endpoint)
        if endpoint.scheme != 'http' or endpoint.username or endpoint.password:
            raise VisionError(503, 'Unsupported body endpoint')
        try:
            address = ipaddress.IPv4Address(endpoint.hostname)
        except (ValueError, TypeError) as exc:
            raise VisionError(503, 'Numeric camera IPv4 endpoint required') from exc
        self.origin = 'http://' + str(address) + ':8082'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _read(self, path, method='GET', limit=16384):
        request = urllib.request.Request(self.origin + path, method=method,
                                         data=b'' if method == 'POST' else None)
        try:
            reply = self.opener.open(request, timeout=2)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise VisionError(502, 'Native camera HTTP request failed: ' + str(status)) from None
        with reply:
            size = reply.headers.get('Content-Length')
            if size is not None and (not size.isdigit() or int(size) > limit):
                raise VisionError(502, 'Camera response exceeds bound')
            data = reply.read(limit + 1)
            if len(data) > limit:
                raise VisionError(502, 'Camera response exceeds bound')
            if size is not None and len(data) != int(size):
                raise VisionError(502, 'Incomplete camera response')
            return data

    def status(self):
        mode = json.loads(self._read('/api/getdeviceinfo/?custom=1&cmd=2008'))
        recording = json.loads(self._read('/api/getdeviceinfo/?custom=1&cmd=2005'))
        number = mode.get('current_mode')
        if type(number) is not int or MODE_NAMES.get(number) != mode.get('curworkmodename'):
            raise VisionError(409, 'Only idle normal video/photo modes are supported')
        if type(recording.get('RecodStatus')) is not int or recording['RecodStatus'] != 0:
            raise VisionError(409, 'Native camera is recording or status is unknown')
        return number

    def _post(self, command, parameter):
        # No retry: timeout can mean an action was accepted but its reply was lost.
        result = json.loads(self._read('/api/setdeviceinfo/?custom=1&cmd=' + str(command)
                                       + '&par=' + str(parameter), 'POST'))
        # Real M105 replies identify the queued command with Value=0 (result
        # code, not the requested parameter). This is not action completion.
        if (type(result.get('Cmd')) is not int or result['Cmd'] != command or
                type(result.get('Value')) is not int or result['Value'] != 0):
            raise VisionError(502, 'Native command result was not successful')

    def mode(self, value):
        if value not in MODE_NAMES:
            raise ValueError('Unsupported native mode')
        self._post(3034, value)

    def shutter(self):
        self._post(1100, 1)

    def files(self):
        parser = _Listing()
        parser.feed(self._read(DIRECTORY, limit=512 * 1024).decode('utf-8', errors='strict'))
        return parser.names

    def image(self, name):
        parser = _Listing()
        parser.handle_starttag('a', [('href', name)])
        if parser.names != {name}:
            raise VisionError(400, 'Invalid image filename')
        return self._read(DIRECTORY + name, limit=MAX_IMAGE)


class Vision:
    def __init__(self, gateway, enabled=False, native_factory=NativePhoto, clock=time.monotonic):
        self.gateway, self.enabled, self.native_factory, self.clock = gateway, enabled, native_factory, clock
        self.lock = threading.RLock()
        self.epoch = 0
        self.controls = 0
        self.sequence = secrets.randbits(48)
        self.job = None
        self.thread = None
        self.fault = None
        self.closed = False

    def capabilities(self):
        return {'available': self.enabled and not self.closed and not self.fault,
                'backend': 'native-photo-experimental', 'continuous_video': False,
                'explicit_capture_required': True, 'creates_photo_on_tf': True,
                'requires_exclusive_native_ui': True, 'fault': self.fault}

    def _body(self, endpoint, agent, path, method='GET', payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        try:
            _, raw = self.gateway.request(endpoint, method, path, data, agent)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise VisionError(status, 'Camera control request failed: ' + str(status)) from None
        return json.loads(raw)

    def _lease(self, endpoint, agent):
        state = self._body(endpoint, agent, '/v1/state')
        if state.get('owner') != agent or state.get('lease_remaining_seconds', 0) <= 0:
            raise VisionError(403, 'An active matching control lease is required')
        return state

    def start(self, agent):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,48}', agent):
            raise VisionError(400, 'Invalid agent ID')
        state = self.gateway.snapshot()
        if not state.get('connected') or state.get('simulation') or state.get('offline'):
            raise VisionError(503, 'Real connected camera required')
        with self.lock:
            if not self.enabled or self.closed:
                raise VisionError(501, 'Native photo snapshots are disabled')
            try:
                from PIL import Image  # noqa: F401 — required before any camera action
            except ImportError:
                raise VisionError(503, 'Pillow is required for validated snapshot delivery') from None
            if self.fault:
                raise VisionError(503, 'Snapshot backend needs operator inspection: ' + self.fault)
            if self.controls or self.job and self.job['active']:
                raise VisionError(409, 'Snapshot/control transaction already active')
            body = self._lease(state['endpoint'], agent)
            if body.get('preview_active') or body['lease_remaining_seconds'] < 25:
                raise VisionError(409, 'Idle preview and at least 25 seconds of lease required')
            self.sequence += 1
            self.job = job = {'id': 'frame-' + str(self.sequence), 'agent': agent,
                'endpoint': state['endpoint'], 'epoch': self.epoch, 'active': True,
                'phase': 'validating', 'cancel': threading.Event(), 'data': None,
                'event_sequence': body['event_sequence'], 'requested_at': utc(),
                'deadline': self.clock() + min(20, body['lease_remaining_seconds']),
                'mode_restored': None, 'hardware_cleanup_confirmed': False}
            self.thread = threading.Thread(target=self._run, args=(job,), daemon=True)
            self.thread.start()
            return self._public(job)

    def _check(self, job):
        with self.lock:
            if job['cancel'].is_set() or self.closed or self.epoch != job['epoch'] or self.clock() >= job['deadline']:
                raise Cancelled()
        current = self.gateway.snapshot()
        if not current.get('connected') or current.get('endpoint') != job['endpoint']:
            raise Cancelled()
        self._lease(job['endpoint'], job['agent'])
        events = self._body(job['endpoint'], job['agent'], '/v1/events')
        rows = events.get('events', [])
        if (events['latest'] < job['event_sequence'] or
                rows and rows[0]['sequence'] > job['event_sequence'] + 1 or
                any(e['sequence'] > job['event_sequence'] and e['type'] in LEASE_EVENTS for e in rows)):
            raise Cancelled()

    def _boot(self, job, deadline=None):
        deadline = job['deadline'] if deadline is None else deadline
        if self.clock() >= deadline:
            raise VisionError(504, 'Boot provenance deadline exceeded')
        probe = self._body(job['endpoint'], job['agent'], '/v1/probes', 'POST', {'probe': 'runtime'})
        job_id = probe['job_id']
        if not re.fullmatch(r'job-[1-9][0-9]{0,19}', job_id):
            raise VisionError(502, 'Invalid probe identifier')
        for _ in range(30):
            if self.clock() >= deadline:
                break
            result = self._body(job['endpoint'], job['agent'], '/v1/probes/' + job_id)
            if result['status'] == 'completed':
                sample = result['result']['boot_id']
                if not sample.get('available') or sample.get('truncated'):
                    break
                return str(uuid.UUID(sample['value'].strip()))
            if result['status'] != 'running':
                break
            time.sleep(.05)
        raise VisionError(503, 'Camera boot provenance unavailable')

    def _phase(self, job, phase):
        with self.lock:
            job['phase'] = phase

    def _run(self, job):
        native = None
        boot = None
        original = None
        mode_maybe_changed = False
        mode_acknowledged = False
        photo_mode_confirmed = False
        shutter_maybe_sent = False
        file_complete = False
        error = None
        data = info = None
        try:
            self._check(job)
            boot = self._boot(job)
            self._check(job)
            native = self.native_factory(job['endpoint'])
            original = native.status()
            baseline = native.files()
            self._check(job)
            if original != 4:
                mode_maybe_changed = True
                self._phase(job, 'selecting-photo-mode')
                native.mode(4)
                mode_acknowledged = True
            for _ in range(30):
                self._check(job)
                if native.status() == 4:
                    photo_mode_confirmed = True
                    break
                time.sleep(.1)
            else:
                raise VisionError(504, 'Native photo mode did not settle')
            self._check(job)
            self._phase(job, 'capturing')
            shutter_maybe_sent = True
            native.shutter()
            # After dispatch, cancellation suppresses output but lets finite capture
            # finish before attempting restoration. Never resend the shutter.
            capture_deadline = self.clock() + 12
            while self.clock() < capture_deadline:
                created = native.files() - baseline
                if len(created) > 1:
                    raise VisionError(409, 'Ambiguous capture: multiple new photos')
                if created:
                    name = next(iter(created))
                    try:
                        data, framing = jpeg_payload(native.image(name))
                        info = jpeg_info(data)
                        info.update(framing)
                    except (VisionError, OSError):
                        time.sleep(.1)
                        continue
                    file_complete = True
                    job.update(filename=name, received_at=utc(), received_mono=self.clock(), **info)
                    break
                time.sleep(.1)
            if not file_complete:
                raise VisionError(504, 'No complete new JPEG; native completion is uncertain')
            self._check(job)
            if self._boot(job) != boot:
                raise VisionError(409, 'Camera boot changed during capture')
            job['boot_id'] = boot
        except Exception as exc:
            error = exc
        finally:
            self._phase(job, 'restoring')
            try:
                if mode_maybe_changed and (not shutter_maybe_sent or file_complete):
                    if not mode_acknowledged:
                        raise VisionError(503, 'Mode dispatch uncertain; inspect before restoring')
                    # Restoration is cleanup of this transaction, even on lease loss.
                    # A new owner or external mode change prevents clobbering it.
                    cleanup_deadline = self.clock() + 8
                    if not photo_mode_confirmed:
                        # An echoed command can still be queued while mode reads
                        # as the original. Never call that a completed restoration.
                        for _ in range(30):
                            if self.clock() >= cleanup_deadline:
                                break
                            if native.status() == 4:
                                photo_mode_confirmed = True
                                break
                            time.sleep(.1)
                        if not photo_mode_confirmed:
                            raise VisionError(503, 'Queued photo mode never settled; inspect native state')
                    current = self.gateway.snapshot()
                    if not current.get('connected') or current.get('endpoint') != job['endpoint'] or self._boot(job, cleanup_deadline) != boot:
                        raise VisionError(409, 'Camera identity changed; restoration needs inspection')
                    state = self._body(job['endpoint'], job['agent'], '/v1/state')
                    events = self._body(job['endpoint'], job['agent'], '/v1/events')
                    rows = events.get('events', [])
                    if (state.get('owner') not in (None, job['agent']) or
                        events['latest'] < job['event_sequence'] or
                        rows and rows[0]['sequence'] > job['event_sequence'] + 1 or
                        any(e['sequence'] > job['event_sequence'] and e['type'] == 'session.acquired' for e in rows)):
                        raise VisionError(409, 'Native owner changed; restoration needs inspection')
                    current_mode = native.status()
                    if current_mode != original:
                        if current_mode != 4:
                            raise VisionError(409, 'Native mode changed; restoration needs inspection')
                        native.mode(original)
                    for _ in range(30):
                        if self.clock() >= cleanup_deadline:
                            raise VisionError(504, 'Mode restoration deadline exceeded')
                        if native.status() == original:
                            break
                        time.sleep(.1)
                    else:
                        raise VisionError(504, 'Original mode did not return')
                    job['mode_restored'] = True
                elif not mode_maybe_changed:
                    job['mode_restored'] = True
                if shutter_maybe_sent and not file_complete:
                    raise VisionError(503, 'Capture completion uncertain; no automatic retry')
                if error is None:
                    self._check(job)
            except Exception as exc:
                self.fault = str(exc)
                error = error or exc
            with self.lock:
                job['active'] = False
                if error or job['cancel'].is_set() or self.epoch != job['epoch']:
                    job.update(phase='cancelled' if isinstance(error, Cancelled) or job['cancel'].is_set() else 'failed',
                               error=str(error or 'Snapshot cancelled'), data=None)
                else:
                    job.update(phase='completed', data=data,
                               backend='native-photo-experimental', synthetic=bool(native.synthetic),
                               native_completion_evidence='new-complete-JPEG',
                               camera_file_retained=True)

    def _public(self, job):
        private = {'cancel', 'data', 'deadline', 'epoch', 'agent', 'endpoint', 'received_mono'}
        result = {k: v for k, v in job.items() if k not in private}
        result['image_available'] = job['data'] is not None and self.clock() - job.get('received_mono', 0) <= MAX_AGE
        if job['data'] is not None:
            result['age_seconds'] = round(self.clock() - job['received_mono'], 3)
        return result

    def get(self, agent, job_id, image=False):
        with self.lock:
            job = self.job
            if not job or job['id'] != job_id:
                raise VisionError(404, 'Snapshot job not found')
            if agent != job['agent']:
                raise VisionError(403, 'Snapshot belongs to another controller')
            if not image:
                return self._public(job)
        # Do not hold the broker lock during HTTP provenance reads: Stop must
        # remain able to invalidate delivery while those reads are pending.
        self._check(job)
        if job['data'] is None or self.clock() - job.get('received_mono', 0) > MAX_AGE:
            with self.lock:
                job['data'] = None
            raise VisionError(410, 'Fresh snapshot image is unavailable')
        if self._boot(job) != job['boot_id']:
            with self.lock:
                job['data'] = None
            raise VisionError(409, 'Camera boot changed since capture')
        self._check(job)
        with self.lock:
            if (self.job is not job or self.epoch != job['epoch'] or job['cancel'].is_set() or
                    job['data'] is None or self.clock() - job['received_mono'] > MAX_AGE):
                raise Cancelled()
            return job['data']

    @contextmanager
    def control(self, path, agent, method='POST'):
        boundary = path in ('/v1/session', '/v1/session/release', '/v1/stop')
        # Keep every other mutating body operation out of this transaction too.
        if method != 'POST' or not boundary and path.startswith('/v1/probes'):
            yield
            return
        with self.lock:
            if self.job and self.job['active']:
                if path == '/v1/stop' or agent == self.job['agent'] and path.endswith('/release'):
                    self.job['cancel'].set()
                raise VisionError(409, 'Snapshot cleanup pending; retry control action after it settles')
            # Holding this reservation prevents a snapshot racing a body POST.
            self.controls += 1
            if boundary:
                self.epoch += 1
                if self.job:
                    self.job['data'] = None
        try:
            yield
        finally:
            with self.lock:
                self.controls -= 1

    def disconnected(self):
        with self.lock:
            self.epoch += 1
            if self.job:
                self.job['cancel'].set()
                self.job['data'] = None

    def close(self):
        self.closed = True
        self.disconnected()
        if self.thread:
            self.thread.join(timeout=20)
