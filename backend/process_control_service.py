from __future__ import annotations

import time
import uuid
from pathlib import Path

from backend.runner_models import RunnerProcessHandle
from backend.windows.windows_strict_helper_protocol import (
    WindowsStrictHelperControlMessage,
    WindowsStrictHelperStatusMessage,
    read_helper_status_message,
    write_helper_control_message,
)


class ProcessControlService:
    def send_command(
        self,
        handle: RunnerProcessHandle,
        command: str | None,
        *,
        request_id: str | None = None,
    ) -> str | None:
        control = handle.control
        if control.kind != "file" or control.control_file is None or not command:
            return None
        resolved_request_id = request_id or f"ctl-{uuid.uuid4().hex[:8]}"
        write_helper_control_message(
            control.control_file,
            WindowsStrictHelperControlMessage(
                command=command,
                request_id=resolved_request_id,
                run_id=handle.run_id,
                backend=handle.backend,
            ),
        )
        return resolved_request_id

    def request_status(
        self,
        handle: RunnerProcessHandle,
        *,
        timeout_seconds: float = 0.5,
        poll_interval_seconds: float = 0.05,
    ) -> WindowsStrictHelperStatusMessage | None:
        control = handle.control
        if control.kind != "file" or control.response_file is None or not control.status_command:
            return None
        response_file: Path = control.response_file
        try:
            response_file.unlink()
        except FileNotFoundError:
            pass
        request_id = f"status-{uuid.uuid4().hex[:8]}"
        if self.send_command(handle, control.status_command, request_id=request_id) is None:
            return None
        deadline = time.monotonic() + timeout_seconds
        while True:
            response = read_helper_status_message(response_file)
            if response is not None and response.request_id == request_id:
                return response
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_interval_seconds)
