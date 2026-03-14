from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from ai_ide.agents import AgentAdapter, AgentRegistry
from ai_ide.agent_transport import AgentTransportPlanner


class AgentTransportPlannerTests(unittest.TestCase):
    def test_describe_marks_pty_preferred_adapter_as_degraded_pipe(self) -> None:
        planner = AgentTransportPlanner(AgentRegistry([AgentAdapter("codex", "Codex CLI", ("codex",))]))

        with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
            plan = planner.describe("codex", "strict")

        self.assertTrue(plan.adapter_available)
        self.assertEqual("pty_preferred", plan.requested_io_mode)
        self.assertEqual("pipe", plan.resolved_io_mode)
        self.assertEqual("degraded", plan.transport_status)
        self.assertEqual("pipe-only", plan.provider_name)
        self.assertFalse(plan.supports_pty)
        self.assertTrue(plan.launchable)

    def test_describe_keeps_pty_required_adapter_unlaunchable(self) -> None:
        planner = AgentTransportPlanner(
            AgentRegistry([AgentAdapter("pty-only", "PTY Only", ("pty-only",), io_mode_preference="pty_required")])
        )

        with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
            plan = planner.describe("pty-only", "projected")

        self.assertEqual("pty_required", plan.requested_io_mode)
        self.assertEqual("none", plan.resolved_io_mode)
        self.assertEqual("unavailable", plan.transport_status)
        self.assertEqual("pipe-only", plan.provider_name)
        self.assertFalse(plan.supports_pty)
        self.assertFalse(plan.launchable)


if __name__ == "__main__":
    unittest.main()
