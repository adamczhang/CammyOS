#!/usr/bin/env python3
"""Run an explicit command with the verified project-local WebRTC runtime."""
import hashlib
import json
import os
from pathlib import Path
import sys
from prepare_webrtc import ROOT, OUT, environment


def verified_environment():
    if not Path(os.environ.get('CAMMY_WORK_MOUNT', '/')).is_mount():raise RuntimeError('CAMMY_WORK_MOUNT is not mounted')
    manifest=json.loads((OUT/'dependency-manifest.json').read_text())
    for name,digest in manifest['plugins'].items():
        file=OUT/'plugins'/name
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('WebRTC plugin identity mismatch: '+name)
    return environment()


if __name__=='__main__':
    args=sys.argv[1:]
    if args[:1]==['--']:args=args[1:]
    if not args:raise SystemExit('Usage: python3 tools/with_webrtc.py PROGRAM [ARG ...]')
    os.execvpe(args[0],args,verified_environment())
