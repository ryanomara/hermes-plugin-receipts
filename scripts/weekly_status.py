"""Weekly maintainer-status updater for hermes-plugin-receipts.

Run: python scripts/weekly_status.py  (from the repo root, stdlib-only)

1. Runs the test suite on Windows (this machine) and on Lenovo (clean clone).
2. Pulls repo stats (stars, clones 14d, open issues) via the GitHub REST API.
3. Creates (first run) or silently EDITS a pinned "Maintainer status" issue —
   edits send no notifications, so watchers are not spammed.
4. Writes docs/status/latest.json for the gazette's RECEIPTS pulse section.

The issue never lies: if a suite could not run, the table says so instead of
showing a stale green.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ISSUE_TITLE = "Maintainer status — checked weekly"
LENOVO = ("ssh", "-i", str(Path.home() / ".ssh" / "storybox_pi"),
          "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
          "snakkrz@192.168.2.147")
REPO_URL = "https://github.com/ryanomara/hermes-plugin-receipts"
API_BASE = "https://api.github.com/repos/ryanomara/hermes-plugin-receipts"


def _token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=20).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise RuntimeError("no github credential")


def _api(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    import urllib.request

    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "hermes-weekly-status",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _windows_suite() -> str:
    """Returns 'N/N green' or an honest failure line."""
    try:
        run = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=REPO, capture_output=True, text=True, timeout=300)
        out = run.stdout or run.stderr
        for line in out.splitlines():
            if line.startswith("Ran "):
                counts = line.strip()
                tail = out.strip().splitlines()
                verdict = tail[-1] if tail else "?"
                return f"{counts} · {verdict}"
        return f"suite ran (exit {run.returncode}) but no summary"
    except Exception as exc:
        return f"NOT RUN — {exc}"


def _linux_suite() -> str:
    """Clean-clone suite on Lenovo; never trusts a warm checkout."""
    try:
        remote = (
            "rm -rf /tmp/receipts-status && git clone -q --depth 1 " + REPO_URL +
            " /tmp/receipts-status && cd /tmp/receipts-status && "
            "find . -name __pycache__ -type d -exec rm -rf {} + ; "
            "python3 -m unittest discover -s tests 2>&1")
        run = subprocess.run(
            LENOVO + (remote,), capture_output=True, text=True, timeout=400)
        out = run.stdout or run.stderr
        for line in out.splitlines():
            if line.startswith("Ran "):
                counts = line.strip()
                tail = [l for l in out.strip().splitlines() if l][-1]
                return f"{counts} · {tail}"
        return f"suite ran (exit {run.returncode}) but no summary: {out[-200:]}"
    except Exception as exc:
        return f"NOT RUN — {exc}"


def _platform() -> str:
    import platform

    return f"Python {platform.python_version()}"


def main() -> int:
    token = _token()
    win = _windows_suite()
    lin = _linux_suite()
    repo = _api("GET", API_BASE, token)
    clones = _api("GET", API_BASE + "/traffic/clones", token)
    issues = _api("GET", API_BASE + "/issues?state=open&per_page=100", token)
    real_issues = [i for i in issues if "pull_request" not in i]
    now = datetime.now(timezone.utc)

    # resolve or create the status issue
    state_file = REPO / "docs" / "status" / "state.json"
    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
    number = state.get("issue")
    if number:
        try:
            _api("GET", f"{API_BASE}/issues/{number}", token)
        except Exception:
            number = None  # issue gone (deleted) — recreate
    if not number:
        created = _api("POST", f"{API_BASE}/issues",
                       token, {"title": ISSUE_TITLE, "body": "(initial — first weekly check pending)"})
        number = created["number"]

    oldest = None
    for i in real_issues:
        age_days = (now - datetime.fromisoformat(i["created_at"].replace("Z", "+00:00"))).days
        oldest = age_days if oldest is None else max(oldest, age_days)
    issue_note = (f"{len(real_issues)} open · oldest {oldest}d unanswered"
                  if real_issues else "0 open — queue clear")

    version = "?"
    for line in (REPO / "receipts" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            version = line.split("=", 1)[1].strip().strip('"')

    body = f"""# Maintainer status — checked weekly

**Last checked:** {now:%Y-%m-%d %H:%M} UTC · platform: Windows 11 ({_platform()})

| Platform | Test suite |
|---|---|
| Windows 11 | {win} |
| Linux (Lenovo, clean clone) | {lin} |

- Catalog version: **{version}** · merged into NousResearch/hermes-agent main (PR #152953)
- Issues: {issue_note}
- Adoption (14d): {clones.get('count', 0)} clones / {clones.get('uniques', 0)} uniques · {repo.get('stargazers_count', 0)} stars

*Updated every Sunday by the maintainer's status cron — silently, so watchers are
not emailed. If the date above is more than 9 days old, something failed: open an
issue or assume the maintainer is away.*"""

    _api("PATCH", f"{API_BASE}/issues/{number}",
         token, {"body": body})

    state = {"issue": number, "updated": now.isoformat(),
             "windows": win, "linux": lin, "url": f"{REPO_URL}/issues/{number}"}
    (REPO / "docs" / "status").mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"issue #{number} updated: {state['url']}")
    print(f"windows: {win}")
    print(f"linux:   {lin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
