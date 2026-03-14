from __future__ import annotations

import subprocess

from ai_ide.process_control_service import ProcessControlService
from ai_ide.runner_models import RunnerProcessHandle


class ProcessStopService:
    def __init__(self, process_control_service: ProcessControlService | None = None) -> None:
        self.process_control_service = process_control_service or ProcessControlService()

    def stop(self, handle: RunnerProcessHandle) -> int:
        process = handle.process
        policy = handle.stop_policy
        if process.poll() is not None:
            return process.returncode if process.returncode is not None else -15

        if self.process_control_service.send_command(handle, handle.control.stop_command) and self._wait(
            process, policy.stdin_close_grace_seconds
        ):
            return process.returncode if process.returncode is not None else 0

        if policy.close_stdin_first:
            self._close_stdin(handle.stdin_file)
        if policy.close_stdin_first and self._wait(process, policy.stdin_close_grace_seconds):
            return process.returncode if process.returncode is not None else 0

        if self.process_control_service.send_command(handle, handle.control.kill_command) and self._wait(
            process, policy.terminate_timeout_seconds
        ):
            return process.returncode if process.returncode is not None else -9

        process.terminate()
        if self._wait(process, policy.terminate_timeout_seconds):
            return process.returncode if process.returncode is not None else -15

        process.kill()
        process.wait(timeout=policy.terminate_timeout_seconds)
        return process.returncode if process.returncode is not None else -9

    @staticmethod
    def _close_stdin(stdin_file) -> None:
        if stdin_file is None or stdin_file.closed:
            return
        stdin_file.close()

    @staticmethod
    def _wait(process, timeout: float) -> bool:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True
