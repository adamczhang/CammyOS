"""Chunk 4/5 boot-launch proposal generator, stock fallback, and one-shot selection package.

Offline local package only:
- Verifies exact stock S01app input identity before emitting any proposal;
- Embeds the inert launch helper (SELECT_GATE=blocked) into S01app while
  mechanically preserving unrelated setup/stop text;
- Replaces the legacy any-vfat backup condition with the tightened normal-card
  backup gate (/mnt/extsd mounted vfat from /dev/mmcblk0{,p1} and no private
  staging residue);
- Replaces all five launch sites (lines 91, 99, 101, 114, 118) with the single
  helper launch entrypoint;
- Implements one-shot status-only candidate preparation and launch behind the
  still-blocked shipped gate, exercised locally via synthetic fixture relocation
  under dash with confined PATH stubs and a Python reference model.
"""
from __future__ import annotations

from .generator import (
    EXPECTED_BACKUP_LINE,
    EXPECTED_BACKUP_LINE_NO,
    EXPECTED_LAUNCH_LINES,
    HELPER_PROPOSAL_PATH,
    STOCK_S01APP_LINES,
    STOCK_S01APP_MD5,
    STOCK_S01APP_SHA256,
    STOCK_S01APP_SIZE,
    STOCK_SDVCAM_MD5,
    STOCK_SDVCAM_SHA256,
    STOCK_SDVCAM_SIZE,
    SourceVerificationError,
    emit_inactive_proposal_bundle,
    generate_proposal,
    verify_helper_proposal,
    verify_stock_s01app,
)
from .model import (
    INFLIGHT_FORMAT,
    INFLIGHT_KEYS,
    SYNTHETIC_CANDIDATE_BYTES,
    SYNTHETIC_LIBRARY_BYTES,
    SYNTHETIC_STOCK_BYTES,
    BootLaunchEnv,
    BootLaunchFakeTree,
    HelperDecision,
    PowerLoss,
    S01AppBranchOutcome,
    inflight_record,
    run_helper_model,
    run_s01app_branch_model,
)

__all__ = [
    'EXPECTED_BACKUP_LINE',
    'EXPECTED_BACKUP_LINE_NO',
    'EXPECTED_LAUNCH_LINES',
    'HELPER_PROPOSAL_PATH',
    'INFLIGHT_FORMAT',
    'INFLIGHT_KEYS',
    'STOCK_S01APP_LINES',
    'STOCK_S01APP_MD5',
    'STOCK_S01APP_SHA256',
    'STOCK_S01APP_SIZE',
    'STOCK_SDVCAM_MD5',
    'STOCK_SDVCAM_SHA256',
    'STOCK_SDVCAM_SIZE',
    'SYNTHETIC_CANDIDATE_BYTES',
    'SYNTHETIC_LIBRARY_BYTES',
    'SYNTHETIC_STOCK_BYTES',
    'SourceVerificationError',
    'emit_inactive_proposal_bundle',
    'generate_proposal',
    'inflight_record',
    'verify_helper_proposal',
    'verify_stock_s01app',
    'BootLaunchEnv',
    'BootLaunchFakeTree',
    'HelperDecision',
    'PowerLoss',
    'S01AppBranchOutcome',
    'run_helper_model',
    'run_s01app_branch_model',
]
