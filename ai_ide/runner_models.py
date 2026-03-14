from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass
class RunnerResult:
    mode: str
    backend: str
    returncode: int
    stdout: str
    stderr: str
    working_directory: str


@dataclass(frozen=True)
class RunnerProcessStopPolicy:
    close_stdin_first: bool = False
    stdin_close_grace_seconds: float = 0.0
    terminate_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class RunnerProcessControl:
    kind: str = "none"
    control_file: Path | None = None
    response_file: Path | None = None
    stop_command: str = "stop"
    kill_command: str | None = None
    status_command: str | None = None


@dataclass
class RunnerProcessHandle:
    run_id: str
    mode: str
    backend: str
    working_directory: str
    process: subprocess.Popen[str]
    stdin_file: TextIO | None
    stdout_path: Path
    stderr_path: Path
    stdout_file: TextIO
    stderr_file: TextIO
    stop_policy: RunnerProcessStopPolicy = RunnerProcessStopPolicy()
    control: RunnerProcessControl = RunnerProcessControl()


@dataclass
class RunnerLaunchResult:
    started: bool
    handle: RunnerProcessHandle | None = None
    result: RunnerResult | None = None
