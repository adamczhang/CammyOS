"""Experimental stock-button transport. No firmware or executable upload.

Maps observed physical controls to ordinary Linux input events. The operator
must establish the current native UI context; these are buttons, not pose APIs.
The trial transport uses existing USB root access with a CammyOS control lease.
"""
import hashlib
import re
import secrets
import shlex
import struct
import uuid

BUTTONS = {
    'left': (0, 115), 'right': (0, 102),
    'zoom_in': (0, 5), 'zoom_out': (0, 6), 'turn_180': (1, 3),
    'center': (0, 7), 'playback_cycle': (1, 139),
}


def input_packet(button):
    """M105 ARM32 kernel input_event layout, observed in passive captures."""
    if button not in BUTTONS:
        raise ValueError('Unobserved button')
    code = BUTTONS[button][1]
    return b''.join(struct.pack('<IIHHi', 0, 0, kind, key, value)
                    for kind, key, value in ((1, code, 1), (0, 0, 0),
                                             (1, code, 0), (0, 0, 0)))


def tap_script(button, identity, nonce=None, *, check_only=False):
    """One regular-file read/write of the complete press+release batch.

    Kernel evdev_write processes this batch under its device mutex. No hold,
    repeat request, raw motor protocol, debug FIFO, native call or VRAM write.
    Only a nonexecutable 64-byte RAM data file is created, hash-checked and
    removed. Caller must never retry an uncertain result.
    """
    packet = input_packet(button)
    boot = str(uuid.UUID(identity['boot']))
    pid = identity['native']['pid']
    start = str(identity['native']['starttime'])
    nonce = nonce or secrets.token_hex(12)
    if type(pid) is not int or pid <= 1 or not re.fullmatch(r'[0-9]+', start):
        raise ValueError('Invalid native process identity')
    if not re.fullmatch(r'[0-9a-f]{24}', nonce):
        raise ValueError('Invalid exclusive RAM nonce')
    device, _ = BUTTONS[button]
    octal = ''.join('\\0%03o' % byte for byte in packet)
    digest = hashlib.sha256(packet).hexdigest()
    action = ':' if check_only else f'/bin/dd if="$d/events" of=/dev/input/event{device} bs=64 count=1 2>/dev/null'
    marker = 'CAMMY_TAP_CHECKED=64' if check_only else 'CAMMY_TAP_DONE=64'
    # All interpolated shell fields are validated integers/UUID/hex or quoted
    # octal data. The event path and device name are compile-time mappings.
    return f'''umask 077
refuse() {{ printf 'CAMMY_TAP_REFUSED=%s\\n' "$1"; exit 3; }}
[ "$(/bin/cat /proc/sys/kernel/random/boot_id)" = "{boot}" ] || refuse boot
[ "$(/bin/cat /sys/class/input/event{device}/device/name)" = "sunxi-gpadc{device}" ] || refuse input-name
[ "$(/bin/cat /sys/class/input/event{device}/dev)" = "13:{64+device}" ] || refuse input-device
[ -c /dev/input/event{device} ] || refuse input-node
[ "$(/usr/bin/readlink /proc/{pid}/exe)" = /usr/bin/sdvcam ] || refuse native-exe
set -- $(/bin/cat /proc/{pid}/stat)
[ "$#" -ge 22 ] || refuse native-stat
shift 21
[ "$1" = "{start}" ] || refuse native-start
d=/tmp/cammy-button-{nonce}
/tmp/cammy-runtime/busybox mkdir -m 700 "$d" || refuse ram-create
trap '/tmp/cammy-runtime/busybox rm -rf "$d"' EXIT HUP INT TERM
printf '%b' {shlex.quote(octal)} > "$d/events" || refuse data-write
set -- $(/tmp/cammy-runtime/busybox sha256sum "$d/events")
[ "$1" = "{digest}" ] || refuse data-hash
{action}
rc=$?
[ "$rc" = 0 ] || {{ printf 'CAMMY_TAP_UNCERTAIN=write\\n'; exit 4; }}
printf '{marker}\\n'
'''


def pulse_script(button, identity, hold_ms=150):
    """One bounded press with an independent release process, not a held key API.

    Release survives an ordinary parent/USB HUP. This is not a guarantee across
    kernel failure or SIGKILL of every process. No automatic key-down retry.
    """
    if (button not in ('left', 'right', 'center', 'playback_cycle') or
            type(hold_ms) is not int or hold_ms not in (150, 5000) or
            (hold_ms == 5000 and button not in ('left', 'right'))):
        raise ValueError('Unsupported button/duration combination')
    script = tap_script(button, identity)
    device = BUTTONS[button][0]
    original = f'/bin/dd if="$d/events" of=/dev/input/event{device} bs=64 count=1 2>/dev/null'
    action = f'''/bin/dd if="$d/events" of="$d/down" bs=32 count=1 2>/dev/null || refuse down-data
/bin/dd if="$d/events" of="$d/up" bs=32 skip=1 count=1 2>/dev/null || refuse up-data
release_key() {{ /bin/dd if="$d/up" of=/dev/input/event{device} bs=32 count=1 2>/dev/null; }}
releaser=
pulse_released=0
cleanup_pulse() {{
    if [ "$pulse_released" != 1 ]; then release_key; fi
    if [ -n "$releaser" ]; then wait "$releaser" 2>/dev/null; fi
    /tmp/cammy-runtime/busybox rm -rf "$d"
}}
trap cleanup_pulse EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
(
    trap '' HUP INT TERM
    /bin/sleep {hold_ms / 1000:.2f}
    release_key
) &
releaser=$!
/bin/dd if="$d/down" of=/dev/input/event{device} bs=32 count=1 2>/dev/null
down_rc=$?
wait "$releaser"
up_rc=$?
release_key
final_up_rc=$?
if [ "$down_rc" != 0 ] || [ "$up_rc" != 0 ] || [ "$final_up_rc" != 0 ]; then
    printf 'CAMMY_TAP_UNCERTAIN=pulse\\n'
    exit 4
fi
pulse_released=1'''
    if script.count(original) != 1:
        raise ValueError('Unexpected base script')
    return script.replace(original, action)


def tap_once(reader, client, button, identity, hold_ms=0):
    state = client.state()
    if (state.get('owner') != client.agent or state.get('lease_remaining_seconds', 0) < 15
            or state.get('preview_active')):
        raise RuntimeError('Matching idle CammyOS lease with at least 15 seconds required')
    # No retries, even if the legacy USB shell loses its reply.
    if type(hold_ms) is not int or hold_ms not in (0, 150, 5000):
        raise ValueError('Unsupported press duration')
    command = pulse_script(button, identity, hold_ms) if hold_ms else tap_script(button, identity)
    reply = reader.shell(command).decode(errors='replace')
    if 'CAMMY_TAP_REFUSED=' in reply:
        raise RuntimeError('Button precondition refused: ' + reply[-300:])
    if reply.strip() != 'CAMMY_TAP_DONE=64':
        raise RuntimeError('Button result uncertain; inspect camera before another command')
    return {'button': button, 'transport': 'usb-adb-stock-evdev',
            'input_events_written': 6 if hold_ms else 4, 'press_release_batch_bytes': 64,
            'input_write_bytes': 96 if hold_ms else 64,
            'redundant_release': bool(hold_ms),
            'hold_requested_ms': hold_ms,
            'physical_effect_confirmed': False, 'pose_or_stop_feedback': False,
            'mid_hold_portal_stop_integrated': False,
            'firmware_or_code_installed': False}
