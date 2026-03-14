from __future__ import annotations

from pathlib import Path


class RunnerHostService:
    def __init__(self, project_root: Path, *, run_subprocess, start_subprocess) -> None:
        self.project_root = project_root.resolve()
        self._run_subprocess = run_subprocess
        self._start_subprocess = start_subprocess

    def run(self, command: str):
        return self._run_subprocess(command, self.project_root, mode="host", backend="host")

    def run_process(self, argv: list[str], env: dict[str, str] | None = None):
        return self._run_subprocess(
            argv,
            self.project_root,
            mode="host",
            backend="host",
            env=env,
            shell=False,
        )

    def start_process(self, argv: list[str], env: dict[str, str] | None = None):
        handle = self._start_subprocess(
            argv,
            self.project_root,
            mode="host",
            backend="host",
            env=env,
            shell=False,
            artifact_root=self.project_root / ".ai_ide_processes",
        )
        return self._started_launch(handle)

    @staticmethod
    def _started_launch(handle):
        from ai_ide.runner_models import RunnerLaunchResult

        return RunnerLaunchResult(started=True, handle=handle)
