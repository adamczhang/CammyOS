#!/usr/bin/env python3
"""Experimental CammyOS host hook for an explicitly leased M105 USB capture.

Default: metadata only. No raw USB operations, playback, mixer or camera writes.
SPDX-License-Identifier: MIT
"""
import argparse
import array
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sdk'))
from cammy import Cammy, CammyError


def identify(sysroot=Path('/sys'), serial='20080411'):
    devices = []
    for usb in (sysroot / 'bus/usb/devices').glob('*'):
        try:
            if (usb / 'idVendor').read_text().strip() != '18d1' or (usb / 'idProduct').read_text().strip() != '100d':
                continue
            if (usb / 'serial').read_text().strip() != serial:
                continue
            cards = []
            for card in (sysroot / 'class/sound').glob('card*'):
                if (card / 'device').resolve().is_relative_to(usb.resolve()):
                    cards.append(int(card.name[4:]))
            devices.append({'usb_path': usb.name, 'serial': serial, 'vid_pid': '18d1:100d', 'cards': cards})
        except (OSError, ValueError):
            continue
    if len(devices) != 1 or len(devices[0]['cards']) != 1:
        raise RuntimeError('Exactly one matching M105 PCCAM device and ALSA card required')
    return devices[0]


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=.3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=1)


def supervise(process, client, validate, deadline, clock=time.monotonic, sleep=time.sleep):
    while process.poll() is None:
        if clock() >= deadline:
            raise RuntimeError('USB capture deadline exceeded')
        state = client.state()
        if state['owner'] != client.agent or state['lease_remaining_seconds'] <= 0:
            raise RuntimeError('USB capture cancelled: body control lease lost or stopped')
        validate()
        sleep(.15)
    if process.returncode != 0:
        raise RuntimeError('USB capture process failed; no alternate/default audio device attempted')


def audio_summary(path):
    with wave.open(str(path), 'rb') as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError('Unexpected captured WAV format')
        count = audio.getnframes(); raw = audio.readframes(count)
    samples = array.array('h', raw)
    if sys.byteorder != 'little': samples.byteswap()
    return {'frames': count, 'duration_seconds': count / 16000,
            'peak': max((abs(x) for x in samples), default=0),
            'rms': round(math.sqrt(sum(x*x for x in samples) / len(samples)), 2) if samples else 0,
            'intelligibility_verified': False}


def capture(client, output, seconds=3, sysroot=Path('/sys'), procroot=Path('/proc')):
    if type(seconds) is not int or not 1 <= seconds <= 5:
        raise ValueError('Capture duration must be 1..5 seconds')
    device = identify(sysroot)
    card = device['cards'][0]
    stream = (procroot / f'asound/card{card}/stream0').read_text()
    if 'Capture:' not in stream or 'S16_LE' not in stream or '16000' not in stream:
        raise RuntimeError('Expected USB capture format was not advertised')
    identity = client.identity()
    if identity.get('simulation') or identity.get('os') != 'CammyOS':
        raise RuntimeError('A real authenticated CammyOS body is required')
    command = shutil.which('arecord')
    if not command: raise RuntimeError('Local arecord is required')
    process = None; acquired = False
    try:
        client.acquire(seconds + 4); acquired = True
        if identify(sysroot) != device: raise RuntimeError('USB identity changed before capture')
        with output.open('xb') as destination:
            output.chmod(0o600)
            process = subprocess.Popen([command, '-q', '-D', f'hw:{card},0', '-t', 'wav',
                '-f', 'S16_LE', '-r', '16000', '-c', '1', '-d', str(seconds), '-'],
                stdout=destination, stderr=subprocess.PIPE)
            def validate():
                if identify(sysroot) != device: raise RuntimeError('USB device disconnected or changed')
            supervise(process, client, validate, time.monotonic() + seconds + 1)
            # stdout is a seekable private file: arecord finalizes its WAV header.
        stats = audio_summary(output)
        if stats['frames'] != seconds * 16000: raise RuntimeError('Incomplete USB audio sample')
        return {'source': 'physical M105 USB-UAC', 'simulation': False, 'device': device,
                'sensing_via': 'CammyOS leased host probe', 'audio': stats}
    finally:
        if process is not None:
            stop_process(process)
            if process.stderr: process.stderr.close()
        if acquired:
            try: client.release()
            except CammyError as error:
                if error.status != 409: raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-audio', action='store_true')
    parser.add_argument('--seconds', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.capture_audio:
        print(json.dumps(identify(), indent=2)); return
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')], check=True, stdout=subprocess.DEVNULL)
    if args.output is None or not args.output.resolve().is_relative_to((ROOT / 'private').resolve()):
        parser.error('--output must be a new file under this project private/ directory')
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = json.loads((ROOT / 'private/host/config.json').read_text())
    token = Path(config['token_file']).read_text().strip()
    client = Cammy('http://127.0.0.1:' + str(config.get('port', 8790)), token,
                   'usb-probe-' + secrets.token_hex(4), timeout=.5)
    try:
        result = capture(client, args.output, args.seconds)
        result['final_state'] = client.state()
        args.output.with_suffix('.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, CammyError) as error:
        print('USB probe failed: ' + str(error), file=sys.stderr); raise SystemExit(1)


if __name__ == '__main__':
    main()
