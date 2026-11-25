#!/usr/bin/env python3
"""Offline exact-stock GUI face candidate. NO install, launch, arm or card modes.

This is a different class from WP2A status-only; old selectors MUST reject it.
Vendor bytes and the library remain ignored, non-executable local artifacts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from tools import build_native_status_candidate as status
from tools.display_owner_audit import audit as owner_audit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'native/loader'))
from elfinfo import Elf32
from abi_audit import audit as library_audit
SOURCE=status.DEFAULT_SOURCE
ADDRESS=0x8e2e20
GUI_SITE=0xe58c0
SLOT=0xafe098
EXPORTS=['cmf_attach_v1','cmf_detach_v1','cmf_gui_v1']
LIBRARY=ROOT/'build/native-face/libcammy_native_face.so'
PREFIX=str(ROOT/'build/toolchain/bin/arm-buildroot-linux-musleabihf-')


def assemble(library_sha):
    if len(library_sha)!=64 or any(c not in '0123456789abcdef' for c in library_sha):
        raise ValueError('SHA256 required')
    path=f'/tmp/cammy-face/{library_sha}/face.so'
    base=ROOT/'build/native-face';base.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stub-',dir=base) as tmp:
        tmp=Path(tmp);obj=tmp/'stub.o';elf=tmp/'stub.elf';binary=tmp/'stub.bin'
        subprocess.run([PREFIX+'gcc','-c','-x','assembler-with-cpp',f'-DLIB_PATH="{path}"',
                        str(ROOT/'native/face/face_stub.S'),'-o',str(obj)],check=True,capture_output=True)
        subprocess.run([PREFIX+'ld',f'-Ttext={ADDRESS:#x}','-e','face_main_stub',str(obj),'-o',str(elf)],check=True,capture_output=True)
        subprocess.run([PREFIX+'objcopy','-O','binary','--only-section=.text',str(elf),str(binary)],check=True,capture_output=True)
        symbols=subprocess.run([PREFIX+'nm','-n',str(elf)],check=True,capture_output=True,text=True).stdout
        labels={name:int(address,16) for line in symbols.splitlines() if len(p:=line.split())==3
                for address,kind,name in [p] if kind.lower()=='t'}
        raw=binary.read_bytes()
    if ADDRESS+len(raw)>0x8e3000:raise ValueError('Stub crosses original mapped RX page')
    return raw,labels,path


def patch(original,library_sha):
    status.check_source(original)
    if not owner_audit(SOURCE)['pass']:raise ValueError('GUI owner static audit failed')
    if struct.unpack_from('<I',original,GUI_SITE-0x10000)[0]!=status.enc_branch(GUI_SITE,0x4a7950,True):
        raise ValueError('GUI call differs')
    stub,labels,path=assemble(library_sha)
    out=bytearray(original)
    ranges=[]
    def replace(offset,data,label):
        out[offset:offset+len(data)]=data;ranges.append({'offset':offset,'size':len(data),'purpose':label})
    replace(status.CALL_SITE-0x10000,struct.pack('<I',status.enc_branch(status.CALL_SITE,labels['face_main_stub'],True)),'main attach/detach bracket')
    replace(GUI_SITE-0x10000,struct.pack('<I',status.enc_branch(GUI_SITE,labels['face_gui_stub'],True)),'GUI timer bracket')
    replace(ADDRESS-0x10000,stub,'two stubs and content-addressed library path')
    # p_filesz+p_memsz of RX; still inside its already-mapped last page.
    rx_size=ADDRESS+len(stub)-0x10000
    replace(0x34+3*32+16,struct.pack('<II',rx_size,rx_size),'RX file/memory size')
    # Explicitly reserve one word after stock BSS, inside the same existing RW page.
    data=Elf32(original).phdrs[4]
    if data['vaddr']+data['memsz']!=SLOT:raise ValueError('BSS end differs')
    replace(0x34+4*32+20,struct.pack('<I',data['memsz']+4),'zero-initialized GUI function slot')
    allowed=set(i for r in ranges for i in range(r['offset'],r['offset']+r['size']))
    if len(out)!=len(original) or any(a!=b and i not in allowed for i,(a,b) in enumerate(zip(original,out))):
        raise ValueError('Patch escaped declared ranges')
    return bytes(out),{'class':'gui-face-trial-v1','source_sha256':status.SOURCE_SHA256,
        'library_sha256':library_sha,'library_path':path,'candidate_sha256':hashlib.sha256(out).hexdigest(),
        'stub_bytes':len(stub),'labels':labels,'changes':ranges,'gui_slot':SLOT,
        'deployed':False,'activation_eligible':False,'hardware_displayed':False,
        'media_sites_changed':False,'status_only':False,'requires_new_activation_review':True,
        'trial_delay_ms':3000,'trial_duration_ms':10000}


def build(out_dir):
    raw_path=Path(out_dir).absolute()
    if any(p.is_symlink() for p in [raw_path,*raw_path.parents]):raise ValueError('Symlink output refused')
    out_dir=raw_path.resolve()
    allowed=(ROOT/'private/native-face',ROOT/'build/native-face')
    if not any(out_dir.is_relative_to(p) for p in allowed):raise ValueError('Ignored face output directory required')
    if out_dir.exists():raise ValueError('Refusing overwrite')
    audit=library_audit(LIBRARY,ROOT/'build/native-loader/device-rootfs',EXPORTS)
    if not audit['pass']:raise ValueError('Device ABI audit failed: '+str(audit))
    library=LIBRARY.read_bytes();sha=hashlib.sha256(library).hexdigest()
    candidate,manifest=patch(SOURCE.read_bytes(),sha)
    manifest['library_abi_audit']=audit
    manifest['source_inputs_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT/'native/face').iterdir()) if p.is_file()}
    manifest['asset']=json.loads((ROOT/'build/native-face/asset.json').read_text())
    manifest['trial_delay_ms']=manifest['asset'].get('delay_ms',3000)
    out_dir.mkdir(parents=True,mode=0o700)
    for name,data in [('sdvcam-face.candidate',candidate),('face.so',library),
                      ('manifest.json',(json.dumps(manifest,indent=2)+'\n').encode())]:
        with (out_dir/name).open('xb') as f:os.fchmod(f.fileno(),0o600);f.write(data)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out-dir',type=Path,required=True)
    try:print(json.dumps(build(p.parse_args().out_dir),indent=2))
    except (ValueError,OSError) as e:p.exit(2,str(e)+'\n')
