"""Stable repository root, independent of process working directory."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
