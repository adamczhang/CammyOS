#!/usr/bin/env python3
"""Read-only native integration evidence. Exit 4 = locally valid, no live backend.
SPDX-License-Identifier: MIT
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from native.adapters.verify_static_evidence import DEFAULT_ELF,EXPECTED_SHA256,Elf
from native.audio.verify_speaker_path import branch_target

# Every PC/literal/add term that derives the native function-name references is
# pinned, rather than trusting a nearby exported symbol in objdump output.
WORDS={
    0x24D73C:0xE59F11B0,0x24D74C:0xE08F1001,0x24D754:0xE2811F77,0x24D75C:0xE2811001,
    0x24D96C:0xE59F11F0,0x24D97C:0xE08F1001,0x24D984:0xE2811E1F,
    0x24DBE0:0xE59F112C,0x24DBEC:0xE08F1001,0x24DBF4:0xE2811C02,
    0x24DBFC:0xE3A03002,0x24DC00:0xE0811003,
    0x130454:0xE5943048,0x130458:0xE3530000,0x13045C:0x0A000004,
    0x130474:0xE1A01005,0x130478:0xE1A00004,0x130480:0xEAFFFFF3,
    0x65694:0xE58DA010,0x65680:0xE3A0A001,0x656A0:0xE58D3018,
    0x656C8:0xE30A3C44,0x656CC:0xE58D3034,0x24DC8C:0xE5953020,
}


def checks(e):
    def call(a,target):return branch_target(e.word(a),a)==target
    names={
        'start':e.cstr(e.word(0x24D8F4)+0x24D74C+8+477),
        'stop':e.cstr(e.word(0x24DB64)+0x24D97C+8+496),
        'send':e.cstr(e.word(0x24DD14)+0x24DBEC+8+514),
    }
    return {
        'address_derivations_and_frame_stores':all(e.word(a)==w for a,w in WORDS.items()),
        'native_names':names=={'start':'AW_MPI_AO_StartChn','stop':'AW_MPI_AO_StopChn','send':'AW_MPI_AO_SendFrame'},
        'stop_wait_helper':call(0x24DAB4,0x130440),
        'wait_is_cond_wait':call(0x13047C,e.plt['pthread_cond_wait']),
        'post_eof_stop_then_restart':call(0x6572C,0x24D934) and call(0x65734,0x24D710),
        'pa_timer_globals':e.got_loads(0x8027C,0x802D0)=={0x8028C:'pa_flag',0x802A8:'g_play_state'},
    }


def sdk_layout_compatible(offsets):
    """Minimum necessary layout match, never sufficient native eligibility."""
    return offsets.get('mpAddr')==8 and offsets.get('first_word')=='bitwidth'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf',type=Path,default=DEFAULT_ELF)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    data=args.elf.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED_SHA256:parser.error('wrong stock ELF identity')
    result=checks(Elf(data))
    sdk={'mpAddr':12,'first_word':'samplerate'}
    report={'evidence_class':'static','source_sha256':EXPECTED_SHA256,'checks':result,
            'sdk_commit':'bf42476c330aa41aada55e7c030e4be22e46107d',
            'sdk_frame_layout':sdk,'sdk_layout_compatible':sdk_layout_compatible(sdk),
            'native_driver_available':False,'activation_eligible':False,'hardware_test_run':False,
            'blockers':['M105 frame layout differs from pinned SDK header',
                        'PA timer and shared output ownership not integrated',
                        'Native stop/close may wait indefinitely; no measured physical stop proof']}
    text=json.dumps(report,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    print(text,end='')
    return 4 if all(result.values()) else 1


if __name__=='__main__':sys.exit(main())
