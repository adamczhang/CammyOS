"""``python3 tools/native_trial`` — local, fail-closed WP4 activation tooling.

There is deliberately no install, select, stop, launch or card-write command.
Every write goes under build/native-activation/ (or the temp dir) and every
simulation runs on a FAKE tree. Output is JSON; exit 0 = ok/eligible,
3 = refused/ineligible, 2 = usage.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import bundle, policy, review
from . import records as R
from .faketree import FakeTree, TreeEnv
from .selector import run_selector

REPO = Path(__file__).resolve().parents[2]
PROPOSAL = REPO / 'native' / 'activation' / 'S00cammy-select.proposal'


def _print(data) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def cmd_status(_args) -> int:
    _print({
        'tool': 'native_trial', 'work_package': 'WP4',
        'native_control_backend': 'unavailable (BackendUnavailable on every call)',
        'installer': 'absent by design',
        'early_selector': f'BLOCKED inert proposal: {PROPOSAL.relative_to(REPO)} (not installed; SELECT_GATE=blocked)',
        'activation': policy.ACTIVATION_BLOCKED,
        'late_handoff': policy.DECISION['late_handoff_status'],
        'media_authorization': policy.DECISION['media_authorization'],
        'open_gates': [g[0] for g in policy.EARLY_SELECTOR_GATES + policy.LATE_HANDOFF_GATES],
        'hardware_performed': False,
    })
    return 0


def cmd_gates(_args) -> int:
    _print(policy.gates_report())
    return 0


def cmd_classify(args) -> int:
    report = bundle.classify(Path(args.candidate), Path(args.source) if args.source else None,
                             args.declared_class, Path(args.library) if args.library else None,
                             Path(args.review) if args.review else None,
                             Path(args.evidence_root) if args.evidence_root else None)
    _print(report)
    return 0 if report['eligible'] else 3


def cmd_make_bundle(args) -> int:
    try:
        manifest = bundle.make_bundle(Path(args.candidate), Path(args.source), Path(args.library),
                                      args.declared_class, Path(args.out), args.attempt, Path(args.review),
                                      Path(args.evidence_root) if args.evidence_root else None)
    except (bundle.BundleError, R.RecordError) as error:
        _print({'ok': False, 'refused': str(error)})
        return 3
    _print({'ok': True, 'attempt': manifest['attempt'], 'out': args.out, 'installed': False,
            'local_only': manifest['local_only'], 'activation_eligible': False})
    return 0


def cmd_validate_bundle(args) -> int:
    report = bundle.validate_bundle(Path(args.bundle), Path(args.source) if args.source else None,
                                    Path(args.evidence_root) if args.evidence_root else None)
    _print(report)
    return 0 if report['ok'] else 3


def cmd_make_ack(args) -> int:
    try:
        path = bundle.make_ack(args.attempt, args.observed_boot, args.outcome, Path(args.out))
    except (bundle.BundleError, R.RecordError) as error:
        _print({'ok': False, 'refused': str(error)})
        return 3
    _print({'ok': True, 'ack': str(path)})
    return 0


def cmd_simulate(_args) -> int:
    """FAKE runs of the selector model: the shipped (blocked) gate, then the design."""
    import hashlib
    source, candidate, library = b'stock' * 100, b'candidate' * 100, b'\x7fELF library' * 10
    s01 = b'#!/bin/sh\n/usr/bin/sdvcam &\n'
    md5 = lambda data: hashlib.md5(data).hexdigest()  # noqa: E731
    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
    attempt = 'a' * 32
    allow = R.allow_record(md5(s01), md5(source), len(source), md5(candidate), len(candidate),
                           R.CLASS_STATUS_ONLY, sha(library), md5(library), len(library))
    arm = R.arm_record(attempt, md5(candidate), len(candidate), R.CLASS_STATUS_ONLY, sha(library), md5(library),
                       len(library))
    boots = [f'0000000{i}-0000-4000-8000-000000000000' for i in range(1, 4)]
    runs = {}
    for label, gate, count in (('shipped_gate_blocked', 'blocked', 1), ('design_review_gate_open', 'open', 3)):
        results = []
        with tempfile.TemporaryDirectory(prefix='cammy-wp4-sim-') as temporary:
            tree = FakeTree.build(Path(temporary) / 'root', boot_id=boots[0], source=source, s01app=s01,
                                  allow=allow, card={'arm': arm, 'candidate.bin': candidate, R.LIBRARY_NAME: library})
            for i, boot in enumerate(boots[:count]):
                result = run_selector(TreeEnv(tree), select_gate=gate)
                results.append({'boot': boot, 'decision': result.decision, 'reason': result.reason})
                if i + 1 < count:
                    tree.reboot(boots[i + 1])
        runs[label] = results
    _print({'evidence_class': 'FAKE tree; no device, card or process', 'activation': policy.ACTIVATION_BLOCKED,
            'runs': runs})
    return 0


def cmd_review_template(args) -> int:
    out = Path(args.out).resolve()
    allowed = [bundle.STAGING_ROOT.resolve(), Path(tempfile.gettempdir()).resolve()]
    if not any(root in out.parents for root in allowed) or out.exists():
        _print({'ok': False, 'refused': 'output must be a new file under build/native-activation/ or the temp dir'})
        return 3
    record = review.template(source_sha256=bundle.hashes(Path(args.source))['sha256'],
                             candidate_sha256=bundle.hashes(Path(args.candidate))['sha256'],
                             library_sha256=bundle.hashes(Path(args.library))['sha256'])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'x') as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
    _print({'ok': True, 'template': str(out), 'note': 'every proof is pending; fill from real evidence only'})
    return 0


def cmd_proposal_patch(args) -> int:
    out = Path(args.out).resolve()
    allowed = [bundle.STAGING_ROOT.resolve(), Path(tempfile.gettempdir()).resolve()]
    if not any(root in out.parents for root in allowed) or out.exists():
        _print({'ok': False, 'refused': 'output must be a new file under build/native-activation/ or the temp dir'})
        return 3
    if args.bundle:
        # WP5: activation is blocked, so no bundle may turn into an installable allowlist.
        _print({'ok': False, 'refused': 'activation blocked; bundles cannot produce a patch',
                'activation': policy.ACTIVATION_BLOCKED})
        return 3
    # Deliberately invalid: an accidental install would refuse (allowlist-invalid -> stock).
    allow = ('format=%s\ns01app_md5=%s\nsource_md5=%s\nsource_size=%d\n'
             'candidate=PENDING-GATE-E1\nlibrary=PENDING-GATE-E1\n'
             % (R.ALLOW_FORMAT, policy.STOCK_S01APP_MD5, policy.STOCK_SDVCAM_MD5, policy.STOCK_SDVCAM_SIZE))
    files = [('etc/init.d/S00cammy-select', '100755', PROPOSAL.read_text()),
             ('etc/cammy/native-select.enabled', '100600', 'mode=observe\n'),
             ('etc/cammy/native-select.allow', '100600', allow)]
    parts = [
        '# CammyOS WP4 bootstrap PATCH PROPOSAL (2026-11-14, WP5 rev). BLOCKED, INERT REVIEW ARTIFACT.\n',
        '# ACTIVATION INELIGIBLE: the hook ships SELECT_GATE=blocked (mode=select -> stock select-blocked)\n',
        '# and the allowlist is a placeholder. See docs/work-packages/WP5-ACCEPTANCE.md (gate EA).\n',
        '# Target: the camera internal writable overlay (JFFS2) root, NOT this repository,\n',
        '# NOT the TF runtime and NOT a host /etc. No tool in this repository applies it.\n',
        '# Applying it requires gate E2 (explicit approval) after gates E1 and E3.\n',
        '# Rollback: delete these three files and /etc/cammy/native-select/ (state), then sync.\n',
        '# Disarm without removal: delete /etc/cammy/native-select.enabled (hook exits silently).\n',
        '# Without a card arm, every boot is stock; mode=observe never selects.\n',
    ]
    for name, mode, text in files:
        lines = text.splitlines(keepends=True)
        parts += [f'diff --git a/{name} b/{name}\n', f'new file mode {mode}\n', '--- /dev/null\n',
                  f'+++ b/{name}\n', f'@@ -0,0 +1,{len(lines)} @@\n']
        parts += ['+' + line for line in lines]
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'x') as handle:
        handle.write(''.join(parts))
    _print({'ok': True, 'patch': str(out), 'applied': False, 'allowlist': 'placeholder', 'activation': 'blocked'})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='native_trial', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('status')
    sub.add_parser('gates')
    p = sub.add_parser('classify')
    p.add_argument('--candidate', required=True)
    p.add_argument('--source')
    p.add_argument('--library')
    p.add_argument('--class', dest='declared_class')
    p.add_argument('--review')
    p.add_argument('--evidence-root')
    p = sub.add_parser('make-bundle')
    for name in ('candidate', 'source', 'library', 'out', 'review'):
        p.add_argument(f'--{name}', required=True)
    p.add_argument('--class', dest='declared_class', required=True)
    p.add_argument('--attempt')
    p.add_argument('--evidence-root')
    p = sub.add_parser('validate-bundle')
    p.add_argument('bundle')
    p.add_argument('--source', required=True)
    p.add_argument('--evidence-root', required=True)
    p = sub.add_parser('review-template')
    for name in ('candidate', 'source', 'library', 'out'):
        p.add_argument(f'--{name}', required=True)
    p = sub.add_parser('make-ack')
    p.add_argument('--attempt', required=True)
    p.add_argument('--observed-boot', required=True)
    p.add_argument('--outcome', required=True, choices=R.ACK_OUTCOMES)
    p.add_argument('--out', required=True)
    sub.add_parser('simulate')
    p = sub.add_parser('proposal-patch')
    p.add_argument('--out', required=True)
    p.add_argument('--bundle')
    args = parser.parse_args(argv)
    handler = {'status': cmd_status, 'gates': cmd_gates, 'classify': cmd_classify,
               'make-bundle': cmd_make_bundle, 'validate-bundle': cmd_validate_bundle,
               'make-ack': cmd_make_ack, 'simulate': cmd_simulate, 'review-template': cmd_review_template,
               'proposal-patch': cmd_proposal_patch}.get(args.command or 'status')
    return handler(args)


if __name__ == '__main__':
    sys.exit(main())
