"""Keep camera-local network configuration outside the portable release."""

import re
import stat

TF_MEMBERS = frozenset({
    'native/arm',
    'native/candidate.bin',
    'native/face.so',
    'runtime/RELEASE',
    'runtime/SHA256SUMS',
    'runtime/boot-helper.sh',
    'runtime/busybox',
    'runtime/cammy-portal',
    'runtime/cammy-ui',
    'runtime/daemon-exec',
    'runtime/dhcp.sh',
    'runtime/enable-shared-capture',
    'runtime/face-status',
    'runtime/native-media-start.sh',
    'runtime/native-release.conf',
    'runtime/native-service-client',
    'runtime/rc-health.sh',
    'runtime/rc-media-worker.sh',
    'runtime/shared-capture.fragment',
    'runtime/shared-capture.sh',
    'runtime/speaker-guardian',
    'runtime/speaker.conf',
    'runtime/startup.sh',
    'runtime/wpa_cli',
    'runtime/wpa_supplicant',
})
TF_SPACE_PROBE = 'runtime/fault-evidence'
CURRENT_TF_MEMBERS = TF_MEMBERS | {TF_SPACE_PROBE}
INTERNAL_FILES = {
    '/etc/init.d/S01app': ('bootstrap/S01app', '0755'),
    '/etc/init.d/S99cammy-tf': ('bootstrap/S99cammy-tf', '0755'),
    '/etc/cammy/native-select.allow': ('bootstrap/native-select.allow', '0600'),
    '/etc/cammy/native-select.enabled': ('bootstrap/native-select.enabled', '0600'),
}
PRIVATE_NAMES = frozenset({'home.conf', 'ap.conf', 'wpa_supplicant.conf', 'portal.token'})
WIFI_SECRET = re.compile(
    rb'(?m)^[ \t]*(?:psk|sae_password|wpa_passphrase|wpa_psk|wpa_psk_file)[ \t]*=')


def release_slot(release):
    if not isinstance(release, str) or not re.fullmatch(r'[0-9a-f]{24}', release):
        raise ValueError('Invalid release ID')
    return 'CammyOS-releases/' + release


def validate_members(members, slot):
    """Check package paths without leaking an unexpected member's name."""
    prefix = 'tf/' + slot + '/'
    actual = {name for name in members if name.startswith('tf/')}
    if actual not in ({prefix + name for name in TF_MEMBERS},
                      {prefix + name for name in CURRENT_TF_MEMBERS}):
        raise ValueError('TF payload differs from the portable release allowlist')
    if any(PRIVATE_NAMES.intersection(name.casefold().split('/')) for name in members):
        raise ValueError('Camera-local configuration must stay outside the release')


def validate_tf_content(data):
    """Reject a credential assignment pasted into a portable text payload."""
    if not data.startswith(b'\x7fELF') and WIFI_SECRET.search(data):
        raise ValueError('Wi-Fi credential assignment found in portable TF payload')


def validate_install_plan(plan, release):
    slot = release_slot(release)
    if plan.get('release_id') != release or plan.get('tf_destination') != '/mnt/extsd/' + slot:
        raise ValueError('Install plan release or TF destination differs')
    items = plan['internal_files']
    mapped = {item['target']: (item['source'], item['mode']) for item in items}
    if len(items) != len(INTERNAL_FILES) or mapped != INTERNAL_FILES:
        raise ValueError('Install plan must preserve camera-local configuration')


def validate_tf_tree(root, slot):
    """Check the actual files the installer will copy, including unlisted ones."""
    path = root / 'tf'
    for part in ('', *slot.split('/')):
        if part:
            path = path / part
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError('TF release directories must be real directories')
    actual = set()
    for member in path.rglob('*'):
        info = member.lstat()
        name = member.relative_to(path).as_posix()
        if stat.S_ISDIR(info.st_mode) and name in {'native', 'runtime'}:
            continue
        if name not in CURRENT_TF_MEMBERS or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Unexpected file in portable TF payload')
        validate_tf_content(member.read_bytes())
        actual.add(name)
    if actual not in (TF_MEMBERS, CURRENT_TF_MEMBERS):
        raise ValueError('Incomplete portable TF payload')
