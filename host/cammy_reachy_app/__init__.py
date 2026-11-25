"""Cammy ParamFace application for the official Reachy Mini app manager."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'sdk'))
