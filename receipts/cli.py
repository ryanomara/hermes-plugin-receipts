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


def default_rollup_path(payload: dict) -> Path:
    window = payload["window"]
    return Path.cwd() / f"rollup-{window['start'][:10]}_{window['end'][:10]}.html"


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
             "(2026-11-01..2026-11-03), a kanban card id (t_ab12cd34), or 'rollup' "
             "for the weekly process digest. Default: latest",
    )
    parser.add_argument("-o", "--out", help="output .html path (default: ./receipts-<session>.html); "
                                            "for rollup, the base path without extension")
    parser.add_argument("--db", help="path to state.db (default: the active Hermes home)")
    parser.add_argument("--json", action="store_true", help="print the manifest/rollup JSON instead of writing HTML")
    parser.add_argument("--list", action="store_true", help="list recent sessions and exit")
    parser.add_argument("--open", action="store_true", help="open the artifact in the default browser")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print the artifact path")
    parser.add_argument("--limit", type=int, default=15, help="rows for --list (default 15)")
    parser.add_argument("--theme", choices=("instrument", "client"), default="instrument",
                        help="instrument (console, default) or client (paper document for sharing)")
    parser.add_argument("--redact", action="store_true",
                        help="write a share-safe copy: secrets, emails, user paths, IPs and phones "
                             "removed, with a redaction manifest beside the file")
    parser.add_argument("--redact-words", dest="redact_words",
                        help="file of extra words to remove (one per line, e.g. client names)")
    # rollup-only
    parser.add_argument("--since", help="rollup window: 7d, 36h, 2w, or a date (2026-10-30)")
    parser.add_argument("--board", help="rollup: restrict to one kanban board slug (e.g. agency)")
    parser.add_argument("--as-of", dest="as_of",
                        help="rollup: pin the window end (ISO datetime or date) — makes a run byte-stable")
    parser.add_argument("--kanban-home", dest="kanban_home",
                        help="rollup: kanban home holding boards/<slug>/kanban.db "
                             "(default: <hermes home>/kanban)")
    parser.add_argument("--include-open", dest="include_open", action="store_true",
                        help="rollup: include sessions still open at the cutoff (non-deterministic)")
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


def run_rollup(args: argparse.Namespace, db: Path) -> int:
    """`receipts rollup --since 7d [--board <slug>]` — the weekly digest."""
    from .rollup import build_rollup, render_json as render_rollup_json
    from .render_rollup import render_rollup

    kanban = Path(args.kanban_home).expanduser() if getattr(args, "kanban_home", None) else None
    payload = build_rollup(
        since=getattr(args, "since", None) or "7d",
        board=getattr(args, "board", None),
        as_of=getattr(args, "as_of", None),
        db_path=db,
        kanban_home_path=kanban,
        include_open=getattr(args, "include_open", False),
    )

    if getattr(args, "json", False):
        print(render_rollup_json(payload), end="")
        return 0

    html = render_rollup(payload)
    out = Path(args.out).expanduser() if getattr(args, "out", None) else default_rollup_path(payload)
    out = out if out.is_absolute() else (Path.cwd() / out)
    stem = out.with_suffix("") if out.suffix.lower() in (".html", ".json") else out
    html_path = stem.with_suffix(".html")
    json_path = stem.with_suffix(".json")
    _atomic_write(html_path, html)
    _atomic_write(json_path, render_rollup_json(payload))

    if getattr(args, "quiet", False):
        print(html_path)
    else:
        cur = payload["current"]
        rate = (cur["failures"] / cur["tool_calls"] * 100.0) if cur["tool_calls"] else 0.0
        top = cur["taxonomy"][0] if cur["taxonomy"] else None
        print(f"rollup → {html_path}")
        print(f"         {json_path}")
        print(f"  {payload['window']['week']} · {cur['sessions']} session(s) · "
              f"{cur['tool_calls']:,} tool calls · {cur['failures']:,} failure(s) ({rate:.1f}%) · "
              f"{cur['retries']:,} retry(ies)")
        print(f"  cost ${(cur['cost']['usage_usd'] or 0):.4f} · "
              f"top class {top['class'] if top else '—'}"
              f"{' ×' + str(top['count']) if top else ''} · "
              f"{(payload.get('stability') or {}).get('digest', '')}")

    if getattr(args, "open", False):
        try:
            import webbrowser

            webbrowser.open(html_path.resolve().as_uri())
        except Exception:
            pass
    return 0


def run(args: argparse.Namespace) -> int:
    db = Path(args.db).expanduser() if getattr(args, "db", None) else state_db_path()

    if getattr(args, "list", False):
        return _list_sessions(db, getattr(args, "limit", 15))

    if (getattr(args, "target", None) or "").strip().lower() == "rollup":
        return run_rollup(args, db)
    if any(getattr(args, flag, None) for flag in ("since", "board", "as_of", "kanban_home",
                                                  "include_open")):
        raise ReceiptsError(
            "--since / --board / --as-of / --kanban-home / --include-open belong to the rollup "
            "target. Try: receipts rollup --since 7d --board agency"
        )

    manifest = build_manifest(getattr(args, "target", None), db_path=db)

    # Redaction runs on the manifest, before rendering: the document, the JSON
    # and the sidecar all describe the same (already redacted) data.
    redaction_report = None
    if getattr(args, "redact", False):
        from .privacy import redact_manifest
        from .privacy import _load_words
        words = _load_words(getattr(args, "redact_words", None))
        manifest, redaction_report = redact_manifest(manifest, words)
        import time as _time
        redaction_report["generated_at"] = _time.strftime("%Y-%m-%dT%H:%M:%S%z")
        manifest["redaction"] = redaction_report

    if getattr(args, "json", False):
        from .render import render_json

        print(render_json(manifest))
        return 0

    html = render_html(manifest, theme=getattr(args, "theme", "instrument") or "instrument")
    out = Path(args.out).expanduser() if getattr(args, "out", None) else default_out_path(manifest)
    out = out if out.is_absolute() else (Path.cwd() / out)
    _atomic_write(out, html)
    if redaction_report:
        import json as _json
        _atomic_write(out.with_suffix(".redaction.json"), _json.dumps(redaction_report, indent=2))

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
        description="Render a Hermes session (or date range, or kanban card) as one self-contained HTML receipt, "
                    "or roll a week of sessions up into a deterministic process digest (`receipts rollup --since 7d`).",
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
