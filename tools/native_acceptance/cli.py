"""``python3 tools/native_acceptance`` — local stage-5 evidence validation (WP5).

Commands: ``spec`` (machine-readable acceptance spec), ``validate REPORT``
(per-stage status and verdict) and ``template`` (a FIXTURE report to fill in).
There is no hardware backend and no command that touches a camera, card,
device, process or network. Writes go only under build/native-acceptance/ or the
temp dir. Exit codes: 0 HARDWARE_EVIDENCE_COMPLETE (still not an approval),
4 LOCAL_ONLY, 3 any other verdict or refusal, 2 usage.

A report outside the retained private evidence tree (private/) is capped at
LOCAL_ONLY whatever it claims, so a fixture copied into build/ or /tmp cannot
present as hardware evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import fixtures, spec, validate

REPO = Path(__file__).resolve().parents[2]
STAGING_ROOT = REPO / 'build' / 'native-acceptance'
EVIDENCE_TREE = REPO / 'private'


def _print(data) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, default=str))


def _new_output(path: str) -> Path | None:
    out = Path(path).resolve()
    allowed = [STAGING_ROOT.resolve(), Path(tempfile.gettempdir()).resolve()]
    if not any(root in out.parents for root in allowed) or out.exists():
        return None
    return out


def run_validate(report_path: Path, evidence_root: Path | None, prerequisite: Path | None,
                 now: datetime | None) -> dict:
    report, digest = validate.load(report_path)
    pre = None
    if prerequisite is not None:
        pre_report, pre_digest = validate.load(prerequisite)
        pre = run_validate(prerequisite, evidence_root, None, now)
        pre['report_sha256'] = pre_digest
        if not isinstance(pre_report, dict) or pre_report.get('profile') != 'status-only':
            pre['hardware_pass'] = False
    result = validate.validate(report, now=now, evidence_root=evidence_root, prerequisite=pre)
    result['report'] = str(report_path)
    result['report_sha256'] = digest
    inside = EVIDENCE_TREE.resolve() in Path(report_path).resolve().parents
    if result['hardware_pass'] and not inside:
        result['hardware_pass'] = False
        result['verdict'] = 'LOCAL_ONLY'
        result['local_reasons'].append('report is not in the retained private evidence tree')
    return result


def cmd_validate(args) -> int:
    now = validate.parse_utc(args.now) if args.now else None
    if args.now and now is None:
        _print({'ok': False, 'refused': '--now must be ISO UTC ending in Z'})
        return 2
    try:
        result = run_validate(Path(args.report), Path(args.evidence_root) if args.evidence_root else None,
                              Path(args.prerequisite) if args.prerequisite else None, now)
    except (OSError, ValueError) as error:
        _print({'ok': False, 'refused': f'unreadable report: {error}'})
        return 3
    if args.out:
        out = _new_output(args.out)
        if out is None:
            _print({'ok': False, 'refused': 'output must be a new file under build/native-acceptance/ or temp'})
            return 3
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'x') as handle:
            handle.write(json.dumps(result, indent=2, sort_keys=True, default=str) + '\n')
    _print(result)
    return {'HARDWARE_EVIDENCE_COMPLETE': 0, 'LOCAL_ONLY': 4}.get(result['verdict'], 3)


def cmd_template(args) -> int:
    out = _new_output(args.out)
    if out is None:
        _print({'ok': False, 'refused': 'output must be a new file under build/native-acceptance/ or temp'})
        return 3
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'x') as handle:
        handle.write(json.dumps(fixtures.report(args.profile), indent=2, sort_keys=True) + '\n')
    _print({'ok': True, 'template': str(out), 'fixture': True,
            'note': 'FIXTURE values; replace from real captures only. Fixtures validate as LOCAL_ONLY at best.'})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='native_acceptance', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('spec')
    p = sub.add_parser('validate')
    p.add_argument('report')
    p.add_argument('--evidence-root')
    p.add_argument('--prerequisite', help='validated status-only report (video profile)')
    p.add_argument('--now', help='validation time, ISO UTC ending in Z (default: now)')
    p.add_argument('--out')
    p = sub.add_parser('template')
    p.add_argument('--profile', choices=spec.PROFILES, default='status-only')
    p.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if args.command in (None, 'spec'):
        _print(spec.describe())
        return 0
    return {'validate': cmd_validate, 'template': cmd_template}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
