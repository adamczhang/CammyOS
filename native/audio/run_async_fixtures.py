#!/usr/bin/env python3
"""Local asynchronous driver fixtures: no camera, sdvcam execution or devices."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
TARGETS={'host':'async-host','host-asan':'async-asan','host-tsan':'async-tsan',
         'arm':'async-arm','device':'async-device'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',choices=TARGETS,default='host')
    args=parser.parse_args()
    subprocess.run(['make','-s','-C',str(ROOT/'native/audio'),TARGETS[args.target]],check=True)
    output=ROOT/'build/native-audio'/args.target
    cmd=[str(output/'async-fixture')]
    if args.target in ('arm','device'):
        qemu=Path(shutil.which('qemu-arm') or ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm')
        if not qemu.is_file():
            parser.error('local qemu-arm missing; no install/download performed')
        prefix=[str(qemu),'-cpu','cortex-a7']
        if args.target=='device':
            prefix+=['-L',str(ROOT/'build/native-loader/device-rootfs'),
                     '-E','LD_LIBRARY_PATH='+str(output)]
            sys.path.insert(0,str(ROOT))
            from native.loader.abi_audit import audit
            export_text=(ROOT/'native/audio/async.exports.map').read_text().split('global:',1)[1].split('local:',1)[0]
            report=audit(output/'libcammy_audio_async.so',ROOT/'build/native-loader/device-rootfs',
                         re.findall(r'\bca_\w+',export_text))
            (output/'abi-audit.json').write_text(json.dumps(report,indent=2)+'\n')
            if not report['pass']:
                print(json.dumps(report['failures']),file=sys.stderr)
                return 1
        cmd=[*prefix,*cmd]
    env={**os.environ,'CAMMY_ASYNC_FIXTURE':json.dumps(cmd)}
    if args.target=='host-tsan':env['CAMMY_ASYNC_TSAN']='1'
    return subprocess.run([sys.executable,'-m','unittest','-v','tests.test_async_audio'],
                          cwd=ROOT,env=env).returncode


if __name__=='__main__':
    sys.exit(main())
