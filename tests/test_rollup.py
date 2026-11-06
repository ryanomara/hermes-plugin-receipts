"""Acceptance tests for rollup mode (v1.1).

Maps to the card's clauses:

* failure taxonomy is deterministic, ranked, and first-seen   -> TestTaxonomy
* cost by model and card reconciles with receipts             -> TestCost
* slowest tools by p50/p95                                    -> TestSlowTools
* cards with the most retries                                 -> TestHotspots
* week-over-week deltas                                       -> TestDeltas
* fixed window is byte-stable across runs                     -> TestStability
* board filter, CLI, and the artifact contract                 -> TestCli, TestArtifact
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from receipts.cli import main  # noqa: E402
from receipts.dbio import ReceiptsError  # noqa: E402
from receipts.manifest import build_manifest  # noqa: E402
from receipts.render_rollup import render_rollup  # noqa: E402

sys.path.insert(0, str(ROOT / "fixtures"))
import make_rollup_db  # noqa: E402
from receipts.rollup import (  # noqa: E402
    TAXONOMY_CLASSES,
    build_rollup,
    canonical_digest,
    classify_tool_failure,
    parse_window,
    percentile,
    render_json,
)

AS_OF = make_rollup_db.AS_OF


class RollupFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "fixture"
        cls.paths = make_rollup_db.build(cls.out)
        cls.db = cls.paths["state_db"]
        cls.kanban = cls.paths["kanban_home"]
        cls.payload = cls.build()
        cls.html = render_rollup(cls.payload)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def build(cls, **kwargs):
        return build_rollup("7d", as_of=str(AS_OF), db_path=cls.db,
                            kanban_home_path=cls.kanban, **kwargs)

    @property
    def cur(self):
        return self.payload["current"]

    def classes(self, window="current"):
        return {t["class"]: t["count"] for t in self.payload[window]["taxonomy"]}


class TestTaxonomy(RollupFixture):
    def test_window_is_explicit_and_utc(self):
        window = self.payload["window"]
        self.assertEqual(window["start_ts"], AS_OF - 7 * 86400)
        self.assertEqual(window["end_ts"], AS_OF)
        self.assertTrue(window["start"].endswith("Z"))
        self.assertEqual(window["previous_end"], window["start"])

    def test_every_failure_is_classified_once(self):
        counted = sum(self.classes().values())
        self.assertEqual(counted, self.cur["failures"])
        self.assertEqual(self.cur["failures"], 6)

    def test_expected_classes_and_counts(self):
        self.assertEqual(self.classes(), {
            "test_failure": 1,
            "patch_context": 1,
            "auth": 1,
            "timeout": 1,
            "not_found": 1,
            "guard_refusal": 1,
        })

    def test_ranked_by_frequency_then_name(self):
        rows = self.cur["taxonomy"]
        keys = [(-r["count"], r["class"]) for r in rows]
        self.assertEqual(keys, sorted(keys))

    def test_first_and_last_seen_are_recorded(self):
        for row in self.cur["taxonomy"]:
            self.assertTrue(row["first_seen"].endswith("Z"))
            self.assertTrue(row["last_seen"].endswith("Z"))
            self.assertLessEqual(row["first_seen"], row["last_seen"])

    def test_refined_classes_name_their_base(self):
        by_class = {r["class"]: r for r in self.cur["taxonomy"]}
        self.assertEqual(by_class["patch_context"]["refined_from"], "not_found")
        self.assertEqual(by_class["test_failure"]["refined_from"], "nonzero_exit")

    def test_unresolved_failures_are_counted(self):
        # auth (never retried) + not_found + guard_refusal
        self.assertEqual(self.cur["unresolved"], 3)
        self.assertEqual(self.cur["retry_chains"], 3)
        self.assertEqual(self.cur["chains_recovered"], 3)

    def test_classifier_is_a_pure_table(self):
        cases = [
            ("old_string not found in src/x.py", None, "patch_context"),
            ("Could not find a match for old_string in the file", None, "patch_context"),
            ("Found 2 matches for old_string. Provide more context", None, "patch_context"),
            ("no such file or directory: 'src/x.py'", None, "not_found"),
            ("FileNotFoundError: [Errno 2]", None, "not_found"),
            ("Error executing tool 'terminal': timed out after 420.0s", None, "timeout"),
            ("TimeoutError: took too long", None, "timeout"),
            ("permission denied: /root/x", None, "permission"),
            ("fatal: Authentication failed for 'https://x'", None, "auth"),
            ("HTTP 429 Too Many Requests", None, "rate_limit"),
            ("connection reset by peer", None, "network"),
            ("command not found: rgx", None, "missing_binary"),
            ("Python cannot import 'flask'. Most often the wrong interpreter", None, "environment"),
            ("error: unknown option `cached'", None, "command_usage"),
            ("===== FAILURES =====\nE assert 1 == 2", 1, "test_failure"),
            ("1 failed, 6 passed in 2.1s", 1, "test_failure"),
            ("Traceback (most recent call last):\nValueError: substring not found", 1, "exception"),
            ("Refusing to overwrite C:/x/index.html", None, "guard_refusal"),
            ("URL blocked by SSRF protection (private/internal address)", None, "guard_refusal"),
            ("tool_call to 'x' is missing required argument(s): action", None, "tool_contract"),
            ("failed argument validation at arguments.action (enum)", None, "tool_contract"),
            ("exit code 1", 1, "exit_nonzero"),
            ("something broke", None, "tool_error"),
            ("", None, "unknown"),
            ("", 2, "exit_nonzero"),
        ]
        for error, code, expected in cases:
            got, refined = classify_tool_failure(error, code)
            self.assertEqual(got, expected, f"{error!r} → {got}, expected {expected}")
            self.assertIn(got, TAXONOMY_CLASSES)
            if refined:
                self.assertIn(refined, ("not_found", "nonzero_exit"))

    def test_classification_ignores_schema_text_after_the_cause(self):
        """A parameter schema quoting `timeout` must not turn a contract error into one."""
        error = ("tool_call to 'process_manage' is missing required argument(s): action. "
                 "The tool was NOT invoked. Parameters schema: {\"properties\": {\"timeout\": ...}}")
        self.assertEqual(classify_tool_failure(error)[0], "tool_contract")
        late = "tool_call to 'x' is missing required argument(s): action." + ("x" * 700) + " timeout"
        self.assertEqual(classify_tool_failure(late)[0], "tool_contract")

    def test_classification_is_stable_across_calls(self):
        text = "E assert 1 == 2\nFAILURES\n"
        first = {classify_tool_failure(text, 1) for _ in range(50)}
        self.assertEqual(len(first), 1)


class TestCost(RollupFixture):
    def test_usage_and_recorded_totals(self):
        cost = self.cur["cost"]
        self.assertAlmostEqual(cost["usage_usd"], 0.900000, places=6)
        self.assertAlmostEqual(cost["recorded_usd"], 0.920000, places=6)
        self.assertAlmostEqual(cost["delta_usd"], -0.020000, places=6)

    def test_sessions_without_usage_are_named_not_priced(self):
        cost = self.cur["cost"]
        self.assertEqual(cost["sessions_without_usage"], 1)
        self.assertEqual(cost["sessions_without_usage_ids"], ["local-eee000"])

    def test_by_model_sums_to_usage_total(self):
        rows = {r["model"]: r for r in self.cur["by_model"]}
        self.assertAlmostEqual(rows["m-one"]["cost_usd"], 0.600000, places=6)
        self.assertAlmostEqual(rows["m-two"]["cost_usd"], 0.300000, places=6)
        self.assertEqual(rows["m-one"]["providers"], ["prov-a"])
        self.assertAlmostEqual(sum(r["cost_usd"] for r in rows.values()), 0.900000, places=6)

    def test_by_card_attributes_subagent_to_its_parent_card(self):
        cards = {c["card"]: c for c in self.cur["by_card"]}
        self.assertAlmostEqual(cards["t_a0000001"]["cost_usd"], 0.600000, places=6)
        self.assertEqual(cards["t_a0000001"]["sessions"], 2)  # worker + sub-agent
        self.assertAlmostEqual(cards["t_b0000001"]["cost_usd"], 0.250000, places=6)
        self.assertAlmostEqual(cards[None]["cost_usd"], 0.050000, places=6)
        self.assertAlmostEqual(
            sum(c["cost_usd"] for c in self.cur["by_card"]), 0.900000, places=6)

    def test_card_attribution_check_has_no_delta(self):
        check = next(c for c in self.cur["checks"] if c["name"].startswith("cost · card"))
        self.assertAlmostEqual(check["delta"], 0.0, places=9)

    def test_rollup_cost_reconciles_with_per_session_receipts(self):
        """The strongest clause: a receipt for each session agrees with the rollup."""
        conn = sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM sessions WHERE started_at >= ? AND started_at < ? "
            "AND COALESCE(hidden,0)=0 AND COALESCE(message_count,0)>0",
            (AS_OF - 7 * 86400, AS_OF))]
        conn.close()
        receipt_total = 0.0
        for sid in ids:
            manifest = build_manifest(sid, db_path=self.db)
            receipt_total += manifest["reconciliation"]["usage_cost"]
        self.assertAlmostEqual(receipt_total, self.cur["cost"]["usage_usd"], places=6)

    def test_rollup_failures_reconcile_with_per_session_receipts(self):
        conn = sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM sessions WHERE started_at >= ? AND started_at < ? "
            "AND COALESCE(hidden,0)=0 AND COALESCE(message_count,0)>0",
            (AS_OF - 7 * 86400, AS_OF))]
        conn.close()
        receipt_failures = sum(
            build_manifest(sid, db_path=self.db)["aggregates"]["failures"] for sid in ids)
        self.assertEqual(receipt_failures, self.cur["failures"])


class TestSlowTools(RollupFixture):
    def test_terminal_percentiles_are_exact(self):
        terminal = next(t for t in self.cur["slow_tools"] if t["tool"] == "terminal")
        self.assertEqual(terminal["calls"], 5)
        self.assertEqual(terminal["timed_calls"], 5)
        self.assertAlmostEqual(terminal["p50_s"], 5.0, places=3)
        self.assertAlmostEqual(terminal["p95_s"], 11.2, places=3)
        self.assertAlmostEqual(terminal["max_s"], 12.5, places=3)

    def test_wait_tools_are_excluded_and_named(self):
        self.assertNotIn("clarify", [t["tool"] for t in self.cur["slow_tools"]])
        excluded = {t["tool"]: t for t in self.cur["slow_tools_excluded"]}
        self.assertIn("clarify", excluded)
        self.assertIn("waits on a human", excluded["clarify"]["reason"])

    def test_ranked_by_p95_descending(self):
        p95 = [t["p95_s"] for t in self.cur["slow_tools"]]
        self.assertEqual(p95, sorted(p95, reverse=True))

    def test_percentile_method(self):
        self.assertEqual(percentile([], 0.5), None)
        self.assertEqual(percentile([4.0], 0.95), 4.0)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 0.95), 3.85)
        self.assertAlmostEqual(percentile([1, 2, 3, 4, 5], 0.5), 3.0)


class TestHotspots(RollupFixture):
    def test_cards_with_most_retries(self):
        hotspots = self.cur["retry_hotspots"]
        self.assertEqual([(h["card"], h["retries"]) for h in hotspots],
                         [("t_a0000001", 2), ("t_b0000001", 1)])

    def test_unattributed_retries_are_counted_outside_the_table(self):
        self.assertEqual(self.cur["unattributed_retries"], 0)

    def test_hotspot_carries_board_status_and_cost(self):
        first = self.cur["retry_hotspots"][0]
        self.assertEqual(first["board"], "alpha")
        self.assertEqual(first["status"], "done")
        self.assertAlmostEqual(first["cost_usd"], 0.600000, places=6)


class TestDeltas(RollupFixture):
    def test_previous_window_is_exactly_seven_days_earlier(self):
        window = self.payload["window"]
        self.assertEqual(window["previous_week"], "2026-W44")
        self.assertEqual(window["week"], "2026-W45")

    def test_headline_movements(self):
        deltas = {d["metric"]: d for d in self.payload["deltas"]}
        self.assertEqual(deltas["failures"]["current"], 6)
        self.assertEqual(deltas["failures"]["previous"], 1)
        self.assertEqual(deltas["failures"]["delta"], 5)
        self.assertAlmostEqual(deltas["cost"]["delta"], 0.58, places=6)
        self.assertAlmostEqual(deltas["cost"]["delta_pct"], 181.25, places=2)
        self.assertEqual(deltas["cost"]["kind"], "money")
        self.assertEqual(deltas["p95 tool duration"]["kind"], "duration")

    def test_every_delta_is_deterministic_and_typed(self):
        for row in self.payload["deltas"]:
            self.assertIn(row["kind"], ("count", "money", "percent", "duration"))
            self.assertIn(row["better_when"], ("lower", "higher", "neutral"))


class TestBoard(RollupFixture):
    def test_board_filter_narrows_to_one_board(self):
        payload = self.build(board="beta")
        cur = payload["current"]
        self.assertEqual(cur["sessions"], 1)
        self.assertEqual(cur["tool_calls"], 4)
        self.assertEqual(cur["failures"], 2)
        self.assertAlmostEqual(cur["cost"]["usage_usd"], 0.250000, places=6)
        self.assertEqual({c["board"] for c in cur["by_card"] if c["card"]}, {"beta"})

    def test_unknown_board_lists_the_known_ones(self):
        with self.assertRaises(ReceiptsError) as ctx:
            self.build(board="no-such-board")
        self.assertIn("Known boards", str(ctx.exception))
        self.assertIn("alpha", str(ctx.exception))

    def test_unresolvable_window_is_a_clean_error(self):
        with self.assertRaises(ReceiptsError):
            parse_window("0h", str(AS_OF))
        with self.assertRaises(ReceiptsError):
            parse_window("not-a-duration", str(AS_OF))


class TestStability(RollupFixture):
    def test_two_runs_are_byte_identical(self):
        first = self.build()
        second = self.build()
        self.assertEqual(render_json(first), render_json(second))
        self.assertEqual(render_rollup(first), render_rollup(second))

    def test_digest_matches_its_payload(self):
        self.assertEqual(self.payload["stability"]["digest"], canonical_digest(self.payload))
        self.assertTrue(self.payload["stability"]["digest"].startswith("sha256:"))

    def test_digest_covers_measurements_not_file_metadata(self):
        stat = self.db.stat()
        try:
            os.utime(self.db, (0, 0))
            moved = self.build()
        finally:
            os.utime(self.db, (stat.st_atime, stat.st_mtime))
        self.assertNotEqual(moved["source"]["mtime"], self.payload["source"]["mtime"])
        self.assertEqual(moved["stability"]["digest"], self.payload["stability"]["digest"])

    def test_no_render_clock_in_the_payload(self):
        blob = render_json(self.payload)
        self.assertNotIn("generated_at", blob)
        allowed = {
            self.payload["window"]["start"], self.payload["window"]["end"],
            self.payload["window"]["previous_start"], self.payload["window"]["previous_end"],
        }
        for window in ("current", "previous"):
            for row in self.payload[window]["taxonomy"]:
                allowed.update({row["first_seen"], row["last_seen"]})
                if row.get("example"):
                    allowed.add(row["example"]["at"])
        stamps = set(re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", blob))
        self.assertTrue(stamps.issubset(allowed), stamps - allowed)

    def test_empty_window_is_an_honest_artifact(self):
        payload = build_rollup("7d", as_of=str(make_rollup_db.PREVIOUS_START - 10 * 86400),
                               db_path=self.db, kanban_home_path=self.kanban)
        self.assertEqual(payload["current"]["sessions"], 0)
        self.assertEqual(payload["current"]["failures"], 0)
        html = render_rollup(payload)
        self.assertIn("No failed tool calls in this window", html)
        self.assertIn("No per-model usage rows in this window", html)
        self.assertIn("No session in this window could be attributed", html)
        self.assertIn("no measurable change", html)


class TestCli(RollupFixture):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_cli_writes_html_and_json(self):
        target = Path(self.tmp.name) / "cli" / "weekly"
        code, out, _ = self.run_cli([
            "rollup", "--since", "7d", "--as-of", str(AS_OF),
            "--db", str(self.db), "--kanban-home", str(self.kanban),
            "--out", str(target),
        ])
        self.assertEqual(code, 0)
        html_path = target.with_suffix(".html")
        json_path = target.with_suffix(".json")
        self.assertTrue(html_path.exists())
        self.assertTrue(json_path.exists())
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["kind"], "rollup")
        self.assertEqual(payload["current"]["failures"], 6)
        self.assertIn("rollup →", out)
        self.assertEqual(html_path.read_text(encoding="utf-8"), render_rollup(payload))

    def test_cli_json_prints_and_writes_nothing(self):
        target = Path(self.tmp.name) / "cli" / "json-only"
        code, out, _ = self.run_cli([
            "rollup", "--since", "7d", "--as-of", str(AS_OF),
            "--db", str(self.db), "--kanban-home", str(self.kanban),
            "--out", str(target), "--json",
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["kind"], "rollup")
        self.assertFalse(target.with_suffix(".html").exists())

    def test_rollup_flags_outside_rollup_are_rejected(self):
        code, _, err = self.run_cli(["latest", "--since", "7d", "--db", str(self.db)])
        self.assertEqual(code, 1)
        self.assertIn("rollup", err)

    def test_board_filter_through_the_cli(self):
        target = Path(self.tmp.name) / "cli" / "beta"
        code, _, _ = self.run_cli([
            "rollup", "--since", "7d", "--as-of", str(AS_OF), "--board", "beta",
            "--db", str(self.db), "--kanban-home", str(self.kanban),
            "--out", str(target), "-q",
        ])
        self.assertEqual(code, 0)
        payload = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(payload["board"], "beta")
        self.assertEqual(payload["current"]["sessions"], 1)

    def test_plugin_cli_registration_reaches_rollup(self):
        """Exactly what `hermes receipts rollup ...` does after the plugin loads."""
        import argparse

        from receipts.plugin import _setup_cli

        target = Path(self.tmp.name) / "cli" / "plugin-path"
        parser = argparse.ArgumentParser(prog="hermes receipts")
        _setup_cli(parser)
        args = parser.parse_args([
            "rollup", "--since", "7d", "--as-of", str(AS_OF),
            "--db", str(self.db), "--kanban-home", str(self.kanban),
            "--out", str(target), "-q",
        ])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(args.func(args), 0)
        self.assertTrue(target.with_suffix(".html").exists())
        self.assertTrue(target.with_suffix(".json").exists())


class TestArtifact(RollupFixture):
    def test_no_external_resources_or_scripts(self):
        self.assertNotIn("<script", self.html.lower())
        self.assertNotIn("<link", self.html.lower())
        self.assertNotIn("@import", self.html)
        self.assertIsNone(re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', self.html),
                          "artifact must not reference any remote resource")

    def test_all_sections_render(self):
        for heading in ("failure taxonomy", "cost · by model", "cost · by card",
                        "reconciliation", "slowest tools", "retry hotspots", "week over week"):
            self.assertIn(heading, self.html)

    def test_digest_is_stamped(self):
        self.assertIn(self.payload["stability"]["digest"], self.html)
        self.assertIn("Deterministic digest", self.html)

    def test_nothing_dynamic_is_unescaped(self):
        payload = self.build()
        payload["current"]["by_card"][0]["title"] = '<script>alert("x")</script>'
        html = render_rollup(payload)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert", html)


class TestCommittedSample(unittest.TestCase):
    """The committed sample is a claim: it must rebuild from the fixture."""

    def test_sample_digest_matches_a_fresh_build(self):
        sample = ROOT / "samples" / "rollup-demo.json"
        self.assertTrue(sample.exists(), "samples/rollup-demo.json is missing")
        committed = json.loads(sample.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_rollup_db.build(Path(tmp) / "fixture")
            fresh = build_rollup("7d", as_of=str(AS_OF), db_path=paths["state_db"],
                                 kanban_home_path=paths["kanban_home"])
        self.assertEqual(fresh["stability"]["digest"], committed["stability"]["digest"])
        self.assertEqual(fresh["headline"], committed["headline"])
        self.assertEqual(fresh["desk_line"], committed["desk_line"])
        self.assertEqual(fresh["current"]["taxonomy"], committed["current"]["taxonomy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
