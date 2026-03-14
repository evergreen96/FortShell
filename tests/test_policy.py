from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.models import PolicyState
from core.policy import PolicyEngine


class PolicyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "secrets").mkdir()
        (self.root / "src").mkdir()
        (self.root / "secrets" / "token.txt").write_text("secret", encoding="utf-8")
        (self.root / "src" / "main.py").write_text("print('ok')", encoding="utf-8")
        self.policy = PolicyEngine(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_add_rule_increments_version_once(self) -> None:
        self.assertTrue(self.policy.add_deny_rule("secrets/**"))
        self.assertEqual(2, self.policy.state.version)
        self.assertFalse(self.policy.add_deny_rule("secrets/**"))
        self.assertEqual(2, self.policy.state.version)

    def test_plain_directory_rule_blocks_descendants(self) -> None:
        self.policy.add_deny_rule("secrets")
        self.assertFalse(self.policy.is_allowed(self.root / "secrets"))
        self.assertFalse(self.policy.is_allowed(self.root / "secrets" / "token.txt"))
        self.assertTrue(self.policy.is_allowed(self.root / "src" / "main.py"))

    def test_glob_rule_blocks_nested_paths(self) -> None:
        self.policy.add_deny_rule("src/**")
        self.assertFalse(self.policy.is_allowed(self.root / "src"))
        self.assertFalse(self.policy.is_allowed(self.root / "src" / "main.py"))

    def test_replace_state_restores_rules_and_version(self) -> None:
        self.policy.replace_state(PolicyState(deny_globs=["secrets/**", "src/"], version=7))

        self.assertEqual(["secrets/**", "src/"], self.policy.state.deny_globs)
        self.assertEqual(7, self.policy.state.version)
        self.assertFalse(self.policy.is_allowed(self.root / "secrets" / "token.txt"))
        self.assertFalse(self.policy.is_allowed(self.root / "src" / "main.py"))

    def test_policy_state_mutation_helpers_keep_versions_consistent(self) -> None:
        state = PolicyState(deny_globs=[])
        self.assertTrue(state.append_deny_glob("secrets/**"))
        self.assertFalse(state.append_deny_glob("secrets/**"))
        self.assertEqual(2, state.version)
        self.assertTrue(state.remove_deny_glob("secrets/**"))
        self.assertFalse(state.remove_deny_glob("secrets/**"))
        self.assertEqual(3, state.version)
        state.replace_rules(["src/**"], 0)
        self.assertEqual(["src/**"], state.deny_globs)
        self.assertEqual(1, state.version)


    def test_denied_paths_inside_root_are_blocked(self) -> None:
        self.policy.add_deny_rule("secrets/**")

        self.assertFalse(self.policy.is_allowed(self.root / "secrets" / "token.txt"))
        self.assertTrue(self.policy.is_allowed(self.root / "src" / "main.py"))

        denied = self.policy.evaluate(self.root / "secrets" / "token.txt")
        self.assertFalse(denied.allowed)
        self.assertEqual("secrets/**", denied.matched_rule)

        allowed = self.policy.evaluate(self.root / "src" / "main.py")
        self.assertTrue(allowed.allowed)
        self.assertIsNone(allowed.matched_rule)


if __name__ == "__main__":
    unittest.main()
