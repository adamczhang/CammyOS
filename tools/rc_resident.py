#!/usr/bin/env python3
"""Make the installed CammyOS release resident (Adam approved, 2026-11-16).

Without /etc/cammy/native-select.resident the face sdvcam runs once per install: the
next boot observes the used claim and starts stock (selector "post-attempt-boot"), and
that stock boot latches startup.pending (runtime_health_fault). With the record, health.sh
re-arms the next boot itself after 120 s healthy at home (scripts/rc06/health.sh).

    .venv-live/bin/python tools/rc_resident.py check     # read-only
    .venv-live/bin/python tools/rc_resident.py apply     # needs Adam's approval each time

apply: PC copies first (private/rc06/resident-<time>/), then on the camera: the state dir
moves to native-select.archived-<time>, startup.pending is copied to
startup.pending.archived-<boot8> and removed, and the resident record is written.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rc_install as I  # noqa: E402

RESIDENT = '/etc/cammy/native-select.resident'
PENDING = '/etc/cammy/startup.pending'


def release_id():
    conf = I.sh('cat /tmp/cammy-runtime/native-release.conf')
    fields = dict(line.split('=', 1) for line in conf.splitlines() if '=' in line)
    rid = fields.get('release_id', '')
    if len(rid) != 24 or any(ch not in '0123456789abcdef' for ch in rid):
        raise SystemExit('ABORT: no valid release_id in native-release.conf')
    if not I.remote_exists('/mnt/extsd/CammyOS-releases/%s/native/arm' % rid):
        raise SystemExit('ABORT: release %s has no arm record on the card' % rid)
    return rid, fields.get('version', '?')


def check():
    rid, version = release_id()
    state = I.sh('ls %s 2>/dev/null || true' % I.STATE).split()
    point = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'boot_id': I.sh('cat /proc/sys/kernel/random/boot_id').strip(),
             'release_id': rid, 'version': version, 'state_dir': state,
             'startup_pending': I.remote_exists(PENDING), 'resident': I.remote_exists(RESIDENT)}
    print(json.dumps(point, indent=1))
    return point


def apply():
    point = check()
    work = I.ROOT / 'private/rc06' / ('resident-' + time.strftime('%Y%m%d-%H%M%S'))
    work.mkdir(parents=True, mode=0o700)
    for name in point['state_dir']:
        (work / ('state-' + name)).write_bytes(I.pull('%s/%s' % (I.STATE, name)))
    if point['startup_pending']:
        (work / 'startup.pending').write_bytes(I.pull(PENDING))
    (work / 'checkpoint.json').write_text(json.dumps(point, indent=1) + '\n')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    if point['state_dir']:
        archive = '/etc/cammy/native-select.archived-%s' % stamp
        I.sh('mkdir -p %s && chmod 700 %s && mv %s/* %s/ && sync' % (archive, archive, I.STATE, archive))
        print('archived %s -> %s' % (point['state_dir'], archive))
    if point['startup_pending']:
        tag = point['boot_id'][:8]
        I.sh('cp -p %s %s.archived-%s && rm %s && sync' % (PENDING, PENDING, tag, PENDING))
        print('archived and cleared startup.pending (copy: startup.pending.archived-%s)' % tag)
    record = 'format=cammy-resident/1\nrelease_id=%s\n' % point['release_id']
    I.sh("umask 077 && printf '%s' > %s.new && mv %s.new %s && sync" % (record.replace('\n', '\\n'), RESIDENT, RESIDENT, RESIDENT))
    if I.pull(RESIDENT).decode() != record:
        raise SystemExit('ABORT: resident record differs after write')
    after = {'state_dir': I.sh('ls %s 2>/dev/null || true' % I.STATE).split(),
             'startup_pending': I.remote_exists(PENDING), 'resident': record}
    (work / 'after.json').write_text(json.dumps(after, indent=1) + '\n')
    print('resident OK for %s (%s); PC record %s' % (point['release_id'], point['version'], work))


if __name__ == '__main__':
    {'check': check, 'apply': apply}[sys.argv[1] if len(sys.argv) > 1 else 'check']()
