"""Build a deterministic 120-call session store for the client-theme print check.

The product fixture (fixtures/make_demo_db.py) deliberately has 18 calls: enough
to exercise every status, small enough to read. Print layout needs the opposite
property — a long receipt that crosses many page boundaries — so this generator
reuses the same schema and Builder, and writes exactly 120 tool calls with four
failed/recovered retry chains.

Everything is invented; no real user, client or credential appears.

Usage::

    python docs/evidence/client-theme/make_print_fixture.py <out.db>
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fixtures"))

from make_demo_db import BASE, SCHEMA, Builder  # noqa: E402

SESSION_ID = "local-120c0ffee00000000000000000000000000000000000000000000000000000"
CYCLES = 12
CALLS_PER_CYCLE = 10
FAILING_CYCLES = {2, 5, 8, 11}


def _read_result(lines: int, body: str) -> dict:
    return {
        "content": body,
        "total_lines": lines,
        "file_size": len(body.encode("utf-8")),
        "truncated": False,
        "is_binary": False,
        "is_image": False,
        "not_found": False,
    }


def _pytest(command: str, exit_code: int, passed: int = 12) -> dict:
    if exit_code == 0:
        return {"output": f"{'.' * passed}\n{passed} passed in 0.4s\n", "exit_code": 0, "error": None}
    return {
        "output": (
            "..F.........\n"
            "=================================== FAILURES ===================================\n"
            f"E   AssertionError: batch cursor drifted after retry\n"
            f"E   assert 41 == 40\n{command}:17: AssertionError\n"
        ),
        "exit_code": 1,
        "error": None,
    }


def build_120(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    b = Builder(conn)

    b.user(
        "Audit the twelve batch-migration modules: run each test file, read the "
        "failing assertion when one breaks, fix it, and record what changed. "
        "If a fix does not hold, say so rather than calling the batch green.",
    )

    for cycle in range(CYCLES):
        batch = f"{cycle + 1:02d}"
        src = f"src/batch/batch_{batch}.py"
        test = f"tests/test_batch_{batch}.py"
        pytest_cmd = f"pytest -q {test}"
        fails = cycle in FAILING_CYCLES
        base = cycle * CALLS_PER_CYCLE

        source = (
            f"def apply_batch(items):\n"
            f"    cursor = 0\n"
            f"    for item in items:\n"
            f"        cursor = advance(item)\n"
            f"    return cursor\n"
        ) * 3
        test_body = (
            f"from src.batch.batch_{batch} import apply_batch\n\n"
            f"def test_cursor_after_retry():\n"
            f"    assert apply_batch([1, 2, 3]) == 40\n"
        )

        # calls 1-2: dispatched together, so the receipt shows a parallel group
        b.assistant_calls([
            (f"call_{base + 1:03d}", "read_file", {"path": src}),
            (f"call_{base + 2:03d}", "grep", {"pattern": "def apply_batch", "path": "src/batch"}),
        ])
        b.result(f"call_{base + 1:03d}", "read_file", _read_result(120, source), 0.6)
        b.result(f"call_{base + 2:03d}", "grep", {"matches": [{"path": src, "line": 1}] * 3,
                                                "count": 3}, 0.3)

        # call 3: the suite — four cycles fail here
        b.assistant_calls([(f"call_{base + 3:03d}", "terminal", {"command": pytest_cmd})])
        b.result(f"call_{base + 3:03d}", "terminal",
                 _pytest(pytest_cmd, 1 if fails else 0), 6.5 if fails else 2.1)

        # call 4-5: read the failing test, apply the fix
        b.assistant_calls([(f"call_{base + 4:03d}", "read_file", {"path": test})])
        b.result(f"call_{base + 4:03d}", "read_file", _read_result(24, test_body), 0.4)
        b.assistant_calls([(f"call_{base + 5:03d}", "patch",
                            {"path": src, "old_string": "cursor = advance(item)",
                             "new_string": "cursor = advance(item, floor=40)"})])
        b.result(f"call_{base + 5:03d}", "patch",
                 {"ok": True, "bytes_written": 214, "path": src}, 0.5)

        # call 6: the retry after a failure, otherwise a compile check
        retry = fails
        cmd6 = pytest_cmd if retry else "python -m compileall -q src/batch"
        b.assistant_calls([(f"call_{base + 6:03d}", "terminal", {"command": cmd6})])
        b.result(f"call_{base + 6:03d}", "terminal",
                 _pytest(cmd6, 0) if retry else {"output": "", "exit_code": 0, "error": None},
                 2.4 if retry else 1.2)

        # calls 7-10: lint, inspect, document
        b.assistant_calls([(f"call_{base + 7:03d}", "terminal",
                            {"command": f"ruff check {src}"})])
        b.result(f"call_{base + 7:03d}", "terminal",
                 {"output": "All checks passed!\n", "exit_code": 0, "error": None}, 0.8)
        b.assistant_calls([(f"call_{base + 8:03d}", "grep",
                            {"pattern": "batch_id", "path": src})])
        b.result(f"call_{base + 8:03d}", "grep",
                 {"matches": [{"path": src, "line": n} for n in (1, 4, 7, 9, 12)], "count": 5}, 0.3)
        b.assistant_calls([(f"call_{base + 9:03d}", "read_file",
                            {"path": f"docs/batch_{batch}.md"})])
        b.result(f"call_{base + 9:03d}", "read_file",
                 _read_result(18, f"# Batch {batch}\n\nMigration notes.\n"), 0.3)
        b.assistant_calls([(f"call_{base + 10:03d}", "write_file",
                            {"path": f"docs/batch_{batch}.notes.md"})])
        b.result(f"call_{base + 10:03d}", "write_file",
                 {"ok": True, "bytes_written": 512, "path": f"docs/batch_{batch}.notes.md"}, 0.4)

    b.assistant_text(
        "Audited all twelve batch modules: 120 calls, four suites failed on the "
        "cursor drift and each passed on the retry after the floor fix. No batch "
        "was reported green without a passing retry."
    )

    conn.executemany(
        "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        [(SESSION_ID, role, content, call_id, calls, tool, ts)
         for (role, content, call_id, calls, tool, ts) in b.rows],
    )

    message_count = len(b.rows)
    started, ended = BASE, b.t
    assert b.tool_calls == 120, f"expected 120 calls, built {b.tool_calls}"

    usage = [
        ("claude-sonnet-4.6", "anthropic", "https://api.anthropic.com", 96,
         984_320, 121_447, 802_112, 0, 8_120, 0.947231, "estimated", "official_docs_snapshot"),
        ("gpt-5.2-codex", "openai", "https://api.openai.com/v1", 24,
         196_004, 32_318, 310_880, 0, 2_204, 0.412095, "estimated", "official_docs_snapshot"),
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
    conn.execute(
        "INSERT INTO sessions (id, source, user_id, chat_id, chat_type, model, started_at, ended_at, "
        "end_reason, message_count, tool_call_count, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, reasoning_tokens, cwd, git_branch, git_repo_root, billing_provider, "
        "billing_base_url, estimated_cost_usd, actual_cost_usd, cost_status, cost_source, "
        "pricing_version, title, last_activity_at, api_call_count, profile_name, created_source, "
        "title_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (SESSION_ID, "cli", "local-user", SESSION_ID, "dm", "claude-sonnet-4.6",
         started, ended, "session_closed", message_count, b.tool_calls,
         sum(u[4] for u in usage), sum(u[5] for u in usage), sum(u[6] for u in usage), 0,
         sum(u[8] for u in usage), "C:/Users/demo/Projects/batch-migration",
         "audit/batch-migration", "C:/Users/demo/Projects/batch-migration",
         "anthropic", "https://api.anthropic.com", total_cost, 0.0, "estimated",
         "official_docs_snapshot", "2026.11.1",
         "Batch migration audit — 120 calls",
         ended, sum(u[3] for u in usage), "default", "cli", "llm"),
    )
    conn.commit()
    conn.close()
    return path


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path("print-120.db")
    build_120(out)
    from receipts.manifest import build_manifest

    manifest = build_manifest("latest", db_path=out)
    print(json.dumps({
        "store": str(out),
        "nodes": len(manifest["nodes"]),
        "tool_calls": manifest["aggregates"]["tool_calls"],
        "failures": manifest["aggregates"]["failures"],
        "retries": manifest["aggregates"]["retries"],
        "chains": manifest["aggregates"]["retry_chains"],
    }, indent=2))
    assert len(manifest["nodes"]) == 120
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
