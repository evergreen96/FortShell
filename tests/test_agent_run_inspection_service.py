from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from ai_ide.agent_run_inspection_service import AgentRunInspectionService
from ai_ide.models import AgentRunRecord
from ai_ide.runner_models import RunnerProcessControl, RunnerProcessHandle
from ai_ide.windows_strict_helper_protocol import WindowsStrictHelperStatusMessage


class _FakeProcess:
    def __init__(self, *, pid: int = 4242, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode


class _StubProcessControlService:
    def __init__(self, response: WindowsStrictHelperStatusMessage | None) -> None:
        self.response = response

    def request_status(self, handle, *, timeout_seconds=0.5, poll_interval_seconds=0.05):
        return self.response


class AgentRunInspectionServiceTests(unittest.TestCase):
    def _record(self) -> AgentRunRecord:
        return AgentRunRecord(
            run_id="run-1234",
            agent_session_id="agent-1234",
            execution_session_id="sess-1234",
            agent_kind="codex",
            runner_mode="strict",
            backend="restricted-host-helper",
            io_mode="pipe",
            transport_status="degraded",
            argv=["codex"],
            created_at="2026-03-08T00:00:00Z",
            ended_at=None,
            pid=4242,
            returncode=-1,
            status="running",
            stdout="",
            stderr="",
        )

    def test_inspect_without_handle_uses_recorded_state(self) -> None:
        record = self._record()
        record.status = "completed"
        record.ended_at = "2026-03-08T00:01:00Z"
        record.returncode = 0
        service = AgentRunInspectionService()

        inspection = service.inspect(record)

        self.assertEqual("recorded", inspection.process.source)
        self.assertEqual("exited", inspection.process.state)
        self.assertEqual(0, inspection.process.returncode)

    def test_inspect_active_helper_uses_helper_control_status_when_available(self) -> None:
        record = self._record()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            handle = RunnerProcessHandle(
                run_id="proc-1234",
                mode="strict",
                backend="restricted-host-helper",
                working_directory=str(root),
                process=_FakeProcess(),  # type: ignore[arg-type]
                stdin_file=io.StringIO(),
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                stdout_file=io.StringIO(),
                stderr_file=io.StringIO(),
                control=RunnerProcessControl(
                    kind="file",
                    control_file=root / "control.json",
                    response_file=root / "status.json",
                    status_command="status",
                ),
            )
            service = AgentRunInspectionService(
                _StubProcessControlService(
                    WindowsStrictHelperStatusMessage(
                        request_id="status-1234",
                        run_id="proc-1234",
                        backend="restricted-host-helper",
                        state="running",
                        pid=9876,
                    )
                )
            )

            inspection = service.inspect(record, handle)

        self.assertEqual("helper-control", inspection.process.source)
        self.assertEqual("running", inspection.process.state)
        self.assertEqual(9876, inspection.process.pid)

    def test_inspect_active_process_falls_back_to_local_when_no_helper_status(self) -> None:
        record = self._record()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            handle = RunnerProcessHandle(
                run_id="proc-1234",
                mode="strict",
                backend="restricted-host-helper",
                working_directory=str(root),
                process=_FakeProcess(pid=3333),  # type: ignore[arg-type]
                stdin_file=io.StringIO(),
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                stdout_file=io.StringIO(),
                stderr_file=io.StringIO(),
            )
            service = AgentRunInspectionService(_StubProcessControlService(None))

            inspection = service.inspect(record, handle)

        self.assertEqual("local", inspection.process.source)
        self.assertEqual("running", inspection.process.state)
        self.assertEqual(3333, inspection.process.pid)


if __name__ == "__main__":
    unittest.main()
