from __future__ import annotations

import unittest

from ai_ide.agent_event_publisher import AgentRuntimeEventPublisher
from ai_ide.events import EventBus
from ai_ide.models import AgentRunRecord


class AgentRuntimeEventPublisherTests(unittest.TestCase):
    def test_publish_run_event_writes_expected_agent_run_event(self) -> None:
        events = EventBus()
        publisher = AgentRuntimeEventPublisher(events)
        record = AgentRunRecord(
            run_id="run-1234",
            agent_session_id="agent-1234",
            execution_session_id="exec-1234",
            agent_kind="codex",
            runner_mode="projected",
            backend="projected",
            io_mode="pipe",
            transport_status="degraded",
            argv=["codex"],
            created_at="2026-03-07T00:00:00Z",
            ended_at=None,
            pid=1234,
            returncode=-1,
            status="running",
            stdout="",
            stderr="",
        )

        publisher.publish_run_event("agent.run.started", record, {"pid": 1234})
        event = events.list_events(limit=1)[0]

        self.assertEqual("agent.run.started", event.kind)
        self.assertEqual("agent-run", event.source_type)
        self.assertEqual("run-1234", event.source_id)
        self.assertEqual("exec-1234", event.execution_session_id)
        self.assertEqual("agent-1234", event.payload["agent_session_id"])
        self.assertEqual("codex", event.payload["agent_kind"])
        self.assertEqual(1234, event.payload["pid"])

    def test_publish_run_event_is_noop_without_event_bus(self) -> None:
        publisher = AgentRuntimeEventPublisher(None)
        record = AgentRunRecord(
            run_id="run-5678",
            agent_session_id="agent-5678",
            execution_session_id="exec-5678",
            agent_kind="codex",
            runner_mode="strict",
            backend="strict-preview",
            io_mode="pipe",
            transport_status="degraded",
            argv=["codex"],
            created_at="2026-03-07T00:00:00Z",
            ended_at=None,
            pid=None,
            returncode=-1,
            status="running",
            stdout="",
            stderr="",
        )

        publisher.publish_run_event("agent.run.started", record, {"pid": None})


if __name__ == "__main__":
    unittest.main()
