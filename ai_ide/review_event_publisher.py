from __future__ import annotations

from ai_ide.events import EventBus
from ai_ide.models import WriteProposal


class ReviewEventPublisher:
    def __init__(self, event_bus: EventBus | None) -> None:
        self.event_bus = event_bus

    def publish_proposal_event(
        self,
        kind: str,
        proposal: WriteProposal,
        payload: dict[str, object] | None = None,
    ) -> None:
        if self.event_bus is None:
            return
        self.event_bus.publish(
            kind,
            source_type="review-proposal",
            source_id=proposal.proposal_id,
            execution_session_id=proposal.session_id,
            payload={
                "target": proposal.target,
                "status": proposal.status,
                "agent_session_id": proposal.agent_session_id,
                **(payload or {}),
            },
        )
