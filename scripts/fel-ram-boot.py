#!/usr/bin/env python3
"""Run the prepared flash-free RAM-kernel experiment from a fresh TF/FEL boot.

Root required for USB access. The only device operations are RAM read/write,
code execution, and version handshake. No flash/erase/program operation exists.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
XFEL = ROOT.parent / 'Cammy Bot/tools/xfel/xfel'
BUILD = ROOT / 'build/fel'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', args.attempt):
        raise ValueError('Invalid attempt label')
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')], check=True,
                   stdout=subprocess.DEVNULL)
    if os.geteuid() != 0:
        raise PermissionError('Run as root for direct FEL USB access')
    manifest = json.loads((BUILD / 'ram-boot-manifest.json').read_text())
    out = ROOT / 'evidence' / ('ram-boot-' + args.attempt)
    out.mkdir(mode=0o755)
    report = dict(test=manifest['test'], started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  flash_written=False, steps=[], boot_verified=False)

    def save():
        (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(*command, timeout=25):
        label = '%02d-%s' % (len(report['steps']), command[0])
        with (out / (label + '.log')).open('wb') as log:
            subprocess.run([str(XFEL), *command], check=True, timeout=timeout,
                           stdout=log, stderr=subprocess.STDOUT)
        report['steps'].append(list(command)); save()
        return (out / (label + '.log')).read_text(errors='replace')

    def upload_verify(address, path, name):
        data = path.read_bytes()
        if address != 0x28000 and not (0x40000000 <= address < address + len(data) <= 0x60000000):
            raise ValueError('Upload outside declared SRAM/DRAM ranges')
        if address == 0x28000 and len(data) != 0x4c80:
            raise ValueError('Unexpected SRAM initializer size')
        run('write', hex(address), str(path), timeout=40)
        back = out / (name + '-readback.bin')
        run('read', hex(address), hex(len(data)), str(back), timeout=40)
        if back.read_bytes() != data:
            raise ValueError(name + ' RAM readback mismatch')
        print(name + ': RAM readback verified', flush=True)

    try:
        version = run('version', timeout=10)
        if 'ID=0x00188600' not in version:
            raise ValueError('Unexpected or missing V85x FEL handshake')
        ddr = BUILD / 'ddr-m105.bin'
        expected = json.loads((BUILD / 'ddr-m105.json').read_text())['sha256']
        if hashlib.sha256(ddr.read_bytes()).hexdigest() != expected:
            raise ValueError('DDR artifact hash mismatch')
        upload_verify(0x28000, ddr, 'ddr')
        run('exec', '0x28000', timeout=15)
        for name, address, seed in [('low', 0x44000000, 31), ('high', 0x5f000000, 113)]:
            path = out / ('dram-' + name + '.bin')
            path.write_bytes(bytes((i * 17 + (i >> 8) * seed + seed) & 255 for i in range(65536)))
            upload_verify(address, path, 'dram-' + name)
        for item in manifest['images']:
            path = BUILD / item['file']
            if path.stat().st_size != item['length'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Boot artifact changed: ' + item['file'])
            upload_verify(int(item['address'], 0), path, item['file'])
        report['handoff_issued'] = True; save()
        run('exec', manifest['entry'], timeout=15)
        print('Kernel handoff issued; boot success requires new Linux/USB evidence.', flush=True)
    except Exception as exc:
        report['error'] = str(exc); save()
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
