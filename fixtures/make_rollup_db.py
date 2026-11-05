"""Generate a synthetic multi-week store + kanban boards for rollup tests.

Everything here is invented: fake cards, fake models, fake repositories.  The
point is a store whose every count, duration and dollar can be checked by hand,
so the rollup's arithmetic is asserted exactly rather than approximately.

Layout::

    <out>/state.db                                  sessions + messages + usage
    <out>/kanban/boards/alpha/kanban.db             cards, one of two boards
    <out>/kanban/boards/beta/kanban.db
    <out>/kanban/boards/<slug>/board.json

Fixed epoch: the current window is ``AS_OF-7d .. AS_OF`` and the previous window
is the seven days before it, so week-over-week deltas are computable by hand.

Usage::

    python fixtures/make_rollup_db.py out/rollup-fixture
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_demo_db import SCHEMA as STATE_SCHEMA  # noqa: E402

#: 2026-11-05T23:00:00Z — the pinned end of every test window.
AS_OF = 1793919600.0
DAY = 86400.0
CURRENT_START = AS_OF - 7 * DAY
PREVIOUS_START = AS_OF - 14 * DAY

KANBAN_SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT,
    assignee TEXT,
    status TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    result TEXT
);
"""


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Session:
    """One synthetic session: ordered messages, then its accounting rows."""

    def __init__(self, sid: str, source: str, title: str, started: float,
                 model: str, parent: str | None = None, cwd: str = "C:/demo/repo"):
        self.id = sid
        self.source = source
        self.title = title
        self.started = started
        self.t = started
        self.model = model
        self.parent = parent
        self.cwd = cwd
        self.rows: list[tuple] = []
        self.tool_calls = 0
        self.usage: list[dict] = []
        self.recorded_cost: float | None = None
        self.cost_status = "estimated"

    # -- messages ---------------------------------------------------------
    def user(self, text: str, advance: float = 0.0) -> "Session":
        self.t += advance
        self.rows.append(("user", text, None, None, None, self.t))
        return self

    def calls(self, calls: list[tuple[str, str, dict]], advance: float = 1.0) -> dict:
        self.t += advance
        payload = []
        for call_id, tool, args in calls:
            payload.append({
                "id": call_id, "type": "function",
                "function": {"name": tool, "arguments": _json(args)},
            })
            self.tool_calls += 1
        self.rows.append(("assistant", None, None, _json(payload), None, self.t))
        return {cid: self.t for cid, _, _ in calls}

    def result(self, call_id: str, tool: str, content, duration: float) -> "Session":
        self.t += duration
        if not isinstance(content, str):
            content = _json(content)
        self.rows.append(("tool", content, call_id, None, tool, self.t))
        return self

    def answer(self, text: str, advance: float = 2.0) -> "Session":
        self.t += advance
        self.rows.append(("assistant", text, None, None, None, self.t))
        return self

    def add_usage(self, model: str, provider: str, calls: int, tin: int, tout: int,
                  cost: float, task: str = "") -> "Session":
        self.usage.append({"model": model, "provider": provider, "calls": calls,
                           "in": tin, "out": tout, "cost": cost, "task": task})
        return self


def _kanban_board(home: Path, slug: str, name: str, cards: list[dict]) -> Path:
    board_dir = home / "boards" / slug
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "board.json").write_text(
        json.dumps({"slug": slug, "name": name, "archived": False}, indent=2), encoding="utf-8")
    db = board_dir / "kanban.db"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    conn.executescript(KANBAN_SCHEMA)
    for card in cards:
        conn.execute(
            "INSERT INTO tasks (id, title, body, assignee, status, created_at, started_at, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (card["id"], card["title"], "", "default", card["status"],
             int(PREVIOUS_START), int(PREVIOUS_START), int(AS_OF - DAY)),
        )
    conn.commit()
    conn.close()
    return db


def build(out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    state_db = out / "state.db"
    if state_db.exists():
        state_db.unlink()

    home = out / "kanban"
    _kanban_board(home, "alpha", "Alpha board", [
        {"id": "t_a0000001", "title": "Rollup fixture card one", "status": "done"},
        {"id": "t_a0000002", "title": "Rollup fixture card two", "status": "done"},
        {"id": "t_a0000003", "title": "Previous window card", "status": "done"},
    ])
    _kanban_board(home, "beta", "Beta board", [
        {"id": "t_b0000001", "title": "Rollup fixture card beta", "status": "done"},
    ])

    sessions: list[Session] = []

    # ---- current window --------------------------------------------------
    # aaa: two recovered retry chains (test failure, patch context) and one
    # auth failure that never recovered.
    aaa = Session("local-aaa000000000000000000000000000000000000000000000000000000000000",
                  "kanban", "Rollup fixture card one", CURRENT_START + 3600, "m-one")
    aaa.user("# Kanban task t_a0000001: Rollup fixture card one\n\nAssignee: default\n"
             "Status:   running\n\n## Body\nSynthetic card for the rollup fixture.")
    aaa.calls([("c1", "terminal", {"command": "pytest -q tests/test_checkout.py"})])
    aaa.result("c1", "terminal", {
        "output": "..F\n=========================== FAILURES ===========================\n"
                  "E   assert 1 == 2\ntests/test_checkout.py:41: AssertionError\n",
        "exit_code": 1, "error": None}, 6.0)
    aaa.calls([("c2", "terminal", {"command": "pytest -q tests/test_checkout.py -x --tb=short"})])
    aaa.result("c2", "terminal", {"output": "7 passed in 5.00s\n", "exit_code": 0, "error": None}, 5.0)
    aaa.calls([("c3", "patch", {"path": "src/cart/discount.py", "old_string": "float(total)"})])
    aaa.result("c3", "patch", {"error": "old_string not found in src/cart/discount.py"}, 0.4)
    aaa.calls([("c4", "patch", {"path": "src/cart/discount.py", "old_string": "round(total, 2)"})])
    aaa.result("c4", "patch", {"files_modified": ["src/cart/discount.py"], "verified": True}, 0.3)
    aaa.calls([("c5", "terminal", {"command": "git push origin fix/rounding"})])
    aaa.result("c5", "terminal", {
        "output": "remote: Permission denied\nfatal: error: 403\n", "exit_code": 128, "error": None}, 1.2)
    aaa.calls([("c6", "read_file", {"path": "src/cart/discount.py"})])
    aaa.result("c6", "read_file", {"content": "def pct():\n    pass\n", "total_lines": 2}, 0.2)
    aaa.calls([("c7", "write_file", {"path": "src/cart/discount.py"})])
    aaa.result("c7", "write_file", {"bytes_written": 244, "verified": True}, 0.5)
    aaa.answer("Fixed the rounding bug; the push was refused and was not retried.")
    aaa.add_usage("m-one", "prov-a", calls=12, tin=40_000, tout=8_000, cost=0.500000)
    aaa.recorded_cost = 0.500000
    sessions.append(aaa)

    # bbb: a timeout that recovered, one missing file, one unverified result.
    bbb = Session("local-bbb000000000000000000000000000000000000000000000000000000000000",
                  "kanban", "Rollup fixture card beta", CURRENT_START + 2 * DAY, "m-two")
    bbb.user("# Kanban task t_b0000001: Rollup fixture card beta\n\n## Body\nSynthetic.")
    bbb.calls([("b1", "terminal", {"command": "npm run build"})])
    bbb.result("b1", "terminal", {"output": "Error executing tool 'terminal': timed out after 420.0s",
                                  "exit_code": None, "error": "timed out after 420.0s"}, 12.5)
    bbb.calls([("b2", "terminal", {"command": "npm run build -- --verbose"})])
    bbb.result("b2", "terminal", {"output": "built in 3.0s\n", "exit_code": 0, "error": None}, 3.0)
    bbb.calls([("b3", "read_file", {"path": "src/missing.py"})])
    bbb.result("b3", "read_file", {"error": "FileNotFoundError: [Errno 2] No such file or directory: 'src/missing.py'"}, 0.3)
    bbb.calls([("b4", "web_search", {"query": "rollup fixture search"})])
    bbb.result("b4", "web_search", "1. A result that carries no structured status\n", 0.4)
    bbb.answer("Build recovered after a timeout; one file was missing.")
    bbb.add_usage("m-two", "prov-b", calls=4, tin=12_000, tout=2_000, cost=0.250000)
    bbb.recorded_cost = 0.250000
    sessions.append(bbb)

    # ccc: a sub-agent that inherits its parent's card (no header of its own).
    ccc = Session("local-ccc000000000000000000000000000000000000000000000000000000000000",
                  "subagent", "Sub-agent research", CURRENT_START + 3600 + 1800, "m-one",
                  parent=aaa.id)
    ccc.user("Research the failing assertion and report back.")
    ccc.calls([("s1", "web_search", {"query": "python decimal rounding"})])
    ccc.result("s1", "web_search", "1. Decimal rounding docs\n", 0.9)
    ccc.answer("Found the rounding rule.")
    ccc.add_usage("m-one", "prov-a", calls=2, tin=5_000, tout=1_000, cost=0.100000)
    ccc.recorded_cost = 0.100000
    sessions.append(ccc)

    # ddd: no card at all; a safety refusal that is not a failure of the world.
    ddd = Session("local-ddd000000000000000000000000000000000000000000000000000000000000",
                  "desktop", "Unattributed desktop session", CURRENT_START + 3 * DAY, "m-two",
                  cwd="C:/demo/desktop")
    ddd.user("Edit a file the guard will not let me overwrite.")
    ddd.calls([("d1", "write_file", {"path": "C:/demo/repo/index.html"})])
    ddd.result("d1", "write_file", {"error": "Refusing to overwrite C:/demo/repo/index.html: not a file this agent wrote"}, 0.3)
    ddd.calls([("d2", "read_file", {"path": "C:/demo/repo/index.html"})])
    ddd.result("d2", "read_file", {"content": "<h1>hi</h1>\n", "total_lines": 1}, 0.2)
    ddd.answer("Read instead of overwriting.")
    ddd.add_usage("m-two", "prov-b", calls=2, tin=3_000, tout=500, cost=0.050000)
    ddd.recorded_cost = 0.050000
    sessions.append(ddd)

    # eee: a card with work but no usage rows — cost must be reported unknown,
    # never assumed zero.
    eee = Session("local-eee000000000000000000000000000000000000000000000000000000000000",
                  "kanban", "Rollup fixture card two", CURRENT_START + 4 * DAY, "m-one")
    eee.user("# Kanban task t_a0000002: Rollup fixture card two\n\n## Body\nNo usage rows here.")
    eee.calls([("e1", "read_file", {"path": "src/app.py"})])
    eee.result("e1", "read_file", {"content": "print('hi')\n", "total_lines": 1}, 0.2)
    eee.answer("Read the file.")
    eee.recorded_cost = 0.020000
    sessions.append(eee)

    # ---- previous window -------------------------------------------------
    fff = Session("local-fff000000000000000000000000000000000000000000000000000000000000",
                  "kanban", "Previous window card", PREVIOUS_START + DAY, "m-one")
    fff.user("# Kanban task t_a0000003: Previous window card\n\n## Body\nSynthetic.")
    fff.calls([("f1", "terminal", {"command": "make test"})])
    fff.result("f1", "terminal", {"output": "1 failed\n", "exit_code": 1, "error": None}, 3.0)
    fff.calls([("f2", "terminal", {"command": "make test -- --fix"})])
    fff.result("f2", "terminal", {"output": "all good\n", "exit_code": 0, "error": None}, 2.0)
    fff.answer("Green again.")
    fff.add_usage("m-one", "prov-a", calls=6, tin=9_000, tout=1_000, cost=0.300000)
    fff.recorded_cost = 0.300000
    sessions.append(fff)

    ggg = Session("local-ggg000000000000000000000000000000000000000000000000000000000000",
                  "desktop", "Previous unattributed", PREVIOUS_START + 2 * DAY, "m-two")
    ggg.user("Just a read.")
    ggg.calls([("g1", "read_file", {"path": "README.md"})])
    ggg.result("g1", "read_file", {"content": "# demo\n", "total_lines": 1}, 0.5)
    ggg.answer("Read it.")
    ggg.add_usage("m-two", "prov-b", calls=1, tin=1_000, tout=200, cost=0.020000)
    ggg.recorded_cost = 0.020000
    sessions.append(ggg)

    # ---- persist ---------------------------------------------------------
    conn = sqlite3.connect(state_db)
    conn.executescript(STATE_SCHEMA)
    for s in sessions:
        conn.executemany(
            "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp) "
            "VALUES (?,?,?,?,?,?,?)",
            [(s.id, role, content, call_id, calls, tool, ts)
             for (role, content, call_id, calls, tool, ts) in s.rows],
        )
        for u in s.usage:
            conn.execute(
                "INSERT INTO session_model_usage (session_id, model, billing_provider, billing_base_url, "
                "billing_mode, task, api_call_count, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, cost_status, "
                "cost_source, first_seen, last_seen) VALUES (?,?,?,?,'',?,?,?,?,0,0,0,?,0.0,'estimated',?,?,?)",
                (s.id, u["model"], u["provider"], "", u["task"], u["calls"], u["in"], u["out"],
                 u["cost"], "fixture", s.started, s.t),
            )
        tin = sum(u["in"] for u in s.usage)
        tout = sum(u["out"] for u in s.usage)
        conn.execute(
            "INSERT INTO sessions (id, source, model, started_at, ended_at, end_reason, message_count, "
            "tool_call_count, input_tokens, output_tokens, cwd, estimated_cost_usd, actual_cost_usd, "
            "cost_status, cost_source, pricing_version, title, last_activity_at, api_call_count, "
            "profile_name, created_source, parent_session_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0.0,?,?,?,?,?,?,?,?,?)",
            (s.id, s.source, s.model, s.started, s.t, "session_closed", len(s.rows),
             s.tool_calls, tin, tout, s.cwd, s.recorded_cost, s.cost_status, "fixture",
             "2026.11.1", s.title, s.t, sum(u["calls"] for u in s.usage), "default", "fixture",
             s.parent),
        )
    conn.commit()
    conn.close()
    return {"state_db": state_db, "kanban_home": home, "as_of": AS_OF}


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path("fixtures/rollup-fixture")
    paths = build(out)
    print(f"synthetic rollup fixture → {paths['state_db'].parent}")
    print(f"  state.db   {paths['state_db']}")
    print(f"  kanban     {paths['kanban_home']}")
    print(f"  as of      {paths['as_of']:.0f} (2026-11-05T23:00:00Z)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
