#!/usr/bin/env python3
"""Observe sustained home Wi-Fi operation with no PC USB connection or sensing.

The operator must power-cycle from a separate USB supply before this test.
SPDX-License-Identifier: MIT
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import discover  # noqa: E402
import os
import argparse
import datetime
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sdk'))
from cammy import Cammy


def usb_present():
    for serial in Path('/sys/bus/usb/devices').glob('*/serial'):
        try:
            if serial.read_text().strip() == '20080411':
                return True
        except OSError:
            pass
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=720)
    parser.add_argument('--startup-timeout', type=float, default=180)
    args = parser.parse_args()
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')],
                   check=True, stdout=subprocess.DEVNULL)
    token = (ROOT / 'private/ram-test.token').read_text().strip()
    client = Cammy('http://127.0.0.1:8790', token, 'power-only-check')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    rows, since, last, last_print, acquired = [], None, None, 0, False
    deadline = time.monotonic() + args.startup_timeout + args.seconds + 30
    success = False
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            row = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       PC_camera_USB_present=usb_present())
            try:
                with opener.open('http://127.0.0.1:8790/health', timeout=3) as reply:
                    health = json.load(reply)
                row.update(connected=health['connected'], endpoint=health.get('endpoint'))
                if row['connected'] and row['endpoint'] == discover.portal() and not row['PC_camera_USB_present']:
                    row['identity'], row['state'] = client.identity(), client.state()
                    if since is None:
                        since = now
                        row['acquire'] = client.acquire(10)
                        acquired = True
                        row['release'] = client.release()
                        acquired = False
                    row['continuous_seconds'] = round(now - since, 1)
                else:
                    since = None
            except Exception as error:
                row['error'] = type(error).__name__ + ': ' + str(error)
                since = None
            rows.append(row)
            state = row.get('connected'), row.get('PC_camera_USB_present'), bool(row.get('error'))
            if state != last or now - last_print >= 30:
                print({k: v for k, v in row.items() if k not in ('identity', 'state')}, flush=True)
                last, last_print = state, now
            if row.get('continuous_seconds', 0) >= args.seconds:
                row['final_acquire'] = client.acquire(10)
                acquired = True
                row['final_release'] = client.release()
                acquired = False
                success = True
                break
            time.sleep(2)
    finally:
        if acquired:
            try:
                client.release()
            except Exception:
                pass
        ports = {}
        for port in (5555, 8787):
            with socket.socket() as sock:
                sock.settimeout(1)
                ports[str(port)] = sock.connect_ex((discover.hosts()[0], port)) == 0
        result = dict(success=success, required_stable_seconds=args.seconds,
                      expected_home_endpoint=discover.portal(),
                      camera_USB_present_at_end=usb_present(), direct_home_ports_open=ports,
                      rows=rows)
        (ROOT / 'evidence/power-only-12min.json').write_text(json.dumps(result, indent=2) + '\n')
        print({k: v for k, v in result.items() if k != 'rows'}, flush=True)
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
