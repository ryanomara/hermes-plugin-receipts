"""Acceptance tests for the agent tool surface (RECEIPTS-1.2 card 1.2-d).

What is pinned here:

* the schema advertises ``theme``, ``redact`` and ``redact_words`` with the
  v1.1.0 defaults (instrument, no redaction);
* a default call still writes the instrument document, byte for byte, and no
  sidecar;
* ``theme="client"`` reaches the file;
* ``redact=True`` writes a *copy* plus a ``.redaction.json`` sidecar, leaves the
  plain receipt untouched and reports what was removed;
* ``redact_words`` accepts the agent-native list and the CLI-compatible path;
* every error path returns ``{"ok": false, "error": ...}`` and writes nothing.

The tool reads the active store through ``build_manifest``; the tests patch
that one call with a real fixture manifest, so no test touches the network or
the real Hermes home.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fixtures"))

from make_demo_db import build as build_fixture  # noqa: E402

import receipts.plugin as plugin  # noqa: E402
import receipts.render as render  # noqa: E402
from receipts.manifest import build_manifest  # noqa: E402

FIXED_CLOCK = 1793900000.0

EMAIL = "client.contact+receipts@example.com"
PHONE = "+49 151 23456789"
IP = "10.42.7.19"
USER_PATH = r"C:\Users\ryoma\Projects\client"
WORD = "Acme Industries"
URL_PASSWORD = "pa55-c0rrect-horse"
URL_CREDS = "https://svc:%s@api.example.com/v1" % URL_PASSWORD

SEEDED_LITERALS = [EMAIL, PHONE, IP, USER_PATH, WORD, URL_PASSWORD]


def seeded(manifest: dict) -> dict:
    """A real fixture manifest with one removable literal of every class."""
    out = copy.deepcopy(manifest)
    out["title"] = "Receipt for " + WORD
    out["asks"][0]["text"] += " mail %s call %s host %s" % (EMAIL, PHONE, IP)
    out["nodes"][1]["args_summary"] = "curl " + URL_CREDS
    out["sessions"][0]["cwd"] = USER_PATH
    return out


class ToolFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        db = cls.root / "state.db"
        build_fixture(db)
        cls.manifest = build_manifest("latest", db_path=db)
        cls.short = cls.manifest["sessions"][0]["short_id"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        # A fresh working directory per test: the default output lands in cwd.
        self.work = Path(tempfile.mkdtemp(dir=self.root, prefix="case-"))
        previous = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, previous)

    def call(self, manifest: dict | None = None, **args) -> dict:
        payload = copy.deepcopy(self.manifest if manifest is None else manifest)
        with mock.patch.object(plugin, "build_manifest", return_value=payload):
            with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
                return json.loads(plugin.receipts_build(args))

    def read(self, name: str) -> str:
        return Path(name).read_text(encoding="utf-8")

    def files(self) -> list[str]:
        return sorted(path.name for path in self.work.iterdir())


class TestSchema(ToolFixture):
    def test_schema_lists_the_three_options(self):
        props = plugin.SCHEMA["function"]["parameters"]["properties"]
        for name in ("target", "out", "theme", "redact", "redact_words"):
            with self.subTest(name=name):
                self.assertIn(name, props)

    def test_theme_enum_is_restricted_and_defaults_to_instrument(self):
        theme = plugin.SCHEMA["function"]["parameters"]["properties"]["theme"]
        self.assertEqual(theme["enum"], ["instrument", "client"])
        self.assertEqual(theme["default"], "instrument")

    def test_redaction_defaults_are_off(self):
        props = plugin.SCHEMA["function"]["parameters"]["properties"]
        self.assertIs(props["redact"]["default"], False)
        self.assertEqual(props["redact_words"]["type"], "array")
        self.assertEqual(props["redact_words"]["items"]["type"], "string")


class TestDefaults(ToolFixture):
    def test_default_call_writes_the_unredacted_instrument_receipt(self):
        result = self.call(out="receipt.html")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["theme"], "instrument")
        self.assertNotIn("redaction", result)

        html = self.read("receipt.html")
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            self.assertEqual(html, render.render_html(self.manifest))
        self.assertIn("@keyframes sweep", html)
        self.assertNotIn("theme-client", html)
        self.assertNotIn("redact-band", html)
        self.assertEqual(self.files(), ["receipt.html"], "no sidecar without redact")

    def test_default_out_name_is_unchanged(self):
        result = self.call()
        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(result["artifact"]).name, "receipts-%s.html" % self.short)
        self.assertEqual(self.files(), ["receipts-%s.html" % self.short])


class TestTheme(ToolFixture):
    def test_client_theme_reaches_the_file(self):
        result = self.call(theme="client", out="client.html")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["theme"], "client")
        html = self.read("client.html")
        self.assertIn('<body class="theme-client">', html)
        self.assertNotIn("@keyframes sweep", html)
        self.assertNotIn("repeating-linear-gradient", html)

    def test_unknown_theme_is_a_clean_error_and_writes_nothing(self):
        result = self.call(theme="fancy")
        self.assertFalse(result["ok"])
        self.assertIn("instrument", result["error"])
        self.assertEqual(self.files(), [])

    def test_keyword_arguments_match_the_argument_dict(self):
        with mock.patch.object(plugin, "build_manifest",
                               return_value=copy.deepcopy(self.manifest)):
            with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
                result = json.loads(plugin.receipts_build(theme="client", out="kw.html"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["theme"], "client")
        self.assertIn('<body class="theme-client">', self.read("kw.html"))


class TestRedaction(ToolFixture):
    def test_redact_writes_a_copy_and_a_sidecar_and_leaves_the_original_alone(self):
        self.call(out="plain.html")
        before = Path("plain.html").read_bytes()

        result = self.call(redact=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(result["artifact"]).name, "receipts-%s.redacted.html" % self.short)
        self.assertEqual(Path("plain.html").read_bytes(), before, "the original must not move")

        sidecar = Path(result["redaction"]["sidecar"])
        self.assertEqual(sidecar.name, "receipts-%s.redacted.redaction.json" % self.short)
        report = json.loads(self.read(sidecar.name))
        self.assertEqual(report["total"], sum(report["classes"].values()))
        self.assertEqual(report["total"], result["redaction"]["total"])
        self.assertEqual(report["classes"], result["redaction"]["classes"])
        self.assertEqual(report["residual"], {})
        self.assertEqual(
            self.files(),
            ["plain.html",
             "receipts-%s.redacted.html" % self.short,
             "receipts-%s.redacted.redaction.json" % self.short],
        )

    def test_explicit_out_puts_the_sidecar_beside_it(self):
        result = self.call(redact=True, out="foo.html")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.files(), ["foo.html", "foo.redaction.json"])

    def test_identity_data_is_gone_from_the_copy_and_the_sidecar(self):
        manifest = seeded(self.manifest)
        result = self.call(manifest, redact=True, redact_words=[WORD], out="copy.html")
        self.assertTrue(result["ok"], result)

        html = self.read("copy.html")
        sidecar = self.read("copy.redaction.json")
        for literal in SEEDED_LITERALS:
            with self.subTest(literal=literal):
                self.assertNotIn(literal, html)
                self.assertNotIn(literal, sidecar)

        # Removals are declared, not silent: the band names every class that fired.
        self.assertIn("Redacted copy.", html)
        for cls in ("secret", "email", "phone", "ip", "user", "word"):
            with self.subTest(cls=cls):
                self.assertIn("%s ×" % cls, html)
        self.assertIn("[secret-1]", html)
        self.assertIn("[word-1]", html)

    def test_word_list_as_a_list_removes_the_name(self):
        manifest = seeded(self.manifest)
        result = self.call(manifest, redact=True, redact_words=[WORD], out="copy.html")
        self.assertTrue(result["ok"], result)
        report = json.loads(self.read("copy.redaction.json"))
        self.assertEqual(report["words"], 1, "entries loaded from the list")
        self.assertEqual(report["classes"]["word"], 1)
        self.assertNotIn(WORD, self.read("copy.html"))

    def test_word_list_as_a_path_is_rejected_from_the_tool(self):
        # PR #152953: the TOOL accepts only a list of strings. The word-list
        # FILE form is a CLI affordance and must be a clean error here.
        manifest = seeded(self.manifest)
        Path("words.txt").write_text("# client names\n\n%s\n" % WORD, encoding="utf-8")
        as_list = self.call(manifest, redact=True, redact_words=[WORD], out="a.html")
        as_path = self.call(manifest, redact=True, redact_words="words.txt", out="b.html")
        self.assertTrue(as_list["ok"], as_list)
        self.assertFalse(as_path["ok"])
        self.assertIn("CLI-only", as_path["error"])
        self.assertEqual(
            [f for f in self.files() if f != "words.txt"], ["a.html", "a.redaction.json"]
        )


class TestCleanErrors(ToolFixture):
    def test_redact_words_without_redact_is_refused(self):
        result = self.call(redact_words=[WORD])
        self.assertFalse(result["ok"])
        self.assertIn("redact=true", result["error"])
        self.assertEqual(self.files(), [])

    def test_unreadable_word_list_is_a_clean_error(self):
        result = self.call(redact=True, redact_words="nope.txt")
        self.assertFalse(result["ok"])
        self.assertIn("CLI-only", result["error"])
        self.assertEqual(self.files(), [])

    def test_word_list_of_the_wrong_type_is_a_clean_error(self):
        result = self.call(redact=True, redact_words={"Acme": True})
        self.assertFalse(result["ok"])
        self.assertIn("list of strings", result["error"])
        self.assertEqual(self.files(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
