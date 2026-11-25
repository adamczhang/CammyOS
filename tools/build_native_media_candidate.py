#!/usr/bin/env python3
"""Build an OFFLINE exact-M105 media-lifecycle candidate, never deploy it.

Original vendor bytes stay private. No device/process access or launch operation.
SPDX-License-Identifier: MIT
"""
import os
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = '8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec'
LOAD_HEADER = 148
TEXT_BASE = 0x10000
ORIGINAL_LOAD_SIZE = 0x8D2E1C
TRAMPOLINE_OFFSET = 0x8D2E20
TRAMPOLINE_ADDRESS = TEXT_BASE + TRAMPOLINE_OFFSET
RESUME = 0xAB9E4
CONNECT_TAIL = 0xAC5D0
DISCONNECT_TAIL = 0xAC638
MEDIA_INIT_BODY = 0xAC46C  # After the AP log; requires only the existing r4=this.
MEDIA_DESTROY = 0xADD60


def branch(source, target):
    delta = target - source - 8
    if source % 4 or target % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise ValueError('Unaligned/out-of-range ARM branch')
    return 0xEA000000 | ((delta // 4) & 0xFFFFFF)


def branch_target(source, instruction):
    if instruction & 0xFF000000 != 0xEA000000:
        raise ValueError('Expected unconditional ARM B')
    offset = instruction & 0xFFFFFF
    if offset & 0x800000: offset -= 0x1000000
    return source + 8 + offset * 4


def patch_image(original):
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA256:
        raise ValueError('Exact preserved M105 firmware hash required')
    if original[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<HH', original, 16) != (2, 40):
        raise ValueError('Expected ARM32 little-endian ET_EXEC')
    header = struct.unpack_from('<8I', original, LOAD_HEADER)
    if header != (1, 0, TEXT_BASE, TEXT_BASE, ORIGINAL_LOAD_SIZE, ORIGINAL_LOAD_SIZE, 5, 0x10000):
        raise ValueError('Executable LOAD layout differs')
    if any(original[ORIGINAL_LOAD_SIZE:0x8D3030]):
        raise ValueError('Expected unallocated zero padding before next LOAD')
    for address in [CONNECT_TAIL, DISCONNECT_TAIL]:
        word = struct.unpack_from('<I', original, address - TEXT_BASE)[0]
        if word != branch(address, RESUME):
            raise ValueError('Native callback tail differs')
    # No new stack frame: destructor returns directly to the native continuation.
    # Its saved context remains the presenter's; native unwinding is untested.
    trampoline = struct.pack('<6I',
        0xE3A00000,  # mov r0, #0
        0xE5C40124,  # strb r0, [r4, #0x124] -- clear media-started flag
        0xE2840004,  # add r0, r4, #4 -- embedded RTSP controller
        0xE59FE000,  # ldr lr, [pc] -- literal at +20
        branch(TRAMPOLINE_ADDRESS + 16, MEDIA_DESTROY),
        RESUME)
    patched = bytearray(original)
    for address, target in [(CONNECT_TAIL, MEDIA_INIT_BODY), (DISCONNECT_TAIL, TRAMPOLINE_ADDRESS)]:
        struct.pack_into('<I', patched, address - TEXT_BASE, branch(address, target))
    patched[TRAMPOLINE_OFFSET:TRAMPOLINE_OFFSET+len(trampoline)] = trampoline
    extended = TRAMPOLINE_OFFSET + len(trampoline)
    # Extend the existing RX LOAD into its zero padding; no segment relocation,
    # new mapping page, section rewrite, entry-point or writable-segment change.
    struct.pack_into('<II', patched, LOAD_HEADER + 16, extended, extended)
    allowed = [(CONNECT_TAIL-TEXT_BASE, 4), (DISCONNECT_TAIL-TEXT_BASE, 4),
               (TRAMPOLINE_OFFSET, len(trampoline)), (LOAD_HEADER+16, 8)]
    assert len(patched) == len(original)
    assert all(any(start <= i < start+size for start, size in allowed)
               for i, (a,b) in enumerate(zip(original, patched)) if a != b)
    return bytes(patched), {'experimental': True, 'deployed': False,
        'source_sha256': SOURCE_SHA256, 'candidate_sha256': hashlib.sha256(patched).hexdigest(),
        'allowed_modified_ranges': [{'offset':hex(start),'bytes':size} for start,size in allowed],
        'connect':{'from':hex(CONNECT_TAIL),'to':hex(MEDIA_INIT_BODY)},
        'disconnect':{'from':hex(DISCONNECT_TAIL),'to':hex(TRAMPOLINE_ADDRESS)},
        'trampoline':{'address':hex(TRAMPOLINE_ADDRESS),'bytes':len(trampoline),
                      'destroy':hex(MEDIA_DESTROY),'resume':hex(RESUME)},
        'scope':'Offline vendor-copy experiment; requires separate launch/ownership/rollback design and hardware validation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'private/native-start/sdvcam-media-candidate')
    args = parser.parse_args()
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    output = args.output.resolve()
    if not output.is_relative_to((ROOT/'private').resolve()) or output == args.source.resolve():
        parser.error('Output must be a new file under this repository private/')
    if output.suffix == '.json':
        parser.error('Candidate output and JSON report must be different files')
    if output.exists() or output.with_suffix('.json').exists():
        parser.error('Refusing existing candidate/report')
    patched, report = patch_image(args.source.read_bytes())
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with output.open('xb') as f: f.write(patched)
    output.chmod(0o600)  # Deliberately not launchable by this builder.
    output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'output':str(output),'candidate_sha256':report['candidate_sha256'],'deployed':False}))


if __name__ == '__main__': main()
