"""Deterministic in-process camera API simulator; no hardware or network I/O.

Synthetic companion adapters describe a proposed contract, not M105 capabilities.
SPDX-License-Identifier: MIT
"""
import base64
from collections import deque
import datetime
import io
import json
import math
import re
import struct
import threading
import time
import wave


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON field')
        result[key]=value
    return result


class Simulator:
    def __init__(self, profile='companion', clock=time.monotonic):
        if profile not in ('stock', 'companion'):
            raise ValueError('Unknown simulator profile')
        self.profile, self.clock = profile, clock
        self.lock = threading.Lock()
        self.owner, self.lease_end = None, 0
        self.preview = False
        self.pan, self.face, self.text = 0, 'idle', ''
        self.events = deque(maxlen=32)
        self.sequence = 0
        self.faults = {'disconnected': False, 'latency_ms': 0, 'stale_frame': False,
                       'probe_hang': False, 'preview_mode': 'sdp-ready'}
        self.jobs = {}
        self.job_sequence = 0
        self.preview_started = None
        self.frame_time = None
        self.frame_sequence = 0
        self.emit('simulator.started')

    def emit(self, event):
        self.sequence += 1
        self.events.append({'sequence': self.sequence, 'type': event})

    def stop_body(self):
        self.owner, self.preview, self.face = None, False, 'idle'
        self.text = ''
        self.preview_started = None
        # Pan is a commanded virtual pose, not moving hardware. Stop holds pose.

    def expire(self):
        self.advance_probe()
        if self.owner and self.clock() >= self.lease_end:
            self.stop_body()
            self.emit('session.expired')

    def settings(self, body=None):
        """Out-of-band simulator controls remain reachable during a body outage."""
        with self.lock:
            if body is not None:
                if not isinstance(body, dict) or not body or set(body) - set(self.faults):
                    return 400, {'error': 'Expected disconnected, latency_ms or stale_frame'}
                for key, value in body.items():
                    if key == 'preview_mode':
                        if value not in ('sdp-ready','no-listener','slow-sdp','protocol-error'):
                            return 400, {'error': 'Unknown preview_mode'}
                    elif key == 'latency_ms':
                        if type(value) is not int or not 0 <= value <= 1500:
                            return 400, {'error': 'latency_ms must be 0..1500'}
                    elif type(value) is not bool:
                        return 400, {'error': key + ' must be boolean'}
                if body.get('disconnected') and not self.faults['disconnected']:
                    self.stop_body()
                    self.emit('simulator.disconnected')
                self.faults.update(body)
                self.emit('simulator.faults_changed')
            return 200, {'simulation': True, 'profile': self.profile, 'faults': dict(self.faults),
                         'limits_are_virtual': True, 'pan_limits_degrees': [-45, 45]}

    def request(self, method, path, raw=None, agent='agent'):
        with self.lock:
            delay = self.faults['latency_ms'] / 1000
        if delay:
            time.sleep(delay)
        with self.lock:
            self.expire()
            if self.faults['disconnected']:
                return 503, {'error': 'Simulated camera disconnected', 'simulation': True}
            if method == 'GET':
                return self.get(path)
            if method != 'POST':
                return 404, {'error': 'Unknown method'}
            try:
                body = json.loads(raw or b'{}',object_pairs_hook=unique_object)
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                return 400, {'error': 'Expected JSON object'}
            if path == '/v1/session':
                name, ttl = body.get('agent'), body.get('ttl', 30)
                if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', name) or name != agent:
                    return 400, {'error': 'agent must match X-Agent-ID, 1..48 safe characters'}
                if type(ttl) is not int or not 1 <= ttl <= 120:
                    return 400, {'error': 'ttl must be an integer 1..120'}
                if self.owner and self.owner != name:
                    return 409, {'error': 'another agent owns the control lease'}
                self.emit('session.renewed' if self.owner else 'session.acquired')
                self.owner, self.lease_end = name, self.clock() + ttl
                return 200, {'owner': name, 'ttl': ttl}
            if path == '/v1/stop':
                self.cancel_probe(); self.stop_body(); self.emit('stop.requested')
                return 200, {'stopped': True, 'simulation': True}
            if path == '/v1/probes':
                if set(body) != {'probe'} or body['probe'] not in self.probe_names or not re.fullmatch(r'[A-Za-z0-9_.-]{1,48}',agent):
                    return 400, {'error':'Expected fixed probe and safe X-Agent-ID'}
                if any(j['status']=='running' for j in self.jobs.values()):
                    return 409, {'error':'Probe worker busy'}
                self.job_sequence += 1; job_id='job-'+str(self.job_sequence)
                job={'job_id':job_id,'probe_id':body['probe'],'agent':agent,'status':'running',
                     'read_only':True,'simulation':True,'started_monotonic_seconds':self.clock(),
                     'finished_monotonic_seconds':0,'result':None}
                self.jobs[job_id]=job
                if len(self.jobs)>2:del self.jobs[next(iter(self.jobs))]
                self.emit('probe.started');return 202, dict(job)
            match=re.fullmatch(r'/v1/probes/(job-[1-9][0-9]{0,19})/cancel',path)
            if match:
                job=self.jobs.get(match[1])
                if job is None:return 404, {'error':'Unknown or expired job'}
                if body:return 400, {'error':'Expected empty object'}
                if job['agent']!=agent:return 409, {'error':'Probe job owner required'}
                if job['status']=='running':self.cancel_probe()
                return 200, dict(job)
            supported = {'/v1/session/release', '/v1/preview/start', '/v1/preview/stop',
                         '/v1/preview/check', '/v1/look', '/v1/face', '/v1/speak', '/v1/audio/capture'}
            if path not in supported:
                return 404, {'error': 'Unknown endpoint'}
            if not self.owner or self.owner != agent:
                return 409, {'error': 'active control lease required for X-Agent-ID'}
            if path == '/v1/session/release':
                self.stop_body(); self.emit('session.released')
                return 200, {'released': True}
            if self.profile == 'stock':
                return 501, {'error': 'hardware adapter not implemented (stock-compatible profile)'}
            if path.startswith('/v1/preview/'):
                if path.endswith('/check'):
                    if not self.preview:return 409, {'error':'Connected preview required'}
                    self.preview_started=self.clock();return 200,self.preview_status()
                if path.endswith('/start') and not self.preview:self.preview_started=self.clock()
                self.preview = path.endswith('/start')
                self.emit('preview.started' if self.preview else 'preview.stopped')
                return 200, {'preview_active': self.preview, 'simulation': True}
            if path == '/v1/look':
                pan = body.get('pan_degrees')
                if type(pan) not in (int, float) or not math.isfinite(pan) or not -45 <= pan <= 45:
                    return 400, {'error': 'Virtual pan_degrees must be finite and within -45..45'}
                self.pan = pan; self.emit('gimbal.commanded')
                return 200, {'simulation': True, 'commanded_pan_degrees': pan, 'measured': False}
            if path == '/v1/face':
                expression = body.get('expression')
                if expression not in ('idle', 'listening', 'thinking', 'speaking', 'muted', 'sleep'):
                    return 400, {'error': 'Unknown virtual expression'}
                self.face = expression; self.emit('face.changed')
                return 200, {'expression': self.face, 'simulation': True}
            if path == '/v1/speak':
                text = body.get('text')
                if not isinstance(text, str) or not 1 <= len(text) <= 160:
                    return 400, {'error': 'text must contain 1..160 characters'}
                self.text = text; self.emit('speech.simulated')
                return 200, {'simulation': True, 'text': text, 'audio_played': False}
            if path == '/v1/audio/capture':
                output = io.BytesIO()
                with wave.open(output, 'wb') as audio:
                    audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    audio.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 440 * n / 16000))) for n in range(1600)))
                self.emit('audio.synthetic_capture')
                return 200, {'simulation': True, 'content_type': 'audio/wav', 'duration_seconds': .1,
                             'source': 'synthetic 440 Hz tone, not microphone or speech',
                             'wav_base64': base64.b64encode(output.getvalue()).decode()}

    probe_names=('runtime','network-bindings','display-layout','audio-state')

    def cancel_probe(self):
        for job in self.jobs.values():
            if job['status']=='running':
                job.update(status='cancelled',finished_monotonic_seconds=self.clock())
                self.emit('probe.cancelled')

    def advance_probe(self):
        for job in self.jobs.values():
            if job['status']!='running':continue
            age=self.clock()-job['started_monotonic_seconds']
            if self.faults['probe_hang']:
                if age>=1:
                    job.update(status='timed_out',finished_monotonic_seconds=self.clock())
                    self.emit('probe.timed_out')
            elif age>=.05:
                sample={'source':'simulation:'+job['probe_id'],'available':True,                        'error_errno':0,'truncated':False,'value':'synthetic metadata'}
                if job['probe_id']=='network-bindings':
                    sample.pop('value');sample['listeners']=[{'address':'127.0.0.1','port':8554}] if self.preview else []
                    sample['row_limit_reached']=False
                job.update(status='completed',finished_monotonic_seconds=self.clock(),result={
                    'simulation':True,'observed_monotonic_seconds':self.clock(),
                    'boot_id':{'source':'simulation:boot-id','available':True,'error_errno':0,'truncated':False,'value':'virtual-boot'},
                    'samples':[sample]})
                self.emit('probe.completed')

    def preview_status(self):
        mode=self.faults['preview_mode']
        age=self.clock()-self.preview_started if self.preview and self.preview_started is not None else 0
        phase='idle';failure=None;listener=None;sdp=None
        if self.preview:
            if mode=='sdp-ready':phase='sdp-ready';listener=True;sdp=True
            elif mode=='protocol-error':phase='failed';failure='rtsp-protocol-error';listener=True;sdp=False
            elif mode=='no-listener':phase='waiting-for-listener';listener=False
            else:phase='checking-sdp';listener=True
            if age>=5 and mode in ('no-listener','slow-sdp'):
                phase='failed';failure='rtsp-unreachable' if mode=='no-listener' else 'rtsp-timeout';sdp=False
        return {'simulation':True,'phase':phase,'preview_active':self.preview,'event_connected':self.preview,
                'commands_sent':int(age//30)+1 if self.preview else 0,'keepalive_interval_seconds':30,
                'listener_reachable':listener,'sdp_ready':sdp,'rtsp_status':200 if sdp else 0,
                'checked_monotonic_seconds':self.clock() if phase in ('failed','sdp-ready') else 0,
                'sample_age_seconds':0,'failure_reason':failure,'frame_fresh':None,'frame_evidence':'not-collected'}

    def get(self, path):
        if path=='/v1/probes':
            return 200, {'simulation':True,'supervisor':'busy' if any(j['status']=='running' for j in self.jobs.values()) else 'ready',
                'history_limit':2,'result_limit_bytes':15999,'probes':[{'id':name,'available':True,'effect':'read-only metadata',
                'deadline_seconds':1,'requires_lease':False} for name in self.probe_names]}
        match=re.fullmatch(r'/v1/probes/(job-[1-9][0-9]{0,19})',path)
        if match:
            job=self.jobs.get(match[1])
            return (200,dict(job)) if job else (404,{'error':'Unknown or expired job'})
        if path=='/v1/preview/status':return 200,self.preview_status()
        if path == '/health':
            return 200, {'service': 'cammy-portal', 'simulation': True}
        if path == '/v1/diagnostics':
            return 200, {'simulation': True, 'read_only': True, 'hardware_attached': False, 'runtime_state': 'SIMULATION'}
        if path == '/v1/identity':
            return 200, {'os': 'CammyOS simulator', 'simulation': True, 'profile': self.profile,
                         'kernel_replaced': False, 'hardware_attached': False}
        if path == '/v1/capabilities':
            available = self.profile == 'companion'
            return 200, {'api_version': '1', 'simulation': True, 'control_leases': True, 'events': True, 'metadata_probes': True,
                         **{name: {'available': available, 'simulated': True, **({'status_available':True} if name=='preview' else {})} for name in
                            ('preview', 'audio_capture', 'speech', 'face', 'gimbal')}}
        if path == '/v1/state':
            return 200, {'simulation': True, 'owner': self.owner,
                         'lease_remaining_seconds': max(0, self.lease_end - self.clock()) if self.owner else 0,
                         'preview_active': self.preview, 'event_sequence': self.sequence,
                         'commanded_pan_degrees': self.pan, 'position_measured': False,
                         'face': self.face, 'last_speech_text': self.text, 'sensing_default': 'off'}
        if path == '/v1/events':
            return 200, {'latest': self.sequence, 'events': list(self.events)}
        if path == '/v1/simulation/frame':
            if self.profile == 'stock':
                return 501, {'error': 'Frame adapter unavailable in stock-compatible profile'}
            if not self.preview:
                return 409, {'error': 'Start simulated preview first'}
            now = self.clock()
            if self.frame_time is None or not self.faults['stale_frame']:
                self.frame_time = now; self.frame_sequence += 1
            age = max(0, now - self.frame_time)
            return (200 if age <= 1 else 503), {'simulation': True, 'synthetic': True,
                'sequence': self.frame_sequence, 'age_ms': round(age * 1000), 'fresh': age <= 1,
                'width': 320, 'height': 180, 'pattern': 'moving-test-target',
                'captured_utc': (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age)).isoformat()}
        return 404, {'error': 'Unknown endpoint'}
