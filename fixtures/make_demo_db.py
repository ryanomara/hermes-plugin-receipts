"""Generate a synthetic session store for tests and for the demo artifact.

Everything here is invented: a fictional ``demo-storefront`` checkout bug, no
client names, no campaign internals, no real credentials.  That is deliberate —
the acceptance rules require the demo to be sanitized, and generating it is more
reliable than scrubbing a real session afterwards.

The numbers are internally exact (the per-model cost rows sum to the session
total), so reconciliation exercises real arithmetic rather than a tolerance.

Usage::

    python fixtures/make_demo_db.py out/state.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BASE = 1793800000.0  # fixed epoch so the fixture is deterministic

SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    session_key TEXT,
    chat_id TEXT,
    chat_type TEXT,
    thread_id TEXT,
    display_name TEXT,
    origin_json TEXT,
    expiry_finalized INTEGER DEFAULT 0,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    system_prompt_hash TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT,
    git_branch TEXT,
    git_repo_root TEXT,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    pricing_version TEXT,
    title TEXT,
    last_activity_at REAL,
    last_activity_description TEXT,
    last_activity_provenance TEXT,
    api_call_count INTEGER DEFAULT 0,
    handoff_state TEXT,
    handoff_platform TEXT,
    handoff_error TEXT,
    compression_failure_cooldown_until REAL,
    compression_failure_error TEXT,
    compression_fallback_streak INTEGER NOT NULL DEFAULT 0,
    compression_ineffective_count INTEGER NOT NULL DEFAULT 0,
    profile_name TEXT,
    rewind_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    last_read_at REAL,
    title_source TEXT,
    git_metadata_generation INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_source TEXT,
    tool_names TEXT,
    auto_archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    effect_disposition TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    reasoning_details TEXT,
    codex_reasoning_items TEXT,
    codex_message_items TEXT,
    platform_message_id TEXT,
    observed INTEGER DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0,
    api_content TEXT,
    display_kind TEXT,
    display_metadata TEXT
);

CREATE TABLE session_model_usage (
    session_id TEXT NOT NULL,
    model TEXT NOT NULL,
    billing_provider TEXT NOT NULL DEFAULT '',
    billing_base_url TEXT NOT NULL DEFAULT '',
    billing_mode TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    api_call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    actual_cost_usd REAL NOT NULL DEFAULT 0,
    cost_status TEXT,
    cost_source TEXT,
    first_seen REAL,
    last_seen REAL,
    PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task)
);
"""

SESSION_ID = "local-7f3a91c2demo00000000000000000000000000000000000000000000000000"

ASK = (
    "Checkout on the demo storefront returns a 500 whenever the cart has a discount code. "
    "Reproduce it, find the root cause, fix it, and prove the fix with the test suite. "
    "If you can't push the branch, say so — don't claim it landed."
)


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _big_json_file(lines: int) -> str:
    body = ",\n".join(
        '  {"sku": "SKU-%05d", "tier": %d, "floor": %.2f, "ceiling": %.2f}' % (i, i % 4, i * 0.5, i * 0.9)
        for i in range(lines)
    )
    return "{\n  \"version\": 3,\n  \"prices\": [\n" + body + "\n  ]\n}\n"


class Builder:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.t = BASE
        self.rows: list[tuple] = []
        self.tool_calls = 0
        self.calls_by_id: dict[str, dict] = {}

    def user(self, text: str, advance: float = 0.0) -> None:
        self.t += advance
        self.rows.append(("user", text, None, None, None, self.t))

    def assistant_calls(self, calls: list[tuple[str, str, dict]], advance: float = 2.0) -> None:
        """One assistant message dispatching one or more parallel tool calls."""
        self.t += advance
        payload = []
        for call_id, tool, args in calls:
            payload.append({
                "id": call_id,
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args, ensure_ascii=False)},
            })
            self.calls_by_id[call_id] = {"tool": tool, "args": args, "ts": self.t}
        self.rows.append(("assistant", None, None, json.dumps(payload, ensure_ascii=False), None, self.t))
        self.tool_calls += len(calls)

    def result(self, call_id: str, tool: str, content, duration: float) -> None:
        self.t += duration
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        self.rows.append(("tool", content, call_id, None, tool, self.t))

    def assistant_text(self, text: str, advance: float = 1.5) -> None:
        self.t += advance
        self.rows.append(("assistant", text, None, None, None, self.t))


def build(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    b = Builder(conn)

    b.user(ASK, advance=0.0)

    # 1-2: two reads dispatched together (parallel group)
    b.assistant_calls([
        ("call_demo_001", "read_file", {"path": "src/cart/pricing.py"}),
        ("call_demo_002", "read_file", {"path": "src/routes/checkout.py"}),
    ])
    b.result("call_demo_001", "read_file", {
        "content": "def line_total(item):\n    return item['unit_price'] * item['quantity']\n\n"
                   "def apply_discount(total, pct):\n    return total - (total * pct / 100)\n",
        "total_lines": 9, "file_size": 187, "truncated": False,
        "is_binary": False, "is_image": False, "not_found": False,
    }, 0.9)
    b.result("call_demo_002", "read_file", {
        "content": "from flask import Blueprint\nbp = Blueprint('checkout', __name__)\n"
                   "@bp.post('/checkout')\ndef checkout():\n    total = line_total(cart)\n"
                   "    total = apply_discount(total, code.pct)\n    return receipt(total)\n",
        "total_lines": 14, "file_size": 321, "truncated": False,
        "is_binary": False, "is_image": False, "not_found": False,
    }, 0.4)

    # 3: the suite fails
    b.assistant_calls([("call_demo_003", "terminal", {"command": "pytest -q tests/test_checkout.py"})])
    b.result("call_demo_003", "terminal", {
        "output": "..F....F\n"
                  "=================================== FAILURES ===================================\n"
                  "_________________ test_checkout_with_percentage_discount _________________\n"
                  "E   assert 89.99999999999999 == 90.0\n"
                  "tests/test_checkout.py:41: AssertionError\n"
                  "_________________ test_receipt_rounds_currency _________________\n"
                  "E   assert '$89.99' == '$90.00'\n",
        "exit_code": 1,
        "error": None,
    }, 8.4)

    # 4: read the failing test
    b.assistant_calls([("call_demo_004", "read_file", {"path": "tests/test_checkout.py"})])
    b.result("call_demo_004", "read_file", {
        "content": "def test_checkout_with_percentage_discount(client):\n"
                   "    resp = client.post('/checkout', json={'items': CART, 'code': 'TEN'})\n"
                   "    assert resp.json['total'] == 90.0\n",
        "total_lines": 58, "file_size": 1644, "truncated": False,
        "is_binary": False, "is_image": False, "not_found": False,
    }, 0.5)

    # 5: retry attempt 2 - narrower run (same intent, extended args)
    b.assistant_calls([("call_demo_005", "terminal",
                        {"command": "pytest -q tests/test_checkout.py -x --tb=short"})])
    b.result("call_demo_005", "terminal", {
        "output": "F\n"
                  "_________________ test_checkout_with_percentage_discount _________________\n"
                  "E   assert 89.99999999999999 == 90.0\n"
                  "1 failed, 6 passed in 6.42s\n",
        "exit_code": 1,
        "error": None,
    }, 6.9)

    # 6: pull the debugging skill
    b.assistant_calls([("call_demo_006", "skill_view", {"name": "systematic-debugging"})])
    b.result("call_demo_006", "skill_view", {
        "success": True, "name": "systematic-debugging",
        "description": "4-phase root cause debugging",
        "content": "# Systematic debugging\n\nPhase 1: reproduce...\n",
        "path": "software-development/systematic-debugging/SKILL.md",
        "readiness_status": "available",
    }, 0.6)

    # 7: the suspect module
    b.assistant_calls([("call_demo_007", "read_file", {"path": "src/cart/discount.py"})])
    b.result("call_demo_007", "read_file", {
        "content": "def pct(value, percent):\n    return value * (1 - percent / 100)\n",
        "total_lines": 3, "file_size": 62, "truncated": False,
        "is_binary": False, "is_image": False, "not_found": False,
    }, 0.4)

    # 8: a large generated file - exercises payload truncation
    b.assistant_calls([("call_demo_008", "read_file", {"path": "src/generated/pricing_schema.json"})])
    b.result("call_demo_008", "read_file", {
        "content": _big_json_file(3180),
        "total_lines": 3183, "file_size": 214_880, "truncated": False,
        "is_binary": False, "is_image": False, "not_found": False,
    }, 1.8)

    # 9: confirm where rounding happens
    b.assistant_calls([("call_demo_009", "terminal", {"command": "grep -rn 'round(' src/cart/"})])
    b.result("call_demo_009", "terminal", {
        "output": "src/cart/receipt.py:12:    return round(total, 2)\n",
        "exit_code": 0, "error": None,
    }, 0.3)

    # 10: the fix
    b.assistant_calls([("call_demo_010", "write_file", {"path": "src/cart/discount.py"})])
    b.result("call_demo_010", "write_file", {
        "bytes_written": 244,
        "files_modified": ["src/cart/discount.py"],
        "resolved_path": "src/cart/discount.py",
        "verified": True,
        "lint": {"status": "ok"},
    }, 1.2)

    # 11: patch fails - stale context
    b.assistant_calls([("call_demo_011", "patch", {"path": "src/cart/discount.py"})])
    b.result("call_demo_011", "patch", {
        "error": "old_string not found in src/cart/discount.py",
        "path": "src/cart/discount.py",
    }, 0.2)

    # 12: retry with narrower context - recovers
    b.assistant_calls([("call_demo_012", "patch", {"path": "src/cart/discount.py"})])
    b.result("call_demo_012", "patch", {
        "files_modified": ["src/cart/discount.py"],
        "resolved_path": "src/cart/discount.py",
        "verified": True,
    }, 1.1)

    # 13: the suite again - retry attempt 3, recovers
    b.assistant_calls([("call_demo_013", "terminal", {"command": "pytest -q tests/test_checkout.py"})])
    b.result("call_demo_013", "terminal", {
        "output": ".......\n7 passed in 9.31s\n",
        "exit_code": 0, "error": None,
    }, 9.6)

    # 14: long build
    b.assistant_calls([("call_demo_014", "terminal", {"command": "npm run build"})])
    b.result("call_demo_014", "terminal", {
        "output": "> demo-storefront@2.4.0 build\n> vite build\n\n"
                  "vite v5.4.2 building for production...\n"
                  "✓ 412 modules transformed.\n✓ built in 41.02s\n",
        "exit_code": 0, "error": None,
    }, 41.8)

    # 15: push fails and is never retried - the honest ending
    b.assistant_calls([("call_demo_015", "terminal",
                        {"command": "git push origin fix/discount-rounding"})])
    b.result("call_demo_015", "terminal", {
        "output": "remote: Permission to demo-org/demo-storefront.git denied to agent-ci.\n"
                  "fatal: unable to access 'https://github.com/demo-org/demo-storefront.git/': "
                  "The requested URL returned error: 403\n",
        "exit_code": 128,
        "error": None,
    }, 2.1)

    # 16: check state (same tool, different intent - must NOT be read as a retry)
    b.assistant_calls([("call_demo_016", "terminal", {"command": "git status --short --branch"})])
    b.result("call_demo_016", "terminal", {
        "output": "## fix/discount-rounding...origin/main\n M src/cart/discount.py\n",
        "exit_code": 0, "error": None,
    }, 0.3)

    # 17: an unstructured result - exercises the "unverified" stance
    b.assistant_calls([("call_demo_017", "web_search",
                        {"query": "python decimal rounding half up money"})])
    b.result("call_demo_017", "web_search",
             "1. Python decimal — ROUND_HALF_UP (docs.python.org)\n"
             "2. Money and rounding (martin fowler)\n", 1.4)

    # 18: leave a note on the card
    b.assistant_calls([("call_demo_018", "kanban_comment",
                        {"task_id": "t_demo0001", "body": "Root cause: float division in pct()."})])
    b.result("call_demo_018", "kanban_comment",
             {"ok": True, "comment_id": "c_0001", "task_id": "t_demo0001"}, 0.2)

    b.assistant_text(
        "Root cause: `pct()` multiplies by a float and the receipt rounds half-even, so 10% off $100 "
        "lands on 89.99999999999999 and renders $89.99. Rewrote it to use `Decimal` with "
        "ROUND_HALF_UP; the checkout suite is green (7 passed).\n\n"
        "One thing did not land: `git push` was refused (403 for agent-ci), and I did not retry it. "
        "The branch fix/discount-rounding exists locally only — someone with push rights needs to publish it."
    )

    # ---- persist messages -------------------------------------------------
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        [(SESSION_ID, role, content, call_id, calls, tool, ts)
         for (role, content, call_id, calls, tool, ts) in b.rows],
    )

    message_count = len(b.rows)
    started = BASE
    ended = b.t

    # ---- usage: exact, and the two rows sum to the session total ----------
    usage = [
        ("claude-sonnet-4.6", "anthropic", "https://api.anthropic.com", 18,
         184_320, 21_447, 402_112, 0, 3_120, 0.847231, "estimated", "official_docs_snapshot"),
        ("gpt-5.2-codex", "openai", "https://api.openai.com/v1", 9,
         96_004, 12_318, 210_880, 0, 1_204, 0.412095, "estimated", "official_docs_snapshot"),
    ]
    for (model, provider, base_url, calls, tin, tout, cread, cwrite, treason,
         cost, status, source) in usage:
        conn.execute(
            "INSERT INTO session_model_usage (session_id, model, billing_provider, billing_base_url, "
            "billing_mode, task, api_call_count, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, "
            "cost_status, cost_source, first_seen, last_seen) "
            "VALUES (?,?,?,?,'','',?,?,?,?,?,?,?,?,?,?,?,?)",
            (SESSION_ID, model, provider, base_url, calls, tin, tout, cread, cwrite,
             treason, cost, 0.0, status, source, started, ended),
        )

    total_cost = round(sum(u[9] for u in usage), 6)
    total_in = sum(u[4] for u in usage)
    total_out = sum(u[5] for u in usage)
    total_cread = sum(u[6] for u in usage)
    total_reason = sum(u[8] for u in usage)

    conn.execute(
        "INSERT INTO sessions (id, source, user_id, chat_id, chat_type, model, started_at, ended_at, "
        "end_reason, message_count, tool_call_count, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, reasoning_tokens, cwd, git_branch, git_repo_root, billing_provider, "
        "billing_base_url, estimated_cost_usd, actual_cost_usd, cost_status, cost_source, "
        "pricing_version, title, last_activity_at, api_call_count, profile_name, created_source, "
        "title_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (SESSION_ID, "cli", "local-user", SESSION_ID, "dm", "claude-sonnet-4.6",
         started, ended, "session_closed", message_count, b.tool_calls, total_in, total_out,
         total_cread, 0, total_reason, "C:/Users/demo/Projects/demo-storefront",
         "fix/discount-rounding", "C:/Users/demo/Projects/demo-storefront",
         "anthropic", "https://api.anthropic.com", total_cost, 0.0, "estimated",
         "official_docs_snapshot", "2026.11.1",
         "Checkout 500 on discounted carts — reproduce, fix, verify",
         ended, sum(u[3] for u in usage), "default", "cli", "llm"),
    )

    conn.commit()
    conn.close()
    return path


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path("fixtures/demo-state.db")
    path = build(out)
    print(f"synthetic session store → {path}")
    print(f"  session {SESSION_ID[:20]}… · {ASK.splitlines()[0][:60]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
