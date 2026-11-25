#!/usr/bin/env python3
"""Prepare the pinned face renderer/editor and editable official Reachy app.

Host-only: no camera contact, startup/init write or automatically started sensing.
Run the installed app through Reachy Mini Control or its existing app-manager API.
"""
import os
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT.parent/'Reachy-Mini-Control'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python',type=Path,default=REFERENCE/'runtime/venv/bin/python')
    args=parser.parse_args()
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['make','-C','native/paramface','host'],cwd=ROOT,check=True)
    web=ROOT/'downloads/stackchan-face-editor/web'
    subprocess.run(['npm','ci','--cache',str(ROOT/'build/npm-cache')],cwd=web,check=True)
    subprocess.run(['npm','run','build','--','--base=/editor/'],cwd=web,check=True)
    # All runtime dependencies install into the explicit reference venv, never system Python.
    if not args.python.is_file() or not (args.python.parent.parent/'pyvenv.cfg').is_file():
        raise RuntimeError('A prepared virtual environment is required')
    subprocess.run([str(args.python),'-m','pip','install','--cache-dir',str(ROOT/'build/pip-cache'),'-e',str(ROOT/'host/cammy_reachy_app')+'[verification]'],check=True)
    print(json.dumps({'prepared':True,'entry_point':'cammy_reachy_app',
                      'url':'http://127.0.0.1:8801','sensing_started':False,
                      'lcd_verified':False}))


if __name__=='__main__':
    main()
