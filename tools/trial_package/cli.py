"""CLI for Chunk 6 disarmed offline native-trial review package builder and validator.

No apply, install, flash, ADB, or enable command exists. Package outputs are
written ONLY into ignored private/ownership-program/chunk6/,
build/native-trial-review/, or temporary directories with mode 0644 and verdict
LOCAL_ONLY/BLOCKED_FOR_ACTIVATION.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .package import (
    DEFAULT_PACKAGE_DIR,
    DEFAULT_TEMPLATE_ACK_OUTCOME,
    DEFAULT_TEMPLATE_ATTEMPT,
    DEFAULT_TEMPLATE_OBSERVED_BOOT,
    HELPER_PROPOSAL_PATH,
    RUNBOOK_DOC_PATH,
    SAVED_STATUS_CANDIDATE,
    SAVED_STATUS_LIBRARY,
    SAVED_STOCK_S01APP,
    SAVED_STOCK_SDVCAM,
    SAVED_WP2A_EVIDENCE_ROOT,
    SAVED_WP5_REVIEW,
    TrialPackageError,
    VERDICT_REJECTED,
    build_review_package,
    compact_public_summary,
    validate_review_package,
)

FORBIDDEN_FLAGS = (
    '--apply',
    '--install',
    '--flash',
    '--adb',
    '--deploy',
    '--enable',
    '--open-gate',
)
FORBIDDEN_COMMANDS = ('apply', 'install', 'flash', 'adb', 'deploy', 'enable')


def _print(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + '\n')


def cmd_build(args) -> int:
    try:
        result = build_review_package(
            Path(args.out_dir),
            stock_s01app_path=Path(args.stock_s01app),
            stock_sdvcam_path=Path(args.stock_sdvcam),
            helper_path=Path(args.helper),
            candidate_path=Path(args.candidate),
            library_path=Path(args.library),
            review_path=Path(args.review),
            evidence_root=Path(args.evidence_root),
            runbook_path=Path(args.runbook),
            attempt=args.attempt,
            observed_boot_placeholder=args.observed_boot_placeholder,
            ack_outcome=args.ack_outcome,
        )
    except (OSError, ValueError, TrialPackageError) as exc:
        _print({
            'ok': False,
            'verdict': VERDICT_REJECTED,
            'activation_eligible': False,
            'installation_authorized': False,
            'deployed': False,
            'refused': str(exc),
        })
        return 3
    _print(result)
    return 0


def cmd_validate(args) -> int:
    report = validate_review_package(
        Path(args.package_dir),
        stock_s01app_path=Path(args.stock_s01app) if args.stock_s01app else None,
        stock_sdvcam_path=Path(args.stock_sdvcam) if args.stock_sdvcam else None,
        helper_path=Path(args.helper) if args.helper else None,
        evidence_root=Path(args.evidence_root) if args.evidence_root else None,
    )
    _print(report)
    return 0 if report['ok'] else 3


def cmd_summary(args) -> int:
    report = compact_public_summary(Path(args.package_dir))
    _print(report)
    return 0 if report['ok'] else 3


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    for token in raw_argv:
        head = token.split('=', 1)[0]
        if head in FORBIDDEN_FLAGS or token in FORBIDDEN_COMMANDS:
            _print({
                'ok': False,
                'verdict': VERDICT_REJECTED,
                'activation_eligible': False,
                'installation_authorized': False,
                'deployed': False,
                'refused': f'forbidden action/flag {token!r}: review package tool never applies, installs, flashes, or enables trials',
            })
            return 3

    parser = argparse.ArgumentParser(
        prog='trial_package',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command')

    p_build = sub.add_parser('build', help='Build disarmed mode-0644 review package into ignored dir')
    p_build.add_argument('--out-dir', default=str(DEFAULT_PACKAGE_DIR))
    p_build.add_argument('--stock-s01app', default=str(SAVED_STOCK_S01APP))
    p_build.add_argument('--stock-sdvcam', default=str(SAVED_STOCK_SDVCAM))
    p_build.add_argument('--helper', default=str(HELPER_PROPOSAL_PATH))
    p_build.add_argument('--candidate', default=str(SAVED_STATUS_CANDIDATE))
    p_build.add_argument('--library', default=str(SAVED_STATUS_LIBRARY))
    p_build.add_argument('--review', default=str(SAVED_WP5_REVIEW))
    p_build.add_argument('--evidence-root', default=str(SAVED_WP2A_EVIDENCE_ROOT))
    p_build.add_argument('--runbook', default=str(RUNBOOK_DOC_PATH))
    p_build.add_argument('--attempt', default=DEFAULT_TEMPLATE_ATTEMPT)
    p_build.add_argument('--observed-boot-placeholder', default=DEFAULT_TEMPLATE_OBSERVED_BOOT)
    p_build.add_argument('--ack-outcome', default=DEFAULT_TEMPLATE_ACK_OUTCOME)

    p_val = sub.add_parser('validate', help='Validate an existing offline review package directory')
    p_val.add_argument('package_dir', nargs='?', default=str(DEFAULT_PACKAGE_DIR))
    p_val.add_argument('--stock-s01app', default=str(SAVED_STOCK_S01APP))
    p_val.add_argument('--stock-sdvcam', default=str(SAVED_STOCK_SDVCAM))
    p_val.add_argument('--helper', default=str(HELPER_PROPOSAL_PATH))
    p_val.add_argument('--evidence-root', default=str(SAVED_WP2A_EVIDENCE_ROOT))

    p_sum = sub.add_parser('summary', help='Print compact public-safe summary of a review package')
    p_sum.add_argument('package_dir', nargs='?', default=str(DEFAULT_PACKAGE_DIR))

    args = parser.parse_args(raw_argv)
    if args.command == 'build':
        return cmd_build(args)
    if args.command == 'summary':
        return cmd_summary(args)
    if args.command == 'validate':
        return cmd_validate(args)
    # Default when invoked without subcommand: validate default package dir.
    default_args = parser.parse_args(['validate'])
    return cmd_validate(default_args)


if __name__ == '__main__':
    sys.exit(main())
