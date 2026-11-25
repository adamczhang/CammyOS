#!/usr/bin/env python3
"""Resident, provider-neutral Cammy gateway. Default listener is PC loopback.

Maintains/retries the body connection. Observation and actuation remain explicit
API operations governed by the body's existing control lease.
SPDX-License-Identifier: MIT
"""
import argparse
import datetime
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import io
from pathlib import Path
import signal
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sdk'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cammy_http import camera_opener
from cammy_vision import Vision, VisionError

MONITOR_S = 10  # camera /health + /v1/identity check period
GET_PATHS = {'/v1/speaker/status','/v1/media/status','/v1/face/status','/v1/input/status','/v1/audio/stream/status', '/v1/preview/status', '/v1/probes', '/v1/diagnostics', '/v1/identity', '/v1/capabilities', '/v1/state', '/v1/events'}
POST_PATHS = {'/v1/speaker/play','/v1/speaker/cancel','/v1/media/cancel','/v1/media/prepare','/v1/input','/v1/audio/stream', '/v1/probes', '/v1/session', '/v1/session/release', '/v1/stop',
              '/v1/preview/start', '/v1/preview/check', '/v1/preview/stop', '/v1/face',
              '/v1/look', '/v1/speak', '/v1/audio/capture'}


class Gateway:
    def __init__(self, token, endpoints, status_file=None, offline=False, simulator=None, native_photo=False):
        self.token, self.endpoints = token, endpoints
        self.offline = offline
        self.simulator = simulator
        self.status_file = Path(status_file) if status_file else None
        self.opener = camera_opener()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.state = {'connected': False, 'endpoint': None, 'name': 'CammyOS',
                      'sensing_started_by_gateway': False, 'offline': self.offline, 'simulation': self.simulator is not None}
        self.vision = Vision(self, enabled=native_photo)

    def request(self, endpoint, method, path, body=None, agent='host-monitor', boot_id=None, lease_id=None):
        if self.offline:
            raise OSError('Offline mode: camera requests disabled')
        if self.simulator is not None:
            status, payload = self.simulator.request(method, path, body, agent)
            data = json.dumps(payload).encode()
            if status >= 400:
                raise urllib.error.HTTPError(path, status, 'Simulated error', {}, io.BytesIO(data))
            return status, data
        request = urllib.request.Request(endpoint.rstrip('/') + path, data=body, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'X-Agent-ID': agent,
                     'Content-Type': 'application/json', **({'X-Cammy-Boot-ID':boot_id} if boot_id else {}), **({'X-Cammy-Lease-ID':lease_id} if lease_id else {})})
        with self.opener.open(request, timeout=2) as reply:
            data = reply.read(65537)
            if len(data) > 65536:
                raise ValueError('Oversized device reply')
            return reply.status, data

    def open_audio_stream(self, endpoint, agent, boot_id=None, lease_id=None):
        if self.offline or self.simulator is not None:
            raise VisionError(501, 'Live audio is unavailable in offline/simulator mode')
        request = urllib.request.Request(endpoint.rstrip('/') + '/v1/audio/stream', data=b'{}', method='POST',
            headers={'Authorization': 'Bearer ' + self.token, 'X-Agent-ID': agent,
                     'Content-Type': 'application/json', **({'X-Cammy-Boot-ID':boot_id} if boot_id else {}), **({'X-Cammy-Lease-ID':lease_id} if lease_id else {})})
        return self.opener.open(request, timeout=3)

    def refresh(self):
        result = {'connected': False, 'endpoint': None, 'name': 'CammyOS',
                  'sensing_started_by_gateway': False, 'offline': self.offline, 'simulation': self.simulator is not None}
        for endpoint in ([] if self.offline else self.endpoints):
            try:
                _, raw = self.request(endpoint, 'GET', '/health')
                if json.loads(raw).get('service') != 'cammy-portal':
                    continue
                _, identity = self.request(endpoint, 'GET', '/v1/identity')
                result.update(connected=True, endpoint=endpoint,
                    identity=json.loads(identity),
                    last_seen_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
                break
            except urllib.error.HTTPError as exc:
                exc.close()
                continue
            except (OSError, ValueError, urllib.error.URLError):
                continue
        with self.lock:
            changed = result.get('endpoint') != self.state.get('endpoint') or result['connected'] != self.state['connected']
            self.state = result
        if changed:
            self.vision.disconnected()
        if changed and self.status_file:
            self.status_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.status_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, indent=2) + '\n')
            temporary.chmod(0o600)
            temporary.replace(self.status_file)
        return result

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def monitor(self):
        # 10 s, not 2 (2026-11-23): C3 holds the camera's control lease and watches it itself;
        # this gateway only serves manual tools (face lab, stock button trial, bridge smoke).
        while not self.stop.is_set():
            self.refresh()
            self.stop.wait(MONITOR_S)


def make_server(gateway, address=('127.0.0.1', 8790)):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, data, content_type='application/json'):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src blob:; media-src blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def handle_api(self):
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if self.command == 'GET' and self.path in assets:
                name, content_type = assets[self.path]
                self.reply(200, (Path(__file__).parent / 'web' / name).read_bytes(), content_type)
                return
            if self.command == 'GET' and self.path == '/health':
                self.reply(200, {'service': 'cammy-host', **gateway.snapshot()})
                return
            given = self.headers.get('Authorization', '')
            if not hmac.compare_digest(given.encode(), ('Bearer ' + gateway.token).encode()):
                self.reply(401, {'error': 'Valid bearer token required'}); return
            if self.command == 'GET' and self.path == '/v1/gateway':
                self.reply(200, gateway.snapshot()); return
            simulation_admin = gateway.simulator is not None and self.path == '/v1/simulation'
            simulation_frame = gateway.simulator is not None and self.path == '/v1/simulation/frame' and self.command == 'GET'
            vision_path = re.fullmatch(r'/v1/vision/(capabilities|capture|frame-[1-9][0-9]{0,18}(?:/image)?)', self.path)
            allowed = GET_PATHS if self.command == 'GET' else POST_PATHS
            probe_job = re.fullmatch(r'/v1/probes/job-[1-9][0-9]{0,19}' + (r'/cancel' if self.command == 'POST' else ''), self.path)
            if self.path not in allowed and not probe_job and not simulation_admin and not simulation_frame and not vision_path:
                self.reply(404, {'error': 'Unknown endpoint'}); return
            if self.headers.get('Transfer-Encoding'):
                self.reply(400, {'error': 'Transfer-Encoding unsupported'}); return
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) > 1:
                self.reply(400, {'error': 'Duplicate Content-Length'}); return
            try:
                length = int(lengths[0]) if lengths else 0
                if not 0 <= length <= (214000 if self.path=='/v1/speaker/play' else 1024):
                    raise ValueError()
            except ValueError:
                self.reply(413, {'error': 'Invalid request length'}); return
            state = gateway.snapshot()
            if not state['connected'] and not simulation_admin:
                self.reply(503, {'error': 'Camera disconnected; gateway is reconnecting'}); return
            self.connection.settimeout(3)
            try:
                body = self.rfile.read(length) if self.command == 'POST' else None
                if body is not None and len(body) != length:
                    self.reply(400, {'error': 'Incomplete request body'}); return
                agent = self.headers.get('X-Agent-ID', 'agent')
                boots=self.headers.get_all('X-Cammy-Boot-ID',[])
                if len(boots)>1 or boots and not re.fullmatch('[0-9a-f]{32}',boots[0]):raise VisionError(400,'Invalid boot binding')
                boot_binding=boots[0] if boots else None
                leases=self.headers.get_all('X-Cammy-Lease-ID',[])
                if len(leases)>1 or leases and not re.fullmatch('[0-9a-f]{32}',leases[0]):raise VisionError(400,'Invalid lease binding')
                control_bindings={}
                if boot_binding:control_bindings['boot_id']=boot_binding
                if leases:control_bindings['lease_id']=leases[0]
                if self.path == '/v1/audio/stream' and self.command == 'POST':
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError):
                        raise VisionError(400, 'Expected empty object') from None
                    if payload != {}:
                        raise VisionError(400, 'Expected empty object')
                    with gateway.open_audio_stream(state['endpoint'], agent,**control_bindings) as upstream:
                        expected = {'X-Cammy-Audio-Format': 'S16_LE', 'X-Cammy-Audio-Rate': '16000',
                                    'X-Cammy-Audio-Channels': '1'}
                        if upstream.headers.get_content_type() != 'application/octet-stream' or any(
                                upstream.headers.get(k) != v for k, v in expected.items()):
                            raise VisionError(502, 'Unsupported live audio format')
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Connection', 'close')
                        self.send_header('Cache-Control', 'no-store')
                        for key, value in expected.items(): self.send_header(key, value)
                        self.end_headers(); self.close_connection = True
                        try:
                            while True:
                                chunk = upstream.read(4096)
                                if not chunk: break
                                self.wfile.write(chunk); self.wfile.flush()
                        except OSError:
                            pass  # Never append JSON after PCM headers/data.
                    return
                if vision_path:
                    action = vision_path[1]
                    if action == 'capabilities' and self.command == 'GET':
                        self.reply(200, gateway.vision.capabilities()); return
                    if action == 'capture' and self.command == 'POST':
                        try:
                            payload = json.loads(body)
                        except (ValueError, UnicodeError):
                            raise VisionError(400, 'Expected empty JSON object') from None
                        if payload != {}:
                            raise VisionError(400, 'Expected empty object')
                        self.reply(202, gateway.vision.start(agent)); return
                    if action.startswith('frame-') and self.command == 'GET':
                        image = action.endswith('/image')
                        data = gateway.vision.get(agent, action.split('/')[0], image)
                        self.reply(200, data, 'image/jpeg' if image else 'application/json'); return
                    raise VisionError(404, 'Unknown vision endpoint or method')
                if simulation_admin:
                    try:
                        settings = json.loads(body) if self.command == 'POST' else None
                        if self.command == 'POST' and not isinstance(settings, dict):
                            raise ValueError('Expected object')
                        status, data = gateway.simulator.settings(settings)
                    except (ValueError, UnicodeError):
                        status, data = 400, {'error': 'Invalid simulator settings JSON'}
                    gateway.refresh()
                    self.reply(status, data)
                    return
                with gateway.vision.control(self.path, agent, self.command):
                    status, data = gateway.request(state['endpoint'], self.command, self.path, body, agent,**control_bindings)
                self.reply(status, data)
            except VisionError as exc:
                self.reply(exc.status, {'error': str(exc), 'hardware_cleanup_confirmed': False})
            except urllib.error.HTTPError as exc:
                with exc:
                    self.reply(exc.code, exc.read(65536))
            except (OSError, ValueError, urllib.error.URLError):
                self.reply(503, {'error': 'Camera connection unavailable'})

        do_GET = handle_api
        do_POST = handle_api

    return ThreadingHTTPServer(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--offline', action='store_true',
                        help='Disable every outbound camera request (including monitoring)')
    parser.add_argument('--simulate', choices=['stock', 'companion'], help='Use only the in-process body simulator')
    parser.add_argument('--native-photo', action='store_true',
                        help='Enable experimental explicit native photo snapshots (TF photo and temporary mode change)')
    parser.add_argument('--token-file', type=Path, help='Override the configured token file')
    parser.add_argument('--port', type=int, help='Override the loopback listener port')
    args = parser.parse_args()
    if args.simulate and (args.config or args.offline):
        parser.error('--simulate cannot be combined with --config or --offline')
    if not args.config and not args.offline and not args.simulate:
        parser.error('--config is required unless --offline is used')
    config = json.loads(args.config.read_text()) if args.config else {}
    token_file = args.token_file or config.get('token_file')
    if not token_file:
        parser.error('Provide --token-file or token_file in --config')
    token = Path(token_file).read_text().strip()
    if not token:
        parser.error('Token file must not be empty')
    simulator = None
    if args.simulate:
        from cammy_simulator import Simulator
        simulator = Simulator(args.simulate)
    endpoints = list(config.get('endpoints', []))
    from c3.discover import cached     # the address C3 last found Cammy at (DHCP moves it)
    found = cached()
    if found:
        endpoints = [found] + [e for e in endpoints if e != found]
    gateway = Gateway(token, ['simulation://local'] if simulator else endpoints,
                      None if args.offline else config.get('status_file'), args.offline, simulator,
                      args.native_photo or config.get('native_photo') is True)
    server = make_server(gateway, ('127.0.0.1', args.port if args.port is not None else config.get('port', 8790)))
    worker = threading.Thread(target=gateway.monitor, daemon=True)
    worker.start()
    def stop(_signum, _frame):
        gateway.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        gateway.vision.close()
        gateway.stop.set(); server.server_close(); worker.join(timeout=5)


if __name__ == '__main__':
    main()
