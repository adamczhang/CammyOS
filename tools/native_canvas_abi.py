"""Bounded exact-native CANVAS primitives with explicit synthetic dependencies.

Never starts sdvcam, GUI loop, allocator, driver or an OS. Unapproved code and
interrupts trap. Constructor/create/set-buffer/delete-callback registration are
emulated; complete LVGL draw/destruction and hardware lifetime are NOT proved.
"""
import struct
from tools.native_image_abi import NativeImageABI,STOCK_SHA,RAM,STOP,PIXELS
OBJECT=RAM+0x400
PARENT=RAM+0x800
DISPLAY=RAM+0x900
SPEC=RAM+0xa00
EVENTS=RAM+0xb00
CALLBACK=RAM+0xc00
RANGES=((0x4aa9e8,0x4aaa3c),(0x4aaa3c,0x4aaa74),(0x4aaa7c,0x4aaac0),
        (0x49c268,0x49c26c),(0x48aad0,0x48aaf4),(0x48ab5c,0x48ab80),
        (0x496a6c,0x496aec),(0x4969bc,0x4969c4))
HOOKS={0x48f940:'class_create',0x48fb40:'class_init',0x4abefc:'image_set_src',
       0x4a3280:'default_display',0x48f7d8:'allocate_spec',0x4a6aa4:'realloc'}
ENTRIES={0x4aa9f0,0x4aa9e8,0x4aaa3c,0x4aaa7c,0x48aad0,0x48ab5c,0x496a6c,0x4969bc}


class NativeCanvasABI(NativeImageABI):
    def primitive(self,entry,args=(),default_display=True):
        from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_INTR,UC_HOOK_MEM_WRITE
        from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
        if entry not in ENTRIES or len(args)>5:raise ValueError('Audited canvas entrypoint required')
        cpu=Uc(UC_ARCH_ARM,UC_MODE_ARM)
        lo=min(a for _,a,_ in self.loads)&~4095;hi=max(a+n for _,a,n in self.loads)
        cpu.mem_map(lo,(hi-lo+4095)&~4095)
        for off,addr,n in self.loads:cpu.mem_write(addr,self.raw[off:off+n])
        cpu.mem_map(RAM,0x200000)
        regs=(UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)
        sp=RAM+0x100000
        for reg,value in zip(regs,args):cpu.reg_write(reg,value)
        if len(args)==5:cpu.mem_write(sp,struct.pack('<I',args[4]))
        cpu.reg_write(UC_ARM_REG_SP,sp);cpu.reg_write(UC_ARM_REG_LR,STOP)
        cpu.mem_write(DISPLAY+16,struct.pack('<I',PARENT))
        cpu.mem_write(DISPLAY+28,struct.pack('<I',PARENT))
        calls=[];instructions=[0]
        def code(engine,pc,n,unused):
            if pc==STOP:engine.emu_stop();return
            if pc in HOOKS:
                values=[engine.reg_read(r) for r in regs]
                if engine.reg_read(UC_ARM_REG_SP)%8:raise RuntimeError('Unaligned native call')
                name=HOOKS[pc];calls.append({'name':name,'args':values})
                result=0
                if name=='class_create':result=OBJECT
                elif name=='default_display':result=DISPLAY if default_display else 0
                elif name=='allocate_spec':engine.mem_write(OBJECT+8,struct.pack('<I',SPEC))
                elif name=='realloc':result=EVENTS
                engine.reg_write(UC_ARM_REG_R0,result)
                engine.reg_write(UC_ARM_REG_PC,engine.reg_read(UC_ARM_REG_LR));return
            if not any(a<=pc<b for a,b in RANGES):raise RuntimeError('Unapproved native target '+hex(pc))
            instructions[0]+=1
        def interrupt(*args):raise RuntimeError('Interrupt forbidden')
        def write(engine,access,address,size,value,unused):
            if not RAM<=address<address+size<=RAM+0x200000:raise RuntimeError('Native write outside synthetic RAM')
        cpu.hook_add(UC_HOOK_CODE,code);cpu.hook_add(UC_HOOK_INTR,interrupt);cpu.hook_add(UC_HOOK_MEM_WRITE,write)
        cpu.emu_start(entry,STOP+4,timeout=50000,count=1000)
        if cpu.reg_read(UC_ARM_REG_PC)!=STOP:raise RuntimeError('Native primitive did not return within budget')
        return {'return':cpu.reg_read(UC_ARM_REG_R0),'calls':calls,'instructions':instructions[0],
                'object':bytes(cpu.mem_read(OBJECT,104)),'spec':bytes(cpu.mem_read(SPEC,40)),
                'events':bytes(cpu.mem_read(EVENTS,12))}


def audit():
    abi=NativeCanvasABI()
    ctor=abi.primitive(0x4aa9f0,(0,OBJECT))
    created=abi.primitive(0x4aaa3c,(PARENT,))
    installed=abi.primitive(0x4aaa7c,(OBJECT,PIXELS,356,400,4))
    destroyed=abi.primitive(0x4aa9e8,(0,OBJECT))
    registered=abi.primitive(0x496a6c,(OBJECT,CALLBACK,33,0))
    top=abi.primitive(0x48ab5c,(0,))
    active=abi.primitive(0x48aad0,(0,))
    empty=abi.primitive(0x48ab5c,(0,),False)
    # Address-backed delete code in the exact file; not execution of full delete.
    def word(va):
        for off,a,n in abi.loads:
            if a<=va< a+n:return struct.unpack_from('<I',abi.raw,off+va-a)[0]
        raise ValueError('Unmapped address')
    checks={
        'canvas_create_class_parent_and_init':created['return']==OBJECT and
            [(c['name'],c['args'][:2]) for c in created['calls']]==[
                ('class_create',[0x8fad00,PARENT]),('class_init',[OBJECT,PARENT])],
        'constructor_embedded_descriptor':struct.unpack_from('<IIIII',ctor['object'],84)==(4,0,0,0,0),
        'constructor_uses_embedded_source':ctor['calls'][0]['args'][:2]==[OBJECT,OBJECT+84],
        'buffer_uses_native_padded_descriptor':struct.unpack_from('<IIIII',installed['object'],84)==(4,356,400,0,PIXELS),
        'buffer_uses_native_image_set_source':installed['calls'][0]['name']=='image_set_src' and installed['calls'][0]['args'][:2]==[OBJECT,OBJECT+84],
        'native_canvas_destructor_only_calls_noop_invalidate':destroyed['calls']==[] and word(0x49c268)==0xe12fff1e,
        'top_layer_offset_28_and_active_offset_16':top['return']==active['return']==PARENT,
        'no_display_returns_null':empty['return']==0,
        'delete_observer_layout':struct.unpack('<III',registered['events'])==(CALLBACK,0,33) and registered['spec'][34]&63==1,
        'delete_event_code_33':word(0x4960fc)==0xe3a01021 and word(0x496104)==0xeb000210,
        'native_event_target_getter':word(0x4969bc)==0xe5900000 and word(0x4969c0)==0xe12fff1e,
    }
    return {'pass':all(checks.values()),'checks':checks,'source_sha256':STOCK_SHA,
            'evidence_class':'OFFLINE_EXACT_NATIVE_PRIMITIVES_WITH_SYNTHETIC_CALLEES',
            'hardware_displayed':False,'full_gui_lifecycle_verified':False,
            'cache_invalidation_is_noop':True,'canvas_bytes':104,'descriptor_offset':84,
            'synthetic_dependencies':list(HOOKS.values())}


if __name__=='__main__':
    import json
    report=audit();print(json.dumps(report,indent=2));raise SystemExit(0 if report['pass'] else 1)
