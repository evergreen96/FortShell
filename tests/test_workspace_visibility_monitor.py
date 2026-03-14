from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.events import EventBus
from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from ai_ide.policy import PolicyEngine
from ai_ide.workspace_access_service import WorkspaceAccessService
from ai_ide.workspace_visibility_backend import PollingWorkspaceVisibilityBackend
from ai_ide.workspace_index_snapshot_builder import WorkspaceIndexSnapshotBuilder
from ai_ide.workspace_visibility_monitor import WorkspaceVisibilityMonitor
from ai_ide.workspace_visibility_models import VisibleWorkspaceState
from ai_ide.workspace_visibility_source import SnapshotWorkspaceVisibilitySource
from ai_ide.workspace_visibility_state_store import WorkspaceVisibilityStateStore


class WorkspaceVisibilityMonitorTests(unittest.TestCase):
    def test_poll_publishes_event_when_visible_workspace_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            policy = PolicyEngine(root)
            access = WorkspaceAccessService(root, policy)
            builder = WorkspaceIndexSnapshotBuilder(root, access)
            source = SnapshotWorkspaceVisibilitySource(
                builder,
                policy_version_provider=lambda: policy.state.version,
            )
            backend = PollingWorkspaceVisibilityBackend(source)
            events = EventBus()
            monitor = WorkspaceVisibilityMonitor(
                backend,
                event_bus=events,
                execution_session_id_provider=lambda: "sess-1",
            )

            self.assertIsNone(monitor.poll())

            target.write_text("changed visible plan", encoding="utf-8")
            state = monitor.poll()
            published = events.list_events(
                kind_prefix="workspace.visible",
                source_type="workspace",
                source_id="visible-tree",
            )

            self.assertIsNotNone(state)
            self.assertEqual(1, len(published))
            self.assertEqual("workspace.visible.changed", published[0].kind)
            self.assertEqual("poll", published[0].payload["reason"])
            self.assertEqual("external_or_unknown", published[0].payload["origin"])
            self.assertEqual(2, published[0].payload["entry_count"])
            self.assertNotEqual(
                published[0].payload["previous_signature"],
                published[0].payload["signature"],
            )

    def test_poll_ignores_denied_and_internal_only_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets").mkdir()
            hidden = root / "secrets" / "token.txt"
            hidden.write_text("hidden plan", encoding="utf-8")
            internal_dir = root / INTERNAL_PROJECT_METADATA_DIR_NAME
            internal_dir.mkdir()
            (internal_dir / "state.json").write_text("{}", encoding="utf-8")

            policy = PolicyEngine(root)
            policy.add_deny_rule("secrets/**")
            access = WorkspaceAccessService(root, policy)
            builder = WorkspaceIndexSnapshotBuilder(root, access)
            source = SnapshotWorkspaceVisibilitySource(
                builder,
                policy_version_provider=lambda: policy.state.version,
            )
            backend = PollingWorkspaceVisibilityBackend(source)
            events = EventBus()
            monitor = WorkspaceVisibilityMonitor(
                backend,
                event_bus=events,
                execution_session_id_provider=lambda: "sess-1",
            )

            hidden.write_text("changed hidden plan", encoding="utf-8")
            (internal_dir / "state.json").write_text('{"changed":true}', encoding="utf-8")

            self.assertIsNone(monitor.poll())
            self.assertEqual([], events.list_events(kind_prefix="workspace.visible"))

    def test_monitor_uses_persisted_baseline_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")
            store = WorkspaceVisibilityStateStore(Path(temp_dir) / "runtime" / "workspace" / "visibility.json")

            first_policy = PolicyEngine(root)
            first_access = WorkspaceAccessService(root, first_policy)
            first_builder = WorkspaceIndexSnapshotBuilder(root, first_access)
            first_source = SnapshotWorkspaceVisibilitySource(
                first_builder,
                policy_version_provider=lambda: first_policy.state.version,
            )
            first_backend = PollingWorkspaceVisibilityBackend(first_source)
            first_monitor = WorkspaceVisibilityMonitor(
                first_backend,
                event_bus=EventBus(),
                execution_session_id_provider=lambda: "sess-1",
                state_store=store,
            )
            self.assertEqual(first_builder.build_signature(), first_monitor.current_state().signature)

            target.write_text("changed visible plan", encoding="utf-8")
            second_policy = PolicyEngine(root)
            second_access = WorkspaceAccessService(root, second_policy)
            second_builder = WorkspaceIndexSnapshotBuilder(root, second_access)
            second_source = SnapshotWorkspaceVisibilitySource(
                second_builder,
                policy_version_provider=lambda: second_policy.state.version,
            )
            second_backend = PollingWorkspaceVisibilityBackend(second_source)
            events = EventBus()
            restarted_monitor = WorkspaceVisibilityMonitor(
                second_backend,
                event_bus=events,
                execution_session_id_provider=lambda: "sess-2",
                state_store=store,
            )

            self.assertEqual(second_builder.build_signature(), restarted_monitor.current_state().signature)

            state = restarted_monitor.poll()
            published = events.list_events(kind_prefix="workspace.visible")

            self.assertIsNotNone(state)
            self.assertEqual(1, len(published))
            self.assertEqual("poll", published[0].payload["reason"])
            self.assertEqual("external_or_unknown", published[0].payload["origin"])

    def test_monitor_accepts_non_polling_backend(self) -> None:
        events = EventBus()
        current = VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1)

        class StubBackend:
            def current_state(self) -> VisibleWorkspaceState:
                return current

            def sync(self) -> VisibleWorkspaceState:
                return VisibleWorkspaceState(signature="sig-2", entry_count=2, policy_version=1)

            def requires_command_boundary_poll(self) -> bool:
                return False

            def start(self, on_change=None) -> None:
                return None

            def close(self) -> None:
                return None

        monitor = WorkspaceVisibilityMonitor(
            StubBackend(),
            event_bus=events,
            execution_session_id_provider=lambda: "sess-1",
        )

        state = monitor.poll()
        published = events.list_events(kind_prefix="workspace.visible")

        self.assertIsNotNone(state)
        self.assertEqual("sig-2", state.signature)
        self.assertEqual(1, len(published))
        self.assertEqual("sig-1", published[0].payload["previous_signature"])
        self.assertEqual("sig-2", published[0].payload["signature"])

    def test_monitor_start_registers_backend_callback_and_publishes_watch_event(self) -> None:
        events = EventBus()

        class StubBackend:
            def __init__(self) -> None:
                self.callback = None
                self.state = VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1)

            def current_state(self) -> VisibleWorkspaceState:
                return self.state

            def sync(self) -> VisibleWorkspaceState:
                return self.state

            def requires_command_boundary_poll(self) -> bool:
                return False

            def start(self, on_change=None) -> None:
                self.callback = on_change

            def close(self) -> None:
                self.callback = None

            def emit(self, state: VisibleWorkspaceState) -> None:
                self.state = state
                assert self.callback is not None
                self.callback(state)

        backend = StubBackend()
        monitor = WorkspaceVisibilityMonitor(
            backend,
            event_bus=events,
            execution_session_id_provider=lambda: "sess-1",
        )

        monitor.start()
        backend.emit(VisibleWorkspaceState(signature="sig-2", entry_count=2, policy_version=1))
        published = events.list_events(kind_prefix="workspace.visible")

        self.assertEqual(1, len(published))
        self.assertEqual("watch", published[0].payload["reason"])
        self.assertEqual("backend", published[0].payload["origin"])
        self.assertEqual("sig-2", monitor.current_state().signature)

    def test_monitor_close_delegates_to_backend(self) -> None:
        class StubBackend:
            def __init__(self) -> None:
                self.closed = False
                self.started = False
                self.state = VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1)

            def current_state(self) -> VisibleWorkspaceState:
                return self.state

            def sync(self) -> VisibleWorkspaceState:
                return self.state

            def requires_command_boundary_poll(self) -> bool:
                return False

            def start(self, on_change=None) -> None:
                self.started = True

            def close(self) -> None:
                self.closed = True

        backend = StubBackend()
        monitor = WorkspaceVisibilityMonitor(
            backend,
            event_bus=EventBus(),
            execution_session_id_provider=lambda: "sess-1",
        )

        monitor.start()
        monitor.close()

        self.assertTrue(backend.started)
        self.assertTrue(backend.closed)

    def test_monitor_reports_backend_poll_requirement(self) -> None:
        class StubBackend:
            def current_state(self) -> VisibleWorkspaceState:
                return VisibleWorkspaceState(signature="sig-1", entry_count=1, policy_version=1)

            def sync(self) -> VisibleWorkspaceState:
                return self.current_state()

            def requires_command_boundary_poll(self) -> bool:
                return False

            def start(self, on_change=None) -> None:
                return None

            def close(self) -> None:
                return None

        monitor = WorkspaceVisibilityMonitor(
            StubBackend(),
            event_bus=EventBus(),
            execution_session_id_provider=lambda: "sess-1",
        )

        self.assertFalse(monitor.requires_command_boundary_poll())


if __name__ == "__main__":
    unittest.main()
