from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_ide.platforms import StrictBackendInvocation
from ai_ide.windows_strict_helper_protocol import STDIO_PROXY_FLAG


@dataclass(frozen=True)
class StrictBackendValidationResult:
    valid: bool
    reason: str = ""


class StrictBackendValidator:
    def validate(
        self,
        invocation: StrictBackendInvocation,
        *,
        projected_root: Path,
    ) -> StrictBackendValidationResult:
        if not invocation.command:
            return StrictBackendValidationResult(False, "empty backend command")
        if invocation.host_working_directory.resolve() != projected_root.resolve():
            return StrictBackendValidationResult(False, "host working directory must match projected root")
        if not invocation.working_directory:
            return StrictBackendValidationResult(False, "missing backend working directory")
        if invocation.backend == "wsl":
            return self._validate_wsl(invocation)
        if invocation.backend == "restricted-host-helper":
            return self._validate_windows_helper(invocation, projected_root=projected_root)
        if invocation.backend == "bwrap":
            return self._validate_bwrap(invocation, projected_root=projected_root)
        return StrictBackendValidationResult(False, f"unsupported strict backend: {invocation.backend}")

    def _validate_wsl(self, invocation: StrictBackendInvocation) -> StrictBackendValidationResult:
        launcher = Path(invocation.command[0]).name.lower()
        if launcher not in {"wsl", "wsl.exe"}:
            return StrictBackendValidationResult(False, "wsl backend must launch through wsl.exe")
        if len(invocation.command) < 5 or invocation.command[1:4] != ["-e", "sh", "-lc"]:
            return StrictBackendValidationResult(False, "wsl backend must use `wsl -e sh -lc`")
        wrapper = invocation.command[-1]
        for snippet in [
            "mkdir -p /tmp/ai-ide-home /tmp/ai-ide-cache",
            f"cd {invocation.working_directory}",
            "AI_IDE_STRICT_BACKEND=wsl",
            "AI_IDE_SANDBOX_ROOT=/workspace",
            "HOME=/tmp/ai-ide-home",
            "TMPDIR=/tmp",
            "XDG_CACHE_HOME=/tmp/ai-ide-cache",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ]:
            if snippet not in wrapper:
                return StrictBackendValidationResult(False, f"missing wsl isolation contract: {snippet}")
        return StrictBackendValidationResult(
            False,
            "wsl backend still exposes host filesystem mounts outside the projected workspace",
        )

    def _validate_windows_helper(
        self,
        invocation: StrictBackendInvocation,
        *,
        projected_root: Path,
    ) -> StrictBackendValidationResult:
        command = invocation.command
        setenv_map = _collect_setenv_map(command)
        if invocation.working_directory != "/workspace":
            return StrictBackendValidationResult(False, "restricted host helper must use /workspace working directory")
        if not _contains_sequence(command, ["--workspace", str(projected_root)]):
            return StrictBackendValidationResult(False, "missing helper workspace mapping")
        if not _contains_sequence(command, ["--cwd", "/workspace"]):
            return StrictBackendValidationResult(False, "missing helper cwd mapping")
        for name, value in [
            ("AI_IDE_RUNNER_MODE", "strict"),
            ("AI_IDE_STRICT_BACKEND", "restricted-host-helper"),
            ("AI_IDE_STRICT_PREVIEW", "1"),
            ("AI_IDE_SANDBOX_ROOT", "/workspace"),
            ("AI_IDE_BOUNDARY_SCOPE", "workspace-only"),
        ]:
            error = _validate_required_setenv(setenv_map, name, value)
            if error:
                return StrictBackendValidationResult(False, error)
        for name in ["HOME", "TMPDIR", "XDG_CACHE_HOME"]:
            error = _validate_required_setenv(setenv_map, name)
            if error:
                return StrictBackendValidationResult(False, error)
        error = _reject_unexpected_setenv_names(
            setenv_map,
            {
                "PATH",
                "PATHEXT",
                "NoDefaultCurrentDirectoryInExePath",
                "SystemRoot",
                "WINDIR",
                "ComSpec",
                "USERPROFILE",
                "TEMP",
                "TMP",
            },
            "helper env contract",
        )
        if error:
            return StrictBackendValidationResult(False, error)
        has_argv = any(token == "--argv" or token.startswith("--argv=") for token in command)
        process_mode = STDIO_PROXY_FLAG in command
        if "--command" not in command and not has_argv:
            return StrictBackendValidationResult(False, "missing helper command or argv contract")
        if process_mode and "--control-file" not in command:
            return StrictBackendValidationResult(False, "missing helper control-file contract for process mode")
        if process_mode and "--response-file" not in command:
            return StrictBackendValidationResult(False, "missing helper response-file contract for process mode")
        return StrictBackendValidationResult(True)

    def _validate_bwrap(
        self,
        invocation: StrictBackendInvocation,
        *,
        projected_root: Path,
    ) -> StrictBackendValidationResult:
        command = invocation.command
        setenv_map = _collect_setenv_map(command)
        if Path(command[0]).name != "bwrap":
            return StrictBackendValidationResult(False, "bwrap backend must launch through bwrap")
        for token in ["--die-with-parent", "--unshare-net", "--clearenv", "--chdir", "/workspace"]:
            if token not in command:
                return StrictBackendValidationResult(False, f"missing bwrap isolation flag: {token}")
        if not _contains_sequence(command, ["--bind", str(projected_root), "/workspace"]):
            return StrictBackendValidationResult(False, "missing writable /workspace bind")
        if "--ro-bind" not in command:
            return StrictBackendValidationResult(False, "missing read-only system binds")
        for name, value in [
            ("AI_IDE_RUNNER_MODE", "strict"),
            ("AI_IDE_STRICT_BACKEND", "bwrap"),
            ("AI_IDE_STRICT_PREVIEW", "1"),
            ("AI_IDE_SANDBOX_ROOT", "/workspace"),
            ("HOME", "/tmp/ai-ide-home"),
            ("TMPDIR", "/tmp"),
            ("XDG_CACHE_HOME", "/tmp/ai-ide-cache"),
        ]:
            error = _validate_required_setenv(setenv_map, name, value)
            if error:
                return StrictBackendValidationResult(False, error)
        error = _reject_unexpected_setenv_names(
            setenv_map,
            {
                "PATHEXT",
                "NoDefaultCurrentDirectoryInExePath",
                "SystemRoot",
                "WINDIR",
                "ComSpec",
                "USERPROFILE",
                "TEMP",
                "TMP",
            },
            "bwrap env contract",
        )
        if error:
            return StrictBackendValidationResult(False, error)
        return StrictBackendValidationResult(True)


def _contains_sequence(items: list[str], sequence: list[str]) -> bool:
    if not sequence or len(items) < len(sequence):
        return False
    last = len(items) - len(sequence) + 1
    for index in range(last):
        if items[index : index + len(sequence)] == sequence:
            return True
    return False


def _collect_setenv_map(command: list[str]) -> dict[str, list[tuple[str, str]]]:
    setenv: dict[str, list[tuple[str, str]]] = {}
    for index in range(len(command) - 2):
        if command[index] != "--setenv":
            continue
        name = command[index + 1]
        value = command[index + 2]
        setenv.setdefault(name.lower(), []).append((name, value))
    return setenv


def _validate_required_setenv(
    setenv_map: dict[str, list[tuple[str, str]]],
    name: str,
    expected_value: str | None = None,
) -> str | None:
    entries = setenv_map.get(name.lower(), [])
    if not entries:
        return f"missing env contract: {name}"
    if len(entries) != 1:
        return f"duplicate env contract: {name}"
    _, actual_value = entries[0]
    if expected_value is not None and actual_value != expected_value:
        return f"invalid env contract: {name}"
    return None


def _reject_unexpected_setenv_names(
    setenv_map: dict[str, list[tuple[str, str]]],
    forbidden_names: set[str],
    label: str,
) -> str | None:
    for name in forbidden_names:
        if name.lower() in setenv_map:
            return f"{label} must not override {name}"
    return None
