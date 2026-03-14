from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import List

from ai_ide.models import AgentSession, ExecutionSession
from ai_ide.policy import PolicyEngine

logger = logging.getLogger(__name__)
MAX_EXECUTION_SESSION_HISTORY = 500
MAX_AGENT_SESSION_HISTORY = 500


@dataclass(frozen=True)
class SessionSyncResult:
    execution_changed: bool
    agent_changed: bool
    previous_execution_session_id: str
    previous_agent_session_id: str


class SessionManager:
    def __init__(self, policy_engine: PolicyEngine) -> None:
        self.policy_engine = policy_engine
        self._lock = threading.RLock()
        self._set_execution_sessions([])
        self._set_agent_sessions([])
        self._set_current_execution_session(self._create_execution_session())
        self._set_current_agent_session(self._create_agent_session(self.current_execution_session.session_id))

    @staticmethod
    def _new_session_id() -> str:
        return f"sess-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _new_agent_session_id() -> str:
        return f"agent-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _now() -> str:
        return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    @property
    def current_session_id(self) -> str:
        with self._lock:
            return self.current_execution_session.session_id

    @property
    def current_agent_session_id(self) -> str:
        with self._lock:
            return self.current_agent_session.agent_session_id

    @property
    def policy_version(self) -> int:
        with self._lock:
            return self.current_execution_session.policy_version

    def is_current_execution_session(self, session_id: str) -> bool:
        with self._lock:
            return self.current_execution_session.session_id == session_id

    def ensure_fresh_execution_session(self, *, force: bool = False) -> bool:
        with self._lock:
            if not force and self.current_execution_session.policy_version == self.policy_engine.state.version:
                return False
            previous_execution = self.current_execution_session
            previous_agent = self.current_agent_session
            previous_execution.mark_stale()
            previous_agent.mark_stale()
            self._set_current_execution_session(self._create_execution_session(rotated_from=previous_execution.session_id))
            self._set_current_agent_session(
                self._create_agent_session(
                    self.current_execution_session.session_id,
                    agent_kind=previous_agent.agent_kind,
                    rotated_from=previous_agent.agent_session_id,
                )
            )
            logger.info(
                "session.rotation execution_from=%s execution_to=%s policy_version=%d",
                previous_execution.session_id,
                self.current_execution_session.session_id,
                self.policy_engine.state.version,
            )
            return True

    def rotate_agent_session(self, agent_kind: str | None = None) -> AgentSession:
        with self._lock:
            previous_agent = self.current_agent_session
            previous_agent.mark_stale()
            self._set_current_agent_session(
                self._create_agent_session(
                    self.current_execution_session.session_id,
                    agent_kind=agent_kind or previous_agent.agent_kind,
                    rotated_from=previous_agent.agent_session_id,
                )
            )
            logger.info(
                "session.rotation agent_from=%s agent_to=%s execution=%s agent_kind=%s",
                previous_agent.agent_session_id,
                self.current_agent_session.agent_session_id,
                self.current_execution_session.session_id,
                self.current_agent_session.agent_kind,
            )
            return self.current_agent_session

    def sync_remote_sessions(
        self,
        execution_session: ExecutionSession,
        agent_session: AgentSession,
    ) -> SessionSyncResult:
        if agent_session.execution_session_id != execution_session.session_id:
            raise ValueError("Agent session must reference the supplied execution session")

        with self._lock:
            previous_execution_session_id = self.current_execution_session.session_id
            previous_agent_session_id = self.current_agent_session.agent_session_id
            execution_changed = execution_session.session_id != previous_execution_session_id
            agent_changed = agent_session.agent_session_id != previous_agent_session_id

            if execution_changed:
                self.current_execution_session.mark_stale()
            if agent_changed:
                self.current_agent_session.mark_stale()

            self._set_current_execution_session(self._upsert_execution_session(execution_session))
            self._set_current_agent_session(self._upsert_agent_session(agent_session))

            if execution_changed or agent_changed:
                logger.info(
                    "session.sync execution=%s agent=%s execution_changed=%s agent_changed=%s",
                    execution_session.session_id,
                    agent_session.agent_session_id,
                    execution_changed,
                    agent_changed,
                )

            return SessionSyncResult(
                execution_changed=execution_changed,
                agent_changed=agent_changed,
                previous_execution_session_id=previous_execution_session_id,
                previous_agent_session_id=previous_agent_session_id,
            )

    def list_agent_sessions(self, execution_session_id: str | None = None) -> List[AgentSession]:
        with self._lock:
            if execution_session_id is None:
                return list(self.agent_sessions)
            return [session for session in self.agent_sessions if session.execution_session_id == execution_session_id]

    def _upsert_execution_session(self, session: ExecutionSession) -> ExecutionSession:
        for index, existing in enumerate(self.execution_sessions):
            if existing.session_id == session.session_id:
                self._replace_execution_session(index, session)
                return session
        self._append_execution_session(session)
        return session

    def _upsert_agent_session(self, session: AgentSession) -> AgentSession:
        for index, existing in enumerate(self.agent_sessions):
            if existing.agent_session_id == session.agent_session_id:
                self._replace_agent_session(index, session)
                return session
        self._append_agent_session(session)
        return session

    def _create_execution_session(self, rotated_from: str | None = None) -> ExecutionSession:
        session = ExecutionSession(
            session_id=self._new_session_id(),
            policy_version=self.policy_engine.state.version,
            created_at=self._now(),
            status="active",
            rotated_from=rotated_from,
        )
        self._append_execution_session(session)
        return session

    def _create_agent_session(
        self,
        execution_session_id: str,
        agent_kind: str = "default",
        rotated_from: str | None = None,
    ) -> AgentSession:
        session = AgentSession(
            agent_session_id=self._new_agent_session_id(),
            execution_session_id=execution_session_id,
            agent_kind=agent_kind,
            created_at=self._now(),
            status="active",
            rotated_from=rotated_from,
        )
        self._append_agent_session(session)
        return session

    def _set_current_execution_session(self, session: ExecutionSession) -> None:
        self.current_execution_session = session

    def _set_current_agent_session(self, session: AgentSession) -> None:
        self.current_agent_session = session

    def _append_execution_session(self, session: ExecutionSession) -> None:
        self._set_execution_sessions([*self.execution_sessions, session])
        self._trim_execution_sessions()

    def _append_agent_session(self, session: AgentSession) -> None:
        self._set_agent_sessions([*self.agent_sessions, session])
        self._trim_agent_sessions()

    def _replace_execution_session(self, index: int, session: ExecutionSession) -> None:
        sessions = list(self.execution_sessions)
        sessions[index] = session
        self._set_execution_sessions(sessions)

    def _replace_agent_session(self, index: int, session: AgentSession) -> None:
        sessions = list(self.agent_sessions)
        sessions[index] = session
        self._set_agent_sessions(sessions)

    def _trim_execution_sessions(self) -> None:
        if len(self.execution_sessions) > MAX_EXECUTION_SESSION_HISTORY:
            removed = len(self.execution_sessions) - MAX_EXECUTION_SESSION_HISTORY
            self._set_execution_sessions(self.execution_sessions[-MAX_EXECUTION_SESSION_HISTORY:])
            logger.info(
                "session.execution.trim removed=%s limit=%s",
                removed,
                MAX_EXECUTION_SESSION_HISTORY,
            )

    def _trim_agent_sessions(self) -> None:
        if len(self.agent_sessions) > MAX_AGENT_SESSION_HISTORY:
            removed = len(self.agent_sessions) - MAX_AGENT_SESSION_HISTORY
            self._set_agent_sessions(self.agent_sessions[-MAX_AGENT_SESSION_HISTORY:])
            logger.info(
                "session.agent.trim removed=%s limit=%s",
                removed,
                MAX_AGENT_SESSION_HISTORY,
            )

    def _set_execution_sessions(self, sessions: List[ExecutionSession]) -> None:
        self.execution_sessions = sessions

    def _set_agent_sessions(self, sessions: List[AgentSession]) -> None:
        self.agent_sessions = sessions
