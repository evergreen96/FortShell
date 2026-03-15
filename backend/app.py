from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from backend.agents import AgentRegistry
from backend.agent_runtime import AgentRuntimeManager
from backend.broker import ToolBroker
from backend.broker_state_store import BrokerStateStore
from backend.command_access_service import CommandContext
from backend.commands import (
    HELP_TEXT,
    handle_agent_command,
    handle_ai_command,
    handle_audit_command,
    handle_events_command,
    handle_policy_command,
    handle_review_command,
    handle_runner_command,
    handle_terminal_command,
    handle_unsafe_command,
    handle_workspace_command,
)
from backend.events import EventBus
from backend.filtered_fs_factory import create_filtered_fs_backend
from core.models import PolicyState
from core.policy import PolicyEngine
from core.policy_state_store import PolicyStateStore
from backend.windows.platforms import get_platform_adapter
from backend.review_event_publisher import ReviewEventPublisher
from backend.review_manager import ReviewManager
from backend.review_state_store import ReviewStateStore
from backend.runner import RunnerManager
from backend.rust_host_client import RustHostClient
from backend.rust_host_control_service import RustHostControlService
from backend.rust_host_protocol import RustHostSnapshot
from backend.runtime_status_service import RuntimeStatusService
from backend.session import SessionManager
from backend.terminal import TerminalManager
from backend.terminal_profiles import TerminalProfileCatalog
from core.workspace_access_service import WorkspaceAccessService
from backend.workspace_catalog_service import WorkspaceCatalogService
from backend.workspace_index_freshness_service import WorkspaceIndexFreshnessService
from backend.workspace_index_service import WorkspaceIndexService
from backend.workspace_index_snapshot_builder import WorkspaceIndexSnapshotBuilder
from backend.workspace_index_state_store import WorkspaceIndexStateStore
from core.workspace_models import WorkspaceIndexSnapshot
from backend.workspace_panel_service import WorkspacePanelService
from backend.workspace_visibility_monitor import WorkspaceVisibilityMonitor
from backend.workspace_visibility_runtime import (
    build_workspace_visibility_backend,
    resolve_workspace_visibility_watcher,
)
from backend.workspace_visibility_source import SnapshotWorkspaceVisibilitySource
from backend.workspace_visibility_state_store import WorkspaceVisibilityStateStore
from backend.workspace_visibility_backend import WorkspaceVisibilityWatcher


class AIIdeApp:
    def __init__(
        self,
        root: Path,
        runtime_root: Path | None = None,
        *,
        rust_host_client: RustHostClient | None = None,
        workspace_visibility_watcher: WorkspaceVisibilityWatcher | None = None,
    ) -> None:
        self.root = root.resolve()
        self.policy = PolicyEngine(self.root)
        self.policy_store = PolicyStateStore(self.root)
        self.policy.replace_state(self.policy_store.load())
        self.sessions = SessionManager(self.policy)
        self.agents = AgentRegistry()
        self.platform = get_platform_adapter()
        self.runtime_root = (runtime_root or self.platform.runtime_root(self.root)).resolve()
        events_root = self.runtime_root / "events"
        self.events = EventBus(
            log_path=events_root / "events.jsonl",
            cursor_path=events_root / "cursors.json",
        )
        self.workspace_access = WorkspaceAccessService(self.root, self.policy)
        self.workspace_catalog = WorkspaceCatalogService(self.root, self.workspace_access)
        self.workspace_index_builder = WorkspaceIndexSnapshotBuilder(self.root, self.workspace_access)
        self.workspace_index = WorkspaceIndexService(
            self.root,
            self.policy,
            self.workspace_access,
            state_store=WorkspaceIndexStateStore(self.runtime_root / "workspace" / "index.json"),
        )
        self.workspace_visibility_source = SnapshotWorkspaceVisibilitySource(
            self.workspace_index_builder,
            policy_version_provider=lambda: self.policy.state.version,
        )
        self.workspace_visibility_watcher = resolve_workspace_visibility_watcher(
            self.platform,
            project_root=self.root,
            runtime_root=self.runtime_root,
            override=workspace_visibility_watcher,
        )
        self.workspace_visibility_backend = build_workspace_visibility_backend(
            self.workspace_visibility_source,
            watcher=self.workspace_visibility_watcher,
        )
        self.workspace_visibility = WorkspaceVisibilityMonitor(
            self.workspace_visibility_backend,
            event_bus=self.events,
            execution_session_id_provider=lambda: self.sessions.current_session_id,
            state_store=WorkspaceVisibilityStateStore(self.runtime_root / "workspace" / "visibility.json"),
        )
        self.workspace_visibility.start()
        self.workspace_index_freshness = WorkspaceIndexFreshnessService(
            self.workspace_index_builder,
            current_signature_provider=lambda: self.workspace_visibility.current_state().signature,
        )
        self.workspace_panel = WorkspacePanelService(self)
        self.broker = ToolBroker(
            self.root,
            self.policy,
            self.sessions,
            state_store=BrokerStateStore(self.runtime_root / "broker" / "state.json"),
            workspace_access=self.workspace_access,
            workspace_catalog=self.workspace_catalog,
        )
        self.reviews = ReviewManager(
            self.root,
            self.policy,
            state_store=ReviewStateStore(self.runtime_root / "reviews" / "state.json"),
            workspace_access=self.workspace_access,
        )
        self.review_events = ReviewEventPublisher(self.events)
        self.terminal_profiles = TerminalProfileCatalog(self.runtime_root)
        self.filtered_fs = create_filtered_fs_backend(
            self.root,
            self.policy,
            self.runtime_root,
            workspace_access=self.workspace_access,
        )
        # Compatibility alias for code paths/tests still referring to "projection".
        self.projection = self.filtered_fs
        self.runners = RunnerManager(
            self.root,
            self.filtered_fs,
            self.sessions,
            self.platform,
            workspace_signature_provider=lambda: self.workspace_visibility.current_state().signature,
        )
        self.agent_runtime = AgentRuntimeManager(
            self.agents,
            self.runners,
            self.sessions,
            event_bus=self.events,
            state_path=self.runtime_root / "agents" / "state.json",
        )
        self.terminals = TerminalManager(
            self.root,
            self.broker.metrics,
            self.runners,
            self.agent_runtime,
            event_bus=self.events,
            state_path=self.runtime_root / "terminals" / "state.json",
            filtered_fs_backend=self.filtered_fs,
            profile_catalog=self.terminal_profiles,
        )
        self.terminals.mark_noncurrent_runner_terminals_stale(
            self.sessions.current_session_id,
            "restored terminal bound to stale execution session",
        )
        self.status_service = RuntimeStatusService()
        self.rust_control = None
        if rust_host_client is not None:
            self.rust_control = RustHostControlService(
                rust_host_client,
                review_manager=self.reviews,
                sync_snapshot=self.sync_rust_snapshot,
            )
            self.sync_rust_snapshot(self.rust_control.snapshot())

        fs_status = self.filtered_fs.status()
        logger.info(
            "app.filtered_fs backend=%s driver_installed=%s degraded=%s",
            fs_status.backend,
            fs_status.driver_installed,
            fs_status.degraded,
        )

    def persist_policy(self) -> None:
        self.policy_store.save(self.policy.state)

    def sync_policy_state(self) -> Optional[str]:
        stored_state = self.policy_store.load()
        if stored_state == self.policy.state:
            return None
        if stored_state.version <= self.policy.state.version:
            stored_state = PolicyState(
                deny_globs=list(stored_state.deny_globs),
                version=self.policy.state.version + 1,
            )
            self.policy_store.save(stored_state)
        self.policy.replace_state(stored_state)
        self.filtered_fs.update_policy()
        return self.maybe_rotate_session(force=True)

    def sync_review_state(self) -> None:
        snapshot = self.reviews.state_store.load()
        self.reviews.replace_proposals(snapshot.proposals)

    def sync_rust_snapshot(self, snapshot: RustHostSnapshot) -> None:
        self.policy.replace_state(snapshot.policy_state)
        self.filtered_fs.update_policy()
        sync_result = self.sessions.sync_remote_sessions(
            snapshot.execution_session,
            snapshot.agent_session,
        )
        if not sync_result.execution_changed:
            return
        previous_session_id = sync_result.previous_execution_session_id
        self.agent_runtime.mark_execution_session_stale(previous_session_id)
        self.terminals.mark_execution_session_stale(
            previous_session_id,
            f"terminal bound to stale execution session {previous_session_id}",
        )
        self.filtered_fs.cleanup_stale(self.sessions.current_session_id)

    def maybe_rotate_session(self, *, force: bool = False) -> Optional[str]:
        previous_session_id = self.sessions.current_session_id
        rotated = self.sessions.ensure_fresh_execution_session(force=force)
        if rotated:
            self.agent_runtime.mark_execution_session_stale(previous_session_id)
            self.terminals.mark_execution_session_stale(
                previous_session_id,
                f"terminal bound to stale execution session {previous_session_id}",
            )
            self.filtered_fs.cleanup_stale(self.sessions.current_session_id)
            return self.sessions.current_session_id
        return None

    def handle_command(self, raw: str, *, context: CommandContext | None = None) -> str:
        raw = raw.strip()
        if not raw:
            return ""
        context = context or CommandContext.user()

        if self.rust_control is None:
            self.sync_policy_state()
        else:
            self.rust_control.sync_policy()
        if self.workspace_visibility.requires_command_boundary_poll():
            self.workspace_visibility.poll()
        parts = shlex.split(raw)
        command = parts[0]

        if command == "help":
            return HELP_TEXT

        if command == "status":
            return self._status_json() if len(parts) >= 2 and parts[1] == "json" else self._status_text()

        if command == "filtered-fs":
            import json as _json
            return _json.dumps(self.filtered_fs_status(), indent=2)

        if command == "policy":
            return handle_policy_command(self, parts)

        if command == "session" and len(parts) >= 2 and parts[1] == "show":
            return self._session_json() if len(parts) >= 3 and parts[2] == "json" else self._session_text()

        if command == "agent":
            return handle_agent_command(self, parts, raw)

        if command == "ai":
            return handle_ai_command(self, parts, raw)

        if command == "term":
            return handle_terminal_command(self, parts, raw)

        if command == "events":
            return handle_events_command(self, parts)

        if command == "audit":
            return handle_audit_command(self, parts)

        if command == "review":
            return handle_review_command(self, parts, raw)

        if command == "workspace":
            return handle_workspace_command(self, parts)

        if command == "runner":
            return handle_runner_command(self, parts, raw)

        if command == "unsafe":
            return handle_unsafe_command(self, parts, raw, context)

        if command == "metrics":
            return self._metrics_json() if len(parts) >= 2 and parts[1] == "json" else self._metrics_text()

        if command == "exit":
            raise SystemExit(0)

        raise ValueError(f"Unknown command: {command}")

    def close(self) -> None:
        self.terminals.pty_manager.destroy_all()
        self.filtered_fs.unmount()
        self.workspace_visibility.close()
        if self.rust_control is not None:
            self.rust_control.client.close()

    def list_workspace_entries(self, target: str = ".") -> list:
        if self.rust_control is not None:
            return self.rust_control.list_workspace(target)
        return self.workspace_catalog.list_dir(target)

    def tree_workspace_entries(self, target: str = ".") -> list:
        if self.rust_control is not None:
            return self.rust_control.tree_workspace(target)
        return list(self.workspace_catalog.iter_tree(target))

    def grep_workspace(self, pattern: str, target: str = ".") -> list:
        if self.rust_control is not None:
            return self.rust_control.grep_workspace(pattern, target)
        return self.workspace_catalog.grep(pattern, target)

    def stage_review(
        self, target: str, proposed_text: str, *, session_id: str, agent_session_id: str,
    ):
        if self.rust_control is not None:
            return self.rust_control.stage_write(
                target, proposed_text,
                session_id=session_id, agent_session_id=agent_session_id,
            )
        return self.reviews.stage_write(
            target, proposed_text,
            session_id=session_id, agent_session_id=agent_session_id,
        )

    def apply_review(self, proposal_id: str):
        if self.rust_control is not None:
            return self.rust_control.apply_proposal(proposal_id)
        return self.reviews.apply_proposal(proposal_id)

    def reject_review(self, proposal_id: str):
        if self.rust_control is not None:
            return self.rust_control.reject_proposal(proposal_id)
        return self.reviews.reject_proposal(proposal_id)

    def render_review(self, proposal_id: str) -> str:
        if self.rust_control is not None:
            return self.rust_control.render_proposal(proposal_id)
        return self.reviews.render_proposal(proposal_id)

    def rotate_agent(self, agent_kind: str | None = None):
        if self.rust_control is not None:
            return self.rust_control.rotate_agent_session(agent_kind)
        return self.sessions.rotate_agent_session(agent_kind)

    def add_policy_rule(self, rule: str) -> tuple[bool, str]:
        """Add a deny rule. Returns (changed, execution_session_id)."""
        if self.rust_control is not None:
            result = self.rust_control.add_deny_rule(rule)
            return result.changed, self.sessions.current_session_id
        changed = self.policy.add_deny_rule(rule)
        if changed:
            self.persist_policy()
            self.filtered_fs.update_policy()
        session_id = self.maybe_rotate_session() if changed else self.sessions.current_session_id
        return changed, session_id

    def remove_policy_rule(self, rule: str) -> tuple[bool, str]:
        """Remove a deny rule. Returns (changed, execution_session_id)."""
        if self.rust_control is not None:
            result = self.rust_control.remove_deny_rule(rule)
            return result.changed, self.sessions.current_session_id
        changed = self.policy.remove_deny_rule(rule)
        if changed:
            self.persist_policy()
            self.filtered_fs.update_policy()
        session_id = self.maybe_rotate_session() if changed else self.sessions.current_session_id
        return changed, session_id

    def save_editor_file(self, target: str, content: str) -> None:
        self.broker.write_file(target, content, action="editor.save")
        self.sync_workspace_index_cache()

    def list_audit_events(self, limit: int = 20, *, allowed: bool | None = None) -> list:
        if self.rust_control is not None:
            return self.rust_control.list_audit_events(limit, allowed=allowed)
        return self.broker.list_audit_events(limit, allowed=allowed)

    def load_workspace_index_snapshot(self) -> WorkspaceIndexSnapshot:
        if self.rust_control is not None:
            return self.rust_control.show_workspace_index()
        return self.workspace_index.load()

    def refresh_workspace_index_snapshot(self) -> WorkspaceIndexSnapshot:
        if self.rust_control is not None:
            return self.rust_control.refresh_workspace_index()
        return self.workspace_index.refresh()

    def workspace_index_stale_reasons(self, snapshot: WorkspaceIndexSnapshot) -> list[str]:
        return self.workspace_index_freshness.stale_reasons(
            snapshot,
            policy_version=self.policy.state.version,
        )

    def workspace_index_is_stale(self, snapshot: WorkspaceIndexSnapshot) -> bool:
        return bool(self.workspace_index_stale_reasons(snapshot))

    def sync_workspace_index_cache(self) -> None:
        # The cache must not block a successful write/apply path; stale data is safer than
        # failing the authoritative command after the workspace mutation already happened.
        try:
            self.refresh_workspace_index_snapshot()
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError, OSError):
            return

    def note_workspace_visibility_change(self, reason: str, *, target: str | None = None) -> None:
        self.workspace_visibility.record_change(reason, target=target)

    def status_snapshot(self):
        return self._status_snapshot()

    def metrics_snapshot(self):
        return self._metrics_snapshot()

    def list_review_proposals(self, *, status: str | None = None, limit: int = 20):
        self.sync_review_state()
        return self.reviews.list_proposals(status=status, limit=limit)

    def list_terminal_inspections(self):
        return self.terminals.list_terminal_inspections()

    def create_terminal(
        self,
        *,
        name: str | None = None,
        transport: str = "runner",
        runner_mode: str | None = None,
        io_mode: str = "command",
        profile_id: str | None = None,
    ):
        resolved_transport = transport
        if profile_id is not None:
            resolved_transport = self.terminal_profiles.get(profile_id).transport
        execution_session_id = self.sessions.current_session_id if resolved_transport == "runner" else None
        return self.terminals.create_terminal(
            name,
            execution_session_id=execution_session_id,
            transport=transport,
            runner_mode=runner_mode or self.runners.mode,
            io_mode=io_mode,
            profile_id=profile_id,
        )

    def list_terminal_profiles(self) -> list[dict[str, object]]:
        return [profile.to_dict() for profile in self.terminal_profiles.list_profiles()]

    def write_to_pty(self, terminal_id: str, data: str) -> None:
        self.terminals.write_to_pty(terminal_id, data)

    def resize_pty(self, terminal_id: str, cols: int, rows: int) -> None:
        self.terminals.resize_pty(terminal_id, cols, rows)

    def get_pty_output(self, terminal_id: str) -> bytes:
        return self.terminals.get_pty_output(terminal_id)

    def inspect_terminal(self, terminal_id: str):
        return self.terminals.inspect_terminal(terminal_id)

    def run_terminal_command(self, terminal_id: str, command: str) -> str:
        return self.terminals.run_command(terminal_id, command)

    def _status_text(self) -> str:
        return self.status_service.status_text(self._status_snapshot())

    def _status_json(self) -> str:
        return self.status_service.to_json(self._status_snapshot().to_dict())

    def _session_text(self) -> str:
        return self.status_service.session_text(self._status_snapshot())

    def _session_json(self) -> str:
        return self.status_service.to_json(self._status_snapshot().to_dict())

    def _metrics_text(self) -> str:
        return self.status_service.metrics_text(self._metrics_snapshot())

    def _metrics_json(self) -> str:
        return self.status_service.to_json(self._metrics_snapshot().to_dict())

    def _status_snapshot(self):
        return self.status_service.build_status_snapshot(
            execution_session_id=self.sessions.current_session_id,
            execution_status=self.sessions.current_execution_session.status,
            agent_session_id=self.sessions.current_agent_session_id,
            agent_kind=self.sessions.current_agent_session.agent_kind,
            agent_status=self.sessions.current_agent_session.status,
            runner_mode=self.runners.mode,
            strict_boundary_scope=self.runners.strict_runner.strict_service.boundary_scope(),
            policy_version=self.policy.state.version,
            deny_rule_count=len(self.policy.state.deny_globs),
            terminal_count=len(self.terminals.terminals),
            event_count=self.events.size(),
            pending_review_count=self.reviews.count_proposals(status="pending"),
        )

    def filtered_fs_status(self) -> dict:
        """Return filtered filesystem backend status as a dict."""
        fs_status = self.filtered_fs.status()
        return {
            "backend": fs_status.backend,
            "driver_installed": fs_status.driver_installed,
            "mounted": fs_status.mounted,
            "mount_point": fs_status.mount_point,
            "degraded": fs_status.degraded,
            "detail": fs_status.detail,
        }

    def _metrics_snapshot(self):
        if self.rust_control is not None:
            return self.rust_control.metrics_snapshot()
        return self.status_service.build_metrics_snapshot(
            self.broker.metrics,
            audit_event_count=len(self.broker.audit_log),
        )
