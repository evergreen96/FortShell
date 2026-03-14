from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandContext:
    trusted: bool
    source: str

    @staticmethod
    def user() -> "CommandContext":
        return CommandContext(trusted=True, source="user")

    @staticmethod
    def agent() -> "CommandContext":
        return CommandContext(trusted=False, source="agent")


class CommandAccessService:
    @staticmethod
    def require_trusted(context: CommandContext, capability: str) -> None:
        if context.trusted:
            return
        raise PermissionError(
            f"Blocked {capability}: trusted control-plane command required for source={context.source}"
        )
