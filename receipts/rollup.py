"""Weekly rollup: turn per-session receipts into a process-improvement digest.

Where a receipt answers *what happened in this session*, a rollup answers *what
keeps happening across sessions* — which failures recur, what they cost, which
tools are slow, which cards burn the most retries, and whether any of it moved
since last week.

Two rules govern this module:

1. **Deterministic by construction.**  Windows are explicit, every ordering has
   a tiebreaker, all timestamps are UTC, all money is rounded once at the end,
   and nothing in the payload is derived from render time.  Re-running a rollup
   over the same closed window produces byte-identical JSON and HTML.
2. **Nothing is estimated.**  Cost is the figure Hermes recorded, summed from
   the same rows a receipt shows; sessions without usage rows are counted and
   named, never priced.  Failure classes come from ordered regex rules over the
   recorded error text — no model, no embedding, no judgement call.

The module only reads: ``state.db`` (sessions/messages/session_model_usage) and
the per-board ``kanban.db`` files under the Hermes home.  No network.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .dbio import ReceiptsError, db_identity, hermes_home, open_ro, state_db_path
from .manifest import (
    _build_nodes,
    _clip,
    _first_line,
    _json_maybe,
    _load_messages,
    _session_meta,
    _usage_rows,
    redact,
)

__all__ = [
    "build_rollup",
    "render_json",
    "parse_window",
    "canonical_digest",
    "classify_tool_failure",
    "TAXONOMY_RULES",
]

ROLLUP_SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #


def _iso(ts: float | None) -> str | None:
    """UTC, second precision — never the renderer's local clock."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError, OverflowError):
        return None


def _day(ts: float | None) -> str:
    if ts is None:
        return "—"
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return "—"


_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([hdw])$", re.I)
_DURATION_UNITS = {"h": 3600.0, "d": 86400.0, "w": 604800.0}


def parse_window(since: str | None, as_of: str | None = None) -> dict:
    """Resolve ``--since`` / ``--as-of`` into a closed, explicit window.

    ``since`` accepts a duration (``36h``, ``7d``, ``2w``) or an ISO date
    (``2026-10-30``, start of that day, local).  ``as_of`` accepts an ISO
    datetime, a date (end of that day, local) or an epoch float.  Default
    ``as_of`` is now, which is why the cron pins the window in its log line.
    """
    text = (since or "7d").strip()
    if as_of in (None, "", "now"):
        end = datetime.now(timezone.utc).timestamp()
    else:
        end = _parse_moment(as_of, end_of_day=True)

    m = _DURATION_RE.match(text)
    if m:
        seconds = float(m.group(1)) * _DURATION_UNITS[m.group(2).lower()]
        start = end - seconds
        since_label = text.lower()
    else:
        try:
            start = _parse_moment(text, end_of_day=False)
        except ReceiptsError as exc:
            raise ReceiptsError(
                f"Bad --since {since!r}. Use a duration (7d, 36h, 2w) or a date (2026-10-30)."
            ) from exc
        since_label = text

    if not (start < end):
        raise ReceiptsError("The rollup window is empty: --since resolves to a time at or after --as-of.")
    if end - start < 3600:
        raise ReceiptsError("The rollup window is shorter than one hour; widen --since.")

    span = end - start
    return {
        "since": since_label,
        "start": start,
        "end": end,
        "seconds": span,
        "days": round(span / 86400.0, 3),
        "start_iso": _iso(start),
        "end_iso": _iso(end),
        "label": f"{_day(start)} → {_day(end)}",
        "previous": {"start": start - span, "end": start,
                     "start_iso": _iso(start - span), "end_iso": _iso(start),
                     "label": f"{_day(start - span)} → {_day(start)}"},
    }


def _parse_moment(value, end_of_day: bool) -> float:
    text = str(value).strip()
    if re.match(r"^\d{9,}(?:\.\d+)?$", text):
        return float(text)
    for fmt, is_date in (("%Y-%m-%d", True), ("%Y-%m-%d %H:%M", False),
                         ("%Y-%m-%dT%H:%M", False), ("%Y-%m-%dT%H:%M:%S", False)):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        local = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local)
        ts = dt.timestamp()
        return ts + 86399.999 if (is_date and end_of_day) else ts
    try:  # ISO with timezone
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt.timestamp()
    except ValueError as exc:
        raise ReceiptsError(f"Bad timestamp {value!r}.") from exc


# --------------------------------------------------------------------------- #
# kanban boards: card index
# --------------------------------------------------------------------------- #

_CARD_HEADER = re.compile(r"#\s*Kanban task\s+(t_[0-9a-z]+)", re.I)
_TASK_COLUMNS = ("id", "title", "status", "assignee", "created_at", "completed_at", "project_id")


def kanban_home() -> Path:
    for name in ("RECEIPTS_KANBAN_HOME", "HERMES_KANBAN_HOME", "KANBAN_HOME"):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return hermes_home() / "kanban"


def _read_tasks(db_path: Path) -> dict:
    """Read one board's tasks with the columns this build actually has."""
    out = {}
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = 1")
    except sqlite3.Error as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    try:
        present = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        cols = [c for c in _TASK_COLUMNS if c in present]
        if not cols:
            return {"error": "no tasks table"}
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM tasks").fetchall()
        for row in rows:
            keys = set(row.keys())
            out[row["id"]] = {
                "id": row["id"],
                "title": (row["title"] if "title" in keys else "") or "",
                "status": (row["status"] if "status" in keys else "") or "",
                "assignee": (row["assignee"] if "assignee" in keys else "") or "",
                "created_at": row["created_at"] if "created_at" in keys else None,
                "completed_at": row["completed_at"] if "completed_at" in keys else None,
            }
        return {"tasks": out}
    except sqlite3.Error as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()


def load_boards(home: Path | None = None) -> dict:
    """Index every board under the kanban home.  Never writes, never raises."""
    home = Path(home) if home is not None else kanban_home()
    boards: dict[str, dict] = {}
    boards_dir = home / "boards"
    legacy = home / "kanban.db"

    candidates = []
    if boards_dir.is_dir():
        for entry in sorted(boards_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            db = entry / "kanban.db"
            if db.is_file():
                candidates.append((entry.name, db, entry / "board.json"))
    if not candidates and legacy.is_file():
        candidates.append(("default", legacy, home / "board.json"))

    for slug, db, meta_path in candidates:
        meta = {}
        try:
            meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        read = _read_tasks(db)
        boards[slug] = {
            "slug": slug,
            "name": meta.get("name") or slug,
            "db": str(db),
            "tasks": read.get("tasks", {}),
            "error": read.get("error"),
        }
    return boards


def _card_index(boards: dict) -> dict:
    """card id -> card, first board wins on collision (slug order is sorted)."""
    cards = {}
    for slug in sorted(boards):
        for cid, task in boards[slug]["tasks"].items():
            if cid in cards:
                cards[cid].setdefault("also_on", []).append(slug)
                continue
            cards[cid] = dict(task, board=slug)
    return cards


# --------------------------------------------------------------------------- #
# session -> card attribution
# --------------------------------------------------------------------------- #


def _first_user_text(conn, session_id: str) -> str:
    row = conn.execute(
        "SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id LIMIT 1",
        (session_id,),
    ).fetchone()
    if not row:
        return ""
    raw = row["content"] or ""
    parsed = _json_maybe(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
        return parsed["text"]
    if isinstance(parsed, list):
        parts = [p.get("text", "") for p in parsed if isinstance(p, dict)]
        return "\n".join(p for p in parts if isinstance(p, str))
    return raw if isinstance(raw, str) else ""


def attribute_sessions(conn, session_ids: list[str], metas: dict, cards: dict) -> dict:
    """Map each session to the card it worked on, deterministically.

    Order of evidence:
      1. the worker transcript's own header — ``# Kanban task t_xxxxxxxx`` in the
         first user message (the dispatcher writes this, so it is the card, not a
         mention of a card);
      2. inheritance from ``parent_session_id`` (sub-agent work belongs to the
         card its parent worked on);
      3. an exact, unique title match against the board index (legacy sessions
         written before the header existed).
    Anything else stays unattributed rather than being guessed at.
    """
    attribution: dict[str, dict] = {}
    for sid in session_ids:
        meta = metas.get(sid) or {}
        text = _first_user_text(conn, sid)
        m = _CARD_HEADER.search(text or "")
        if m:
            attribution[sid] = {"card": m.group(1), "via": "transcript"}
    # 3. exact unique title match, only for sessions still unmapped
    by_title: dict[str, list[str]] = {}
    for cid, card in cards.items():
        title = (card.get("title") or "").strip()
        if title:
            by_title.setdefault(title, []).append(cid)
    for sid in session_ids:
        if sid in attribution:
            continue
        title = ((metas.get(sid) or {}).get("title") or "").strip()
        hits = by_title.get(title) if title else None
        if hits and len(hits) == 1:
            attribution[sid] = {"card": hits[0], "via": "title"}

    # 2. parent inheritance, resolving parents outside the window too
    parent_card_cache: dict[str, str | None] = {}

    def card_of(sid: str, depth: int = 0) -> str | None:
        if sid in attribution:
            return attribution[sid]["card"]
        if depth > 6 or sid in parent_card_cache:
            return parent_card_cache.get(sid)
        parent = (metas.get(sid) or {}).get("parent_session_id")
        if not parent:
            parent_card_cache[sid] = None
            return None
        parent_card_cache[sid] = None  # cycle guard
        parent_meta = metas.get(parent)
        if parent_meta is None:
            try:
                parent_meta = _session_meta(conn, parent)
                metas[parent] = parent_meta
            except ReceiptsError:
                parent_meta = None
        if parent_meta is not None:
            m = _CARD_HEADER.search(_first_user_text(conn, parent) or "")
            if m:
                attribution[parent] = {"card": m.group(1), "via": "transcript"}
                parent_card_cache[sid] = m.group(1)
                return m.group(1)
        inherited = card_of(parent, depth + 1)
        parent_card_cache[sid] = inherited
        return inherited

    for sid in session_ids:
        if sid in attribution:
            continue
        inherited = card_of(sid)
        if inherited:
            attribution[sid] = {"card": inherited, "via": "parent"}
    return attribution


# --------------------------------------------------------------------------- #
# failure taxonomy — ordered rules, no model
# --------------------------------------------------------------------------- #

#: Ordered ``(class, pattern, refined_from)``.  First match wins, so the order is
#: part of the contract.  Three lessons are baked in here:
#:
#: * a *tool-contract* error is tested first — a call rejected for a missing
#:   argument is the agent's mistake, not the world's, and it must not be filed
#:   as a timeout just because the parameter schema quoted by the error mentions
#:   ``timeout``;
#: * ``old_string`` must be tested before the generic ``not found`` rule, or
#:   every stale edit context would be filed as a missing file;
#: * HTTP codes are matched only with their status context (``error: 403``), not
#:   as bare digits that also occur in pids, dates and offsets.
TAXONOMY_RULES = [
    ("tool_contract", re.compile(
        r"(?i)missing required argument|failed argument validation|the tool was not invoked|"
        r"re-send without|no edit was applied because old_string and new_string are identical|"
        r"unsupported image|invalid image|invalid arguments|unknown tool\b|requires a '"), None),
    ("guard_refusal", re.compile(
        r"(?i)refusing to (?:overwrite|write|delete)|blocked by ssrf protection|"
        r"blocked \(hardline\)|refused by policy|safety (?:guard|refusal)"), None),
    ("timeout", re.compile(
        r"(?i)timed out|timeout after|timeout at|deadline exceeded|operation would block|"
        r"\bTimeoutError\b|exit 124: the command hit its timeout"), None),
    ("permission", re.compile(r"(?i)permission denied|access is denied|\bEACCES\b|\bEPERM\b"), None),
    ("rate_limit", re.compile(
        r"(?i)rate limit|too many requests|\b(?:http|status|error|code)[ :=]*429\b"), None),
    ("auth", re.compile(
        r"(?i)authentication failed|unauthorized|\b(?:http|status|error|code)[ :=]*40[13]\b|"
        r"invalid api key|403 forbidden|401 unauthorized"), None),
    ("disk", re.compile(r"(?i)no space left|disk full|\bENOSPC\b"), None),
    ("network", re.compile(
        r"(?i)connection (?:refused|reset|aborted|error|failed)|network (?:is unreachable|error)|"
        r"\bECONN"), None),
    ("environment", re.compile(
        r"(?i)ModuleNotFoundError|No module named|cannot import|wrong interpreter|"
        r"not installed in this (?:interpreter|environment)"), None),
    ("missing_binary", re.compile(
        r"(?i)command not found|is not recognized as an internal or external command|"
        r"is not installed or not on path"), None),
    ("command_usage", re.compile(
        r"(?im)invalid argument/option|unknown option|unrecognized option|invalid option|"
        r"^usage: "), None),
    ("patch_context", re.compile(
        r"(?i)old_string not found|old_string does not match|could not find a match for old_string|"
        r"found \d+ matches? for old_string|escape-drift detected|no match found|"
        r"string to replace not found|patch (?:failed|context)"), "not_found"),
    ("test_failure", re.compile(
        r"(?i)\bFAILURES?\b|AssertionError|\b\d+ failed, \d+ passed\b|\bassert .+? == "), "nonzero_exit"),
    ("not_found", re.compile(
        r"(?i)FileNotFoundError|\bENOENT\b|no such file|ls: cannot access|cannot find the path|"
        r"does not exist"), None),
    ("exception", re.compile(
        r"(?i)Traceback \(most recent call last\)|JSONDecodeError|\b(?:Type|Value|Key|Runtime|"
        r"Operational|Attribute|Import|Index|ZeroDivision)Error\b|SyntaxError"), None),
    ("not_found", re.compile(r"(?i)\bnot found\b|cannot find\b"), None),
]

TAXONOMY_CLASSES = [
    "tool_contract", "guard_refusal", "timeout", "permission", "rate_limit", "auth", "disk",
    "network", "environment", "missing_binary", "command_usage", "patch_context",
    "test_failure", "not_found", "exception", "exit_nonzero", "tool_error", "unknown",
]

#: Tools whose wall-clock is mostly a *wait for a human*, not work.  Kept out of
#: the slow-tool ranking (they are still counted in the timeline) so a single
#: blocked approval cannot own the p95 for a week.
WAIT_TOOLS = {"clarify"}

#: A tool needs at least this many timed calls before it can rank as slow.  A
#: single 3600-second outlier is an anecdote; five is the smallest sample where a
#: p95 says anything at all about the next call.
SLOW_TOOL_MIN_SAMPLES = 5


def classify_tool_failure(error: str, exit_code=None) -> tuple[str, str | None]:
    """Classify one failed call.  Returns ``(class, refined_from)``.

    Deterministic: the same text and exit code always produce the same class.
    Only the leading 600 characters are matched — an error's cause is at the
    top; the parameter schema some tools quote after it is not evidence.
    ``refined_from`` records which v1 receipt class the rollup class refines, so
    the digest can be traced back to the per-session artifact.
    """
    text = (error or "")[:600]
    for name, pattern, refined in TAXONOMY_RULES:
        if pattern.search(text):
            return name, refined
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return "exit_nonzero", "nonzero_exit"
    return ("tool_error", None) if text.strip() else ("unknown", None)


# --------------------------------------------------------------------------- #
# small numeric helpers
# --------------------------------------------------------------------------- #


def _r(value, places: int = 6):
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], p: float) -> float | None:
    """Linear interpolation between closest ranks, ``(n-1)*p``.

    Explicit method matters: p95 computed three different ways gives three
    different numbers, and a digest that changes its definition between weeks is
    not a measurement.  One method, stated on the page.
    """
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[int(pos)]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _pct(delta, previous):
    if previous in (None, 0):
        return None
    return round(delta / previous * 100.0, 2)


# --------------------------------------------------------------------------- #
# window analysis
# --------------------------------------------------------------------------- #


def _window_session_ids(conn, start: float, end: float) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM sessions WHERE started_at >= ? AND started_at < ? "
        "AND COALESCE(hidden, 0) = 0 AND COALESCE(message_count, 0) > 0 "
        "ORDER BY started_at ASC, id ASC",
        (start, end),
    ).fetchall()
    return [r["id"] for r in rows]


def _usage_for(conn, session_ids: list[str]) -> list[dict]:
    """Chunked so a long window cannot blow SQLite's parameter limit."""
    out: list[dict] = []
    for i in range(0, len(session_ids), 900):
        out.extend(_usage_rows(conn, session_ids[i:i + 900]))
    return out


def _summarise_nodes(nodes: list[dict], attribution: dict, cards: dict) -> dict:
    failures = [n for n in nodes if n["status"] == "failed"]
    unverified = [n for n in nodes if n["status"] == "unverified"]
    unterminated = [n for n in nodes if n["status"] == "unterminated"]
    retries = [n for n in nodes if n["retry_of"] is not None]
    chains = [n for n in nodes if n["chain_length"] > 1 and n["retry_of"] is None]
    recovered_chains = [n for n in chains if n.get("chain_recovered") is True]
    unresolved = [n for n in failures if n["retried_by"] is None]

    taxonomy: dict[str, dict] = {}
    for node in failures:
        klass, refined = classify_tool_failure(node.get("error") or "", node.get("exit_code"))
        entry = taxonomy.setdefault(klass, {
            "class": klass,
            "refined_from": refined,
            "count": 0,
            "first_seen_ts": None,
            "last_seen_ts": None,
            "tools": {},
            "retried": 0,
            "recovered": 0,
            "unresolved": 0,
            "example": None,
        })
        entry["count"] += 1
        entry["tools"][node["tool"]] = entry["tools"].get(node["tool"], 0) + 1
        ts = node.get("started_at")
        if ts is not None:
            if entry["first_seen_ts"] is None or ts < entry["first_seen_ts"]:
                entry["first_seen_ts"] = ts
            if entry["last_seen_ts"] is None or ts > entry["last_seen_ts"]:
                entry["last_seen_ts"] = ts
        if node["retried_by"] is not None:
            entry["retried"] += 1
            if node.get("chain_recovered"):
                entry["recovered"] += 1
        else:
            entry["unresolved"] += 1
        if entry["example"] is None:
            entry["example"] = {
                "tool": node["tool"],
                "session": node.get("session_short"),
                "at": _iso(ts),
                "text": redact(_clip(_first_line(node.get("error") or "", 160), 160)),
            }

    taxonomy_rows = []
    for entry in taxonomy.values():
        tools = sorted(entry["tools"].items(), key=lambda kv: (-kv[1], kv[0]))
        taxonomy_rows.append({
            "class": entry["class"],
            "refined_from": entry["refined_from"],
            "count": entry["count"],
            "share_pct": _r(entry["count"] / len(failures) * 100.0, 2) if failures else None,
            "first_seen": _iso(entry["first_seen_ts"]),
            "last_seen": _iso(entry["last_seen_ts"]),
            "tools": [{"tool": t, "count": c} for t, c in tools],
            "retried": entry["retried"],
            "recovered": entry["recovered"],
            "unresolved": entry["unresolved"],
            "example": entry["example"],
        })
    taxonomy_rows.sort(key=lambda e: (-e["count"], e["class"]))

    durations_by_tool: dict[str, dict] = {}
    for node in nodes:
        bucket = durations_by_tool.setdefault(node["tool"], {
            "tool": node["tool"], "calls": 0, "failures": 0, "durations": []})
        bucket["calls"] += 1
        if node["status"] == "failed":
            bucket["failures"] += 1
        if node["duration_s"] is not None:
            bucket["durations"].append(float(node["duration_s"]))

    slow_tools = []
    excluded_tools = []
    for bucket in durations_by_tool.values():
        durs = bucket["durations"]
        row = {
            "tool": bucket["tool"],
            "calls": bucket["calls"],
            "failures": bucket["failures"],
            "timed_calls": len(durs),
            "p50_s": _r(percentile(durs, 0.50), 3),
            "p95_s": _r(percentile(durs, 0.95), 3),
            "max_s": _r(max(durs), 3) if durs else None,
            "total_s": _r(sum(durs), 3),
        }
        if row["tool"] in WAIT_TOOLS:
            excluded_tools.append(dict(row, reason="waits on a human, not work"))
            continue
        if len(durs) < SLOW_TOOL_MIN_SAMPLES:
            excluded_tools.append(dict(row, reason=f"fewer than {SLOW_TOOL_MIN_SAMPLES} timed calls"))
            continue
        slow_tools.append(row)
    slow_tools.sort(key=lambda t: (-(t["p95_s"] or 0.0), -(t["p50_s"] or 0.0), t["tool"]))
    excluded_tools.sort(key=lambda t: (-(t["max_s"] or 0.0), t["tool"]))

    # retries and spend per card
    per_card: dict[str, dict] = {}

    def bucket_for(card_id):
        key = card_id or "unattributed"
        card = cards.get(card_id) if card_id else None
        entry = per_card.setdefault(key, {
            "card": card_id,
            "title": (card or {}).get("title") or ("unattributed sessions" if not card_id else ""),
            "board": (card or {}).get("board") or ("unattributed" if not card_id else "unindexed"),
            "status": (card or {}).get("status") or "",
            "sessions": 0,
            "tool_calls": 0,
            "failures": 0,
            "retries": 0,
            "unresolved": 0,
            "recovered": 0,
        })
        return entry

    for sid in {n.get("session_id") for n in nodes}:
        info = attribution.get(sid) or {}
        bucket_for(info.get("card"))["sessions"] += 1

    for node in nodes:
        info = attribution.get(node.get("session_id")) or {}
        entry = bucket_for(info.get("card"))
        entry["tool_calls"] += 1
        if node["status"] == "failed":
            entry["failures"] += 1
            if node["retried_by"] is None:
                entry["unresolved"] += 1
        if node["retry_of"] is not None:
            entry["retries"] += 1
        if node.get("chain_recovered") is True and node["retry_of"] is None and node["chain_length"] > 1:
            entry["recovered"] += 1

    unattributed_retries = sum(1 for n in retries
                               if not (attribution.get(n.get("session_id")) or {}).get("card"))
    unattributed_failures = sum(1 for n in failures
                                if not (attribution.get(n.get("session_id")) or {}).get("card"))

    return {
        "failures": len(failures),
        "unverified": len(unverified),
        "unterminated": len(unterminated),
        "retries": len(retries),
        "retry_chains": len(chains),
        "chains_recovered": len(recovered_chains),
        "unresolved": len(unresolved),
        "unattributed_retries": unattributed_retries,
        "unattributed_failures": unattributed_failures,
        "taxonomy": taxonomy_rows,
        "slow_tools": slow_tools,
        "slow_tools_excluded": excluded_tools,
        "per_card": per_card,
    }


def _analyse_window(conn, start: float, end: float, cards: dict, board: str | None) -> dict:
    session_ids = _window_session_ids(conn, start, end)
    metas = {sid: _session_meta(conn, sid) for sid in session_ids}
    attribution = attribute_sessions(conn, session_ids, metas, cards)

    if board:
        keep = []
        for sid in session_ids:
            cid = (attribution.get(sid) or {}).get("card")
            if cid and cards.get(cid, {}).get("board") == board:
                keep.append(sid)
        session_ids = keep

    nodes: list[dict] = []
    for sid in session_ids:
        messages = _load_messages(conn, sid)
        for node in _build_nodes(messages):
            node["session_id"] = sid
            node["session_short"] = sid[:12]
            nodes.append(node)

    summary = _summarise_nodes(nodes, attribution, cards)

    usage = _usage_for(conn, session_ids)
    usage_by_session: dict[str, float] = {}
    for row in usage:
        usage_by_session[row["session_id"]] = usage_by_session.get(row["session_id"], 0.0) + (
            row["estimated_cost"] or 0.0)

    by_model: dict[str, dict] = {}
    for row in usage:
        entry = by_model.setdefault(row["model"], {
            "model": row["model"], "providers": set(),
            "api_calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "reasoning": 0, "cost_usd": 0.0, "cost_status": set(),
        })
        if row["provider"]:
            entry["providers"].add(row["provider"])
        for field in ("input", "output", "cache_read", "cache_write", "reasoning"):
            entry[field] += row[field] or 0
        entry["api_calls"] += row["api_calls"] or 0
        entry["cost_usd"] += row["estimated_cost"] or 0.0
        if row.get("cost_status"):
            entry["cost_status"].add(row["cost_status"])
    model_rows = []
    for entry in by_model.values():
        model_rows.append({
            "model": entry["model"],
            "providers": sorted(entry["providers"]),
            "api_calls": entry["api_calls"],
            "input": entry["input"],
            "output": entry["output"],
            "cache_read": entry["cache_read"],
            "cache_write": entry["cache_write"],
            "reasoning": entry["reasoning"],
            "cost_usd": _r(entry["cost_usd"]),
            "cost_status": ", ".join(sorted(entry["cost_status"])) or None,
        })
    model_rows.sort(key=lambda r: (-(r["cost_usd"] or 0.0), r["model"]))

    # add per-session cost to each card bucket
    card_costs: dict[str, float] = {}
    for sid in session_ids:
        cid = (attribution.get(sid) or {}).get("card") or "unattributed"
        card_costs[cid] = card_costs.get(cid, 0.0) + usage_by_session.get(sid, 0.0)
    card_rows = []
    for key, entry in summary["per_card"].items():
        entry = dict(entry)
        entry["cost_usd"] = _r(card_costs.get(key, 0.0))
        card_rows.append(entry)
    card_rows.sort(key=lambda c: (-(c["cost_usd"] or 0.0), -c["tool_calls"], c["card"] or "zz"))

    usage_total = sum((u["estimated_cost"] or 0.0) for u in usage)
    recorded_total = sum((metas[sid]["cost"]["estimated"] or 0.0) for sid in session_ids)
    recorded_tools = sum((metas[sid]["counters"]["tool_call_count"] or 0) for sid in session_ids)
    without_usage = [sid for sid in session_ids if sid not in usage_by_session]
    mismatches = []
    for sid in session_ids:
        if sid not in usage_by_session:
            continue
        recorded = metas[sid]["cost"]["estimated"] or 0.0
        delta = usage_by_session[sid] - recorded
        if abs(delta) > 0.0000005:
            mismatches.append({
                "session": sid[:12],
                "usage_cost": _r(usage_by_session[sid]),
                "recorded_cost": _r(recorded),
                "delta": _r(delta),
            })
    mismatches.sort(key=lambda m: (-abs(m["delta"]), m["session"]))

    unattributed = next((c for c in card_rows if c["card"] is None), None)
    attributed_total = sum((c["cost_usd"] or 0.0) for c in card_rows if c["card"] is not None)

    durations = [n["duration_s"] for n in nodes
                 if n["duration_s"] is not None and n["tool"] not in WAIT_TOOLS]

    return {
        "window": {"start": start, "end": end,
                   "start_iso": _iso(start), "end_iso": _iso(end),
                   "days": round((end - start) / 86400.0, 3)},
        "sessions": len(session_ids),
        "nodes": len(nodes),
        "tool_calls": len(nodes),
        "failures": summary["failures"],
        "unverified": summary["unverified"],
        "unterminated": summary["unterminated"],
        "retries": summary["retries"],
        "retry_chains": summary["retry_chains"],
        "chains_recovered": summary["chains_recovered"],
        "unresolved": summary["unresolved"],
        "unattributed_retries": summary["unattributed_retries"],
        "unattributed_failures": summary["unattributed_failures"],
        "distinct_tools": len({n["tool"] for n in nodes}),
        "distinct_cards": len([c for c in card_rows if c["card"] is not None]),
        "duration": {
            "total_s": _r(sum(durations), 3),
            "p50_s": _r(percentile(durations, 0.50), 3),
            "p95_s": _r(percentile(durations, 0.95), 3),
            "max_s": _r(max(durations), 3) if durations else None,
        },
        "cost": {
            "usage_usd": _r(usage_total),
            "recorded_usd": _r(recorded_total),
            "delta_usd": _r(usage_total - recorded_total),
            "sessions_with_usage": len(session_ids) - len(without_usage),
            "sessions_without_usage": len(without_usage),
            "sessions_without_usage_ids": [s[:12] for s in sorted(without_usage)][:8],
            "attributed_usd": _r(attributed_total),
            "unattributed_usd": _r((unattributed or {}).get("cost_usd") or 0.0),
            "receipt_mismatches": mismatches[:6],
            "receipt_mismatch_count": len(mismatches),
            "statuses": sorted({s for r in usage for s in [r.get("cost_status")] if s}),
        },
        "taxonomy": summary["taxonomy"],
        "by_model": model_rows,
        "by_card": card_rows,
        "slow_tools": summary["slow_tools"],
        "slow_tools_excluded": summary["slow_tools_excluded"],
        "retry_hotspots": _hotspots(card_rows),
        "checks": [
            {
                "name": "cost · per-model rows vs session rows",
                "recorded": _r(recorded_total),
                "derived": _r(usage_total),
                "delta": _r(usage_total - recorded_total),
                "unit": "USD",
                "note": "the same arithmetic a receipt performs, across every session in the window",
            },
            {
                "name": "cost · card attribution covers the window",
                "recorded": _r(usage_total),
                "derived": _r(attributed_total + ((unattributed or {}).get("cost_usd") or 0.0)),
                "delta": _r(attributed_total + ((unattributed or {}).get("cost_usd") or 0.0) - usage_total),
                "unit": "USD",
                "note": "attributed by card + explicitly unattributed must equal the per-model total",
            },
            {
                "name": "tool calls · message log vs session counters",
                "recorded": recorded_tools,
                "derived": len(nodes),
                "delta": len(nodes) - recorded_tools,
                "unit": "calls",
                "note": "rows in the message log vs the counter Hermes wrote on the session",
            },
        ],
    }


def _hotspots(card_rows: list[dict]) -> list[dict]:
    out = [c for c in card_rows if c["retries"] > 0 and c["card"] is not None]
    out.sort(key=lambda c: (-c["retries"], -c["failures"], c["card"] or "zz"))
    return out


# --------------------------------------------------------------------------- #
# week-over-week
# --------------------------------------------------------------------------- #

_DELTA_METRICS = [
    ("sessions", lambda w: w["sessions"], "neutral", "sessions with messages", "count"),
    ("tool calls", lambda w: w["tool_calls"], "neutral", "dispatched tool calls", "count"),
    ("failures", lambda w: w["failures"], "lower", "calls that returned an error", "count"),
    ("failure rate", lambda w: _r(w["failures"] / w["tool_calls"] * 100.0, 2) if w["tool_calls"] else 0.0,
     "lower", "failures / tool calls, %", "percent"),
    ("retries", lambda w: w["retries"], "lower", "calls that repeated a failed intent", "count"),
    ("unresolved failures", lambda w: w["unresolved"], "lower", "failures never retried", "count"),
    ("chains recovered", lambda w: w["chains_recovered"], "higher", "failed chains that ended in a success", "count"),
    ("cost", lambda w: w["cost"]["usage_usd"], "lower", "USD from per-model rows", "money"),
    ("tokens in", lambda w: sum(m["input"] for m in w["by_model"]), "neutral", "input tokens", "count"),
    ("tokens out", lambda w: sum(m["output"] for m in w["by_model"]), "neutral", "output tokens", "count"),
    ("median tool duration", lambda w: w["duration"]["p50_s"], "lower", "p50 seconds", "duration"),
    ("p95 tool duration", lambda w: w["duration"]["p95_s"], "lower", "p95 seconds", "duration"),
]


def _deltas(current: dict, previous: dict) -> list[dict]:
    rows = []
    for name, get, better_when, unit, kind in _DELTA_METRICS:
        cur = get(current)
        prev = get(previous)
        if cur is None or prev is None:
            delta = None
            pct = None
        else:
            delta = cur - prev
            if isinstance(delta, float):
                delta = round(delta, 6 if kind == "money" else 3)
            pct = _pct(delta, prev)
        rows.append({
            "metric": name,
            "current": cur,
            "previous": prev,
            "delta": delta,
            "delta_pct": pct,
            "better_when": better_when,
            "unit": unit,
            "kind": kind,
        })
    return rows


# --------------------------------------------------------------------------- #
# headline / gazette line
# --------------------------------------------------------------------------- #


def _headline(payload: dict) -> str:
    current = payload["current"]
    rate = _r(current["failures"] / current["tool_calls"] * 100.0, 1) if current["tool_calls"] else 0.0
    return (f"{current['sessions']} sessions · {current['tool_calls']:,} tool calls · "
            f"{current['failures']:,} failures ({rate}%) · ${(current['cost']['usage_usd'] or 0):.4f}")


def _desk_line(payload: dict) -> str:
    current = payload["current"]
    top = current["taxonomy"][0] if current["taxonomy"] else None
    slow = current["slow_tools"][0] if current["slow_tools"] else None
    recovered_pct = (_r(current["chains_recovered"] / current["retry_chains"] * 100.0, 0)
                     if current["retry_chains"] else None)
    cost_delta = next((d for d in payload["deltas"] if d["metric"] == "cost"), None)
    parts = [payload["window"]["week"]]
    parts.append(f"top failure {top['class']} ×{top['count']}" if top else "no failures recorded")
    parts.append(f"retries {current['retries']}"
                 + (f" ({recovered_pct:.0f}% of chains recovered)" if recovered_pct is not None else ""))
    if slow and slow["p95_s"] is not None:
        parts.append(f"slowest {slow['tool']} p95 {slow['p95_s']:.1f}s")
    parts.append(f"cost ${(current['cost']['usage_usd'] or 0):.4f}")
    if cost_delta and cost_delta["delta_pct"] is not None:
        parts.append(f"cost {cost_delta['delta_pct']:+.0f}% vs prior window")
    return " · ".join(parts)


def _week_label(ts: float) -> str:
    """ISO week of the *middle* of the window, not its end.

    A Monday-06:30 run covers the week that just finished; labelling it by the
    end instant would name the new week and make two editions look like one.
    The bounds in `window.start/end` remain authoritative.
    """
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# --------------------------------------------------------------------------- #
# canonical JSON + digest
# --------------------------------------------------------------------------- #


def canonical_digest(payload: dict) -> str:
    """SHA-256 over the canonical payload, excluding provenance and the digest itself.

    ``source`` (store path, size, mtime) is deliberately out: the digest is a
    fingerprint of the *measurements*, so copying the same store or rebuilding an
    identical fixture does not change it.  ``stability`` is self-referential.
    """
    body = {k: v for k, v in payload.items() if k not in ("stability", "source")}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def render_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #


def build_rollup(since: str = "7d", board: str | None = None, as_of: str | None = None,
                 db_path: Path | None = None, kanban_home_path: Path | None = None) -> dict:
    """Read the store and return the complete rollup payload."""
    db_path = Path(db_path) if db_path is not None else state_db_path()
    if not db_path.exists():
        raise ReceiptsError(f"No Hermes session store at {db_path}. Pass --db or set RECEIPTS_DB.")

    window = parse_window(since, as_of)
    boards = load_boards(kanban_home_path)
    cards = _card_index(boards)
    midpoint = window["start"] + window["seconds"] / 2.0
    previous_midpoint = window["previous"]["start"] + window["seconds"] / 2.0

    if board:
        slug = board.strip()
        if slug not in boards:
            known = ", ".join(sorted(boards)) or "none found"
            raise ReceiptsError(f"No kanban board named {slug!r}. Known boards: {known}.")

    with open_ro(db_path) as conn:
        current = _analyse_window(conn, window["start"], window["end"], cards, board)
        previous = _analyse_window(conn, window["previous"]["start"], window["previous"]["end"],
                                   cards, board)

    payload = {
        "schema_version": ROLLUP_SCHEMA_VERSION,
        "kind": "rollup",
        "tool_version": __version__,
        "board": board or None,
        "window": {
            "since": window["since"],
            "start": window["start_iso"],
            "end": window["end_iso"],
            "start_ts": window["start"],
            "end_ts": window["end"],
            "days": window["days"],
            "label": window["label"],
            "week": _week_label(midpoint),
            "previous_start": window["previous"]["start_iso"],
            "previous_end": window["previous"]["end_iso"],
            "previous_label": window["previous"]["label"],
            "previous_week": _week_label(previous_midpoint),
        },
        "source": dict(db_identity(db_path), kanban_home=str(kanban_home_path or kanban_home())),
        "boards": [{"slug": b["slug"], "name": b["name"], "cards": len(b["tasks"]),
                    "error": b["error"]} for b in (boards[k] for k in sorted(boards))],
        "scope": {
            "sessions": current["sessions"],
            "tool_calls": current["tool_calls"],
            "distinct_tools": current["distinct_tools"],
            "distinct_cards": current["distinct_cards"],
            "previous_sessions": previous["sessions"],
            "previous_tool_calls": previous["tool_calls"],
        },
        "current": {k: v for k, v in current.items() if k != "nodes"},
        "previous": {k: v for k, v in previous.items() if k != "nodes"},
        "deltas": _deltas(current, previous),
    }
    payload["headline"] = _headline(payload)
    payload["desk_line"] = _desk_line(payload)
    payload["stability"] = {
        "digest": canonical_digest(payload),
        "canonical": "sha256(json/sort_keys/utf-8)",
        "timestamps": "UTC, second precision; no render clock in the payload",
        "percentile_method": "linear interpolation at (n-1)*p over observed durations",
    }
    return payload
