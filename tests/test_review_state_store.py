from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.models import WriteProposal
from ai_ide.review_state_store import ReviewStateStore


class ReviewStateStoreTests(unittest.TestCase):
    def test_load_returns_empty_snapshot_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ReviewStateStore(Path(temp_dir) / "reviews" / "state.json")
            snapshot = store.load()

            self.assertEqual([], snapshot.proposals)

    def test_save_and_load_round_trip_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ReviewStateStore(Path(temp_dir) / "reviews" / "state.json")
            proposals = [
                WriteProposal(
                    proposal_id="rev-1234",
                    target="src/app.py",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                    created_at="2026-03-07T00:00:00Z",
                    updated_at="2026-03-07T00:00:01Z",
                    status="pending",
                    base_sha256="abc",
                    base_text="print('old')\n",
                    proposed_text="print('new')\n",
                )
            ]

            store.save(proposals)
            snapshot = store.load()

            self.assertEqual(proposals, snapshot.proposals)

    def test_load_reuses_cached_snapshot_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ReviewStateStore(Path(temp_dir) / "reviews" / "state.json")
            proposals = [
                WriteProposal(
                    proposal_id="rev-1234",
                    target="src/app.py",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                    created_at="2026-03-07T00:00:00Z",
                    updated_at="2026-03-07T00:00:01Z",
                    status="pending",
                    base_sha256="abc",
                    base_text="print('old')\n",
                    proposed_text="print('new')\n",
                )
            ]

            store.save(proposals)
            first_snapshot = store.load()

            with patch.object(Path, "read_text", side_effect=AssertionError("should reuse cached snapshot")):
                second_snapshot = store.load()

        self.assertEqual(first_snapshot.proposals, second_snapshot.proposals)
