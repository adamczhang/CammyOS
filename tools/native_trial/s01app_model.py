"""What stock S01app does right after the selector (WP5 finding F2). Model only.

Summarised from the private live init text (private/native-start/
live-init-scripts.txt, S01app ``start``); no vendor text is reproduced here. In
every branch S01app finally runs ``/usr/bin/sdvcam &``, so it executes whatever
that path shows. Its branch keys on ``grep '\\<vfat\\>' /proc/mounts``:

* no vfat line: launch only;
* a vfat line anywhere (for example the selector's private ``/run/cammy-select/tf``
  mount after a failed detach): the backup branch, which may write
  ``/data/app_md5`` (md5sum of the bound file) and copy ``/usr/bin/sdvcam`` to
  ``/mnt/extsd/.sdvcam``. When ``/mnt/extsd`` is not itself a mount point (it is
  mounted later, by S10udev), that copy lands in the INTERNAL overlay directory,
  subject to S01app's own du/df free-space comparison.

``effects`` reports which bytes would run and which durable writes may happen.
"""
from __future__ import annotations

import re

_VFAT = re.compile(r'(?<![A-Za-z0-9_])vfat(?![A-Za-z0-9_])')
EXTSD = '/mnt/extsd'


def effects(mounts: list[str], *, app_executable: bool, app_md5: str, stock_md5: str,
            verified_candidate_md5: str | None, firstboot: bool = False,
            card_backup_exists: bool = False, root: str = '') -> dict:
    """Branch, executed bytes and possible durable writes of stock S01app."""
    vfat = any(_VFAT.search(line) for line in mounts)
    extsd_is_mount = any(len(f) > 1 and f[1] == f'{root}{EXTSD}' for f in (line.split() for line in mounts))
    writes: list[str] = []
    if vfat:
        branch = 'backup'
        copy_target = 'card' if extsd_is_mount else 'internal-overlay'
        if firstboot or not card_backup_exists:
            writes += ['/data/app_md5 (md5 of the bound file)',
                       f'/mnt/extsd/.sdvcam -> {copy_target} (if du <= df free)']
        else:
            writes.append('/mnt/extsd/.sdvcam may be removed on md5 mismatch')
        if firstboot:
            writes += ['/overlay/.firstboot removed', '/mnt/extsd/isp*.bin removed']
    else:
        branch = 'launch-only'
    if not app_executable:
        runs = 'none (exec fails; no app until power cycle)'
    elif app_md5 == stock_md5:
        runs = 'stock'
    elif verified_candidate_md5 and app_md5 == verified_candidate_md5:
        runs = 'verified-candidate'
    else:
        runs = 'UNVERIFIED'
    return {'branch': branch, 'runs': runs, 'durable_writes': writes,
            'fail_closed': runs in ('stock', 'verified-candidate') or runs.startswith('none'),
            'no_durable_side_effect': not writes}
