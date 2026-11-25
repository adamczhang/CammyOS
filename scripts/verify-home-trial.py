#!/usr/bin/env python3
"""Observe the staged camera trial over USB; acknowledge only a working LAN link."""
import datetime
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
ADB = ROOT.parent / 'Cammy Bot/tools/adb-camera.sh'


def adb(command):
    return subprocess.check_output([str(ADB), '-s', '20080411', 'shell', command],
                                   text=True, timeout=8).replace('\r', '')


def status():
    text = adb('/tmp/cammy-home/wpa_cli -p /tmp/cammy-home/ctrl -i wlan0 status')
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def probe(address):
    subprocess.run(['ping', '-I', 'enp11s0', '-c', '1', '-W', '2', address],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
    with socket.create_connection((address, 8082), 3) as sock:
        sock.sendall(('GET / HTTP/1.0\r\nHost: ' + address + '\r\n\r\n').encode())
        first = sock.recv(1024).split(b'\r\n')[0].decode(errors='replace')
    if '200' not in first:
        raise ValueError('Camera HTTP health probe did not return200')
    return first


def main():
    deadline = time.monotonic() + 65
    while True:
        state = status()
        if state.get('wpa_state') == 'COMPLETED' and state.get('ip_address'):
            address = str(ipaddress.IPv4Address(state['ip_address']))
            if ipaddress.IPv4Address(address).is_private and not address.startswith('192.168.10.'):
                try:
                    first = probe(address)
                    break
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
        if time.monotonic() >= deadline:
            raise TimeoutError('No completed association with verified home-LAN service')
        time.sleep(1)
    adb('echo HOST_LAN_CONFIRMED >/tmp/cammy-home/home-confirmed')
    result = dict(timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  state=state, host_ethernet_ping=True, home_http=first,
                  trial_acknowledged=True, flash_written=False,
                  persisted_on_camera=False)
    # This vendor driver can ignore iw's disconnect request. An interface down/up
    # transition forces link loss without setting supplicant's user-disconnect
    # flag. Its own retry mechanism must reassociate without a reconnect command.
    adb('ifconfig wlan0 down; sleep 1')
    disabled = status()
    saw_disconnected = disabled.get('wpa_state') != 'COMPLETED'
    adb('ifconfig wlan0 up')
    deadline = time.monotonic() + 35
    while True:
        current = status()
        if current.get('wpa_state') != 'COMPLETED':
            saw_disconnected = True
        if saw_disconnected and current.get('wpa_state') == 'COMPLETED':
            probe(address)
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('No verified automatic reassociation after driver disconnect')
        time.sleep(.5)
    result.update(driver_disconnect_observed=saw_disconnected,
                  disabled_state=disabled,
                  automatic_reassociation_verified=True, final_state=current)
    (ROOT / 'evidence/home-wifi-trial.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
