from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from ai_ide.process_stop_service import ProcessStopService
from ai_ide.runner_models import RunnerProcessControl, RunnerProcessHandle, RunnerProcessStopPolicy
from ai_ide.windows_strict_helper_protocol import read_helper_control_message


class _CloseAwarePipe(io.StringIO):
    def __init__(self, on_close) -> None:
        super().__init__()
        self._on_close = on_close

    def close(self) -> None:
        if not self.closed:
            self._on_close()
        super().close()


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self._force_timeout_after_terminate = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None or self._force_timeout_after_terminate:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if not self._force_timeout_after_terminate:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self._force_timeout_after_terminate = False
        self.returncode = -9


class _ControlAwareProcess(_FakeProcess):
    def __init__(self, control_file: Path) -> None:
        super().__init__()
        self.control_file = control_file

    def wait(self, timeout=None):
        if self.returncode is None:
            message = read_helper_control_message(self.control_file)
            if message is not None and message.command == "stop":
                self.returncode = 0
        return super().wait(timeout=timeout)


class _KillAwareProcess(_FakeProcess):
    def __init__(self, control_file: Path) -> None:
        super().__init__()
        self.control_file = control_file

    def wait(self, timeout=None):
        if self.returncode is None:
            message = read_helper_control_message(self.control_file)
            if message is not None and message.command == "kill":
                self.returncode = -9
        return super().wait(timeout=timeout)


class ProcessStopServiceTests(unittest.TestCase):
    def test_stop_writes_file_control_command_before_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_file = root / "control" / "stop.txt"
            process = _ControlAwareProcess(control_file)
            service = ProcessStopService()
            handle = RunnerProcessHandle(
                run_id="proc-0",
                mode="strict",
                backend="restricted-host-helper",
                working_directory=str(root),
                process=process,  # type: ignore[arg-type]
                stdin_file=io.StringIO(),
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                stdout_file=io.StringIO(),
                stderr_file=io.StringIO(),
                stop_policy=RunnerProcessStopPolicy(
                    close_stdin_first=True,
                    stdin_close_grace_seconds=0.01,
                    terminate_timeout_seconds=0.01,
                ),
                control=RunnerProcessControl(kind="file", control_file=control_file, stop_command="stop"),
            )

            returncode = service.stop(handle)
            self.assertEqual(0, returncode)
            self.assertTrue(control_file.exists())
            message = read_helper_control_message(control_file)
            self.assertIsNotNone(message)
            assert message is not None
            self.assertEqual("stop", message.command)
            self.assertEqual("proc-0", message.run_id)
            self.assertEqual("restricted-host-helper", message.backend)
            self.assertFalse(process.terminated)

    def test_stop_closes_stdin_and_allows_graceful_exit_before_terminate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = _FakeProcess()
            stdin_file = _CloseAwarePipe(lambda: setattr(process, "returncode", 0))
            service = ProcessStopService()
            handle = RunnerProcessHandle(
                run_id="proc-1",
                mode="strict",
                backend="restricted-host-helper",
                working_directory=str(root),
                process=process,  # type: ignore[arg-type]
                stdin_file=stdin_file,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                stdout_file=io.StringIO(),
                stderr_file=io.StringIO(),
                stop_policy=RunnerProcessStopPolicy(
                    close_stdin_first=True,
                    stdin_close_grace_seconds=0.01,
                    terminate_timeout_seconds=0.01,
                ),
            )

            returncode = service.stop(handle)

        self.assertEqual(0, returncode)
        self.assertTrue(stdin_file.closed)
        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)

    def test_stop_terminates_without_stdin_close_when_policy_disables_graceful_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = _FakeProcess()
            close_called = False

            def mark_closed() -> None:
                nonlocal close_called
                close_called = True

            stdin_file = _CloseAwarePipe(mark_closed)
            service = ProcessStopService()
            handle = RunnerProcessHandle(
                run_id="proc-2",
                mode="projected",
                backend="projected",
                working_directory=str(root),
                process=process,  # type: ignore[arg-type]
                stdin_file=stdin_file,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                stdout_file=io.StringIO(),
                stderr_file=io.StringIO(),
                stop_policy=RunnerProcessStopPolicy(
                    close_stdin_first=False,
                    stdin_close_grace_seconds=0.01,
                    terminate_timeout_seconds=0.01,
                ),
            )

            returncode = service.stop(handle)

        self.assertEqual(-15, returncode)
        self.assertFalse(close_called)
        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)

    def test_stop_kills_when_terminate_does_not_finish_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process = _FakeProcess()
            process._force_timeout_after_terminate = True
            stdin_file = io.StringIO()
            service = ProcessStopService()
            handle = RunnerProcessHandle(
                run_id="proc-3",
                mode="strict",
                backend="restricted-host-helper",
                working_directory=str(root),
                process=process,  # type: ignore[arg-type]
                stdin_file=stdin_file,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                stdout_file=io.StringIO(),
                stderr_file=io.StringIO(),
                stop_policy=RunnerProcessStopPolicy(
                    close_stdin_first=True,
                    stdin_close_grace_seconds=0.01,
                    terminate_timeout_seconds=0.01,
                ),
            )

            returncode = service.stop(handle)

        self.assertEqual(-9, returncode)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_stop_uses_control_kill_before_parent_terminate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_file = root / "control" / "stop.json"
            process = _KillAwareProcess(control_file)
            service = ProcessStopService()
            handle = RunnerProcessHandle(
                run_id="proc-4",
                mode="strict",
                backend="restricted-host-helper",
                working_directory=str(root),
                process=process,  # type: ignore[arg-type]
                stdin_file=io.StringIO(),
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                stdout_file=io.StringIO(),
                stderr_file=io.StringIO(),
                stop_policy=RunnerProcessStopPolicy(
                    close_stdin_first=True,
                    stdin_close_grace_seconds=0.01,
                    terminate_timeout_seconds=0.01,
                ),
                control=RunnerProcessControl(
                    kind="file",
                    control_file=control_file,
                    stop_command="stop",
                    kill_command="kill",
                ),
            )

            returncode = service.stop(handle)
            self.assertEqual(-9, returncode)
            message = read_helper_control_message(control_file)
            self.assertIsNotNone(message)
            assert message is not None
            self.assertEqual("kill", message.command)
            self.assertFalse(process.terminated)


if __name__ == "__main__":
    unittest.main()
