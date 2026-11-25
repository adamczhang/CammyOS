#!/usr/bin/env python3
"""Run the WP2 bridge suite against the static ARM build under local qemu-arm.

User-mode Cortex-A7 emulation of the bridge process only: no camera, vendor app,
firmware image, device node, root privilege or network endpoint is involved.
Passing here shows ARM codegen/ABI and protocol behavior with inert backends;
it does not show native-app integration or hardware readiness.
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
        subprocess.run(['make', '-s', '-C', str(ROOT / 'native/bridge'), 'arm'], check=True,
                       stdout=subprocess.DEVNULL)
    out = ROOT / 'build/native-bridge/arm'
    prefix = [str(Path(qemu).resolve()), '-cpu', 'cortex-a7']
    env = {**os.environ,
           'CAMMY_BRIDGE_COMMAND': json.dumps(prefix + [str(out / 'cammy-bridge')]),
           'CAMMY_BRIDGE_SELFTEST': json.dumps(prefix + [str(out / 'bridge-selftest')])}
    return subprocess.run([sys.executable, '-m', 'unittest', '-v', 'tests.test_native_bridge'],
                          cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
