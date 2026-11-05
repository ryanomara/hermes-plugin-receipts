"""Normalize a Hermes session record into a render-ready manifest.

The manifest is deliberately a plain dict: it is the contract between the reader,
the renderer and the tests, and it is what gets embedded in the artifact.  One
rule governs every judgement call here - **never launder a failure**.  A tool call
that errored stays an errored node even when a later retry succeeded; an
unparsable result is reported as *unverified*, never as success.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .dbio import ReceiptsError, db_identity, open_ro, state_db_path

__all__ = ["build_manifest", "resolve_sessions", "SCHEMA_VERSION"]

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- #
# redaction
# --------------------------------------------------------------------------- #

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "«redacted:private-key»"),
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})"), "«redacted:api-key»"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})"), "«redacted:github-token»"),
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), "«redacted:aws-key»"),
    (re.compile(r"\b(xox[abprs]-[A-Za-z0-9\-]{10,})"), "«redacted:slack-token»"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|bearer)"
                r"(\s*[:=]\s*|\s+)(['\"]?)([A-Za-z0-9_\-\.\+\/]{12,})\3"),
     r"\1\2\3«redacted»\3"),
]


def redact(text: str) -> str:
    """Mask credential-shaped strings. Applied to every string that reaches HTML."""
    if not text:
        return ""
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _json_maybe(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return None


def _clip(text: str, limit: int) -> str:
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_line(text: str, limit: int = 220) -> str:
    if not text:
        return ""
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            return _clip(line, limit)
    return ""


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return None


# --------------------------------------------------------------------------- #
# tool-call summarization
# --------------------------------------------------------------------------- #

_ARG_KEYS = {
    "terminal": ("command",),
    "read_file": ("path",),
    "write_file": ("path",),
    "patch": ("path",),
    "web_search": ("query",),
    "web_extract": ("urls",),
    "skill_view": ("name",),
    "skill_manage": ("operations",),
    "browser_exec": ("code",),
    "kanban_show": ("task_id",),
    "kanban_complete": ("summary",),
    "kanban_create": ("title",),
    "kanban_comment": ("body",),
    "delegate_task": ("tasks",),
    "execute_code": ("code",),
    "image_generate": ("prompt",),
    "video_generate": ("prompt",),
    "memory": ("target",),
}


def summarize_args(tool_name: str, args) -> str:
    """A one-line, human-legible gist of what the call asked for."""
    if args is None:
        return ""
    if isinstance(args, str):
        return redact(_clip(_first_line(args), 200))
    if not isinstance(args, dict):
        return redact(_clip(str(args), 200))

    order = _ARG_KEYS.get(tool_name, ())
    for key in order:
        if key in args and args[key] not in (None, "", [], {}):
            value = args[key]
            if isinstance(value, (list, dict)):
                try:
                    value = json.dumps(value, ensure_ascii=False)
                except Exception:
                    value = str(value)
            return redact(_clip(_first_line(str(value)), 200))

    for key in ("command", "path", "query", "url", "name", "prompt", "task_id", "title"):
        if key in args and args[key] not in (None, "", [], {}):
            return redact(_clip(_first_line(str(args[key])), 200))

    scalars = [f"{k}={v}" for k, v in args.items() if isinstance(v, (str, int, float, bool))]
    if scalars:
        return redact(_clip(" · ".join(scalars), 200))
    return f"{len(args)} argument(s)"


def _result_blob(parsed, raw: str) -> dict:
    """Flatten the many shapes a tool result takes into one comparable view."""
    if isinstance(parsed, dict):
        output = parsed.get("output")
        if output is None and isinstance(parsed.get("content"), str):
            output = parsed["content"]
        return {
            "error": parsed.get("error") or parsed.get("stderr"),
            "exit_code": parsed.get("exit_code"),
            "ok": parsed.get("ok"),
            "success": parsed.get("success"),
            "output": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            if isinstance(output, (dict, list)) else None,
            "hint": parsed.get("hint"),
            "status": parsed.get("status"),
        }
    return {"error": None, "exit_code": None, "ok": None, "success": None,
            "output": raw if isinstance(raw, str) else None, "hint": None, "status": None}


_FAILURE_CLASSES = [
    (re.compile(r"(?i)\btimeout\b|timed out|deadline exceeded"), "timeout"),
    (re.compile(r"(?i)permission denied|access is denied|EACCES|EPERM"), "permission"),
    (re.compile(r"(?i)not found|no such file|ENOENT|cannot find"), "not_found"),
    (re.compile(r"(?i)rate limit|429|too many requests"), "rate_limit"),
    (re.compile(r"(?i)authentication|unauthorized|401|403 forbidden|invalid api key"), "auth"),
    (re.compile(r"(?i)no space left|disk full"), "disk"),
    (re.compile(r"(?i)JSONDecodeError|Traceback \(most recent call last\)"), "exception"),
    (re.compile(r"(?i)connection (?:refused|reset)|network is unreachable|ECONN"), "network"),
    (re.compile(r"(?i)command not found|is not recognized as"), "missing_binary"),
]


def classify_failure(parsed, raw: str, blob: dict) -> tuple[str | None, str]:
    """Return ``(status, error_text)``.

    ``failed``      - the record says it failed
    ``unverified``  - the record has no structured status; we refuse to call it OK
    ``ok``          - the record says it succeeded
    """
    if parsed is None:
        text = (raw or "").strip()
        if not text:
            return "unverified", ""
        head = text[:400]
        if re.match(r"(?i)^(error|exception|traceback|fatal)\b", head) or "Traceback (most recent call last)" in head:
            return "failed", _first_line(text, 400)
        return "unverified", ""

    if not isinstance(parsed, dict):
        return "ok", ""

    err = blob.get("error")
    exit_code = blob.get("exit_code")

    if err not in (None, "", False):
        return "failed", _clip(str(err), 800)
    if blob.get("ok") is False:
        return "failed", _failure_hint(blob) or "the tool reported ok=false"
    if blob.get("success") is False:
        return "failed", _failure_hint(blob) or "the tool reported success=false"
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return "failed", _failure_hint(blob) or f"exit code {exit_code}"
    return "ok", ""


def _failure_hint(blob: dict) -> str:
    """The most explanatory slice of a failed result.

    One line is not enough: a ``git push`` refusal reads "permission denied" on
    the first line and "403" on the second, and dropping the second turns a
    specific failure into a vague one.
    """
    hint = blob.get("hint")
    if isinstance(hint, str) and hint.strip():
        return _clip(hint.strip(), 800)
    output = blob.get("output")
    if not isinstance(output, str) or not output.strip():
        return ""

    lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]
    markers = ("error", "denied", "not found", "traceback", "fatal", "cannot",
               "failed", "refused", "exception", "invalid", "unable")
    hits = [ln for ln in lines if any(tok in ln.lower() for tok in markers)]

    if not hits:
        return _clip("\n".join(lines[-3:]), 700)

    # Keep the first few marked lines; they carry the cause and the code.
    chosen = hits[:4]
    return _clip("\n".join(chosen), 800)


def failure_class(text: str, blob: dict) -> str:
    if isinstance(blob.get("exit_code"), int) and blob["exit_code"] != 0:
        for pattern, name in _FAILURE_CLASSES:
            if pattern.search(text or ""):
                return name
        return "nonzero_exit"
    for pattern, name in _FAILURE_CLASSES:
        if pattern.search(text or ""):
            return name
    return "tool_error" if text else "unknown"


def summarize_result(parsed, raw: str, blob: dict, status: str) -> str:
    if status == "failed":
        return _clip(_first_line(str(blob.get("error") or ""), 200), 200) or _clip(blob.get("output") or "", 200)
    if isinstance(parsed, dict):
        if isinstance(parsed.get("total_lines"), int):
            return f"{parsed['total_lines']} lines"
        if isinstance(parsed.get("bytes_written"), int):
            return f"{parsed['bytes_written']} bytes written"
        if isinstance(parsed.get("matches"), list):
            return f"{len(parsed['matches'])} matches"
        if parsed.get("status"):
            return _clip(str(parsed["status"]), 120)
    output = blob.get("output")
    if isinstance(output, str) and output.strip():
        return _clip(_first_line(output, 200), 200)
    if raw:
        return _clip(_first_line(raw, 200), 200)
    return ""


# --------------------------------------------------------------------------- #
# session resolution
# --------------------------------------------------------------------------- #

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\.\.(\d{4}-\d{2}-\d{2}))?$")


def _parse_day(value: str, end_of_day: bool) -> float:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).astimezone()
    except ValueError as exc:
        raise ReceiptsError(f"Bad date {value!r}; expected YYYY-MM-DD.") from exc
    ts = dt.timestamp()
    return ts + 86399.999 if end_of_day else ts


def resolve_sessions(conn, target: str | None) -> tuple[list[str], str]:
    """Resolve a target string to session ids. Returns ``(ids, kind)``.

    Accepted targets:
      ``latest``                          newest non-empty session
      ``2026-11-01`` / ``2026-11-01..2026-11-05``   date range
      ``t_abc123`` / ``card:t_abc123``    kanban card evidence chain
      ``<id>`` / ``<id-prefix>``          one session
    """
    target = (target or "latest").strip()

    if target.startswith("session:"):
        target = target.split(":", 1)[1].strip()
    elif target.startswith("range:"):
        target = target.split(":", 1)[1].strip()
    elif target.startswith("card:"):
        target = "card:" + target.split(":", 1)[1].strip()

    if target in ("", "latest", "last"):
        row = conn.execute(
            "SELECT id FROM sessions WHERE COALESCE(hidden,0)=0 AND COALESCE(message_count,0) > 0 "
            "ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise ReceiptsError("No sessions in this store yet. Run Hermes once, then try again.")
        return [row["id"]], "session"

    if _DATE_RE.match(target):
        m = _DATE_RE.match(target)
        start = _parse_day(m.group(1), end_of_day=False)
        end = _parse_day(m.group(2) or m.group(1), end_of_day=True)
        if end < start:
            start, end = end, start
        rows = conn.execute(
            "SELECT id FROM sessions WHERE started_at BETWEEN ? AND ? "
            "AND COALESCE(hidden,0)=0 ORDER BY started_at ASC",
            (start, end),
        ).fetchall()
        if not rows:
            raise ReceiptsError(f"No sessions started between {m.group(1)} and {m.group(2) or m.group(1)}.")
        return [r["id"] for r in rows], "range"

    if target.startswith("card:"):
        card_id = target.split(":", 1)[1]
        rows = conn.execute(
            "SELECT DISTINCT session_id FROM messages WHERE content LIKE ? "
            "ORDER BY session_id LIMIT 40",
            (f"%{card_id}%",),
        ).fetchall()
        if not rows:
            raise ReceiptsError(f"No session mentions card {card_id!r}.")
        return [r["session_id"] for r in rows], "card"

    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (target,)).fetchone()
    if row:
        return [row["id"]], "session"
    row = conn.execute(
        "SELECT id FROM sessions WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1", (target + "%",)
    ).fetchone()
    if row:
        return [row["id"]], "session"
    raise ReceiptsError(
        f"No session matches {target!r}. Try `latest`, a session id, a date (2026-11-01), "
        "or a kanban card id (t_xxxxxxxx)."
    )


# --------------------------------------------------------------------------- #
# session record
# --------------------------------------------------------------------------- #


def _session_meta(conn, session_id: str) -> dict:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise ReceiptsError(f"Session {session_id!r} disappeared from the store.")
    keys = set(row.keys())

    def g(name, default=None):
        return row[name] if name in keys else default

    return {
        "id": session_id,
        "short_id": session_id[:12],
        "title": g("title") or "(untitled session)",
        "source": g("source") or "unknown",
        "profile": g("profile_name"),
        "model": g("model"),
        "started_at": g("started_at"),
        "ended_at": g("ended_at"),
        "end_reason": g("end_reason"),
        "last_activity_at": g("last_activity_at"),
        "cwd": g("cwd"),
        "git_branch": g("git_branch"),
        "parent_session_id": g("parent_session_id"),
        "counters": {
            "message_count": g("message_count"),
            "tool_call_count": g("tool_call_count"),
            "api_call_count": g("api_call_count"),
        },
        "cost": {
            "estimated": g("estimated_cost_usd"),
            "actual": g("actual_cost_usd"),
            "status": g("cost_status"),
            "source": g("cost_source"),
            "pricing_version": g("pricing_version"),
            "billing_provider": g("billing_provider"),
        },
        "tokens": {
            "input": g("input_tokens"),
            "output": g("output_tokens"),
            "cache_read": g("cache_read_tokens"),
            "cache_write": g("cache_write_tokens"),
            "reasoning": g("reasoning_tokens"),
        },
    }


def _usage_rows(conn, session_ids: list[str]) -> list[dict]:
    if not session_ids:
        return []
    placeholders = ",".join("?" * len(session_ids))
    rows = conn.execute(
        f"SELECT * FROM session_model_usage WHERE session_id IN ({placeholders})", session_ids
    ).fetchall()
    out = []
    for row in rows:
        keys = set(row.keys())
        out.append({
            "session_id": row["session_id"],
            "model": row["model"],
            "provider": row["billing_provider"] or "",
            "task": row["task"] or "",
            "api_calls": row["api_call_count"] or 0,
            "input": row["input_tokens"] or 0,
            "output": row["output_tokens"] or 0,
            "cache_read": row["cache_read_tokens"] or 0,
            "cache_write": row["cache_write_tokens"] or 0,
            "reasoning": row["reasoning_tokens"] or 0,
            "estimated_cost": row["estimated_cost_usd"],
            "actual_cost": row["actual_cost_usd"],
            "cost_status": row["cost_status"] if "cost_status" in keys else None,
            "cost_source": row["cost_source"] if "cost_source" in keys else None,
            "first_seen": row["first_seen"] if "first_seen" in keys else None,
            "last_seen": row["last_seen"] if "last_seen" in keys else None,
        })
    out.sort(key=lambda r: (r["first_seen"] or 0, r["model"]))
    return out


def _load_messages(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, role, content, tool_call_id, tool_calls, tool_name, timestamp, "
        "finish_reason, active, compacted, display_kind "
        "FROM messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    out = []
    for row in rows:
        keys = set(row.keys())
        out.append({
            "row_id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "tool_call_id": row["tool_call_id"],
            "tool_calls": row["tool_calls"],
            "tool_name": row["tool_name"],
            "timestamp": row["timestamp"],
            "finish_reason": row["finish_reason"] if "finish_reason" in keys else None,
            "active": row["active"] if "active" in keys else 1,
            "compacted": row["compacted"] if "compacted" in keys else 0,
            "display_kind": row["display_kind"] if "display_kind" in keys else None,
        })
    return out


# --------------------------------------------------------------------------- #
# the arc: asks, tool nodes, answer
# --------------------------------------------------------------------------- #


def _extract_asks(messages: list[dict]) -> list[dict]:
    asks = []
    for msg in messages:
        if msg["role"] != "user":
            continue
        raw = msg["content"] or ""
        parsed = _json_maybe(raw)
        text = raw
        if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
            text = parsed["text"]
        text = redact(text or "")
        asks.append({
            "row_id": msg["row_id"],
            "timestamp": msg["timestamp"],
            "text": _clip(text, 6000),
            "chars": len(text),
        })
    return asks


def _build_nodes(messages: list[dict]) -> list[dict]:
    """Pair every dispatched tool call with its result and time it."""
    calls: dict[str, dict] = {}
    order: list[dict] = []

    for idx, msg in enumerate(messages):
        if msg["role"] == "assistant" and msg["tool_calls"]:
            parsed_calls = _json_maybe(msg["tool_calls"])
            if isinstance(parsed_calls, list):
                for call in parsed_calls:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") or {}
                    name = fn.get("name") or call.get("name") or "unknown_tool"
                    raw_args = fn.get("arguments", call.get("arguments"))
                    args = _json_maybe(raw_args)
                    call_id = call.get("id") or call.get("call_id") or f"row{msg['row_id']}"
                    node = {
                        "call_id": call_id,
                        "tool": name,
                        "args": args,
                        "args_raw": raw_args if isinstance(raw_args, str) else None,
                        "args_summary": summarize_args(name, args if args is not None else raw_args),
                        "started_at": msg["timestamp"],
                        "dispatch_row": msg["row_id"],
                        "parallel_group": len(parsed_calls) if len(parsed_calls) > 1 else None,
                        "result": None,
                    }
                    calls[call_id] = node
                    order.append(node)

        elif msg["role"] == "tool":
            call_id = msg["tool_call_id"]
            node = calls.get(call_id)
            if node is None:
                # Orphan result (compaction can drop the dispatch row). Keep it visible.
                node = {
                    "call_id": call_id or f"orphan{msg['row_id']}",
                    "tool": msg["tool_name"] or "unknown_tool",
                    "args": None,
                    "args_raw": None,
                    "args_summary": "",
                    "started_at": None,
                    "dispatch_row": None,
                    "parallel_group": None,
                    "result": None,
                    "orphan": True,
                }
                order.append(node)
            node["result"] = msg

    nodes = []
    for i, node in enumerate(order):
        raw = (node["result"] or {}).get("content") or ""
        parsed = _json_maybe(raw)
        blob = _result_blob(parsed, raw)
        status, error = classify_failure(parsed, raw, blob)
        if node["result"] is None:
            status, error = "unterminated", "the session ended before this call returned a result"

        started = node.get("started_at")
        finished = (node["result"] or {}).get("timestamp")
        duration = None
        if started and finished:
            duration = max(0.0, float(finished) - float(started))
        elif started and not finished:
            duration = None

        args_sig = ""
        if node.get("args") is not None:
            try:
                args_sig = hashlib.sha1(
                    json.dumps(node["args"], sort_keys=True, ensure_ascii=False, default=str).encode()
                ).hexdigest()[:12]
            except Exception:
                args_sig = ""

        nodes.append({
            "index": i,
            "call_id": node["call_id"],
            "tool": node["tool"],
            "args_summary": node["args_summary"],
            "args_detail": _clip(redact(node["args_raw"] or ""), 1200),
            "args_signature": args_sig,
            "parallel_group": node.get("parallel_group"),
            "dispatch_row": node.get("dispatch_row"),
            "result_row": (node.get("result") or {}).get("row_id"),
            "started_at": started,
            "finished_at": finished,
            "duration_s": duration,
            "status": status,
            "failure_class": failure_class(error, blob) if status == "failed" else None,
            "error": redact(_clip(error, 1500)) if error else "",
            "result_summary": redact(summarize_result(parsed, raw, blob, status)),
            "result_chars": len(raw or ""),
            "result_excerpt": redact(_clip(blob.get("output") or raw or "", 900)),
            "exit_code": blob.get("exit_code") if isinstance(blob.get("exit_code"), int) else None,
            "orphan": bool(node.get("orphan")),
            "retry_of": None,
            "retried_by": None,
            "attempt": 1,
            "chain_length": 1,
            "repeat_of": None,
        })

    _link_chains(nodes)
    return nodes


def _token_set(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9_./-]+", (text or "").lower()) if t}


def _looks_like_retry(previous: dict, node: dict, max_distance: int = 8) -> bool:
    """Was *node* the same intent as *previous*?

    Same tool is not enough: a ``git push`` that failed is not retried by the
    ``git status`` that follows it. Require identical arguments, or an argument
    gist that contains the earlier one (agents narrow or extend a command when
    they retry). Anything looser would invent a retry that never happened.
    """
    if previous["tool"] != node["tool"]:
        return False
    if node["index"] - previous["index"] > max_distance:
        return False
    if node["args_signature"] and node["args_signature"] == previous["args_signature"]:
        return True
    a = _token_set(previous["args_summary"])
    b = _token_set(node["args_summary"])
    if not a or not b:
        return True
    return len(a & b) / min(len(a), len(b)) >= 0.6


def _link_chains(nodes: list[dict]) -> None:
    """Wire attempts into visible chains: a retry is the same intent after a failure."""
    pending: dict[str, dict] = {}
    seen_args: dict[tuple, dict] = {}

    for node in nodes:
        key = (node["tool"], node["args_signature"])
        prev_same_args = seen_args.get(key)
        if prev_same_args is not None and node["args_signature"]:
            node["repeat_of"] = prev_same_args["index"]
        seen_args[key] = node

        prior = pending.get(node["tool"])
        if prior is not None and _looks_like_retry(prior, node):
            node["retry_of"] = prior["index"]
            prior["retried_by"] = node["index"]
            node["attempt"] = prior["attempt"] + 1

        if node["status"] == "failed":
            pending[node["tool"]] = node
        elif node["status"] == "ok" and node["retry_of"] is not None:
            # Only a *successful retry* closes a chain. An unrelated successful
            # call to the same tool says nothing about the failure before it.
            pending.pop(node["tool"], None)

    # chain_length is the highest attempt reached in each chain
    for node in nodes:
        if node["retry_of"] is not None:
            continue
        chain = [node]
        cursor = node
        while cursor["retried_by"] is not None:
            cursor = nodes[cursor["retried_by"]]
            chain.append(cursor)
        if len(chain) > 1:
            for member in chain:
                member["chain_length"] = len(chain)
                member["chain_root"] = node["index"]
                member["chain_recovered"] = chain[-1]["status"] == "ok"

    for node in nodes:
        node.setdefault("chain_root", None)
        node.setdefault("chain_recovered", None)


def _build_timeline(messages: list[dict], asks: list[dict], nodes: list[dict], answers: list[dict]) -> list[dict]:
    """One ordered event stream for the renderer: asks, tool nodes, answers."""
    events: list[tuple] = []
    ask_by_row = {a["row_id"]: a for a in asks}
    answer_by_row = {a["row_id"]: a for a in answers}
    node_by_dispatch = {}
    for node in nodes:
        # attach node to the dispatch row so it lands after that assistant message
        node_by_dispatch.setdefault(node.get("dispatch_row"), []).append(node)

    for msg in messages:
        rid = msg["row_id"]
        if rid in ask_by_row:
            events.append((rid, "ask", ask_by_row[rid]))
        if rid in answer_by_row:
            events.append((rid, "answer", answer_by_row[rid]))
        for node in node_by_dispatch.get(rid, []):
            events.append((rid, "tool", node))

    # orphans have no dispatch row: place them by their result row
    for node in nodes:
        if node["orphan"] or node.get("dispatch_row") is None:
            key = node.get("result_row") or (10 ** 9 + node["index"])
            events.append((key, "tool", node))

    events.sort(key=lambda e: (e[0] if isinstance(e[0], int) else 0))
    return [{"kind": kind, "payload": payload} for _, kind, payload in events]


def _extract_answers(messages: list[dict]) -> list[dict]:
    """Final assistant text, ignoring pure tool-call turns."""
    answers = []
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        if msg["tool_calls"]:
            continue
        text = (msg["content"] or "").strip()
        if not text:
            continue
        answers.append({
            "row_id": msg["row_id"],
            "timestamp": msg["timestamp"],
            "text": _clip(redact(text), 6000),
        })
    return answers


# --------------------------------------------------------------------------- #
# aggregates
# --------------------------------------------------------------------------- #


def _aggregate(sessions_meta, nodes, usage) -> dict:
    durations = [n["duration_s"] for n in nodes if n["duration_s"] is not None]
    failures = [n for n in nodes if n["status"] == "failed"]
    unverified = [n for n in nodes if n["status"] == "unverified"]
    unterminated = [n for n in nodes if n["status"] == "unterminated"]
    retries = [n for n in nodes if n["retry_of"] is not None]
    chains = [n for n in nodes if n["chain_length"] > 1 and n["retry_of"] is None]
    recovered = [n for n in nodes if n.get("chain_recovered")]
    unrecovered = [n for n in nodes if n["status"] == "failed" and n["retried_by"] is None]

    by_tool: dict[str, dict] = {}
    for node in nodes:
        bucket = by_tool.setdefault(node["tool"], {"tool": node["tool"], "calls": 0,
                                                  "failures": 0, "seconds": 0.0})
        bucket["calls"] += 1
        if node["status"] == "failed":
            bucket["failures"] += 1
        if node["duration_s"] is not None:
            bucket["seconds"] += node["duration_s"]

    flow = []
    for node in nodes:
        if flow and flow[-1]["tool"] == node["tool"]:
            flow[-1]["count"] += 1
            flow[-1]["failures"] += 1 if node["status"] == "failed" else 0
            flow[-1]["seconds"] += node["duration_s"] or 0.0
        else:
            flow.append({"tool": node["tool"], "count": 1,
                         "failures": 1 if node["status"] == "failed" else 0,
                         "seconds": node["duration_s"] or 0.0})

    usage_totals = {
        "api_calls": sum(u["api_calls"] for u in usage),
        "input": sum(u["input"] for u in usage),
        "output": sum(u["output"] for u in usage),
        "cache_read": sum(u["cache_read"] for u in usage),
        "cache_write": sum(u["cache_write"] for u in usage),
        "reasoning": sum(u["reasoning"] for u in usage),
        "estimated_cost": sum((u["estimated_cost"] or 0.0) for u in usage),
        "actual_cost": sum((u["actual_cost"] or 0.0) for u in usage),
    }

    wall_clock = 0.0
    if sessions_meta:
        starts = [s["started_at"] for s in sessions_meta if s["started_at"]]
        ends = [(s["ended_at"] or s["last_activity_at"] or s["started_at"]) for s in sessions_meta]
        if starts and ends:
            wall_clock = max(0.0, max(ends) - min(starts))

    return {
        "tool_calls": len(nodes),
        "failures": len(failures),
        "unverified": len(unverified),
        "unterminated": len(unterminated),
        "retries": len(retries),
        "retry_chains": len(chains),
        "chains_recovered": len(recovered),
        "failures_unrecovered": len(unrecovered),
        "distinct_tools": len(by_tool),
        "duration_total_s": sum(durations),
        "duration_median_s": (sorted(durations)[len(durations) // 2] if durations else None),
        "duration_max_s": (max(durations) if durations else None),
        "wall_clock_s": wall_clock,
        "by_tool": sorted(by_tool.values(), key=lambda b: (-b["calls"], b["tool"])),
        "flow": flow,
        "usage": usage_totals,
    }


def _reconcile(sessions_meta, nodes, usage) -> dict:
    """Compare what the artifact derived against what the session row recorded."""
    recorded_cost = sum((s["cost"]["estimated"] or 0.0) for s in sessions_meta)
    usage_cost = sum((u["estimated_cost"] or 0.0) for u in usage)
    recorded_tools = sum((s["counters"]["tool_call_count"] or 0) for s in sessions_meta)
    recorded_msgs = sum((s["counters"]["message_count"] or 0) for s in sessions_meta)

    checks = [
        {
            "name": "cost",
            "recorded": round(recorded_cost, 6),
            "derived": round(usage_cost, 6),
            "delta": round(usage_cost - recorded_cost, 6),
            "unit": "USD",
            "note": "sum of per-model rows vs the session total",
        },
        {
            "name": "tool calls",
            "recorded": recorded_tools,
            "derived": len(nodes),
            "delta": len(nodes) - recorded_tools,
            "unit": "calls",
            "note": "rows in the message log vs the session counter",
        },
    ]
    available = all(u["estimated_cost"] is not None for u in usage) if usage else False
    return {
        "checks": checks,
        "recorded_cost": recorded_cost,
        "usage_cost": usage_cost,
        "cost_available": available or recorded_cost > 0,
        "recorded_messages": recorded_msgs,
        "cost_status": next((s["cost"]["status"] for s in sessions_meta if s["cost"]["status"]), None),
        "cost_source": next((s["cost"]["source"] for s in sessions_meta if s["cost"]["source"]), None),
        "pricing_version": next((s["cost"]["pricing_version"] for s in sessions_meta
                                 if s["cost"]["pricing_version"]), None),
    }


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #


def build_manifest(target: str | None = None, db_path: Path | None = None) -> dict:
    """Read the store and return the full render-ready manifest."""
    db_path = Path(db_path) if db_path is not None else state_db_path()
    with open_ro(db_path) as conn:
        session_ids, kind = resolve_sessions(conn, target)
        sessions_meta = [_session_meta(conn, sid) for sid in session_ids]

        all_nodes: list[dict] = []
        all_asks: list[dict] = []
        all_answers: list[dict] = []
        per_session = []

        for meta in sessions_meta:
            messages = _load_messages(conn, meta["id"])
            asks = _extract_asks(messages)
            nodes = _build_nodes(messages)
            answers = _extract_answers(messages)
            for node in nodes:
                node["session"] = meta["short_id"]
            all_nodes.extend(nodes)
            all_asks.extend(asks)
            all_answers.extend(answers)
            per_session.append({
                "id": meta["id"],
                "short_id": meta["short_id"],
                "title": meta["title"],
                "source": meta["source"],
                "started_at": meta["started_at"],
                "ended_at": meta["ended_at"] or meta["last_activity_at"],
                "message_count": len(messages),
                "tool_calls": len(nodes),
                "failures": sum(1 for n in nodes if n["status"] == "failed"),
                "model": meta["model"],
                "cost": meta["cost"]["estimated"],
            })
            meta["timeline"] = _build_timeline(messages, asks, nodes, answers)
            meta["message_count"] = len(messages)

        usage = _usage_rows(conn, session_ids)

    aggregates = _aggregate(sessions_meta, all_nodes, usage)
    reconciliation = _reconcile(sessions_meta, all_nodes, usage)

    primary = sessions_meta[0] if sessions_meta else {}
    multi = len(sessions_meta) > 1
    if multi:
        title = f"{len(sessions_meta)} sessions · {kind}"
        subtitle = f"{per_session[0]['started_at']:.0f}–{per_session[-1]['started_at']:.0f}" if per_session else ""
    else:
        title = primary.get("title") or "Session"
        subtitle = ""

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kind": kind,
        "target": target or "latest",
        "source": db_identity(db_path),
        "title": title,
        "subtitle": subtitle,
        "multi": multi,
        "sessions": sessions_meta,
        "per_session": per_session,
        "nodes": all_nodes,
        "asks": all_asks,
        "answers": all_answers,
        "usage": usage,
        "aggregates": aggregates,
        "reconciliation": reconciliation,
    }
