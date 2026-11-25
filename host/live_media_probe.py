"""Bounded live RTSP decode through an authenticated, leased Cammy body.

No TF recording, shutter, mode change, device fallback or firmware write.
Raw media stays in bounded memory; public output contains measurements only.
"""
import argparse
import array
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import secrets
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sdk'))
from cammy import Cammy

HOSTS = __import__('c3.discover', fromlist=['hosts']).hosts()   # saved address, then the camera's AP
FRAME_BYTES = 160 * 90


def describe(host):
    if host not in HOSTS:
        raise ValueError('Only the two configured M105 addresses are allowed')
    url = 'rtsp://' + host + ':8554/ch00'
    with socket.create_connection((host, 8554), timeout=2) as connection:
        connection.sendall(('DESCRIBE ' + url + ' RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n').encode())
        raw = b''
        while b'\r\n\r\n' not in raw:
            part = connection.recv(2048)
            if not part or len(raw) + len(part) > 8192:
                raise ValueError('Incomplete or excessive RTSP headers')
            raw += part
        header, body = raw.split(b'\r\n\r\n', 1)
        lines = header.decode('ascii').split('\r\n')
        if lines[0] != 'RTSP/1.0 200 OK':
            raise ValueError('RTSP DESCRIBE refused')
        fields = {}
        for line in lines[1:]:
            key, value = line.split(':', 1)
            key = key.lower().strip()
            if key in fields:
                raise ValueError('Duplicate RTSP header')
            fields[key] = value.strip()
        length = int(fields.get('content-length', '-1'))
        if not 0 < length <= 16384 or fields.get('cseq') != '1' or fields.get('content-type', '').split(';')[0] != 'application/sdp':
            raise ValueError('Invalid bounded SDP response')
        while len(body) < length:
            part = connection.recv(min(2048, length - len(body)))
            if not part:
                raise ValueError('Truncated SDP')
            body += part
        lines = body[:length].decode('utf-8').replace('\r', '').splitlines()
        if 'v=0' not in lines or not any(x.startswith('m=video ') for x in lines):
            raise ValueError('Video SDP missing')
        return any(x.startswith('m=audio ') for x in lines)


def command(host, audio_fd=None):
    if host not in HOSTS:
        raise ValueError('Unconfigured source')
    executable = shutil.which('ffmpeg')
    if not executable:
        raise RuntimeError('ffmpeg is required')
    args = [executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-rtsp_transport', 'tcp', '-timeout', '3000000',
            '-analyzeduration', '1000000', '-probesize', '1000000', '-i',
            'rtsp://' + host + ':8554/ch00', '-map', '0:v:0', '-an',
            '-vf', 'scale=160:90,fps=5', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']
    if audio_fd is not None:
        args += ['-map', '0:a:0', '-vn', '-ar', '16000', '-ac', '1', '-f', 's16le', 'pipe:' + str(audio_fd)]
    return args


def terminate(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def receive(factory, seconds, audio, check, clock=time.monotonic, on_frame=None):
    """Consume decoded data while the source runs; fixtures supply their own factory."""
    if not 1 <= seconds <= 1800:
        raise ValueError('Duration must be1..1800seconds')
    read_fd, write_fd = os.pipe() if audio else (None, None)
    process = None
    selector = selectors.DefaultSelector()
    started = clock()
    report = {'video_frames': 0, 'changed_frame_transitions': 0, 'audio_samples': 0,
              'audio_nonzero_samples': 0, 'first_video_seconds': None, 'first_audio_seconds': None,
              'latency_end_to_end_measured': False, 'physical_capabilities_verified': False}
    frame = bytearray()
    audio_tail = b''
    squares = 0
    last_hash = None
    last_media = started
    logs = bytearray()
    try:
        process = subprocess.Popen(factory(write_fd), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   pass_fds=(() if write_fd is None else (write_fd,)))
        if write_fd is not None:
            os.close(write_fd); write_fd = None
        for fd, kind in [(process.stdout.fileno(), 'video'), (process.stderr.fileno(), 'log')] + (
                [(read_fd, 'audio')] if read_fd is not None else []):
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, kind)
        while clock() - started < seconds:
            check()
            for key, _ in selector.select(timeout=0.1):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                if key.data == 'log':
                    logs.extend(chunk[:max(0, 4096 - len(logs))])
                    continue
                last_media = clock()
                if key.data == 'video':
                    frame.extend(chunk)
                    while len(frame) >= FRAME_BYTES:
                        digest = hashlib.sha256(frame[:FRAME_BYTES]).digest()
                        del frame[:FRAME_BYTES]
                        report['changed_frame_transitions'] += int(last_hash is not None and last_hash != digest)
                        last_hash = digest
                        report['video_frames'] += 1
                        if on_frame is not None:
                            on_frame()
                        if report['first_video_seconds'] is None:
                            report['first_video_seconds'] = round(clock() - started, 3)
                else:
                    data = audio_tail + chunk
                    audio_tail = data[len(data) - len(data) % 2:] if len(data) % 2 else b''
                    samples = array.array('h', data[:len(data) - len(data) % 2])
                    if sys.byteorder != 'little':
                        samples.byteswap()
                    report['audio_samples'] += len(samples)
                    report['audio_nonzero_samples'] += sum(x != 0 for x in samples)
                    squares += sum(x * x for x in samples)
                    if samples and report['first_audio_seconds'] is None:
                        report['first_audio_seconds'] = round(clock() - started, 3)
            if process.poll() is not None:
                raise RuntimeError('Live decoder exited: ' + logs.decode(errors='replace')[-1000:])
            if clock() - last_media > 5:
                raise RuntimeError('Live decoder stalled; frames=%s audio_samples=%s; %s' % (
                    report['video_frames'], report['audio_samples'], logs.decode(errors='replace')[-1000:]))
        report['observed_seconds'] = round(clock() - started, 3)
        report['decoded_monitor_fps'] = report['video_frames'] / report['observed_seconds']
        report['audio_rms_dbfs'] = (20 * math.log10(math.sqrt(squares / report['audio_samples']) / 32768)
                                      if squares else None)
        # Transport delivery is not acoustic intelligibility or an active cue proof.
        report['live_data_received'] = report['video_frames'] > 0
        report['video_and_nonzero_audio_received'] = bool(report['video_frames'] and report['audio_nonzero_samples'])
        return report
    finally:
        if process is not None:
            terminate(process)
            process.stdout.close(); process.stderr.close()
        selector.close()
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)


def run(client, host, seconds):
    if host not in HOSTS:
        raise ValueError('Unconfigured source')
    identity = client.identity()
    if identity.get('os') != 'CammyOS' or identity.get('simulation') or client.state().get('owner') is not None:
        raise RuntimeError('An idle authenticated real Cammy body is required')
    # Fail without changing the preview/session if the native listener is absent.
    with socket.create_connection((host, 8554), timeout=2):
        pass
    held = False
    deadline = 0
    next_check = 0
    try:
        sent = time.monotonic()
        client.acquire(ttl=30); held = True; deadline = sent + 30
        client.preview_start()
        # The phone app opens its event channel before requesting SDP/media.
        audio = describe(host)

        def check():
            nonlocal deadline, next_check
            now = time.monotonic()
            if now >= deadline:
                raise RuntimeError('Local control lease expired')
            if now < next_check:
                return
            if client.identity() != identity:
                raise RuntimeError('Body identity changed')
            state = client.state()
            if state.get('owner') != client.agent or state.get('lease_remaining_seconds', 0) <= 0 or not state.get('preview_active'):
                raise RuntimeError('Live receive cancelled by Stop, lease loss or preview stop')
            if deadline - now < 10:
                client.renew(ttl=30)
                if time.monotonic() >= deadline:
                    raise RuntimeError('Renewal arrived after the old deadline')
                deadline = now + 30
            next_check = now + 1

        result = receive(lambda fd: command(host, fd), seconds, audio, check)
        result.update({'source': 'native RTSP', 'audio_advertised': audio, 'tf_recording_requested': False})
        return result
    finally:
        if held:
            # Owner-scoped release closes preview; never globally stop a new owner.
            client.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=HOSTS, default=HOSTS[0])
    parser.add_argument('--seconds', type=int, choices=range(1, 1801), default=10, metavar='1..1800')
    args = parser.parse_args()
    config = json.loads((ROOT / 'private/host/config.json').read_text())
    client = Cammy('http://' + args.host + ':8787', Path(config['token_file']).read_text().strip(),
                   agent='live-media-probe-' + secrets.token_hex(6), timeout=2)
    try:
        result = run(client, args.host, args.seconds)
        print(json.dumps(result, indent=2))
        return 0 if result['video_and_nonzero_audio_received'] else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'live_data_received': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
