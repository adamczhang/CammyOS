#!/usr/bin/env python3
"""Run the WP3 owned-media suite against the static ARM fixture under local qemu-arm.

User-mode Cortex-A7 emulation of the fixture process only: no camera, vendor
app, firmware image, device node, root privilege or network endpoint. Passing
shows ARM codegen/atomics and supervision behavior with the fake backend; it
does not show native-app integration, in-process ABI or hardware readiness.
SPDX-License-Identifier: MIT
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--qemu', type=Path, help='local qemu-arm (no install/download is performed)')
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    qemu = args.qemu or shutil.which('qemu-arm') or ROOT.parent / 'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'
    if not Path(qemu).is_file():
        parser.error('local qemu-arm not found; pass --qemu')
    if not args.no_build:
        subprocess.run(['make', '-s', '-C', str(ROOT / 'native/media'), 'arm'], check=True, stdout=subprocess.DEVNULL)
    fixture = ROOT / 'build/native-media-owned/arm/media-fixture'
    env = {**os.environ, 'CAMMY_MEDIA_FIXTURE': json.dumps([str(Path(qemu).resolve()), '-cpu', 'cortex-a7', str(fixture)])}
    return subprocess.run([sys.executable, '-m', 'unittest', '-v', 'tests.test_owned_media'], cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
