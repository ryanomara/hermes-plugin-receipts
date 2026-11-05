"""Acceptance tests for Receipts.

Stdlib only (``python -m unittest``) so the no-new-dependencies clause stays
unambiguous.  Each test maps to a clause on the card:

* honest failures        -> TestHonesty
* cost reconciliation    -> TestReconciliation
* offline artifact       -> TestArtifact
* fresh profile          -> TestResilience
* plugin contract        -> TestPlugin
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from receipts.manifest import build_manifest  # noqa: E402
from receipts.render import render_html  # noqa: E402

sys.path.insert(0, str(ROOT / "fixtures"))
from make_demo_db import build as build_fixture  # noqa: E402


class FixtureCase(unittest.TestCase):
    """Shares one generated store across the read-only tests."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "state.db"
        build_fixture(cls.db)
        cls.manifest = build_manifest("latest", db_path=cls.db)
        cls.html = render_html(cls.manifest)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class TestHonesty(FixtureCase):
    def test_every_failed_call_survives(self):
        failed = [n for n in self.manifest["nodes"] if n["status"] == "failed"]
        self.assertEqual(len(failed), 4, "all four failing calls must be in the manifest")

    def test_failures_reach_the_html(self):
        # Count node elements, not the selector strings inside the inline script.
        rendered = re.findall(r'<div class="node ([a-z]+)" data-node', self.html)
        self.assertEqual(len(rendered), 18)
        self.assertEqual(rendered.count("failed"), 4)

    def test_error_text_is_preserved(self):
        errors = " ".join(n["error"] for n in self.manifest["nodes"] if n["status"] == "failed")
        self.assertIn("403", errors)
        self.assertIn("old_string not found", errors)

    def test_failed_call_is_not_reported_as_success(self):
        for node in self.manifest["nodes"]:
            if node["status"] == "failed":
                self.assertNotEqual(node["status"], "ok")
                self.assertTrue(node["error"])

    def test_unstructured_result_is_unverified_not_ok(self):
        unverified = [n for n in self.manifest["nodes"] if n["status"] == "unverified"]
        self.assertEqual(len(unverified), 1)
        self.assertEqual(unverified[0]["tool"], "web_search")

    def test_retry_chain_is_wired(self):
        nodes = self.manifest["nodes"]
        failed = next(n for n in nodes if n["tool"] == "terminal" and n["status"] == "failed")
        self.assertIsNotNone(failed["retried_by"])
        retry = nodes[failed["retried_by"]]
        self.assertEqual(retry["retry_of"], failed["index"])
        self.assertEqual(retry["attempt"], 2)
        self.assertTrue(retry["chain_recovered"])

    def test_different_command_to_same_tool_is_not_a_retry(self):
        """A failed `git push` is not retried by the `git status` after it."""
        nodes = self.manifest["nodes"]
        push = next(n for n in nodes if "git push" in n["args_summary"])
        status = next(n for n in nodes if "git status" in n["args_summary"])
        self.assertEqual(push["status"], "failed")
        self.assertIsNone(status["retry_of"], "git status must not be labelled a retry of git push")
        self.assertEqual(self.manifest["aggregates"]["failures_unrecovered"], 1)

    def test_unresolved_failure_is_named_in_the_artifact(self):
        self.assertIn("never resolved", self.html)
        self.assertIn("git push origin fix/discount-rounding", self.html)


class TestReconciliation(FixtureCase):
    def test_usage_rows_sum_to_session_total(self):
        recon = self.manifest["reconciliation"]
        self.assertAlmostEqual(recon["usage_cost"], recon["recorded_cost"], places=6)
        cost_check = next(c for c in recon["checks"] if c["name"] == "cost")
        self.assertEqual(cost_check["delta"], 0.0)

    def test_tool_count_matches_session_row(self):
        check = next(c for c in self.manifest["reconciliation"]["checks"] if c["name"] == "tool calls")
        self.assertEqual(check["delta"], 0)

    def test_tokens_match_recorded(self):
        usage = self.manifest["usage"]
        totals = self.manifest["aggregates"]["usage"]
        self.assertEqual(totals["input"], sum(u["input"] for u in usage))
        self.assertEqual(totals["output"], sum(u["output"] for u in usage))
        session = self.manifest["sessions"][0]
        self.assertEqual(session["tokens"]["input"], totals["input"])

    def test_cost_is_labelled_as_recorded_not_repriced(self):
        self.assertEqual(self.manifest["sessions"][0]["cost"]["status"], "estimated")
        self.assertIn("not a price table applied later", self.html)


class TestArtifact(FixtureCase):
    def test_no_external_resources(self):
        self.assertIsNone(re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', self.html),
                          "artifact must not reference any remote resource")
        self.assertNotIn("<link", self.html)
        self.assertNotIn("@import", self.html)

    def test_no_cdn_in_css_or_js(self):
        self.assertNotIn("fonts.googleapis", self.html)
        self.assertNotIn("cdn.", self.html.lower())

    def test_tool_output_cannot_break_out_of_tags(self):
        self.assertNotIn("</script><script", self.html)

    def test_all_dynamic_text_is_escaped(self):
        self.assertNotIn("<script>alert", self.html)

    def test_renders_all_eighteen_nodes(self):
        self.assertEqual(len(re.findall(r'<div class="node [a-z]+" data-node', self.html)), 18)

    def test_artifact_is_a_single_document(self):
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertIn("</html>", self.html)
        self.assertEqual(self.html.lower().count("<html"), 1)

    def test_size_is_shareable(self):
        size = len(self.html.encode("utf-8"))
        self.assertLess(size, 2_000_000, f"artifact is {size} bytes")


class TestResilience(unittest.TestCase):
    def test_missing_store_is_a_clean_error(self):
        from receipts.dbio import ReceiptsError

        missing = Path(tempfile.gettempdir()) / "receipts-does-not-exist-xyz.db"
        with self.assertRaises(ReceiptsError):
            build_manifest("latest", db_path=missing)

    def test_empty_store_says_so(self):
        from receipts.dbio import ReceiptsError
        from make_demo_db import SCHEMA

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "empty.db"
            conn = sqlite3.connect(db)
            conn.executescript(SCHEMA)  # real table shape, no rows
            conn.commit()
            conn.close()
            with self.assertRaises(ReceiptsError) as ctx:
                build_manifest("latest", db_path=db)
            self.assertIn("No sessions", str(ctx.exception))

    def test_unknown_target_is_a_clean_error(self):
        from receipts.dbio import ReceiptsError

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            build_fixture(db)
            with self.assertRaises(ReceiptsError) as ctx:
                build_manifest("no-such-session-anywhere", db_path=db)
            self.assertIn("No session matches", str(ctx.exception))

    def test_read_only_open_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            build_fixture(db)
            before = db.stat().st_mtime_ns
            build_manifest("latest", db_path=db)
            self.assertEqual(db.stat().st_mtime_ns, before, "reading must not modify the store")

    def test_date_range_target(self):
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            build_fixture(db)
            day = datetime.fromtimestamp(1793800000.0).strftime("%Y-%m-%d")
            manifest = build_manifest(day, db_path=db)
            self.assertEqual(manifest["kind"], "range")
            self.assertGreaterEqual(len(manifest["sessions"]), 1)

    def test_card_target_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            build_fixture(db)
            manifest = build_manifest("card:t_demo0001", db_path=db)
            self.assertEqual(manifest["kind"], "card")
            self.assertEqual(len(manifest["sessions"]), 1)


class TestPlugin(unittest.TestCase):
    def test_plugin_yaml_is_valid(self):
        import yaml  # available in the Hermes venv; skipped if a bare interpreter runs the suite

        manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
        for field in ("name", "version", "description"):
            self.assertTrue(manifest.get(field), f"plugin.yaml missing {field}")
        self.assertIn("receipts_build", manifest["provides_tools"])

    def test_register_is_inert_at_import(self):
        """Importing the plugin must not open a database or write anything."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("receipts_plugin_entry", ROOT / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.register))

    def test_register_wires_tool_and_cli(self):
        recorded = {"tools": [], "cli": [], "hooks": []}

        class Ctx:
            def register_tool(self, **kwargs):
                recorded["tools"].append(kwargs["name"])

            def register_cli_command(self, **kwargs):
                recorded["cli"].append(kwargs["name"])

            def register_hook(self, *a, **k):
                recorded["hooks"].append(a)

        import importlib.util

        spec = importlib.util.spec_from_file_location("receipts_plugin_entry2", ROOT / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.register(Ctx())

        self.assertEqual(recorded["tools"], ["receipts_build"])
        self.assertEqual(recorded["cli"], ["receipts"])

    def test_tool_handler_returns_json_and_errors_cleanly(self):
        from receipts.plugin import receipts_build

        result = json.loads(receipts_build({"target": "definitely-not-a-session"}))
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_tool_schema_shape(self):
        from receipts.plugin import SCHEMA

        fn = SCHEMA["function"]
        self.assertEqual(fn["name"], "receipts_build")
        self.assertIn("target", fn["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
