"""CammyOS v1 HTTP client. Python standard library; SPDX-License-Identifier: MIT."""
import json
import base64
import re
import time
import threading
import urllib.error
import urllib.request
from cammy_http import camera_opener

class CammyError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)

class Cammy:
    def __init__(self, endpoint, token, agent="agent", timeout=5, label=None):
        self.endpoint = endpoint.rstrip("/")
        self.token, self.agent, self.timeout = token, agent, timeout
        # Free-text agent ID shown on Cammy's Agent screen (0.7), e.g. the T3 thread.
        self.label = label
        self.opener = camera_opener()
        self._lease_active = False
        self._boot_binding = None
        self._lease_binding = None
        self._input_sequence = self._face_sequence = self._speaker_sequence = 0
        self._sequence_lock = threading.Lock()

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers={"Authorization":"Bearer "+self.token,"X-Agent-ID":self.agent,"Content-Type":"application/json"}
        if self._boot_binding is not None:headers['X-Cammy-Boot-ID']=self._boot_binding
        if self._lease_binding is not None:headers['X-Cammy-Lease-ID']=self._lease_binding
        req = urllib.request.Request(self.endpoint + path,data=data,method=method,headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as reply:
                return json.load(reply)
        except urllib.error.HTTPError as exc:
            with exc:
                try:
                    message = json.load(exc).get("error", "request failed")
                except (ValueError, AttributeError):
                    message = str(exc)
            raise CammyError(exc.code, message) from None

    def probes(self): return self.request("GET", "/v1/probes")
    def probe_start(self, probe): return self.request("POST", "/v1/probes", {"probe": probe})
    @staticmethod
    def _probe_path(job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"job-[1-9][0-9]{0,19}", job_id):
            raise ValueError("Invalid probe job ID")
        return "/v1/probes/" + job_id
    def probe_status(self, job_id): return self.request("GET", self._probe_path(job_id))
    def probe_cancel(self, job_id): return self.request("POST", self._probe_path(job_id) + "/cancel", {})
    def diagnostics(self): return self.request("GET", "/v1/diagnostics")
    def identity(self): return self.request("GET", "/v1/identity")
    def capabilities(self): return self.request("GET", "/v1/capabilities")
    def speaker_status(self): return self.request("GET","/v1/speaker/status")
    def speaker_cancel(self): return self.request("POST","/v1/speaker/cancel",{})
    def speaker_peak_limit(self):
        """Peak the portal accepts: 4000 on 0.6.0-rc2, 10000 on later builds."""
        if getattr(self,'_speaker_peak',None) is None:
            value=self.speaker_status().get('peak_limit',4000)
            self._speaker_peak=value if isinstance(value,int) and 1000<=value<=32767 else 4000
        return self._speaker_peak
    def play_pcm(self,pcm,append=False):
        """Queue50ms..5s mono16k S16_LE within the portal's peak_limit; no text-to-speech here."""
        if not isinstance(pcm,bytes) or len(pcm)%2 or not 1600<=len(pcm)<=160000:
            raise ValueError("Finite mono16k S16_LE PCM required")
        import struct
        limit=self.speaker_peak_limit()
        if any(abs(v[0])>limit for v in struct.iter_unpack('<h',pcm)):
            raise ValueError("Speaker peak limit%d exceeded"%limit)
        with self._sequence_lock:
            self._speaker_sequence+=1
            sequence=self._speaker_sequence
        body={"sequence":sequence,"pcm_s16le_mono16k":base64.b64encode(pcm).decode('ascii')}
        if append:body["append"]=True  # continue the active job gaplessly (portal stream mode)
        return self.request("POST","/v1/speaker/play",body)
    def media_status(self): return self.request("GET","/v1/media/status")
    def prepare_media(self):
        if self._boot_binding is None:self.state()
        return self.request("POST","/v1/media/prepare",{})
    def cancel_media_preparation(self): return self.request("POST","/v1/media/cancel",{})
    def face_status(self): return self.request("GET", "/v1/face/status")
    def input_status(self): return self.request("GET", "/v1/input/status")
    def input_button(self, button=None, action="tap"):
        """Observed stock buttons; no calibrated pose or physical-motion claim.

        Left/right holds must be renewed, normally10Hz. Independent device
        guardian releases within its deadline when renewals cease.
        """
        if action not in ("tap","hold","release"):
            raise ValueError("Unsupported input action")
        if action!="release" and button not in ("left","right","zoom_in","zoom_out","turn_180","center","playback_cycle"):
            raise ValueError("Unsupported button")
        if action=="hold" and button not in ("left","right"):
            raise ValueError("Only observed pan buttons support holds")
        with self._sequence_lock:
            self._input_sequence+=1
            sequence=self._input_sequence
        body={"action":action,"sequence":sequence}
        if button is not None:body["button"]=button
        return self.request("POST","/v1/input",body)
    def face(self, expression=None, action="show"):
        """Queue an owner-scoped native GUI intent; poll status for application.

        An HTTP202 is not a physical display confirmation. The compiled native
        bank supports six moods; arbitrary host skin/mouth/gaze stays separate.
        """
        if action not in ("show", "hide", "replay", "idle", "speak"):
            raise ValueError("Unsupported face action")
        with self._sequence_lock:
            self._face_sequence+=1
            sequence=self._face_sequence
        body={"action":action,"sequence":sequence}
        if expression is not None:
            if expression not in ("neutral","happy","angry","sad","doubt","sleepy"):
                raise ValueError("Unsupported face expression")
            body["expression"]=expression
        return self.request("POST", "/v1/face", body)
    def face_pack(self, pack):
        """Grant ("cammy") or end ("child") the CCC-only Cammy pack on this face lease.

        Needs the lease and a shown face. The grant lasts while the face lease does; a
        physical pick on Cammy's menu, stock camera mode or expiry ends it, and only a new
        call starts it again. Poll face_pack_status() for what is on screen. The pack is
        display state, never authorization.
        """
        if pack not in ("cammy", "child"):
            raise ValueError("Unsupported face pack")
        with self._sequence_lock:
            self._face_sequence+=1
            sequence=self._face_sequence
        return self.request("POST", "/v1/face/pack", {"pack":pack,"sequence":sequence})
    def face_pack_status(self): return self.request("GET", "/v1/face/pack")
    def state(self):
        value=self.request("GET","/v1/state")
        boot=value.get('boot_id')
        if self._boot_binding is None and isinstance(boot,str) and re.fullmatch('[0-9a-f]{32}',boot):self._boot_binding=boot
        return value
    def audio_stream_status(self): return self.request("GET", "/v1/audio/stream/status")
    def open_audio_stream(self):
        """Explicit owner-only live mono16kHz S16_LE PCM. Close the response to stop.

        The caller must keep its control lease alive. This starts no recorder on
        TF and does not enable automatic sensing or claim physical mute/cleanup.
        """
        req = urllib.request.Request(self.endpoint + '/v1/audio/stream', data=b'{}', method='POST',
            headers={'Authorization': 'Bearer ' + self.token, 'X-Agent-ID': self.agent,
                     'Content-Type': 'application/json',**({'X-Cammy-Boot-ID':self._boot_binding} if self._boot_binding is not None else {}),
                     **({'X-Cammy-Lease-ID':self._lease_binding} if self._lease_binding is not None else {})})
        try:
            reply = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            with exc:
                try: message = json.loads(exc.read(4096)).get('error', 'audio stream refused')
                except (ValueError, AttributeError): message = 'audio stream refused'
            raise CammyError(exc.code, message) from None
        if (reply.headers.get_content_type() != 'application/octet-stream'
                or reply.headers.get('X-Cammy-Audio-Format') != 'S16_LE'
                or reply.headers.get('X-Cammy-Audio-Rate') != '16000'
                or reply.headers.get('X-Cammy-Audio-Channels') != '1'):
            reply.close()
            raise CammyError(502, 'Unsupported live audio format')
        return reply
    def acquire(self, ttl=30):
        # Only explicit acquisition may recover after expiry. Renewal retains
        # its old nonce so a delayed renewal cannot start a new ownership epoch.
        state = self.state()
        if state.get('owner') is None:
            self._lease_binding = None
        return self._session(ttl)
    def _session(self, ttl):
        body = {"agent": self.agent, "ttl": ttl}
        if self.label:
            body["label"] = self.label
        result = self.request("POST", "/v1/session", body)
        lease=result.get('lease_id')
        if lease is not None and (not isinstance(lease,str) or not re.fullmatch('[0-9a-f]{32}',lease)):
            raise CammyError(502,'Invalid lease binding')
        if lease!=self._lease_binding:
            self._input_sequence=self._face_sequence=0
        self._lease_binding=lease
        self._lease_active = True
        return result
    def renew(self, ttl=30):
        if not self._lease_active:
            raise CammyError(409, 'No active lease to renew')
        return self._session(ttl)
    def release(self):
        result = self.request("POST", "/v1/session/release", {})
        self._lease_active = False
        self._lease_binding = None
        return result
    def preview_status(self): return self.request("GET", "/v1/preview/status")
    def preview_check(self): return self.request("POST", "/v1/preview/check", {})
    def preview_start(self): return self.request("POST", "/v1/preview/start", {})
    def preview_stop(self): return self.request("POST", "/v1/preview/stop", {})
    def vision_capabilities(self): return self.request("GET", "/v1/vision/capabilities")
    def frame_start(self): return self.request("POST", "/v1/vision/capture", {})
    @staticmethod
    def _frame_path(job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"frame-[1-9][0-9]{0,18}", job_id):
            raise ValueError("Invalid frame job ID")
        return "/v1/vision/" + job_id
    def frame_status(self, job_id): return self.request("GET", self._frame_path(job_id))
    def frame_image(self, job_id):
        req = urllib.request.Request(self.endpoint + self._frame_path(job_id) + "/image",
            headers={"Authorization": "Bearer " + self.token, "X-Agent-ID": self.agent})
        try:
            with self.opener.open(req, timeout=self.timeout) as reply:
                if reply.headers.get("Content-Type") != "image/jpeg":
                    raise CammyError(502, "Expected JPEG response")
                data = reply.read(24 * 1024 * 1024 + 1)
                if len(data) > 24 * 1024 * 1024 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                    raise CammyError(502, "Invalid or oversized frame response")
                return data
        except urllib.error.HTTPError as exc:
            with exc:
                try: message = json.load(exc).get("error", "frame unavailable")
                except (ValueError, AttributeError): message = "frame unavailable"
            raise CammyError(exc.code, message) from None
    def capture_frame(self, timeout=25):
        job = self.frame_start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.frame_status(job["id"])
            if not job["active"]:
                if job["phase"] != "completed":
                    raise CammyError(503, job.get("error", "Snapshot did not complete"))
                return {"metadata": job, "jpeg": self.frame_image(job["id"])}
            time.sleep(.1)
        raise CammyError(504, "Snapshot still active; inspect status before releasing or retrying")
    def stop(self):
        if self._boot_binding is None:self.state()
        result = self.request("POST", "/v1/stop", {})
        self._lease_active = False
        self._lease_binding = None
        return result
    def events(self, after=0):
        result = self.request("GET", "/v1/events")
        rows = result["events"]
        result["history_gap"] = bool(rows and after < rows[0]["sequence"] - 1)
        result["events"] = [e for e in rows if e["sequence"] > after]
        return result
    def __enter__(self):
        self.acquire()
        return self
    def __exit__(self, exc_type, exc, tb):
        if not self._lease_active:
            return
        try:
            self.release()
        except (CammyError, OSError):
            if exc_type is None:
                raise
