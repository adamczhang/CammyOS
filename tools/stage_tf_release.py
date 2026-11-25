#!/usr/bin/env python3
"""Verify and stage a TF release under local dist/, never overwrite a runtime/card.

SPDX-License-Identifier: MIT
"""
import os
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
MAX_SIZE = 16 * 1024 * 1024


def verify(archive):
    files = {}; total = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for entry in tar:
            path = PurePosixPath(entry.name)
            if not entry.isfile() or len(path.parts) != 2 or path.parts[0] != 'CammyOS' or '..' in path.parts:
                raise ValueError('Only regular files directly inside CammyOS are accepted')
            name = path.parts[1]
            if name in files or entry.size < 0 or entry.size > MAX_SIZE:
                raise ValueError('Duplicate or oversized member')
            total += entry.size
            if total > MAX_SIZE: raise ValueError('Release too large')
            files[name] = tar.extractfile(entry).read()
    manifest = json.loads(files['manifest.json'])
    if manifest.get('kind') != 'tf-userspace-runtime' or manifest.get('firmware_image') is not False:
        raise ValueError('Not a TF userspace runtime')
    expected = manifest['files']
    if set(files) != set(expected) | {'manifest.json', 'SHA256SUMS'}:
        raise ValueError('Unexpected or missing files')
    for name, digest in expected.items():
        if hashlib.sha256(files[name]).hexdigest() != digest:
            raise ValueError('Payload hash mismatch: ' + name)
    sums = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                   for name, data in sorted(files.items()) if name != 'SHA256SUMS')
    if files['SHA256SUMS'].decode() != sums:
        raise ValueError('Checksum manifest mismatch')
    if files['RELEASE'].decode().strip() != manifest['version']:
        raise ValueError('Version mismatch')
    return files


def stage(archive, destination):
    files = verify(archive)
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        path = destination / name
        path.write_bytes(data)
        path.chmod(0o700 if name in ('cammy-portal','busybox','wpa_supplicant','wpa_cli','daemon-exec','startup.sh','dhcp.sh') else 0o600)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--stage-name', default='tf-runtime-0.2.0')
    args = parser.parse_args()
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    if not args.stage_name or Path(args.stage_name).name != args.stage_name or args.stage_name in ('.','..'):
        parser.error('--stage-name must be a single folder name')
    dest = ROOT / 'dist' / args.stage_name / 'CammyOS'
    if dest.parent.is_symlink() or (ROOT / 'dist').is_symlink():
        parser.error('Refusing symlink staging directories')
    print('Verified and staged locally:', stage(args.archive, dest))
