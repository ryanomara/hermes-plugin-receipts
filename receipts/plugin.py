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
from .cli import add_arguments, atomic_write, default_out_path, run
from .dbio import ReceiptsError
from .manifest import build_manifest
from .privacy import canonical_json, load_words, redact_manifest
from .render import render_html

logger = logging.getLogger(__name__)

TOOL_NAME = "receipts_build"
THEMES = ("instrument", "client")

SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Render a Hermes session as one self-contained HTML 'receipt': the original ask, every "
            "tool call as a timed node, failures and retries as recorded, per-model token spend and "
            "recorded cost, and a work-flow diagram. Reads the local session store only; no network. "
            "Use when the user wants to show, share, archive or audit what an agent actually did. "
            "Set redact=true for a share-safe copy and theme='client' for a paper document instead "
            "of the console."
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
                    "description": (
                        "Output .html path. Defaults to ./receipts-<session>.html; with redact=true "
                        "it gains .redacted: ./receipts-<session>.redacted.html"
                    ),
                },
                "theme": {
                    "type": "string",
                    "enum": list(THEMES),
                    "default": "instrument",
                    "description": (
                        "'instrument' (default): the console-styled sheet. 'client': the same "
                        "receipt as a paper document, for sending to a client."
                    ),
                },
                "redact": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Write a share-safe copy: e-mails, phone numbers, IP addresses, account "
                        "paths, word-list entries and the credential shapes the build-time masker "
                        "does not know are replaced with [class-N] placeholders. The default output "
                        "name gains .redacted and a .redaction.json sidecar is written beside it, "
                        "so the plain receipt keeps its name."
                    ),
                },
                "redact_words": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "With redact=true: extra whole words to remove — client names, bare account "
                        "handles, anything the pattern rules cannot see. A path to a word-list file "
                        "(one entry per line, # comments) is also accepted, as with the CLI's "
                        "--redact-words."
                    ),
                },
            },
            "required": [],
        },
    },
}


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _tool_words(value) -> list[str]:
    """Word list from a tool argument: a list of words, or a path (CLI parity)."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            return load_words(value)
        except OSError as exc:
            reason = getattr(exc, "strerror", None) or exc
            raise ReceiptsError(f"cannot read redact_words file {value!s}: {reason}") from exc
        except UnicodeDecodeError as exc:
            raise ReceiptsError(f"redact_words file {value!s} is not UTF-8 text") from exc
    if isinstance(value, (list, tuple)):
        words = []
        for entry in value:
            if not isinstance(entry, str):
                raise ReceiptsError("redact_words must be a list of strings or a word-list path")
            word = entry.strip()
            if word:
                words.append(word)
        return words
    raise ReceiptsError("redact_words must be a list of strings or a word-list path")


def receipts_build(args: dict | None = None, **_kwargs) -> str:
    """Tool handler. Returns a JSON string, never raises.

    Hermes calls the handler with one parsed-argument dict; the same options are
    accepted as keywords so ``receipts_build(theme="client")`` works in a REPL.
    Defaults are the v1.1.0 behaviour: instrument theme, no redaction, no
    sidecar. ``theme`` and ``redact`` reach the same code paths the CLI uses.
    """
    args = dict(args or {})
    for key in ("target", "out", "theme", "redact", "redact_words"):
        if key in _kwargs and key not in args:
            args[key] = _kwargs[key]
    target = str(args.get("target") or "latest").strip()
    out_arg = str(args.get("out") or "").strip()
    theme = str(args.get("theme") or "instrument").strip().lower()
    redact = bool(args.get("redact"))

    try:
        if theme not in THEMES:
            raise ReceiptsError(
                f"unknown theme {theme!r}: expected 'instrument' (default) or 'client'"
            )
        if args.get("redact_words") and not redact:
            raise ReceiptsError(
                "redact_words needs redact=true: it names words to remove from a share-safe copy"
            )
        words = _tool_words(args.get("redact_words"))

        manifest = build_manifest(target)

        # Redact the manifest before rendering, exactly as the CLI does: the
        # document, the sidecar and this response describe the same data, and
        # the band is drawn from the report embedded here.
        report = None
        if redact:
            manifest, report = redact_manifest(manifest, words)
            report["generated_at"] = manifest.get("generated_at")
            manifest["redaction"] = report

        html = render_html(manifest, theme=theme)

        if out_arg:
            out = Path(out_arg).expanduser()
            if not out.is_absolute():
                out = Path.cwd() / out
        else:
            # A redacted run defaults to a *new* file; the unredacted receipt of
            # the same session is never overwritten.
            out = default_out_path(manifest, redact=report is not None)

        atomic_write(out, html)

        sidecar = None
        if report is not None:
            sidecar = out.with_suffix(".redaction.json")
            atomic_write(sidecar, canonical_json(report) + "\n")

        agg = manifest["aggregates"]
        recon = manifest["reconciliation"]
        payload = {
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
            "theme": theme,
            "note": "Open the file directly; it needs no network and no server.",
        }
        if report is not None:
            payload["redaction"] = {
                "total": report["total"],
                "classes": report["classes"],
                "residual": report["residual"],
                "sidecar": str(sidecar),
            }
            payload["note"] = (
                "Redacted copy written. Redaction is pattern-based, so review the copy "
                "before sharing it."
            )
        return json.dumps(payload, ensure_ascii=False)
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
        description="Render a Hermes session as a self-contained HTML receipt, optionally redacted.",
        emoji="🧾",
    )
    ctx.register_cli_command(
        name="receipts",
        help="Render a session as a self-contained HTML receipt, or a week as a process digest",
        setup_fn=_setup_cli,
        handler_fn=_cli_handler,
        description=(
            "Turn a Hermes session, a date range, or a kanban card's evidence chain into one "
            "offline HTML document: timeline, failures, retries, token spend, recorded cost. "
            "`--redact` writes a share-safe copy, `--theme client` a paper document; "
            "`receipts rollup --since 7d` renders the weekly digest instead."
        ),
    )
    logger.debug("receipts %s registered (%s + `hermes receipts`)", __version__, TOOL_NAME)
