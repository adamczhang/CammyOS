#!/usr/bin/env python3
"""Observe Cammy boots and resets from the PC (PLAN milestone 1). Read-only.

Logs one line per change, plus a heartbeat every 30 s, to
private/reset-diag/observer-<UTC>.log:
- USB presence of the camera's ADB gadget (18d1:1002), read from PC sysfs (no
  camera load);
- on each USB appearance: boot_id and uptime through adb (one read);
- portal /health over Wi-Fi every 5 s.

It sends no commands that change camera state. Stop with Ctrl-C or SIGTERM.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from c3 import discover  # noqa: E402
import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT.parent / 'Cammy Bot/tools/build-deps/usr'
ADB = DEPS / 'lib/android-sdk/platform-tools/adb'
SERIAL = '20080411'


def usb_present():
    for dev in Path('/sys/bus/usb/devices').glob('*'):
        try:
            if (dev / 'idVendor').read_text().strip() == '18d1' and \
               (dev / 'idProduct').read_text().strip() == '1002':
                return True
        except OSError:
            continue
    return False


def adb_identity(port):
    env = dict(os.environ, LD_LIBRARY_PATH='%s:%s' % (DEPS / 'lib/x86_64-linux-gnu/android',
                                                      DEPS / 'lib/x86_64-linux-gnu'))
    try:
        out = subprocess.run([str(ADB), '-P', str(port), '-s', SERIAL, 'shell',
                              'cat /proc/sys/kernel/random/boot_id; cut -d" " -f1 /proc/uptime'],
                             capture_output=True, text=True, timeout=10, env=env).stdout
        parts = out.replace('\r', '').split()
        return (parts[0][:8], parts[1]) if len(parts) >= 2 else None
    except (OSError, subprocess.SubprocessError):
        return None


def portal(host):
    try:
        with urllib.request.urlopen('http://%s:8787/health' % host, timeout=2) as r:
            return json.loads(r.read()).get('version', 'ok')
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host', default=discover.hosts()[0])
    ap.add_argument('--adb-port', type=int, default=5038)
    ap.add_argument('--out-dir', type=Path, default=ROOT / 'private/reset-diag')
    a = ap.parse_args(argv)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(a.out_dir, 0o700)
    path = a.out_dir / ('observer-%s.log' % dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))

    def log(msg):
        line = '%s %s' % (dt.datetime.now().astimezone().strftime('%H:%M:%S'), msg)
        with path.open('a') as f:
            f.write(line + '\n')
        print(line, flush=True)

    log('observer start log=%s' % path)
    usb = None
    web = 'unknown'
    last_beat = 0
    last_web = 0
    try:
        while not stop:
            now = time.monotonic()
            present = usb_present()
            if present != usb:
                usb = present
                ident = adb_identity(a.adb_port) if present else None
                if present and ident is None:
                    time.sleep(3)
                    ident = adb_identity(a.adb_port)
                log('usb %s%s' % ('UP' if present else 'DOWN',
                                  ' boot=%s uptime=%s' % ident if ident else ''))
            if now - last_web >= 5:
                last_web = now
                state = portal(a.host) or 'down'
                if state != web:
                    web = state
                    log('portal %s' % web)
            if now - last_beat >= 30:
                last_beat = now
                log('beat usb=%s portal=%s' % ('UP' if usb else 'DOWN', web))
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    log('observer stop')


if __name__ == '__main__':
    main()
