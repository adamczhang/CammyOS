#!/usr/bin/env python3
"""Prepare a non-flashing stock-kernel RAM boot with our read-only debug startup.

The kernel does not expose clear initramfs support, so the first experiment uses
the original internal SquashFS read-only and a RAM-supplied PID1 command. It never
starts stock PID1/rcS, mounts the writable overlay, or calls an updater.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT.parent / 'Cammy Bot/tools/build-deps'
OUT = ROOT / 'build/fel'


def main():
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')], check=True,
                   stdout=subprocess.DEVNULL)
    OUT.mkdir(exist_ok=True)
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = str(TOOLS / 'usr/lib/x86_64-linux-gnu')
    fdtput = TOOLS / 'usr/bin/fdtput'
    source = ROOT / 'private/board/stock-running.dtb'
    dtb = OUT / 'm105-debug.dtb'
    shutil.copyfile(source, dtb)
    # Avoid SMC firmware calls: no OP-TEE/PSCI monitor is installed by this route.
    for node in ['/firmware/optee', '/e907_rproc@0',
                 '/soc@03000000/disp@05000000']:
        subprocess.run([str(fdtput), '-t', 's', str(dtb), node, 'status', 'disabled'],
                       env=env, check=True)
    # Linux4.9's early psci_dt_init uses of_find_matching_node_and_match,
    # which can match disabled nodes. Remove its compatible declaration.
    subprocess.run([str(fdtput), '-d', str(dtb), '/psci', 'compatible'],
                   env=env, check=True)
    for prop in ['cpu-idle-states', 'enable-method']:
        subprocess.run([str(fdtput), '-d', str(dtb), '/cpus/cpu@0', prop],
                       env=env, check=True)
    for prop in ['linux,initrd-start', 'linux,initrd-end']:
        subprocess.run([str(fdtput), '-d', str(dtb), '/chosen', prop],
                       env=env, check=True)
    commands = [
        'export PATH=/sbin:/usr/sbin:/bin:/usr/bin',
        'mount -t devtmpfs none /dev',
        'mount -t proc proc /proc',
        'mount -t sysfs sysfs /sys',
        'mount -t tmpfs tmpfs /tmp',
        'exec >/tmp/cammy-boot.log 2>&1',
        'mkdir /dev/pts',
        'mount -t devpts devpts /dev/pts',
        "printf '#!/bin/sh\\nexec /bin/sh \\042$@\\042\\n' >/tmp/adb_shell",
        'chmod 755 /tmp/adb_shell',
        'mount -o bind /tmp/adb_shell /bin/adb_shell',
        'echo CAMMY_RAM_KERNEL_BOOT >/tmp/cammy-boot-marker',
        '/usr/bin/setusbconfig adb',
        '/bin/adbd & while true; do sleep 5; done',
    ]
    script = ';'.join(commands)
    assert '"' not in script
    args = ('root=/dev/mtdblock3 ro rootwait init=/bin/sh console=ttyS0,115200 '
            'loglevel=7 panic=10 cpuidle.off=1 boot_type=3 '
            'androidboot.hardware=sun8iw21p1 -- -c "' + script + '"')
    assert len(args.encode()) < 1024, len(args)
    subprocess.run([str(fdtput), '-t', 's', str(dtb), '/chosen', 'bootargs', args],
                   env=env, check=True)
    (OUT / 'debug-startup.sh').write_text(script + '\n')
    subprocess.run(['sh', '-n', str(OUT / 'debug-startup.sh')], check=True)
    shutil.copyfile(ROOT / 'private/board/stock-zImage', OUT / 'zImage')
    prefix = str(ROOT / 'build/toolchain/bin/arm-buildroot-linux-musleabihf-')
    subprocess.run([prefix + 'gcc', '-nostdlib', '-static', '-march=armv7-a',
                    '-marm', '-Wl,-Ttext=0x43f00000', '-Wl,--build-id=none',
                    str(ROOT / 'src/boot/arm_launch.S'), '-o', str(OUT / 'arm-launch.elf')], check=True)
    subprocess.run([prefix + 'objcopy', '-O', 'binary', str(OUT / 'arm-launch.elf'),
                    str(OUT / 'arm-launch.bin')], check=True)
    images = []
    for name, address in [('arm-launch.bin', 0x43f00000), ('zImage', 0x44000000),
                          ('m105-debug.dtb', 0x4a000000)]:
        data = (OUT / name).read_bytes()
        images.append(dict(file=name, address=hex(address), length=len(data),
                           sha256=hashlib.sha256(data).hexdigest()))
    manifest = dict(test='FEL RAM-loaded stock kernel, custom debug PID1; original rootfs read-only',
                    bootargs_bytes=len(args), images=images, entry='0x43f00000',
                    initramfs_used=False, flash_written=False, boot_verified=False)
    (OUT / 'ram-boot-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
