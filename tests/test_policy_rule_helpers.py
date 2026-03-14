from __future__ import annotations

import unittest

from ai_ide.policy_rule_helpers import deny_rule_for_target


class PolicyRuleHelperTests(unittest.TestCase):
    def test_deny_rule_for_file_and_directory_targets(self) -> None:
        self.assertEqual("notes/todo.txt", deny_rule_for_target("notes/todo.txt", is_dir=False))
        self.assertEqual("notes/**", deny_rule_for_target("notes", is_dir=True))
        self.assertEqual("notes/deep/**", deny_rule_for_target(r"notes\deep", is_dir=True))

    def test_deny_rule_rejects_root_and_parent_escape_targets(self) -> None:
        with self.assertRaises(ValueError):
            deny_rule_for_target(".", is_dir=True)
        with self.assertRaises(ValueError):
            deny_rule_for_target("../outside", is_dir=False)
        with self.assertRaises(ValueError):
            deny_rule_for_target("/absolute/path", is_dir=False)


if __name__ == "__main__":
    unittest.main()
