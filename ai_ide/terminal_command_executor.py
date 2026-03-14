from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from ai_ide.models import TerminalSession, UsageMetrics

logger = logging.getLogger(__name__)


class TerminalCommandExecutor:
    def __init__(
        self,
        project_root: Path,
        metrics: UsageMetrics,
        runner_manager,
        *,
        persist_state,
        publish_event,
    ) -> None:
        self.project_root = project_root
        self.metrics = metrics
        self.runner_manager = runner_manager
        self._persist_state = persist_state
        self._publish_event = publish_event

    def execute(self, session: TerminalSession, command: str) -> str:
        session.append_command_history(command)
        self.metrics.increment_terminal_runs()

        if session.status != "active":
            return self._execute_blocked(session, command)
        if session.transport == "runner":
            return self._execute_runner(session, command)
        return self._execute_host(session, command)

    def _execute_blocked(self, session: TerminalSession, command: str) -> str:
        self.metrics.increment_blocked()
        mode_label = session.runner_mode or "host"
        exec_label = session.execution_session_id or "(host)"
        detail = session.stale_reason or "terminal is not active"
        self._publish_event(
            "terminal.command.blocked",
            session,
            {"command": command, "reason": detail},
        )
        self._persist_state()
        logger.warning(
            "terminal_command.blocked terminal_id=%s transport=%s mode=%s reason=%s",
            session.terminal_id,
            session.transport,
            mode_label,
            detail,
        )
        return (
            f"[transport={session.transport} mode={mode_label} status={session.status} "
            f"exec={exec_label} blocked=true]\nblocked: {detail}"
        )

    def _execute_runner(self, session: TerminalSession, command: str) -> str:
        if self.runner_manager is None or session.runner_mode is None:
            raise RuntimeError("Runner terminal requires a runner manager and runner mode")
        result = self.runner_manager.run_in_mode(
            session.runner_mode,
            command,
            execution_session_id=session.execution_session_id,
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip() or "(no output)"
        self._publish_event(
            "terminal.command.completed",
            session,
            {
                "command": command,
                "backend": result.backend,
                "returncode": result.returncode,
            },
        )
        self._persist_state()
        logger.info(
            "terminal_command.completed terminal_id=%s transport=runner mode=%s backend=%s returncode=%s",
            session.terminal_id,
            result.mode,
            result.backend,
            result.returncode,
        )
        return (
            f"[transport=runner mode={result.mode} backend={result.backend} "
            f"code={result.returncode} exec={session.execution_session_id}]\n{output}"
        )

    def _execute_host(self, session: TerminalSession, command: str) -> str:
        shell_argv = self._host_shell_argv(command)
        proc = subprocess.run(
            shell_argv,
            cwd=self.project_root,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip() or "(no output)"
        self._publish_event(
            "terminal.command.completed",
            session,
            {"command": command, "backend": "host", "returncode": proc.returncode},
        )
        self._persist_state()
        logger.info(
            "terminal_command.completed terminal_id=%s transport=host backend=host returncode=%s",
            session.terminal_id,
            proc.returncode,
        )
        return f"[transport=host mode=host backend=host code={proc.returncode} unsafe=true]\n{output}"

    @staticmethod
    def _host_shell_argv(command: str) -> list[str]:
        comspec = os.environ.get("ComSpec") or "cmd.exe"
        # Use an explicit shell executable rather than shell=True so the host path
        # is stable and not delegated through Python's shell resolution.
        return [comspec, "/d", "/s", "/c", command]
