"""Hermes plugin entry point for Receipts.

The loader imports this file as the plugin module and calls ``register(ctx)``.
All real logic lives in the sibling :mod:`receipts` package so the same code runs
standalone (``python -m receipts``) without Hermes on the path.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def register(ctx) -> None:
    """Called once at startup. Imports lazily so load stays cheap and inert."""
    from receipts.plugin import register as _register

    _register(ctx)
