from __future__ import annotations

import unittest

from backend.events import EventBus
from core.models import WriteProposal
from backend.review_event_publisher import ReviewEventPublisher


class ReviewEventPublisherTests(unittest.TestCase):
    def test_publish_proposal_event_writes_expected_review_event(self) -> None:
        events = EventBus()
        publisher = ReviewEventPublisher(events)
        proposal = WriteProposal(
            proposal_id="rev-1234",
            target="src/app.py",
            session_id="sess-1234",
            agent_session_id="agent-1234",
            created_at="2026-03-07T00:00:00Z",
            updated_at="2026-03-07T00:00:01Z",
            status="pending",
            base_sha256="abc",
            base_text="old\n",
            proposed_text="new\n",
        )

        publisher.publish_proposal_event("review.proposal.staged", proposal, {"source": "ai.write"})
        event = events.list_events(limit=1)[0]

        self.assertEqual("review.proposal.staged", event.kind)
        self.assertEqual("review-proposal", event.source_type)
        self.assertEqual("rev-1234", event.source_id)
        self.assertEqual("sess-1234", event.execution_session_id)
        self.assertEqual("src/app.py", event.payload["target"])
        self.assertEqual("pending", event.payload["status"])
        self.assertEqual("agent-1234", event.payload["agent_session_id"])
        self.assertEqual("ai.write", event.payload["source"])

    def test_publish_proposal_event_is_noop_without_event_bus(self) -> None:
        publisher = ReviewEventPublisher(None)
        proposal = WriteProposal(
            proposal_id="rev-5678",
            target="src/app.py",
            session_id="sess-5678",
            agent_session_id="agent-5678",
            created_at="2026-03-07T00:00:00Z",
            updated_at="2026-03-07T00:00:01Z",
            status="pending",
            base_sha256=None,
            base_text=None,
            proposed_text="new\n",
        )

        publisher.publish_proposal_event("review.proposal.staged", proposal, {"source": "ai.write"})
