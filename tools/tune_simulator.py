#!/usr/bin/env python3
"""Exercise/tune the PC stack against an in-process body, never a remote endpoint.

Emits synthetic-only acceptance and loopback latency results, not hardware timings.
SPDX-License-Identifier: MIT
"""
import argparse
import json
from pathlib import Path
import secrets
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from cammy_host import Gateway, make_server
from cammy_simulator import Simulator
from cammy import Cammy, CammyError


def run(samples=30):
    simulator = Simulator()
    gateway = Gateway(secrets.token_urlsafe(32), ['simulation://local'], simulator=simulator)
    gateway.refresh()
    server = make_server(gateway, ('127.0.0.1', 0))
    worker = threading.Thread(target=server.serve_forever); worker.start()
    client = Cammy(f'http://127.0.0.1:{server.server_port}', gateway.token, 'local-tuning')
    timings = []
    try:
        client.acquire(120)
        client.preview_start()
        for n in range(samples):
            start = time.perf_counter()
            client.request('POST', '/v1/look', {'pan_degrees': [-20, 0, 20][n % 3]})
            client.request('POST', '/v1/face', {'expression': 'listening' if n % 2 else 'thinking'})
            assert client.request('GET', '/v1/simulation/frame')['fresh']
            timings.append((time.perf_counter() - start) * 1000)
        # Model frozen acquisition without waiting on wall-clock time.
        client.request('POST', '/v1/simulation', {'stale_frame': True})
        with simulator.lock:
            simulator.frame_time -= 2
        try:
            client.request('GET', '/v1/simulation/frame')
            raise AssertionError('Frozen frame was accepted')
        except CammyError as failure:
            assert failure.status == 503
        client.request('POST', '/v1/simulation', {'stale_frame': False})
        assert client.request('GET', '/v1/simulation/frame')['fresh']
        client.request('POST', '/v1/simulation', {'disconnected': True})
        assert not gateway.snapshot()['connected']
        client.request('POST', '/v1/simulation', {'disconnected': False})
        assert gateway.snapshot()['connected']
        assert client.state()['owner'] is None
        client.acquire()
        audio = client.request('POST', '/v1/audio/capture', {})
        assert audio['simulation'] and audio['duration_seconds'] == .1
        client.request('POST', '/v1/speak', {'text': 'Local simulation acceptance completed.'})
        start = time.perf_counter(); client.stop()
        stop_ms = (time.perf_counter() - start) * 1000
        assert client.state()['owner'] is None
        sorted_ms = sorted(timings)
        return {'simulation': True, 'hardware_tested': False, 'external_endpoints_used': False,
                'samples': samples, 'three_request_cycle_ms': {
                    'median': round(statistics.median(timings), 3),
                    'p95': round(sorted_ms[min(samples - 1, int(samples * .95))], 3)},
                'stop_round_trip_ms': round(stop_ms, 3),
                'stale_frame_rejected': True, 'fresh_frame_recovered': True,
                'reconnection_requires_new_lease': True, 'synthetic_audio_validated': True,
                'result': 'pass'}
    finally:
        gateway.stop.set()
        server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000:
        parser.error('--samples must be 1..1000')
    print(json.dumps(run(args.samples), indent=2))
