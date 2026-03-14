from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from core.models import PolicyState


WILDCARD_CHARS = {"*", "?", "["}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    matched_rule: str | None = None


def normalize_rule(rule: str) -> str:
    return rule.strip().replace("\\", "/")


class PolicyEngine:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.Lock()
        self._set_state(PolicyState(deny_globs=[]))

    def replace_state(self, state: PolicyState) -> None:
        with self._lock:
            self.state.replace_rules(
                [normalize_rule(rule) for rule in state.deny_globs if normalize_rule(rule)],
                state.version,
            )
            logger.info(
                "policy.replace version=%s deny_rules=%s",
                self.state.version,
                len(self.state.deny_globs),
            )

    def add_deny_rule(self, rule: str) -> bool:
        normalized = normalize_rule(rule)
        if not normalized:
            return False
        with self._lock:
            changed = self.state.append_deny_glob(normalized)
            if changed:
                logger.info("policy.add rule=%s version=%s", normalized, self.state.version)
            return changed

    def remove_deny_rule(self, rule: str) -> bool:
        normalized = normalize_rule(rule)
        if not normalized:
            return False
        with self._lock:
            changed = self.state.remove_deny_glob(normalized)
            if changed:
                logger.info("policy.remove rule=%s version=%s", normalized, self.state.version)
            return changed

    def _set_state(self, state: PolicyState) -> None:
        self.state = state

    def is_allowed(self, path: Path) -> bool:
        return self.evaluate(path).allowed

    def evaluate(self, path: Path) -> PolicyDecision:
        relative_path = self._relative_path(path)
        if relative_path is None:
            return PolicyDecision(allowed=True, matched_rule=None)
        with self._lock:
            matched_rule = next(
                (rule for rule in self.state.deny_globs if self._matches_rule(relative_path, rule)),
                None,
            )
        return PolicyDecision(
            allowed=matched_rule is None,
            matched_rule=matched_rule,
        )

    def _relative_path(self, path: Path) -> str | None:
        resolved = path.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError:
            return None
        text = relative.as_posix()
        return text or "."

    def _matches_rule(self, relative_path: str, rule: str) -> bool:
        if relative_path == ".":
            return False

        if rule.endswith("/**"):
            return self._matches_prefix(relative_path, rule[:-3].rstrip("/"))

        if rule.endswith("/"):
            return self._matches_prefix(relative_path, rule.rstrip("/"))

        if not any(char in rule for char in WILDCARD_CHARS):
            return self._matches_prefix(relative_path, rule.rstrip("/"))

        return fnmatch.fnmatch(relative_path, rule)

    @staticmethod
    def _matches_prefix(relative_path: str, prefix: str) -> bool:
        if not prefix:
            return False
        return relative_path == prefix or relative_path.startswith(prefix + "/")
