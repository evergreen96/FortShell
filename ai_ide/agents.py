from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class AgentAdapterProbe:
    kind: str
    display_name: str
    available: bool
    status_code: str
    detail: str
    transport: str
    requires_tty: bool
    io_mode_preference: str
    launcher: str | None = None
    launcher_hint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "display_name": self.display_name,
            "available": self.available,
            "status_code": self.status_code,
            "detail": self.detail,
            "transport": self.transport,
            "requires_tty": self.requires_tty,
            "io_mode_preference": self.io_mode_preference,
            "launcher": self.launcher,
            "launcher_hint": self.launcher_hint,
        }


@dataclass(frozen=True)
class AgentLaunchPlan:
    kind: str
    display_name: str
    available: bool
    status_code: str
    detail: str
    transport: str
    requires_tty: bool
    io_mode_preference: str
    launcher: str | None
    argv: List[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "display_name": self.display_name,
            "available": self.available,
            "status_code": self.status_code,
            "detail": self.detail,
            "transport": self.transport,
            "requires_tty": self.requires_tty,
            "io_mode_preference": self.io_mode_preference,
            "launcher": self.launcher,
            "argv": list(self.argv),
        }


class AgentAdapter:
    def __init__(
        self,
        kind: str,
        display_name: str,
        launcher_candidates: Iterable[str],
        *,
        transport: str = "stdio-cli",
        requires_tty: bool = True,
        io_mode_preference: str = "pty_preferred",
        default_args: Iterable[str] | None = None,
    ) -> None:
        self.kind = kind
        self.display_name = display_name
        self.launcher_candidates = tuple(launcher_candidates)
        self.transport = transport
        self.requires_tty = requires_tty
        self.io_mode_preference = io_mode_preference
        self.default_args = tuple(default_args or ())

    def probe(self) -> AgentAdapterProbe:
        for candidate in self.launcher_candidates:
            launcher = shutil.which(candidate)
            if launcher:
                return AgentAdapterProbe(
                    kind=self.kind,
                    display_name=self.display_name,
                    available=True,
                    status_code="ready",
                    detail=f"{self.display_name} launcher is available",
                    transport=self.transport,
                    requires_tty=self.requires_tty,
                    io_mode_preference=self.io_mode_preference,
                    launcher=launcher,
                    launcher_hint=candidate,
                )
        launcher_hint = self.launcher_candidates[0] if self.launcher_candidates else None
        return AgentAdapterProbe(
            kind=self.kind,
            display_name=self.display_name,
            available=False,
            status_code="not_found",
            detail=f"{self.display_name} launcher was not found on PATH",
            transport=self.transport,
            requires_tty=self.requires_tty,
            io_mode_preference=self.io_mode_preference,
            launcher=None,
            launcher_hint=launcher_hint,
        )

    def launch_plan(self) -> AgentLaunchPlan:
        probe = self.probe()
        argv = [probe.launcher, *self.default_args] if probe.available and probe.launcher else []
        return AgentLaunchPlan(
            kind=self.kind,
            display_name=self.display_name,
            available=probe.available,
            status_code=probe.status_code,
            detail=probe.detail,
            transport=probe.transport,
            requires_tty=probe.requires_tty,
            io_mode_preference=probe.io_mode_preference,
            launcher=probe.launcher,
            argv=argv,
        )


class VirtualAgentAdapter(AgentAdapter):
    def __init__(self) -> None:
        super().__init__(
            kind="default",
            display_name="Default Agent Session",
            launcher_candidates=(),
            transport="session-placeholder",
            requires_tty=False,
            io_mode_preference="session-placeholder",
        )

    def probe(self) -> AgentAdapterProbe:
        return AgentAdapterProbe(
            kind=self.kind,
            display_name=self.display_name,
            available=False,
            status_code="virtual",
            detail="No concrete agent adapter selected for this session",
            transport=self.transport,
            requires_tty=self.requires_tty,
            io_mode_preference=self.io_mode_preference,
            launcher=None,
            launcher_hint=None,
        )


class AgentRegistry:
    def __init__(self, adapters: Iterable[AgentAdapter] | None = None) -> None:
        self._adapters = {
            adapter.kind: adapter for adapter in (adapters or build_default_agent_adapters())
        }

    def kinds(self) -> list[str]:
        return list(self._adapters)

    def has(self, kind: str) -> bool:
        return kind in self._adapters

    def get(self, kind: str) -> AgentAdapter:
        try:
            return self._adapters[kind]
        except KeyError as exc:
            known = ", ".join(self.kinds())
            raise ValueError(f"Unknown agent kind: {kind}. Known kinds: {known}") from exc

    def probe(self, kind: str) -> AgentAdapterProbe:
        return self.get(kind).probe()

    def probe_all(self) -> list[AgentAdapterProbe]:
        return [self._adapters[kind].probe() for kind in self.kinds()]

    def launch_plan(self, kind: str) -> AgentLaunchPlan:
        return self.get(kind).launch_plan()


def build_default_agent_adapters() -> list[AgentAdapter]:
    return [
        VirtualAgentAdapter(),
        AgentAdapter("claude", "Claude CLI", ("claude", "claude-code")),
        AgentAdapter("codex", "Codex CLI", ("codex",)),
        AgentAdapter("gemini", "Gemini CLI", ("gemini",)),
        AgentAdapter("opencode", "OpenCode CLI", ("opencode",)),
    ]
