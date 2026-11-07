"""Acceptance tests for the redacted-copy CLI surface (RECEIPTS-1.2 card 1.2-b).

What is pinned here:

* ``--redact`` writes a *copy* plus a ``.redaction.json`` sidecar; the
  unredacted receipt of the same session is never touched;
* no seeded secret survives in the HTML, the sidecar or the ``--json`` output;
* the sidecar's arithmetic agrees with the document it describes;
* ``--theme client`` reaches the file as the paper document, with no sweep and
  no scanline texture; the default (instrument) route is the v1.1.0 document;
* the error paths are clean — exit 1 with a sentence, never a traceback.

The store is the demo fixture built into a temporary directory: no test here
touches the network or the real Hermes home.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
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
from make_demo_db import build as build_fixture  # noqa: E402

import receipts.cli as cli  # noqa: E402
import receipts.render as render  # noqa: E402
from receipts import privacy  # noqa: E402
from receipts.manifest import build_manifest  # noqa: E402

FIXED_CLOCK = 1793900000.0

PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "AAAAB3NzaC1yc2EAAAADAQABAAABgQDfixtureonlynotakey\n"
    "-----END OPENSSH PRIVATE KEY-----"
)
JWT = (
    "eyJhbGciOiJIUzI1NiJ9."
    "eyJzdWIiOiJmaXh0dXJlIn0."
    "ZmFrZXNpZ25hdHVyZWZvcnRoZXJlY2VpcHRzdWl0ZQ"
)
SK_KEY = "sk-ant-api03-FixtureOnlyAbCdEfGhIjKlMnOp"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
EMAIL = "client.contact+receipts@example.com"
PHONE = "+49 151 23456789"
IP = "10.42.7.19"
WORD = "Acme Industries"
URL_PASSWORD = "pa55-c0rrect-horse"
USER_PATH = r"C:\Users\ryoma\Projects\client"

SEEDED_LITERALS = [PEM, JWT, SK_KEY, AWS_KEY, EMAIL, PHONE, IP, WORD, URL_PASSWORD, USER_PATH]

SIDECAR_KEYS = ["version", "generated_at", "total", "classes", "rules", "fields", "words", "residual"]

PLACEHOLDER_RE = re.compile(r"\[(?:secret|email|phone|ip|word)-\d+\]|\[user\]")


def survivors(text: str, literals: list[str]) -> list[str]:
    return [literal for literal in literals if literal in text]


def seeded_manifest(base: dict) -> dict:
    """A real fixture manifest with one literal of every class planted in it.

    Every field below is rendered by the instrument document — the tests prove
    that with an ``assertIn`` on the unredacted render before asserting the
    redacted one is clean, so a seed cannot silently miss the document.
    """
    manifest = copy.deepcopy(base)
    manifest["title"] = "Receipt for " + WORD
    manifest["asks"][0]["text"] += " mail %s call %s host %s" % (EMAIL, PHONE, IP)
    manifest["nodes"][1]["args_summary"] = "curl https://svc:%s@api.example.com/v1" % URL_PASSWORD
    manifest["nodes"][2]["result_excerpt"] = "key:\n" + PEM + "\nend"
    manifest["nodes"][3]["args_detail"] = (
        '{"command": "OPENAI_API_KEY=' + SK_KEY + ' python run.py"}'
    )
    manifest["nodes"][4]["result_excerpt"] = "token " + JWT + " aws " + AWS_KEY
    manifest["sessions"][0]["cwd"] = USER_PATH
    return manifest


class CliFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.db = cls.root / "state.db"
        build_fixture(cls.db)
        cls.manifest = build_manifest("latest", db_path=cls.db)
        cls.short = cls.manifest["sessions"][0]["short_id"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        # A fresh working directory per test: the CLI's default output lands in
        # cwd, and a test must not see another test's receipt.
        self.work = Path(tempfile.mkdtemp(dir=self.root, prefix="case-"))
        previous = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, previous)

    # -- helpers -----------------------------------------------------------

    def run_cli(self, argv, manifest=None):
        args = list(argv) + ["--db", str(self.db)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if manifest is None:
                code = cli.main(args)
            else:
                payload = copy.deepcopy(manifest)
                with mock.patch.object(cli, "build_manifest", return_value=payload):
                    code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def read(self, name: str) -> str:
        return Path(name).read_text(encoding="utf-8")

    def sidecar(self, name: str) -> dict:
        return json.loads(self.read(name))

    def write_words(self, *entries: str) -> Path:
        path = Path("words.txt")
        path.write_text("\n".join(entries) + "\n", encoding="utf-8")
        return path

    def files(self) -> list[str]:
        return sorted(path.name for path in self.work.iterdir() if path.name != "words.txt")


class TestDefaultNames(CliFixture):
    def test_plain_default_name_is_unchanged(self):
        path = cli.default_out_path(self.manifest)
        self.assertEqual(path.name, "receipts-%s.html" % self.short)
        self.assertEqual(path.parent, Path.cwd())

    def test_redacted_default_name_gains_redacted_before_the_extension(self):
        path = cli.default_out_path(self.manifest, redact=True)
        self.assertEqual(path.name, "receipts-%s.redacted.html" % self.short)
        self.assertNotEqual(path, cli.default_out_path(self.manifest))


class TestRedactedCopy(CliFixture):
    def test_redact_writes_a_copy_and_a_sidecar(self):
        code, out, err = self.run_cli(["latest", "--redact", "-q"])
        self.assertEqual(code, 0, err)
        # The sidecar is out.with_suffix(".redaction.json") — for the default
        # copy that keeps the .redacted token, so the two names stay paired.
        self.assertEqual(
            self.files(),
            ["receipts-%s.redacted.html" % self.short,
             "receipts-%s.redacted.redaction.json" % self.short],
        )
        self.assertIn(str(Path("receipts-%s.redacted.html" % self.short)), out)

    def test_explicit_out_puts_the_sidecar_beside_it(self):
        code, _, err = self.run_cli(["latest", "--redact", "-o", "foo.html", "-q"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.files(), ["foo.html", "foo.redaction.json"])
        self.assertGreaterEqual(self.sidecar("foo.redaction.json")["total"], 1)

    def test_redaction_never_overwrites_the_unredacted_receipt(self):
        code, out, _ = self.run_cli(["latest", "-q"])
        self.assertEqual(code, 0)
        plain = Path("receipts-%s.html" % self.short)
        before = plain.read_bytes()

        code, _, err = self.run_cli(["latest", "--redact", "-q"])
        self.assertEqual(code, 0, err)
        redacted = Path("receipts-%s.redacted.html" % self.short)
        self.assertTrue(redacted.exists())
        self.assertEqual(plain.read_bytes(), before, "the original receipt must not move")
        self.assertNotEqual(redacted.read_bytes(), before)

    def test_quiet_prints_only_the_artifact_path(self):
        code, out, err = self.run_cli(["latest", "-q"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertEqual(out.strip(), str(Path.cwd() / ("receipts-%s.html" % self.short)))

    def test_redacted_copy_is_offline_and_self_contained(self):
        code, _, err = self.run_cli(["latest", "--redact", "-q"])
        self.assertEqual(code, 0, err)
        html = self.read("receipts-%s.redacted.html" % self.short)
        self.assertIsNone(re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', html))
        self.assertNotIn("<link", html)
        self.assertNotIn("@import", html)


class TestSidecar(CliFixture):
    def test_sidecar_has_the_shipped_keys_and_consistent_counts(self):
        code, _, err = self.run_cli(["latest", "--redact", "-q"])
        self.assertEqual(code, 0, err)
        report = self.sidecar("receipts-%s.redacted.redaction.json" % self.short)

        self.assertEqual(list(report), SIDECAR_KEYS)
        self.assertEqual(report["version"], privacy.REPORT_VERSION)
        self.assertEqual(report["total"], sum(report["classes"].values()))
        self.assertEqual(report["total"], sum(report["rules"].values()))
        self.assertEqual(
            report["total"], sum(sum(bucket.values()) for bucket in report["fields"].values())
        )
        self.assertEqual(report["residual"], {})

        # The sidecar describes the manifest; the document renders it. A field
        # can legitimately be printed twice (the title is both <title> and the
        # heading), so the document is checked for the placeholders, not for
        # the report's arithmetic.
        html = self.read("receipts-%s.redacted.html" % self.short)
        self.assertEqual(set(PLACEHOLDER_RE.findall(html)), {"[user]"})

    def test_sidecar_timestamp_is_the_manifest_timestamp(self):
        manifest = seeded_manifest(self.manifest)
        manifest["generated_at"] = "2026-11-07T01:02:03+01:00"
        code, _, err = self.run_cli(["latest", "--redact", "-o", "copy.html", "-q"],
                                    manifest=manifest)
        self.assertEqual(code, 0, err)
        report = self.sidecar("copy.redaction.json")
        self.assertEqual(report["generated_at"], manifest["generated_at"])

    def test_sidecar_is_byte_stable_for_a_fixed_manifest(self):
        manifest = seeded_manifest(self.manifest)
        self.write_words("# client names", WORD)
        for name in ("a.html", "b.html"):
            code, _, err = self.run_cli(
                ["latest", "--redact", "--redact-words", "words.txt", "-o", name, "-q"],
                manifest=manifest,
            )
            self.assertEqual(code, 0, err)
        self.assertEqual(Path("a.redaction.json").read_bytes(),
                         Path("b.redaction.json").read_bytes())

    def test_redacted_document_is_reproducible_for_a_fixed_manifest(self):
        manifest = seeded_manifest(self.manifest)
        self.write_words(WORD)
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            for name in ("a.html", "b.html"):
                code, _, err = self.run_cli(
                    ["latest", "--redact", "--redact-words", "words.txt", "-o", name, "-q"],
                    manifest=manifest,
                )
                self.assertEqual(code, 0, err)
        self.assertEqual(Path("a.html").read_bytes(), Path("b.html").read_bytes())

    def test_cli_writes_exactly_the_redacted_document_it_reports(self):
        manifest = seeded_manifest(self.manifest)
        self.write_words(WORD)
        redacted, report = privacy.redact_manifest(manifest, [WORD])
        report["generated_at"] = manifest["generated_at"]
        redacted["redaction"] = report
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            expected = render.render_html(redacted)
            code, _, err = self.run_cli(
                ["latest", "--redact", "--redact-words", "words.txt",
                 "-o", "copy.html", "-q"],
                manifest=manifest,
            )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("copy.html"), expected)


class TestSecretsDoNotTravel(CliFixture):
    def test_every_seed_reaches_the_unredacted_document(self):
        # The control for the next test: if a seed is not rendered, a clean
        # redacted copy would prove nothing about it.
        manifest = seeded_manifest(self.manifest)
        instrument = render.render_html(manifest)
        self.assertEqual(survivors(instrument, SEEDED_LITERALS), SEEDED_LITERALS)

    def test_no_seed_survives_in_the_html_sidecar_or_band(self):
        manifest = seeded_manifest(self.manifest)
        self.write_words(WORD)
        code, out, err = self.run_cli(
            ["latest", "--redact", "--redact-words", "words.txt", "-q"], manifest=manifest
        )
        self.assertEqual(code, 0, err)
        html = self.read("receipts-%s.redacted.html" % self.short)
        sidecar = self.read("receipts-%s.redacted.redaction.json" % self.short)

        self.assertEqual(survivors(html, SEEDED_LITERALS), [])
        self.assertEqual(survivors(sidecar, SEEDED_LITERALS), [])
        self.assertNotIn("Traceback", err)

        # A redaction is declared, not silent: the band names every family.
        self.assertIn("Redacted copy.", html)
        for cls in ("secret", "email", "phone", "ip", "user", "word"):
            with self.subTest(cls=cls):
                self.assertIn("%s ×" % cls, html)
        self.assertIn("[secret-1]", html)
        self.assertIn("[word-1]", html)

    def test_json_mode_prints_the_redacted_manifest_and_writes_nothing(self):
        manifest = seeded_manifest(self.manifest)
        self.write_words(WORD)
        code, out, err = self.run_cli(
            ["latest", "--redact", "--redact-words", "words.txt", "--json"], manifest=manifest
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertIn("redaction", payload)
        self.assertGreater(payload["redaction"]["total"], 0)
        self.assertEqual(
            privacy.count_placeholders(payload), payload["redaction"]["classes"],
            "the emitted manifest is exactly the document the report describes",
        )
        self.assertEqual(survivors(out, SEEDED_LITERALS), [])
        self.assertEqual(self.files(), [], "a --json run must not write an artifact")

    def test_word_list_entries_and_hits_are_both_counted(self):
        manifest = seeded_manifest(self.manifest)
        self.write_words("# client names", "", WORD, "acme industries")
        code, _, err = self.run_cli(
            ["latest", "--redact", "--redact-words", "words.txt", "-o", "copy.html", "-q"],
            manifest=manifest,
        )
        self.assertEqual(code, 0, err)
        report = self.sidecar("copy.redaction.json")
        html = self.read("copy.html")
        self.assertEqual(report["words"], 1, "deduped entries loaded from the list")
        self.assertEqual(report["classes"]["word"], 1, "one occurrence in the manifest")
        self.assertIn("[word-1]", html)
        self.assertNotIn(WORD, html)


class TestThemes(CliFixture):
    def test_theme_client_through_the_cli(self):
        code, out, err = self.run_cli(["latest", "--theme", "client", "-q"])
        self.assertEqual(code, 0, err)
        html = Path(out.strip()).read_text(encoding="utf-8")
        self.assertIn('<body class="theme-client">', html)
        self.assertNotIn("@keyframes sweep", html)
        self.assertNotIn("animation:sweep", html)
        self.assertNotIn("repeating-linear-gradient", html)

    def test_instrument_is_the_default_through_the_cli(self):
        code, out, err = self.run_cli(["latest", "-q"])
        self.assertEqual(code, 0, err)
        html = Path(out.strip()).read_text(encoding="utf-8")
        self.assertNotIn("theme-client", html)
        self.assertIn("@keyframes sweep", html)
        self.assertIn('<body><div class="wrap">', html)

    def test_cli_writes_exactly_the_instrument_document(self):
        manifest = seeded_manifest(self.manifest)
        with mock.patch.object(render, "_now_ts", new=lambda: FIXED_CLOCK):
            expected = render.render_html(manifest)
            code, _, err = self.run_cli(["latest", "-q"], manifest=manifest)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("receipts-%s.html" % self.short), expected)

    def test_redacted_client_copy_is_the_share_artifact(self):
        manifest = seeded_manifest(self.manifest)
        self.write_words(WORD)
        code, _, err = self.run_cli(
            ["latest", "--redact", "--redact-words", "words.txt", "--theme", "client", "-q"],
            manifest=manifest,
        )
        self.assertEqual(code, 0, err)
        html = self.read("receipts-%s.redacted.html" % self.short)
        self.assertIn('<body class="theme-client">', html)
        self.assertIn('class="redact-band"', html)
        self.assertNotIn("@keyframes sweep", html)
        self.assertEqual(survivors(html, SEEDED_LITERALS), [])


class TestCleanErrors(CliFixture):
    def test_redact_words_requires_redact(self):
        code, out, err = self.run_cli(["latest", "--redact-words", "words.txt"])
        self.assertEqual(code, 1)
        self.assertIn("--redact-words", err)
        self.assertIn("needs --redact", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.files(), [])

    def test_unreadable_word_file_is_a_clean_error(self):
        code, out, err = self.run_cli(["latest", "--redact", "--redact-words", "nope.txt"])
        self.assertEqual(code, 1)
        self.assertIn("cannot read --redact-words file", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.files(), [])

    def test_rollup_refuses_redaction(self):
        code, out, err = self.run_cli(["rollup", "--since", "7d", "--redact"])
        self.assertEqual(code, 1)
        self.assertIn("does not support redaction yet", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.files(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
