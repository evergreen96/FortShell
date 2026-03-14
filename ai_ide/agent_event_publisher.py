from __future__ import annotations

from ai_ide.events import EventBus
from ai_ide.models import AgentRunRecord


class AgentRuntimeEventPublisher:
    def __init__(self, event_bus: EventBus | None) -> None:
        self.event_bus = event_bus

    def publish_run_event(self, kind: str, record: AgentRunRecord, payload: dict[str, object]) -> None:
        if self.event_bus is None:
            return
        self.event_bus.publish(
            kind,
            source_type="agent-run",
            source_id=record.run_id,
            execution_session_id=record.execution_session_id,
            payload={
                "agent_session_id": record.agent_session_id,
                "agent_kind": record.agent_kind,
                **payload,
            },
        )
