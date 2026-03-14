from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.models import AgentSession, ExecutionSession
from core.policy import PolicyEngine
from backend.session import (
    MAX_AGENT_SESSION_HISTORY,
    MAX_EXECUTION_SESSION_HISTORY,
    SessionManager,
)


class SessionManagerTests(unittest.TestCase):
    def test_execution_session_rotates_when_policy_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = PolicyEngine(Path(temp_dir))
            sessions = SessionManager(policy)
            previous_execution = sessions.current_session_id
            previous_agent = sessions.current_agent_session_id

            policy.add_deny_rule("secrets/**")

            self.assertTrue(sessions.ensure_fresh_execution_session())
            self.assertNotEqual(previous_execution, sessions.current_session_id)
            self.assertNotEqual(previous_agent, sessions.current_agent_session_id)
            self.assertEqual(policy.state.version, sessions.policy_version)
            self.assertEqual("stale", sessions.execution_sessions[0].status)
            self.assertEqual("stale", sessions.agent_sessions[0].status)

    def test_execution_session_rotation_preserves_agent_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = PolicyEngine(Path(temp_dir))
            sessions = SessionManager(policy)
            sessions.rotate_agent_session("codex")

            policy.add_deny_rule("secrets/**")
            sessions.ensure_fresh_execution_session()

            self.assertEqual("codex", sessions.current_agent_session.agent_kind)

    def test_execution_session_does_not_rotate_without_policy_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = PolicyEngine(Path(temp_dir))
            sessions = SessionManager(policy)

            self.assertFalse(sessions.ensure_fresh_execution_session())

    def test_is_current_execution_session_tracks_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = PolicyEngine(Path(temp_dir))
            sessions = SessionManager(policy)
            original_session = sessions.current_session_id

            self.assertTrue(sessions.is_current_execution_session(original_session))
            policy.add_deny_rule("secrets/**")
            sessions.ensure_fresh_execution_session()

            self.assertFalse(sessions.is_current_execution_session(original_session))
            self.assertTrue(sessions.is_current_execution_session(sessions.current_session_id))

    def test_agent_rotation_keeps_execution_session_and_stales_previous_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = PolicyEngine(Path(temp_dir))
            sessions = SessionManager(policy)
            previous_agent = sessions.current_agent_session

            rotated_agent = sessions.rotate_agent_session("claude")

            self.assertEqual(sessions.current_session_id, rotated_agent.execution_session_id)
            self.assertEqual("claude", rotated_agent.agent_kind)
            self.assertEqual("stale", previous_agent.status)
            self.assertEqual("active", rotated_agent.status)

    def test_sync_remote_sessions_adopts_remote_ids_and_marks_previous_sessions_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = PolicyEngine(Path(temp_dir))
            sessions = SessionManager(policy)
            original_execution = sessions.current_execution_session
            original_agent = sessions.current_agent_session

            result = sessions.sync_remote_sessions(
                ExecutionSession(
                    session_id="rust-sess-0002",
                    policy_version=3,
                    created_at="2026-03-07T00:00:10Z",
                    status="active",
                    rotated_from=original_execution.session_id,
                ),
                AgentSession(
                    agent_session_id="rust-agent-0002",
                    execution_session_id="rust-sess-0002",
                    agent_kind="codex",
                    created_at="2026-03-07T00:00:11Z",
                    status="active",
                    rotated_from=original_agent.agent_session_id,
                ),
            )

            self.assertTrue(result.execution_changed)
            self.assertTrue(result.agent_changed)
            self.assertEqual("stale", original_execution.status)
            self.assertEqual("stale", original_agent.status)
            self.assertEqual("rust-sess-0002", sessions.current_session_id)
            self.assertEqual("rust-agent-0002", sessions.current_agent_session_id)
            self.assertEqual("codex", sessions.current_agent_session.agent_kind)

    def test_execution_session_history_is_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = PolicyEngine(Path(temp_dir))
            sessions = SessionManager(policy)

            for index in range(MAX_EXECUTION_SESSION_HISTORY + 2):
                policy.add_deny_rule(f"rule-{index}")
                sessions.ensure_fresh_execution_session()

            self.assertEqual(MAX_EXECUTION_SESSION_HISTORY, len(sessions.execution_sessions))

    def test_agent_session_history_is_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = PolicyEngine(Path(temp_dir))
            sessions = SessionManager(policy)

            for index in range(MAX_AGENT_SESSION_HISTORY + 2):
                sessions.rotate_agent_session(f"agent-{index}")

            self.assertEqual(MAX_AGENT_SESSION_HISTORY, len(sessions.agent_sessions))


if __name__ == "__main__":
    unittest.main()
