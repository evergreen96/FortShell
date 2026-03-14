from __future__ import annotations

import json
import unittest

from ai_ide.models import UsageMetrics
from ai_ide.runtime_status_service import RuntimeStatusService


class RuntimeStatusServiceTests(unittest.TestCase):
    def test_status_snapshot_formats_text_and_json(self) -> None:
        service = RuntimeStatusService()
        snapshot = service.build_status_snapshot(
            execution_session_id="sess-1",
            execution_status="active",
            agent_session_id="agent-1",
            agent_kind="codex",
            agent_status="active",
            runner_mode="projected",
            strict_boundary_scope="workspace-only",
            policy_version=3,
            deny_rule_count=2,
            terminal_count=1,
            event_count=5,
            pending_review_count=4,
        )

        text = service.status_text(snapshot)
        payload = json.loads(service.to_json(snapshot.to_dict()))

        self.assertIn("execution_session=sess-1", text)
        self.assertIn("strict_boundary_scope=workspace-only", text)
        self.assertEqual("codex", payload["agent_kind"])
        self.assertEqual("workspace-only", payload["strict_boundary_scope"])
        self.assertEqual(4, payload["pending_review_count"])

    def test_metrics_snapshot_formats_text_and_json(self) -> None:
        service = RuntimeStatusService()
        snapshot = service.build_metrics_snapshot(
            UsageMetrics(
                list_count=1,
                read_count=2,
                write_count=3,
                grep_count=4,
                blocked_count=5,
                terminal_runs=6,
            ),
            audit_event_count=7,
        )

        text = service.metrics_text(snapshot)
        payload = json.loads(service.to_json(snapshot.to_dict()))

        self.assertIn("write=3", text)
        self.assertEqual(7, payload["audit_event_count"])
