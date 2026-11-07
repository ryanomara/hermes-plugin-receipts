"""Offline check for the 1.2 samples.

The samples claim three things a browser can settle: they open from ``file://``
with no console output at all, they make no network request (nothing to load),
and they render the same document the source promises (redaction band present,
twelve call nodes, the client sheet on the share copy).

Chrome's headless CLI reports page console messages on stderr as
``INFO:CONSOLE:<line>`` — including uncaught errors — so a run that prints no
such line is a page that logged nothing and threw nothing. The DOM is dumped in
the same pass and checked for the structural markers.

Usage::

    python docs/evidence/1.2/check_offline.py [--chrome PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "offline-check.json"

SAMPLES = {
    "samples/receipt-original-instrument.html": {
        "theme": None,
        "band": False,
        "nodes": 12,
    },
    "samples/receipt-redacted-instrument.html": {
        "theme": None,
        "band": True,
        "nodes": 12,
    },
    "samples/receipt-redacted-client.html": {
        "theme": "theme-client",
        "band": True,
        "nodes": 12,
    },
}

CONSOLE_RE = re.compile(r":CONSOLE:")
EXTERNAL_RE = re.compile(r"""(?:\bsrc\s*=|\bhref\s*=|@import|\burl\(\s*['"]?https?:)""", re.I)


def find_chrome(explicit: str | None) -> str:
    candidates = [
        explicit,
        os.environ.get("CHROME"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chrome"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise SystemExit("no Chrome found; pass --chrome PATH")


def check(chrome: str, rel: str, expect: dict) -> dict:
    path = ROOT / rel
    html = path.read_text(encoding="utf-8")
    proc = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--enable-logging=stderr",
         "--v=0", "--virtual-time-budget=1500", "--dump-dom", path.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    stderr = proc.stderr or ""
    console = [line for line in stderr.splitlines() if CONSOLE_RE.search(line)]
    dom = proc.stdout or ""
    body_class = re.search(r"<body([^>]*)>", dom)
    body_class = (body_class.group(1) if body_class else "")
    result = {
        "path": rel,
        "bytes": path.stat().st_size,
        "exit_code": proc.returncode,
        "console_messages": len(console),
        "console_lines": console[:10],
        "dom_chars": len(dom),
        "band_present": 'class="redact-band"' in dom,
        "nodes": len(re.findall(r'data-node="', dom)),
        "body_class": body_class.strip(),
        "external_references": len(EXTERNAL_RE.findall(html)),
    }
    result["ok"] = (
        result["console_messages"] == 0
        and result["band_present"] is expect["band"]
        and result["nodes"] == expect["nodes"]
        and (expect["theme"] is None) == ('class="theme-client"' not in body_class)
        and result["external_references"] == 0
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome")
    args = parser.parse_args(argv)
    chrome = find_chrome(args.chrome)

    results = [check(chrome, rel, expect) for rel, expect in SAMPLES.items()]
    payload = {
        "chrome": Path(chrome).name,
        "method": (
            "headless Chrome on file:// URLs; page console messages are read from "
            "INFO:CONSOLE stderr lines, the DOM from --dump-dom"
        ),
        "files": results,
        "all_ok": all(r["ok"] for r in results),
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for r in results:
        print(f"{'ok ' if r['ok'] else 'FAIL'} {r['path']}: "
              f"console={r['console_messages']} nodes={r['nodes']} "
              f"band={r['band_present']} external={r['external_references']}")
    print(f"→ {OUT}")
    return 0 if payload["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
