from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.models import AgentSession, AuditEvent, ExecutionSession, PolicyState, WriteProposal
from core.policy import PolicyEngine
from core.policy_state_store import PolicyStateStore
from backend.review_manager import ReviewManager
from backend.review_state_store import ReviewStateStore
from backend.rust_host_client import RustHostRemoteError
from backend.rust_host_control_service import RustHostControlService
from backend.rust_host_protocol import PolicyChangeResult, PolicyChangeSnapshot, RustHostSnapshot
from backend.rust_host_protocol import AgentSessionSnapshot
from backend.rust_host_protocol import RuntimeMetricsSnapshot
from core.workspace_models import (
    WorkspaceCatalogEntry,
    WorkspaceIndexEntry,
    WorkspaceIndexSnapshot,
    WorkspaceSearchMatch,
)


class _FakeRustHostClient:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[dict[str, object], str | None]] = []

    def request_ok(self, payload: dict[str, object], *, expected_type: str | None = None) -> object:
        self.calls.append((payload, expected_type))
        return self.handler(payload, expected_type)


class RustHostControlServiceTests(unittest.TestCase):
    def test_sync_policy_refreshes_snapshot_after_remote_store_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            store = PolicyStateStore(root)
            review_manager = ReviewManager(root, policy, state_store=ReviewStateStore(None))
            synced_snapshots: list[RustHostSnapshot] = []

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                if payload["type"] == "policy_sync":
                    self.assertEqual("policy_change_snapshot", expected_type)
                    store.save(PolicyState(["secrets/**"], version=2))
                    return PolicyChangeSnapshot(
                        result=PolicyChangeResult(True, True, "sess-2", "agent-2", 2),
                        snapshot=RustHostSnapshot(
                            policy_state=store.load(),
                            execution_session=ExecutionSession("sess-2", 2, "2026-03-07T00:00:01Z", "active", "sess-1"),
                            agent_session=AgentSession("agent-2", "sess-2", "default", "2026-03-07T00:00:02Z", "active", "agent-1"),
                            review_count=0,
                            pending_review_count=0,
                        ),
                    )
                raise AssertionError(f"unexpected payload: {payload}")

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=lambda snapshot: (
                    policy.replace_state(snapshot.policy_state),
                    synced_snapshots.append(snapshot),
                ),
            )

            result = service.sync_policy()

            self.assertTrue(result.changed)
            self.assertEqual(["secrets/**"], policy.state.deny_globs)
            self.assertEqual("sess-2", synced_snapshots[0].execution_session.session_id)

    def test_add_deny_rule_syncs_local_policy_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            store = PolicyStateStore(root)
            review_manager = ReviewManager(root, policy, state_store=ReviewStateStore(None))
            synced_snapshots: list[RustHostSnapshot] = []

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                if payload["type"] == "policy_add_deny_rule":
                    self.assertEqual("policy_change_snapshot", expected_type)
                    store.save(PolicyState(["secrets/**"], version=2))
                    return PolicyChangeSnapshot(
                        result=PolicyChangeResult(True, True, "sess-2", "agent-2", 2),
                        snapshot=RustHostSnapshot(
                            policy_state=store.load(),
                            execution_session=ExecutionSession("sess-2", 2, "2026-03-07T00:00:01Z", "active", "sess-1"),
                            agent_session=AgentSession("agent-2", "sess-2", "default", "2026-03-07T00:00:02Z", "active", "agent-1"),
                            review_count=0,
                            pending_review_count=0,
                        ),
                    )
                raise AssertionError(f"unexpected payload: {payload}")

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=lambda snapshot: (
                    policy.replace_state(snapshot.policy_state),
                    synced_snapshots.append(snapshot),
                ),
            )

            result = service.add_deny_rule("secrets/**")

            self.assertTrue(result.changed)
            self.assertEqual(["secrets/**"], policy.state.deny_globs)
            self.assertEqual(2, policy.state.version)
            self.assertEqual("sess-2", synced_snapshots[0].execution_session.session_id)

    def test_rotate_agent_session_syncs_remote_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            review_manager = ReviewManager(root, policy, state_store=ReviewStateStore(None))
            synced_snapshots: list[RustHostSnapshot] = []

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                if payload["type"] == "rotate_agent_session":
                    self.assertEqual("agent_session_snapshot", expected_type)
                    self.assertEqual("codex", payload["agent_kind"])
                    return AgentSessionSnapshot(
                        session=AgentSession("agent-2", "sess-1", "codex", "2026-03-07T00:00:02Z", "active", "agent-1"),
                        snapshot=RustHostSnapshot(
                            policy_state=PolicyState([], version=1),
                            execution_session=ExecutionSession("sess-1", 1, "2026-03-07T00:00:01Z", "active", None),
                            agent_session=AgentSession("agent-2", "sess-1", "codex", "2026-03-07T00:00:02Z", "active", "agent-1"),
                            review_count=0,
                            pending_review_count=0,
                        ),
                    )
                raise AssertionError(f"unexpected payload: {payload}")

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=synced_snapshots.append,
            )

            session = service.rotate_agent_session("codex")

            self.assertEqual("agent-2", session.agent_session_id)
            self.assertEqual("codex", session.agent_kind)
            self.assertEqual("agent-2", synced_snapshots[0].agent_session.agent_session_id)

    def test_workspace_queries_return_typed_results_and_map_permission_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            review_manager = ReviewManager(root, policy, state_store=ReviewStateStore(None))

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                if payload["type"] == "workspace_list":
                    self.assertEqual("workspace_list", expected_type)
                    self.assertEqual("notes", payload["target"])
                    return [WorkspaceCatalogEntry(path="notes/todo.txt", is_dir=False)]
                if payload["type"] == "workspace_tree":
                    self.assertEqual("workspace_tree", expected_type)
                    return [WorkspaceCatalogEntry(path="notes", is_dir=True)]
                if payload["type"] == "workspace_grep":
                    self.assertEqual("workspace_grep", expected_type)
                    if payload["target"] == "notes":
                        return [
                            WorkspaceSearchMatch(
                                path="notes/todo.txt",
                                line_number=1,
                                line_text="visible plan",
                            )
                        ]
                    raise RustHostRemoteError(
                        "workspace_blocked_by_policy",
                        "Blocked by policy: secrets/token.txt",
                    )
                if payload["type"] == "workspace_index_show":
                    self.assertEqual("workspace_index_snapshot", expected_type)
                    return WorkspaceIndexSnapshot(
                        policy_version=2,
                        entries=[
                            WorkspaceIndexEntry(
                                path="notes",
                                is_dir=True,
                                size=0,
                                modified_ns=10,
                            )
                        ],
                    )
                if payload["type"] == "workspace_index_refresh":
                    self.assertEqual("workspace_index_snapshot", expected_type)
                    return WorkspaceIndexSnapshot(
                        policy_version=2,
                        entries=[
                            WorkspaceIndexEntry(
                                path="notes",
                                is_dir=True,
                                size=0,
                                modified_ns=10,
                            ),
                            WorkspaceIndexEntry(
                                path="notes/todo.txt",
                                is_dir=False,
                                size=12,
                                modified_ns=11,
                            ),
                        ],
                    )
                raise AssertionError(f"unexpected payload: {payload}")

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=lambda snapshot: None,
            )

            entries = service.list_workspace("notes")
            self.assertEqual([WorkspaceCatalogEntry(path="notes/todo.txt", is_dir=False)], entries)
            self.assertEqual([WorkspaceCatalogEntry(path="notes", is_dir=True)], service.tree_workspace("notes"))
            self.assertEqual(
                [
                    WorkspaceSearchMatch(
                        path="notes/todo.txt",
                        line_number=1,
                        line_text="visible plan",
                    )
                ],
                service.grep_workspace("plan", "notes"),
            )
            self.assertEqual(
                WorkspaceIndexSnapshot(
                    policy_version=2,
                    entries=[
                        WorkspaceIndexEntry(
                            path="notes",
                            is_dir=True,
                            size=0,
                            modified_ns=10,
                        )
                    ],
                ),
                service.show_workspace_index(),
            )
            self.assertEqual(
                WorkspaceIndexSnapshot(
                    policy_version=2,
                    entries=[
                        WorkspaceIndexEntry(
                            path="notes",
                            is_dir=True,
                            size=0,
                            modified_ns=10,
                        ),
                        WorkspaceIndexEntry(
                            path="notes/todo.txt",
                            is_dir=False,
                            size=12,
                            modified_ns=11,
                        ),
                    ],
                ),
                service.refresh_workspace_index(),
            )

            with self.assertRaises(PermissionError):
                service.grep_workspace("token", "secrets")

    def test_metrics_and_audit_queries_return_typed_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            review_manager = ReviewManager(root, policy, state_store=ReviewStateStore(None))

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                if payload["type"] == "metrics_show":
                    self.assertEqual("runtime_metrics_snapshot", expected_type)
                    return RuntimeMetricsSnapshot(
                        list_count=1,
                        read_count=2,
                        write_count=3,
                        grep_count=4,
                        blocked_count=5,
                        terminal_runs=6,
                        audit_event_count=2,
                    )
                if payload["type"] == "audit_list":
                    self.assertEqual("audit_list", expected_type)
                    self.assertEqual(10, payload["limit"])
                    self.assertFalse(payload["allowed"])
                    return [
                        AuditEvent(
                            timestamp="2026-03-07T00:00:01Z",
                            session_id="sess-1",
                            action="read",
                            target="C:/repo/secret.txt",
                            allowed=False,
                            detail="denied by policy",
                        )
                    ]
                raise AssertionError(f"unexpected payload: {payload}")

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=lambda snapshot: None,
            )

            metrics = service.metrics_snapshot()
            audit = service.list_audit_events(10, allowed=False)

            self.assertEqual(2, metrics.read_count)
            self.assertEqual(2, metrics.audit_event_count)
            self.assertEqual(1, len(audit))
            self.assertFalse(audit[0].allowed)

    def test_alias_guard_errors_map_to_permission_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            review_manager = ReviewManager(root, policy, state_store=ReviewStateStore(None))

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                if payload["type"] == "review_stage_write":
                    raise RustHostRemoteError(
                        "review_hardlink_path",
                        "Blocked hardlink path: safe/token-alias.txt",
                    )
                if payload["type"] == "workspace_tree":
                    raise RustHostRemoteError(
                        "workspace_symlink_path",
                        "Blocked symlink path: safe/link",
                    )
                raise AssertionError(f"unexpected payload: {payload}")

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=lambda snapshot: None,
            )

            with self.assertRaises(PermissionError):
                service.stage_write(
                    "safe/token-alias.txt",
                    "changed\n",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                )
            with self.assertRaises(PermissionError):
                service.tree_workspace("safe/link")

    def test_stage_write_reloads_review_store_and_uses_supplied_session_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            review_store = ReviewStateStore(root / "runtime" / "reviews.json")
            review_manager = ReviewManager(root, policy, state_store=review_store)

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                self.assertEqual("review_proposal", expected_type)
                self.assertEqual("review_stage_write", payload["type"])
                self.assertEqual("sess-1234", payload["session_id"])
                self.assertEqual("agent-1234", payload["agent_session_id"])
                proposal = WriteProposal(
                    proposal_id="rev-1234",
                    target="src/app.py",
                    session_id=str(payload["session_id"]),
                    agent_session_id=str(payload["agent_session_id"]),
                    created_at="2026-03-07T00:00:00Z",
                    updated_at="2026-03-07T00:00:00Z",
                    status="pending",
                    base_sha256="abc123",
                    base_text="print('old')\n",
                    proposed_text="print('new')\n",
                )
                review_store.save([proposal])
                return proposal

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=lambda snapshot: None,
            )

            proposal = service.stage_write(
                "src/app.py",
                "print('new')\n",
                session_id="sess-1234",
                agent_session_id="agent-1234",
            )

            self.assertEqual("rev-1234", proposal.proposal_id)
            self.assertEqual(1, review_manager.count_proposals(status="pending"))
            self.assertEqual("sess-1234", review_manager.get_proposal("rev-1234").session_id)

    def test_apply_conflict_maps_remote_error_and_refreshes_local_review_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            review_store = ReviewStateStore(root / "runtime" / "reviews.json")
            review_manager = ReviewManager(root, policy, state_store=review_store)
            pending = WriteProposal(
                "rev-1234",
                "src/app.py",
                "sess-1",
                "agent-1",
                "2026-03-07T00:00:00Z",
                "2026-03-07T00:00:00Z",
                "pending",
                "abc123",
                "print('old')\n",
                "print('new')\n",
            )
            review_store.save([pending])
            review_manager.replace_proposals([pending])

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                self.assertEqual("review_apply", payload["type"])
                conflicted = WriteProposal(
                    "rev-1234",
                    "src/app.py",
                    "sess-1",
                    "agent-1",
                    "2026-03-07T00:00:00Z",
                    "2026-03-07T00:00:01Z",
                    "conflict",
                    "abc123",
                    "print('old')\n",
                    "print('new')\n",
                )
                review_store.save([conflicted])
                raise RustHostRemoteError(
                    "review_conflict",
                    "Proposal conflicted with current file state: rev-1234",
                )

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=lambda snapshot: None,
            )

            with self.assertRaises(RuntimeError):
                service.apply_proposal("rev-1234")

            self.assertEqual("conflict", review_manager.get_proposal("rev-1234").status)

    def test_render_proposal_returns_rendered_content_from_remote_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = PolicyEngine(root)
            review_store = ReviewStateStore(root / "runtime" / "reviews.json")
            review_manager = ReviewManager(root, policy, state_store=review_store)

            def handle(payload: dict[str, object], expected_type: str | None) -> object:
                self.assertEqual("review_render", expected_type)
                self.assertEqual("review_render", payload["type"])
                self.assertEqual("rev-1234", payload["proposal_id"])

                class _Rendered:
                    proposal_id = "rev-1234"
                    content = "proposal_id=rev-1234\n--- a/src/app.py\n+++ b/src/app.py"

                return _Rendered()

            service = RustHostControlService(
                _FakeRustHostClient(handle),
                review_manager=review_manager,
                sync_snapshot=lambda snapshot: None,
            )

            rendered = service.render_proposal("rev-1234")
            self.assertIn("--- a/src/app.py", rendered)


    def test_store_and_io_errors_map_to_os_error(self) -> None:
        from backend.rust_host_control_service import RustHostControlService as Svc

        store_io_codes = [
            "review_store_error",
            "review_io_error",
            "control_store_error",
            "broker_state_store_error",
            "workspace_io_error",
            "workspace_index_store_error",
        ]
        for code in store_io_codes:
            exc = RustHostRemoteError(code, f"test {code}")
            mapped = Svc._map_remote_error(exc)
            self.assertIsInstance(mapped, OSError, f"{code} should map to OSError")
            self.assertIn(code, str(mapped))

    def test_invalid_request_maps_to_value_error(self) -> None:
        from backend.rust_host_control_service import RustHostControlService as Svc

        exc = RustHostRemoteError("invalid_request", "malformed JSON")
        mapped = Svc._map_remote_error(exc)
        self.assertIsInstance(mapped, ValueError)

    def test_unknown_error_code_falls_through_to_runtime_error(self) -> None:
        from backend.rust_host_control_service import RustHostControlService as Svc

        exc = RustHostRemoteError("some_future_error", "unexpected")
        mapped = Svc._map_remote_error(exc)
        self.assertIsInstance(mapped, RuntimeError)
        self.assertNotIsInstance(mapped, OSError)


if __name__ == "__main__":
    unittest.main()
