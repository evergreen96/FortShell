from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_ide.agent_launch_coordinator import AgentLaunchCoordinator
from ai_ide.agents import AgentAdapter, AgentRegistry
from ai_ide.agent_transport import AgentTransportPlanner
from ai_ide.models import AgentSession


class AgentLaunchCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = AgentSession(
            agent_session_id="agent-1234",
            execution_session_id="sess-1234",
            agent_kind="codex",
            created_at="2026-03-07T00:00:00Z",
            status="active",
        )

    def test_prepare_builds_launch_argv_and_env_for_available_adapter(self) -> None:
        registry = AgentRegistry([AgentAdapter("codex", "Codex CLI", ("codex",))])
        coordinator = AgentLaunchCoordinator(
            registry,
            AgentTransportPlanner(registry),
            lambda: "projected",
        )

        with patch("ai_ide.agents.shutil.which", return_value="/tmp/codex"):
            prepared = coordinator.prepare(self.session)

        self.assertTrue(prepared.launchable)
        self.assertEqual("projected", prepared.runner_mode)
        self.assertEqual(["/tmp/codex", "--version"], prepared.argv)
        self.assertEqual("pipe", prepared.transport.resolved_io_mode)
        self.assertEqual("degraded", prepared.transport.transport_status)
        self.assertEqual("agent-1234", prepared.env["AI_IDE_AGENT_SESSION_ID"])
        self.assertEqual("sess-1234", prepared.env["AI_IDE_EXECUTION_SESSION_ID"])
        self.assertEqual("codex", prepared.env["AI_IDE_AGENT_KIND"])

    def test_prepare_marks_adapter_missing_as_blocked_launch(self) -> None:
        registry = AgentRegistry([AgentAdapter("codex", "Codex CLI", ("codex",))])
        coordinator = AgentLaunchCoordinator(
            registry,
            AgentTransportPlanner(registry),
            lambda: "projected",
        )

        with patch("ai_ide.agents.shutil.which", return_value=None):
            prepared = coordinator.prepare(self.session, extra_args=["--version"])

        self.assertFalse(prepared.launchable)
        self.assertIsNotNone(prepared.block)
        assert prepared.block is not None
        result = prepared.block.to_runner_result(prepared.runner_mode)
        self.assertEqual("agent-adapter", result.backend)
        self.assertEqual(127, result.returncode)
        self.assertIn("unavailable:", result.stderr)

    def test_prepare_marks_pty_required_adapter_as_transport_unavailable(self) -> None:
        registry = AgentRegistry(
            [AgentAdapter("codex", "Codex CLI", ("codex",), io_mode_preference="pty_required")]
        )
        coordinator = AgentLaunchCoordinator(
            registry,
            AgentTransportPlanner(registry),
            lambda: "strict",
        )

        with patch("ai_ide.agents.shutil.which", return_value="/tmp/codex"):
            prepared = coordinator.prepare(self.session)

        self.assertFalse(prepared.launchable)
        self.assertEqual("none", prepared.transport.resolved_io_mode)
        self.assertIsNotNone(prepared.block)
        assert prepared.block is not None
        result = prepared.block.to_runner_result(prepared.runner_mode)
        self.assertEqual("agent-transport", result.backend)
        self.assertEqual(125, result.returncode)
        self.assertIn("unavailable transport", result.stderr)

    def test_prepare_rejects_unsupported_runner_mode(self) -> None:
        registry = AgentRegistry([AgentAdapter("codex", "Codex CLI", ("codex",))])
        coordinator = AgentLaunchCoordinator(
            registry,
            AgentTransportPlanner(registry),
            lambda: "projected",
        )

        with self.assertRaises(ValueError):
            coordinator.prepare(self.session, mode="host")


if __name__ == "__main__":
    unittest.main()
