#!/usr/bin/env python3
"""Run the actual ARM portal protocol suite under local Cortex-A7 emulation.

No camera, firmware image, root privileges or network endpoint is required.
SPDX-License-Identifier: MIT
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, help='Path to local qemu-arm')
    parser.add_argument('--binary', type=Path, default=ROOT / 'build/arm/cammy-portal')
    args = parser.parse_args()
    qemu = args.qemu or shutil.which('qemu-arm') or ROOT.parent / 'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'
    if not Path(qemu).is_file() or not args.binary.is_file():
        parser.error('Local qemu-arm and built ARM portal required; no automatic install/download is performed')
    command = [str(Path(qemu).resolve()), '-cpu', 'cortex-a7', str(args.binary.resolve())]
    env={**os.environ, 'CAMMY_TEST_COMMAND': json.dumps(command)}
    result=subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                           '-p', 'test_portal.py', '-v'], cwd=ROOT, env=env)
    if result.returncode:return result.returncode
    compiler=ROOT/'build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc'
    shim=ROOT/'build/arm/arm_legacy_stat.o'
    if not compiler.is_file() or not shim.is_file():
        parser.error('Built local ARM toolchain/shim required for preview fault fixtures; run make arm first')
    env.update(CAMMY_PREVIEW_CC=json.dumps([str(compiler),str(shim),'-static','-march=armv7-a','-mfpu=neon-vfpv4','-mfloat-abi=hard']),
               CAMMY_PREVIEW_RUNNER=json.dumps(command[:-1]))
    return subprocess.run([sys.executable,'-m','unittest','discover','-s','tests',
                           '-p','test_preview_readiness.py','-v'],cwd=ROOT,env=env).returncode



if __name__ == '__main__':
    raise SystemExit(main())
