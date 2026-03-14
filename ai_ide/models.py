from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, List, Optional

from ai_ide.terminal_inbox import TerminalInboxEntry

MAX_TERMINAL_COMMAND_HISTORY = 500
MAX_TERMINAL_INBOX_ENTRIES = 500
MAX_AGENT_RUN_STREAM_BYTES = 1_048_576


@dataclass
class AuditEvent:
    timestamp: str
    session_id: str
    action: str
    target: str
    allowed: bool
    detail: str = ""


@dataclass
class UsageMetrics:
    list_count: int = 0
    read_count: int = 0
    write_count: int = 0
    grep_count: int = 0
    blocked_count: int = 0
    terminal_runs: int = 0

    def increment_list(self) -> None:
        self.list_count += 1

    def increment_read(self) -> None:
        self.read_count += 1

    def increment_write(self) -> None:
        self.write_count += 1

    def increment_grep(self) -> None:
        self.grep_count += 1

    def increment_blocked(self) -> None:
        self.blocked_count += 1

    def increment_terminal_runs(self) -> None:
        self.terminal_runs += 1


@dataclass
class PolicyState:
    deny_globs: List[str]
    version: int = 1

    def replace_rules(self, deny_globs: List[str], version: int) -> None:
        self.deny_globs = list(deny_globs)
        self.version = max(1, int(version))

    def append_deny_glob(self, rule: str) -> bool:
        if rule in self.deny_globs:
            return False
        self.deny_globs.append(rule)
        self.version += 1
        return True

    def remove_deny_glob(self, rule: str) -> bool:
        if rule not in self.deny_globs:
            return False
        self.deny_globs.remove(rule)
        self.version += 1
        return True


@dataclass
class WriteProposal:
    proposal_id: str
    target: str
    session_id: str
    agent_session_id: str
    created_at: str
    updated_at: str
    status: str
    base_sha256: str | None
    base_text: str | None
    proposed_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "target": self.target,
            "session_id": self.session_id,
            "agent_session_id": self.agent_session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "base_sha256": self.base_sha256,
            "base_text": self.base_text,
            "proposed_text": self.proposed_text,
        }


@dataclass
class ExecutionSession:
    session_id: str
    policy_version: int
    created_at: str
    status: str
    rotated_from: Optional[str] = None

    def mark_stale(self) -> None:
        self.status = "stale"


@dataclass
class AgentSession:
    agent_session_id: str
    execution_session_id: str
    agent_kind: str
    created_at: str
    status: str
    rotated_from: Optional[str] = None

    def mark_stale(self) -> None:
        self.status = "stale"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_session_id": self.agent_session_id,
            "execution_session_id": self.execution_session_id,
            "agent_kind": self.agent_kind,
            "created_at": self.created_at,
            "status": self.status,
            "rotated_from": self.rotated_from,
        }


@dataclass
class AgentRunRecord:
    run_id: str
    agent_session_id: str
    execution_session_id: str
    agent_kind: str
    runner_mode: str
    backend: str
    io_mode: str
    transport_status: str
    argv: List[str]
    created_at: str
    ended_at: Optional[str]
    pid: Optional[int]
    returncode: int
    status: str
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent_session_id": self.agent_session_id,
            "execution_session_id": self.execution_session_id,
            "agent_kind": self.agent_kind,
            "runner_mode": self.runner_mode,
            "backend": self.backend,
            "io_mode": self.io_mode,
            "transport_status": self.transport_status,
            "argv": list(self.argv),
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "pid": self.pid,
            "returncode": self.returncode,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    def finish(self, *, status: str, returncode: int, ended_at: str) -> None:
        self.status = status
        self.returncode = returncode
        self.ended_at = ended_at

    def append_stdout(self, chunk: str) -> None:
        self.stdout = self._trim_stream(f"{self.stdout}{chunk}")

    def append_stderr(self, chunk: str) -> None:
        self.stderr = self._trim_stream(f"{self.stderr}{chunk}")

    def append_stderr_line(self, line: str) -> None:
        suffix = f"\n{line}" if self.stderr else line
        self.stderr = self._trim_stream(f"{self.stderr}{suffix}".strip())

    def mark_stopped(self, *, returncode: int, ended_at: str, reason: str | None = None) -> None:
        self.finish(status="stopped", returncode=returncode, ended_at=ended_at)
        if reason:
            self.append_stderr_line(f"stopped: {reason}")

    def mark_interrupted(self, *, ended_at: str, reason: str) -> None:
        self.finish(status="interrupted", returncode=-2, ended_at=ended_at)
        self.append_stderr_line(reason)

    @staticmethod
    def _trim_stream(text: str) -> str:
        if len(text) <= MAX_AGENT_RUN_STREAM_BYTES:
            return text
        return text[-MAX_AGENT_RUN_STREAM_BYTES:]


@dataclass
class AgentRunWatch:
    watch_id: str
    run_id: str
    consumer_id: str
    created_at: str
    name: str
    updated_at: Optional[str] = None

    def touch(self, timestamp: str) -> None:
        self.updated_at = timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "watch_id": self.watch_id,
            "run_id": self.run_id,
            "consumer_id": self.consumer_id,
            "created_at": self.created_at,
            "name": self.name,
            "updated_at": self.updated_at,
        }


@dataclass
class TerminalSession:
    terminal_id: str
    name: str
    created_at: str
    transport: str
    runner_mode: Optional[str]
    status: str
    stale_reason: Optional[str]
    execution_session_id: Optional[str]
    bound_agent_run_id: Optional[str]
    command_history: List[str]
    inbox: List[TerminalInboxEntry]
    io_mode: str = "command"
    _state_lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _command_history_lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _inbox_lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)

    def append_command_history(self, command: str) -> None:
        with self._command_history_lock:
            self.command_history.append(command)
            if len(self.command_history) > MAX_TERMINAL_COMMAND_HISTORY:
                del self.command_history[: len(self.command_history) - MAX_TERMINAL_COMMAND_HISTORY]

    def snapshot_command_history(self) -> list[str]:
        with self._command_history_lock:
            return list(self.command_history)

    def append_inbox(self, entry: TerminalInboxEntry) -> None:
        """Single entry point for inbox mutation."""
        with self._inbox_lock:
            self.inbox.append(entry)
            if len(self.inbox) > MAX_TERMINAL_INBOX_ENTRIES:
                del self.inbox[: len(self.inbox) - MAX_TERMINAL_INBOX_ENTRIES]

    def snapshot_inbox(self) -> list[TerminalInboxEntry]:
        with self._inbox_lock:
            return list(self.inbox)

    def mark_stale(self, reason: str) -> None:
        with self._state_lock:
            self.status = "stale"
            self.stale_reason = reason

    def bind_agent_run(self, run_id: str) -> None:
        with self._state_lock:
            self.bound_agent_run_id = run_id

    def unbind_agent_run(self) -> None:
        with self._state_lock:
            self.bound_agent_run_id = None


@dataclass
class TerminalEventWatch:
    watch_id: str
    consumer_id: str
    kind_prefix: Optional[str]
    source_type: Optional[str]
    source_id: Optional[str]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def touch(self, timestamp: str) -> None:
        self.updated_at = timestamp
