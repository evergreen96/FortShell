from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app import AIIdeApp


class PolicyCommandTests(unittest.TestCase):
    def test_policy_show_json_exposes_machine_readable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            runtime_root = Path(temp_dir) / "runtime"
            root.mkdir()

            app = AIIdeApp(root, runtime_root=runtime_root)
            shown = json.loads(app.handle_command("policy show json"))

            self.assertEqual("policy", shown["kind"])
            self.assertEqual([], shown["deny_globs"])
            self.assertEqual(1, shown["version"])
            self.assertEqual(app.sessions.current_session_id, shown["execution_session_id"])
            self.assertEqual(app.sessions.current_agent_session_id, shown["agent_session_id"])

    def test_policy_add_and_remove_json_return_structured_change_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            runtime_root = Path(temp_dir) / "runtime"
            root.mkdir()

            app = AIIdeApp(root, runtime_root=runtime_root)

            added = json.loads(app.handle_command("policy add secrets/** json"))
            removed = json.loads(app.handle_command("policy remove secrets/** json"))

            self.assertTrue(added["changed"])
            self.assertEqual("add", added["action"])
            self.assertEqual("secrets/**", added["rule"])
            self.assertEqual(2, added["policy_version"])
            self.assertIn("execution_session_id", added)
            self.assertIn("agent_session_id", added)

            self.assertTrue(removed["changed"])
            self.assertEqual("remove", removed["action"])
            self.assertEqual("secrets/**", removed["rule"])
            self.assertEqual(3, removed["policy_version"])


if __name__ == "__main__":
    unittest.main()
