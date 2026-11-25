"""Minimal read-only ELF32 little-endian parser for the WP2A loader proofs.

Standard library only, so the candidate builder and ABI audit need no
pyelftools/capstone. It reads headers, program/section headers, the dynamic
section, dynamic symbols and REL relocations. It never executes or writes input.
SPDX-License-Identifier: MIT
"""
import struct

PT_LOAD, PT_DYNAMIC, PT_INTERP, PT_TLS, PT_GNU_STACK, PT_ARM_EXIDX = 1, 2, 3, 7, 0x6474E551, 0x70000001
SHT_DYNSYM, SHT_REL = 11, 9
DT = {1: 'NEEDED', 12: 'INIT', 13: 'FINI', 14: 'SONAME', 15: 'RPATH', 22: 'TEXTREL', 25: 'INIT_ARRAY',
      26: 'FINI_ARRAY', 27: 'INIT_ARRAYSZ', 28: 'FINI_ARRAYSZ', 29: 'RUNPATH', 30: 'FLAGS', 32: 'PREINIT_ARRAY',
      23: 'JMPREL', 17: 'REL', 5: 'STRTAB', 6: 'SYMTAB', 0x6FFFFFFB: 'FLAGS_1', 24: 'BIND_NOW'}
STB = {0: 'LOCAL', 1: 'GLOBAL', 2: 'WEAK', 10: 'GNU_UNIQUE'}
STT = {0: 'NOTYPE', 1: 'OBJECT', 2: 'FUNC', 3: 'SECTION', 4: 'FILE', 5: 'COMMON', 6: 'TLS', 10: 'IFUNC'}


class ElfError(ValueError):
    pass


class Elf32:
    def __init__(self, data):
        self.data = bytes(data)
        d = self.data
        if d[:4] != b'\x7fELF' or d[4] != 1 or d[5] != 1:
            raise ElfError('not ELF32 little-endian')
        (self.e_type, self.e_machine, _, self.e_entry, self.e_phoff, self.e_shoff, self.e_flags,
         _, self.e_phentsize, self.e_phnum, self.e_shentsize, self.e_shnum,
         self.e_shstrndx) = struct.unpack_from('<HHIIIIIHHHHHH', d, 16)
        self.phdrs = [dict(zip(('type', 'offset', 'vaddr', 'paddr', 'filesz', 'memsz', 'flags', 'align'),
                               struct.unpack_from('<8I', d, self.e_phoff + i * 32))) for i in range(self.e_phnum)]
        self.shdrs = []
        if self.e_shoff:
            for i in range(self.e_shnum):
                v = struct.unpack_from('<10I', d, self.e_shoff + i * 40)
                self.shdrs.append(dict(zip(('name', 'type', 'flags', 'addr', 'offset', 'size', 'link', 'info',
                                            'addralign', 'entsize'), v)))
            strtab = self.shdrs[self.e_shstrndx]
            for s in self.shdrs:
                s['sname'] = self.cstr(strtab['offset'] + s['name'])

    def cstr(self, off):
        end = self.data.index(b'\0', off)
        return self.data[off:end].decode('latin-1')

    def vaddr_to_offset(self, va):
        for p in self.phdrs:
            if p['type'] == PT_LOAD and p['vaddr'] <= va < p['vaddr'] + p['filesz']:
                return va - p['vaddr'] + p['offset']
        raise ElfError('address %#x not file-backed' % va)

    def u32(self, va):
        return struct.unpack_from('<I', self.data, self.vaddr_to_offset(va))[0]

    def section(self, name):
        for s in self.shdrs:
            if s['sname'] == name:
                return s
        return None

    def dynamic(self):
        out = []
        for p in self.phdrs:
            if p['type'] != PT_DYNAMIC:
                continue
            for i in range(p['filesz'] // 8):
                tag, val = struct.unpack_from('<iI', self.data, p['offset'] + 8 * i)
                if tag == 0:
                    break
                out.append((tag & 0xFFFFFFFF, val))
        return out

    def dynstr(self, val):
        strtab = [v for t, v in self.dynamic() if t == 5][0]
        return self.cstr(self.vaddr_to_offset(strtab + val))

    def needed(self):
        return [self.dynstr(v) for t, v in self.dynamic() if t == 1]

    def dynsyms(self):
        sec = next((s for s in self.shdrs if s['type'] == SHT_DYNSYM), None)
        if sec is None:
            raise ElfError('no .dynsym section header')
        strsec = self.shdrs[sec['link']]
        out = []
        for i in range(sec['size'] // 16):
            name, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', self.data, sec['offset'] + 16 * i)
            out.append({'index': i, 'name': self.cstr(strsec['offset'] + name), 'value': value, 'size': size,
                        'bind': STB.get(info >> 4, info >> 4), 'type': STT.get(info & 15, info & 15),
                        'visibility': other & 3, 'shndx': shndx})
        return out

    def exported(self):
        """Names a dynamic linker can bind to (defined, global/weak/unique, default or protected)."""
        return {s['name'] for s in self.dynsyms()
                if s['shndx'] != 0 and s['bind'] in ('GLOBAL', 'WEAK', 'GNU_UNIQUE') and s['visibility'] in (0, 3)
                and s['name'] and s['type'] != 'TLS'}

    def undefined(self):
        return sorted({s['name'] for s in self.dynsyms() if s['shndx'] == 0 and s['name']})

    def relocations(self):
        syms = self.dynsyms()
        out = []
        for s in self.shdrs:
            if s['type'] != SHT_REL:
                continue
            for i in range(s['size'] // 8):
                off, info = struct.unpack_from('<II', self.data, s['offset'] + 8 * i)
                out.append({'section': s['sname'], 'offset': off, 'type': info & 0xFF,
                            'symbol': syms[info >> 8]['name'] if info >> 8 else ''})
        return out
