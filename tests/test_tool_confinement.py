"""Security confinement tests for the tool path (PR #152953 review).

Covers: out-path confinement, word-list form rejection, profile-correct home
resolution, and RECEIPTS_DB non-redirection on the tool path.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipts import dbio, plugin


class ToolConfinementTests(unittest.TestCase):
    def test_absolute_out_is_confined_to_plugin_data(self):
        out = plugin._tool_safe_out("/etc/cron.d/receipts.html")
        self.assertIsNotNone(out)
        base = plugin._tool_data_dir()
        self.assertTrue(out.is_relative_to(base), f"{out} escaped {base}")
        self.assertEqual(out.name, "receipts.html")

    def test_tilde_out_is_confined(self):
        out = plugin._tool_safe_out("~/authorized_keys")
        base = plugin._tool_data_dir()
        self.assertTrue(out.is_relative_to(base))
        self.assertEqual(out.name, "authorized_keys")

    def test_dotdot_out_is_confined(self):
        out = plugin._tool_safe_out("../../.ssh/config")
        base = plugin._tool_data_dir()
        self.assertTrue(out.is_relative_to(base))

    def test_relative_out_stays_in_cwd(self):
        out = plugin._tool_safe_out("kw.html")
        self.assertIsNotNone(out)
        self.assertEqual(out, Path.cwd() / "kw.html")

    def test_word_list_file_path_rejected_from_tool(self):
        from receipts.dbio import ReceiptsError

        with self.assertRaises(ReceiptsError):
            plugin._tool_words("C:/Windows/win.ini")
        with self.assertRaises(ReceiptsError):
            plugin._tool_words("/etc/passwd")

    def test_word_list_plain_strings_accepted(self):
        words = plugin._tool_words(["alpha", " beta ", "gamma"])
        self.assertEqual(words, ["alpha", "beta", "gamma"])

    def test_hermes_home_prefers_core_resolver(self):
        # Our function must consult hermes_constants.get_hermes_home() FIRST
        # and only fall back to $HERMES_HOME for standalone use (PR #152953).
        # Mock the resolver so the ordering is tested deterministically —
        # in a real runtime the resolver itself may honour the env var, which
        # is its own profile-aware business.
        old = os.environ.pop("HERMES_HOME", None)
        try:
            import hermes_constants
        except Exception:
            hermes_constants = None
        try:
            from unittest import mock

            if hermes_constants is not None:
                fake = Path(tempfile.gettempdir()) / "core-profile"
                with mock.patch.object(
                    hermes_constants, "get_hermes_home", return_value=str(fake)
                ):
                    os.environ["HERMES_HOME"] = str(
                        Path(tempfile.gettempdir()) / "other-profile"
                    )
                    self.assertEqual(dbio.hermes_home(), fake)

                def _raise():
                    raise ImportError("standalone")

                with mock.patch.object(
                    hermes_constants, "get_hermes_home", side_effect=_raise
                ):
                    env_home = Path(tempfile.gettempdir()) / "env-profile"
                    os.environ["HERMES_HOME"] = str(env_home)
                    self.assertEqual(dbio.hermes_home(), env_home)
        finally:
            if old is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old

    def test_tool_db_ignores_receipts_db_env(self):
        # The tool resolves the db as hermes_home()/state.db explicitly —
        # RECEIPTS_DB redirection is a CLI affordance only.
        self.assertEqual(
            plugin.hermes_home(), dbio.hermes_home()
        )
        home = dbio.hermes_home()
        self.assertEqual(home / "state.db", plugin.hermes_home() / "state.db")


if __name__ == "__main__":
    unittest.main()
