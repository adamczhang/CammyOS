#!/usr/bin/env python3
"""Observe a physical startup over USB and verify home control via the PC gateway.

Does not reboot the camera or enable its Wi-Fi. No sensing is started.
SPDX-License-Identifier: MIT
"""
import os
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ADB = ROOT.parent / 'Cammy Bot/tools/adb-camera.sh'
sys.path.insert(0, str(ROOT / 'sdk'))
from cammy import Cammy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-boot', required=True)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--output', type=Path, default=ROOT / 'evidence/guarded-coldboot.json')
    args = parser.parse_args()
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')],
                   check=True, stdout=subprocess.DEVNULL)
    token = (ROOT / 'private/ram-test.token').read_text().strip()
    client = Cammy('http://127.0.0.1:8790', token, 'coldboot-check')
    rows, new_boot, last, capture, log = [], None, None, None, None
    result = 'not_observed'
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            reply = subprocess.run([str(ADB), '-s', '20080411', 'shell',
                'echo BOOT; cat /proc/sys/kernel/random/boot_id; echo GUARD; '
                'cat /etc/cammy/startup.pending; echo STATE; cat /tmp/cammy-runtime/state'],
                capture_output=True, text=True, timeout=5)
            raw = reply.stdout.strip()
            row = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       usb_rc=reply.returncode, status=raw)
            rows.append(row)
            if raw != last:
                print(row, flush=True)
                last = raw
            lines = raw.splitlines()
            boot = lines[1] if len(lines) > 1 and lines[0] == 'BOOT' else None
            if boot and boot != args.previous_boot:
                if new_boot and boot != new_boot:
                    result = 'reset_during_startup'
                    break
                new_boot = boot
                result = 'startup_incomplete'
                if capture is None:
                    log = (ROOT / 'private/coldboot/guarded-start-kernel.log').open('wb')
                    capture = subprocess.Popen([str(ADB), '-s', '20080411', 'shell',
                        'cat /proc/kmsg'], stdout=log, stderr=subprocess.STDOUT)
                if 'HOME_STATION' in raw:
                    try:
                        row['identity'] = client.identity()
                        row['acquire'] = client.acquire(10)
                        row['release'] = client.release()
                        row['state'] = client.state()
                        if 'phase=' not in raw:
                            result = 'passed'
                            break
                    except Exception as error:
                        row['gateway_error'] = type(error).__name__ + ': ' + str(error)
            time.sleep(2)
    finally:
        if capture:
            capture.terminate()
            try:
                capture.wait(timeout=3)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait()
        if log:
            log.close()
        args.output.write_text(json.dumps(dict(result=result, success=result == 'passed',
            previous_boot_id=args.previous_boot, new_boot_id=new_boot, rows=rows), indent=2) + '\n')
        print(dict(result=result, new_boot_id=new_boot), flush=True)
    return 0 if result == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
