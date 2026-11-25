#!/usr/bin/env python3
"""Run/validate the forked CammyOS root in camera RAM; never flashes or reboots."""
import argparse
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
CAMERA=ROOT.parent/'Cammy Bot'
ADB=Path(os.environ.get('CAMMY_ADB',str(CAMERA/'tools/adb-camera.sh')))
sys.path.insert(0,str(ROOT/'sdk'))
from cammy import Cammy, CammyError
REMOTE='/tmp/cammyos-ramtest'
TOKEN_REMOTE='/tmp/cammyos-ramtest.token'
PID_REMOTE='/tmp/cammyos-ramtest.pid'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-existing',action='store_true',help='Validate the existing known RAM deployment without redeploying')
    args=parser.parse_args()
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    evidence=ROOT/'evidence';evidence.mkdir(exist_ok=True)
    with (evidence/'ram-validation.log' if args.verify_existing else evidence/'ram-deploy.log').open('w') as log:
        def adb(*params,timeout=180,capture=False):
            return subprocess.run([str(ADB),'-s','192.168.10.1:5555',*params],check=True,timeout=timeout,stdout=subprocess.PIPE if capture else log,stderr=log,text=capture)
        private=ROOT/'private';private.mkdir(exist_ok=True);private.chmod(0o700)
        tokenfile=private/'ram-test.token'
        boot_before=adb('shell','cat /proc/sys/kernel/random/boot_id',capture=True).stdout.strip()
        if not args.verify_existing:
            ready=adb('shell',f'grep "^tmpfs /tmp tmpfs " /proc/mounts >/dev/null && test ! -e {REMOTE} && echo RAMTEST_READY',capture=True).stdout
            if 'RAMTEST_READY' not in ready:raise RuntimeError('No verified fresh tmpfs deployment path; inspect/stop the previous test')
            tokenfile.write_text(secrets.token_hex(32)+'\n');tokenfile.chmod(0o600)
            adb('shell','mkdir -p /tmp/cammyos-tools')
            adb('push',str(ROOT/'build/arm/busybox'),'/tmp/cammyos-tools/busybox')
            adb('push',str(ROOT/'dist/cammyos-rootfs-ramtest.tar.gz'),'/tmp/cammyos-rootfs-ramtest.tar.gz')
            adb('push',str(tokenfile),TOKEN_REMOTE)
            command=f'mkdir {REMOTE} && /tmp/cammyos-tools/busybox tar -xzf /tmp/cammyos-rootfs-ramtest.tar.gz -C {REMOTE} && chmod 600 {TOKEN_REMOTE} && {REMOTE}/usr/bin/cammy-portal --root {REMOTE} --token-file {TOKEN_REMOTE} --pid-file {PID_REMOTE} --bind 0.0.0.0 --port 8787 --stock-preview --daemonize'
            launched=adb('shell',command+' && echo CAMMY_LAUNCHED',capture=True).stdout
            if 'CAMMY_LAUNCHED' not in launched:raise RuntimeError('RAM launch failed: '+launched)
        client=Cammy('http://192.168.10.1:8787',tokenfile.read_text().strip(),'ram-validation')
        deadline=time.monotonic()+8
        while True:
            try:identity=client.identity();break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        if not identity['image_marker_present']:raise RuntimeError('Service is not seeing the new rootfs marker')
        result=dict(timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),test='real ARM hardware chroot in camera tmpfs; not a cold boot',identity=identity,capabilities=client.capabilities(),state=client.state(),flash_written=False,kernel_replaced=False,portal_running=True)
        proof=adb('shell',f'pid=$(cat {PID_REMOTE}); readlink /proc/$pid/root; cat /proc/$pid/root/etc/cammy-release; cat /proc/$pid/status | grep Uid; cat /proc/sys/kernel/random/boot_id',capture=True).stdout
        (evidence/'ram-process-proof.txt').write_text(proof)
        if REMOTE not in proof:raise RuntimeError('Process root does not match RAM test filesystem')
        client.stop();client.acquire()
        other=Cammy(client.endpoint,client.token,'second-agent')
        try:other.acquire()
        except CammyError as exc:
            if exc.status!=409:raise
            result['two_agent_exclusion_verified']=True
        else:raise RuntimeError('Second agent improperly acquired control')
        client.release();other.acquire();other.release()
        with client:
            result['preview_start']=client.preview_start()
            proc=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','warning','-y','-rtsp_transport','tcp','-timeout','5000000','-analyzeduration','3000000','-probesize','1000000','-i','rtsp://192.168.10.1:8554/ch00','-map','0:v:0','-frames:v','1','-update','1',str(evidence/'portal-preview.jpg')],capture_output=True,timeout=25)
            (evidence/'portal-preview.log').write_bytes(proc.stderr)
            result['preview_ffmpeg_exit']=proc.returncode
            frame=evidence/'portal-preview.jpg'
            result['preview_frame_bytes']=frame.stat().st_size if frame.exists() else 0
            if proc.returncode or not result['preview_frame_bytes']:raise RuntimeError('Preview frame verification failed')
            client.preview_stop()
        result['final_state']=client.state()
        result['same_kernel_boot_id']=boot_before==adb('shell','cat /proc/sys/kernel/random/boot_id',capture=True).stdout.strip()
        if not result['same_kernel_boot_id']:raise RuntimeError('Unexpected device reboot during RAM test')
        (evidence/'ram-test.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({k:result[k] for k in ('test','identity','two_agent_exclusion_verified','preview_ffmpeg_exit','preview_frame_bytes','final_state','same_kernel_boot_id','flash_written','portal_running')},indent=2))
if __name__=='__main__':main()
