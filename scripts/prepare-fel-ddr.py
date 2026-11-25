#!/usr/bin/env python3
"""Extract xfel's V85x DDR initializer with this camera's complete DRAM block.

Build only. Upload/execute through FEL separately on a stopped camera.
"""
import os
from pathlib import Path
import hashlib
import json
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CAMERA = ROOT.parent / 'Cammy Bot'
XFEL_REVISION = '445e8aefe6914c85817cc9bd1d201629364b0ec6'
BOOT0_SHA256 = '80494b39528b3454f8475876fcd7b831c23a25b4c983763596de54cc33ba6ec0'


def main():
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')], check=True,
                   stdout=subprocess.DEVNULL)
    repo = CAMERA / 'tools/xfel'
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != XFEL_REVISION:
        raise ValueError('Review the changed xfel revision before preparing a payload')
    stock = (CAMERA / 'M105 investigation/work/firmware-backup/mtd0-uboot.bin').read_bytes()
    if hashlib.sha256(stock).hexdigest() != BOOT0_SHA256:
        raise ValueError('Original board bootloader backup does not match')
    text = (repo / 'chips/v851_v853.c').read_text()
    array = text.split('static const uint8_t ddr_payload[] = {', 1)[1].split('};', 1)[0]
    data = bytearray(int(n, 16) for n in re.findall(r'0x([0-9a-fA-F]{2})\b', array))
    if len(data) != 0x4c80 or data[4:12] != b'eGON.BT0':
        raise ValueError('Unexpected DDR initializer layout')
    data[0x38:0xb8] = stock[0x38:0xb8]
    assert struct.unpack_from('<II', data, 0x38) == (912, 3)
    struct.pack_into('<I', data, 12, 0x5f0a6c39)
    struct.pack_into('<I', data, 12, sum(struct.unpack('<%dI' % (len(data) // 4), data)) & 0xffffffff)
    out = ROOT / 'build/fel'; out.mkdir(exist_ok=True)
    (out / 'ddr-m105.bin').write_bytes(data)
    manifest = dict(source_revision=revision, board_boot0_sha256=BOOT0_SHA256,
                    parameters='stock boot0 offset0x38, all128bytes', clock_mhz=912,
                    load_address='0x28000', length=len(data),
                    sha256=hashlib.sha256(data).hexdigest(), build_only=True)
    (out / 'ddr-m105.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
