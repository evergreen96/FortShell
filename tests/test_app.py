from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.app import AIIdeApp
from ai_ide.broker import MAX_AUDIT_ENTRIES, MAX_READ_FILE_BYTES
from ai_ide.command_access_service import CommandContext
from ai_ide.events import EventBus
from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME, INTERNAL_POLICY_STATE_FILENAME
from ai_ide.models import (
    AuditEvent,
    WriteProposal,
)
from ai_ide.runner_models import RunnerResult
from ai_ide.terminal_inbox import TerminalInboxEntry
from ai_ide.terminal_watch_manager import TerminalEventWatch
from ai_ide.windows_strict_helper_resolution import WINDOWS_STRICT_HELPER_RUST_DEV
from ai_ide.workspace_visibility_watcher import QueueSignalWorkspaceVisibilityWatcher


_RUST_DEV_HELPER_READ_BOUNDARY_CACHE: dict[Path, bool] = {}


def _rust_dev_helper_supports_read_boundary(workspace: Path) -> bool:
    workspace = workspace.resolve()
    cached = _RUST_DEV_HELPER_READ_BOUNDARY_CACHE.get(workspace)
    if cached is not None:
        return cached

    repo_root = Path(__file__).resolve().parents[1]
    helper_root = workspace.parent / "helper-capability"
    command = [
        "cargo",
        "run",
        "--quiet",
        "--manifest-path",
        str((repo_root / "rust" / "Cargo.toml").resolve()),
        "-p",
        "ai-ide-windows-helper",
        "--",
        "--workspace",
        str(workspace),
        "--cwd",
        "/workspace",
        "--setenv",
        "AI_IDE_SANDBOX_ROOT",
        "/workspace",
        "--setenv",
        "HOME",
        str((helper_root / "home").resolve()),
        "--setenv",
        "XDG_CACHE_HOME",
        str((helper_root / "cache").resolve()),
        "--setenv",
        "TMPDIR",
        str((helper_root / "tmp").resolve()),
        "--command",
        "echo __AI_IDE_FIXTURE__ .ai_ide_strict_fixture.txt",
    ]
    process = shutil.which("cargo")
    if process is None:
        _RUST_DEV_HELPER_READ_BOUNDARY_CACHE[workspace] = False
        return False
    completed = __import__("subprocess").run(command, capture_output=True, text=True, check=False)
    supported = (
        "__AI_IDE_FIXTURE__ restricted_token=enabled" in completed.stdout
        and "__AI_IDE_FIXTURE__ read_boundary=enabled" in completed.stdout
    )
    _RUST_DEV_HELPER_READ_BOUNDARY_CACHE[workspace] = supported
    return supported


class AIIdeAppTests(unittest.TestCase):
    def test_policy_change_rotates_session_and_blocks_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            original_session = app.sessions.current_session_id
            original_agent = app.sessions.current_agent_session_id

            response = app.handle_command("policy add secrets/**")
            listing = app.handle_command("ai ls .")
            grep_results = app.handle_command("ai grep plan .")

            self.assertIn("new_session=", response)
            self.assertIn("new_agent_session=", response)
            self.assertNotEqual(original_session, app.sessions.current_session_id)
            self.assertNotEqual(original_agent, app.sessions.current_agent_session_id)
            self.assertEqual("safe/", listing)
            self.assertEqual("safe/todo.txt:1:visible plan", grep_results)

    def test_policy_state_persists_across_app_restart_and_metadata_stays_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            first_app.handle_command("policy add secrets/**")
            policy_path = root / INTERNAL_PROJECT_METADATA_DIR_NAME / INTERNAL_POLICY_STATE_FILENAME

            self.assertTrue(policy_path.exists())

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            policy_show = restarted_app.handle_command("policy show")
            listing = restarted_app.handle_command("ai ls .")
            grep_results = restarted_app.handle_command("ai grep plan .")

            self.assertIn("secrets/**", policy_show)
            self.assertEqual("safe/", listing)
            self.assertEqual("safe/todo.txt:1:visible plan", grep_results)

    def test_metrics_and_audit_log_persist_across_app_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            first_app.handle_command("policy add secrets/**")
            first_app.handle_command("ai read safe/todo.txt")
            with self.assertRaises(PermissionError):
                first_app.broker.read_file("secrets/token.txt")

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            metrics = restarted_app.handle_command("metrics")
            audit = restarted_app.handle_command("audit list 10 blocked")

            self.assertIn("read=2", metrics)
            self.assertIn("blocked=1", metrics)
            self.assertIn("allowed=false", audit)
            self.assertIn("secrets/token.txt", audit)

    def test_audit_log_is_trimmed_on_app_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            app = AIIdeApp(root, runtime_root=runtime_root)
            for index in range(MAX_AUDIT_ENTRIES + 20):
                app.broker.audit_log.append(
                    AuditEvent(
                        timestamp=f"2026-03-13T00:00:{index % 60:02d}Z",
                        session_id="sess-1",
                        action="read",
                        target=f"target-{index}",
                        allowed=bool(index % 2),
                        detail="synthetic",
                    )
                )
            app.broker.state_store.save(app.broker.metrics, app.broker.audit_log)

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)

            self.assertEqual(MAX_AUDIT_ENTRIES, len(restarted_app.broker.audit_log))
            self.assertEqual("target-20", restarted_app.broker.audit_log[0].target)

    def test_ai_read_blocks_oversized_file_and_records_blocked_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "large.txt").write_text("x" * (MAX_READ_FILE_BYTES + 1), encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)

            with self.assertRaisesRegex(ValueError, "File too large to read safely"):
                app.handle_command("ai read notes/large.txt")

            metrics = app.handle_command("metrics")
            audit = app.handle_command("audit list 10 blocked")

            self.assertIn("blocked=1", metrics)
            self.assertIn("file too large", audit)

    def test_status_session_and_metrics_json_expose_structured_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("old\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            app.handle_command("ai write notes/todo.txt new")

            status_payload = json.loads(app.handle_command("status json"))
            session_payload = json.loads(app.handle_command("session show json"))
            metrics_payload = json.loads(app.handle_command("metrics json"))

            self.assertEqual("codex", status_payload["agent_kind"])
            self.assertEqual("projected", status_payload["runner_mode"])
            self.assertEqual("workspace-only", status_payload["strict_boundary_scope"])
            self.assertEqual(1, status_payload["pending_review_count"])
            self.assertEqual(status_payload["execution_session_id"], session_payload["execution_session_id"])
            self.assertEqual(1, metrics_payload["write_count"])

    def test_external_policy_change_syncs_and_preserves_agent_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")

            left = AIIdeApp(root, runtime_root=runtime_root)
            right = AIIdeApp(root, runtime_root=runtime_root)
            right.handle_command("agent rotate codex")
            previous_execution = right.sessions.current_session_id

            left.handle_command("policy add secrets/**")

            status = right.handle_command("status")
            listing = right.handle_command("ai ls .")

            self.assertNotEqual(previous_execution, right.sessions.current_session_id)
            self.assertEqual("codex", right.sessions.current_agent_session.agent_kind)
            self.assertIn("policy_version=", status)
            self.assertEqual("safe/", listing)

    def test_review_stage_apply_and_persist_across_app_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            staged = first_app.handle_command("review stage src/app.py print('new')")
            proposal_id = staged.split("proposal_id=", 1)[1].split()[0]

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            listing = restarted_app.handle_command("review list pending 10")
            shown = restarted_app.handle_command(f"review show {proposal_id}")
            applied = restarted_app.handle_command(f"review apply {proposal_id}")

            self.assertIn(proposal_id, listing)
            self.assertIn("+++ b/src/app.py", shown)
            self.assertIn(f"proposal_id={proposal_id}", applied)
            self.assertEqual("print('new')", (root / "src" / "app.py").read_text(encoding="utf-8").strip())

    def test_review_apply_refreshes_workspace_index_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
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
            self.assertFalse(shown["stale"])


    def test_review_proposals_drop_internal_metadata_targets_on_app_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            app = AIIdeApp(root, runtime_root=runtime_root)
            proposals = [
                WriteProposal(
                    proposal_id="proposal-valid",
                    target="src/file.py",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                    created_at="2026-03-13T00:00:00Z",
                    updated_at="2026-03-13T00:00:00Z",
                    status="pending",
                    base_sha256=None,
                    base_text=None,
                    proposed_text="print('ok')",
                ),
                WriteProposal(
                    proposal_id="proposal-internal",
                    target=f"{INTERNAL_PROJECT_METADATA_DIR_NAME}/policy.json",
                    session_id="sess-1",
                    agent_session_id="agent-1",
                    created_at="2026-03-13T00:02:00Z",
                    updated_at="2026-03-13T00:02:00Z",
                    status="pending",
                    base_sha256=None,
                    base_text=None,
                    proposed_text="blocked",
                ),
            ]
            app.reviews.state_store.save(proposals)

            restarted = AIIdeApp(root, runtime_root=runtime_root)

            self.assertEqual(
                ["proposal-valid"],
                [proposal.proposal_id for proposal in restarted.reviews.proposals],
            )



    def test_close_closes_visibility_monitor_and_rust_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            closed: list[str] = []

            class StubVisibility:
                def close(self) -> None:
                    closed.append("visibility")

            class StubRustClient:
                def close(self) -> None:
                    closed.append("rust")

            class StubRustControl:
                def __init__(self) -> None:
                    self.client = StubRustClient()

            app.workspace_visibility = StubVisibility()
            app.rust_control = StubRustControl()

            app.close()

            self.assertEqual(["visibility", "rust"], closed)

    def test_ai_write_stages_review_proposal_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("old\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            response = app.handle_command("ai write notes/todo.txt new")
            proposal_id = response.split("proposal_id=", 1)[1].split()[0]
            proposals = app.handle_command("review list pending 10")
            metrics = app.handle_command("metrics")
            audit = app.handle_command("audit list 10 all")
            events = app.handle_command("events list 10 review.proposal review-proposal")

            self.assertIn("staged proposal_id=", response)
            self.assertEqual("old\n", target.read_text(encoding="utf-8"))
            self.assertIn(proposal_id, proposals)
            self.assertIn("write=1", metrics)
            self.assertIn("action=review.stage", audit)
            self.assertIn("allowed=true", audit)
            self.assertIn("review.proposal.staged", events)
            self.assertIn(proposal_id, events)

    def test_review_apply_marks_conflict_when_target_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            staged = app.handle_command("review stage src/app.py print('new')")
            proposal_id = staged.split("proposal_id=", 1)[1].split()[0]
            target.write_text("print('changed')\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                app.handle_command(f"review apply {proposal_id}")

            listing = app.handle_command("review list conflict 10")
            audit = app.handle_command("audit list 10 blocked")
            events = app.handle_command("events list 10 review.proposal review-proposal")
            self.assertIn(proposal_id, listing)
            self.assertEqual("print('changed')", target.read_text(encoding="utf-8").strip())
            self.assertIn("action=review.apply", audit)
            self.assertIn("allowed=false", audit)
            self.assertIn("review.proposal.conflict", events)

    def test_unsafe_write_writes_immediately_and_audits_as_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("old\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            response = app.handle_command("unsafe write notes/todo.txt new")
            proposals = app.handle_command("review list pending 10")
            audit = app.handle_command("audit list 10 all")

            self.assertIn("written unsafe=true", response)
            self.assertEqual("new", target.read_text(encoding="utf-8").strip())
            self.assertEqual("(no proposals)", proposals)
            self.assertIn("action=unsafe.write", audit)

    def test_unsafe_write_refreshes_workspace_index_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("old\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            initial = json.loads(app.handle_command("workspace index refresh json"))

            app.handle_command("unsafe write notes/todo.txt much-longer-updated-text")
            shown = json.loads(app.handle_command("workspace index show json"))

            initial_entry = next(entry for entry in initial["entries"] if entry["path"] == "notes/todo.txt")
            shown_entry = next(entry for entry in shown["entries"] if entry["path"] == "notes/todo.txt")
            self.assertNotEqual(initial_entry["size"], shown_entry["size"])
            self.assertEqual(target.stat().st_size, shown_entry["size"])
            self.assertFalse(shown["stale"])

    def test_ai_write_direct_flag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("old\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)

            with self.assertRaises(ValueError):
                app.handle_command("ai write --direct notes/todo.txt new")

    def test_unsafe_write_requires_trusted_command_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("old\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)

            with self.assertRaises(PermissionError):
                app.handle_command(
                    "unsafe write notes/todo.txt new",
                    context=CommandContext.agent(),
                )

            audit = app.handle_command("audit list 10 blocked")
            metrics = app.handle_command("metrics")
            self.assertEqual("old", target.read_text(encoding="utf-8").strip())
            self.assertIn("action=unsafe.write", audit)
            self.assertIn("trusted control-plane command required", audit)
            self.assertIn("write=1", metrics)

    def test_ai_write_still_allowed_from_untrusted_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("old\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            response = app.handle_command(
                "ai write notes/todo.txt new",
                context=CommandContext.agent(),
            )

            self.assertIn("staged proposal_id=", response)
            self.assertEqual("old", target.read_text(encoding="utf-8").strip())

    def test_review_reject_records_audit_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            staged = app.handle_command("review stage src/app.py print('new')")
            proposal_id = staged.split("proposal_id=", 1)[1].split()[0]

            rejected = app.handle_command(f"review reject {proposal_id}")
            audit = app.handle_command("audit list 10 all")
            events = app.handle_command("events list 10 review.proposal review-proposal")

            self.assertIn(f"proposal_id={proposal_id}", rejected)
            self.assertIn("action=review.reject", audit)
            self.assertIn("review.proposal.rejected", events)

    def test_blocked_ai_write_stage_is_audited_and_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "secrets").mkdir()
            (root / "secrets" / "token.txt").write_text("secret\n", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")

            with self.assertRaises(PermissionError):
                app.handle_command("ai write secrets/token.txt changed")

            metrics = app.handle_command("metrics")
            audit = app.handle_command("audit list 10 blocked")

            self.assertIn("write=1", metrics)
            self.assertIn("action=review.stage", audit)
            self.assertIn("allowed=false", audit)

    def test_review_list_rejects_unknown_status_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            app = AIIdeApp(root, runtime_root=runtime_root)

            with self.assertRaises(ValueError):
                app.handle_command("review list unknown 10")

    def test_runner_exec_uses_projected_workspace_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")
            result = app.handle_command('runner exec python -c "import os; print(chr(10).join(sorted(os.listdir(\'.\'))))"')

            self.assertIn("[mode=projected", result)
            self.assertIn("safe", result)
            self.assertNotIn("secrets", result)

    def test_runner_exec_blocks_network_command_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("runner mode strict")
            result = app.handle_command("runner exec curl https://example.com")

            self.assertIn("[mode=strict", result)
            self.assertIn("blocked", result)

    def test_runner_exec_blocks_python_in_strict_preview_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("runner mode strict")
            with patch.object(app.runners.platform_adapter, "strict_backend_invocation", return_value=None):
                result = app.handle_command('runner exec python -c "print(1)"')

            self.assertIn("[mode=strict", result)
            self.assertIn("interpreter", result)

    def test_runner_probe_reports_backend_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            result = app.handle_command("runner probe")

            self.assertIn("platform=", result)
            self.assertIn("ready=", result)
            self.assertIn("status=", result)

    def test_runner_info_reports_structured_backend_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            payload = json.loads(app.handle_command("runner info"))

            self.assertEqual("projected", payload["mode"])
            self.assertIn("strict_backend_status", payload)
            self.assertIn("strict_backend_detail", payload)
            self.assertIn("strict_backend_contract_status", payload)
            self.assertIn("strict_backend_validation_status", payload)
            self.assertTrue(payload["strict_preview_guarded"])

    def test_runner_info_reports_invalid_contract_when_backend_invocation_is_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            with patch.object(
                app.runners.platform_adapter,
                "strict_probe",
                return_value=type(
                    "Probe",
                    (),
                    {
                        "platform_name": "windows",
                        "ready": True,
                        "backend": "wsl",
                        "status_code": "ready",
                        "detail": "ready",
                    },
                )(),
            ):
                with patch.object(
                    app.runners.platform_adapter,
                    "strict_backend_invocation",
                    return_value=type(
                        "Invocation",
                        (),
                        {
                            "backend": "wsl",
                            "command": ["sh", "-lc", "printf bad"],
                            "host_working_directory": root,
                            "working_directory": "/workspace",
                        },
                    )(),
                ):
                    payload = json.loads(app.handle_command("runner info"))

            self.assertFalse(payload["strict_backend_ready"])
            self.assertEqual("invalid_contract", payload["strict_backend_status"])
            self.assertEqual("invalid_contract", payload["strict_backend_contract_status"])

    def test_runner_validate_reports_skipped_when_backend_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.runners.strict_backend_fixture.strict_backend_health_provider = lambda: type(
                "Health",
                (),
                {
                    "platform_name": "windows",
                    "backend": "wsl",
                    "probe_ready": False,
                    "probe_status": "not_found",
                    "probe_detail": "not found",
                    "contract_valid": False,
                    "contract_status": "skipped",
                    "contract_detail": "probe not ready",
                    "ready": False,
                },
            )()
            app.runners.status_service.strict_backend_health_provider = (
                app.runners.strict_backend_fixture.strict_backend_health_provider
            )
            text = app.handle_command("runner validate")
            payload = json.loads(app.handle_command("runner validate json"))
            info = json.loads(app.handle_command("runner info"))

            self.assertIn("status=skipped", text)
            self.assertEqual("skipped", payload["status"])
            self.assertEqual("not found", payload["reason"])
            self.assertEqual("skipped", info["strict_backend_validation_status"])
            self.assertEqual("not found", info["strict_backend_validation_reason"])

    def test_runner_info_marks_cached_validation_as_stale_after_execution_session_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.runners.strict_backend_fixture.strict_backend_health_provider = lambda: type(
                "Health",
                (),
                {
                    "platform_name": "windows",
                    "backend": "wsl",
                    "probe_ready": False,
                    "probe_status": "not_found",
                    "probe_detail": "not found",
                    "contract_valid": False,
                    "contract_status": "skipped",
                    "contract_detail": "probe not ready",
                    "ready": False,
                },
            )()
            app.runners.status_service.strict_backend_health_provider = (
                app.runners.strict_backend_fixture.strict_backend_health_provider
            )

            app.handle_command("runner validate")
            app.handle_command("policy add secrets/**")
            payload = json.loads(app.handle_command("runner info"))

            self.assertEqual("stale", payload["strict_backend_validation_status"])
            self.assertEqual(
                "execution session changed after last validation",
                payload["strict_backend_validation_reason"],
            )

    def test_runner_info_marks_cached_validation_as_stale_after_visible_workspace_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            target = root / "safe" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            session_projection_root = runtime_root / "projections" / app.sessions.current_session_id

            def health():
                return type(
                    "Health",
                    (),
                    {
                        "platform_name": "linux",
                        "backend": "bwrap",
                        "probe_ready": True,
                        "probe_status": "ready",
                        "probe_detail": "ready",
                        "contract_valid": True,
                        "contract_status": "valid",
                        "contract_detail": "contract valid",
                        "ready": True,
                    },
                )()

            def strict_runner_run(command: str) -> RunnerResult:
                session_projection_root.mkdir(parents=True, exist_ok=True)
                (session_projection_root / ".ai_ide_strict_fixture.txt").write_text("fixture", encoding="utf-8")
                return RunnerResult(
                    mode="strict",
                    backend="bwrap",
                    returncode=0,
                    stdout=(
                        "__AI_IDE_FIXTURE__ sandbox=/workspace\n"
                        "__AI_IDE_FIXTURE__ home=/tmp/ai-ide-home\n"
                        "__AI_IDE_FIXTURE__ cache=/tmp/ai-ide-cache\n"
                        "__AI_IDE_FIXTURE__ denied_relative=hidden\n"
                        "__AI_IDE_FIXTURE__ denied_direct=hidden\n"
                        "__AI_IDE_FIXTURE__ direct_write=blocked\n"
                    ),
                    stderr="",
                    working_directory="/workspace",
                )

            app.runners.strict_backend_fixture.strict_backend_health_provider = health
            app.runners.status_service.strict_backend_health_provider = health
            app.runners.strict_backend_fixture.strict_runner_run = strict_runner_run

            app.handle_command("runner validate")
            target.write_text("visible plan with changed size", encoding="utf-8")
            payload = json.loads(app.handle_command("runner info"))

            self.assertEqual("stale", payload["strict_backend_validation_status"])
            self.assertEqual(
                "visible workspace changed after last validation",
                payload["strict_backend_validation_reason"],
            )

    def test_external_visible_workspace_change_publishes_event_on_command_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            target.write_text("changed visible plan", encoding="utf-8")

            app.handle_command("status")
            events = app.events.list_events(
                kind_prefix="workspace.visible",
                source_type="workspace",
                source_id="visible-tree",
            )

            self.assertEqual(1, len(events))
            self.assertEqual("poll", events[0].payload["reason"])
            self.assertEqual("external_or_unknown", events[0].payload["origin"])

    def test_policy_add_publishes_visible_workspace_change_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")
            events = app.events.list_events(
                kind_prefix="workspace.visible",
                source_type="workspace",
                source_id="visible-tree",
            )

            self.assertEqual(1, len(events))
            self.assertEqual("policy.add", events[0].payload["reason"])
            self.assertEqual("app", events[0].payload["origin"])

    def test_visibility_monitor_detects_external_change_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            first_app.handle_command("status")
            target.write_text("changed visible plan", encoding="utf-8")

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            restarted_app.handle_command("status")
            events = restarted_app.events.list_events(
                kind_prefix="workspace.visible",
                source_type="workspace",
                source_id="visible-tree",
            )

            self.assertEqual(1, len(events))
            self.assertEqual("poll", events[0].payload["reason"])
            self.assertEqual("external_or_unknown", events[0].payload["origin"])

    def test_app_accepts_workspace_visibility_watcher_and_publishes_watch_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            watcher = QueueSignalWorkspaceVisibilityWatcher()
            app = AIIdeApp(
                root,
                runtime_root=runtime_root,
                workspace_visibility_watcher=watcher,
            )
            try:
                target.write_text("changed visible plan", encoding="utf-8")
                watcher.notify_change()
                time.sleep(0.05)
                events = app.events.list_events(
                    kind_prefix="workspace.visible",
                    source_type="workspace",
                    source_id="visible-tree",
                )
            finally:
                app.close()

            self.assertEqual(1, len(events))
            self.assertEqual("watch", events[0].payload["reason"])
            self.assertEqual("backend", events[0].payload["origin"])

    def test_event_driven_visibility_backend_skips_command_boundary_poll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")

            watcher = QueueSignalWorkspaceVisibilityWatcher()
            app = AIIdeApp(
                root,
                runtime_root=runtime_root,
                workspace_visibility_watcher=watcher,
            )
            try:
                def fail_sync():
                    raise AssertionError("event-driven backend should skip command-boundary poll")

                app.workspace_visibility_backend.sync = fail_sync  # type: ignore[method-assign]
                status = app.handle_command("status")
            finally:
                app.close()

            self.assertIn("execution_session=", status)

    def test_workspace_index_stale_reasons_use_visibility_monitor_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            snapshot = app.refresh_workspace_index_snapshot()
            target.write_text("changed visible plan", encoding="utf-8")
            app.handle_command("status")
            app.workspace_index_builder.build_signature = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
                AssertionError("should use visibility monitor state")
            )

            self.assertEqual(["workspace"], app.workspace_index_stale_reasons(snapshot))

    def test_runner_info_uses_visibility_monitor_state_for_validation_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.runners.strict_backend_validation.record(
                session_id=app.sessions.current_session_id,
                backend="wsl",
                ready=True,
                status="passed",
                reason="ok",
                workspace_signature=app.workspace_visibility.current_state().signature,
            )
            target.write_text("changed visible plan", encoding="utf-8")
            app.handle_command("status")
            app.runners.workspace_index_builder.build_signature = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
                AssertionError("should use visibility monitor state")
            )

            payload = json.loads(app.handle_command("runner info"))

            self.assertEqual("stale", payload["strict_backend_validation_status"])
            self.assertEqual(
                "visible workspace changed after last validation",
                payload["strict_backend_validation_reason"],
            )

    def test_policy_rotation_cleans_stale_projection_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            initial_session = app.sessions.current_session_id
            initial_manifest = app.runners.refresh_projection()

            app.handle_command("policy add secrets/**")
            next_session = app.sessions.current_session_id
            next_manifest = app.runners.refresh_projection()

            self.assertNotEqual(initial_session, next_session)
            self.assertFalse(initial_manifest.root.exists())
            self.assertTrue(next_manifest.root.exists())

    def test_runner_refresh_refreshes_workspace_index_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("workspace index refresh json")
            (root / "safe" / "new.txt").write_text("fresh file", encoding="utf-8")

            before = json.loads(app.handle_command("workspace index show json"))
            app.handle_command("runner refresh")
            after = json.loads(app.handle_command("workspace index show json"))

            self.assertNotIn("safe/new.txt", [entry["path"] for entry in before["entries"]])
            self.assertIn("safe/new.txt", [entry["path"] for entry in after["entries"]])
            self.assertFalse(after["stale"])

    def test_agent_rotation_keeps_execution_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            execution_session = app.sessions.current_session_id
            first_agent = app.sessions.current_agent_session_id

            result = app.handle_command("agent rotate claude")

            self.assertIn("rotated_agent_session=", result)
            self.assertEqual(execution_session, app.sessions.current_session_id)
            self.assertNotEqual(first_agent, app.sessions.current_agent_session_id)

    def test_agent_show_includes_adapter_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            result = app.handle_command("agent show")

            self.assertIn("adapter_status=virtual", result)
            self.assertIn("adapter_available=false", result)
            self.assertIn("transport=session-placeholder", result)
            self.assertIn("io_pref=session-placeholder", result)
            self.assertIn("provider=pipe-only", result)
            self.assertIn("pty=false", result)
            self.assertIn("io=pipe", result)
            self.assertIn("transport_status=native", result)
            self.assertIn("launchable=false", result)

    def test_agent_registry_reports_known_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            result = app.handle_command("agent registry")

            self.assertIn("claude", result)
            self.assertIn("codex", result)
            self.assertIn("gemini", result)
            self.assertIn("opencode", result)

    def test_agent_plan_reports_launch_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            with patch("ai_ide.agents.shutil.which", return_value="/tmp/codex"):
                result = app.handle_command("agent plan codex")

            self.assertIn("agent_kind=codex", result)
            self.assertIn("status=ready", result)
            self.assertIn("argv=/tmp/codex", result)
            self.assertIn("io_pref=pty_preferred", result)
            self.assertIn("provider=pipe-only", result)
            self.assertIn("pty=false", result)
            self.assertIn("io=pipe", result)
            self.assertIn("transport_status=degraded", result)
            self.assertIn("launchable=true", result)

    def test_agent_transport_reports_runtime_transport_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            with patch("ai_ide.agents.shutil.which", return_value="/tmp/codex"):
                result = app.handle_command("agent transport codex --mode strict")

            self.assertIn("agent_kind=codex", result)
            self.assertIn("mode=strict", result)
            self.assertIn("requested_io=pty_preferred", result)
            self.assertIn("provider=pipe-only", result)
            self.assertIn("pty=false", result)
            self.assertIn("io=pipe", result)
            self.assertIn("transport_status=degraded", result)
            self.assertIn("launchable=true", result)
            self.assertIn("runtime has no PTY transport yet", result)

    def test_agent_rotate_rejects_unknown_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)

            with self.assertRaisesRegex(ValueError, "Unknown agent kind: mystery"):
                app.handle_command("agent rotate mystery")

    def test_agent_exec_runs_current_adapter_inside_runner_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                result = app.handle_command('agent exec -- -c "print(\'agent-ok\')"')

            self.assertIn("kind=codex", result)
            self.assertIn("mode=projected", result)
            self.assertIn("io=pipe", result)
            self.assertIn("transport_status=degraded", result)
            self.assertIn("status=completed", result)
            self.assertIn("agent-ok", result)

    def test_agent_history_lists_current_execution_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                app.handle_command('agent exec -- -c "print(\'agent-history\')"')
            result = app.handle_command("agent history")

            self.assertIn("kind=codex", result)
            self.assertIn("status=completed", result)
            self.assertIn(sys.executable, result)

    def test_agent_metadata_json_commands_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                show_payload = json.loads(app.handle_command("agent show json"))
                list_payload = json.loads(app.handle_command("agent list json"))
                registry_payload = json.loads(app.handle_command("agent registry json"))
                plan_payload = json.loads(app.handle_command("agent plan json"))
                transport_payload = json.loads(app.handle_command("agent transport json"))

            self.assertEqual("codex", show_payload["session"]["agent_kind"])
            self.assertEqual("codex", show_payload["probe"]["kind"])
            self.assertEqual("codex", show_payload["launch_plan"]["kind"])
            self.assertEqual("codex", show_payload["transport_plan"]["agent_kind"])
            self.assertIn("codex", [item["agent_kind"] for item in list_payload])
            self.assertIn("codex", [item["kind"] for item in registry_payload])
            self.assertEqual("codex", plan_payload["launch_plan"]["kind"])
            self.assertEqual("codex", transport_payload["agent_kind"])
            self.assertIn("resolved_io_mode", transport_payload)

    def test_agent_start_poll_and_stop_manage_streaming_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import time; print(\'stream-app\', flush=True); time.sleep(5)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.2)
                polled = app.handle_command(f"agent poll {run_id}")
                stopped = app.handle_command(f"agent stop {run_id}")

            self.assertIn("status=running", started)
            self.assertIn("stream-app", polled)
            self.assertIn("status=stopped", stopped)

    def test_agent_poll_history_watch_and_inbox_json_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import time; print(\'agent-json\', flush=True); time.sleep(5)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                watch_payload = json.loads(app.handle_command(f"agent watch {run_id} observer --replay json"))
                time.sleep(0.2)
                poll_payload = json.loads(app.handle_command(f"agent poll {run_id} json"))
                history_payload = json.loads(app.handle_command("agent history json"))
                watches_payload = json.loads(app.handle_command("agent watches json"))
                inbox_payload = json.loads(app.handle_command(f"agent inbox {watch_payload['watch_id']} 20 json"))
                app.handle_command(f"agent stop {run_id}")

            self.assertEqual(run_id, poll_payload["record"]["run_id"])
            self.assertEqual("running", poll_payload["record"]["status"])
            self.assertIn("source", poll_payload["process"])
            self.assertEqual(run_id, history_payload[0]["record"]["run_id"])
            self.assertEqual(watch_payload["watch_id"], watches_payload[0]["watch_id"])
            self.assertEqual("observer", watch_payload["name"])
            self.assertIn("agent.run.started", [event["kind"] for event in inbox_payload])
            self.assertIn("agent.run.stdout", [event["kind"] for event in inbox_payload])

    def test_agent_poll_reports_helper_process_status_for_active_strict_helper_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            helper_script = Path(__file__).resolve().parents[1] / "ai_ide" / "windows_restricted_host_helper_stub.py"
            helper_command = f"{sys.executable} {helper_script}"

            with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
                with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                    started = app.handle_command(
                        'agent start --mode strict -- -u -c "import time; print(\'helper-poll\', flush=True); time.sleep(5)"'
                    )
                    run_id = started.split()[0].split("=", 1)[1]
                    time.sleep(0.2)
                    polled = app.handle_command(f"agent poll {run_id}")
                    app.handle_command(f"agent stop {run_id}")

            self.assertIn("process_source=helper-control", polled)
            self.assertIn("process_state=running", polled)
            self.assertIn("backend=restricted-host-helper", polled)

    def test_agent_history_reports_helper_process_status_for_active_strict_helper_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            helper_script = Path(__file__).resolve().parents[1] / "ai_ide" / "windows_restricted_host_helper_stub.py"
            helper_command = f"{sys.executable} {helper_script}"

            with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
                with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                    started = app.handle_command(
                        'agent start --mode strict -- -u -c "import time; print(\'helper-history\', flush=True); time.sleep(5)"'
                    )
                    run_id = started.split()[0].split("=", 1)[1]
                    time.sleep(0.2)
                    history = app.handle_command("agent history")
                    app.handle_command(f"agent stop {run_id}")

            self.assertIn(run_id, history)
            self.assertIn("process_source=helper-control", history)
            self.assertIn("process_state=running", history)
            self.assertIn("backend=restricted-host-helper", history)

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_agent_exec_in_strict_mode_can_use_rust_dev_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            real_which = shutil.which

            with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
                with patch(
                    "ai_ide.agents.shutil.which",
                    side_effect=lambda name: sys.executable if name == "codex" else real_which(name),
                ):
                    result = app.handle_command(
                        'agent exec --mode strict -- -c "print(\'rust-helper-exec\')"'
                    )

            self.assertIn("backend=restricted-host-helper", result)
            self.assertIn("status=completed", result)
            self.assertIn("rust-helper-exec", result)

    def test_agent_watch_and_inbox_follow_run_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import time; print(\'watch-app\', flush=True); time.sleep(0.2); print(\'watch-done\', flush=True)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                watched = app.handle_command(f"agent watch {run_id} observer")
                watch_id = watched.split()[0].split("=", 1)[1]
                time.sleep(0.35)
                app.handle_command(f"agent poll {run_id}")
                inbox = app.handle_command(f"agent inbox {watch_id} 20")

            self.assertIn("name=observer", watched)
            self.assertIn("agent.run.stdout", inbox)
            self.assertIn("watch-done", inbox)

    def test_agent_watch_rejects_when_watch_limit_is_exceeded(self) -> None:
        from ai_ide.agent_watch_manager import MAX_AGENT_RUN_WATCHES

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command('agent start -- -u -c "import time; print(\'watch-limit\', flush=True); time.sleep(5)"')
                run_id = started.split()[0].split("=", 1)[1]
                for index in range(MAX_AGENT_RUN_WATCHES):
                    app.handle_command(f"agent watch {run_id} watch-{index}")

                with self.assertRaisesRegex(ValueError, "Too many agent watches"):
                    app.handle_command(f"agent watch {run_id} overflow")

                app.handle_command(f"agent stop {run_id}")

    def test_agent_watch_state_restores_across_app_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            first_app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = first_app.handle_command('agent start -- -u -c "print(\'restore-watch\', flush=True)"')
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.1)
                first_app.handle_command(f"agent poll {run_id}")
                watched = first_app.handle_command(f"agent watch {run_id} persisted --replay")
            watch_id = watched.split()[0].split("=", 1)[1]

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            watches = restarted_app.handle_command("agent watches")
            inbox = restarted_app.handle_command(f"agent inbox {watch_id} 20")

            self.assertIn(watch_id, watches)
            self.assertIn("persisted", watches)
            self.assertIn("agent.run.completed", inbox)



    def test_agent_history_restores_across_app_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            first_app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                first_app.handle_command('agent exec -- -c "print(\'restore-history\')"')
            run_id = first_app.agent_runtime.list_runs()[0].run_id

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            history = restarted_app.handle_command("agent history")

            self.assertIn(run_id, history)
            self.assertIn("status=completed", history)

    def test_running_agent_history_restores_as_interrupted_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            first_app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = first_app.handle_command(
                    'agent start -- -u -c "import time; print(\'restore-interrupted\', flush=True); time.sleep(5)"'
                )
            run_id = started.split()[0].split("=", 1)[1]

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            history = restarted_app.handle_command("agent history")
            first_app.agent_runtime.stop_run(run_id, reason="test cleanup")

            self.assertIn(run_id, history)
            self.assertIn("status=interrupted", history)

    def test_agent_gc_removes_stale_watch_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command('agent start -- -u -c "print(\'gc-agent\', flush=True)"')
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.1)
                app.handle_command(f"agent poll {run_id}")
            watched = app.handle_command(f"agent watch {run_id} stale")
            watch_id = watched.split()[0].split("=", 1)[1]
            app.agent_runtime.get_watch(watch_id).updated_at = "2020-01-01T00:00:00Z"

            removed = app.handle_command("agent gc 60")
            watches = app.handle_command("agent watches")

            self.assertIn("removed_agent_watches=1", removed)
            self.assertEqual("(no agent watches)", watches)

    def test_agent_send_delivers_input_to_running_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import sys, time; line=sys.stdin.readline().strip(); print(f\'app-received:{line}\', flush=True); time.sleep(0.2)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                sent = app.handle_command(f"agent send {run_id} hello-from-app")
                time.sleep(0.3)
                polled = app.handle_command(f"agent poll {run_id}")

            self.assertIn("sent_input", sent)
            self.assertIn("app-received:hello-from-app", polled)

    def test_policy_rotation_stops_active_agent_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import time; print(\'stale-agent\', flush=True); time.sleep(5)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                app.handle_command("policy add secrets/**")
                polled = app.handle_command(f"agent poll {run_id}")

            self.assertIn("status=stopped", polled)
            self.assertIn("became stale", polled)

    def test_events_list_reports_agent_and_terminal_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            left = app.handle_command("term new left").split()[1]
            right = app.handle_command("term new right").split()[1]
            app.handle_command(f"term msg {left} {right} hello")
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import time; print(\'event-agent\', flush=True); time.sleep(0.2)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.3)
                app.handle_command(f"agent poll {run_id}")
            events = app.handle_command("events list 20")

            self.assertIn("terminal.message.sent", events)

    def test_term_new_rejects_when_terminal_limit_is_exceeded(self) -> None:
        from ai_ide.terminal import MAX_TERMINALS

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_root = root / ".runtime"
            runtime_root.mkdir()
            app = AIIdeApp(root, runtime_root=runtime_root)

            for index in range(MAX_TERMINALS):
                app.handle_command(f"term new term-{index}")

            with self.assertRaisesRegex(ValueError, "Too many terminals"):
                app.handle_command("term new overflow")




    def test_events_tail_filters_from_cursor_and_kind_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            left_id = app.handle_command("term new left").split()[1]
            right_id = app.handle_command("term new right").split()[1]
            app.handle_command(f"term msg {left_id} {right_id} hello")
            first_event_id = app.handle_command("events list 1").split()[0]
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "print(\'tail-agent\', flush=True)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.1)
                app.handle_command(f"agent poll {run_id}")
            tail = app.handle_command(f"events tail {first_event_id} 20 agent.run")

            self.assertIn("agent.run.started", tail)
            self.assertNotIn("terminal.message.sent", tail)

    def test_events_pull_supports_source_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            left_id = app.handle_command("term new left").split()[1]
            right_id = app.handle_command("term new right").split()[1]
            other_id = app.handle_command("term new other").split()[1]
            app.handle_command(f"term msg {left_id} {right_id} first")
            app.handle_command(f"term msg {other_id} {right_id} second")

            pulled = app.handle_command(f"events pull ui-main 20 terminal.message terminal {left_id}")
            repeated = app.handle_command(f"events pull ui-main 20 terminal.message terminal {left_id}")

            self.assertIn(f"source=terminal:{left_id}", pulled)
            self.assertNotIn(f"source=terminal:{other_id}", pulled)
            self.assertEqual("(no events)", repeated)

    def test_events_pull_refreshes_active_agent_run_without_poll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import time; print(\'event-auto\', flush=True); time.sleep(0.2); print(\'event-done\', flush=True)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.35)

            pulled = app.handle_command(f"events pull ui-main 20 agent.run agent-run {run_id}")
            history = app.handle_command("agent history")

            self.assertIn("agent.run.stdout", pulled)
            self.assertIn("event-done", pulled)
            self.assertIn(f"{run_id}", history)
            self.assertIn("status=completed", history)

    def test_events_survive_app_restart_with_shared_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            left_id = first_app.handle_command("term new left").split()[1]
            right_id = first_app.handle_command("term new right").split()[1]
            first_app.handle_command(f"term msg {left_id} {right_id} persisted")

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            before = restarted_app.handle_command("events list 20")
            new_left = restarted_app.handle_command("term new left2").split()[1]
            new_right = restarted_app.handle_command("term new right2").split()[1]
            restarted_app.handle_command(f"term msg {new_left} {new_right} resumed")
            tail = restarted_app.handle_command("events tail evt-000001 20 terminal.message")

            self.assertIn("evt-000001", before)
            self.assertIn("terminal.message.sent", before)
            self.assertIn("evt-000002", tail)
            self.assertNotIn("evt-000001", tail)

    def test_events_pull_persists_consumer_cursor_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            left_id = first_app.handle_command("term new left").split()[1]
            right_id = first_app.handle_command("term new right").split()[1]
            first_app.handle_command(f"term msg {left_id} {right_id} first")
            first_pull = first_app.handle_command("events pull ui-main 1 terminal.message")

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            next_left = restarted_app.handle_command("term new left2").split()[1]
            next_right = restarted_app.handle_command("term new right2").split()[1]
            restarted_app.handle_command(f"term msg {next_left} {next_right} second")
            cursor_before = restarted_app.handle_command("events cursor ui-main")
            resumed_pull = restarted_app.handle_command("events pull ui-main 20 terminal.message")
            cursor_after = restarted_app.handle_command("events cursor ui-main")

            self.assertIn("evt-000001", first_pull)
            self.assertIn("cursor=evt-000001", cursor_before)
            self.assertIn("evt-000002", resumed_pull)
            self.assertNotIn("evt-000001", resumed_pull)
            self.assertIn("cursor=evt-000002", cursor_after)

    def test_events_list_refreshes_from_shared_runtime_across_app_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            second_app = AIIdeApp(root, runtime_root=runtime_root)

            left_id = first_app.handle_command("term new left").split()[1]
            right_id = first_app.handle_command("term new right").split()[1]
            first_app.handle_command(f"term msg {left_id} {right_id} first")

            third_id = second_app.handle_command("term new third").split()[1]
            fourth_id = second_app.handle_command("term new fourth").split()[1]
            second_app.handle_command(f"term msg {third_id} {fourth_id} second")

            events = first_app.handle_command("events list 20")

            self.assertIn("evt-000001", events)
            self.assertIn("evt-000002", events)
            self.assertIn("payload={'to_terminal_id':", events)

    def test_events_compact_preserves_cursor_anchor_and_latest_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            left_id = app.handle_command("term new left").split()[1]
            right_id = app.handle_command("term new right").split()[1]
            app.handle_command(f"term msg {left_id} {right_id} one")
            app.handle_command(f"term msg {left_id} {right_id} two")
            app.handle_command(f"term msg {left_id} {right_id} three")
            app.handle_command(f"term msg {left_id} {right_id} four")
            app.handle_command("events ack ui-main evt-000001")

            compacted = app.handle_command("events compact 2")
            listing = app.handle_command("events list 20")
            pulled = app.handle_command("events pull ui-main 20 terminal.message")

            self.assertIn("retained=3 removed=1", compacted)
            self.assertIn("evt-000001", listing)
            self.assertIn("evt-000003", listing)
            self.assertIn("evt-000004", listing)
            self.assertNotIn("evt-000002", listing)
            self.assertIn("evt-000003", pulled)
            self.assertIn("evt-000004", pulled)

    def test_events_gc_removes_stale_cursor_and_allows_anchor_to_trim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            left_id = app.handle_command("term new left").split()[1]
            right_id = app.handle_command("term new right").split()[1]
            app.handle_command(f"term msg {left_id} {right_id} one")
            app.handle_command(f"term msg {left_id} {right_id} two")
            app.handle_command(f"term msg {left_id} {right_id} three")
            app.events.set_cursor("stale-ui", "evt-000001", updated_at="2020-01-01T00:00:00Z")

            removed = app.handle_command("events gc 60")
            compacted = app.handle_command("events compact 1")
            listing = app.handle_command("events list 20")
            cursor = app.handle_command("events cursor stale-ui")

            self.assertIn("removed_cursors=1", removed)
            self.assertIn("retained=1 removed=2", compacted)
            self.assertIn("evt-000003", listing)
            self.assertNotIn("evt-000001", listing)
            self.assertIn("cursor=(none)", cursor)

    def test_events_ack_rejects_when_cursor_limit_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            app = AIIdeApp(root, runtime_root=runtime_root)
            event = app.events.publish(
                "terminal.message.sent",
                source_type="terminal",
                source_id="term-1",
                payload={"text": "seed"},
            )
            for index in range(EventBus.MAX_CURSORS):
                app.handle_command(f"events ack consumer-{index} {event.event_id}")

            with self.assertRaisesRegex(ValueError, "too many cursors"):
                app.handle_command(f"events ack consumer-overflow {event.event_id}")



    def test_term_watch_routes_agent_run_events_to_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            terminal_id = app.handle_command("term new watcher").split()[1]
            app.handle_command(f"term watch {terminal_id} agent.run")
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "print(\'watch-agent\', flush=True)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.1)
                app.handle_command(f"agent poll {run_id}")
            inbox = app.handle_command(f"term inbox {terminal_id}")

            self.assertIn("agent.run.started", inbox)
            self.assertIn("agent.run.completed", inbox)

    def test_term_watch_rejects_when_watch_limit_is_exceeded(self) -> None:
        from ai_ide.terminal_watch_manager import MAX_TERMINAL_EVENT_WATCHES

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            app = AIIdeApp(root, runtime_root=runtime_root)
            terminal_id = app.handle_command("term new watcher").split()[1]

            for index in range(MAX_TERMINAL_EVENT_WATCHES):
                app.handle_command(f"term watch {terminal_id} terminal.message terminal term-{index}")

            with self.assertRaisesRegex(ValueError, "Terminal watch limit exceeded"):
                app.handle_command(f"term watch {terminal_id} terminal.message terminal term-overflow")

    def test_term_inbox_refreshes_active_agent_run_without_poll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            terminal_id = app.handle_command("term new watcher").split()[1]
            app.handle_command(f"term watch {terminal_id} agent.run")
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import time; print(\'term-auto\', flush=True); time.sleep(0.2); print(\'term-done\', flush=True)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.35)

            inbox = app.handle_command(f"term inbox {terminal_id}")
            history = app.handle_command("agent history")

            self.assertIn("agent.run.stdout", inbox)
            self.assertIn("term-done", inbox)
            self.assertIn(run_id, history)
            self.assertIn("status=completed", history)

    def test_term_gc_removes_stale_watch_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            terminal_id = app.handle_command("term new watcher").split()[1]
            app.handle_command("agent rotate codex")
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "print(\'watch-agent\', flush=True)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                time.sleep(0.1)
                app.handle_command(f"agent poll {run_id}")
            app.handle_command(f"term attach {terminal_id} {run_id}")
            app.terminals.event_watches[terminal_id][0].updated_at = "2020-01-01T00:00:00Z"

            removed = app.handle_command("term gc 60")
            listing = app.handle_command("term list")

            self.assertIn("removed_terminal_watches=1", removed)
            self.assertIn("bound_run=(none)", listing)
            self.assertNotIn(terminal_id, app.terminals.bridge_watches)

    def test_term_watch_and_terminal_restore_across_app_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            watcher_id = first_app.handle_command("term new --host watcher").split()[1]
            first_app.handle_command(f"term watch {watcher_id} terminal.message")

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            listing = restarted_app.handle_command("term list")
            left_id = restarted_app.handle_command("term new --host left").split()[1]
            right_id = restarted_app.handle_command("term new --host right").split()[1]
            restarted_app.handle_command(f"term msg {left_id} {right_id} restored")
            inbox = restarted_app.handle_command(f"term inbox {watcher_id}")

            self.assertIn(watcher_id, listing)
            self.assertIn("transport=host", listing)
            self.assertIn("terminal.message.sent", inbox)
            self.assertIn("restored", inbox)


    def test_term_attach_and_input_bridge_to_agent_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            terminal_id = app.handle_command("term new --mode strict bridge").split()[1]
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import sys, time; line=sys.stdin.readline().strip(); print(f\'bridge:{line}\', flush=True); time.sleep(0.2)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                attached = app.handle_command(f"term attach {terminal_id} {run_id}")
                sent = app.handle_command(f"term input {terminal_id} hello-bridge")
                time.sleep(0.3)
                app.handle_command(f"agent poll {run_id}")
                inbox = app.handle_command(f"term inbox {terminal_id}")

            self.assertIn(f"run_id={run_id}", attached)
            self.assertIn("sent_terminal_input", sent)
            self.assertIn("agent.run.stdout", inbox)
            self.assertIn("bridge:hello-bridge", inbox)

    def test_term_attach_and_list_report_helper_process_status_for_active_strict_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            terminal_id = app.handle_command("term new --mode strict bridge").split()[1]
            helper_script = Path(__file__).resolve().parents[1] / "ai_ide" / "windows_restricted_host_helper_stub.py"
            helper_command = f"{sys.executable} {helper_script}"

            with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
                with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                    started = app.handle_command(
                        'agent start --mode strict -- -u -c "import time; print(\'term-helper\', flush=True); time.sleep(5)"'
                    )
                    run_id = started.split()[0].split("=", 1)[1]
                    time.sleep(0.2)
                    attached = app.handle_command(f"term attach {terminal_id} {run_id}")
                    listing = app.handle_command("term list")
                    app.handle_command(f"agent stop {run_id}")

            self.assertIn(f"run_id={run_id}", attached)
            self.assertIn("run_status=running", attached)
            self.assertIn("process_source=helper-control", attached)
            self.assertIn("process_state=running", attached)
            self.assertIn(f"bound_run={run_id}", listing)
            self.assertIn("bound_run_status=running", listing)
            self.assertIn("bound_run_process_source=helper-control", listing)
            self.assertIn("bound_run_process_state=running", listing)
            self.assertIn("bound_run_backend=restricted-host-helper", listing)

    def test_term_list_json_reports_machine_readable_helper_process_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            terminal_id = app.handle_command("term new --mode strict bridge").split()[1]
            helper_script = Path(__file__).resolve().parents[1] / "ai_ide" / "windows_restricted_host_helper_stub.py"
            helper_command = f"{sys.executable} {helper_script}"

            with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
                with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                    started = app.handle_command(
                        'agent start --mode strict -- -u -c "import time; print(\'term-helper-json\', flush=True); time.sleep(5)"'
                    )
                    run_id = started.split()[0].split("=", 1)[1]
                    time.sleep(0.2)
                    app.handle_command(f"term attach {terminal_id} {run_id}")
                    payload = json.loads(app.handle_command("term list json"))
                    app.handle_command(f"agent stop {run_id}")

            terminal_payload = next(item for item in payload if item["terminal_id"] == terminal_id)
            self.assertEqual("runner", terminal_payload["transport"])
            self.assertEqual("strict", terminal_payload["runner_mode"])
            self.assertEqual(run_id, terminal_payload["bound_run"]["run_id"])
            self.assertEqual("restricted-host-helper", terminal_payload["bound_run"]["backend"])
            self.assertEqual("helper-control", terminal_payload["bound_run"]["process_source"])
            self.assertEqual("running", terminal_payload["bound_run"]["process_state"])

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_term_show_json_can_use_rust_dev_helper_for_active_strict_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            terminal_id = app.handle_command("term new --mode strict bridge").split()[1]
            real_which = shutil.which

            with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
                with patch(
                    "ai_ide.agents.shutil.which",
                    side_effect=lambda name: sys.executable if name == "codex" else real_which(name),
                ):
                    started = app.handle_command(
                        'agent start --mode strict -- -u -c "import time; print(\'term-rust-helper\', flush=True); time.sleep(5)"'
                    )
                    run_id = started.split()[0].split("=", 1)[1]
                    time.sleep(0.2)
                    app.handle_command(f"term attach {terminal_id} {run_id}")
                    deadline = time.monotonic() + 25.0
                    while True:
                        payload = json.loads(app.handle_command(f"term show {terminal_id} json"))
                        if payload["bound_run"]["process_source"] == "helper-control":
                            break
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.2)
                    app.handle_command(f"agent stop {run_id}")

            self.assertEqual(terminal_id, payload["terminal_id"])
            self.assertEqual("runner", payload["transport"])
            self.assertEqual("strict", payload["runner_mode"])
            self.assertEqual(run_id, payload["bound_run"]["run_id"])
            self.assertEqual("restricted-host-helper", payload["bound_run"]["backend"])
            self.assertEqual("helper-control", payload["bound_run"]["process_source"])
            self.assertEqual("running", payload["bound_run"]["process_state"])

    def test_term_watch_and_inbox_json_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            terminal_id = app.handle_command("term new watcher").split()[1]

            watch_payload = json.loads(app.handle_command(f"term watch {terminal_id} agent.run agent-run run-1 json"))
            app.events.publish("agent.run.started", source_type="agent-run", source_id="run-1", payload={"step": 1})
            inbox_payload = json.loads(app.handle_command(f"term inbox {terminal_id} json"))
            watches_payload = json.loads(app.handle_command(f"term watches {terminal_id} json"))

            self.assertEqual(terminal_id, watch_payload["terminal_id"])
            self.assertEqual("agent.run", watch_payload["kind_prefix"])
            self.assertEqual("agent-run", watch_payload["source_type"])
            self.assertEqual("run-1", watch_payload["source_id"])
            self.assertEqual([watch_payload["watch_id"]], inbox_payload["watch_ids"])
            self.assertIn("agent.run.started", inbox_payload["messages"][0])
            self.assertEqual("runtime-event", inbox_payload["entries"][0]["kind"])
            self.assertEqual("agent.run.started", inbox_payload["entries"][0]["event_kind"])
            self.assertEqual(watch_payload["watch_id"], watches_payload[0]["watch_id"])
            self.assertFalse(watches_payload[0]["bridge"])

    def test_term_attach_json_marks_bridge_watch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("agent rotate codex")
            terminal_id = app.handle_command("term new bridge").split()[1]
            with patch("ai_ide.agents.shutil.which", return_value=sys.executable):
                started = app.handle_command(
                    'agent start -- -u -c "import time; print(\'bridge-json\', flush=True); time.sleep(5)"'
                )
                run_id = started.split()[0].split("=", 1)[1]
                payload = json.loads(app.handle_command(f"term attach {terminal_id} {run_id} json"))
                watch_payload = json.loads(app.handle_command(f"term watches {terminal_id} json"))
                app.handle_command(f"agent stop {run_id}")

            self.assertEqual(terminal_id, payload["terminal_id"])
            self.assertEqual(run_id, payload["bound_run"]["run_id"])
            self.assertEqual(1, len(watch_payload))
            self.assertTrue(watch_payload[0]["bridge"])
            self.assertEqual("agent.run", watch_payload[0]["kind_prefix"])

    def test_new_terminal_binds_to_current_execution_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            execution_session = app.sessions.current_session_id

            result = app.handle_command("term new work")
            listing = app.handle_command("term list")

            self.assertIn("exec=" + execution_session, result)
            self.assertIn("transport=runner", result)
            self.assertIn("mode=projected", result)
            self.assertIn("status=active", result)
            self.assertIn("exec=" + execution_session, listing)
            self.assertIn("transport=runner", listing)
            self.assertIn("status=active", listing)

    def test_term_run_uses_runner_boundary_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "secrets").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")
            (root / "secrets" / "token.txt").write_text("hidden plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            app.handle_command("policy add secrets/**")
            created = app.handle_command("term new work")
            terminal_id = created.split()[1]
            result = app.handle_command(
                f'term run {terminal_id} python -c "import os; print(chr(10).join(sorted(os.listdir(\'.\'))))"'
            )

            self.assertIn("[transport=runner mode=projected", result)
            self.assertIn("safe", result)
            self.assertNotIn("secrets", result)

    def test_new_host_terminal_is_explicitly_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            created = app.handle_command("term new --host ops")
            listing = app.handle_command("term list")

            self.assertIn("transport=host", created)
            self.assertIn("unsafe=true", created)
            self.assertIn("exec=(host)", created)
            self.assertIn("status=active", created)
            self.assertIn("transport=host", listing)
            self.assertIn("unsafe=true", listing)

    def test_restored_runner_terminal_is_marked_stale_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            first_app = AIIdeApp(root, runtime_root=runtime_root)
            terminal_id = first_app.handle_command("term new work").split()[1]

            restarted_app = AIIdeApp(root, runtime_root=runtime_root)
            listing = restarted_app.handle_command("term list")

            self.assertIn(terminal_id, listing)
            self.assertIn("status=stale", listing)

    def test_policy_rotation_stales_existing_runner_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "safe").mkdir()
            (root / "safe" / "todo.txt").write_text("visible plan", encoding="utf-8")

            app = AIIdeApp(root, runtime_root=runtime_root)
            created = app.handle_command("term new work")
            terminal_id = created.split()[1]

            app.handle_command("policy add secrets/**")
            listing = app.handle_command("term list")
            result = app.handle_command(f"term run {terminal_id} echo hello")

            self.assertIn("status=stale", listing)
            self.assertIn("blocked=true", result)
            self.assertIn("stale execution session", result)


if __name__ == "__main__":
    unittest.main()
