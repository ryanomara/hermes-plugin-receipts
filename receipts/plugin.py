"""Plugin adapter: the agent-facing tool and the ``hermes receipts`` subcommand.

Import-time is inert — no database is opened, no file is written, no network is
touched until a handler actually runs.  A plugin that throws while loading takes
the whole agent down with it, so every failure here is converted into a
structured error string.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import __version__
from .cli import add_arguments, run
from .dbio import ReceiptsError
from .manifest import build_manifest
from .render import render_html

logger = logging.getLogger(__name__)

TOOL_NAME = "receipts_build"

SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Render a Hermes session as one self-contained HTML 'receipt': the original ask, every "
            "tool call as a timed node, failures and retries as recorded, per-model token spend and "
            "recorded cost, and a work-flow diagram. Reads the local session store only; no network. "
            "Use when the user wants to show, share, archive or audit what an agent actually did."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "What to render: 'latest' (default), a session id or unique prefix, a date "
                        "'2026-11-01', a range '2026-11-01..2026-11-03', or a kanban card id "
                        "'t_ab12cd34' for its full evidence chain."
                    ),
                },
                "out": {
                    "type": "string",
                    "description": "Output .html path. Defaults to ./receipts-<session>.html",
                },
            },
            "required": [],
        },
    },
}


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def receipts_build(args: dict | None = None, **_kwargs) -> str:
    """Tool handler. Returns a JSON string, never raises."""
    args = args or {}
    target = (args.get("target") or "latest").strip()
    out_arg = (args.get("out") or "").strip()

    try:
        manifest = build_manifest(target)
        html = render_html(manifest)

        if out_arg:
            out = Path(out_arg).expanduser()
            if not out.is_absolute():
                out = Path.cwd() / out
        else:
            short = manifest["sessions"][0]["short_id"] if manifest.get("sessions") else "session"
            if manifest.get("multi"):
                short = f"{len(manifest['sessions'])}-sessions"
            out = Path.cwd() / f"receipts-{short}.html"

        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(html, encoding="utf-8", newline="\n")
        tmp.replace(out)

        agg = manifest["aggregates"]
        recon = manifest["reconciliation"]
        return json.dumps({
            "ok": True,
            "artifact": str(out),
            "bytes": len(html.encode("utf-8")),
            "sessions": len(manifest["sessions"]),
            "tool_calls": agg["tool_calls"],
            "failures": agg["failures"],
            "retries": agg["retries"],
            "tokens_in": agg["usage"]["input"],
            "tokens_out": agg["usage"]["output"],
            "cost_usd": recon["usage_cost"] or recon["recorded_cost"],
            "cost_status": recon.get("cost_status"),
            "note": "Open the file directly; it needs no network and no server.",
        }, ensure_ascii=False)
    except ReceiptsError as exc:
        return _error(str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("receipts_build failed")
        return _error(f"{type(exc).__name__}: {exc}")


def _cli_handler(args) -> int:
    return run(args)


def _setup_cli(subparser) -> None:
    add_arguments(subparser)
    subparser.set_defaults(func=_cli_handler)


def register(ctx) -> None:  # noqa: D401 - Hermes plugin entry point
    """Wire the tool and the CLI subcommand."""
    ctx.register_tool(
        name=TOOL_NAME,
        toolset="receipts",
        schema=SCHEMA,
        handler=receipts_build,
        description="Render a Hermes session as a self-contained HTML receipt.",
        emoji="🧾",
    )
    ctx.register_cli_command(
        name="receipts",
        help="Render a session as a self-contained HTML receipt",
        setup_fn=_setup_cli,
        handler_fn=_cli_handler,
        description=(
            "Turn a Hermes session, a date range, or a kanban card's evidence chain into one "
            "offline HTML document: timeline, failures, retries, token spend, recorded cost."
        ),
    )
    logger.debug("receipts %s registered (%s + `hermes receipts`)", __version__, TOOL_NAME)
