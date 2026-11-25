#!/usr/bin/env python3
"""Run finite-speaker tests with synthetic host, sanitizer or ARM sinks only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('host', 'host-asan', 'arm'), default='host')
    args = parser.parse_args()
    subprocess.run(['make', '-s', '-C', str(ROOT / 'native/audio'), args.target], check=True)
    cmd = [str(ROOT / 'build/native-audio' / args.target / 'audio-fixture')]
    if args.target == 'arm':
        qemu = shutil.which('qemu-arm') or ROOT.parent / 'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'
        if not Path(qemu).is_file():
            parser.error('local qemu-arm missing; no download/install is performed')
        cmd = [str(qemu), '-cpu', 'cortex-a7', *cmd]
    env = {**os.environ, 'CAMMY_AUDIO_FIXTURE': json.dumps(cmd)}
    return subprocess.run([sys.executable, '-m', 'unittest', '-v', 'tests.test_owned_audio'],
                          cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
