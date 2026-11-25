#!/usr/bin/env python3
"""Build an OFFLINE status-only sdvcam candidate (WP2A); never deploy or launch it.

Starts from the exact pristine stock sdvcam (no media-event redirects), replaces
the single `bl 0xA4294` at 0x31DB0 with `bl` to a stub in the verified zero cave,
and extends the executable LOAD to cover the stub. The stub:

    push  {r0-r6, lr}                 ; keep r0-r3 for the original, 8-byte aligned
    h = dlopen(LIB_PATH, RTLD_NOW)    ; via the existing dlopen PLT entry
    a = dlsym(h, "cammy_native_attach_v1"); d = dlsym(h, "cammy_native_detach_v1")
    any NULL                -> fallback
    build host record v1 on the stack (abi 1, size 48, profile 1, sha256, r0)
    a(&host) != 0           -> fallback
    r0:r1 = 0xA4294(r0..r3) ; original, returns only at a terminal queue close
    d(1)                    ; EXIT_QUEUE_CLOSED; r0:r1 of the original are kept
    pop   {r0-r6, pc}       ; back to 0x31DB4
  fallback:
    pop   {r0-r6, lr}
    b     0xA4294           ; stock tail: same r0-r3, sp and lr=0x31DB4

The library path is content-addressed by its SHA-256 and baked in at build time:
/tmp/cammy-native-status/<library-sha256>/libcammy_native_status.so. dlopen does not
verify that hash; staging integrity belongs to the activation package (WP4).

Original vendor bytes and candidates stay under ignored private/. Mode 0600.
SPDX-License-Identifier: MIT
"""
import os
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'native/loader'))
from elfinfo import Elf32, PT_LOAD, PT_ARM_EXIDX  # noqa: E402

SOURCE_SHA256 = '8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec'
DEFAULT_SOURCE = ROOT.parent / 'Cammy Bot/M105 investigation/work/firmware-rootfs/usr/bin/sdvcam'
TEXT_BASE = 0x10000
TEXT_PHDR_INDEX = 3
ORIGINAL_LOAD_SIZE = 0x8D2E1C
DATA_LOAD_OFFSET = 0x8D3030
LAST_TEXT_PAGE_END = 0x8D3000       # stub must end inside the already-mapped last text page
CALL_SITE = 0x31DB0                 # main+0x4E8: bl 0xA4294 (single caller)
RESUME = 0x31DB4
ORIGINAL = 0xA4294                  # loads g_task_queue, tail-branches to TaskQueue::Run 0x57F38
DLOPEN_PLT, DLOPEN_GOT = 0x2CA70, 0x8FB43C
DLSYM_PLT, DLSYM_GOT = 0x2D574, 0x8FB7E8
MEDIA_CANDIDATE_RANGE = (0x8E2E20, 0x8E2E38)   # left untouched for later coexistence
STUB_ADDRESS = 0x8E2E40
CAVE_END = 0x8E3030
RTLD_NOW = 2                        # musl value
HOST_RECORD_SIZE = 48
LIB_DIR = '/tmp/cammy-native-status'
LIB_NAME = 'libcammy_native_status.so'
ATTACH_SYMBOL = 'cammy_native_attach_v1'
DETACH_SYMBOL = 'cammy_native_detach_v1'

# Exact stock words checked before patching (static evidence, not hardware proof).
EXPECTED_PHDRS = [
    (0x70000001, 0x8C8CE8, 0x8D8CE8, 0x8D8CE8, 0xA130, 0xA130, 4, 4),
    (6, 0x34, 0x10034, 0x10034, 0x100, 0x100, 5, 4),
    (3, 0x134, 0x10134, 0x10134, 0x18, 0x18, 4, 1),
    (1, 0, 0x10000, 0x10000, ORIGINAL_LOAD_SIZE, ORIGINAL_LOAD_SIZE, 5, 0x10000),
    (1, DATA_LOAD_OFFSET, 0x8F3030, 0x8F3030, 0x1FC040, 0x20B068, 6, 0x10000),
    (2, 0x8DAFE0, 0x8FAFE0, 0x8FAFE0, 0x150, 0x150, 6, 4),
    (0x6474E551, 0, 0, 0, 0, 0, 7, 0x10),
    (0x6474E552, DATA_LOAD_OFFSET, 0x8F3030, 0x8F3030, 0x9FD0, 0x9FD0, 4, 1),
]
# 0xA4294: r0/r1 are never read, no stack argument is read, r2/r3 are scratch.
EXPECTED_ORIGINAL = [0xE59F301C, 0xE24DD008, 0xE59F2018, 0xE08F3003, 0xE7933002,
                     0xE58D3004, 0xE1A00003, 0xE28DD008, 0xEAFECF1F]
MAIN_ADDRESS, MAIN_EXTAB = 0x318C8, 0x8CF924


# ---------------------------------------------------------------- ARM encoders
def _modimm(value):
    for rot in range(16):
        imm8 = ((value << (2 * rot)) | (value >> (32 - 2 * rot))) & 0xFFFFFFFF if rot else value
        if imm8 < 256:
            return (rot << 8) | imm8
    raise ValueError('immediate %#x not encodable' % value)


def _dp_imm_value(word):
    """Value of an ARM data-processing modified immediate (imm8 rotated right by 2*rot)."""
    imm8, rot = word & 0xFF, 2 * ((word >> 8) & 0xF)
    return ((imm8 >> rot) | (imm8 << (32 - rot))) & 0xFFFFFFFF if rot else imm8


def _regs(*regs):
    mask = 0
    for r in regs:
        mask |= 1 << r
    return mask


SP, LR, PC, IP = 13, 14, 15, 12


def enc_branch(source, target, link=False, cond=0xE):
    delta = target - source - 8
    if source % 4 or target % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise ValueError('unaligned/out-of-range ARM branch %#x -> %#x' % (source, target))
    return (cond << 28) | (0b101 << 25) | (int(link) << 24) | ((delta >> 2) & 0xFFFFFF)


def dec_branch(source, word):
    """Return (cond, link, target) for an ARM B/BL, else None."""
    if (word >> 25) & 7 != 0b101 or word >> 28 == 0xF:
        return None
    off = word & 0xFFFFFF
    if off & 0x800000:
        off -= 0x1000000
    return word >> 28, bool(word & (1 << 24)), source + 8 + 4 * off


def enc_dp_imm(opcode, rd, rn, imm, s=False):
    return 0xE2000000 | (opcode << 21) | (int(s) << 20) | (rn << 16) | (rd << 12) | _modimm(imm)


def enc_adr(rd, source, target):
    delta = target - (source + 8)
    if delta < 0:
        raise ValueError('data must follow code')
    return enc_dp_imm(0b0100, rd, PC, delta)          # add rd, pc, #delta


def build_stub(library_sha256, address=STUB_ADDRESS):
    """Assemble the stub. Returns (bytes, labels). Two passes resolve labels."""
    library_sha256 = library_sha256.lower()
    if len(library_sha256) != 64 or any(c not in '0123456789abcdef' for c in library_sha256):
        raise ValueError('library SHA-256 must be 64 hex digits')
    path = f'{LIB_DIR}/{library_sha256}/{LIB_NAME}'.encode()

    def program(L):
        a = address
        code = []

        def emit(fn):
            code.append(fn)

        emit(lambda pc: 0xE92D0000 | _regs(0, 1, 2, 3, 4, 5, 6, LR))           # push {r0-r6, lr}
        emit(lambda pc: enc_adr(0, pc, L['path']))                            # adr r0, path
        emit(lambda pc: enc_dp_imm(0b1101, 1, 0, RTLD_NOW))                    # mov r1, #RTLD_NOW
        emit(lambda pc: enc_branch(pc, DLOPEN_PLT, link=True))                 # bl dlopen@plt
        emit(lambda pc: enc_dp_imm(0b0010, 5, 0, 0, s=True))                   # subs r5, r0, #0
        emit(lambda pc: enc_branch(pc, L['fallback'], cond=0x0))               # beq fallback
        emit(lambda pc: enc_adr(1, pc, L['sym_attach']))                       # adr r1, attach name
        emit(lambda pc: enc_branch(pc, DLSYM_PLT, link=True))                  # bl dlsym@plt (r0 = handle)
        emit(lambda pc: enc_dp_imm(0b0010, 6, 0, 0, s=True))                   # subs r6, r0, #0
        emit(lambda pc: enc_branch(pc, L['fallback'], cond=0x0))               # beq fallback
        emit(lambda pc: 0xE1A00005)                                            # mov r0, r5
        emit(lambda pc: enc_adr(1, pc, L['sym_detach']))                       # adr r1, detach name
        emit(lambda pc: enc_branch(pc, DLSYM_PLT, link=True))                  # bl dlsym@plt
        emit(lambda pc: enc_dp_imm(0b0010, 5, 0, 0, s=True))                   # subs r5, r0, #0
        emit(lambda pc: enc_branch(pc, L['fallback'], cond=0x0))               # beq fallback
        emit(lambda pc: enc_dp_imm(0b0010, SP, SP, HOST_RECORD_SIZE))          # sub sp, sp, #48
        emit(lambda pc: enc_dp_imm(0b1101, 0, 0, 1))                           # mov r0, #1 (abi)
        emit(lambda pc: enc_dp_imm(0b1101, 1, 0, HOST_RECORD_SIZE))            # mov r1, #48 (size)
        emit(lambda pc: enc_dp_imm(0b1101, 2, 0, 1))                           # mov r2, #1 (status-only)
        emit(lambda pc: 0xE8800000 | (SP << 16) | _regs(0, 1, 2))              # stm sp, {r0-r2}
        emit(lambda pc: enc_adr(1, pc, L['hash']))                             # adr r1, source hash
        emit(lambda pc: enc_dp_imm(0b0100, 0, SP, 12))                         # add r0, sp, #12
        for _ in range(2):
            emit(lambda pc: 0xE8B00000 | (1 << 16) | _regs(2, 3, IP, LR))      # ldm r1!, {r2,r3,ip,lr}
            emit(lambda pc: 0xE8A00000 | (0 << 16) | _regs(2, 3, IP, LR))      # stm r0!, {r2,r3,ip,lr}
        emit(lambda pc: 0xE5900000 | (SP << 16) | (2 << 12) | HOST_RECORD_SIZE)  # ldr r2, [sp, #48] (saved r0)
        emit(lambda pc: 0xE5800000 | (0 << 16) | (2 << 12))                   # str r2, [r0] (main_module)
        emit(lambda pc: 0xE1A0000D)                                            # mov r0, sp
        emit(lambda pc: 0xE12FFF36)                                            # blx r6 (attach)
        emit(lambda pc: enc_dp_imm(0b0100, SP, SP, HOST_RECORD_SIZE))          # add sp, sp, #48
        emit(lambda pc: enc_dp_imm(0b1010, 0, 0, 0, s=True))                   # cmp r0, #0
        emit(lambda pc: enc_branch(pc, L['fallback'], cond=0x1))               # bne fallback
        emit(lambda pc: 0xE8900000 | (SP << 16) | _regs(0, 1, 2, 3))           # ldm sp, {r0-r3}
        emit(lambda pc: enc_branch(pc, ORIGINAL, link=True))                   # bl original
        emit(lambda pc: 0xE8800000 | (SP << 16) | _regs(0, 1))                 # stm sp, {r0, r1}
        emit(lambda pc: enc_dp_imm(0b1101, 0, 0, 1))                           # mov r0, #1 (EXIT_QUEUE_CLOSED)
        emit(lambda pc: 0xE12FFF35)                                            # blx r5 (detach)
        emit(lambda pc: 0xE8BD0000 | _regs(0, 1, 2, 3, 4, 5, 6, PC))           # pop {r0-r6, pc}
        fallback = a + 4 * len(code)
        emit(lambda pc: 0xE8BD0000 | _regs(0, 1, 2, 3, 4, 5, 6, LR))           # fallback: pop {r0-r6, lr}
        emit(lambda pc: enc_branch(pc, ORIGINAL))                              # b original (tail)
        words = b''.join(struct.pack('<I', fn(a + 4 * i)) for i, fn in enumerate(code))
        labels = {'fallback': fallback, 'code_end': a + len(words)}
        blob = bytearray(words)

        def put(name, data):
            while len(blob) % 4:
                blob.append(0)
            labels[name] = a + len(blob)
            blob.extend(data)

        put('hash', bytes.fromhex(SOURCE_SHA256))
        put('path', path + b'\0')
        put('sym_attach', ATTACH_SYMBOL.encode() + b'\0')
        put('sym_detach', DETACH_SYMBOL.encode() + b'\0')
        while len(blob) % 4:
            blob.append(0)
        labels['end'] = a + len(blob)
        return bytes(blob), labels

    placeholder = {k: address + 0x100 for k in ('path', 'sym_attach', 'sym_detach', 'hash', 'fallback')}
    _, labels = program(placeholder)
    blob, labels2 = program(labels)
    if labels2 != labels:
        raise AssertionError('stub label resolution did not converge')
    return blob, labels


# ---------------------------------------------------------------- guards
def check_source(original):
    """Raise unless `original` is the exact stock image with every assumption intact.
    Returns the static evidence that was checked."""
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA256:
        raise ValueError('Exact preserved M105 sdvcam hash required')
    elf = Elf32(original)
    if (elf.e_type, elf.e_machine, elf.e_flags, elf.e_entry) != (2, 40, 0x5000400, 0x334A8):
        raise ValueError('Expected ARM EABI5 hard-float ET_EXEC with stock entry')
    phdrs = [tuple(p.values()) for p in elf.phdrs]
    if phdrs[:len(EXPECTED_PHDRS)] != EXPECTED_PHDRS or len(phdrs) != len(EXPECTED_PHDRS):
        raise ValueError('Program header layout differs')
    if any(original[ORIGINAL_LOAD_SIZE:DATA_LOAD_OFFSET]):
        raise ValueError('Code cave is not zero')
    if struct.unpack_from('<I', original, CALL_SITE - TEXT_BASE)[0] != enc_branch(CALL_SITE, ORIGINAL, link=True):
        raise ValueError('Call site 0x31DB0 is not bl 0xA4294')
    words = list(struct.unpack_from('<9I', original, ORIGINAL - TEXT_BASE))
    if words != EXPECTED_ORIGINAL:
        raise ValueError('0xA4294 body differs')
    if dec_branch(ORIGINAL + 32, words[8])[2] != 0x57F38:
        raise ValueError('0xA4294 does not tail-branch to TaskQueue::Run')
    # Exactly one direct branch reaches 0xA4294 (its only caller is main).
    callers = []
    text = original[:ORIGINAL_LOAD_SIZE]
    for off in range(0, len(text) - 3, 4):
        w = struct.unpack_from('<I', text, off)[0]
        b = dec_branch(off + TEXT_BASE, w)
        if b and b[2] == ORIGINAL:
            callers.append(off + TEXT_BASE)
    if callers != [CALL_SITE]:
        raise ValueError('Unexpected direct callers of 0xA4294: %s' % [hex(c) for c in callers])
    # PLT entries resolve through GOT slots carrying R_ARM_JUMP_SLOT for the expected symbol.
    slots = {r['offset']: r for r in elf.relocations() if r['section'] == '.rel.plt'}
    for plt, got, name in [(DLOPEN_PLT, DLOPEN_GOT, 'dlopen'), (DLSYM_PLT, DLSYM_GOT, 'dlsym')]:
        w0, w1, w2 = struct.unpack_from('<3I', original, plt - TEXT_BASE)
        if w0 != 0xE28FC608 or w1 & 0xFFFFF000 != 0xE28CC000 or w2 & 0xFFFFF000 != 0xE5BCF000:
            raise ValueError('Unexpected PLT entry shape for ' + name)
        target = plt + 8 + _dp_imm_value(w0) + _dp_imm_value(w1) + (w2 & 0xFFF)
        if target != got or slots.get(got, {}).get('symbol') != name or slots[got]['type'] != 22:
            raise ValueError('PLT/GOT/JUMP_SLOT mismatch for ' + name)
    # Unwinding: the last EXIDX entry is CANTUNWIND and starts before the cave, so an
    # exception unwinding through the stub frame stops (terminate), as stock would
    # via main's catch-free LSDA.
    ex = next(p for p in elf.phdrs if p['type'] == PT_ARM_EXIDX)
    last = ex['offset'] + ex['filesz'] - 8
    fn_rel, last_word = struct.unpack_from('<II', original, last)
    rel = fn_rel & 0x7FFFFFFF
    fn = ex['vaddr'] + ex['filesz'] - 8 + (rel - 0x80000000 if rel & 0x40000000 else rel)  # prel31
    if last_word != 1 or not fn < STUB_ADDRESS:
        raise ValueError('Last EXIDX entry is not CANTUNWIND before the cave')
    # main's LSDA has no type table (TTYPE encoding omit): no catch clause in main.
    w1 = elf.u32(MAIN_EXTAB + 4)
    lsda = MAIN_EXTAB + 8 + 4 * ((w1 >> 24) & 0xFF)
    lp_enc, tt_enc = original[elf.vaddr_to_offset(lsda)], original[elf.vaddr_to_offset(lsda) + 1]
    if lp_enc != 0xFF or tt_enc != 0xFF:
        raise ValueError('main LSDA has a type table; exception behaviour would differ')
    return {
        'call_site': hex(CALL_SITE), 'original': hex(ORIGINAL), 'original_direct_callers': [hex(CALL_SITE)],
        'original_reads_r0_r3_or_stack_args': False, 'original_tail': '0x57f38',
        'plt': {'dlopen': [hex(DLOPEN_PLT), hex(DLOPEN_GOT)], 'dlsym': [hex(DLSYM_PLT), hex(DLSYM_GOT)]},
        'last_exidx': {'function': hex(fn), 'cantunwind': True}, 'main_lsda_type_table': 'omitted',
        'cave_zero': [hex(ORIGINAL_LOAD_SIZE), hex(DATA_LOAD_OFFSET)],
    }


def patch_image(original, library_sha256):
    evidence = check_source(original)
    stub, labels = build_stub(library_sha256)
    start_off = STUB_ADDRESS - TEXT_BASE
    end_off = start_off + len(stub)
    if not (ORIGINAL_LOAD_SIZE <= MEDIA_CANDIDATE_RANGE[1] - TEXT_BASE <= start_off and end_off <= LAST_TEXT_PAGE_END):
        raise ValueError('Stub does not fit the cave inside the last mapped text page')
    patched = bytearray(original)
    struct.pack_into('<I', patched, CALL_SITE - TEXT_BASE, enc_branch(CALL_SITE, STUB_ADDRESS, link=True))
    patched[start_off:end_off] = stub
    load_header = 52 + 32 * TEXT_PHDR_INDEX
    struct.pack_into('<II', patched, load_header + 16, end_off, end_off)
    allowed = [(CALL_SITE - TEXT_BASE, 4), (start_off, len(stub)), (load_header + 16, 8)]
    patched = bytes(patched)
    verify_candidate(original, patched, library_sha256, allowed, labels)
    manifest = {
        'kind': 'sdvcam-status-only-candidate', 'package': 'WP2A', 'experimental': True,
        'deployed': False, 'hardware_deployed': False, 'tf_release_included': False,
        'media_event_modifications': 0,
        'source_sha256': SOURCE_SHA256, 'candidate_sha256': hashlib.sha256(patched).hexdigest(),
        'library_sha256': library_sha256, 'library_path': f'{LIB_DIR}/{library_sha256}/{LIB_NAME}',
        'stub': {'address': hex(STUB_ADDRESS), 'bytes': len(stub), 'sha256': hashlib.sha256(stub).hexdigest(),
                 'labels': {k: hex(v) for k, v in labels.items()}},
        'call_site': {'address': hex(CALL_SITE), 'from': 'bl 0xa4294', 'to': 'bl %#x' % STUB_ADDRESS},
        'text_load_size': {'from': hex(ORIGINAL_LOAD_SIZE), 'to': hex(end_off)},
        'allowed_modified_ranges': [{'offset': hex(s), 'bytes': n} for s, n in allowed],
        'static_evidence': evidence,
        'scope': 'Offline status-only candidate. Not staged, not deployed, not on any TF release. '
                 'Activation, staging integrity and hardware acceptance are separate gates.',
    }
    return patched, manifest


def verify_candidate(original, patched, library_sha256, allowed, labels):
    if len(patched) != len(original):
        raise AssertionError('size changed')
    for i, (a, b) in enumerate(zip(original, patched)):
        if a != b and not any(s <= i < s + n for s, n in allowed):
            raise AssertionError('byte %#x changed outside allowed ranges' % i)
    o, p = Elf32(original), Elf32(patched)
    if (o.e_entry, o.e_phoff, o.e_shoff, o.e_flags, o.e_shnum) != (p.e_entry, p.e_phoff, p.e_shoff, p.e_flags, p.e_shnum):
        raise AssertionError('ELF header changed')
    for i, (a, b) in enumerate(zip(o.phdrs, p.phdrs)):
        if i == TEXT_PHDR_INDEX:
            if {k: v for k, v in a.items() if k not in ('filesz', 'memsz')} != \
               {k: v for k, v in b.items() if k not in ('filesz', 'memsz')}:
                raise AssertionError('text LOAD changed beyond its size')
            if not (b['filesz'] == b['memsz'] == labels['end'] - TEXT_BASE <= LAST_TEXT_PAGE_END < DATA_LOAD_OFFSET):
                raise AssertionError('text LOAD bounds wrong')
        elif a != b:
            raise AssertionError('program header %d changed' % i)
    if o.shdrs != p.shdrs or o.dynamic() != p.dynamic():
        raise AssertionError('sections or dynamic changed')
    site = dec_branch(CALL_SITE, struct.unpack_from('<I', patched, CALL_SITE - TEXT_BASE)[0])
    if site != (0xE, True, STUB_ADDRESS):
        raise AssertionError('call site does not bl the stub')
    targets = []
    for va in range(STUB_ADDRESS, labels['code_end'], 4):
        b = dec_branch(va, struct.unpack_from('<I', patched, va - TEXT_BASE)[0])
        if b:
            targets.append((va, b))
    allowed_targets = {DLOPEN_PLT, DLSYM_PLT, ORIGINAL, labels['fallback']}
    if any(t not in allowed_targets for _, (_, _, t) in targets):
        raise AssertionError('stub branches outside the allowed set')
    calls = [(c, t) for _, (c, link, t) in targets if link]
    if calls != [(0xE, DLOPEN_PLT), (0xE, DLSYM_PLT), (0xE, DLSYM_PLT), (0xE, ORIGINAL)]:
        raise AssertionError('unexpected stub call sequence')
    tail = dec_branch(labels['code_end'] - 4, struct.unpack_from('<I', patched, labels['code_end'] - 4 - TEXT_BASE)[0])
    if tail != (0xE, False, ORIGINAL):
        raise AssertionError('fallback does not tail-branch to the original')
    path = f'{LIB_DIR}/{library_sha256}/{LIB_NAME}'.encode() + b'\0'
    off = labels['path'] - TEXT_BASE
    if patched[off:off + len(path)] != path:
        raise AssertionError('library path not embedded')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--library', type=Path, required=True, help='built status library; its SHA-256 is baked in')
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args()
    subprocess.run(['findmnt', '-n', os.environ.get('CAMMY_WORK_MOUNT', '/')], check=True, stdout=subprocess.DEVNULL)
    library_sha = hashlib.sha256(args.library.read_bytes()).hexdigest()
    output = (args.output or ROOT / f'private/ownership-program/wp2a/candidate/sdvcam-status-{library_sha[:12]}').resolve()
    if not output.is_relative_to((ROOT / 'private').resolve()) or output == args.source.resolve():
        parser.error('Output must be a new file under this repository private/')
    report = output.with_suffix('.json')
    if output.suffix == '.json' or output.exists() or report.exists():
        parser.error('Refusing existing candidate/report (or a .json candidate name)')
    patched, manifest = patch_image(args.source.read_bytes(), library_sha)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with output.open('xb') as f:
        f.write(patched)
    output.chmod(0o600)  # deliberately not executable or launchable by this builder
    with report.open('x') as f:
        f.write(json.dumps(manifest, indent=2) + '\n')
    report.chmod(0o600)
    print(json.dumps({'output': str(output), 'candidate_sha256': manifest['candidate_sha256'],
                      'library_sha256': library_sha, 'deployed': False}))


if __name__ == '__main__':
    main()
