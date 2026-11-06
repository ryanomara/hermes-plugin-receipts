"""Check the printed client receipt: every row present, no card split across pages.

Runs against the PDF produced by Chrome's print path (the same @media print rules
a user gets from File > Print > Save as PDF) and the store the receipt came from.

Usage::

    uv run --no-project --with pypdfium2 python \
        docs/evidence/client-theme/print_check.py \
        docs/evidence/client-theme/receipt-120-client.pdf \
        <print-120.db> \
        docs/evidence/client-theme

Writes print-check.json and page PNGs beside the PDF. Exits non-zero if a row is
missing or a row's arguments landed on a different page than its number.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from receipts.manifest import build_manifest  # noqa: E402


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def idx_pattern(number: int) -> re.Pattern:
    # "#10" must not match inside "#100"
    return re.compile(rf"#{number:02d}(?![0-9])")


def main(argv: list[str]) -> int:
    pdf_path = Path(argv[1])
    db_path = Path(argv[2])
    out_dir = Path(argv[3]) if len(argv) > 3 else pdf_path.parent

    manifest = build_manifest("latest", db_path=db_path)
    doc = pdfium.PdfDocument(pdf_path)
    pages = [norm(doc[i].get_textpage().get_text_range()) for i in range(len(doc))]
    width, height = doc[0].get_size()

    rows = []
    for node in manifest["nodes"]:
        number = node["index"]
        pattern = idx_pattern(number)
        args = norm(node.get("args_summary") or node.get("tool") or "")
        # Keep a distinctive slice: paths/commands are long, and the PDF may wrap.
        probe = args[:90]
        pages_idx = [i for i, text in enumerate(pages) if pattern.search(text)]
        pages_probe = [i for i, text in enumerate(pages) if probe and probe in text]
        same = [i for i in pages_idx if i in pages_probe]
        rows.append({
            "index": number,
            "tool": node["tool"],
            "status": node["status"],
            "idx_pages": pages_idx,
            "probe_pages": pages_probe,
            "start_page": (same[0] + 1) if same else None,
            "ok": bool(same),
        })

    missing = [r for r in rows if not r["ok"]]
    starts = [r["start_page"] for r in rows if r["start_page"]]
    # A page break between cards: one node starts on the page after another.
    breaks = []
    for prev, cur in zip(rows, rows[1:]):
        if prev["start_page"] and cur["start_page"] and cur["start_page"] > prev["start_page"]:
            breaks.append(prev["start_page"])

    # The work-flow summary groups consecutive same-tool calls. A flex container
    # cannot fragment, so it must fit one page complete: verify every group and
    # the legend are actually in the PDF, not clipped away.
    flow = manifest["aggregates"]["flow"]
    flow_pages = [i for i, text in enumerate(pages)
                  if "work flow" in text or "grouped" in text]
    flow_groups = 0
    flow_calls = 0
    for i in flow_pages:
        for label in re.findall(r"(?:1 call|×\d+)\s*·", pages[i]):
            flow_groups += 1
            flow_calls += 1 if label.startswith("1 call") else int(re.search(r"×(\d+)", label).group(1))
    flow_legend = any("consecutive calls to the same tool are grouped" in pages[i] for i in flow_pages)

    report = {
        "pdf": pdf_path.name,
        "pdf_bytes": pdf_path.stat().st_size,
        "pages": len(pages),
        "page_size_pt": [round(width, 1), round(height, 1)],
        "nodes": len(rows),
        "nodes_with_row_and_args_on_same_page": sum(1 for r in rows if r["ok"]),
        "missing_or_split": missing,
        "first_node_page": starts[0] if starts else None,
        "last_node_page": rows[-1]["start_page"],
        "page_break_after_node_pages": breaks,
        "page_text_chars": {"min": min(len(p) for p in pages), "max": max(len(p) for p in pages)},
        "flow_pages": [i + 1 for i in flow_pages],
        "flow_groups_expected": len(flow),
        "flow_groups_in_pdf": flow_groups,
        "flow_calls_expected": manifest["aggregates"]["tool_calls"],
        "flow_calls_in_pdf": flow_calls,
        "flow_legend_present": flow_legend,
    }
    report_path = out_dir / "print-check.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))

    # Spot-check PNGs: the first page, a mid-document page break, the last page.
    picks = [(1, 0)]
    if breaks:
        picks.append((breaks[len(breaks) // 2], breaks[len(breaks) // 2] - 1))
    picks.append((len(pages), len(pages) - 1))
    for label, zero_index in picks:
        page = doc[zero_index]
        bitmap = page.render(scale=1.6)
        image = bitmap.to_pil()
        name = f"page-{label:02d}.png" if label in (1, len(pages)) else f"page-break-{label:02d}.png"
        image.save(out_dir / name)
        print("wrote", out_dir / name)

    return 0 if (not missing and flow_groups == len(flow) and flow_legend) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
