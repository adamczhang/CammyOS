#!/usr/bin/env python3
"""Exact-build, read-only instruction checks; never executes sdvcam.

These assertions cover a bounded instruction slice, not global audio ownership.
In particular helper names at 0x24xxxx are NOT inferred from nearby objdump
symbol labels. SHA256 binds the addresses to the saved stock ELF.
SPDX-License-Identifier: MIT
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from native.adapters.verify_static_evidence import DEFAULT_ELF, EXPECTED_SHA256, Elf

# Entire six-instruction EOF polling loop: read, compare, exit if set,
# sleep(5000), unconditional back edge. No deadline/stop test inside the loop.
POLL = {0x656F8: 0xE59430D8, 0x656FC: 0xE3530000, 0x65700: 0x1A000002,
        0x65704: 0xE3010388, 0x65708: 0xEBFF1EEE, 0x6570C: 0xEAFFFFF9}


def branch_target(word, address):
    if word & 0x0F000000 != 0x0B000000:
        return None
    offset = word & 0xFFFFFF
    if offset & 0x800000:
        offset -= 0x1000000
    return address + 8 + offset * 4


def checks(elf):
    def words(expected):
        return all(elf.word(a) == w for a, w in expected.items())

    def call(address, target):
        return branch_target(elf.word(address), address) == target

    return {
        'speaker_mutex_load': elf.got_loads(0x80E0C, 0x80E90).get(0x80E80) == 'g_audio_mutex',
        'speaker_trylock': call(0x80E8C, elf.plt['pthread_mutex_trylock']),
        'play_call': call(0x81050, 0x655EC),
        'poll_complete_slice': words(POLL),
        'poll_sleep_target': call(0x65708, elf.plt['usleep']),
        'callback_event_256': words({0x64DAC: 0xE3520C01, 0x64DB0: 0x03A03001,
                                     0x64DB4: 0x058030D8, 0x64DB8: 0x0AFFFFF8}),
        'frame_metadata_44100': words({0x656C8: 0xE30A3C44, 0x656CC: 0xE58D3034}),
        'frame_submit_call': call(0x656D8, 0x24DBAC),
        'eof_set_call': call(0x656F4, 0x24F2BC),
        'post_eof_calls': call(0x65724, 0x24F2BC) and call(0x6572C, 0x24D934)
                          and call(0x65734, 0x24D710),
        'post_play_unlock': words({0x81150: 0xE59D000C}) and
                            call(0x81154, elf.plt['pthread_mutex_unlock']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--elf', type=Path, default=DEFAULT_ELF)
    args = parser.parse_args()
    raw = args.elf.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        parser.error('wrong stock ELF SHA256')
    result = checks(Elf(raw))
    print(json.dumps({'source_sha256': digest, 'evidence_class': 'static',
                      'hardware_validated': False, 'checks': result}, indent=2))
    return 0 if all(result.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
