"""Pure generators that make the TF runtime run only RAM bytes it has verified.

Audit concern (2026-11-17): the 0.7.9 bootstrap md5-checked three files on the TF
card, then copied/executed them from the card again, and the runtime copied the
payload to RAM before checking the *card* copies against an unpinned manifest. A
card that returns different bytes on a second read (or a swapped manifest) could
run unverified code. These helpers move every check onto the private RAM copy:

* ``bootstrap_s99`` stages each runtime member in ``/tmp/cammy-runtime``: stock
  ``md5sum`` pins the copied BusyBox, whose ``sha256sum`` then checks the pinned
  manifest digest and every other staged copy before it is renamed into place.
  Only then is the one-shot guard written and ``daemon-exec``/``startup.sh`` run
  from RAM. The manifest digest lives only here (S99 is outside the manifest), so
  ``startup.sh`` never has to name a digest of itself.
* ``ram_runtime`` wraps ``rc06_build.runtime()`` output so it runs the RAM
  copies S99 verified and reads nothing from the card except the optional
  diagnostics flag.
* ``rc_script`` packages the rc06 shell templates; health sources the verified
  RAM helper instead of the card copy. The TF arm stays read from the card.

Stock-shell rules: no mktemp, rmdir, sha256sum or ``mv -T`` on the camera, so the
stage file has a fixed name and a directory at a target name is refused.
Nothing here touches hardware or the filesystem.
"""
import hashlib
import re

RUN_DIR = '/tmp/cammy-runtime'
SUPERVISOR = RUN_DIR + '/startup.sh'
MANIFEST = 'SHA256SUMS'
STAGE_ORDER_FIRST = ('busybox', MANIFEST)
SAFE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\Z')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def md5(data):
    return hashlib.md5(data).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Source context differs: ' + old[:70])
    return text.replace(old, new)


def _one_match(pattern, text, what):
    found = list(pattern.finditer(text))
    if len(found) != 1:
        raise ValueError('Runtime payload context differs: ' + what)
    return found[0]


# Members run directly (mode 700); every other member is data or run by /bin/sh.
PROGRAMS = frozenset({'busybox', 'wpa_supplicant', 'wpa_cli', 'cammy-portal', 'daemon-exec', 'native-service-client',
                      'face-status', 'fault-evidence', 'speaker-guardian', 'cammy-ui', 'dhcp.sh'})


def manifest(payload):
    return ''.join(sha(data) + '  ' + name + '\n' for name, data in sorted(payload.items())).encode()


def _check_payload(payload):
    if MANIFEST not in payload or 'busybox' not in payload or 'daemon-exec' not in payload or 'startup.sh' not in payload:
        raise ValueError('Runtime payload lacks a bootstrap member')
    for name in payload:
        if not SAFE_NAME.match(name):
            raise ValueError('Unsafe runtime member name: ' + name)
    rest = {n: d for n, d in payload.items() if n != MANIFEST}
    if payload[MANIFEST] != manifest(rest):
        raise ValueError('Runtime manifest differs from its members')


def stage_block(card, payload):
    """Shell lines (two-space case indent) that stage and verify the payload in RAM."""
    _check_payload(payload)
    lines = [
        '  # Run only private RAM copies checked after the copy: stock md5sum pins',
        '  # BusyBox, whose sha256sum checks the pinned manifest and each member',
        '  # before it is renamed into place. A refusal writes no guard.',
        '  R=' + RUN_DIR,
        '  C=' + card,
        '  S=$R/.stage',
        '  cammy_refuse() {',
        '   rm -f "$S"',
        "   echo 'CammyOS startup skipped: payload differs from the pinned release'",
        '   exit 1',
        '  }',
        '  cammy_stage() {',
        '   rm -f "$S" && cp "$C/$1" "$S" && chmod "$2" "$S"',
        '  }',
        '  cammy_place() {',
        '   [ ! -d "$R/$1" ] && mv -f "$S" "$R/$1"',
        '  }',
        '  cammy_take() {',
        '   cammy_stage "$1" "$2" && [ "$("$R/busybox" sha256sum "$S" | cut -c1-64)" = "$3" ] && cammy_place "$1"',
        '  }',
        '  [ ! -L "$R" ] && mkdir -p "$R" && [ -d "$R" ] && [ ! -L "$R" ] && chmod 700 "$R" || cammy_refuse',
        '  cammy_stage busybox 700 && [ "$(md5sum "$S" | cut -c1-32)" = "%s" ] && cammy_place busybox || cammy_refuse' % md5(payload['busybox']),
        '  cammy_take %s 600 %s || cammy_refuse' % (MANIFEST, sha(payload[MANIFEST])),
    ]
    for name in sorted(payload):
        if name in STAGE_ORDER_FIRST:
            continue
        mode = '700' if name in PROGRAMS else '600'
        lines.append('  cammy_take %s %s %s || cammy_refuse' % (name, mode, sha(payload[name])))
    return '\n'.join(lines) + '\n'


S99_GUARD = '''  (set -C; printf 'phase=launch\\nboot_id=%s\\n' "$(cat /proc/sys/kernel/random/boot_id)" >"$GUARD") || exit 1
  sync
'''
S99_LAUNCH = '''  umask 077
''' + S99_GUARD + '''  cp @CARD@/daemon-exec /tmp/cammy-launcher
  chmod 700 /tmp/cammy-launcher
  /tmp/cammy-launcher /tmp/cammy-startup.log /bin/sh @CARD@/startup.sh
'''
S99_DETECT = "grep -q 'CammyOS/startup.sh'"


def bootstrap_s99(source, card, payload, *, require_candidate=False):
    """Generated /etc/init.d/S99cammy-tf for one release slot.

    Order is unchanged where it matters: supervisor detection, credentials and
    the startup latch are checked before any TF I/O, every staging failure exits
    before the one-shot guard exists, and the guard is synced before execution.
    """
    if source.count(S99_DETECT) != 2:
        raise ValueError('Supervisor detection context differs')
    # The supervisor now runs from RAM; its command line names the RAM copy.
    s = source.replace(S99_DETECT, "grep -q '%s'" % SUPERVISOR)
    if require_candidate:
        s = _once(s, '  [ -r /etc/cammy/home.conf ]', """  # S01 must have elected the native process for this very boot.
  STAMP=/run/cammy-select/launch.stamp
  [ -f "$STAMP" ] && [ ! -L "$STAMP" ] || exit 0
  grep -qx 'target=candidate' "$STAMP" || exit 0
  grep -qx "boot=$(cat /proc/sys/kernel/random/boot_id)" "$STAMP" || exit 0
  [ -r /etc/cammy/home.conf ]""")
    s = s.replace('/mnt/extsd/CammyOS', card)
    s = _once(s, S99_LAUNCH.replace('@CARD@', card), '  umask 077\n' + stage_block(card, payload) + S99_GUARD
              + '  "$R/daemon-exec" /tmp/cammy-startup.log /bin/sh "$R/startup.sh"\n')
    if '/tmp/cammy-launcher' in s or card + '/startup.sh' in s:
        raise ValueError('Bootstrap still executes card bytes')
    return s


PROGRAM_COPY = re.compile(
    r'for program in ([a-z_ -]+); do\n'
    r' cp "\$CARD/\$program" "\$R/\$program" \|\| exit 1\n'
    r' chmod 700 "\$R/\$program"\n'
    r'done\n'
    r'B=\$R/busybox\n'
    r'\(cd "\$CARD" && "\$B" sha256sum -c SHA256SUMS\) >"\$R/payload-check\.log" 2>&1 \|\| exit 1\n')
DATA_COPY = re.compile(
    r'for data in ([a-z._ -]+); do\n'
    r' cp "\$CARD/\$data" "\$R/\$data" \|\| exit 1\n'
    r' chmod 600 "\$R/\$data" \|\| exit 1\n'
    r'done\n')
# S99 checked every staged RAM copy against the manifest digest it pins, just
# before it ran this script from that same private directory; a second
# `sha256sum -c` over the same bytes added nothing but a boot-time read.
RAM_CHECK = '''# The bootstrap staged this payload in RAM and checked each copy against the
# manifest it pins before running this script; nothing is read from the card.
B=$R/busybox
'''


def ram_runtime(raw, names):
    """Wrap rc06_build.runtime() bytes: payload comes from the verified RAM copy.

    ``names`` is the staged payload; every file the runtime used to copy from
    the card must be one of them. Only the diagnostics opt-in stays on TF.
    """
    s = raw.decode()
    names = set(names)
    m = _one_match(PROGRAM_COPY, s, 'program copy')
    copied = m.group(1).split()
    if not set(copied) <= PROGRAMS:
        raise ValueError('Runtime program not staged executable: ' + ', '.join(sorted(set(copied) - PROGRAMS)))
    s = s[:m.start()] + RAM_CHECK + s[m.end():]
    m = _one_match(DATA_COPY, s, 'data copy')
    copied += m.group(1).split()
    s = s[:m.start()] + s[m.end():]
    s = _once(s, 'cp "$CARD/dhcp.sh" "$R/dhcp.sh"\nchmod 700 "$R/dhcp.sh"\n', '')
    s = _once(s, '# Optional release marker is verified with the payload and copied only to RAM.\n'
              '[ ! -r "$CARD/RELEASE" ] || cp "$CARD/RELEASE" "$R/RELEASE"\n', '')
    s = _once(s, 'release=$(cat "$CARD/RELEASE" 2>/dev/null)', 'release=$(cat "$R/RELEASE" 2>/dev/null)')
    if s.count('"$CARD/enable-shared-capture"') != 3:
        raise ValueError('Runtime payload context differs: shared capture opt-in')
    s = s.replace('"$CARD/enable-shared-capture"', '"$R/enable-shared-capture"')
    missing = sorted(set(copied + ['dhcp.sh', 'RELEASE', 'enable-shared-capture', MANIFEST, 'startup.sh']) - names)
    if missing:
        raise ValueError('Runtime expects RAM files the bootstrap does not stage: ' + ', '.join(missing))
    left = re.findall(r'\$\{?CARD\}?(/[A-Za-z0-9._-]*)?', s)
    if any(path != '/enable-diag' for path in left):
        raise ValueError('Runtime still reads payload from the card')
    return s.encode()


def rc_script(name, template, card):
    """Package an rc06 shell template for ``card``; health sources the RAM helper."""
    s = template.replace('@CARD@', card)
    if name == 'rc-health.sh':
        s = _once(s, '. "$CARD/boot-helper.sh"', '. "$R/boot-helper.sh"')
    return s.encode()
