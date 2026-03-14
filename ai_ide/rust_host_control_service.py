from __future__ import annotations

from collections.abc import Callable

from ai_ide.models import AgentSession, WriteProposal
from ai_ide.review_manager import ReviewManager
from ai_ide.rust_host_client import RustHostClient, RustHostRemoteError
from ai_ide.rust_host_protocol import (
    AgentSessionSnapshot,
    PolicyChangeResult,
    PolicyChangeSnapshot,
    RuntimeMetricsSnapshot,
    audit_list_request,
    metrics_show_request,
    policy_add_deny_rule_request,
    policy_remove_deny_rule_request,
    policy_sync_request,
    review_apply_request,
    review_render_request,
    review_reject_request,
    review_stage_write_request,
    rotate_agent_session_request,
    RustHostSnapshot,
    snapshot_request,
    workspace_grep_request,
    workspace_index_refresh_request,
    workspace_index_show_request,
    workspace_list_request,
    workspace_tree_request,
)
from ai_ide.workspace_models import WorkspaceCatalogEntry, WorkspaceIndexSnapshot, WorkspaceSearchMatch


class RustHostControlService:
    def __init__(
        self,
        client: RustHostClient,
        *,
        review_manager: ReviewManager,
        sync_snapshot: Callable[[RustHostSnapshot], None],
    ) -> None:
        self.client = client
        self.review_manager = review_manager
        self._sync_snapshot = sync_snapshot

    def snapshot(self) -> RustHostSnapshot:
        return self._request_ok(snapshot_request(), expected_type="snapshot")

    def sync_policy(self) -> PolicyChangeResult:
        return self._request_policy_change_snapshot(policy_sync_request())

    def add_deny_rule(self, rule: str) -> PolicyChangeResult:
        return self._request_policy_change_snapshot(policy_add_deny_rule_request(rule))

    def remove_deny_rule(self, rule: str) -> PolicyChangeResult:
        return self._request_policy_change_snapshot(policy_remove_deny_rule_request(rule))

    def rotate_agent_session(self, agent_kind: str | None = None) -> AgentSession:
        response = self._request_ok(
            rotate_agent_session_request(agent_kind),
            expected_type="agent_session_snapshot",
        )
        assert isinstance(response, AgentSessionSnapshot)
        self._sync_snapshot(response.snapshot)
        return response.session

    def list_workspace(self, target: str = ".") -> list[WorkspaceCatalogEntry]:
        return self._request_ok(workspace_list_request(target), expected_type="workspace_list")

    def tree_workspace(self, target: str = ".") -> list[WorkspaceCatalogEntry]:
        return self._request_ok(workspace_tree_request(target), expected_type="workspace_tree")

    def grep_workspace(self, pattern: str, target: str = ".") -> list[WorkspaceSearchMatch]:
        return self._request_ok(
            workspace_grep_request(pattern, target),
            expected_type="workspace_grep",
        )

    def show_workspace_index(self) -> WorkspaceIndexSnapshot:
        return self._request_ok(
            workspace_index_show_request(),
            expected_type="workspace_index_snapshot",
        )

    def metrics_snapshot(self) -> RuntimeMetricsSnapshot:
        return self._request_ok(metrics_show_request(), expected_type="runtime_metrics_snapshot")

    def list_audit_events(
        self,
        limit: int = 20,
        *,
        allowed: bool | None = None,
    ) -> list[object]:
        return self._request_ok(
            audit_list_request(limit, allowed),
            expected_type="audit_list",
        )

    def refresh_workspace_index(self) -> WorkspaceIndexSnapshot:
        return self._request_ok(
            workspace_index_refresh_request(),
            expected_type="workspace_index_snapshot",
        )

    def sync_review_state(self) -> None:
        snapshot = self.review_manager.state_store.load()
        self.review_manager.replace_proposals(snapshot.proposals)

    def stage_write(
        self,
        target: str,
        proposed_text: str,
        *,
        session_id: str,
        agent_session_id: str,
    ) -> WriteProposal:
        proposal = self._request_ok(
            review_stage_write_request(
                target,
                proposed_text,
                session_id=session_id,
                agent_session_id=agent_session_id,
            ),
            expected_type="review_proposal",
        )
        self.sync_review_state()
        return proposal

    def apply_proposal(self, proposal_id: str) -> WriteProposal:
        try:
            proposal = self._request_ok(
                review_apply_request(proposal_id),
                expected_type="review_proposal",
            )
        except RuntimeError:
            self.sync_review_state()
            raise
        self.sync_review_state()
        return proposal

    def reject_proposal(self, proposal_id: str) -> WriteProposal:
        proposal = self._request_ok(
            review_reject_request(proposal_id),
            expected_type="review_proposal",
        )
        self.sync_review_state()
        return proposal

    def render_proposal(self, proposal_id: str) -> str:
        rendered = self._request_ok(
            review_render_request(proposal_id),
            expected_type="review_render",
        )
        return rendered.content

    def _request_policy_change_snapshot(self, payload: dict[str, object]) -> PolicyChangeResult:
        response = self._request_ok(payload, expected_type="policy_change_snapshot")
        assert isinstance(response, PolicyChangeSnapshot)
        self._sync_snapshot(response.snapshot)
        return response.result

    def _request_ok(self, payload: dict[str, object], *, expected_type: str) -> object:
        try:
            return self.client.request_ok(payload, expected_type=expected_type)
        except RustHostRemoteError as exc:
            raise self._map_remote_error(exc) from exc

    @staticmethod
    def _map_remote_error(exc: RustHostRemoteError) -> Exception:
        if exc.code in {
            "review_blocked_by_policy",
            "review_symlink_path",
            "review_hardlink_path",
            "review_internal_path",
            "review_path_escapes_root",
            "workspace_blocked_by_policy",
            "workspace_symlink_path",
            "workspace_hardlink_path",
            "workspace_internal_path",
            "workspace_path_escapes_root",
        }:
            return PermissionError(exc.message)
        if exc.code == "review_target_is_directory":
            return IsADirectoryError(exc.message)
        if exc.code == "workspace_directory_not_found":
            return FileNotFoundError(exc.message)
        if exc.code in {"review_conflict"}:
            return RuntimeError(exc.message)
        if exc.code in {"review_not_pending", "review_unknown_proposal", "invalid_request"}:
            return ValueError(exc.message)
        if exc.code in {
            "review_store_error",
            "review_io_error",
            "control_store_error",
            "broker_state_store_error",
            "workspace_io_error",
            "workspace_index_store_error",
        }:
            return OSError(exc.message)
        return RuntimeError(exc.message)
