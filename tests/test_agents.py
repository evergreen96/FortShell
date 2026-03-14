from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_ide.agents import AgentRegistry


class AgentRegistryTests(unittest.TestCase):
    def test_default_registry_exposes_expected_agent_kinds(self) -> None:
        registry = AgentRegistry()

        self.assertEqual(["default", "claude", "codex", "gemini", "opencode"], registry.kinds())

    def test_virtual_default_agent_is_reported_as_placeholder(self) -> None:
        registry = AgentRegistry()

        probe = registry.probe("default")

        self.assertFalse(probe.available)
        self.assertEqual("virtual", probe.status_code)
        self.assertEqual("session-placeholder", probe.transport)
        self.assertEqual("session-placeholder", probe.io_mode_preference)

    def test_cli_agent_probe_reports_ready_when_launcher_exists(self) -> None:
        registry = AgentRegistry()

        with patch("ai_ide.agents.shutil.which", return_value="/tmp/codex"):
            probe = registry.probe("codex")

        self.assertTrue(probe.available)
        self.assertEqual("ready", probe.status_code)
        self.assertEqual("/tmp/codex", probe.launcher)

    def test_launch_plan_includes_launcher_when_available(self) -> None:
        registry = AgentRegistry()

        with patch("ai_ide.agents.shutil.which", return_value="/tmp/claude"):
            plan = registry.launch_plan("claude")

        self.assertTrue(plan.available)
        self.assertEqual(["/tmp/claude"], plan.argv)
        self.assertTrue(plan.requires_tty)
        self.assertEqual("pty_preferred", plan.io_mode_preference)


if __name__ == "__main__":
    unittest.main()
