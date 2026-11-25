#!/usr/bin/env python3
"""DRAFT static verifier for WP6 body-adapter address claims; see README.md.

Reads ONLY the saved stock `sdvcam` ELF (exact SHA256 below) and checks each
preliminary address-backed claims. The package was interrupted before its
contract/report was written; the initial manager run passed only 9/22 checks.
Chunk 2 repaired AO2 GOT resolution, bringing the partial result to 11/22.
Only AO2 was independently reviewed there; other claims remain drafts.
This tool is not hardware acceptance evidence. Standard library only: a minimal
ELF32/ARM decoder (branch, literal load, add-pc, immediate data transfer,
add/mov/movw/cmp immediates, GOT relocations, .ARM.exidx bounds). No vendor
bytes are copied, nothing is executed and no device, camera or network is
touched. Exit 0 = every check passed, 1 = a check failed, 2 = wrong input.

A pass shows that these instructions and references exist in the exact build.
It does not show runtime thread identity, lock completeness or physical
behaviour; those remain inferences/unknowns until a reviewed contract exists.

Usage: verify_static_evidence.py [--elf PATH] [--json]
SPDX-License-Identifier: MIT
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

EXPECTED_SHA256 = '8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec'
DEFAULT_ELF = (Path(__file__).resolve().parents[3] /
               'Cammy Bot/M105 investigation/work/firmware-rootfs/usr/bin/sdvcam')


class Elf:
    def __init__(self, raw):
        self.raw = raw
        if raw[:4] != b'\x7fELF' or raw[4] != 1 or raw[5] != 1:
            raise ValueError('not a little-endian ELF32')
        (_, _, _, _, phoff, shoff, _, _, phentsize, phnum, shentsize, shnum,
         shstrndx) = struct.unpack_from('<HHIIIIIHHHHHH', raw, 16)
        self.loads = []
        for i in range(phnum):
            p_type, p_off, p_va, _, p_filesz = struct.unpack_from('<IIIII', raw, phoff + i * phentsize)
            if p_type == 1:
                self.loads.append((p_va, p_off, p_filesz))
        secs = [struct.unpack_from('<IIIIIIIIII', raw, shoff + i * shentsize) for i in range(shnum)]
        names = secs[shstrndx]
        self.sec = {}
        for s in secs:
            name = raw[names[4] + s[0]:].split(b'\0', 1)[0].decode()
            self.sec[name] = {'addr': s[3], 'off': s[4], 'size': s[5], 'entsize': s[9]}
        dynstr = self.sec['.dynstr']
        self.dynsym = []
        sym = self.sec['.dynsym']
        for off in range(sym['off'], sym['off'] + sym['size'], 16):
            st_name, value, size, info, _, _ = struct.unpack_from('<IIIBBH', raw, off)
            self.dynsym.append((raw[dynstr['off'] + st_name:].split(b'\0', 1)[0].decode(), value, size))
        self.plt = {}
        rel, plt = self.sec['.rel.plt'], self.sec['.plt']
        for i, off in enumerate(range(rel['off'], rel['off'] + rel['size'], 8)):
            _, info = struct.unpack_from('<II', raw, off)
            self.plt[self.dynsym[info >> 8][0]] = plt['addr'] + 20 + 12 * i
        self.got_sym = {}
        rd = self.sec['.rel.dyn']
        for off in range(rd['off'], rd['off'] + rd['size'], 8):
            r_off, info = struct.unpack_from('<II', raw, off)
            if info >> 8:
                self.got_sym[r_off] = self.dynsym[info >> 8][0]
        got = self.sec.get('.got')
        if got:
            by_val = {v: n for n, v, _ in self.dynsym if v and n}
            for off in range(0, got['size'], 4):
                val = struct.unpack_from('<I', raw, got['off'] + off)[0]
                if val in by_val:
                    self.got_sym.setdefault(got['addr'] + off, by_val[val])
        ex = self.sec['.ARM.exidx']
        starts = []
        for off in range(0, ex['size'], 8):
            w = struct.unpack_from('<I', raw, ex['off'] + off)[0] & 0x7fffffff
            if w & 0x40000000:
                w -= 0x80000000
            starts.append(ex['addr'] + off + w)
        self.fn_starts = sorted(set(starts))
        self.text = self.sec['.text']

    def word(self, va):
        for p_va, p_off, size in self.loads:
            if p_va <= va and va + 4 <= p_va + size:
                return struct.unpack_from('<I', self.raw, p_off + va - p_va)[0]
        raise KeyError(hex(va))

    def cstr(self, va):
        for p_va, p_off, size in self.loads:
            if p_va <= va < p_va + size:
                return self.raw[p_off + va - p_va:p_off + size].split(b'\0', 1)[0].decode('latin-1')
        return None

    def sym(self, name):
        for n, v, s in self.dynsym:
            if n == name and v:
                return v, s
        raise KeyError(name)

    def bounds(self, va):
        lo = max((x for x in self.fn_starts if x <= va), default=self.text['addr'])
        hi = min((x for x in self.fn_starts if x > va), default=self.text['addr'] + self.text['size'])
        return lo, hi

    # ---- ARM (A32) decoding of the few forms the claims need -------------
    def branch(self, va):
        w = self.word(va)
        if w >> 28 == 0xf or (w & 0x0e000000) != 0x0a000000:
            return None
        d = w & 0xffffff
        if d & 0x800000:
            d -= 0x1000000
        return {'link': bool(w & 0x01000000), 'cond': w >> 28, 'target': (va + 8 + 4 * d) & 0xffffffff}

    def branch_sites(self, target, link_only=False):
        out, base, raw_off = [], self.text['addr'], self.text['off']
        for off in range(0, self.text['size'] - 3, 4):
            w = struct.unpack_from('<I', self.raw, raw_off + off)[0]
            if w >> 28 == 0xf or (w & 0x0e000000) != 0x0a000000 or (link_only and not w & 0x01000000):
                continue
            d = w & 0xffffff
            if d & 0x800000:
                d -= 0x1000000
            if (base + off + 8 + 4 * d) & 0xffffffff == target:
                out.append(base + off)
        return out

    def ldr_literal(self, va):
        w = self.word(va)
        if (w & 0x0f7f0000) != 0x051f0000:
            return None
        lit = va + 8 + ((w & 0xfff) if w & 0x00800000 else -(w & 0xfff))
        return (w >> 12) & 15, self.word(lit)

    def add_pc(self, va):
        w = self.word(va)
        if (w & 0x0fff0ff0) != 0x008f0000:
            return None
        return (w >> 12) & 15, w & 15

    def mem_imm(self, va):
        w = self.word(va)
        if (w & 0x0e000000) != 0x04000000:
            return None
        return {'cond': w >> 28, 'load': bool(w & 0x00100000), 'byte': bool(w & 0x00400000),
                'rn': (w >> 16) & 15, 'rt': (w >> 12) & 15,
                'imm': (w & 0xfff) if w & 0x00800000 else -(w & 0xfff)}

    def dp_imm(self, va):
        w = self.word(va)
        if (w & 0x0e000000) != 0x02000000:
            return None
        rot = ((w >> 8) & 15) * 2
        imm = ((w & 0xff) >> rot | (w & 0xff) << (32 - rot)) & 0xffffffff if rot else w & 0xff
        return {'cond': w >> 28, 'op': (w >> 21) & 15, 's': bool(w & 0x00100000),
                'rn': (w >> 16) & 15, 'rd': (w >> 12) & 15, 'imm': imm}

    def movw(self, va):
        w = self.word(va)
        if (w & 0x0ff00000) != 0x03000000:
            return None
        return (w >> 12) & 15, ((w >> 4) & 0xf000) | (w & 0xfff)

    def blx_reg(self, va):
        w = self.word(va)
        return (w & 0x0ffffff0) == 0x012fff30 and w & 15

    def string_refs(self, lo, hi):
        """Strings materialised by `ldr rX,=lit` + `add rX, pc, rX` inside [lo,hi)."""
        refs = {}
        for va in range(lo, hi, 4):
            lit = self.ldr_literal(va)
            if not lit:
                continue
            for p in range(va + 4, min(va + 52, hi + 52), 4):
                ap = self.add_pc(p)
                if ap and ap[0] == lit[0] and ap[1] == lit[0]:
                    s = self.cstr((p + 8 + lit[1]) & 0xffffffff)
                    if s:
                        refs.setdefault(s, []).append(p)
                    break
        return refs

    def got_loads(self, lo, hi):
        """Linear register tracking (branches ignored) of GOT loads `ldr rd,[rn,rm]`."""
        regs, out = {}, {}
        for va in range(lo, hi, 4):
            w = self.word(va)
            lit = self.ldr_literal(va)
            ap = self.add_pc(va)
            if lit:
                regs[lit[0]] = ('lit', lit[1])
            elif ap and regs.get(ap[1], (None,))[0] == 'lit':
                regs[ap[0]] = ('addr', (va + 8 + regs[ap[1]][1]) & 0xffffffff)
            elif (w & 0x0ff00ff0) == 0x07900000:   # ldr rd, [rn, rm]
                rn, rd, rm = (w >> 16) & 15, (w >> 12) & 15, w & 15
                a, b = regs.get(rn), regs.get(rm)
                if a and b and {a[0], b[0]} == {'addr', 'lit'}:
                    slot = (a[1] + b[1]) & 0xffffffff
                    if slot in self.got_sym:
                        out[va] = self.got_sym[slot]
                regs.pop(rd, None)
        return out


class Checker:
    def __init__(self, elf):
        self.e, self.results = elf, []

    def check(self, cid, claim, fn):
        try:
            ok, detail = fn()
        except (KeyError, ValueError, TypeError, struct.error) as exc:
            ok, detail = False, f'error: {exc!r}'
        self.results.append({'id': cid, 'claim': claim, 'ok': bool(ok), 'detail': detail})

    # helpers returning (ok, detail)
    def bl_to(self, site, target, link=True):
        b = self.e.branch(site)
        ok = bool(b) and b['target'] == target and b['link'] == link
        return ok, f'{hex(site)} -> {hex(b["target"]) if b else None}'

    def bl_plt(self, site, name):
        return self.bl_to(site, self.e.plt[name])

    def refs(self, lo, hi, *strings):
        found = self.e.string_refs(lo, hi)
        missing = [s for s in strings if s not in found]
        return not missing, f'[{hex(lo)},{hex(hi)}) missing={missing}'

    def exidx(self, va, lo, hi):
        b = self.e.bounds(va)
        return b == (lo, hi), f'{hex(va)} in [{hex(b[0])},{hex(b[1])})'


def all_checks(c):
    e = c.e
    ck = c.check
    # ---------------- audio output (stock prompt player) ----------------
    ck('AO1', 'PlaySound 0x80E0C runs system("tinymix set 20 1") at 0x80E70',
       lambda: (c.bl_plt(0x80E70, 'system')[0] and
                e.cstr(0x80E68 + 8 + e.ldr_literal(0x80E58)[1]) == 'tinymix set 20 1', 'system + string'))
    ck('AO2', 'PlaySound trylocks g_audio_mutex at 0x80E8C (GOT load at 0x80E80)',
       lambda: (c.bl_plt(0x80E8C, 'pthread_mutex_trylock')[0] and
                e.got_loads(0x80E0C, 0x80E90).get(0x80E80) == 'g_audio_mutex', 'trylock + GOT'))
    ck('AO3', 'PlaySound refuses on contention and calls WavPlayer::Play 0x655EC at 0x81050',
       lambda: (c.refs(0x80E0C, 0x81270, 'audio device is busy, can not play keytone', 'PlaySound')[0] and
                c.bl_to(0x81050, 0x655EC)[0] and
                c.refs(0x655EC, 0x657A8, 'Play end', 'source/device_model/media/player/wav_player.cpp')[0],
                'refs + call'))
    ck('AO4', 'Play sends frames, sets EOF, then polls +0xD8 every 5 ms with no bound (0x656F8-0x6570C)',
       lambda: (c.bl_to(0x656D8, 0x24DBAC)[0] and c.refs(0x24DBAC, 0x24DD4C, 'AW_MPI_AO_SendFrame')[0] and
                c.bl_to(0x656F4, 0x24F2BC)[0] and c.refs(0x24F2BC, 0x24F4E0, 'AW_MPI_AO_SetStreamEof')[0] and
                (lambda m: m and m['load'] and m['rn'] == 4 and m['rt'] == 3 and m['imm'] == 0xD8)(e.mem_imm(0x656F8)) and
                (lambda d: d and d['op'] == 10 and d['rn'] == 3 and d['imm'] == 0)(e.dp_imm(0x656FC)) and
                (lambda b: b and b['cond'] == 1 and b['target'] == 0x65710)(e.branch(0x65700)) and
                e.movw(0x65704) == (0, 0x1388) and c.bl_plt(0x65708, 'usleep')[0] and
                (lambda b: b and b['cond'] == 14 and not b['link'] and b['target'] == 0x656F8)(e.branch(0x6570C)),
                'send/eof/poll loop'))
    ck('AO5', 'WavPlayer::MPPAOCallback 0x64D44 sets +0xD8 only on event 0x100 (0x64DAC/0x64DB4)',
       lambda: (e.sym('_ZN11EyeseeLinux9WavPlayer13MPPAOCallbackEPvP9MPP_CHN_SN_14MPP_EVENT_TYPEES1_')[0] == 0x64D44 and
                (lambda d: d and d['op'] == 10 and d['rn'] == 2 and d['imm'] == 0x100)(e.dp_imm(0x64DAC)) and
                (lambda m: m and not m['load'] and m['cond'] == 0 and m['rn'] == 0 and m['rt'] == 3 and
                 m['imm'] == 0xD8)(e.mem_imm(0x64DB4)), 'callback eof flag'))
    ck('AO6', 'AudioCtrl::AudioProc 0x8027C (timer) turns the PA off: AW_MPI_AO_SetPA 0 + tinymix set 20 0',
       lambda: (e.sym('_ZN11EyeseeLinux9AudioCtrl9AudioProcE6sigval')[0] == 0x8027C and
                c.refs(0x8027C, 0x80328, 'AW_MPI_AO_SetPA 0', 'tinymix set 20 0')[0], 'refs'))
    # ---------------- audio input ----------------
    ck('AI1', 'snd_pcm_readi has exactly one call site, 0x2805A4, in alsaReadPcm',
       lambda: (e.branch_sites(e.plt['snd_pcm_readi']) == [0x2805A4] and
                c.refs(0x280544, 0x280804, 'alsaReadPcm', 'audio/alsa_interface.c')[0], 'single site'))
    ck('AI2', 'alsaReadPcm 0x280544 is called only from audioHw_AI_CapThread (0x257E78)',
       lambda: (e.branch_sites(0x280544) == [0x257E78] and
                c.refs(0x257D48, 0x258354, 'audioHw_AI_CapThread', 'audio/audio_hw.c')[0], 'single caller'))
    ck('AI3', 'snd_pcm_writei has one site (0x2808EC, alsaWritePcm); snd_pcm_open has two',
       lambda: (e.branch_sites(e.plt['snd_pcm_writei']) == [0x2808EC] and
                len(e.branch_sites(e.plt['snd_pcm_open'])) == 2, 'site counts'))
    # ---------------- gimbal / motor UART ----------------
    send_sites = [0x52D8C, 0x7E3D4, 0x7E458, 0x7ED10, 0xAB634, 0xAB6A8, 0xC6EFC, 0xDB71C]
    locks = [e.plt[n] for n in ('pthread_mutex_lock', 'pthread_mutex_trylock', 'pthread_mutex_unlock')] + [0x37298]
    ck('G1', 'SendMotorControl [0x7E0A0,0x7E2DC) writes the UART at 0x7E228 and calls no mutex/lock wrapper',
       lambda: (c.exidx(0x7E0A0, 0x7E0A0, 0x7E2DC)[0] and c.refs(0x7E0A0, 0x7E2DC, 'SendMotorControl')[0] and
                c.bl_plt(0x7E228, 'write')[0] and
                not any((lambda b: b and b['link'] and b['target'] in locks)(e.branch(v))
                        for v in range(0x7E0A0, 0x7E2DC, 4)), 'write, no lock'))
    ck('G2', 'SendMotorControl has exactly 8 direct call sites', lambda: (
        sorted(e.branch_sites(0x7E0A0)) == send_sites, str([hex(x) for x in sorted(e.branch_sites(0x7E0A0))])))
    ck('G3', 'site 0x7E458 is inside MotorCmdTimerHandler(sigval), the tracking timer callback',
       lambda: ((lambda v: v[0] <= 0x7E458 < v[0] + v[1])(
           e.sym('_ZN11EyeseeLinux16MotorUartManager20MotorCmdTimerHandlerE6sigval')), 'in range'))
    ck('G4', 'sites 0xAB634/0xAB6A8 and firmware call 0xAB7F0 are in PreviewPresenter::HandleGUIMessage [0xAAD34,0xAB934)',
       lambda: (all(c.exidx(s, 0xAAD34, 0xAB934)[0] for s in (0xAB634, 0xAB6A8, 0xAB7F0)) and
                c.refs(0xAAD34, 0xAB934, 'HandleGUIMessage', 'source/bll_presenter/preview.cpp')[0], 'bounds'))
    ck('G5', '0xAB7F0 calls the motor firmware uploader 0x7F34C (/mnt/extsd/pocket_one_APP.bin)',
       lambda: (c.bl_to(0xAB7F0, 0x7F34C)[0] and
                c.refs(0x7F34C, 0x7F5CC, '/mnt/extsd/pocket_one_APP.bin', 'firmwareuploader')[0], 'uploader'))
    ck('G6', 'SetFaceFollowState 0x343AC starts/stops the 30 fps motor tracking timer 0x7EB04',
       lambda: (c.bl_to(0x343AC, 0x7EB04)[0] and c.refs(0x342E0, 0x34444, 'SetFaceFollowState')[0] and
                c.refs(0x7EB04, 0x7EDAC, 'MOTOR: tracking timer started (30fps)', 'StopMotorTimer')[0], 'timer'))
    ck('G7', 'UartMonitorThread 0x7F300 -> reader 0x7EF7C -> ProcessReceivedPacket 0x7E4E8 (MCU status/protection)',
       lambda: (e.sym('_ZN11EyeseeLinux16MotorUartManager17UartMonitorThreadEPv')[0] == 0x7F300 and
                c.bl_to(0x7F338, 0x7EF7C)[0] and c.bl_to(0x7F278, 0x7E4E8)[0] and
                c.refs(0x7E4E8, 0x7EA1C, 'ProcessReceivedPacket', '------------UART_MOTOR_PROTECTION----------')[0],
                'feedback path'))
    ck('G8', 'IOT MOTOR_CTR (0x85C64) posts httpServer::postmsg3preview(0xF, v) to g_task_queue via 0x8413C/0x84054',
       lambda: (c.refs(0x85848, 0x85CF4, '-----IOTYPE_CDR_MOTOR_CTR---result_value = %d')[0] and
                c.bl_to(0x85C80, 0x8413C)[0] and
                (lambda d: d and d['op'] == 13 and d['rd'] == 1 and d['imm'] == 0xF)(e.dp_imm(0x84140)) and
                c.bl_to(0x84148, 0x84054)[0] and c.bl_to(0x840C4, 0x58010)[0] and
                set(e.got_loads(0x84054, 0x840C8).values()) >= {
                    '_ZN11EyeseeLinux10httpServer15postmsg3previewEii', 'g_task_queue'}, 'post'))
    ck('G9', 'postmsg3preview 0x82404 tail-calls PreviewPresenter HandleRemoteMessage 0xA9348',
       lambda: (e.sym('_ZN11EyeseeLinux10httpServer15postmsg3previewEii')[0] == 0x82404 and
                c.bl_to(0x8240C, 0xA9348, link=False)[0] and
                c.refs(0xA9348, 0xA9610, 'HandleRemoteMessage')[0], 'tail call'))
    # ---------------- GUI / face context ----------------
    ck('U1', 'WindowManager::PostMsg 0xE3F38 posts to g_task_queue (worker), not the GUI thread',
       lambda: (c.refs(0xE3F38, 0xE406C, 'PostMsg', 'source/uilayer_view/gui/lvgl/window/window_manager.cpp')[0] and
                c.bl_to(0xE4010, 0x58010)[0] and 'g_task_queue' in e.got_loads(0xE3F38, 0xE4014).values(), 'worker'))
    ck('U2', '0xE5024 enqueues to the GUI deque (+0xE4) under the +0x80 lock',
       lambda: ((lambda d: d and d['op'] == 4 and (d['rd'], d['rn'], d['imm']) == (0, 4, 0x80))(e.dp_imm(0xE5078)) and
                (lambda d: d and d['op'] == 4 and (d['rd'], d['rn'], d['imm']) == (4, 4, 0xE4))(e.dp_imm(0xE507C)) and
                c.bl_to(0xE5084, 0x37298)[0] and len(e.branch_sites(0xE5024, link_only=True)) >= 10, 'enqueue'))
    ck('U3', 'GUI loop 0xE582C drains +0xE4 under +0x80, then under +0x24 calls 0x4A7950 and window vtable+0x28, sleeps 2 ms',
       lambda: ((lambda d: d and (d['rd'], d['rn'], d['imm']) == (0, 5, 0x80))(e.dp_imm(0xE5874)) and
                c.bl_to(0xE587C, 0x37298)[0] and
                (lambda d: d and (d['rd'], d['rn'], d['imm']) == (7, 5, 0x24))(e.dp_imm(0xE58B0)) and
                c.bl_to(0xE58BC, 0x37298)[0] and c.bl_to(0xE58C0, 0x4A7950)[0] and
                e.branch_sites(0x4A7950) == [0xE58C0] and
                (lambda m: m and m['load'] and (m['rn'], m['rt'], m['imm']) == (3, 8, 0x28))(e.mem_imm(0xE5914)) and
                e.blx_reg(0xE591C) == 8 and
                (lambda d: d and d['op'] == 13 and d['imm'] == 0x7D0)(e.dp_imm(0xE592C)) and
                c.bl_plt(0xE5930, 'usleep')[0], 'loop'))
    ck('U4', 'the GUI loop is entered only from UIInit [0xA4468,..) at 0xA454C',
       lambda: (e.branch_sites(0xE582C) == [0xA454C] and e.bounds(0xA454C)[0] == 0xA4468, 'entry'))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--elf', type=Path, default=DEFAULT_ELF)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    if not args.elf.is_file():
        print(f'input not found: {args.elf}', file=sys.stderr)
        return 2
    raw = args.elf.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        print(f'refusing: sha256 {digest} is not the exact stock build', file=sys.stderr)
        return 2
    c = Checker(Elf(raw))
    all_checks(c)
    failed = [r for r in c.results if not r['ok']]
    if args.json:
        print(json.dumps({'sha256': digest, 'checks': len(c.results), 'failed': failed,
                          'results': c.results}, indent=1))
    else:
        for r in c.results:
            print(f"{'PASS' if r['ok'] else 'FAIL'} {r['id']:4} {r['claim']}" + ('' if r['ok'] else f"  ({r['detail']})"))
        print(f'{len(c.results) - len(failed)}/{len(c.results)} checks passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
