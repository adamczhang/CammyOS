#!/usr/bin/env python3
"""Build a credential-free TF filesystem payload locally; never writes a card.

SPDX-License-Identifier: MIT
"""
import os
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.shared_capture_profile import fragment


def payload(root=ROOT):
    version = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid release version')
    source = (root / 'src/cammy_portal.c').read_text()
    if f'#define VERSION "{version}"' not in source:
        raise ValueError('Portal source version differs from release')
    inputs = {'cammy-portal': 'build/arm/cammy-portal',
              'busybox': 'build/arm/busybox-dhcp',
              'wpa_supplicant': 'build/arm/wpa_supplicant', 'wpa_cli': 'build/arm/wpa_cli',
              'native-service-client': 'build/arm/native-service-client',
              'native-media-start.sh': 'scripts/native-media-start.sh',
              'daemon-exec': 'build/arm/daemon-exec', 'startup.sh': 'scripts/tf-runtime-startup.sh',
              'dhcp.sh': 'scripts/home-dhcp.sh', 'shared-capture.sh': 'scripts/shared-capture.sh',
              'LICENSE-Cammy': 'LICENSE', 'LICENSE-BusyBox': 'build/busybox-dhcp-src/LICENSE',
              'COPYING-wpa_supplicant': 'build/wpa_supplicant-2.12/COPYING'}
    result = {name: (root / path).read_bytes() for name, path in inputs.items()}
    result['shared-capture.fragment'] = fragment().encode()
    for name in ['cammy-portal', 'busybox', 'wpa_supplicant', 'wpa_cli', 'daemon-exec', 'native-service-client']:
        data = result[name]
        if data[:6] != b'\x7fELF\x01\x01' or int.from_bytes(data[18:20], 'little') != 40:
            raise ValueError(name + ' must be an ARM32 little-endian ELF')
    if version.encode() not in result['cammy-portal']:
        raise ValueError('ARM portal does not contain release version; rebuild it first')
    result['RELEASE'] = (version + '\n').encode()
    result['SOURCE-NOTICE.txt'] = (
        'Private local-use runtime; contains no stock camera binaries or credentials.\n'
        'Cammy original sources: MIT (LICENSE-Cammy).\n'
        'BusyBox 1.38.0: GPLv2 (LICENSE-BusyBox); local source/config: build/busybox-dhcp-src.\n'
        'wpa_supplicant 2.12: see COPYING-wpa_supplicant; source: build/wpa_supplicant-2.12.\n'
        'libnl-tiny static dependency: local pinned source at downloads/libnl-tiny;\n'
        'copyright/license notices are retained in those source files.\n'
        'Include matching complete sources/notices and meet applicable licenses before distribution.\n'
        'Existing installed S99cammy-tf hook/private config are prerequisites, not included.\n'
    ).encode()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.items())}
    result['manifest.json'] = (json.dumps({'version': version, 'kind': 'tf-userspace-runtime',
        'kernel_replaced': False, 'firmware_image': False, 'files': hashes}, indent=2, sort_keys=True) + '\n').encode()
    result['SHA256SUMS'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
        for name, data in sorted(result.items())).encode()
    return version, result


def build(root=ROOT):
    version, files = payload(root)
    dest = root / 'dist' / f'cammyos-tf-runtime-{version}.tar.gz'
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix('.tmp')
    with temporary.open('wb') as output:
        with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode='w|') as tar:
                for name, data in sorted(files.items()):
                    info = tarfile.TarInfo('CammyOS/' + name)
                    info.size = len(data); info.mtime = 0; info.uid = info.gid = 0
                    info.mode = 0o700 if name in ('cammy-portal','busybox','wpa_supplicant','wpa_cli','daemon-exec','startup.sh','dhcp.sh') else 0o600
                    tar.addfile(info, io.BytesIO(data))
    temporary.replace(dest)
    checksum = hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix(dest.suffix + '.sha256').write_text(checksum + '  ' + dest.name + '\n')
    return dest


if __name__ == '__main__':
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')], check=True, stdout=subprocess.DEVNULL)
    path = build()
    print(f'Built {path}: {path.stat().st_size} bytes. No card or camera written.')
