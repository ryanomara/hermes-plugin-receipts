"""Acceptance tests for the redaction engine (RECEIPTS-1.2 card 1.2-a).

The contract these tests pin down:

* one seeded literal of every class is removed; none survives in the JSON dump
  of the redacted manifest or of the report;
* placeholders are numbered per class in document order;
* the report's totals equal the number of placeholders introduced;
* user paths keep their structural prefix;
* a longer secret is never consumed by a shorter class;
* the same input produces byte-identical output — in this process and in a
  fresh interpreter with a different hash seed.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from receipts import privacy

REPO_ROOT = Path(__file__).resolve().parents[1]

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
    "KUpRKfFLfRYC9AIKjbJTWit+CqvjWYzvQwECAwEAAQJAIJLixBy2qpFoS4DSmoEm\n"
    "-----END RSA PRIVATE KEY-----"
)

GH_PAT = "ghp_EXAMPLE0000000000000000000000000000"
SK_KEY = "sk-proj-EXAMPLEaaaaaaaaaaaaaaaaaaaa"
SLACK = "xoxb-EXAMPLE-123456789012-AbCdEfGhIjKlMnOpQrStUvWx"
AWS = "AKIAIOSFODNN7EXAMPLE"
URL_PASSWORD = "pa55-c0rrect-horse"
ENV_SECRET = "hunter2-c0rrect-battery"
EMAIL = "client.contact+receipts@example.com"
EMAIL2 = "second.contact@example.org"
PHONE = "+49 151 23456789"
IP = "10.42.7.19"
WORDS = ["Acme Industries", "ACME"]


def seeded_manifest() -> dict:
    """A synthetic manifest with exactly one literal of every class."""
    return {
        "schema_version": 1,
        "title": "Receipt for Acme Industries",
        "source": {"path": r"C:\Users\ryoma\AppData\Local\hermes\state.db"},
        "sessions": [
            {
                "id": "local-0123456789abcdef",
                "turns": [
                    {
                        "index": 0,
                        "args": {
                            "command": (
                                "curl -H 'Authorization: Bearer " + GH_PAT + "' "
                                "https://svc:" + URL_PASSWORD + "@api.example.com/v1"
                            )
                        },
                        "output": (
                            "wrote key:\n" + PEM + "\n"
                            "to /home/ryoma/work/out; mail " + EMAIL + "; "
                            "call " + PHONE + "; host " + IP
                        ),
                    },
                    {
                        "index": 1,
                        "args": {
                            "command": "OPENAI_API_KEY=" + SK_KEY + " python run.py"
                        },
                        "output": (
                            "slack " + SLACK + " aws " + AWS + " "
                            "token " + JWT
                        ),
                    },
                ],
            }
        ],
        "contact": {
            EMAIL2: {"note": "second address, same client Acme Industries"},
        },
    }


SEEDED_LITERALS = [
    JWT, GH_PAT, SK_KEY, SLACK, AWS, URL_PASSWORD, ENV_SECRET, EMAIL, EMAIL2,
    PHONE, IP, r"C:\Users\ryoma", "/home/ryoma", "Acme Industries", PEM,
]


class RedactionAcceptance(unittest.TestCase):
    def test_every_class_is_removed_and_none_survives(self):
        redacted, report = privacy.redact_manifest(seeded_manifest(), WORDS)
        dump = privacy.canonical_json(redacted)
        self.assertEqual(privacy.leaks(redacted, SEEDED_LITERALS), [], dump)
        self.assertEqual(privacy.leaks(report, SEEDED_LITERALS), [], privacy.canonical_json(report))
        seen = privacy.count_placeholders(redacted)
        for cls in ("secret", "email", "phone", "ip", "user", "word"):
            self.assertGreater(seen.get(cls, 0), 0, "class %r did not fire: %r" % (cls, seen))
        self.assertEqual(report["residual"], {})
        self.assertIsNone(report["generated_at"])

    def test_report_totals_equal_placeholders_introduced(self):
        redacted, report = privacy.redact_manifest(seeded_manifest(), WORDS)
        counts = privacy.count_placeholders(redacted)
        self.assertEqual(counts, report["classes"])
        self.assertEqual(report["total"], sum(counts.values()))
        self.assertEqual(report["total"], sum(report["rules"].values()))
        self.assertEqual(
            report["total"],
            sum(sum(bucket.values()) for bucket in report["fields"].values()),
        )

    def test_same_input_twice_is_byte_identical(self):
        first, report_one = privacy.redact_manifest(seeded_manifest(), WORDS)
        second, report_two = privacy.redact_manifest(seeded_manifest(), WORDS)
        self.assertEqual(privacy.canonical_json(first), privacy.canonical_json(second))
        self.assertEqual(privacy.canonical_json(report_one), privacy.canonical_json(report_two))

    def test_deterministic_across_interpreter_hash_seeds(self):
        manifest = seeded_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "manifest.json"
            fixture.write_text(json.dumps(manifest), encoding="utf-8")
            wordfile = Path(tmp) / "words.txt"
            wordfile.write_text("\n".join(WORDS) + "\n", encoding="utf-8")
            script = (
                "import hashlib, json, sys\n"
                "from receipts.privacy import canonical_json, redact_manifest\n"
                "manifest = json.load(open(sys.argv[1], encoding='utf-8'))\n"
                "words = [w for w in open(sys.argv[2], encoding='utf-8').read().splitlines() if w]\n"
                "redacted, report = redact_manifest(manifest, words)\n"
                "print(hashlib.sha256((canonical_json(redacted) + canonical_json(report)).encode()).hexdigest())\n"
            )
            digests = []
            for seed in ("0", "1"):
                env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(REPO_ROOT))
                result = subprocess.run(
                    [sys.executable, "-c", script, str(fixture), str(wordfile)],
                    cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=True,
                )
                digests.append(result.stdout.strip())
        self.assertEqual(digests[0], digests[1])

    def test_numbering_is_document_order(self):
        manifest = {
            "first": "api " + SK_KEY,
            "second": "mail " + EMAIL + " then " + EMAIL2,
            "third": PEM,
        }
        redacted, report = privacy.redact_manifest(manifest)
        # an API key before a PEM gets secret-1 even though the PEM rule has
        # higher priority — numbering follows the document, not the rule list
        self.assertIn("[secret-1]", redacted["first"])
        self.assertIn("[secret-2]", redacted["third"])
        self.assertEqual(
            [redacted["second"].index(tok) for tok in ("[email-1]", "[email-2]")],
            sorted([redacted["second"].index(tok) for tok in ("[email-1]", "[email-2]")]),
        )
        self.assertEqual(report["total"], 4)

    def test_user_paths_keep_their_structural_prefix(self):
        cases = [
            (r"C:\Users\ryoma\AppData\Local\hermes\state.db",
             r"C:\Users\[user]\AppData\Local\hermes\state.db"),
            (r"C:\\Users\\ryoma\\out.txt", r"C:\\Users\\[user]\\out.txt"),
            ("C:/Users/ryoma/Projects/x", "C:/Users/[user]/Projects/x"),
            ("/home/ryoma/projects/app.env", "/home/[user]/projects/app.env"),
            ("/Users/ryoma/Library/Preferences/x", "/Users/[user]/Library/Preferences/x"),
            ("/c/Users/ryoma/Projects/x", "/c/Users/[user]/Projects/x"),
            (r"\\build01\Users\ryoma\out", r"\\build01\Users\[user]\out"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                out, report = privacy.redact_text(text)
                self.assertEqual(out, expected)
                self.assertEqual(report["classes"], {"user": 1})

    def test_no_shorter_class_consumes_a_longer_secret(self):
        # URL credentials must win over e-mail, and keep the scheme readable
        out, report = privacy.redact_text(
            "postgres://svc:" + URL_PASSWORD + "@db.example.com:5432/app"
        )
        self.assertEqual(out, "postgres://[secret-1]@db.example.com:5432/app")
        self.assertEqual(report["classes"], {"secret": 1})

        # an authorization header keeps its scheme; the JWT is not split up
        out, _ = privacy.redact_text("Authorization: Bearer " + JWT)
        self.assertEqual(out, "Authorization: Bearer [secret-1]")
        self.assertNotIn("dozjg", out)

        # a JWT never gets eaten by the API-key, e-mail or phone rules
        out, report = privacy.redact_text("token " + JWT)
        self.assertEqual(out, "token [secret-1]")
        self.assertEqual(report["rules"], {"jwt": 1})

        # env assignments keep the key name
        out, _ = privacy.redact_text("OPENAI_API_KEY=" + SK_KEY)
        self.assertEqual(out, "OPENAI_API_KEY=[secret-1]")

        # a provider key is one secret, not a phone fragment
        out, report = privacy.redact_text("slack " + SLACK + " end")
        self.assertEqual(out, "slack [secret-1] end")
        self.assertEqual(report["classes"], {"secret": 1})

    def test_idempotent_second_pass_changes_nothing(self):
        redacted, _ = privacy.redact_manifest(seeded_manifest(), WORDS)
        again, report = privacy.redact_manifest(redacted, WORDS)
        self.assertEqual(privacy.canonical_json(again), privacy.canonical_json(redacted))
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["residual"], {})

    def test_word_list_is_case_insensitive_and_longest_first(self):
        out, report = privacy.redact_manifest(
            {"a": "ACME Industries and Acme and acme industries"}, WORDS
        )
        self.assertEqual(out["a"], "[word-1] and [word-2] and [word-3]")
        self.assertEqual(report["classes"], {"word": 3})

    def test_word_matches_after_an_escaped_newline(self):
        # JSON-in-a-command text: the "n" of \n must not hide the client name
        out, report = privacy.redact_manifest({"x": "names\\nAcme\\nend"}, ["Acme"])
        self.assertEqual(out["x"], "names\\n[word-1]\\nend")
        self.assertEqual(report["classes"], {"word": 1})

    def test_word_file_loading_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "words.txt"
            path.write_text("# client names\nAcme\n\nAcme Industries\nacme\n", encoding="utf-8")
            words = privacy.load_words(path)
        self.assertEqual(words, ["Acme Industries", "Acme"])

    def test_fields_breakdown_names_the_field(self):
        redacted, report = privacy.redact_manifest(seeded_manifest(), WORDS)
        self.assertEqual(report["fields"][r"$.source.path"], {"user": 1})
        self.assertEqual(report["fields"]["$.contact"], {"email": 1})
        for field, counts in report["fields"].items():
            self.assertTrue(field.startswith("$"), field)
            self.assertTrue(counts)

    def test_dict_keys_are_redacted(self):
        redacted, report = privacy.redact_manifest({EMAIL: "value"})
        self.assertEqual(list(redacted), ["[email-1]"])
        self.assertEqual(redacted["[email-1]"], "value")
        self.assertEqual(report["fields"]["$"], {"email": 1})
        self.assertEqual(privacy.leaks(redacted, [EMAIL]), [])

    def test_key_collision_keeps_both_values(self):
        redacted, report = privacy.redact_manifest(
            {r"C:\Users\alice\x": 1, r"C:\Users\bob\x": 2}
        )
        self.assertEqual(len(redacted), 2)
        self.assertEqual(sorted(redacted.values()), [1, 2])
        self.assertTrue(any(key.endswith("#2") for key in redacted), redacted)
        self.assertEqual(report["total"], 2)

    def test_unterminated_private_key_is_removed(self):
        out, report = privacy.redact_text("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n")
        self.assertEqual(out, "[secret-1]")
        self.assertEqual(report["rules"], {"private_key_open": 1})

    def test_invalid_ipv4_is_not_an_address(self):
        for text in ("999.999.999.999", "1.2.3.4.5", "2026-11-07T00:03:55+01:00"):
            with self.subTest(text=text):
                out, _ = privacy.redact_text(text)
                self.assertEqual(out, text)

    def test_phone_formats(self):
        numbers = ["+49 151 23456789", "(415) 555-0132", "415-555-0132", "0151 23456789"]
        for number in numbers:
            with self.subTest(number=number):
                out, report = privacy.redact_text("call " + number + " now")
                self.assertEqual(out, "call [phone-1] now")
                self.assertEqual(report["classes"], {"phone": 1})
        for non_phone in ("2026-11-07", "0.011236296", "4294967295", "1.2.3"):
            with self.subTest(non_phone=non_phone):
                out, report = privacy.redact_text(non_phone)
                self.assertNotIn("phone", report["classes"])
                self.assertEqual(out, non_phone)

    def test_env_assignments_only_redact_secret_values(self):
        cases = [
            ('export DB_PASSWORD="' + ENV_SECRET + '" --flag',
             'export DB_PASSWORD="[secret-1]" --flag'),
            ("AUTH_TIMEOUT=30", "AUTH_TIMEOUT=30"),
            ("TOKEN=$CI_JOB_TOKEN", "TOKEN=$CI_JOB_TOKEN"),
            ("DEBUG=true", "DEBUG=true"),
            ("PATH=C:/Users/ryoma/bin", "PATH=C:/Users/[user]/bin"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                out, _ = privacy.redact_text(text)
                self.assertEqual(out, expected)

    def test_query_string_secrets(self):
        out, report = privacy.redact_text("GET https://x.example/cb?token=abc123def&x=1")
        self.assertEqual(out, "GET https://x.example/cb?token=[secret-1]&x=1")
        self.assertEqual(report["classes"], {"secret": 1})

    def test_url_credentials_without_a_username(self):
        out, report = privacy.redact_text("redis://:s3cr3t-pass@cache.internal:6379/0")
        self.assertEqual(out, "redis://[secret-1]@cache.internal:6379/0")
        self.assertEqual(report["classes"], {"secret": 1})

    def test_clean_text_is_untouched(self):
        clean = "pytest -q tests/ && git status --short"
        out, report = privacy.redact_text(clean)
        self.assertEqual(out, clean)
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["classes"], {})

    def test_bare_bearer_prose_is_not_a_secret(self):
        text = "the bearer authentication scheme is documented"
        out, report = privacy.redact_text(text)
        self.assertEqual(out, text)
        self.assertEqual(report["total"], 0)

    def test_deep_walk_covers_lists_and_bare_strings(self):
        payload = ["mail " + EMAIL, {"deep": ["and " + EMAIL2]}]
        original = json.dumps(payload)
        redacted, report = privacy.redact_manifest(payload)
        self.assertEqual(redacted, ["mail [email-1]", {"deep": ["and [email-2]"]}])
        self.assertEqual(report["total"], 2)
        # the input is not mutated
        self.assertEqual(json.dumps(payload), original)

    def test_non_strings_pass_through(self):
        redacted, report = privacy.redact_manifest(
            {"tokens": 123456789, "ratio": 0.5, "ok": True, "none": None}
        )
        self.assertEqual(redacted, {"tokens": 123456789, "ratio": 0.5, "ok": True, "none": None})
        self.assertEqual(report["total"], 0)

    def test_words_report_counts_entries_not_values(self):
        _, report = privacy.redact_manifest({"x": "Acme Industries"}, WORDS)
        self.assertEqual(report["words"], len(WORDS))

    def test_empty_word_entries_are_ignored(self):
        _, report = privacy.redact_manifest({"x": "Acme"}, ["", None, "Acme", ""])
        self.assertEqual(report["words"], 1)
        self.assertEqual(report["classes"], {"word": 1})


# --------------------------------------------------------------------------- #
# Rule-level coverage (added with RECEIPTS-1.2 card 1.2-e)
# --------------------------------------------------------------------------- #

#: One sample per registered rule. A rule that stops firing fails its own row;
#: a rule added without a sample fails ``test_every_registered_rule_fires``.
#: ``placeholder`` is protected (tested through idempotence) and ``word`` is
#: built per pass from the list, so neither appears here.
RULE_SAMPLES = (
    ("private_key", PEM),
    ("private_key_open", "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3NzaC1yc2EAAAADAQABAAABgQDfixture"),
    ("url_creds", "postgres://svc:" + URL_PASSWORD + "@db.example.com:5432/app"),
    ("url_query_secret", "https://x.example/cb?token=abc123def&x=1"),
    ("jwt", JWT),
    ("api_key", GH_PAT),
    ("auth_scheme", "Bearer abcd1234efgh5678"),
    ("authorization", "Authorization: abcd1234efgh5678"),
    ("env_secret", "DB_PASSWORD=" + ENV_SECRET),
    ("win_user_path", r"C:\Users\fixture\out.txt"),
    ("posix_user_path", "/home/fixture/out.txt"),
    ("msys_user_path", "/c/Users/fixture/out.txt"),
    ("unc_user_path", r"\\build01\Users\fixture\out.txt"),
    ("email", EMAIL),
    ("ipv4", IP),
    ("phone", PHONE),
)


class RuleCoverage(unittest.TestCase):
    def test_every_registered_rule_fires(self):
        registered = {rule.name for rule in privacy.RULES if not rule.protected}
        self.assertEqual(registered, {name for name, _ in RULE_SAMPLES},
                         "every rule needs a sample here, and every sample a rule")
        classes = {rule.name: rule.cls for rule in privacy.RULES if not rule.protected}
        for name, sample in RULE_SAMPLES:
            with self.subTest(rule=name):
                out, report = privacy.redact_text(sample)
                self.assertGreaterEqual(report["rules"].get(name, 0), 1,
                                        "rule %r did not fire: %r" % (name, report["rules"]))
                self.assertIn(classes[name], report["classes"])
                self.assertNotIn(sample, out,
                                 "the sample survived its own rule: %r" % (out,))

    def test_word_rule_is_claimed_last_after_every_secret_rule(self):
        # The word list is a convenience, never a licence to eat a credential.
        out, report = privacy.redact_text("aws " + AWS, [AWS])
        self.assertEqual(out, "aws [secret-1]")
        self.assertEqual(report["classes"], {"secret": 1})
        self.assertEqual(report["words"], 1)

    def test_word_matching_is_whole_word_only(self):
        out, report = privacy.redact_text("Acmecorp Acme Acme.", ["Acme"])
        self.assertEqual(out, "Acmecorp [word-1] [word-2].")
        self.assertEqual(report["classes"], {"word": 2})

    def test_word_matching_ignores_case_in_both_directions(self):
        out, _ = privacy.redact_text("ACME INDUSTRIES shipped", ["acme industries"])
        self.assertEqual(out, "[word-1] shipped")
        out, _ = privacy.redact_text("shipped acme", ["ACME"])
        self.assertEqual(out, "shipped [word-1]")

    def test_word_rule_never_eats_a_placeholder(self):
        out, report = privacy.redact_text("already [secret-1] here", ["secret"])
        self.assertEqual(out, "already [secret-1] here")
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["residual"], {})

    def test_report_classes_follow_the_family_order(self):
        _, report = privacy.redact_manifest(seeded_manifest(), WORDS)
        self.assertEqual(list(report["classes"]),
                         [cls for cls in privacy.CLASSES if cls in report["classes"]])
        self.assertEqual(tuple(report["classes"]), privacy.CLASSES)

    def test_report_has_no_zero_counts(self):
        for payload, words in ((seeded_manifest(), WORDS), ({"clean": "nothing here"}, None)):
            _, report = privacy.redact_manifest(payload, words)
            for bucket in ("classes", "rules"):
                for name, count in report[bucket].items():
                    with self.subTest(bucket=bucket, name=name):
                        self.assertGreater(count, 0)
            for field, counts in report["fields"].items():
                with self.subTest(field=field):
                    self.assertTrue(counts)
                    self.assertGreater(sum(counts.values()), 0)

    def test_field_breakdown_sums_to_the_class_totals(self):
        _, report = privacy.redact_manifest(seeded_manifest(), WORDS)
        per_class: dict[str, int] = {}
        for counts in report["fields"].values():
            for cls, count in counts.items():
                per_class[cls] = per_class.get(cls, 0) + count
        self.assertEqual(per_class, report["classes"])
        self.assertEqual(report["version"], privacy.REPORT_VERSION)

    def test_residual_flags_a_leak_that_bypassed_the_pass(self):
        redactor = privacy.Redactor()
        self.assertEqual(redactor.residual({"note": "mail " + EMAIL + " now"}), {"email": 1})
        self.assertEqual(redactor.residual({"note": "already [email-1]"}), {})

    def test_leaks_reports_each_surviving_literal(self):
        self.assertEqual(privacy.leaks({"a": EMAIL}, [EMAIL, "not-present"]), [EMAIL])
        self.assertEqual(privacy.leaks({"a": "[email-1]"}, [EMAIL]), [])


if __name__ == "__main__":
    unittest.main()
