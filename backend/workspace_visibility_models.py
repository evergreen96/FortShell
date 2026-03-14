from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisibleWorkspaceState:
    signature: str
    entry_count: int
    policy_version: int

    def to_dict(self) -> dict[str, object]:
        return {
            "signature": self.signature,
            "entry_count": self.entry_count,
            "policy_version": self.policy_version,
        }
