"""CLI for Chunk 4 boot-launch proposal generation and local stock-fallback verification.

No apply/install command exists. Generated vendor-derived scripts/diffs/manifests
are written ONLY into ignored build/boot-launch/, private/ownership-program/chunk4/,
or temporary directories with mode 0644 and deployed=False, activation_eligible=False.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .generator import (
    BUILD_OUT_ROOT,
    HELPER_PROPOSAL_PATH,
    SAVED_STOCK_S01APP,
    SourceVerificationError,
    emit_inactive_proposal_bundle,
    generate_proposal,
)


def _print(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + '\n')


def cmd_verify_source(args) -> int:
    try:
        proposal = generate_proposal(
            Path(args.source).read_bytes(),
            Path(args.helper).read_bytes(),
        )
    except (OSError, SourceVerificationError, UnicodeDecodeError) as exc:
        _print({'ok': False, 'refused': str(exc)})
        return 3
    _print({'ok': True, 'manifest': proposal.manifest()})
    return 0


def cmd_emit_proposal(args) -> int:
    try:
        result = emit_inactive_proposal_bundle(
            Path(args.out_dir),
            source_path=Path(args.source),
            helper_path=Path(args.helper),
        )
    except (OSError, ValueError, SourceVerificationError, UnicodeDecodeError) as exc:
        _print({'ok': False, 'refused': str(exc)})
        return 3
    _print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='boot_launch',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command')

    p_ver = sub.add_parser('verify-source', help='Verify exact stock S01app and helper without writing files')
    p_ver.add_argument('--source', default=str(SAVED_STOCK_S01APP))
    p_ver.add_argument('--helper', default=str(HELPER_PROPOSAL_PATH))

    p_emit = sub.add_parser('emit-proposal', help='Emit inactive mode-0644 proposal bundle into ignored dir')
    p_emit.add_argument('--out-dir', default=str(BUILD_OUT_ROOT))
    p_emit.add_argument('--source', default=str(SAVED_STOCK_S01APP))
    p_emit.add_argument('--helper', default=str(HELPER_PROPOSAL_PATH))

    args = parser.parse_args(argv)
    if args.command == 'emit-proposal':
        return cmd_emit_proposal(args)
    return cmd_verify_source(args)


if __name__ == '__main__':
    sys.exit(main())
