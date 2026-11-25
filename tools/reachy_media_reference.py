#!/usr/bin/env python3
"""Start the managed official reference/app with the local WebRTC profile.

--restart replaces only our identified idle/reference process and this Cammy app;
any different active app or daemon refuses. No native camera process is signalled.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
import urllib.error
import urllib.request

from with_webrtc import verified_environment

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=ROOT.parent/'Reachy-Mini-Control'
BASE='http://127.0.0.1:8000'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path,post=False):
    req=urllib.request.Request(BASE+path,data=b'' if post else None,method='POST' if post else 'GET')
    with OPENER.open(req,timeout=4) as response:return json.load(response)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart',action='store_true')
    args=parser.parse_args()
    env=verified_environment();env['CAMMY_REACHY_WEBRTC']='1'
    status=request('/api/daemon/status')
    if (status.get('robot_name')!='cammy-reference-simulator' or not status.get('mockup_sim_enabled')
            or not status.get('no_media') or status.get('version')!='1.14.0'):
        raise RuntimeError('Port8000 is not the pinned managed reference')
    app=request('/api/apps/current-app-status')
    if not args.restart:
        print(json.dumps({'reference':status['robot_name'],'app':app,
                          'note':'Use --restart to activate the prepared profile'}));return
    if app is not None and app.get('state')!='stopped':
        if app.get('info',{}).get('name')!='cammy_reachy_app':raise RuntimeError('Another app is active')
        request('/api/apps/stop-current-app',post=True)
        deadline=time.monotonic()+8
        while request('/api/apps/current-app-status') is not None:
            if time.monotonic()>deadline:raise RuntimeError('Cammy app did not stop')
            time.sleep(.1)
    record=json.loads((REFERENCE/'daemon-process.json').read_text())
    pid=record['pid'];fd=os.pidfd_open(pid)
    try:
        argv=(Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')
        if (b'reachy_mini.daemon.app.main' not in argv or b'cammy-reference-simulator' not in argv
                or b'--mockup-sim' not in argv or b'--no-media' not in argv):
            raise RuntimeError('Managed daemon identity changed')
        signal.pidfd_send_signal(fd,signal.SIGTERM)
        selector=selectors.DefaultSelector()
        try:
            selector.register(fd,selectors.EVENT_READ)
            if not selector.select(8):raise RuntimeError('Reference did not exit')
        finally:selector.close()
    finally:os.close(fd)
    subprocess.run(['python3',str(REFERENCE/'launch_reference.py'),'--daemon-only'],env=env,check=True)
    request('/api/apps/start-app/cammy_reachy_app',post=True)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try:
            with OPENER.open('http://127.0.0.1:8801/status',timeout=1) as response:value=json.load(response)
            if value.get('webrtc_configured'):
                print(json.dumps({'app':'cammy_reachy_app','running':True,'webrtc_configured':True,
                                  'sensing_started':False,'reference_body_simulated':True}));return
        except OSError:pass
        time.sleep(.1)
    raise RuntimeError('App WebRTC profile did not start')


if __name__=='__main__':main()
