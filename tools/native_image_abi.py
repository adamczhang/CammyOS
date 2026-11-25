#!/usr/bin/env python3
"""Bounded offline emulation of exact-stock image primitives, never an OS launch.

Only the classifier, metadata decoder, variable-image open and pixel-size helper
may execute in Unicorn. No system calls, filesystem/driver code, native app init,
display access or code patching. Vendor bytes stay in ignored inputs/build RAM.
"""
import hashlib
import json
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[1]
STOCK=ROOT.parent/'Cammy Bot/M105 investigation/work/firmware-rootfs/usr/bin/sdvcam'
STOCK_SHA='8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec'
RAM=0x02000000
SOURCE=RAM+0x100
OUTPUT=RAM+0x200
DECODER=RAM+0x300
PIXELS=RAM+0x1000
STOP=RAM+0x1f0000
ALLOWED=((0x498a08,0x498ab4),(0x49c2c8,0x49c3dc),(0x49c8a0,0x49cb1c))


class NativeImageABI:
    def __init__(self,path=STOCK):
        self.raw=Path(path).read_bytes()
        if hashlib.sha256(self.raw).hexdigest()!=STOCK_SHA:
            raise ValueError('Exact stock executable required')
        phoff=struct.unpack_from('<I',self.raw,28)[0]
        size,count=struct.unpack_from('<HH',self.raw,42)
        self.loads=[]
        for index in range(count):
            kind,offset,address,_,length=struct.unpack_from('<IIIII',self.raw,phoff+index*size)
            if kind==1:self.loads.append((offset,address,length))

    def call(self,address,source=b'',descriptor=None,argument=0):
        from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_INTR
        from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
        if address not in (0x498a08,0x498a7c,0x49c2c8,0x49c8a0):
            raise ValueError('Only audited pure image primitives are allowed')
        if len(source)>64 or descriptor is not None and len(descriptor)!=64:
            raise ValueError('Only bounded synthetic descriptors are allowed')
        cpu=Uc(UC_ARCH_ARM,UC_MODE_ARM)
        first=min(address for _,address,_ in self.loads)&~4095
        end=max(address+size for _,address,size in self.loads)
        cpu.mem_map(first,(end-first+4095)&~4095)
        for offset,start,length in self.loads:cpu.mem_write(start,self.raw[offset:offset+length])
        cpu.mem_map(RAM,0x200000)
        if source:cpu.mem_write(SOURCE,bytes(source))
        cpu.mem_write(PIXELS,b'\x11\x22\x33\xff'*8)
        if descriptor is not None:cpu.mem_write(DECODER,bytes(descriptor))
        cpu.reg_write(UC_ARM_REG_R0,argument)
        cpu.reg_write(UC_ARM_REG_R1,SOURCE if descriptor is None else DECODER)
        cpu.reg_write(UC_ARM_REG_R2,OUTPUT)
        cpu.reg_write(UC_ARM_REG_SP,RAM+0x100000)
        cpu.reg_write(UC_ARM_REG_LR,STOP)
        count=[0]
        def instruction(engine,pc,size,unused):
            if pc==STOP:engine.emu_stop();return
            if not any(lo<=pc<hi for lo,hi in ALLOWED):
                raise RuntimeError('Unapproved code target '+hex(pc))
            count[0]+=1
        def interrupt(*args):raise RuntimeError('Syscall/interrupt forbidden')
        cpu.hook_add(UC_HOOK_CODE,instruction)
        cpu.hook_add(UC_HOOK_INTR,interrupt)
        cpu.emu_start(address,STOP+4,timeout=50000,count=1000)
        if cpu.reg_read(UC_ARM_REG_PC)!=STOP:raise RuntimeError('Bounded primitive did not return')
        return {'return':cpu.reg_read(UC_ARM_REG_R0),
                'header':list(struct.unpack('<III',cpu.mem_read(OUTPUT,12))),
                'decoder_pixel_pointer':struct.unpack('<I',cpu.mem_read(DECODER+32,4))[0],
                'instructions':count[0]}

    def image_info(self,source):return self.call(0x49c2c8,source)

    def variable_open(self,width=356,height=400,data_address=PIXELS):
        # +0xc is conventionally data_size; its semantics are NOT proven by
        # these primitives. The pointer at+0x10 and header are observed directly.
        source=struct.pack('<IIIII',4,width,height,width*height*4,data_address)
        decoder=bytearray(64)
        struct.pack_into('<I',decoder,4,SOURCE)
        decoder[16]=0  # variable source
        struct.pack_into('<III',decoder,20,4,width,height)
        return self.call(0x49c8a0,source,decoder)


def bgra_preview(rgb,width=356,height=400):
    if type(width) is not int or type(height) is not int or not 1<=width<=356 or not 1<=height<=400:
        raise ValueError('Bounded panel-sized pixels required')
    if not isinstance(rgb,bytes) or len(rgb)!=width*height*3:
        raise ValueError('Exact RGB888 source required')
    out=bytearray(width*height*4)
    out[0::4]=rgb[2::3];out[1::4]=rgb[1::3];out[2::4]=rgb[0::3];out[3::4]=b'\xff'*(width*height)
    return bytes(out)


def audit():
    abi=NativeImageABI()
    source=struct.pack('<IIIII',4,356,400,356*400*4,PIXELS)
    native=abi.image_info(source)
    standard=abi.image_info(struct.pack('<III',4|(356<<10)|(400<<21),356*400*4,PIXELS))
    opened=abi.variable_open()
    null=abi.variable_open(data_address=0)
    pixel_size=abi.call(0x498a08,argument=4)
    checks={'native_header_is_12_bytes':native['return']==1 and native['header']==[4,356,400],
            'upstream_4byte_header_is_misinterpreted':standard['return']==1 and standard['header']!=[4,356,400],
            'variable_pointer_at_16_becomes_decoder_data_at_32':opened['return']==1 and opened['decoder_pixel_pointer']==PIXELS,
            'null_variable_pixels_refused':null['return']==0,
            'true_color_is_32_bits':pixel_size['return']==32}
    return {'source_sha256':STOCK_SHA,'evidence_class':'OFFLINE_EXACT_NATIVE_PRIMITIVE_EMULATION',
            'pass':all(checks.values()),'checks':checks,'native_header':native,'standard_header':standard,
            'variable_open':opened,'null_pixels':null,'pixel_size':pixel_size,
            'data_size_offset_12_semantics_verified':False,'full_LVGL_ABI_verified':False,
            'native_app_executed':False,'native_primitives_emulated':True,'display_written':False}


if __name__=='__main__':
    result=audit();print(json.dumps(result,indent=2));raise SystemExit(0 if result['pass'] else 1)
