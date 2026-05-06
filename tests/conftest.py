"""Shared pytest fixtures."""

import sys
from pathlib import Path

# Ensure tests can find the in-tree src layout when running pytest without
# `pip install -e .` first.
_PKG_ROOT = Path(__file__).parent.parent / "src"
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))
