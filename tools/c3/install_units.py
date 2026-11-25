#!/usr/bin/env python3
"""Install CammyOS's PC services and the CCC launcher for this checkout.

The unit files in the repository are templates: @ROOT@ is this checkout, @MOUNT@ an
optional drive that must be mounted first, @ENV@ optional Environment= lines. This
writes real files to ~/.config/systemd/user and ~/.local/share/applications, then
reloads systemd. It never enables, starts or stops a service.

  python3 tools/c3/install_units.py                       # all of them
  python3 tools/c3/install_units.py cammy-c3 --env CAMMY_SPOTTER_MODEL=/models/ggml-tiny.en.bin
  python3 tools/c3/install_units.py --mount /mnt/work --dry-run

--mount defaults to $CAMMY_WORK_MOUNT; without either, no mount check is written.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNITS = Path.home() / '.config/systemd/user'
APPS = Path.home() / '.local/share/applications'
TEMPLATES = {
    'cammy-c3': (ROOT / 'tools/c3/cammy-c3.service', UNITS / 'cammy-c3.service'),
    'cammy-calendar-broker': (ROOT / 'tools/c3/cammy-calendar-broker.service', UNITS / 'cammy-calendar-broker.service'),
    'cammy-host': (ROOT / 'systemd/cammy-host.service', UNITS / 'cammy-host.service'),
    'cammy-agent': (ROOT / 'scripts/systemd/cammy-agent.service', UNITS / 'cammy-agent.service'),
    'desktop': (ROOT / 'tools/c3/cammy-c3.desktop', APPS / 'cammy-c3.desktop'),
}


def render(text, root=ROOT, mount=None, env=()):
    """Fill a template. Lines naming @MOUNT@ are dropped when there is no mount."""
    lines = []
    for line in text.splitlines():
        if line == '@ENV@':
            lines += [f'Environment={item}' for item in env]
            continue
        if '@MOUNT@' in line:
            if not mount:
                continue
            line = line.replace('@MOUNT@', str(mount))
        lines.append(line.replace('@ROOT@', str(root)))
    out = '\n'.join(lines) + '\n'
    for mark in ('@ROOT@', '@MOUNT@', '@ENV@'):
        if mark in out:
            raise ValueError(f'unfilled placeholder {mark}')
    return out


def install(name, *, mount=None, env=(), dry_run=False):
    source, target = TEMPLATES[name]
    text = render(source.read_text(), mount=mount, env=env)
    if dry_run:
        print(f'--- {target}\n{text}', end='')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()   # older installs linked straight to the template
    target.write_text(text)
    target.chmod(0o644)
    return target


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('names', nargs='*', help=f'any of {", ".join(TEMPLATES)} (default: all)')
    ap.add_argument('--mount', default=os.environ.get('CAMMY_WORK_MOUNT'))
    ap.add_argument('--env', action='append', default=[], metavar='KEY=VALUE',
                    help='Environment= line for the C3 and host services (repeatable)')
    ap.add_argument('--dry-run', action='store_true', help='print the files instead of writing them')
    args = ap.parse_args(argv)
    for name in args.names:
        if name not in TEMPLATES:
            ap.error(f'unknown name {name!r}; choose from {", ".join(TEMPLATES)}')
    for item in args.env:
        if '=' not in item or '\n' in item:
            ap.error(f'--env needs KEY=VALUE: {item!r}')
    for name in args.names or TEMPLATES:
        print(install(name, mount=args.mount, env=args.env, dry_run=args.dry_run))
    if not args.dry_run:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
