"""Render the 1.2 share-safe samples from the synthetic share store.

Produces, from one manifest at one instant:

* ``samples/receipt-original-instrument.html``  — the before: unredacted instrument receipt
* ``samples/receipt-redacted-instrument.html``  — the after, same theme (diff is the proof)
* ``samples/receipt-redacted-instrument.redaction.json``
* ``samples/receipt-redacted-client.html``      — the share artefact (paper document)
* ``samples/receipt-redacted-client.redaction.json``

and ``docs/evidence/1.2/samples-check.json`` recording the checks below.

The claim this script has to earn is narrow and testable: **the redacted
document is the original document with placeholders substituted, plus the
band**. So it does not merely eyeball the files — it re-runs the redaction
engine over the *rendered original* and requires the result to be byte-equal to
the rendered redacted document once the band and its stylesheet rule are
removed (placeholder numbering normalised, because the engine numbers manifest
fields and the document prints a subset of them).

Usage::

    python docs/evidence/1.2/make_samples.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import receipts.render as render  # noqa: E402
from receipts import privacy  # noqa: E402
from receipts.manifest import build_manifest  # noqa: E402

from make_share_fixture import LITERALS, build as build_store  # noqa: E402

BAND_RE = re.compile(r'<div class="redact-band">.*?</div>', re.S)
NUMBERED_RE = re.compile(r"\[(secret|email|phone|ip|word)-\d+\]")
PLACEHOLDER_RE = re.compile(r"\[(?:secret|email|phone|ip|word)-\d+\]|\[user\]")
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")

WORDS_FILE = HERE / "redact-words.txt"
STORE_REL = "fixtures/share-state.db"
STORE = ROOT / STORE_REL
SAMPLES = ROOT / "samples"

# The instant these samples were first rendered. Pinned on purpose: three
# timestamps reach the documents (the render clock, the manifest's
# ``generated_at`` and the store's mtime, which the provenance block prints), so
# an unpinned run rewrites all three and a re-run never reproduces the committed
# bytes. Pinning them makes "regenerate the evidence" a no-op diff. The clock is
# formatted in local time, so a machine in another timezone will still print a
# different clock text; everything else is timezone-independent.
PINNED_CLOCK_ISO = "2026-11-07T01:29:59+01:00"
PINNED_CLOCK = int(datetime.fromisoformat(PINNED_CLOCK_ISO).timestamp())

ARTIFACTS = {
    "original": SAMPLES / "receipt-original-instrument.html",
    "redacted_instrument": SAMPLES / "receipt-redacted-instrument.html",
    "redacted_client": SAMPLES / "receipt-redacted-client.html",
}
SIDECARS = {
    "redacted_instrument": SAMPLES / "receipt-redacted-instrument.redaction.json",
    "redacted_client": SAMPLES / "receipt-redacted-client.redaction.json",
}
CHECK_JSON = HERE / "samples-check.json"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise(text: str) -> str:
    """Collapse placeholder numbering so numbering schemes are comparable."""
    return NUMBERED_RE.sub(r"[\1]", text)


def placeholder_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in PLACEHOLDER_RE.findall(text):
        cls = token[1:-1].split("-")[0].rstrip("]")
        counts[cls] = counts.get(cls, 0) + 1
    return counts


def strip_band(html: str, theme: str) -> tuple[str, str]:
    match = BAND_RE.search(html)
    assert match, "expected a redaction band in the redacted document"
    band = match.group(0)
    stripped = html.replace(band, "", 1)
    if theme == "instrument":
        # The band's own rule is appended only when a band exists, so a redacted
        # instrument sheet carries one CSS block the original does not.
        stripped = stripped.replace(render._REDACT_BAND_CSS, "", 1)
    return stripped, band


def diff_proof(original_html: str, redacted_html: str, words: list[str], theme: str) -> dict:
    """Prove the document diff is exactly placeholders + band."""
    stripped, band = strip_band(redacted_html, theme)
    rewritten, _report = privacy.redact_text(original_html, words)
    return {
        "band": band,
        "equal": normalise(rewritten) == normalise(stripped),
        "original_bytes": len(original_html.encode("utf-8")),
        "redacted_bytes": len(redacted_html.encode("utf-8")),
        "band_bytes": len(band.encode("utf-8")),
        "css_bytes_removed": (
            len(render._REDACT_BAND_CSS.encode("utf-8")) if theme == "instrument" else 0
        ),
        "placeholders_visible": placeholder_counts(stripped),
    }


def main() -> int:
    # The CLI is run from the repo root, and the store path it records in the
    # document is the one it was given. Build the manifest from the same
    # relative path so the samples are byte-identical to a CLI run a reader can
    # reproduce — and so no absolute home path is baked into the original.
    os.chdir(ROOT)
    words = privacy.load_words(WORDS_FILE)
    assert words, "word list is empty"

    store = build_store(STORE)
    clock = PINNED_CLOCK  # pinned: see PINNED_CLOCK_ISO
    render._now_ts = lambda: float(clock)  # noqa: E731 - deterministic across the trio
    # The provenance block prints the store's mtime, and build_store just
    # rewrote the file, so pin that reading as well.
    os.utime(STORE, (clock, clock))

    manifest = build_manifest("latest", db_path=STORE_REL)
    manifest["generated_at"] = PINNED_CLOCK_ISO  # build_manifest read the real clock
    manifest_path = STORE_REL

    original = {
        "instrument": render.render_html(manifest),
        "client": render.render_html(manifest, theme="client"),
    }

    redacted_manifest, report = privacy.redact_manifest(manifest, words)
    report["generated_at"] = manifest.get("generated_at")
    redacted_manifest["redaction"] = report

    redacted = {
        "instrument": render.render_html(redacted_manifest),
        "client": render.render_html(redacted_manifest, theme="client"),
    }
    sidecar_text = privacy.canonical_json(report) + "\n"

    # ---- write the artifacts ------------------------------------------------
    write_text(ARTIFACTS["original"], original["instrument"])
    write_text(ARTIFACTS["redacted_instrument"], redacted["instrument"])
    write_text(ARTIFACTS["redacted_client"], redacted["client"])
    write_text(SIDECARS["redacted_instrument"], sidecar_text)
    write_text(SIDECARS["redacted_client"], sidecar_text)

    # ---- checks -------------------------------------------------------------
    literal_values = list(LITERALS.values()) + words
    survivors = {
        name: [lit for lit in literal_values if lit in text]
        for name, text in (
            ("redacted_instrument", redacted["instrument"]),
            ("redacted_client", redacted["client"]),
            ("sidecar", sidecar_text),
        )
    }
    assert not any(survivors.values()), f"a literal survived redaction: {survivors}"

    # Some planted values never reach the document: the manifest masks
    # credential-shaped strings (PEM, sk-, ghp_, AKIA, xox…, bearer/secret
    # assignments) when it is built. Record which values the redaction pass is
    # actually responsible for, so the evidence cannot overclaim.
    seeded = {
        name: {
            "reached_document": value in original["instrument"],
            "in_redacted_document": value in redacted["instrument"],
        }
        for name, value in LITERALS.items()
    }
    assert not any(v["in_redacted_document"] for v in seeded.values()), "seed survived"
    assert sum(v["reached_document"] for v in seeded.values()) >= 6, (
        "too few planted values reached the document to prove anything"
    )

    assert report["total"] == sum(report["classes"].values()), "report arithmetic"
    for theme, doc in redacted.items():
        band = BAND_RE.search(doc)
        assert band, f"{theme}: no band"
        assert f"{report['total']} item(s) removed" in band.group(0), f"{theme}: band count"
        for cls, count in report["classes"].items():
            assert f"{cls} ×{count}" in band.group(0), f"{theme}: band missing {cls}"

    instrument_proof = diff_proof(original["instrument"], redacted["instrument"], words, "instrument")
    client_proof = diff_proof(original["client"], redacted["client"], words, "client")
    assert instrument_proof["equal"], "instrument diff is not only placeholders + band"
    assert client_proof["equal"], "client diff is not only placeholders + band"

    # The CLI is the shipped surface; the samples must be what it writes. Compare
    # a CLI render of the same store with the committed file, masking the two
    # legitimate clock readings (build time and render time).
    import subprocess

    cli_out = HERE / "_cli-check.html"
    cli_sidecar = cli_out.with_suffix(".redaction.json")
    subprocess.run(
        [sys.executable, "-m", "receipts", "latest", "--db", STORE_REL,
         "--redact", "--redact-words", str(WORDS_FILE), "--theme", "client",
         "-o", str(cli_out)],
        cwd=str(ROOT), check=True, capture_output=True, text=True,
    )
    cli_html = cli_out.read_text(encoding="utf-8")
    cli_report = json.loads(cli_sidecar.read_text(encoding="utf-8"))
    cli_out.unlink()
    cli_sidecar.unlink()
    mask = lambda text: ISO_RE.sub("<generated>", re.sub(
        r"rendered <b>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}</b>", "rendered <b><clock></b>", text))
    cli_matches_sample = mask(cli_html) == mask(redacted["client"])
    cli_report.pop("generated_at", None)
    script_report = json.loads(sidecar_text)
    script_report.pop("generated_at", None)
    cli_report_matches = cli_report == script_report

    check = {
        "generated_at": manifest.get("generated_at"),
        "render_clock": clock,
        "store": manifest_path,
        "word_list": str(WORDS_FILE.relative_to(ROOT)).replace("\\", "/"),
        "words_loaded": len(words),
        "session": {
            "id": manifest["sessions"][0]["id"],
            "title": manifest["sessions"][0]["title"],
            "tool_calls": manifest["aggregates"]["tool_calls"],
            "failures": manifest["aggregates"]["failures"],
            "retries": manifest["aggregates"]["retries"],
        },
        "redaction": report,
        "documents": {
            "original_instrument": {
                "path": str(ARTIFACTS["original"].relative_to(ROOT)).replace("\\", "/"),
                "bytes": len(original["instrument"].encode("utf-8")),
                "sha256": sha256(original["instrument"]),
            },
            "redacted_instrument": {
                "path": str(ARTIFACTS["redacted_instrument"].relative_to(ROOT)).replace("\\", "/"),
                "bytes": len(redacted["instrument"].encode("utf-8")),
                "sha256": sha256(redacted["instrument"]),
            },
            "redacted_client": {
                "path": str(ARTIFACTS["redacted_client"].relative_to(ROOT)).replace("\\", "/"),
                "bytes": len(redacted["client"].encode("utf-8")),
                "sha256": sha256(redacted["client"]),
            },
        },
        "checks": {
            "no_literal_survives": True,
            "seeded_values": seeded,
            "report_arithmetic": True,
            "band_names_every_class": True,
            "instrument_diff_is_placeholders_and_band_only": instrument_proof,
            "client_diff_is_placeholders_and_band_only": client_proof,
            "cli_render_matches_sample": cli_matches_sample,
            "cli_sidecar_matches_sample": cli_report_matches,
            "sidecar_matches_instrument_report": json.loads(sidecar_text) == report,
            "author_home_absent": Path.home().name not in "".join(
                [original["instrument"], redacted["instrument"], redacted["client"], sidecar_text]
            ),
        },
        "document_placeholders_vs_manifest_fields": {
            "manifest_fields_removed": report["total"],
            "visible_in_instrument_document": sum(instrument_proof["placeholders_visible"].values()),
            "note": (
                "The sidecar and band count manifest fields. The manifest stores each call twice "
                "(timeline event + flow node) and its arguments twice (summary + detail), so the "
                "document prints fewer placeholders than the report counts. Both numbers are "
                "reported here so the sample cannot be read as claiming one equals the other."
            ),
        },
    }
    check["all_passed"] = (
        check["checks"]["no_literal_survives"]
        and instrument_proof["equal"] and client_proof["equal"]
        and cli_matches_sample and cli_report_matches
        and check["checks"]["sidecar_matches_instrument_report"]
        and check["checks"]["author_home_absent"]
    )
    assert cli_matches_sample, "the CLI render differs from the committed sample"
    assert cli_report_matches, "the CLI sidecar differs from the committed sidecar"
    assert check["checks"]["author_home_absent"], "a real home path reached the samples"
    write_text(CHECK_JSON, json.dumps(check, indent=2, ensure_ascii=False) + "\n")

    print(f"original  → {ARTIFACTS['original']} ({check['documents']['original_instrument']['bytes']:,} B)")
    print(f"redacted  → {ARTIFACTS['redacted_instrument']} ({check['documents']['redacted_instrument']['bytes']:,} B)")
    print(f"share     → {ARTIFACTS['redacted_client']} ({check['documents']['redacted_client']['bytes']:,} B)")
    print(f"  removed {report['total']} field(s): "
          + ", ".join(f"{k} ×{v}" for k, v in report["classes"].items()))
    print(f"  diff proof: instrument={instrument_proof['equal']} client={client_proof['equal']} "
          f"cli-sample={cli_matches_sample}")
    print(f"  check    → {CHECK_JSON}")
    return 0 if check["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
