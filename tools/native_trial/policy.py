"""WP4 activation decision, stock identities and open hardware gates.

Identities below come from the extracted stock rootfs image whose sdvcam hash
matches the live read-only hash recorded in docs/NATIVE-LIFECYCLE-2026-11-14.md.
Live S01app and sdvcam hashes were confirmed by the manager's bounded read-only
metadata snapshot (see E3_LIVE_FACTS); early-boot availability remains unverified.
"""
from __future__ import annotations

STOCK_SDVCAM_SHA256 = '8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec'
STOCK_SDVCAM_MD5 = '8689ce53c4674563a7a6935f97c0d727'
STOCK_SDVCAM_SIZE = 11335176
STOCK_S01APP_SHA256 = '3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136'
STOCK_S01APP_MD5 = 'daeb505e811834088f64cb07696c5cbe'

# Patch sites of the current offline media candidate (file offsets). Changing
# either makes a native 9999 client connect start/stop media without a lease.
CONNECT_TRIGGER_SITES = (
    (0x9C5D0, 4, 'client connect -> stock media init'),
    (0x9C638, 4, 'client disconnect -> media destroy trampoline'),
)

# WP1 status-only patch scope (docs/native-bridge/NATIVE-ABI.md section 7), as file
# offsets checked against the stock ELF: the text LOAD maps file 0 at VA 0x10000.
STATUS_BL_SITE = (0x21DB0, 4, 'bl 0xA4294 at main+0x4E8 (VA 0x31DB0)')
# Reviewed WP2A structural constants (tools/build_native_status_candidate.py,
# docs/native-bridge/LOADER-PROOF.md section 4), as file offsets: the status stub
# starts exactly at VA 0x8E2E40 and must end inside the already-mapped last text
# page (file 0x8D3000); the media candidate's cave range VA 0x8E2E20-0x8E2E38 stays
# untouched. A test cross-checks these against the WP2A tool (read-only).
STATUS_STUB_START = 0x8D2E40
LAST_TEXT_PAGE_END = 0x8D3000
MEDIA_CAVE_RANGE = (0x8D2E20, 0x8D2E38)
STATUS_ONLY_ALLOWED_RANGES = (
    STATUS_BL_SITE,
    (STATUS_STUB_START, LAST_TEXT_PAGE_END - STATUS_STUB_START,
     'status stub window (VA 0x8E2E40-0x8E3000) of the zero code cave'),
    (0xA4, 8, 'executable LOAD p_filesz/p_memsz'),
)
ARM_EABI5_HARD_FLOAT = 0x05000400
# Stock text LOAD p_filesz/p_memsz end exactly at the cave start; a status build
# may extend them only far enough to cover its stub, never past the cave end.
STOCK_TEXT_LOAD_SIZE = 0x8D2E1C
CAVE = (0x8D2E1C, 0x8D3030)  # whole verified zero cave (WP1); status builds use the stub window only
TEXT_VA_BIAS = 0x10000  # text LOAD maps file offset 0 at VA 0x10000

# WP5 review (2026-11-14): the bind-overlay early selector could not be shown to
# fail closed on hardware (bind-unrestorable and tf-detach-failed both leave stock
# S01app to run or back up whatever /usr/bin/sdvcam shows). Activation therefore
# stays ineligible; the proposal ships SELECT_GATE=blocked and no tool emits an
# installable allowlist. Reopening needs gate EA plus a reviewed constant change.
ACTIVATION_BLOCKED = ('activation ineligible: bind-overlay early selector has no proven fail-closed '
                      'launch (WP5 findings F2/F5); gate EA open; proposal ships SELECT_GATE=blocked')

# Read-only live facts from private/ownership-program/manager-e3-live.json
# (late boot, uptime ~9120 s, 2026-11-14 17:54 UTC). They are CURRENT LATE-BOOT
# metadata only: not S00-time availability, boot timing, mount options at S00,
# applet/option support or watchdog nowayout.
E3_LIVE_FACTS = {
    'source': 'private/ownership-program/manager-e3-live.json',
    'confirmed_late_boot': [
        'S01app sha256 3327c3a9... equals stock (sha256sum output in the evidence)',
        'sdvcam sha256 8771961f... equals stock',
        '/tmp is tmpfs rw,relatime; /run is tmpfs rw,relatime (no noexec)',
        'card is /dev/mmcblk0 (no mmcblk0p1), mounted vfat rw at /mnt/extsd after boot',
        'direct md5sum of /etc/init.d/S01app works and equals daeb505e...',
        'MemAvailable 421304 kB late boot',
    ],
    'not_verified': [
        'busybox applet list: self-dispatch/list mode failed ("applet not found")',
        'shell command discovery: "command" is not a builtin of the stock /bin/sh '
        '(stock shell identity and set -C, ${#v}, eval, $(( )) support unproven)',
        'mount -o bind, umount -l, tail -c, awk -v, grep \\< \\> option support',
        'availability of /dev/mmcblk0 and /run, /tmp mounts at S00 time',
        'selector duration budget, bootloader-armed watchdog, nowayout',
        'MemAvailable at S00 time',
    ],
}

DECISION = {
    'date': '2026-11-14',
    'selected_route': 'none (activation ineligible)',
    'selected_route_status': ('WP4 early selector kept as a BLOCKED design proposal after the WP5 review; '
                              'fake/relocated-shell tested only; internal install NOT authorized'),
    'activation': ACTIVATION_BLOCKED,
    'design_deviation': ('the WP4 bind overlay of /usr/bin/sdvcam deviates from the guarded launch plan, '
                         'which avoided mounting over the stock executable; recorded, not approved'),
    'mechanism': ('S00cammy-select runs from rc.final before S00mpp/S01app, verifies an internal '
                  'one-candidate allowlist, copies the TF candidate to /run, durably claims the attempt '
                  'on the internal overlay, then bind-mounts the copy over /usr/bin/sdvcam for this boot '
                  'only. Stock S01app runs unchanged and starts the selected app once.'),
    'late_handoff_status': 'ineligible: no process-only shutdown, watchdog, continuity or warm-restore contract',
    'media_authorization': ('Selection grants no media authority. Arm/allowlist records carry no lease or '
                            'token. Only class status-only is selectable; media-lease-gated stays gated until '
                            'the bridge enforces a fresh current-boot lease; media-connect-triggered '
                            '(the current offline candidate) is never selectable.'),
    'current_candidate': ('WP2A status-only build has offline ABI/loader proof but no activation approval; '
                          'the older media-connect-triggered candidate remains ineligible'),
    'classification': ('structural byte-range/ELF checks are not behavioural proof; E1 needs a SHA256-bound '
                       'review/ABI evidence record (native_trial review) and manager closure'),
}

# Gates that must close, in order, before the early selector may select anything
# on hardware. None is closed by local tests. Status values: open | blocked.
EARLY_SELECTOR_GATES = (
    ('EA', 'blocked', 'Activation mechanism: a launch route that provably never lets stock S01app run or back '
                      'up unverified bytes (bind-unrestorable, tf-detach-failed, pre-claim hang/reset loop), '
                      'reviewed against the WP5 revised bootstrap design. Until then SELECT_GATE=blocked.'),
    ('E1', 'open', 'Status-only native build (WP2 bridge in an exact stock copy) exists, is STRUCTURALLY '
                   'status-only with no connect-trigger sites changed, AND a SHA256-bound review/ABI evidence '
                   'record (NATIVE-ABI section 7 proofs 1-5, provenance) is verified; manager closes it.'),
    ('E2', 'open', 'Adam approves the internal-overlay change: three files (/etc/init.d/S00cammy-select, '
                   '/etc/cammy/native-select.allow, /etc/cammy/native-select.enabled=mode=observe) plus '
                   'the removal/rollback procedure. Separate from any TF runtime update.'),
    ('E3', 'partial', 'Read-only live facts. Late-boot facts recorded (see E3_LIVE_FACTS); still open: '
                      'stock shell identity/constructs, busybox applets and options (mount bind, umount -l, '
                      'tail -c, awk -v, grep \\<\\>), S00-time card/mount availability, /proc/filesystems, '
                      'unshare/mount namespaces, MemAvailable at S00.'),
    ('E4', 'open', 'Observe-mode boot: status shows observe-only, selector time within budget, no reset, '
                   'TF not premounted and detached, normal stock app/LCD/home Wi-Fi afterwards.'),
    ('E5', 'open', 'Selected boot (mode=select, status-only class): exactly one native app whose exe is '
                   'the RAM copy and holds /dev/watchdog; stable watchdog, LCD, home Wi-Fi and portal; '
                   'portal hardware capabilities still unavailable; bridge hello/status only.'),
    ('E6', 'open', 'Stock restore: next boot reports post-attempt-boot with observed boot id and stock '
                   'sdvcam hash; a card-removed boot is stock; operator ack recorded.'),
    ('E7', 'open', 'Crash/core policy accepted: S01app sets ulimit -c unlimited and core_pattern '
                   '/mnt/extsd/core.%e.%p; before the card is mounted there (S10udev) a core lands in the '
                   'INTERNAL overlay directory. A candidate crash must be bounded or explicitly accepted.'),
    ('E8', 'blocked', 'media-lease-gated class: bridge must enforce fresh current-boot lease and generation '
                      'before any media path is selectable (WP2/WP3). Not part of the first trial.'),
)

LATE_HANDOFF_GATES = (
    ('L1', 'open', 'Cooperative, thread-correct process-only shutdown request/ack avoiding power/reboot/OTA.'),
    ('L2', 'open', 'Proven watchdog handling: effective timeout, nowayout/close semantics, native disable ack.'),
    ('L3', 'open', 'Radio/USB/TF continuity through native teardown and restart.'),
    ('L4', 'open', 'Exact stock launch profile and bounded readiness for a single warm restore.'),
    ('L5', 'open', 'Identity-bound resource-release acknowledgement and no-respawn evidence.'),
)


def gates_report() -> dict:
    def rows(gates):
        return [{'id': gid, 'status': status, 'requirement': text} for gid, status, text in gates]
    return {'decision': DECISION, 'early_selector_gates': rows(EARLY_SELECTOR_GATES), 'e3_live_facts': E3_LIVE_FACTS,
            'late_handoff_gates': rows(LATE_HANDOFF_GATES),
            'hardware_performed': False,
            'evidence_class': 'local fake/relocated-shell only'}
