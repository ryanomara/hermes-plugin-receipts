"""Prove the client theme's contract against a real manifest.

Checks, in one run:

* the two renders differ only in the <body> class and the stylesheet;
* the instrument stylesheet is exactly ``render._CSS`` (untouched);
* the client stylesheet is exactly ``client_sheet(_CSS)`` — same layout, console
  surface removed, ``CLIENT_CSS`` appended;
* the client document contains no sweep keyframes, no scanline gradient and no
  animation;
* the print, paper, serif/sans and link rules are present;
* an unknown theme is rejected rather than silently rendered as instrument.

Usage::

    python docs/evidence/client-theme/verify_theme.py <state.db> \
        docs/evidence/client-theme/theme-check.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import receipts.render as render  # noqa: E402
from receipts.manifest import build_manifest  # noqa: E402
from receipts.theme_client import CLIENT_CSS, client_sheet  # noqa: E402

HEAD = re.compile(r"<style>(?P<style>.*?)</style></head><body(?P<body>[^>]*)>", re.S)


def parts(doc: str):
    match = HEAD.search(doc)
    if not match:
        raise AssertionError("document shell not found")
    shell = doc[:match.start()] + doc[match.end():]
    return match.group("style"), match.group("body"), shell


def main(argv: list[str]) -> int:
    db_path = Path(argv[1])
    out_path = Path(argv[2]) if len(argv) > 2 else Path("theme-check.json")

    manifest = build_manifest("latest", db_path=db_path)
    render._now_ts = lambda: 1793900000.0  # one clock reading for both renders

    instrument = render.render_html(manifest)
    client = render.render_html(manifest, theme="client")
    i_style, i_body, i_shell = parts(instrument)
    c_style, c_body, c_shell = parts(client)

    checks = {
        "same_body_content": i_shell == c_shell,
        "body_class_only": (i_body, c_body) == ("", ' class="theme-client"'),
        "instrument_style_is_render_css": i_style == render._CSS,
        "client_style_is_client_sheet": c_style == client_sheet(render._CSS),
        "client_css_appended_last": c_style.endswith(CLIENT_CSS),
        "client_has_no_sweep_keyframes": "@keyframes sweep" not in c_style,
        "client_has_no_sweep_animation": "animation:sweep" not in c_style,
        "client_has_no_scanline_gradient": "repeating-linear-gradient" not in c_style,
        "instrument_keeps_sweep": "@keyframes sweep" in i_style,
        "paper_background": "#fbfaf7" in c_style,
        "serif_headings": "body.theme-client h2.title{font-family:var(--serif)" in c_style,
        "system_sans_body": "system-ui" in c_style and "font-family:var(--sans)" in c_style,
        "code_stays_monospace": "body.theme-client .mono,body.theme-client code" in c_style,
        "softer_border_tokens": "--line:#e3e0d7" in c_style and "--line2:#d1cdc2" in c_style,
        "link_underline": "body.theme-client a{color:var(--cyan);text-decoration:none;" in c_style,
        "print_media": "@media print{" in c_style,
        "print_white_background": "body.theme-client{background:#fff;color:#111" in c_style,
        "print_no_page_padding": "body.theme-client .wrap{max-width:none;margin:0;padding:0}" in c_style,
        "print_break_inside_cards": "break-inside:avoid;page-break-inside:avoid" in c_style,
        "print_page_margin": "@page{margin:14mm 13mm}" in c_style,
    }

    unknown_rejected = False
    try:
        render.render_html(manifest, theme="broadsheet")
    except ValueError:
        unknown_rejected = True
    checks["unknown_theme_rejected"] = unknown_rejected

    # 1.2-b's cross-card finding: the redaction band had no instrument styling.
    # The base rule is appended only when a band exists, so a non-redacted
    # instrument receipt keeps its exact v1.1.0 bytes.
    band_manifest = dict(manifest)
    band_manifest["redaction"] = {"total": 3, "classes": {"secret": 3}}
    band_instrument = render.render_html(band_manifest)
    checks["instrument_no_band_css_without_band"] = ".redact-band{" not in i_style
    checks["instrument_band_styled_when_present"] = ".redact-band{" in parts(band_instrument)[0]
    checks["band_markup_in_both_themes"] = (
        'class="redact-band"' in band_instrument
        and 'class="redact-band"' in render.render_html(band_manifest, theme="client")
    )

    rejected = sorted(name for name, ok in checks.items() if not ok)
    report = {
        "store": str(db_path),
        "nodes": len(manifest["nodes"]),
        "tool_calls": manifest["aggregates"]["tool_calls"],
        "instrument_bytes": len(instrument.encode("utf-8")),
        "client_bytes": len(client.encode("utf-8")),
        "stylesheet_delta_bytes": len(c_style.encode("utf-8")) - len(i_style.encode("utf-8")),
        "checks": checks,
        "failed": rejected,
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
