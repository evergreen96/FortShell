from __future__ import annotations

import unittest

from backend.agents import AgentAdapter, AgentRegistry
from backend.agent_run_ledger import MAX_AGENT_RUN_HISTORY, AgentRunLedger
from core.models import AgentRunRecord
from backend.runner import RunnerResult


class AgentRunLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = AgentRunLedger(lambda: "2026-03-07T00:00:00Z")
        self.registry = AgentRegistry([AgentAdapter("codex", "Codex CLI", ("codex",))])

    def test_create_started_run_tracks_running_history(self) -> None:
        record = self.ledger.create_started_run(
            agent_session_id="agent-1234",
            execution_session_id="exec-1234",
            agent_kind="codex",
            runner_mode="projected",
            backend="projected",
            io_mode="pipe",
            transport_status="degraded",
            argv=["codex", "--version"],
            pid=4242,
        )

        self.assertEqual("running", record.status)
        self.assertEqual(4242, record.pid)
        self.assertEqual(record, self.ledger.get_run(record.run_id))

    def test_create_completed_run_marks_blocked_from_guard_result(self) -> None:
        plan = self.registry.launch_plan("codex")
        result = RunnerResult(
            mode="projected",
            backend="guard",
            returncode=126,
            stdout="",
            stderr="blocked",
            working_directory="",
        )

        record = self.ledger.create_completed_run(
            agent_session_id="agent-1234",
            execution_session_id="exec-1234",
            agent_kind="codex",
            runner_mode="projected",
            io_mode="pipe",
            transport_status="degraded",
            argv=["codex", "--version"],
            launch_plan=plan,
            result=result,
        )

        self.assertEqual("blocked", record.status)
        self.assertEqual(126, record.returncode)

    def test_reconcile_restored_runs_marks_running_records_interrupted(self) -> None:
        record = self.ledger.create_started_run(
            agent_session_id="agent-5678",
            execution_session_id="exec-5678",
            agent_kind="codex",
            runner_mode="strict",
            backend="strict-preview",
            io_mode="pipe",
            transport_status="degraded",
            argv=["codex"],
            pid=1111,
        )

        changed = self.ledger.reconcile_restored_runs()

        self.assertTrue(changed)
        self.assertEqual("interrupted", record.status)
        self.assertEqual(-2, record.returncode)
        self.assertIn("lost live process handle", record.stderr)

    def test_create_started_run_trims_history(self) -> None:
        for index in range(MAX_AGENT_RUN_HISTORY + 2):
            self.ledger.create_started_run(
                agent_session_id=f"agent-{index}",
                execution_session_id=f"exec-{index}",
                agent_kind="codex",
                runner_mode="projected",
                backend="projected",
                io_mode="pipe",
                transport_status="native",
                argv=["codex"],
                pid=index,
            )

        self.assertEqual(MAX_AGENT_RUN_HISTORY, len(self.ledger.runs))
        self.assertEqual("exec-2", self.ledger.runs[0].execution_session_id)

    def test_replace_state_trims_history(self) -> None:
        runs = [
            AgentRunRecord(
                run_id=f"run-{index}",
                agent_session_id=f"agent-{index}",
                execution_session_id=f"exec-{index}",
                agent_kind="codex",
                runner_mode="projected",
                backend="projected",
                io_mode="pipe",
                transport_status="native",
                argv=["codex"],
                created_at="2026-03-07T00:00:00Z",
                ended_at=None,
                pid=None,
                returncode=-1,
                status="running",
                stdout="",
                stderr="",
            )
            for index in range(MAX_AGENT_RUN_HISTORY + 2)
        ]

        self.ledger.replace_state(runs)

        self.assertEqual(MAX_AGENT_RUN_HISTORY, len(self.ledger.runs))
        self.assertEqual("exec-2", self.ledger.runs[0].execution_session_id)


if __name__ == "__main__":
    unittest.main()
