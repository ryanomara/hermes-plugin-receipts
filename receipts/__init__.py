"""Receipts — a session flight recorder for Hermes.

Turns the local session store into one self-contained HTML document: what the
agent was asked, every tool call as a timed node, failures and retries exactly
as recorded, per-model token spend, and the recorded cost.

Public API::

    from receipts import build_manifest, render_html
    html = render_html(build_manifest("latest"))
"""

__version__ = "1.2.0"
__all__ = [
    "__version__",
    "build_manifest",
    "build_rollup",
    "render_html",
    "render_rollup",
    "render_json",
    "main",
]

from .manifest import build_manifest  # noqa: E402
from .render import render_html, render_json  # noqa: E402
from .rollup import build_rollup  # noqa: E402
from .render_rollup import render_rollup  # noqa: E402
from .cli import main  # noqa: E402
