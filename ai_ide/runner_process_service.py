from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from ai_ide.runner_models import (
    RunnerProcessControl,
    RunnerProcessHandle,
    RunnerProcessStopPolicy,
    RunnerResult,
)


class RunnerProcessService:
    def run_subprocess(
        self,
        command: str | list[str],
        working_directory: Path,
        *,
        mode: str,
        backend: str,
        env: dict[str, str] | None = None,
        reported_working_directory: str | None = None,
        shell: bool = True,
    ) -> RunnerResult:
        env = env or dict(os.environ)
        env["AI_IDE_RUNNER_MODE"] = mode

        proc = subprocess.run(
            command,
            cwd=working_directory,
            shell=shell,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        return RunnerResult(
            mode=mode,
            backend=backend,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            working_directory=reported_working_directory or str(working_directory),
        )

    def start_subprocess(
        self,
        command: str | list[str],
        working_directory: Path,
        *,
        mode: str,
        backend: str,
        env: dict[str, str] | None = None,
        reported_working_directory: str | None = None,
        shell: bool = True,
        artifact_root: Path | None = None,
        stop_policy: RunnerProcessStopPolicy | None = None,
        control: RunnerProcessControl | None = None,
    ) -> RunnerProcessHandle:
        env = env or dict(os.environ)
        env["AI_IDE_RUNNER_MODE"] = mode
        artifact_root = artifact_root or (working_directory / ".ai_ide_processes")
        stop_policy = stop_policy or RunnerProcessStopPolicy()
        control = control or RunnerProcessControl()
        artifact_root.mkdir(parents=True, exist_ok=True)
        run_id = f"proc-{uuid.uuid4().hex[:8]}"
        stdout_path = artifact_root / f"{run_id}.stdout.log"
        stderr_path = artifact_root / f"{run_id}.stderr.log"
        stdout_file = stdout_path.open("w", encoding="utf-8")
        stderr_file = stderr_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=working_directory,
                shell=shell,
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except Exception:
            stdout_file.close()
            stderr_file.close()
            raise
        return RunnerProcessHandle(
            run_id=run_id,
            mode=mode,
            backend=backend,
            working_directory=reported_working_directory or str(working_directory),
            process=process,
            stdin_file=process.stdin,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            stop_policy=stop_policy,
            control=control,
        )
