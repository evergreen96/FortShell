from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ENCODED_CHAR_SEQUENCE_PATTERN = re.compile(r"(?<![\w-])(?:\d{1,6}\s*,\s*){7,}\d{1,6}(?![\w-])")
CHR_LITERAL_PATTERN = re.compile(r"chr\(\s*(\d{1,6})\s*\)", re.IGNORECASE)

STRICT_NETWORK_PATTERNS = (
    r"\bcurl\b",
    r"\bwget\b",
    r"\bscp\b",
    r"\bssh\b",
    r"\btelnet\b",
    r"\bnc\b",
    r"\bncat\b",
    r"\binvoke-webrequest\b",
    r"\biwr\b",
    r"\birm\b",
    r"https?://",
)

STRICT_NESTED_SHELL_PATTERNS = (
    r"\bpowershell(\.exe)?\b",
    r"\bpwsh\b",
    r"\bbash\b",
    r"\bsh\b\s+-c\b",
    r"\bzsh\b",
    r"\bfish\b",
    r"\bcmd(\.exe)?\b",
)

STRICT_PREVIEW_INTERPRETER_PATTERNS = (
    r"\bpython(\.exe)?\b",
    r"\bpython3(\.exe)?\b",
    r"\bpy(\.exe)?\b",
    r"\bnode(\.exe)?\b",
    r"\bdeno(\.exe)?\b",
    r"\bbun(\.exe)?\b",
    r"\bperl(\.exe)?\b",
    r"\bruby(\.exe)?\b",
    r"\bphp(\.exe)?\b",
    r"\blua(\.exe)?\b",
    r"\bwscript(\.exe)?\b",
    r"\bcscript(\.exe)?\b",
)


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


class CommandGuard:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        project_root_text = self._normalize_text(str(self.project_root))
        self.project_root_texts = {
            project_root_text,
        }

    def evaluate(self, mode: str, command: str) -> GuardDecision:
        normalized = self._normalize_text(command)

        if mode in {"projected", "strict", "strict-preview"}:
            if self._contains_host_project_path(normalized):
                return GuardDecision(False, "command references host project path directly")
            if self._contains_encoded_host_project_path(command):
                return GuardDecision(False, "command references host project path directly")

        if mode in {"strict", "strict-preview"}:
            for pattern in STRICT_NETWORK_PATTERNS:
                if re.search(pattern, normalized):
                    return GuardDecision(False, "strict mode blocks obvious network-capable commands")

            for pattern in STRICT_NESTED_SHELL_PATTERNS:
                if re.search(pattern, normalized):
                    return GuardDecision(False, "strict mode blocks nested shell launches")

        if mode == "strict-preview":
            for pattern in STRICT_PREVIEW_INTERPRETER_PATTERNS:
                if re.search(pattern, normalized):
                    return GuardDecision(False, "strict preview blocks interpreter launches")

        return GuardDecision(True)

    def _contains_host_project_path(self, text: str) -> bool:
        return any(root_text in text for root_text in self.project_root_texts)

    def _contains_encoded_host_project_path(self, command: str) -> bool:
        for candidate in self._decoded_path_candidates(command):
            if self._contains_host_project_path(candidate):
                return True
        return False

    def _decoded_path_candidates(self, command: str) -> list[str]:
        candidates = []

        for match in ENCODED_CHAR_SEQUENCE_PATTERN.finditer(command):
            decoded = self._decode_numeric_sequence(match.group(0))
            if decoded is not None:
                candidates.append(decoded)

        chr_codes = [int(match.group(1)) for match in CHR_LITERAL_PATTERN.finditer(command)]
        decoded_chr_path = self._decode_codes(chr_codes)
        if decoded_chr_path is not None:
            candidates.append(decoded_chr_path)

        return candidates

    def _decode_numeric_sequence(self, sequence_text: str) -> str | None:
        codes = [int(part.strip()) for part in sequence_text.split(",")]
        return self._decode_codes(codes)

    def _decode_codes(self, codes: list[int]) -> str | None:
        if len(codes) < 8:
            return None

        chars = []
        for code in codes:
            if code < 1 or code > 0x10FFFF:
                return None
            char = chr(code)
            if char in {"\0", "\r", "\n"}:
                return None
            chars.append(char)

        decoded = self._normalize_text("".join(chars))
        if not self._looks_like_path(decoded):
            return None
        return decoded

    def _looks_like_path(self, value: str) -> bool:
        if "/" not in value:
            return False
        if value.startswith("/"):
            return True
        if value.startswith("//"):
            return True
        return re.match(r"^[a-z]:/", value) is not None

    def _normalize_text(self, value: str) -> str:
        normalized = value.lower().replace("\\\\", "\\")
        return normalized.replace("\\", "/")
