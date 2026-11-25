#!/usr/bin/env python3
"""Build the 0.5.0-diag1 TF payload: verified 0.5.0 plus TF diagnostics only.

Copies dist/staged-0.5.0/CammyOS, replaces startup.sh and native-media-start.sh
with the instrumented scripts from scripts/, and regenerates SHA256SUMS and
manifest.json. Every other file must stay byte-identical to 0.5.0, and the two
scripts must differ from 0.5.0 only by added lines. Never touches a card or the
camera. PLAN milestone 1.
"""
import difflib
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'dist/staged-0.5.0/CammyOS'
OUT = ROOT / 'dist/staged-0.5.0-diag2/CammyOS'
REPLACE = {'startup.sh': 'scripts/tf-runtime-startup.sh',
           'native-media-start.sh': 'scripts/native-media-start.sh'}
FLAG = 'cammy-diag-v1\n'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def additions_only(old, new):
    for op in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if op[0] in ('replace', 'delete'):
            removed = old[op[1]:op[2]]
            # status() gained a note call; that is the only permitted line edit.
            if removed != ["status(){ printf '%s\\n' \"$1\" >\"$R/native-video.state\"; }\n"]:
                return False
    return True


def build():
    sums = {l.split()[1]: l.split()[0] for l in (BASE / 'SHA256SUMS').read_text().splitlines()}
    for name, digest in sums.items():
        if sha(BASE / name) != digest:
            raise SystemExit('0.5.0 base does not verify: %s' % name)
    if OUT.parent.exists():
        shutil.rmtree(OUT.parent)
    shutil.copytree(BASE, OUT, copy_function=shutil.copy2)
    for name, src in REPLACE.items():
        old = (BASE / name).read_text().splitlines(keepends=True)
        new = (ROOT / src).read_text().splitlines(keepends=True)
        if not additions_only(old, new):
            raise SystemExit('%s changes more than diagnostics' % name)
        shutil.copy2(ROOT / src, OUT / name)
        (OUT / name).chmod((BASE / name).stat().st_mode & 0o777)
    manifest = json.loads((BASE / 'manifest.json').read_text())
    manifest['version'] = '0.5.0-diag2'
    manifest['diagnostics'] = {'flag_file': 'enable-diag', 'flag_value': FLAG.strip(),
                               'log_dir': 'CammyOS-diag'}
    for name in REPLACE:
        manifest['files'][name] = sha(OUT / name)
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True) + '\n')
    lines = []
    for name in sums:
        lines.append('%s  %s\n' % (sha(OUT / name), name))
    (OUT / 'SHA256SUMS').write_text(''.join(lines))
    (OUT.parent / 'enable-diag').write_text(FLAG)
    changed = sorted(n for n in sums if sha(OUT / n) != sums[n])
    print('Built %s; changed vs 0.5.0: %s. Flag file: %s. No card or camera written.'
          % (OUT, ', '.join(changed), OUT.parent / 'enable-diag'))
    return OUT


if __name__ == '__main__':
    build()
