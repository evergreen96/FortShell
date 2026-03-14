from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.models import WriteProposal
from core.policy import PolicyEngine
from backend.review_manager import MAX_REVIEW_FILE_BYTES, MAX_REVIEW_PROPOSAL_HISTORY, ReviewManager
from backend.review_state_store import ReviewStateStore


class ReviewManagerTests(unittest.TestCase):
    def test_stage_and_apply_round_trip_updates_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")
            manager = ReviewManager(
                root,
                PolicyEngine(root),
                state_store=ReviewStateStore(root / ".runtime" / "reviews.json"),
            )

            proposal = manager.stage_write(
                "src/app.py",
                "print('new')\n",
                session_id="sess-1",
                agent_session_id="agent-1",
            )
            shown = manager.render_proposal(proposal.proposal_id)
            applied = manager.apply_proposal(proposal.proposal_id)

            self.assertEqual("pending", proposal.status)
            self.assertIn("--- a/src/app.py", shown)
            self.assertEqual("applied", applied.status)
            self.assertEqual("print('new')\n", target.read_text(encoding="utf-8"))

    def test_apply_marks_conflict_when_file_changed_after_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")
            manager = ReviewManager(root, PolicyEngine(root))
            proposal = manager.stage_write(
                "src/app.py",
                "print('new')\n",
                session_id="sess-1",
                agent_session_id="agent-1",
            )

            target.write_text("print('changed')\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                manager.apply_proposal(proposal.proposal_id)

            self.assertEqual("conflict", manager.get_proposal(proposal.proposal_id).status)
            self.assertEqual("print('changed')\n", target.read_text(encoding="utf-8"))

    def test_stage_write_rejects_policy_blocked_and_internal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            manager = ReviewManager(root, policy)

            with self.assertRaises(PermissionError):
                manager.stage_write(
                    "secrets/token.txt",
                    "secret\n",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                )

            with self.assertRaises(PermissionError):
                manager.stage_write(
                    ".ai-ide/policy.json",
                    "{}\n",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                )

    def test_stage_write_rejects_oversized_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            target = root / "src" / "large.txt"
            target.write_text("x" * (MAX_REVIEW_FILE_BYTES + 1), encoding="utf-8")
            manager = ReviewManager(root, PolicyEngine(root))

            with self.assertRaisesRegex(ValueError, "File too large to review safely"):
                manager.stage_write(
                    "src/large.txt",
                    "replacement",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                )

    def test_proposal_history_is_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            manager = ReviewManager(root, PolicyEngine(root))

            for index in range(MAX_REVIEW_PROPOSAL_HISTORY + 2):
                manager.stage_write(
                    f"src/file-{index}.txt",
                    f"proposal-{index}",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                )

            self.assertEqual(MAX_REVIEW_PROPOSAL_HISTORY, len(manager.proposals))
            self.assertEqual("src/file-2.txt", manager.proposals[0].target)

    def test_loaded_proposals_drop_denied_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            store = ReviewStateStore(root / ".runtime" / "reviews.json")
            proposals = [
                WriteProposal(
                    proposal_id="valid",
                    target="src/app.py",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                    created_at="2026-03-13T00:00:00Z",
                    updated_at="2026-03-13T00:00:00Z",
                    status="pending",
                    base_sha256=None,
                    base_text=None,
                    proposed_text="ok",
                ),
                WriteProposal(
                    proposal_id="denied",
                    target="secrets/token.txt",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                    created_at="2026-03-13T00:00:00Z",
                    updated_at="2026-03-13T00:00:00Z",
                    status="pending",
                    base_sha256=None,
                    base_text=None,
                    proposed_text="blocked",
                ),
            ]
            store.save(proposals)

            manager = ReviewManager(root, policy, state_store=store)

            self.assertEqual(["valid"], [proposal.proposal_id for proposal in manager.proposals])
