from __future__ import annotations

import os
import shlex
import subprocess


class RunnerEnvironmentService:
    def build_strict_environment(self) -> dict[str, str]:
        allowed_names = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "COMSPEC",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "WINDIR",
        }
        blocked_name_parts = ("TOKEN", "SECRET", "API_KEY", "PASSWORD")
        blocked_prefixes = (
            "OPENAI_",
            "ANTHROPIC_",
            "GEMINI_",
            "GOOGLE_",
            "AWS_",
            "AZURE_",
            "GITHUB_",
        )

        env = {}
        for name, value in os.environ.items():
            upper_name = name.upper()
            if upper_name in allowed_names:
                env[name] = value
                continue
            if any(upper_name.startswith(prefix) for prefix in blocked_prefixes):
                continue
            if any(part in upper_name for part in blocked_name_parts):
                continue
        env["AI_IDE_STRICT_PREVIEW"] = "1"
        return env

    @staticmethod
    def argv_to_command(argv: list[str]) -> str:
        if os.name == "nt":
            return subprocess.list2cmdline(argv)
        return shlex.join(argv)

    @staticmethod
    def merge_environment(base: dict[str, str], overlay: dict[str, str] | None) -> dict[str, str]:
        if not overlay:
            return base
        merged = dict(base)
        merged.update(overlay)
        return merged
