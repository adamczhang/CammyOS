#!/usr/bin/env python3
"""Create a private, stock-derived CammyOS image. Does not touch the camera."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]
CAMERA=ROOT.parent/'Cammy Bot'
BACKUP=Path(os.environ.get('CAMMY_STOCK_BACKUP',str(CAMERA/'M105 investigation/work/firmware-backup')))
BUILD=ROOT/'build/fork'
DIST=ROOT/'dist'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(args, log):
    with (ROOT/'evidence'/log).open('w') as f:
        subprocess.run(args,check=True,stdout=f,stderr=subprocess.STDOUT)
def inventory(root):
    rows={}
    for p in root.rglob('*'):
        rel=str(p.relative_to(root))
        if p.is_symlink():rows[rel]=dict(type='symlink',target=str(p.readlink()))
        elif p.is_file():rows[rel]=dict(type='file',sha256=sha(p),mode=p.stat().st_mode&0o7777)
        elif p.is_dir():rows[rel]=dict(type='directory',mode=p.stat().st_mode&0o7777)
        else:raise RuntimeError(f'Unexpected special object: {rel}')
    return rows

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--rebuild",action="store_true",help="Replace only this builder's generated fork artifacts");args=parser.parse_args()
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    if args.rebuild:
        if BUILD.exists():shutil.rmtree(BUILD)
        for name in ("cammyos-m105-rootfs.squashfs","stock-spi16MiB.bin","cammyos-m105-spi16MiB.bin","manifest.json","cammyos-rootfs-ramtest.tar.gz","rootfs-inventory.json"):
            p=DIST/name
            if p.exists():p.unlink()
    rows=json.loads((BACKUP/'partition-manifest.json').read_text())
    if len(rows)!=7 or sum(r['expected_size'] for r in rows)!=16*1024*1024:raise RuntimeError('Unexpected flash layout')
    for r in rows:
        p=BACKUP/f'{r["device"]}-{r["name"]}.bin'
        if p.stat().st_size!=r['expected_size'] or sha(p)!=r['sha256']:raise RuntimeError(f'Stock backup mismatch: {p.name}')
    binary=ROOT/'build/arm/cammy-portal'
    if not binary.exists():raise RuntimeError('Run make arm first')
    for p in [BUILD,DIST,ROOT/'evidence']:p.mkdir(parents=True,exist_ok=True)
    stock=BUILD/'stock-rootfs';staged=BUILD/'rootfs';verified=BUILD/'verified-rootfs'
    for p in [stock,staged,verified]:
        if p.exists():raise RuntimeError(f'{p} exists: preserve results or choose a fresh build directory')
    run(['unsquashfs','-d',str(stock),str(BACKUP/'mtd3-rootfs.bin')],'extract-stock.log')
    shutil.copytree(stock,staged,symlinks=True)
    before=inventory(stock)
    shutil.copytree(ROOT/'overlay',staged,dirs_exist_ok=True,symlinks=True)
    # Preserve vendor radio settings and replace only the user-selected AP label.
    net_config=staged/'data/net_config.lua'
    text=net_config.read_text()
    text,count=re.subn(r'(?m)^(\s*(?:ssid_prefix|\["ssid_prefix"\])\s*=\s*)"AT-M105"',
                       r'\1"CammyOS"',text)
    if count!=1:raise RuntimeError('Unexpected stock AP name configuration')
    net_config.write_text(text)
    shutil.copy2(binary,staged/'usr/bin/cammy-portal')
    (staged/'usr/libexec/cammy').mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'build/arm/busybox',staged/'usr/libexec/cammy/busybox')
    # Existing vendor directory modes are part of the board image contract.
    for rel,meta in before.items():
        if meta['type']=='directory':(staged/rel).chmod(meta['mode'])
    image=DIST/'cammyos-m105-rootfs.squashfs'
    if image.exists():raise RuntimeError('Image exists; do not silently overwrite a review artifact')
    run(['mksquashfs',str(staged),str(image),'-comp','xz','-b','262144','-noappend','-all-root','-no-xattrs','-exports','-mkfs-time','0','-all-time','0','-processors','2'],'mksquashfs.log')
    cap=next(r['expected_size'] for r in rows if r['name']=='rootfs')
    if image.stat().st_size>cap:raise RuntimeError('Image exceeds real rootfs partition')
    run(['unsquashfs','-d',str(verified),str(image)],'verify-rootfs.log')
    after=inventory(verified);expected=inventory(staged)
    if after!=expected:raise RuntimeError('Image extraction differs from staged path/content/mode inventory')
    changed=sorted(k for k in before if k not in after or before[k]!=after[k]);added=sorted(set(after)-set(before))
    if changed!=['bin/adb_shell','data/net_config.lua']:raise RuntimeError(f'Unexpected modified stock paths: {changed}')
    # Full private images preserve all non-rootfs partitions and settings exactly.
    stock_raw=DIST/'stock-spi16MiB.bin';candidate=DIST/'cammyos-m105-spi16MiB.bin'
    with stock_raw.open('wb') as stockf,candidate.open('wb') as newf:
        for r in rows:
            data=(BACKUP/f'{r["device"]}-{r["name"]}.bin').read_bytes();stockf.write(data)
            if r['name']=='rootfs':data=image.read_bytes()+b'\xff'*(cap-image.stat().st_size)
            newf.write(data)
    if stock_raw.stat().st_size!=16*1024*1024 or candidate.stat().st_size!=16*1024*1024:raise RuntimeError('Raw image size mismatch')
    manifest=dict(product='CammyOS',version='0.1.0',base='private stock-derived M105 fork',stock_partitions=rows,modified_stock_paths=changed,added_paths=added,rootfs_bytes=image.stat().st_size,rootfs_partition_bytes=cap,portal_sha256=sha(binary),rootfs_sha256=sha(image),stock_spi_sha256=sha(stock_raw),candidate_spi_sha256=sha(candidate),kernel_unchanged=True,bootloader_unchanged=True,full_boot_verified=False,recovery_verified=False,flashed=False,redistribution='Not approved: includes vendor binaries and device configuration',raw_image_warning='Raw SPI layout, not a PhoenixSuit/PhoenixCard package')
    (DIST/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    # RAM test archive is source stock + open overlay, with no /dev binding by default.
    with tarfile.open(DIST/'cammyos-rootfs-ramtest.tar.gz','w:gz') as archive:
        def canonical(info):
            info.uid=info.gid=0;info.uname=info.gname='root';info.mtime=0;return info
        for p in sorted(staged.iterdir()):archive.add(p,arcname=p.name,recursive=True,filter=canonical)
    (DIST/'rootfs-inventory.json').write_text(json.dumps(after,indent=2)+'\n')
    for p in DIST.iterdir():p.chmod(0o600)
    DIST.chmod(0o700)
    print(json.dumps({k:manifest[k] for k in ('product','version','rootfs_bytes','rootfs_partition_bytes','modified_stock_paths','added_paths','kernel_unchanged','bootloader_unchanged','flashed')},indent=2))
if __name__=='__main__':main()
