from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


VALID_TRANSPORTS = {"runner", "host"}
VALID_IO_MODES = {"command", "pty"}
VALID_RUNNER_MODES = {"projected", "strict"}
VALID_CWD_MODES = {"project", "runner_mount"}


@dataclass(frozen=True)
class TerminalProfile:
    profile_id: str
    label: str
    description: str
    transport: str
    io_mode: str
    runner_mode: str | None = None
    spawn_argv: list[str] = field(default_factory=list)
    command_argv_prefix: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd_mode: str = "project"
    source: str = "builtin"
    default: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "description": self.description,
            "transport": self.transport,
            "io_mode": self.io_mode,
            "runner_mode": self.runner_mode,
            "cwd_mode": self.cwd_mode,
            "source": self.source,
            "default": self.default,
        }


class TerminalProfileCatalog:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.config_path = runtime_root / "desktop" / "terminal_profiles.json"
        profiles = self._builtin_profiles()
        for override in self._load_user_profiles():
            profiles[override.profile_id] = override
        self._profiles = profiles

    def list_profiles(self) -> list[TerminalProfile]:
        return list(self._profiles.values())

    def get(self, profile_id: str) -> TerminalProfile:
        if profile_id not in self._profiles:
            raise ValueError(f"Unknown terminal profile: {profile_id}")
        return self._profiles[profile_id]

    def default_profile_id(self) -> str | None:
        for profile in self._profiles.values():
            if profile.default:
                return profile.profile_id
        return next(iter(self._profiles), None)

    def _builtin_profiles(self) -> dict[str, TerminalProfile]:
        if os.name == "nt":
            return {
                "managed-projected": TerminalProfile(
                    profile_id="managed-projected",
                    label="Managed Projected",
                    description="Command-mode managed shell in the projected workspace.",
                    transport="runner",
                    io_mode="command",
                    runner_mode="projected",
                    cwd_mode="runner_mount",
                    default=True,
                ),
                "managed-pty": TerminalProfile(
                    profile_id="managed-pty",
                    label="Managed PTY",
                    description="Interactive PTY shell rooted in the projected workspace.",
                    transport="runner",
                    io_mode="pty",
                    runner_mode="projected",
                    cwd_mode="runner_mount",
                ),
                "strict-command": TerminalProfile(
                    profile_id="strict-command",
                    label="Strict Command",
                    description="Command-mode managed shell in strict mode.",
                    transport="runner",
                    io_mode="command",
                    runner_mode="strict",
                    cwd_mode="runner_mount",
                ),
                "cmd": TerminalProfile(
                    profile_id="cmd",
                    label="Command Prompt",
                    description="Interactive Windows Command Prompt.",
                    transport="host",
                    io_mode="pty",
                    spawn_argv=["cmd.exe"],
                    command_argv_prefix=["cmd.exe", "/d", "/s", "/c"],
                ),
                "powershell": TerminalProfile(
                    profile_id="powershell",
                    label="Windows PowerShell",
                    description="Interactive Windows PowerShell with the user profile.",
                    transport="host",
                    io_mode="pty",
                    spawn_argv=["powershell.exe", "-NoLogo"],
                    command_argv_prefix=["powershell.exe", "-NoLogo", "-Command"],
                ),
                "pwsh": TerminalProfile(
                    profile_id="pwsh",
                    label="PowerShell",
                    description="Interactive PowerShell Core with the user profile.",
                    transport="host",
                    io_mode="pty",
                    spawn_argv=["pwsh.exe", "-NoLogo"],
                    command_argv_prefix=["pwsh.exe", "-NoLogo", "-Command"],
                ),
                "wsl-bash": TerminalProfile(
                    profile_id="wsl-bash",
                    label="WSL Bash",
                    description="Interactive WSL bash shell.",
                    transport="host",
                    io_mode="pty",
                    spawn_argv=["wsl.exe", "bash", "-il"],
                    command_argv_prefix=["wsl.exe", "bash", "-lc"],
                ),
                "wsl-tmux": TerminalProfile(
                    profile_id="wsl-tmux",
                    label="WSL tmux",
                    description="Attach to or create a tmux session inside WSL.",
                    transport="host",
                    io_mode="pty",
                    spawn_argv=["wsl.exe", "bash", "-lc", "tmux attach -t main || tmux new -s main"],
                ),
            }
        shell = os.environ.get("SHELL", "/bin/bash")
        shell_name = Path(shell).name or "shell"
        return {
            "managed-projected": TerminalProfile(
                profile_id="managed-projected",
                label="Managed Projected",
                description="Command-mode managed shell in the projected workspace.",
                transport="runner",
                io_mode="command",
                runner_mode="projected",
                cwd_mode="runner_mount",
                default=True,
            ),
            "managed-pty": TerminalProfile(
                profile_id="managed-pty",
                label="Managed PTY",
                description="Interactive PTY shell rooted in the projected workspace.",
                transport="runner",
                io_mode="pty",
                runner_mode="projected",
                cwd_mode="runner_mount",
                spawn_argv=[shell],
            ),
            "strict-command": TerminalProfile(
                profile_id="strict-command",
                label="Strict Command",
                description="Command-mode managed shell in strict mode.",
                transport="runner",
                io_mode="command",
                runner_mode="strict",
                cwd_mode="runner_mount",
            ),
            "host-shell": TerminalProfile(
                profile_id="host-shell",
                label=f"Host {shell_name}",
                description="Interactive host shell using the user's default terminal.",
                transport="host",
                io_mode="pty",
                spawn_argv=[shell],
                command_argv_prefix=[shell, "-lc"],
            ),
            "tmux": TerminalProfile(
                profile_id="tmux",
                label="tmux",
                description="Attach to or create a tmux session on the host.",
                transport="host",
                io_mode="pty",
                spawn_argv=[shell, "-lc", "tmux attach -t main || tmux new -s main"],
            ),
        }

    def _load_user_profiles(self) -> list[TerminalProfile]:
        if not self.config_path.exists():
            return []
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        items = raw.get("profiles") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValueError("terminal profile config must be a list or an object with a 'profiles' list")
        return [self._profile_from_mapping(item, source="user") for item in items if isinstance(item, dict)]

    @staticmethod
    def _profile_from_mapping(mapping: dict[str, object], *, source: str) -> TerminalProfile:
        profile_id = str(mapping.get("profile_id") or mapping.get("id") or "").strip()
        label = str(mapping.get("label") or "").strip()
        if not profile_id or not label:
            raise ValueError("terminal profile requires non-empty 'profile_id'/'id' and 'label'")
        transport = str(mapping.get("transport", "host")).strip()
        io_mode = str(mapping.get("io_mode", "pty")).strip()
        runner_mode_raw = mapping.get("runner_mode")
        runner_mode = None if runner_mode_raw in {None, ""} else str(runner_mode_raw).strip()
        cwd_mode = str(mapping.get("cwd_mode", "project")).strip()
        if transport not in VALID_TRANSPORTS:
            raise ValueError(f"invalid terminal profile transport: {transport}")
        if io_mode not in VALID_IO_MODES:
            raise ValueError(f"invalid terminal profile io_mode: {io_mode}")
        if runner_mode is not None and runner_mode not in VALID_RUNNER_MODES:
            raise ValueError(f"invalid terminal profile runner_mode: {runner_mode}")
        if cwd_mode not in VALID_CWD_MODES:
            raise ValueError(f"invalid terminal profile cwd_mode: {cwd_mode}")
        spawn_argv = [str(item) for item in mapping.get("spawn_argv", []) if str(item).strip()]
        command_argv_prefix = [str(item) for item in mapping.get("command_argv_prefix", []) if str(item).strip()]
        env_raw = mapping.get("env", {})
        env = {str(key): str(value) for key, value in env_raw.items()} if isinstance(env_raw, dict) else {}
        return TerminalProfile(
            profile_id=profile_id,
            label=label,
            description=str(mapping.get("description", "")).strip(),
            transport=transport,
            io_mode=io_mode,
            runner_mode=runner_mode,
            spawn_argv=spawn_argv,
            command_argv_prefix=command_argv_prefix,
            env=env,
            cwd_mode=cwd_mode,
            source=source,
            default=bool(mapping.get("default", False)),
        )
