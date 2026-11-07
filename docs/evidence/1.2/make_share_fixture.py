"""Build the synthetic store behind the 1.2 share-safe samples.

The product fixture (``fixtures/make_demo_db.py``) is sanitized *by
construction*: it deliberately contains nothing a client must not see, so a
redacted render of it removes exactly one account path and proves very little.
The samples in ``samples/`` therefore use this store instead — a fictional
campaign launch whose tool calls carry an invented secret for every class the
redaction engine claims, plus a client name from the word list.

Every credential below is fabricated: ``example.com`` hosts, private-range
addresses, a 555-style number, and provider shapes that exist only as demo
values. Nothing here is, or ever was, a working secret.

**Why no ``sk-`` / ``AKIA`` / ``ghp_`` / PEM literals here.** The plugin
security scanner (``hermes plugins validate``) treats a key-shaped literal in a
shipped file as a leaked credential, and it is right to: the only safe default
is to assume it is real. Those shapes are covered by ``tests/test_privacy.py``,
where the guard's test-tree policy expects hostile fixtures. This file uses
provider shapes a scanner does not read as credentials (Stripe ``whsec_``,
Slack ``xoxb-``) so the committed sample can still show provider-token
redaction without teaching anyone to route around the gate.

The numbers are exact and internally consistent (the per-model cost rows sum to
the session total), like the demo fixture.

Usage::

    python docs/evidence/1.2/make_share_fixture.py [out/state.db]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fixtures"))

from make_demo_db import BASE, SCHEMA, Builder  # noqa: E402

SESSION_ID = "local-4b7e2d91share"
TITLE = "Spring landing page — deploy staging, rotate credentials, hand off"

#: Invented credentials, one per class. Kept here (not inline in the calls) so
#: the sample check can assert none of them survives into a shared document.
LITERALS = {
    "slack_token": "xoxb-000000000000000-demo",
    "stripe_secret": "whsec_demo0000000000000000",
    "webhook_secret": "demo-env-7f3a9c12",
    "db_password": "demo-password-9f2c",
    "bearer": "campaign-deploy-7f3a9c12",
    "query_token": "demo-query-8b2e41",
    "jwt": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiJjYW1wYWlnbi1kZXBsb3kiLCJpYXQiOjE3OTM4MDAwMDB9"
        ".ZmFrZS1zaWduYXR1cmUtZm9yLXRoZS1yZWNlaXB0cy1zaGFyZS1kZW1v"
    ),
    "email": "client.contact@example.com",
    "phone": "+49 151 23456789",
    "ip": "10.42.7.19",
    "word": "Acme Industries",
}

ASK = (
    "Ship the spring landing page for Acme Industries: deploy staging, rotate the staging "
    "credentials, and hand the client a shareable receipt. Production promotion is gated on "
    "sign-off — do not cut over."
)

DEPLOY_CMD = (
    "curl -sS -X POST -H \"Authorization: Bearer {bearer}\" "
    "\"https://api.example.com/v1/deploy?token={token}&env=staging\" "
    "-F bundle=@dist/campaign.zip"
).format(bearer=LITERALS["bearer"], token=LITERALS["query_token"])


def _read(path: str, content: str, lines: int, size: int) -> dict:
    return {
        "content": content,
        "total_lines": lines,
        "file_size": size,
        "truncated": False,
        "is_binary": False,
        "is_image": False,
        "not_found": False,
    }


def build(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    b = Builder(conn)

    b.user(ASK, advance=0.0)

    # 1: the staging env file — two provider keys and a webhook secret
    b.assistant_calls([("call_share_001", "read_file", {"path": ".env.staging"})])
    b.result("call_share_001", "read_file", _read(
        ".env.staging",
        "VITE_API_BASE=https://api.example.com\n"
        f"SLACK_BOT_TOKEN={LITERALS['slack_token']}\n"
        f"STRIPE_WEBHOOK_SECRET={LITERALS['stripe_secret']}\n"
        f"CAMPAIGN_WEBHOOK_SECRET={LITERALS['webhook_secret']}\n"
        "NODE_ENV=production\n",
        5, 214,
    ), 0.4)

    # 2: the campaign database (credentials embedded in the DSN)
    b.assistant_calls([("call_share_002", "terminal", {
        "command": "psql \"postgres://demo_user:" + LITERALS["db_password"]
                   + "@db.example.com:5432/campaign\" -c \"select count(*) from campaign_leads\"",
    })])
    b.result("call_share_002", "terminal", {
        "output": " count\n-------\n 48120\n(1 row)\n",
        "exit_code": 0, "error": None,
    }, 5.8)

    # 3: first deploy attempt — upstream timeout
    b.assistant_calls([("call_share_003", "terminal", {"command": DEPLOY_CMD})])
    b.result("call_share_003", "terminal", {
        "output": "HTTP/1.1 502 Bad Gateway\nupstream staging-web timed out after 30s\n",
        "exit_code": 1, "error": None,
    }, 31.4)

    # 4: same deploy, retried after the timeout — this one lands
    b.assistant_calls([("call_share_004", "terminal", {"command": DEPLOY_CMD + " --retry 3"})])
    b.result("call_share_004", "terminal", {
        "output": "HTTP/1.1 200 OK\n{\"deploy\":\"campaign-2026-11-06-3\",\"status\":\"live\",\"region\":\"eu-central\"}\n",
        "exit_code": 0, "error": None,
    }, 12.6)

    # 5: a short-lived API token in a tool payload
    b.assistant_calls([("call_share_005", "terminal", {
        "command": "curl -sS -X POST -u \"$DEPLOY_USER:$DEPLOY_PASS\" "
                   "https://auth.example.com/oauth/token -d grant_type=client_credentials",
    })])
    b.result("call_share_005", "terminal", {
        "output": json.dumps({"access_token": LITERALS["jwt"], "expires_in": 3600}) + "\n",
        "exit_code": 0, "error": None,
    }, 0.9)

    # 6-8: the three account-path spellings a real session mixes
    b.assistant_calls([("call_share_006", "read_file", {
        "path": "C:/Users/demo/Projects/campaign/config/production.yaml",
    })])
    b.result("call_share_006", "read_file", _read(
        "C:/Users/demo/Projects/campaign/config/production.yaml",
        "base_url: https://www.example.com\npromote: manual\napprovals_required: 2\n",
        3, 88,
    ), 0.3)
    b.assistant_calls([("call_share_007", "read_file", {
        "path": "/home/deploy/campaign/config/staging.yaml",
    })])
    b.result("call_share_007", "read_file", _read(
        "/home/deploy/campaign/config/staging.yaml",
        "base_url: https://staging.example.com\nttl_hours: 72\n",
        2, 62,
    ), 0.3)
    b.assistant_calls([("call_share_008", "read_file", {
        "path": "/c/Users/demo/Projects/campaign/README.md",
    })])
    b.result("call_share_008", "read_file", _read(
        "/c/Users/demo/Projects/campaign/README.md",
        "# Campaign landing page\n\nBuild: npm run build. Deploy: push to main.\n",
        3, 74,
    ), 0.3)

    # 9: restart the staging service on its private address
    b.assistant_calls([("call_share_009", "terminal", {
        "command": "ssh deploy@" + LITERALS["ip"]
                   + " 'sudo systemctl restart campaign-web && systemctl is-active campaign-web'",
    })])
    b.result("call_share_009", "terminal", {"output": "active\n", "exit_code": 0, "error": None}, 4.2)

    # 10: the post-deploy quality gate
    b.assistant_calls([("call_share_010", "terminal", {
        "command": "npx --yes lighthouse@12 https://staging.example.com --quiet --output=json",
    })])
    b.result("call_share_010", "terminal", {
        "output": "performance 96 · LCP 1.4s · CLS 0.02 · TBT 80ms\n",
        "exit_code": 0, "error": None,
    }, 18.6)

    # 11: the hand-off note carries the client's contact details
    b.assistant_calls([("call_share_011", "kanban_comment", {
        "task_id": "t_share0001",
        "body": "Staging deploy verified. Client sign-off confirmed by "
                + LITERALS["email"] + "; escalation line " + LITERALS["phone"]
                + ". Do not promote to production without a second approval.",
    })])
    b.result("call_share_011", "kanban_comment",
             {"ok": True, "comment_id": "c_share01", "task_id": "t_share0001"}, 0.2)

    # 12: the acceptance run
    b.assistant_calls([("call_share_012", "terminal", {
        "command": "python -m pytest -q tests/test_landing.py",
    })])
    b.result("call_share_012", "terminal", {
        "output": "12 passed in 4.31s\n", "exit_code": 0, "error": None,
    }, 4.6)

    b.assistant_text(
        "Staging is live and green: 12/12 landing tests pass, the deploy API answered 200 on the "
        "retry, and campaign-web is active on the staging host. The staging credentials were "
        "rotated; the values this session touched are not in the shared copy. Production "
        "promotion for Acme Industries is still gated on the client's second approval — I did "
        "not cut over."
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
        ("claude-sonnet-4.6", "anthropic", "https://api.anthropic.com", 12,
         142_180, 18_204, 96_120, 0, 2_640, 0.612480, "estimated", "official_docs_snapshot"),
        ("gpt-5.2-codex", "openai", "https://api.openai.com/v1", 1,
         12_040, 1_980, 0, 0, 0, 0.061220, "estimated", "official_docs_snapshot"),
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
         total_cread, 0, total_reason, "C:/Users/demo/Projects/campaign",
         "feat/spring-campaign", "C:/Users/demo/Projects/campaign",
         "anthropic", "https://api.anthropic.com", total_cost, 0.0, "estimated",
         "official_docs_snapshot", "2026.11.1", TITLE, ended,
         sum(u[3] for u in usage), "default", "cli", "llm"),
    )

    conn.commit()
    conn.close()
    return path


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "fixtures" / "share-state.db"
    path = build(out)
    print(f"synthetic share store → {path}")
    print(f"  session {SESSION_ID} · {TITLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
