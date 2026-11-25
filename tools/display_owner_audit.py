#!/usr/bin/env python3
"""Read-only exact-stock checks for a proposed GUI-owned face integration.

Checks specific instructions and direct branches, not runtime thread ownership,
LVGL object layouts, callbacks, cleanup, display visibility or hardware safety.
No process is run, patched or contacted; saved executable is read as data only.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
STOCK=ROOT.parent/'Cammy Bot/M105 investigation/work/firmware-rootfs/usr/bin/sdvcam'
SHA='8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec'


def audit(path=STOCK):
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SHA:
        raise ValueError('Exact saved stock executable required')
    phoff=struct.unpack_from('<I',raw,28)[0]
    size,count=struct.unpack_from('<HH',raw,42)
    loads=[]
    for index in range(count):
        kind,offset,address,_,length=struct.unpack_from('<IIIII',raw,phoff+index*size)
        if kind==1: loads.append((offset,address,length))
    def word(address):
        for offset,start,length in loads:
            if start<=address and address+4<=start+length:
                return struct.unpack_from('<I',raw,offset+address-start)[0]
        raise ValueError('Address outside file-backed segments')
    def branch(site,target,linked=True):
        value=word(site)
        distance=value&0xffffff
        if distance&0x800000: distance-=0x1000000
        return (value>>24== (0xeb if linked else 0xea)
                and (site+8+4*distance)&0xffffffff==target)
    rows=[]
    def check(label,ok): rows.append({'claim':label,'pass':bool(ok)})
    check('Loop preserves WindowManager this in r5 at0xe5838',word(0xe5838)==0xe1a05000)
    check('GUI mutex address this+0x24 at0xe58b0',word(0xe58b0)==0xe2857024)
    check('GUI mutex wrapper call at0xe58bc',branch(0xe58bc,0x37298))
    check('Wrapper locks pthread mutex at0x3729c',branch(0x3729c,0x2ce30))
    check('Periodic GUI handler called at0xe58c0',branch(0xe58c0,0x4a7950))
    check('Message virtual handler invoked via r8 at0xe591c',word(0xe591c)==0xe12fff38)
    check('GUI unlock wrapper at0xe5928',branch(0xe5928,0xe3e3c))
    check('Unlock wrapper tail-calls pthread_mutex_unlock',branch(0xe3e3c,0x2ca94,False))
    check('Loop sleeps2000us at0xe592c/0xe5930',word(0xe592c)==0xe3a00e7d and branch(0xe5930,0x2d2c8))
    check('32bit row copy in sunxifb_flush at0x4b0b10',branch(0x4b0b10,0x2d2a4))
    check('Flush passes ioctl command0x4630 at0x4b09fc',word(0x4b09fc)==0xe3041630 and branch(0x4b0a24,0x2c800))
    check('Flush passes FBIOPAN_DISPLAY0x4606 at0x4b0a54',word(0x4b0a54)==0xe3041606 and branch(0x4b0a60,0x2c800))
    return {'source_sha256':SHA,'evidence_class':'offline-static','checks':rows,
            'pass':all(row['pass'] for row in rows),'native_face_backend_available':False,
            'lcd_verified':False}


if __name__=='__main__':
    result=audit()
    print(json.dumps(result,indent=2))
    sys.exit(0 if result['pass'] else 1)
