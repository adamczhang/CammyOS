#!/usr/bin/env python3
"""Mechanical device-ABI audit of the WP2A status library (offline, read-only).

Checks the built library against the SAVED camera rootfs:
  * ELF32 ARM ET_DYN, EABI5 hard-float flags, no PT_TLS/PT_INTERP, non-exec stack
  * DT_NEEDED exactly ["libc.so"], no RPATH/RUNPATH/TEXTREL, no INIT/FINI/
    INIT_ARRAY/FINI_ARRAY/PREINIT_ARRAY (no constructors or destructors)
  * exported dynamic symbols exactly the expected set
  * every import is exported by the saved musl 1.1.24 libc.so, none is a
    `*time64*` symbol, none is on the forbidden list (exit, exec, ioctl, kill,
    signal installation, dlclose, ...), and none is a TLS or libstdc++/libgcc_s name
  * interposition: in musl's global lookup order for sdvcam (the executable, then
    its DT_NEEDED breadth-first), the FIRST object defining each import (and the
    stub's dlopen/dlsym) is libc.so, not sdvcam or a vendor library
  * relocation types limited to RELATIVE/GLOB_DAT/JUMP_SLOT/ABS32
SPDX-License-Identifier: MIT
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elfinfo import Elf32, PT_TLS, PT_INTERP, PT_GNU_STACK  # noqa: E402

LIBC_SHA256 = '5fea26c4e562809794d33fd9c62304cef68b2db6233a8372bedd31e0baac7f51'
SDVCAM_SHA256 = '8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec'
# Camera search path: LD_LIBRARY_PATH=/usr/lib/eyesee-mpp (etc/init.d/rc.final), then musl's default.
SEARCH_DIRS = ['usr/lib/eyesee-mpp', 'lib', 'usr/local/lib', 'usr/lib']
FORBIDDEN = {
    'exit', '_exit', '_Exit', 'abort', 'raise', 'kill', 'tkill', 'tgkill', 'pthread_kill', 'signal', 'sigaction',
    'execve', 'execv', 'execvp', 'execl', 'execlp', 'execle', 'system', 'popen', 'fork', 'vfork', 'posix_spawn',
    'ioctl', 'mount', 'umount', 'reboot', 'dlclose', 'dlopen', 'pthread_cancel', 'pthread_exit', 'setsid',
    'umask', 'chdir', 'setuid', 'setgid', 'prctl', 'syscall', '__tls_get_addr', '__cxa_atexit', 'atexit',
    'mmap', 'mprotect', 'munmap', 'sigprocmask'}
FORBIDDEN_PREFIXES = ('_Unwind_', '__cxa_', '__gxx_', '_ZN', '_ZS', '__aeabi_', '__gnu_')
# Imports whose arguments include a libc structure that contains time_t.
TIME_BEARING = {'clock_gettime': 'struct timespec', 'lstat': 'struct stat', 'fstat': 'struct stat',
                'stat': 'struct stat', 'fstatat': 'struct stat', 'pthread_timedjoin_np': 'struct timespec',
                'nanosleep': 'struct timespec', 'ppoll': 'struct timespec', 'select': 'struct timeval',
                'gettimeofday': 'struct timeval', 'recvmmsg': 'struct timespec'}
RELOC_OK = {2: 'R_ARM_ABS32', 21: 'R_ARM_GLOB_DAT', 22: 'R_ARM_JUMP_SLOT', 23: 'R_ARM_RELATIVE'}


def find_lib(rootfs, name):
    for d in SEARCH_DIRS:
        p = rootfs / d / name
        if p.exists():
            return p
    return None


def load_order(rootfs, sdvcam):
    """musl 1.1.24 load_deps: walk the DSO list in order, appending each NEEDED once."""
    order = [('sdvcam', sdvcam)]
    names = {'sdvcam'}
    i = 0
    while i < len(order):
        _, elf = order[i]
        for need in elf.needed():
            if need in names:
                continue
            path = find_lib(rootfs, need)
            if path is None:
                raise SystemExit(f'missing library in saved rootfs: {need}')
            names.add(need)
            order.append((need, Elf32(path.read_bytes())))
        i += 1
    return order


def audit(library, rootfs, expected_exports, *, profile='status'):
    # The face service maps only its defined256B private RAM IPC file. Keep
    # status/finite-face audits unchanged; never accept an arbitrary allowlist.
    if profile not in ('status', 'face-service'):
        raise ValueError('Unknown ABI audit profile')
    data = library.read_bytes()
    lib = Elf32(data)
    libc_bytes = (rootfs / 'lib/libc.so').read_bytes()
    sdv_bytes = (rootfs / 'usr/bin/sdvcam').read_bytes()
    failures = []

    def need(cond, msg):
        if not cond:
            failures.append(msg)

    need(hashlib.sha256(libc_bytes).hexdigest() == LIBC_SHA256, 'saved libc.so hash differs')
    need(hashlib.sha256(sdv_bytes).hexdigest() == SDVCAM_SHA256, 'saved sdvcam hash differs')
    need((lib.e_type, lib.e_machine) == (3, 40), 'not an ARM ET_DYN')
    need(lib.e_flags == 0x5000400, 'e_flags %#x is not EABI5 hard-float' % lib.e_flags)
    types = [p['type'] for p in lib.phdrs]
    need(PT_TLS not in types, 'PT_TLS present')
    need(PT_INTERP not in types, 'PT_INTERP present')
    stack = [p for p in lib.phdrs if p['type'] == PT_GNU_STACK]
    need(len(stack) == 1 and not stack[0]['flags'] & 1, 'executable or missing GNU_STACK')
    dyn = lib.dynamic()
    tags = {t for t, _ in dyn}
    need(lib.needed() == ['libc.so'], 'DT_NEEDED is %s' % lib.needed())
    for tag, name in [(15, 'RPATH'), (29, 'RUNPATH'), (22, 'TEXTREL'), (12, 'INIT'), (13, 'FINI'),
                      (25, 'INIT_ARRAY'), (26, 'FINI_ARRAY'), (32, 'PREINIT_ARRAY')]:
        need(tag not in tags, 'DT_%s present' % name)
    flags = [v for t, v in dyn if t == 30]
    need(not flags or not flags[0] & 4, 'DF_TEXTREL set')
    sections = {s['sname'] for s in lib.shdrs}
    need(not sections & {'.init_array', '.fini_array', '.ctors', '.dtors', '.init', '.fini', '.tbss', '.tdata'},
         'constructor/destructor/TLS sections: %s' % sorted(sections & {'.init_array', '.fini_array', '.ctors',
                                                                      '.dtors', '.init', '.fini', '.tbss', '.tdata'}))
    exports = sorted(lib.exported())
    need(exports == sorted(expected_exports), 'exports %s' % exports)
    imports = lib.undefined()
    libc = Elf32(libc_bytes)
    libc_exports = libc.exported()
    need(all(i in libc_exports for i in imports), 'imports missing from saved libc: %s'
         % [i for i in imports if i not in libc_exports])
    need(not [i for i in imports if 'time64' in i], 'time64 imports present')
    admitted = {'mmap', 'munmap'} if profile == 'face-service' else set()
    bad = sorted(i for i in imports if (i in FORBIDDEN and i not in admitted) or i.startswith(FORBIDDEN_PREFIXES))
    need(not bad, 'forbidden imports: %s' % bad)
    tls_syms = [s['name'] for s in lib.dynsyms() if s['type'] == 'TLS']
    need(not tls_syms, 'TLS symbols: %s' % tls_syms)
    relocs = lib.relocations()
    need(all(r['type'] in RELOC_OK for r in relocs), 'unexpected relocation types: %s'
         % sorted({r['type'] for r in relocs if r['type'] not in RELOC_OK}))
    # Interposition in sdvcam's global namespace.
    order = load_order(rootfs, Elf32(sdv_bytes))
    exported_by = [(name, elf.exported()) for name, elf in order]
    first = {}
    for sym in imports + ['dlopen', 'dlsym']:
        first[sym] = next((name for name, ex in exported_by if sym in ex), None)
    interposed = {s: o for s, o in first.items() if o != 'libc.so'}
    need(not interposed, 'interposed or unresolved imports: %s' % interposed)
    need([n for n, _ in order].index('libc.so') >= 1, 'libc not in sdvcam load order')
    return {
        'library': str(library), 'library_sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
        'e_flags': hex(lib.e_flags), 'needed': lib.needed(), 'exports': exports, 'imports': imports,
        'import_count': len(imports), 'time_bearing_imports': {i: TIME_BEARING[i] for i in imports if i in TIME_BEARING},
        'relocation_types': sorted({RELOC_OK.get(r['type'], r['type']) for r in relocs}),
        'sdvcam_lookup_order': [n for n, _ in order], 'libc_position': [n for n, _ in order].index('libc.so'),
        'first_definer_all_libc': not interposed, 'libc_sha256': LIBC_SHA256,
        'failures': failures, 'pass': not failures,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--export', action='append', default=None)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    expected = args.export or ['cammy_native_attach_v1', 'cammy_native_detach_v1']
    report = audit(args.library, args.rootfs, expected)
    text = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(json.dumps({k: report[k] for k in ('library_sha256', 'import_count', 'libc_position', 'pass', 'failures')}))
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
