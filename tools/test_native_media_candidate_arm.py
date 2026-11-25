#!/usr/bin/env python3
"""Local ARM branch/ABI fixture, NOT a boot of the vendor app or camera hardware.

Copies the exact patched presenter slice into a synthetic executable with inert
stubs for every reached external call. No sensor/media library or device is used.
SPDX-License-Identifier: MIT
"""
import os
import argparse
import json
from pathlib import Path
import shutil
import resource
import struct
import subprocess
from build_native_media_candidate import patch_image, TEXT_BASE, TRAMPOLINE_OFFSET, TRAMPOLINE_ADDRESS

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT.parent/'Cammy Bot/M105 investigation/work/firmware-rootfs/usr/bin/sdvcam')
    args=parser.parse_args()
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    compiler=ROOT/'build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc'
    qemu=shutil.which('qemu-arm') or str(ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm')
    if not compiler.is_file() or not Path(qemu).is_file():parser.error('Prepared local ARM compiler/qemu required; no install/download')
    data,report=patch_image(args.source.read_bytes())
    out=ROOT/'build/native-media-fixture';out.mkdir(parents=True,exist_ok=True)
    start,end=0xAB934,0xAD09C
    handler=bytearray(data[start-TEXT_BASE:end-TEXT_BASE])
    # Fixture continuation restores its synthetic caller frame, not native state.
    for exit_address in [0xAB9E4,0xABB50]:
        struct.pack_into('<II',handler,exit_address-start,0xE28DD080,0xE8BD9FF0)
    (out/'handler.bin').write_bytes(handler)
    (out/'cleanup.bin').write_bytes(data[TRAMPOLINE_OFFSET:TRAMPOLINE_OFFSET+24])
    (out/'fixture.c').write_text(r'''
#include <stdint.h>
#include <string.h>
uintptr_t recorder_result, destroyed_object, sender_recorder, presenter_mode;
unsigned created, started, sent, destroyed;
unsigned char presenter[0x200];
extern void connect_tail(void *), disconnect_tail(void *);
static void reset(void) {
 memset(presenter,0,sizeof(presenter));
 *(uintptr_t *)(presenter+0xb0)=1;recorder_result=0x12345678;
 created=started=sent=destroyed=0;destroyed_object=sender_recorder=presenter_mode=0;
}
int main(void) {
 reset();connect_tail(presenter);
 if(created!=1 || started!=1 || sent!=1 || presenter[0x124]!=1 || sender_recorder!=recorder_result)return 1;
 connect_tail(presenter);if(created!=1 || started!=1 || sent!=1)return 2;
 disconnect_tail(presenter);
 if(destroyed!=1 || destroyed_object!=(uintptr_t)(presenter+4) || presenter[0x124]!=0)return 3;
 connect_tail(presenter);if(created!=2 || started!=2 || sent!=2)return 4;
 reset();*(uintptr_t *)(presenter+0xb0)=0;connect_tail(presenter);
 if(created || started || sent || presenter[0x124])return 5;
 reset();recorder_result=0;connect_tail(presenter);
 if(created || started || sent || presenter[0x124])return 6;
 return 0;
}
''')
    assembly=r'''
.syntax unified
.arm
.section .text
.global connect_tail,disconnect_tail
connect_tail:
 push {r4-r11,ip,lr}
 sub sp,sp,#128
 mov r4,r0
 b 0xAC5D0
 disconnect_tail:
 push {r4-r11,ip,lr}
 sub sp,sp,#128
 mov r4,r0
 b 0xAC638
.section .native_handler,"ax",%progbits
.incbin "HANDLER"
.section .native_cleanup,"ax",%progbits
.incbin "CLEANUP"
.section .stub_recorder,"ax",%progbits
 ldr r2,=presenter_mode
 str r0,[r2]
 ldr r0,=recorder_result
 ldr r0,[r0]
 bx lr
 .ltorg
.section .stub_log,"ax",%progbits
 mov r0,#0
 bx lr
.section .stub_sleep,"ax",%progbits
 bx lr
.section .stub_create,"ax",%progbits
 ldr r2,=created
 ldr r3,[r2]
 add r3,r3,#1
 str r3,[r2]
 mov r0,#0
 bx lr
 .ltorg
.section .stub_start,"ax",%progbits
 ldr r2,=started
 ldr r3,[r2]
 add r3,r3,#1
 str r3,[r2]
 mov r0,#0
 bx lr
 .ltorg
.section .stub_sender,"ax",%progbits
 ldr r2,=sender_recorder
 str r1,[r2]
 ldr r2,=sent
 ldr r3,[r2]
 add r3,r3,#1
 str r3,[r2]
 bx lr
 .ltorg
.section .stub_destroy,"ax",%progbits
 ldr r2,=destroyed_object
 str r0,[r2]
 ldr r2,=destroyed
 ldr r3,[r2]
 add r3,r3,#1
 str r3,[r2]
 bx lr
 .ltorg
.section .note.GNU-stack,"",%progbits
'''.replace('HANDLER',str(out/'handler.bin')).replace('CLEANUP',str(out/'cleanup.bin'))
    (out/'fixture.S').write_text(assembly)
    sections={'.native_handler':start,'.native_cleanup':TRAMPOLINE_ADDRESS,
        '.stub_recorder':0x508C4,'.stub_log':0x489FE0,'.stub_sleep':0x2D2C8,
        '.stub_create':0xADB90,'.stub_start':0xAD90C,'.stub_sender':0xAE210,'.stub_destroy':0xADD60}
    command=[str(compiler),'-O1','-static','-march=armv7-a','-mfpu=neon-vfpv4','-mfloat-abi=hard',
             '-Wall','-Wextra','-Werror',str(out/'fixture.c'),str(out/'fixture.S'),'-o',str(out/'fixture')]
    command += [f'-Wl,--section-start={name}={address:#x}' for name,address in sections.items()]
    subprocess.run(command,check=True,capture_output=True,timeout=20)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    result=subprocess.run([qemu,'-cpu','cortex-a7',str(out/'fixture')],capture_output=True,timeout=5)
    if result.returncode:
        raise SystemExit('ARM fixture failed: returncode='+str(result.returncode)+' '+result.stderr.decode(errors='replace'))
    summary={'candidate_sha256':report['candidate_sha256'],'local_arm_fixture':'passed',
        'cases':['create/start/sender flow','already-started guard','clear flag/correct destroy object/continuation',
                 'reconnect after cleanup','null work mode','null recorder'],
        'hardware_validated':False,'camera_or_vendor_app_launched':False}
    report_path = ROOT/'private/native-start/arm-fixture-result.json'
    report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_path.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':main()
