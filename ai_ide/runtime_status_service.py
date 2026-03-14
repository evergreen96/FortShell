from __future__ import annotations

import json
from dataclasses import dataclass

from ai_ide.models import UsageMetrics


@dataclass(frozen=True)
class RuntimeStatusSnapshot:
    execution_session_id: str
    execution_status: str
    agent_session_id: str
    agent_kind: str
    agent_status: str
    runner_mode: str
    strict_boundary_scope: str
    policy_version: int
    deny_rule_count: int
    terminal_count: int
    event_count: int
    pending_review_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "execution_session_id": self.execution_session_id,
            "execution_status": self.execution_status,
            "agent_session_id": self.agent_session_id,
            "agent_kind": self.agent_kind,
            "agent_status": self.agent_status,
            "runner_mode": self.runner_mode,
            "strict_boundary_scope": self.strict_boundary_scope,
            "policy_version": self.policy_version,
            "deny_rule_count": self.deny_rule_count,
            "terminal_count": self.terminal_count,
            "event_count": self.event_count,
            "pending_review_count": self.pending_review_count,
        }


@dataclass(frozen=True)
class RuntimeMetricsSnapshot:
    list_count: int
    read_count: int
    write_count: int
    grep_count: int
    blocked_count: int
    terminal_runs: int
    audit_event_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "list_count": self.list_count,
            "read_count": self.read_count,
            "write_count": self.write_count,
            "grep_count": self.grep_count,
            "blocked_count": self.blocked_count,
            "terminal_runs": self.terminal_runs,
            "audit_event_count": self.audit_event_count,
        }


class RuntimeStatusService:
    @staticmethod
    def build_status_snapshot(
        *,
        execution_session_id: str,
        execution_status: str,
        agent_session_id: str,
        agent_kind: str,
        agent_status: str,
        runner_mode: str,
        strict_boundary_scope: str,
        policy_version: int,
        deny_rule_count: int,
        terminal_count: int,
        event_count: int,
        pending_review_count: int,
    ) -> RuntimeStatusSnapshot:
        return RuntimeStatusSnapshot(
            execution_session_id=execution_session_id,
            execution_status=execution_status,
            agent_session_id=agent_session_id,
            agent_kind=agent_kind,
            agent_status=agent_status,
            runner_mode=runner_mode,
            strict_boundary_scope=strict_boundary_scope,
            policy_version=policy_version,
            deny_rule_count=deny_rule_count,
            terminal_count=terminal_count,
            event_count=event_count,
            pending_review_count=pending_review_count,
        )

    @staticmethod
    def build_metrics_snapshot(metrics: UsageMetrics, audit_event_count: int) -> RuntimeMetricsSnapshot:
        return RuntimeMetricsSnapshot(
            list_count=metrics.list_count,
            read_count=metrics.read_count,
            write_count=metrics.write_count,
            grep_count=metrics.grep_count,
            blocked_count=metrics.blocked_count,
            terminal_runs=metrics.terminal_runs,
            audit_event_count=audit_event_count,
        )

    @staticmethod
    def status_text(snapshot: RuntimeStatusSnapshot) -> str:
        return (
            f"execution_session={snapshot.execution_session_id} "
            f"agent_session={snapshot.agent_session_id} "
            f"strict_boundary_scope={snapshot.strict_boundary_scope} "
            f"policy_version={snapshot.policy_version} "
            f"deny_rules={snapshot.deny_rule_count} terminals={snapshot.terminal_count} "
            f"events={snapshot.event_count}"
        )

    @staticmethod
    def session_text(snapshot: RuntimeStatusSnapshot) -> str:
        return (
            f"execution_session_id={snapshot.execution_session_id} "
            f"agent_session_id={snapshot.agent_session_id} "
            f"policy_version={snapshot.policy_version} "
            f"execution_status={snapshot.execution_status}"
        )

    @staticmethod
    def metrics_text(snapshot: RuntimeMetricsSnapshot) -> str:
        return (
            f"list={snapshot.list_count} read={snapshot.read_count} write={snapshot.write_count} "
            f"grep={snapshot.grep_count} blocked={snapshot.blocked_count} "
            f"terminal_runs={snapshot.terminal_runs} audit_events={snapshot.audit_event_count}"
        )

    @staticmethod
    def to_json(payload: dict[str, object]) -> str:
        return json.dumps(payload, sort_keys=True)
