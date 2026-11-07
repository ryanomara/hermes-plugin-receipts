"""Acceptance tests for the client theme and the frozen instrument output.

Two contracts are pinned here (RECEIPTS-1.2 card 1.2-c):

* ``render_html(manifest)`` is still the v1.1.0 instrument document, byte for
  byte — the theme work must not move a single byte of it;
* ``render_html(manifest, theme="client")`` is the same document with the
  console surface removed and the paper rules appended: same body, same data,
  a still page.

The byte pin is the instrument document rendered from the demo fixture with the
masthead clock fixed.  It was captured by rendering the same manifest with the
``d541317`` (v1.1.0) renderer and comparing the two documents byte for byte;
the version token is normalised out of the hash so a release bump does not make
the pin lie about the layout.

To regenerate the pin deliberately::

    git archive d541317 | tar -x -C <tmp>
    # render <manifest> with <tmp>/receipts/render.py and with the current one
    # with receipts.render._now_ts patched to FIXED_CLOCK; the two documents
    # must be equal before the version token is normalised.
"""

from __future__ import annotations

import copy
import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "fixtures"))
from make_demo_db import BASE, build as build_fixture  # noqa: E402

import receipts.render as render  # noqa: E402
from receipts.manifest import build_manifest  # noqa: E402
from receipts.theme_client import CLIENT_CSS, CONSOLE_SURFACE, client_sheet  # noqa: E402

#: One clock reading for every render, so the masthead cannot move the bytes.
FIXED_CLOCK = 1793900000.0
FIXED_GENERATED_AT = "2026-11-07T00:00:00+01:00"

#: sha256 of the instrument document, version token normalised, at d541317.
PINNED_INSTRUMENT_SHA256 = "9dfc210ed4dd50a46abcee851e4f6dfff6da3d5f942692dce50e40d917b2e7e1"
PINNED_INSTRUMENT_BYTES = 39_450

#: ``<style>…</style></head><body …>`` — the only part the theme may change.
HEAD = re.compile(r"<style>(?P<style>.*?)</style></head><body(?P<body>[^>]*)>", re.S)
VERSION_TOKEN = re.compile(r"receipts \d+\.\d+\.\d+")


def split_document(doc: str) -> tuple[str, str, str]:
    """Return (stylesheet, body attributes, everything else)."""
    match = HEAD.search(doc)
    if not match:
        raise AssertionError("document shell not found")
    shell = doc[: match.start()] + doc[match.end():]
    return match.group("style"), match.group("body"), shell


def pinned(document: str) -> bytes:
    """Bytes used for the byte-stability pin: the version token normalised out.

    The footer carries ``receipts 1.1.0``; a release bump legitimately changes
    that one token.  Everything else must stay put.
    """
    return VERSION_TOKEN.sub("receipts <version>", document).encode("utf-8")


def stable_manifest(db: Path) -> dict:
    """A manifest whose every input is fixed, so its render is reproducible.

    ``generated_at`` is the build clock and ``source`` carries the temp store's
    path, size and mtime — all three would otherwise vary per machine and per
    run without saying anything about the renderer.
    """
    manifest = build_manifest("latest", db_path=db)
    manifest["generated_at"] = FIXED_GENERATED_AT
    manifest["source"] = {
        "path": "fixtures/demo-state.db",
        "exists": True,
        "size_bytes": 307200,
        "mtime": BASE,
    }
    return manifest


class ThemeFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "state.db"
        build_fixture(cls.db)
        cls.manifest = stable_manifest(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            self.instrument = render.render_html(self.manifest)
            self.client = render.render_html(self.manifest, theme="client")
        self.i_style, self.i_body, self.i_shell = split_document(self.instrument)
        self.c_style, self.c_body, self.c_shell = split_document(self.client)

    def band_manifest(self, total: int = 3) -> dict:
        manifest = copy.deepcopy(self.manifest)
        manifest["redaction"] = {"total": total, "classes": {"secret": total} if total else {}}
        return manifest


class TestClientSurface(ThemeFixture):
    def test_client_body_carries_the_theme_class(self):
        self.assertEqual(self.i_body, "")
        self.assertEqual(self.c_body, ' class="theme-client"')
        self.assertIn('<body class="theme-client">', self.client)
        self.assertIn("<body><div", self.instrument)

    def test_client_document_has_no_console_surface(self):
        for token in ("@keyframes sweep", "animation:sweep", "repeating-linear-gradient",
                      "radial-gradient"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.c_style)
        self.assertNotIn(render._CSS, self.client)

    def test_client_document_keeps_the_paper_document_rules(self):
        for token in (
            "#fbfaf7",                                   # paper background
            "font-family:var(--serif)",                  # serif headings
            "font-family:var(--sans)",                   # sans body
            "body.theme-client .mono,body.theme-client code",  # code stays mono
            "@media print{",                             # print sheet
            "@page{margin:14mm 13mm}",                   # print margins
            "break-inside:avoid;page-break-inside:avoid",  # cards not cut in half
            "body.theme-client a{",                      # document links
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.c_style)

    def test_client_document_is_offline_and_self_contained(self):
        self.assertIsNone(re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', self.client))
        self.assertNotIn("<link", self.client)
        self.assertNotIn("@import", self.client)
        self.assertEqual(self.client.lower().count("<html"), 1)
        self.assertTrue(self.client.startswith("<!doctype html>"))

    def test_client_keeps_every_node_and_failure(self):
        rendered = re.findall(r'<div class="node ([a-z]+)" data-node', self.client)
        self.assertEqual(len(rendered), 18)
        self.assertEqual(rendered.count("failed"), 4)
        self.assertEqual(re.findall(r'<div class="node ([a-z]+)" data-node', self.instrument),
                         rendered)


class TestStylesheetContract(ThemeFixture):
    def test_instrument_stylesheet_is_the_frozen_sheet(self):
        self.assertEqual(self.i_style, render._CSS)

    def test_client_stylesheet_is_instrument_minus_console_plus_client(self):
        self.assertEqual(self.c_style, client_sheet(render._CSS))
        self.assertTrue(self.c_style.endswith(CLIENT_CSS))
        self.assertNotEqual(self.c_style, render._CSS + CLIENT_CSS)
        for block in CONSOLE_SURFACE:
            with self.subTest(block=block.splitlines()[0]):
                self.assertNotIn(block, self.c_style)

    def test_themes_change_only_the_stylesheet_and_the_body_attribute(self):
        self.assertEqual(self.i_shell, self.c_shell,
                         "the two themes must render the same body")
        self.assertEqual((self.i_body, self.c_body), ("", ' class="theme-client"'))
        self.assertNotEqual(self.i_style, self.c_style)

    def test_unknown_theme_is_rejected_rather_than_falling_back(self):
        for bad in ("console", "CLIENT", "", None, "client "):
            with self.subTest(theme=bad):
                with self.assertRaises(ValueError) as ctx:
                    render.render_html(self.manifest, theme=bad)
                self.assertIn(repr(bad), str(ctx.exception))

    def test_client_sheet_refuses_a_sheet_without_the_console_surface(self):
        # The screen slices are exact; if the instrument sheet is edited and the
        # client would silently inherit scanlines, this must fail loudly.
        with self.assertRaises(RuntimeError):
            client_sheet("body{background:#07080a}")


class TestInstrumentFreeze(ThemeFixture):
    def test_instrument_document_is_byte_identical_to_v1_1_0(self):
        document = pinned(self.instrument)
        self.assertEqual(len(document), PINNED_INSTRUMENT_BYTES)
        self.assertEqual(hashlib.sha256(document).hexdigest(), PINNED_INSTRUMENT_SHA256)

    def test_instrument_document_is_reproducible_in_process(self):
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            again = render.render_html(copy.deepcopy(self.manifest))
        self.assertEqual(again, self.instrument)

    def test_client_render_does_not_mutate_the_manifest(self):
        before = copy.deepcopy(self.manifest)
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            render.render_html(self.manifest)
            render.render_html(self.manifest, theme="client")
        self.assertEqual(self.manifest, before)

    def test_instrument_keeps_the_console_surface(self):
        # Positive control: the sweep and the scanline texture still belong to
        # the instrument, or the client assertions above would be vacuous.
        self.assertIn("@keyframes sweep", self.i_style)
        self.assertIn("repeating-linear-gradient", self.i_style)


class TestRedactionBand(ThemeFixture):
    def test_band_markup_appears_in_both_themes(self):
        manifest = self.band_manifest(3)
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            instrument = render.render_html(manifest)
            client = render.render_html(manifest, theme="client")
        for doc in (instrument, client):
            with self.subTest(theme="instrument" if doc is instrument else "client"):
                self.assertIn('class="redact-band"', doc)
                self.assertIn("3 item(s) removed", doc)
                self.assertIn("secret ×3", doc)
                self.assertIn("The recorded session is unchanged.", doc)

    def test_instrument_band_rule_is_added_only_when_a_band_exists(self):
        self.assertNotIn(".redact-band{", self.i_style)
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            banded = render.render_html(self.band_manifest(3))
        self.assertIn(".redact-band{", split_document(banded)[0])

    def test_client_always_carries_the_band_rule(self):
        # The client sheet is complete: a redacted client copy is the product,
        # so the rule is present even when this particular document is clean.
        self.assertIn(".redact-band{", self.c_style)

    def test_band_is_absent_when_nothing_was_removed(self):
        manifest = self.band_manifest(0)
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            document = render.render_html(manifest)
        self.assertNotIn('class="redact-band"', document)
        self.assertNotIn(".redact-band{", split_document(document)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
