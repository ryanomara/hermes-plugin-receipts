"""Command line surface.

``python -m receipts <target>``            standalone
``hermes receipts <target>``               through the plugin

The same options serve both; only the name in the usage line differs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .dbio import ReceiptsError, state_db_path
from .manifest import build_manifest
from .render import render_html

__all__ = ["main", "add_arguments", "run", "default_out_path"]


def default_out_path(manifest: dict) -> Path:
    short = manifest["sessions"][0]["short_id"] if manifest.get("sessions") else "session"
    if manifest.get("multi"):
        short = f"{len(manifest['sessions'])}-sessions"
    return Path.cwd() / f"receipts-{short}.html"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".receipts-", suffix=path.suffix or ".html", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Options shared by the standalone and plugin entry points."""
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="session id or prefix, 'latest', a date (2026-11-01), a range "
             "(2026-11-01..2026-11-03), or a kanban card id (t_ab12cd34). Default: latest",
    )
    parser.add_argument("-o", "--out", help="output .html path (default: ./receipts-<session>.html)")
    parser.add_argument("--db", help="path to state.db (default: the active Hermes home)")
    parser.add_argument("--json", action="store_true", help="print the manifest JSON instead of writing HTML")
    parser.add_argument("--list", action="store_true", help="list recent sessions and exit")
    parser.add_argument("--open", action="store_true", help="open the artifact in the default browser")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print the artifact path")
    parser.add_argument("--limit", type=int, default=15, help="rows for --list (default 15)")
    return parser


def _list_sessions(db: Path | None, limit: int) -> int:
    from .dbio import open_ro

    with open_ro(db) as conn:
        rows = conn.execute(
            "SELECT id, title, source, message_count, tool_call_count, started_at, "
            "estimated_cost_usd FROM sessions WHERE COALESCE(hidden,0)=0 "
            "ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
    if not rows:
        print("No sessions in this store yet.", file=sys.stderr)
        return 1
    from datetime import datetime

    print(f"{'session':<14} {'started':<17} {'src':<8} {'msgs':>5} {'tools':>6} {'cost':>10}  title")
    for row in rows:
        started = datetime.fromtimestamp(row["started_at"]).strftime("%Y-%m-%d %H:%M") if row["started_at"] else "—"
        cost = row["estimated_cost_usd"]
        cost_txt = f"${cost:.4f}" if isinstance(cost, (int, float)) and cost else "—"
        title = (row["title"] or "")[:52]
        print(f"{row['id'][:12]:<14} {started:<17} {(row['source'] or ''):<8} "
              f"{row['message_count'] or 0:>5} {row['tool_call_count'] or 0:>6} {cost_txt:>10}  {title}")
    return 0


def run(args: argparse.Namespace) -> int:
    db = Path(args.db).expanduser() if getattr(args, "db", None) else state_db_path()

    if getattr(args, "list", False):
        return _list_sessions(db, getattr(args, "limit", 15))

    manifest = build_manifest(getattr(args, "target", None), db_path=db)

    if getattr(args, "json", False):
        from .render import render_json

        print(render_json(manifest))
        return 0

    html = render_html(manifest)
    out = Path(args.out).expanduser() if getattr(args, "out", None) else default_out_path(manifest)
    out = out if out.is_absolute() else (Path.cwd() / out)
    _atomic_write(out, html)

    if getattr(args, "quiet", False):
        print(out)
    else:
        agg = manifest["aggregates"]
        recon = manifest["reconciliation"]
        cost = recon["usage_cost"] or recon["recorded_cost"]
        print(f"receipt → {out}")
        print(f"  {len(manifest['sessions'])} session(s) · {agg['tool_calls']} tool calls · "
              f"{agg['failures']} failure(s) · {agg['retries']} retry(ies)")
        print(f"  tokens {agg['usage']['input']:,} in / {agg['usage']['output']:,} out · "
              f"cost ${cost:.6f} ({recon.get('cost_status') or 'unrecorded'})")
        print(f"  {len(html.encode('utf-8')) / 1024:.0f} KB · opens offline")

    if getattr(args, "open", False):
        try:
            import webbrowser

            webbrowser.open(out.resolve().as_uri())
        except Exception:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes receipts",
        description="Render a Hermes session (or date range, or kanban card) as one self-contained HTML receipt.",
    )
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except ReceiptsError as exc:
        print(f"receipts: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # never dump a traceback at a user
        if os.environ.get("RECEIPTS_DEBUG"):
            raise
        print(f"receipts: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
