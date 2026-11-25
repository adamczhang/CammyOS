"""Exact-source S01app boot-launch proposal generator (Chunk 4/5, shipped gate blocked).

Refuses any input that does not match the saved stock S01app byte-for-byte
(156 lines, 4,246 bytes, SHA256 3327c3a919b8863afd16ad9cced159634e2870ae6b07333bccd4d0a921a72136)
and verifies the single active legacy backup condition (line 78) and all five
launch sites (lines 91, 99, 101, 114, 118) before generating anything.

Generated vendor-derived scripts, diffs and manifests may ONLY be written to
ignored build/boot-launch/, private/ownership-program/{chunk4,chunk5}/, or guarded
temporary directories with mode 0644 and explicit inactive metadata
(deployed=False, activation_eligible=False, select_gate=blocked,
candidate_selection_logic_present=True, candidate_selection_live_routing=False).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from native_trial import policy as trial_policy

ROOT = Path(__file__).resolve().parents[2]
HELPER_PROPOSAL_PATH = ROOT / 'native/activation/launch_selected_or_stock.proposal'
BUILD_OUT_ROOT = ROOT / 'build/boot-launch'
PRIVATE_OUT_ROOT = ROOT / 'private/ownership-program/chunk4'
PRIVATE_CHUNK5_OUT_ROOT = ROOT / 'private/ownership-program/chunk5'
SAVED_STOCK_S01APP = Path(
    os.environ.get('CAMMY_STOCK_WORK', str(Path(__file__).resolve().parents[2].parent / 'Cammy Bot/M105 investigation/work')) + '/firmware-rootfs/etc/init.d/S01app'
)

# Authoritative stock identities (cross-checked against tools/native_trial/policy.py).
STOCK_S01APP_SHA256 = trial_policy.STOCK_S01APP_SHA256
STOCK_S01APP_MD5 = trial_policy.STOCK_S01APP_MD5
STOCK_S01APP_SIZE = 4246
STOCK_S01APP_LINES = 156

STOCK_SDVCAM_SHA256 = trial_policy.STOCK_SDVCAM_SHA256
STOCK_SDVCAM_MD5 = trial_policy.STOCK_SDVCAM_MD5
STOCK_SDVCAM_SIZE = trial_policy.STOCK_SDVCAM_SIZE

# Exact 1-indexed line numbers in the 156-line stock S01app.
EXPECTED_BACKUP_LINE_NO = 78
EXPECTED_BACKUP_LINE = '    if [ ! -z "$(grep \'\\<vfat\\>\' /proc/mounts)" ]; then\n'

EXPECTED_LAUNCH_LINES: dict[int, str] = {
    91: '            /usr/bin/sdvcam &\n',
    99: '                    /usr/bin/sdvcam &\n',
    101: '                    /usr/bin/sdvcam &\n',
    114: '                /usr/bin/sdvcam &\n',
    118: '        /usr/bin/sdvcam &\n',
}

HELPER_BEGIN_MARKER = '# --- begin cammy boot-launch helper (chunk4/5 proposal; SELECT_GATE=blocked) ---\n'
HELPER_END_MARKER = '# --- end cammy boot-launch helper ---\n'

REPLACEMENT_BACKUP_LINES = (
    '    cammy_prepare_launch\n',
    '    if [ "$LAUNCH_BACKUP_ELIGIBLE" = "1" ]; then\n',
)

REPLACEMENT_LAUNCH_CMD = 'cammy_launch_selected_or_stock\n'


class SourceVerificationError(ValueError):
    """Raised when stock S01app or helper source fails pre-generation checks."""


@dataclass(frozen=True)
class GeneratedProposal:
    source_sha256: str
    source_md5: str
    source_size: int
    source_lines: int
    helper_sha256: str
    helper_md5: str
    helper_size: int
    replacement_text: str
    replacement_sha256: str
    replacement_md5: str
    replacement_size: int
    replacement_lines: int
    unified_diff: str
    preserved_ranges: list[dict]
    replaced_sites: list[dict]

    def manifest(self) -> dict:
        return {
            'schema': 'cammy-boot-launch-proposal-manifest/1',
            'chunk': 'chunk5',
            'status': 'inactive-proposal',
            'deployed': False,
            'activation_eligible': False,
            'select_gate': 'blocked',
            'candidate_selection_implemented': False,
            'candidate_selection_logic_present': True,
            'candidate_selection_live_routing': False,
            'chunk5_hook': {
                'constant': 'SELECT_GATE=blocked',
                'function': 'cammy_prepare_one_shot',
                'status': 'logic_present_behind_blocked_gate',
                'note': (
                    'One-shot status-only candidate selection logic is present in '
                    'launch_selected_or_stock.proposal behind SELECT_GATE=blocked. '
                    'Public generator refuses any helper without SELECT_GATE=blocked, so '
                    'live routing remains disabled and only synthetic test relocation '
                    '(SELECT_GATE=chunk5-fixture-open, activation_eligible=false) exercises selection.'
                ),
            },
            'identities': {
                'build_input_s01app': {
                    'role': 'build-time exact source input (never compared to modified S01app at runtime)',
                    'sha256': self.source_sha256,
                    'md5': self.source_md5,
                    'size': self.source_size,
                    'lines': self.source_lines,
                },
                'helper_proposal': {
                    'path': 'native/activation/launch_selected_or_stock.proposal',
                    'sha256': self.helper_sha256,
                    'md5': self.helper_md5,
                    'size': self.helper_size,
                },
                'generated_replacement_s01app': {
                    'role': 'generated replacement S01app proposal (distinct from build-input stock S01app hash)',
                    'sha256': self.replacement_sha256,
                    'md5': self.replacement_md5,
                    'size': self.replacement_size,
                    'lines': self.replacement_lines,
                },
                'runtime_stock_sdvcam': {
                    'role': 'runtime stock executable verified before any fallback launch',
                    'sha256': STOCK_SDVCAM_SHA256,
                    'md5': STOCK_SDVCAM_MD5,
                    'size': STOCK_SDVCAM_SIZE,
                },
            },
            'transformation': {
                'backup_gate_line': EXPECTED_BACKUP_LINE_NO,
                'launch_site_lines': sorted(EXPECTED_LAUNCH_LINES),
                'preserved_ranges': self.preserved_ranges,
                'replaced_sites': self.replaced_sites,
                'backup_equivalence_note': (
                    'Backup eligibility is intentionally tightened from any-vfat in /proc/mounts '
                    'to a verified vfat mount at /mnt/extsd with no private staging residue. '
                    'Unrelated vfat mounts and failed private TF detaches no longer authorize '
                    'backup writes into the internal overlay.'
                ),
            },
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def verify_stock_s01app(source_bytes: bytes) -> list[str]:
    """Verify exact stock S01app bytes, line count, backup condition and 5 launch sites.

    Returns the 156 lines (with line endings preserved) or raises SourceVerificationError.
    """
    if not isinstance(source_bytes, (bytes, bytearray)):
        raise SourceVerificationError('source_bytes must be bytes')
    raw = bytes(source_bytes)
    if len(raw) != STOCK_S01APP_SIZE:
        raise SourceVerificationError(
            f'stock S01app size mismatch: expected {STOCK_S01APP_SIZE}, got {len(raw)}'
        )
    actual_sha = _sha256(raw)
    if actual_sha != STOCK_S01APP_SHA256:
        raise SourceVerificationError(
            f'stock S01app sha256 mismatch: expected {STOCK_S01APP_SHA256}, got {actual_sha}'
        )
    actual_md5 = _md5(raw)
    if actual_md5 != STOCK_S01APP_MD5:
        raise SourceVerificationError(
            f'stock S01app md5 mismatch: expected {STOCK_S01APP_MD5}, got {actual_md5}'
        )
    text = raw.decode('ascii')
    lines = text.splitlines(keepends=True)
    if len(lines) != STOCK_S01APP_LINES:
        raise SourceVerificationError(
            f'stock S01app line count mismatch: expected {STOCK_S01APP_LINES}, got {len(lines)}'
        )
    if lines[EXPECTED_BACKUP_LINE_NO - 1] != EXPECTED_BACKUP_LINE:
        raise SourceVerificationError(
            f'stock line {EXPECTED_BACKUP_LINE_NO} does not match expected vfat backup condition'
        )
    for line_no, expected_text in EXPECTED_LAUNCH_LINES.items():
        if lines[line_no - 1] != expected_text:
            raise SourceVerificationError(
                f'stock line {line_no} does not match expected launch site: {lines[line_no - 1]!r}'
            )
    # Confirm no other active /usr/bin/sdvcam launch lines exist.
    active_launches = [
        idx + 1
        for idx, line in enumerate(lines)
        if not line.lstrip().startswith('#') and '/usr/bin/sdvcam' in line and 'cp /usr/bin/sdvcam' not in line
        and 'du /usr/bin/sdvcam' not in line and 'md5sum /usr/bin/sdvcam' not in line
    ]
    if active_launches != sorted(EXPECTED_LAUNCH_LINES):
        raise SourceVerificationError(f'unexpected active sdvcam launch lines: {active_launches}')
    return lines


def verify_helper_proposal(helper_bytes: bytes) -> list[str]:
    """Verify the helper proposal stays pinned to SELECT_GATE=blocked and contains required functions."""
    text = helper_bytes.decode('ascii')
    lines = text.splitlines(keepends=True)
    if not lines or lines[0] != '#!/bin/sh\n':
        raise SourceVerificationError('helper proposal must start with #!/bin/sh')
    if 'SELECT_GATE=blocked\n' not in lines:
        raise SourceVerificationError('helper proposal must pin SELECT_GATE=blocked')
    for required in (
        'cammy_prepare_launch()',
        'cammy_prepare_one_shot()',
        'cammy_launch_selected_or_stock()',
        'cammy_verify_stock_app()',
        'cammy_backup_eligible()',
    ):
        if required not in text:
            raise SourceVerificationError(f'helper proposal missing required function {required}')
    return lines


def generate_proposal(
    source_bytes: bytes,
    helper_bytes: bytes | None = None,
) -> GeneratedProposal:
    """Transform verified stock S01app into the proposal in memory."""
    stock_lines = verify_stock_s01app(source_bytes)
    if helper_bytes is None:
        if (HELPER_PROPOSAL_PATH.stat().st_mode & 0o111) != 0:
            raise SourceVerificationError('helper proposal file must not have executable bits set')
        helper_bytes = HELPER_PROPOSAL_PATH.read_bytes()
    helper_lines = verify_helper_proposal(helper_bytes)
    # Strip leading shebang from embedded block so S01app keeps its single top shebang.
    embedded_helper = [HELPER_BEGIN_MARKER] + helper_lines[1:] + [HELPER_END_MARKER, '\n']
    helper_line_count = len(embedded_helper)

    out_lines: list[str] = []
    replaced_sites: list[dict] = []

    for idx, line in enumerate(stock_lines, start=1):
        if idx == 24:
            # Insert helper block immediately before start() at stock line 24.
            out_lines.extend(embedded_helper)

        if idx == EXPECTED_BACKUP_LINE_NO:
            out_start = len(out_lines) + 1
            out_lines.extend(REPLACEMENT_BACKUP_LINES)
            replaced_sites.append({
                'kind': 'backup_gate',
                'stock_line': idx,
                'stock_text': line.rstrip('\n'),
                'replacement_lines': [out_start, out_start + len(REPLACEMENT_BACKUP_LINES) - 1],
                'replacement_text': [r.rstrip('\n') for r in REPLACEMENT_BACKUP_LINES],
            })
        elif idx in EXPECTED_LAUNCH_LINES:
            indent = line[:len(line) - len(line.lstrip(' '))]
            replacement = indent + REPLACEMENT_LAUNCH_CMD
            out_line_no = len(out_lines) + 1
            out_lines.append(replacement)
            replaced_sites.append({
                'kind': 'launch_site',
                'stock_line': idx,
                'stock_text': line.rstrip('\n'),
                'replacement_lines': [out_line_no, out_line_no],
                'replacement_text': [replacement.rstrip('\n')],
            })
        else:
            out_lines.append(line)

    replacement_text = ''.join(out_lines)
    replacement_bytes = replacement_text.encode('ascii')
    replacement_sha256 = _sha256(replacement_bytes)
    if replacement_sha256 == STOCK_S01APP_SHA256:
        raise SourceVerificationError('generated replacement hash must differ from stock input hash')

    # Verify mechanical preservation of every non-replaced stock line range.
    preserved_ranges = _verify_preserved_ranges(stock_lines, out_lines, helper_line_count)

    diff = ''.join(
        difflib.unified_diff(
            stock_lines,
            out_lines,
            fromfile='a/etc/init.d/S01app (stock)',
            tofile='b/etc/init.d/S01app.proposal (chunk5-inactive)',
        )
    )

    return GeneratedProposal(
        source_sha256=_sha256(source_bytes),
        source_md5=_md5(source_bytes),
        source_size=len(source_bytes),
        source_lines=len(stock_lines),
        helper_sha256=_sha256(helper_bytes),
        helper_md5=_md5(helper_bytes),
        helper_size=len(helper_bytes),
        replacement_text=replacement_text,
        replacement_sha256=replacement_sha256,
        replacement_md5=_md5(replacement_bytes),
        replacement_size=len(replacement_bytes),
        replacement_lines=len(out_lines),
        unified_diff=diff,
        preserved_ranges=preserved_ranges,
        replaced_sites=replaced_sites,
    )


def _verify_preserved_ranges(
    stock_lines: list[str],
    out_lines: list[str],
    helper_line_count: int,
) -> list[dict]:
    """Check that all unrelated stock setup, firstboot/backup body, and stop/case ranges match byte-for-byte."""
    spec_ranges = [
        (1, 23, 0, 'top-level VM sysctls, exports, /tmp/data symlink'),
        (24, 77, helper_line_count, 'start() DRAM/mbus_pmu QoS, AXP power regs, core_pattern, comments'),
        (79, 90, helper_line_count + 1, 'firstboot size/space check and .sdvcam backup copy'),
        (92, 98, helper_line_count + 1, 'firstboot cleanup and .sdvcam MD5 verification'),
        (100, 100, helper_line_count + 1, 'backup MD5 match else branch'),
        (102, 113, helper_line_count + 1, 'normal boot missing-.sdvcam backup copy'),
        (115, 117, helper_line_count + 1, 'vfat else branch framing'),
        (119, 156, helper_line_count + 1, 'start() tail, stop() function, and start/stop/restart case'),
    ]
    verified: list[dict] = []
    for s_start, s_end, offset, label in spec_ranges:
        o_start = s_start + offset
        o_end = s_end + offset
        s_slice = stock_lines[s_start - 1 : s_end]
        o_slice = out_lines[o_start - 1 : o_end]
        if s_slice != o_slice:
            raise SourceVerificationError(
                f'preserved range {s_start}..{s_end} ({label}) was modified'
            )
        verified.append({
            'label': label,
            'stock_lines': [s_start, s_end],
            'proposal_lines': [o_start, o_end],
            'line_count': s_end - s_start + 1,
            'sha256': _sha256(''.join(s_slice).encode('ascii')),
        })
    return verified


def _assert_allowed_output_dir(out_dir: Path) -> Path:
    resolved = out_dir.resolve()
    allowed_roots = (
        BUILD_OUT_ROOT.resolve(),
        PRIVATE_OUT_ROOT.resolve(),
        PRIVATE_CHUNK5_OUT_ROOT.resolve(),
        Path(tempfile.gettempdir()).resolve(),
    )
    for root in allowed_roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise ValueError(
        f'refusing to write vendor-derived proposal outside ignored build/private/temp dirs: {out_dir}'
    )


def _write_0644(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(content)
        os.fchmod(handle.fileno(), 0o644)


def emit_inactive_proposal_bundle(
    out_dir: Path,
    source_path: Path = SAVED_STOCK_S01APP,
    helper_path: Path = HELPER_PROPOSAL_PATH,
) -> dict:
    """Verify source and write inactive proposal artifacts (mode 0644) into an ignored directory."""
    target_dir = _assert_allowed_output_dir(Path(out_dir))
    source_bytes = Path(source_path).read_bytes()
    helper_bytes = Path(helper_path).read_bytes()
    proposal = generate_proposal(source_bytes, helper_bytes)

    s01_out = target_dir / 'S01app.proposal'
    diff_out = target_dir / 'S01app.proposal.diff'
    manifest_out = target_dir / 'manifest.json'

    # Refuse the whole write before following or replacing any existing artifact.
    for target in (s01_out, diff_out, manifest_out):
        if target.exists() or target.is_symlink():
            raise ValueError(f'output artifact already exists: {target}; choose a new directory')

    _write_0644(s01_out, proposal.replacement_text.encode('ascii'))
    _write_0644(diff_out, proposal.unified_diff.encode('ascii'))
    manifest_data = proposal.manifest()
    _write_0644(manifest_out, (json.dumps(manifest_data, indent=2, sort_keys=True) + '\n').encode('ascii'))

    return {
        'ok': True,
        'out_dir': str(target_dir),
        'files': {
            'proposal': str(s01_out),
            'diff': str(diff_out),
            'manifest': str(manifest_out),
        },
        'manifest': manifest_data,
    }
