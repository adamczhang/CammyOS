#!/usr/bin/env python3
"""Package a manual TF -> RAM userspace fallback, with no boot sectors or credentials."""
from pathlib import Path
import gzip
import hashlib
import io
import os
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')],
                   check=True, stdout=subprocess.DEVNULL)
    inputs = {
        'start.sh': ROOT / 'scripts/start-from-tf.sh',
        'busybox': ROOT / 'build/arm/busybox',
        'rootfs.tar.gz': ROOT / 'dist/cammyos-rootfs-ramtest.tar.gz',
    }
    sums = ''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + n + '\n'
                   for n, p in inputs.items()).encode()
    dest = ROOT / 'dist/cammyos-tf-userspace-fallback-v0.1.0.tar.gz'
    with dest.open('wb') as raw:
        os.fchmod(raw.fileno(), 0o600)
        with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode='w|') as tar:
                for name, p in inputs.items():
                    info = tar.gettarinfo(str(p), arcname='Cammy/' + name)
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = 'root'
                    info.mode = 0o700 if name in ('start.sh', 'busybox') else 0o600
                    with p.open('rb') as f:
                        tar.addfile(info, f)
                info = tarfile.TarInfo('Cammy/SHA256SUMS')
                info.size = len(sums); info.mode = 0o600
                tar.addfile(info, io.BytesIO(sums))
    print(f'Packaged {dest.name}: {dest.stat().st_size} bytes; no TF device written')


if __name__ == '__main__':
    main()
