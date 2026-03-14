from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.broker_state_store import BrokerStateStore
from backend.app import AIIdeApp
from core.models import AgentSession, AuditEvent, ExecutionSession, PolicyState, UsageMetrics, WriteProposal
from core.policy import PolicyEngine
from core.policy_state_store import PolicyStateStore
from backend.review_state_store import ReviewStateStore
from backend.rust_host_client import RustHostRemoteError
from backend.rust_host_protocol import (
    AgentSessionSnapshot,
    PolicyChangeResult,
    PolicyChangeSnapshot,
    RuntimeMetricsSnapshot,
    RustHostSnapshot,
)
from core.workspace_access_service import WorkspaceAccessService
from backend.workspace_catalog_service import WorkspaceCatalogService
from backend.workspace_index_service import WorkspaceIndexService
from backend.workspace_index_state_store import WorkspaceIndexStateStore


class _FakeRustHostClient:
    def __init__(self, root: Path, runtime_root: Path) -> None:
        self.root = root
        self.runtime_root = runtime_root
        self.policy_store = PolicyStateStore(root)
        self.review_store = ReviewStateStore(runtime_root / "reviews" / "state.json")
        self.broker_store = BrokerStateStore(runtime_root / "broker" / "state.json")
        self.calls: list[tuple[dict[str, object], str | None]] = []
        self._proposal_counter = 0
        self._execution_counter = 1
        self._agent_counter = 1
        self.current_execution_session = ExecutionSession(
            session_id="rust-sess-0001",
            policy_version=self.policy_store.load().version,
            created_at="2026-03-07T00:00:01Z",
            status="active",
            rotated_from=None,
        )
        self.current_agent_session = AgentSession(
            agent_session_id="rust-agent-0001",
            execution_session_id=self.current_execution_session.session_id,
            agent_kind="default",
            created_at="2026-03-07T00:00:02Z",
            status="active",
            rotated_from=None,
        )

    def close(self) -> None:
        pass

    def request_ok(self, payload: dict[str, object], *, expected_type: str | None = None) -> object:
        self.calls.append((payload, expected_type))
        request_type = payload["type"]

        if request_type == "snapshot":
            return self._snapshot()

        if request_type == "policy_sync":
            state = self.policy_store.load()
            changed = state.version != self.current_execution_session.policy_version
            if changed:
                self._rotate_execution_session(policy_version=state.version)
            return PolicyChangeSnapshot(
                result=PolicyChangeResult(
                    changed=changed,
                    rotated=changed,
                    execution_session_id=self.current_execution_session.session_id,
                    agent_session_id=self.current_agent_session.agent_session_id,
                    policy_version=state.version,
                ),
                snapshot=self._snapshot(),
            )

        if request_type == "policy_add_deny_rule":
            state = self.policy_store.load()
            rule = str(payload["rule"])
            if rule not in state.deny_globs:
                state = PolicyState([*state.deny_globs, rule], version=state.version + 1)
                changed = True
                self._rotate_execution_session(policy_version=state.version)
            else:
                changed = False
            self.policy_store.save(state)
            return PolicyChangeSnapshot(
                result=PolicyChangeResult(
                    changed=changed,
                    rotated=changed,
                    execution_session_id=self.current_execution_session.session_id,
                    agent_session_id=self.current_agent_session.agent_session_id,
                    policy_version=state.version,
                ),
                snapshot=self._snapshot(),
            )

        if request_type == "policy_remove_deny_rule":
            state = self.policy_store.load()
            rule = str(payload["rule"])
            if rule in state.deny_globs:
                state = PolicyState([item for item in state.deny_globs if item != rule], version=state.version + 1)
                changed = True
                self.policy_store.save(state)
                self._rotate_execution_session(policy_version=state.version)
            else:
                changed = False
            return PolicyChangeSnapshot(
                result=PolicyChangeResult(
                    changed=changed,
                    rotated=changed,
                    execution_session_id=self.current_execution_session.session_id,
                    agent_session_id=self.current_agent_session.agent_session_id,
                    policy_version=state.version,
                ),
                snapshot=self._snapshot(),
            )

        if request_type == "rotate_agent_session":
            self._rotate_agent_session(str(payload["agent_kind"]) if payload["agent_kind"] is not None else None)
            return AgentSessionSnapshot(
                session=self.current_agent_session,
                snapshot=self._snapshot(),
            )

        if request_type == "workspace_list":
            try:
                return self._workspace_catalog().list_dir(str(payload["target"]))
            except PermissionError as exc:
                raise self._workspace_permission_error(str(payload["target"]), exc) from exc
            except FileNotFoundError as exc:
                raise RustHostRemoteError("workspace_directory_not_found", str(exc)) from exc

        if request_type == "workspace_tree":
            try:
                return list(self._workspace_catalog().iter_tree(str(payload["target"])))
            except PermissionError as exc:
                raise self._workspace_permission_error(str(payload["target"]), exc) from exc
            except FileNotFoundError as exc:
                raise RustHostRemoteError("workspace_directory_not_found", str(exc)) from exc

        if request_type == "workspace_grep":
            try:
                return self._workspace_catalog().grep(
                    str(payload["pattern"]),
                    str(payload["target"]),
                )
            except PermissionError as exc:
                raise self._workspace_permission_error(str(payload["target"]), exc) from exc
            except FileNotFoundError as exc:
                raise RustHostRemoteError("workspace_directory_not_found", str(exc)) from exc

        if request_type == "workspace_index_show":
            return self._workspace_index_service().load()

        if request_type == "workspace_index_refresh":
            return self._workspace_index_service().refresh()

        if request_type == "metrics_show":
            snapshot = self.broker_store.load()
            return RuntimeMetricsSnapshot(
                list_count=snapshot.metrics.list_count,
                read_count=snapshot.metrics.read_count,
                write_count=snapshot.metrics.write_count,
                grep_count=snapshot.metrics.grep_count,
                blocked_count=snapshot.metrics.blocked_count,
                terminal_runs=snapshot.metrics.terminal_runs,
                audit_event_count=len(snapshot.audit_log),
            )

        if request_type == "audit_list":
            snapshot = self.broker_store.load()
            events = snapshot.audit_log
            allowed = payload.get("allowed")
            if allowed is not None:
                events = [event for event in events if event.allowed is bool(allowed)]
            limit = int(payload.get("limit", 20))
            return events[-limit:]

        if request_type == "review_stage_write":
            if not self._is_allowed_path(str(payload["target"])):
                raise RustHostRemoteError(
                    "review_blocked_by_policy",
                    f"Blocked by policy: {payload['target']}",
                )
            self._proposal_counter += 1
            target = str(payload["target"])
            proposal = WriteProposal(
                proposal_id=f"rev-{self._proposal_counter:04d}",
                target=target,
                session_id=str(payload["session_id"]),
                agent_session_id=str(payload["agent_session_id"]),
                created_at="2026-03-07T00:00:00Z",
                updated_at="2026-03-07T00:00:00Z",
                status="pending",
                base_sha256="abc123",
                base_text=(self.root / target).read_text(encoding="utf-8")
                if (self.root / target).exists()
                else None,
                proposed_text=str(payload["proposed_text"]),
            )
            self.review_store.save([*self.review_store.load().proposals, proposal])
            return proposal

        if request_type == "review_apply":
            proposal_id = str(payload["proposal_id"])
            proposals = self.review_store.load().proposals
            for index, proposal in enumerate(proposals):
                if proposal.proposal_id != proposal_id:
                    continue
                target_path = self.root / proposal.target
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(proposal.proposed_text, encoding="utf-8")
                updated = WriteProposal(
                    proposal_id=proposal.proposal_id,
                    target=proposal.target,
                    session_id=proposal.session_id,
                    agent_session_id=proposal.agent_session_id,
                    created_at=proposal.created_at,
                    updated_at="2026-03-07T00:00:01Z",
                    status="applied",
                    base_sha256=proposal.base_sha256,
                    base_text=proposal.base_text,
                    proposed_text=proposal.proposed_text,
                )
                proposals[index] = updated
                self.review_store.save(proposals)
                return updated
            raise RuntimeError(f"unknown proposal: {proposal_id}")

        if request_type == "review_render":
            proposal_id = str(payload["proposal_id"])
            for proposal in self.review_store.load().proposals:
                if proposal.proposal_id == proposal_id:
                    class _Rendered:
                        def __init__(self, content: str) -> None:
                            self.content = content

                    return _Rendered(
                        f"proposal_id={proposal.proposal_id} target={proposal.target}\n--- a/{proposal.target}\n+++ b/{proposal.target}"
                    )
            raise RuntimeError(f"unknown proposal: {proposal_id}")

        raise AssertionError(f"unexpected request type: {request_type}")

    def _snapshot(self) -> RustHostSnapshot:
        proposals = self.review_store.load().proposals
        return RustHostSnapshot(
            policy_state=self.policy_store.load(),
            execution_session=self.current_execution_session,
            agent_session=self.current_agent_session,
            review_count=len(proposals),
            pending_review_count=sum(1 for proposal in proposals if proposal.status == "pending"),
        )

    def _rotate_execution_session(self, *, policy_version: int) -> None:
        previous_execution = self.current_execution_session
        previous_agent = self.current_agent_session
        previous_execution.status = "stale"
        previous_agent.status = "stale"
        self._execution_counter += 1
        next_execution_id = f"rust-sess-{self._execution_counter:04d}"
        self.current_execution_session = ExecutionSession(
            session_id=next_execution_id,
            policy_version=policy_version,
            created_at=f"2026-03-07T00:00:{self._execution_counter:02d}Z",
            status="active",
            rotated_from=previous_execution.session_id,
        )
        self._agent_counter += 1
        self.current_agent_session = AgentSession(
            agent_session_id=f"rust-agent-{self._agent_counter:04d}",
            execution_session_id=next_execution_id,
            agent_kind=previous_agent.agent_kind,
            created_at=f"2026-03-07T00:01:{self._agent_counter:02d}Z",
            status="active",
            rotated_from=previous_agent.agent_session_id,
        )

    def _rotate_agent_session(self, agent_kind: str | None) -> None:
        previous_agent = self.current_agent_session
        previous_agent.status = "stale"
        self._agent_counter += 1
        self.current_agent_session = AgentSession(
            agent_session_id=f"rust-agent-{self._agent_counter:04d}",
            execution_session_id=self.current_execution_session.session_id,
            agent_kind=agent_kind or previous_agent.agent_kind,
            created_at=f"2026-03-07T00:02:{self._agent_counter:02d}Z",
            status="active",
            rotated_from=previous_agent.agent_session_id,
        )

    def _is_allowed_path(self, target: str) -> bool:
        policy = PolicyEngine(self.root)
        policy.replace_state(self.policy_store.load())
        return policy.is_allowed(self.root / target)

    def _workspace_catalog(self) -> WorkspaceCatalogService:
        policy = PolicyEngine(self.root)
        policy.replace_state(self.policy_store.load())
        access = WorkspaceAccessService(self.root, policy)
        return WorkspaceCatalogService(self.root, access)

    def _workspace_index_service(self) -> WorkspaceIndexService:
        policy = PolicyEngine(self.root)
        policy.replace_state(self.policy_store.load())
        access = WorkspaceAccessService(self.root, policy)
        return WorkspaceIndexService(
            self.root,
            policy,
            access,
            state_store=WorkspaceIndexStateStore(self.runtime_root / "workspace" / "index.json"),
        )

    @staticmethod
    def _workspace_permission_error(target: str, exc: PermissionError) -> RustHostRemoteError:
        message = str(exc)
        if message.startswith("Blocked internal path:"):
            return RustHostRemoteError("workspace_internal_path", message)
        if message.startswith("Blocked by policy:"):
            return RustHostRemoteError("workspace_blocked_by_policy", message)
        return RustHostRemoteError(
            "workspace_path_escapes_root",
            f"Path is not under workspace root: {target}",
        )


class AIIdeAppRustHostTests(unittest.TestCase):
    @staticmethod
    def _call_types(client: _FakeRustHostClient) -> list[str]:
        return [str(payload["type"]) for payload, _ in client.calls]

    def test_policy_add_can_use_rust_host_and_rotate_python_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            original_session = app.sessions.current_session_id

            response = app.handle_command("policy add secrets/**")
            listing = app.handle_command("ai ls .")

            self.assertIn("new_session=", response)
            self.assertNotEqual(original_session, app.sessions.current_session_id)
            self.assertEqual(client.current_execution_session.session_id, app.sessions.current_session_id)
            self.assertEqual(client.current_agent_session.agent_session_id, app.sessions.current_agent_session_id)
            self.assertEqual(["secrets/**"], app.policy.state.deny_globs)
            self.assertEqual("safe/", listing)
            self.assertEqual(
                [
                    "snapshot",
                    "policy_sync",
                    "policy_add_deny_rule",
                    "policy_sync",
                ],
                self._call_types(client),
            )

    def test_agent_rotate_can_use_rust_host_and_adopt_remote_session_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            original_execution_session = app.sessions.current_session_id
            original_agent_session = app.sessions.current_agent_session_id

            response = app.handle_command("agent rotate codex")

            self.assertIn("rotated_agent_session=", response)
            self.assertEqual(original_execution_session, app.sessions.current_session_id)
            self.assertNotEqual(original_agent_session, app.sessions.current_agent_session_id)
            self.assertEqual("codex", app.sessions.current_agent_session.agent_kind)
            self.assertEqual(client.current_agent_session.agent_session_id, app.sessions.current_agent_session_id)
            self.assertEqual(
                ["snapshot", "policy_sync", "rotate_agent_session"],
                self._call_types(client),
            )

    def test_review_commands_can_use_rust_host_and_keep_python_session_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            current_session_id = app.sessions.current_session_id
            current_agent_session_id = app.sessions.current_agent_session_id

            staged = app.handle_command("review stage src/app.py print('new')")
            proposal_id = staged.split("proposal_id=", 1)[1].split()[0]
            shown = app.handle_command(f"review show {proposal_id}")
            applied = app.handle_command(f"review apply {proposal_id}")
            audit = app.handle_command("audit list 10 all")
            events = app.handle_command("events list 10 review.proposal review-proposal")

            stage_payload = next(payload for payload, _ in client.calls if payload["type"] == "review_stage_write")
            self.assertEqual("review_stage_write", stage_payload["type"])
            self.assertEqual(current_session_id, stage_payload["session_id"])
            self.assertEqual(current_agent_session_id, stage_payload["agent_session_id"])
            self.assertIn("--- a/src/app.py", shown)
            self.assertIn(f"proposal_id={proposal_id}", applied)
            self.assertEqual("print('new')", target.read_text(encoding="utf-8").strip())
            self.assertIn("action=review.stage", audit)
            self.assertIn("action=review.apply", audit)
            self.assertIn("review.proposal.staged", events)
            self.assertIn("review.proposal.applied", events)
            self.assertIn("policy_sync", self._call_types(client))

    def test_review_apply_refreshes_workspace_index_cache_through_rust_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            initial = json.loads(app.handle_command("workspace index refresh json"))
            proposal_id = app.handle_command("review stage src/app.py print('newer output')").split(
                "proposal_id=",
                1,
            )[1].split()[0]

            app.handle_command(f"review apply {proposal_id}")
            shown = json.loads(app.handle_command("workspace index show json"))

            initial_entry = next(entry for entry in initial["entries"] if entry["path"] == "src/app.py")
            shown_entry = next(entry for entry in shown["entries"] if entry["path"] == "src/app.py")
            self.assertNotEqual(initial_entry["size"], shown_entry["size"])
            self.assertEqual(target.stat().st_size, shown_entry["size"])
            self.assertGreaterEqual(self._call_types(client).count("workspace_index_refresh"), 2)

    def test_external_policy_change_syncs_rust_host_before_review_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            previous_session_id = app.sessions.current_session_id

            PolicyStateStore(root).save(PolicyState(["secrets/**"], version=2))

            with self.assertRaises(PermissionError):
                app.handle_command("review stage secrets/token.txt changed")

            self.assertNotEqual(previous_session_id, app.sessions.current_session_id)
            self.assertEqual(["secrets/**"], app.policy.state.deny_globs)
            self.assertIn("policy_sync", self._call_types(client))
            self.assertEqual(client.current_execution_session.session_id, app.sessions.current_session_id)

    def test_workspace_commands_can_use_rust_host_without_touching_broker_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "nested").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "notes" / "nested" / "deep.txt").write_text("deep plan", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            app.handle_command("policy add secrets/**")
            before_metrics = app.handle_command("metrics json")

            listing = json.loads(app.handle_command("workspace list . json"))
            tree = json.loads(app.handle_command("workspace tree notes json"))
            grep = json.loads(app.handle_command("workspace grep plan . json"))
            after_metrics = app.handle_command("metrics json")

            self.assertEqual(before_metrics, after_metrics)
            self.assertEqual(["notes"], [entry["path"] for entry in listing["entries"]])
            self.assertEqual(
                ["notes/nested", "notes/nested/deep.txt", "notes/todo.txt"],
                [entry["path"] for entry in tree["entries"]],
            )
            self.assertEqual(
                ["notes/nested/deep.txt", "notes/todo.txt"],
                [match["path"] for match in grep["matches"]],
            )
            self.assertIn("workspace_list", self._call_types(client))
            self.assertIn("workspace_tree", self._call_types(client))
            self.assertIn("workspace_grep", self._call_types(client))
            self.assertGreaterEqual(self._call_types(client).count("metrics_show"), 2)

    def test_metrics_and_audit_commands_can_use_rust_host_broker_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            client = _FakeRustHostClient(root, runtime_root)
            BrokerStateStore(runtime_root / "broker" / "state.json").save(
                UsageMetrics(read_count=2, blocked_count=1),
                [
                    AuditEvent(
                        timestamp="2026-03-07T00:00:00Z",
                        session_id="sess-1",
                        action="read",
                        target=str(root / "notes" / "todo.txt"),
                        allowed=True,
                        detail="bytes=10",
                    ),
                    AuditEvent(
                        timestamp="2026-03-07T00:00:01Z",
                        session_id="sess-1",
                        action="read",
                        target=str(root / "secrets" / "token.txt"),
                        allowed=False,
                        detail="denied by policy",
                    ),
                ],
            )

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)

            metrics = json.loads(app.handle_command("metrics json"))
            audit = app.handle_command("audit list 10 blocked")

            self.assertEqual(2, metrics["read_count"])
            self.assertEqual(1, metrics["blocked_count"])
            self.assertEqual(2, metrics["audit_event_count"])
            self.assertIn("allowed=false", audit)
            self.assertIn("secrets/token.txt", audit.replace("\\", "/"))
            self.assertIn("metrics_show", self._call_types(client))
            self.assertIn("audit_list", self._call_types(client))

    def test_workspace_panel_command_can_use_rust_host_without_touching_broker_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "nested").mkdir()
            (root / "notes" / "nested" / "deep.txt").write_text("deep plan", encoding="utf-8")
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            app.handle_command("policy add secrets/**")
            before_metrics = app.handle_command("metrics json")

            panel = json.loads(app.handle_command("workspace panel notes json"))

            after_metrics = app.handle_command("metrics json")
            self.assertEqual(before_metrics, after_metrics)
            self.assertEqual("panel", panel["kind"])
            self.assertEqual("notes", panel["target"])
            self.assertEqual(["secrets/**"], panel["deny_rules"])
            self.assertEqual(app.sessions.current_session_id, panel["execution_session_id"])
            self.assertEqual(app.sessions.current_agent_session_id, panel["agent_session_id"])
            self.assertEqual(
                [
                    {
                        "path": "notes/nested",
                        "deny_rule": "notes/nested/**",
                    },
                    {
                        "path": "notes/nested/deep.txt",
                        "deny_rule": "notes/nested/deep.txt",
                    },
                    {
                        "path": "notes/todo.txt",
                        "deny_rule": "notes/todo.txt",
                    },
                ],
                [
                    {
                        "path": entry["path"],
                        "deny_rule": entry["deny_rule"],
                    }
                    for entry in panel["entries"]
                ],
            )
            self.assertIn("workspace_tree", self._call_types(client))

    def test_workspace_index_commands_can_use_rust_host_and_track_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            initial = json.loads(app.handle_command("workspace index show json"))
            app.handle_command("policy add secrets/**")
            refreshed = json.loads(app.handle_command("workspace index refresh json"))
            shown = json.loads(app.handle_command("workspace index show json"))

            self.assertTrue(initial["stale"])
            self.assertEqual(["policy"], initial["stale_reasons"])
            self.assertFalse(refreshed["stale"])
            self.assertEqual([], refreshed["stale_reasons"])
            self.assertEqual(["notes", "notes/todo.txt"], [entry["path"] for entry in refreshed["entries"]])
            self.assertEqual(refreshed["entries"], shown["entries"])
            self.assertEqual([], shown["stale_reasons"])
            self.assertIn("workspace_index_show", self._call_types(client))
            self.assertIn("workspace_index_refresh", self._call_types(client))

    def test_runner_refresh_uses_rust_host_workspace_index_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            app.handle_command("workspace index refresh json")
            (root / "notes" / "new.txt").write_text("fresh file", encoding="utf-8")

            before = json.loads(app.handle_command("workspace index show json"))
            app.handle_command("runner refresh")
            after = json.loads(app.handle_command("workspace index show json"))

            self.assertNotIn("notes/new.txt", [entry["path"] for entry in before["entries"]])
            self.assertIn("notes/new.txt", [entry["path"] for entry in after["entries"]])
            self.assertGreaterEqual(self._call_types(client).count("workspace_index_refresh"), 2)

    def test_workspace_index_show_marks_workspace_stale_after_external_visible_change_in_rust_host_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            app.handle_command("workspace index refresh json")
            target.write_text("changed visible plan", encoding="utf-8")

            shown = json.loads(app.handle_command("workspace index show json"))

            self.assertTrue(shown["stale"])
            self.assertEqual(["workspace"], shown["stale_reasons"])
            self.assertGreaterEqual(self._call_types(client).count("workspace_index_show"), 1)


    def test_python_restart_adopts_rust_host_session_state(self) -> None:
        """Simulates Python app restart: a new AIIdeApp is created against an already-running
        Rust host that holds newer session/policy state than what Python had previously."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            client = _FakeRustHostClient(root, runtime_root)

            # First app instance — add a policy rule so Rust host rotates session
            app1 = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            app1.handle_command("policy add secrets/**")
            rust_session_after_policy = client.current_execution_session.session_id
            rust_agent_after_policy = client.current_agent_session.agent_session_id
            app1.close()

            # Second app instance — simulates Python restart, same Rust host
            app2 = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)

            # Python should adopt Rust host's current session state from initial snapshot
            self.assertEqual(rust_session_after_policy, app2.sessions.current_session_id)
            self.assertEqual(rust_agent_after_policy, app2.sessions.current_agent_session_id)
            self.assertEqual(["secrets/**"], app2.policy.state.deny_globs)
            app2.close()

    def test_policy_version_mismatch_after_external_store_update_triggers_sync(self) -> None:
        """When an external process updates the policy store between commands,
        the next command should pick up the change via Rust host sync."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            initial_session = app.sessions.current_session_id
            self.assertEqual([], app.policy.state.deny_globs)

            # External process writes a new policy version directly to the store
            PolicyStateStore(root).save(PolicyState(["notes/**"], version=5))

            # Next command triggers policy_sync through Rust host, which detects the change
            app.handle_command("status")
            self.assertNotEqual(initial_session, app.sessions.current_session_id)
            self.assertEqual(["notes/**"], app.policy.state.deny_globs)
            self.assertIn("policy_sync", self._call_types(client))
            app.close()

    def test_session_continuity_across_multiple_policy_rotations(self) -> None:
        """Verify that session IDs form a proper chain (rotated_from tracking)
        across multiple policy changes through Rust host."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            client = _FakeRustHostClient(root, runtime_root)

            app = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            session_chain = [app.sessions.current_session_id]

            for i in range(3):
                app.handle_command(f"policy add rule_{i}/**")
                session_chain.append(app.sessions.current_session_id)

            # All session IDs should be distinct
            self.assertEqual(len(session_chain), len(set(session_chain)))
            # Final session should match Rust host state
            self.assertEqual(client.current_execution_session.session_id, session_chain[-1])
            # Policy should have all rules
            self.assertEqual(
                ["rule_0/**", "rule_1/**", "rule_2/**"],
                app.policy.state.deny_globs,
            )
            app.close()

    def test_review_proposals_survive_python_restart_in_rust_host_mode(self) -> None:
        """Review proposals persisted by Rust host remain accessible after Python restart."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")
            client = _FakeRustHostClient(root, runtime_root)

            app1 = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            staged = app1.handle_command("review stage src/app.py print('new')")
            proposal_id = staged.split("proposal_id=", 1)[1].split()[0]
            app1.close()

            # Python restart — review state is loaded from shared store
            app2 = AIIdeApp(root, runtime_root=runtime_root, rust_host_client=client)
            app2.sync_review_state()
            listing = app2.handle_command("review list pending")

            self.assertIn(proposal_id, listing)
            self.assertEqual(1, app2.reviews.count_proposals(status="pending"))
            app2.close()


if __name__ == "__main__":
    unittest.main()
