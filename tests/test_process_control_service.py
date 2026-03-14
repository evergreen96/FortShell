from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ai_ide.process_control_service import ProcessControlService
from ai_ide.runner_models import RunnerProcessControl, RunnerProcessHandle
from ai_ide.windows_strict_helper_protocol import (
    WindowsStrictHelperStatusMessage,
    read_helper_control_message,
    write_helper_status_message,
)


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None

    def poll(self):
        return self.returncode


class ProcessControlServiceTests(unittest.TestCase):
    def test_send_command_writes_structured_control_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_file = root / "control" / "helper-control.json"
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
                    control_file=control_file,
                    stop_command="stop",
                ),
            )

            request_id = ProcessControlService().send_command(handle, "stop")

            self.assertIsNotNone(request_id)
            message = read_helper_control_message(control_file)
            self.assertIsNotNone(message)
            assert message is not None
            self.assertEqual("stop", message.command)
            self.assertEqual("proc-1234", message.run_id)
            self.assertEqual("restricted-host-helper", message.backend)
            self.assertEqual(request_id, message.request_id)

    def test_request_status_returns_matching_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_file = root / "control" / "helper-control.json"
            response_file = root / "control" / "helper-status.json"
            handle = RunnerProcessHandle(
                run_id="proc-5678",
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
                    control_file=control_file,
                    response_file=response_file,
                    status_command="status",
                ),
            )
            service = ProcessControlService()

            def write_response() -> None:
                deadline = time.time() + 1
                request_id: str | None = None
                while time.time() < deadline and request_id is None:
                    message = read_helper_control_message(control_file)
                    if message is not None:
                        request_id = message.request_id
                        write_helper_status_message(
                            response_file,
                            WindowsStrictHelperStatusMessage(
                                request_id=request_id,
                                run_id=message.run_id,
                                backend=message.backend,
                                state="running",
                                pid=4242,
                            ),
                        )
                        return
                    time.sleep(0.02)

            writer = threading.Thread(target=write_response, daemon=True)
            writer.start()
            response = service.request_status(handle, timeout_seconds=0.5, poll_interval_seconds=0.02)
            writer.join(timeout=1)

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual("running", response.state)
        self.assertEqual(4242, response.pid)
        self.assertEqual("proc-5678", response.run_id)

    def test_request_status_ignores_stale_response_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_file = root / "control" / "helper-control.json"
            response_file = root / "control" / "helper-status.json"
            response_file.parent.mkdir(parents=True, exist_ok=True)
            write_helper_status_message(
                response_file,
                WindowsStrictHelperStatusMessage(
                    request_id="status-stale",
                    run_id="proc-old",
                    backend="restricted-host-helper",
                    state="running",
                    pid=1111,
                ),
            )
            handle = RunnerProcessHandle(
                run_id="proc-live",
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
                    control_file=control_file,
                    response_file=response_file,
                    status_command="status",
                ),
            )
            service = ProcessControlService()

            def write_response() -> None:
                deadline = time.time() + 1
                while time.time() < deadline:
                    message = read_helper_control_message(control_file)
                    if message is not None and message.request_id != "status-stale":
                        write_helper_status_message(
                            response_file,
                            WindowsStrictHelperStatusMessage(
                                request_id=message.request_id,
                                run_id=message.run_id,
                                backend=message.backend,
                                state="running",
                                pid=4242,
                            ),
                        )
                        return
                    time.sleep(0.02)

            writer = threading.Thread(target=write_response, daemon=True)
            writer.start()
            response = service.request_status(handle, timeout_seconds=0.5, poll_interval_seconds=0.02)
            writer.join(timeout=1)

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual("proc-live", response.run_id)
        self.assertEqual(4242, response.pid)


if __name__ == "__main__":
    unittest.main()
