from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
import shlex

from ai_ide.workspace_visibility_backend import WorkspaceVisibilityWatcher
from ai_ide.windows_strict_helper_resolution import (
    WINDOWS_STRICT_HELPER_ENV,
    resolve_windows_strict_helper_command,
)
from ai_ide.windows_strict_helper_protocol import (
    WindowsStrictHelperRequest,
    build_helper_command,
    encode_visible_host_path_token,
)


STRICT_RESERVED_ENV_KEYS = {
    "ai_ide_runner_mode",
    "ai_ide_strict_backend",
    "ai_ide_strict_preview",
    "ai_ide_sandbox_root",
    "ai_ide_boundary_scope",
    "ai_ide_blocked_read_roots",
    "home",
    "tmpdir",
    "xdg_cache_home",
    "path",
    "pathext",
    "nodefaultcurrentdirectoryinexepath",
    "systemroot",
    "windir",
    "comspec",
    "userprofile",
    "temp",
    "tmp",
}

DEFAULT_STRICT_BOUNDARY_SCOPE = "workspace-only"


@dataclass(frozen=True)
class PlatformCapabilities:
    platform_name: str
    projection_supported: bool
    strict_sandbox_available: bool
    strict_sandbox_strategy: str


@dataclass(frozen=True)
class StrictSandboxProbe:
    platform_name: str
    ready: bool
    backend: str
    status_code: str
    detail: str


@dataclass(frozen=True)
class StrictBackendInvocation:
    backend: str
    command: list[str]
    host_working_directory: Path
    working_directory: str


@dataclass(frozen=True)
class StrictBackendFixtureExpectations:
    sandbox_root: str
    home_prefix: str
    cache_prefix: str


class PlatformAdapter:
    name = "generic"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform_name=self.name,
            projection_supported=True,
            strict_sandbox_available=False,
            strict_sandbox_strategy="none",
        )

    def pty_available(self) -> bool:
        from ai_ide.pty_session import pty_available

        return pty_available(self.name.capitalize() if self.name != "generic" else None)

    def runtime_root(self, project_root: Path) -> Path:
        project_hash = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]
        return self.cache_root() / "projects" / project_hash

    def cache_root(self) -> Path:
        for candidate in self.cache_root_candidates():
            try:
                candidate.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            return candidate
        return Path(tempfile.gettempdir()) / "ai_ide_runtime"

    def cache_root_candidates(self) -> list[Path]:
        return [Path(tempfile.gettempdir()) / "ai_ide_runtime"]

    def strict_probe(self) -> StrictSandboxProbe:
        return StrictSandboxProbe(
            platform_name=self.name,
            ready=False,
            backend="none",
            status_code="unsupported",
            detail="no strict sandbox backend configured",
        )

    def strict_backend_invocation(
        self,
        command: str,
        projected_root: Path,
        env: dict[str, str] | None = None,
        *,
        process_mode: bool = False,
        argv: list[str] | None = None,
        control_file: Path | None = None,
        response_file: Path | None = None,
    ) -> StrictBackendInvocation | None:
        return None

    def strict_backend_visible_path(self, host_path: Path, backend: str) -> str:
        return str(host_path.resolve())

    def strict_backend_fixture_expectations(self, backend: str) -> StrictBackendFixtureExpectations:
        return StrictBackendFixtureExpectations(
            sandbox_root="/workspace",
            home_prefix="/tmp/ai-ide-home",
            cache_prefix="/tmp/ai-ide-cache",
        )

    def workspace_visibility_watcher(
        self,
        project_root: Path,
        runtime_root: Path,
    ) -> WorkspaceVisibilityWatcher | None:
        return None


class WindowsPlatformAdapter(PlatformAdapter):
    name = "windows"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform_name=self.name,
            projection_supported=True,
            strict_sandbox_available=True,
            strict_sandbox_strategy="wsl2-or-restricted-host-helper",
        )

    def cache_root(self) -> Path:
        return super().cache_root()

    def cache_root_candidates(self) -> list[Path]:
        candidates = []
        base = os.environ.get("LOCALAPPDATA")
        if base:
            candidates.append(Path(base) / "ai_ide_runtime")
        candidates.extend(super().cache_root_candidates())
        return candidates

    def strict_probe(self) -> StrictSandboxProbe:
        helper_command = resolve_windows_strict_helper_command()
        if helper_command:
            return StrictSandboxProbe(
                platform_name=self.name,
                ready=True,
                backend="restricted-host-helper",
                status_code="ready",
                detail="restricted host helper detected for strict backend execution",
            )
        wsl_binary = shutil.which("wsl.exe") or shutil.which("wsl")
        if wsl_binary:
            result = _probe_subprocess([wsl_binary, "--status"])
            if result.returncode == 0:
                return StrictSandboxProbe(
                    platform_name=self.name,
                    ready=True,
                    backend="wsl",
                    status_code="ready",
                    detail="wsl.exe is available for strict backend execution",
                )
            detail = (result.stderr or result.stdout or "wsl probe failed").strip().replace("\n", " ")
            return StrictSandboxProbe(
                platform_name=self.name,
                ready=False,
                backend="wsl",
                status_code=_classify_wsl_probe_failure(detail),
                detail=_ascii_safe(detail),
            )
        return StrictSandboxProbe(
            platform_name=self.name,
            ready=False,
            backend="wsl",
            status_code="not_found",
            detail="wsl.exe not found; strict sandbox currently limited to guarded preview",
        )

    def strict_backend_invocation(
        self,
        command: str,
        projected_root: Path,
        env: dict[str, str] | None = None,
        *,
        process_mode: bool = False,
        argv: list[str] | None = None,
        control_file: Path | None = None,
        response_file: Path | None = None,
    ) -> StrictBackendInvocation | None:
        probe = self.strict_probe()
        if probe.backend == "restricted-host-helper" and probe.ready:
            helper_command = resolve_windows_strict_helper_command()
            if not helper_command:
                return None
            return StrictBackendInvocation(
                backend="restricted-host-helper",
                command=_build_windows_restricted_host_helper_command(
                    helper_command,
                    projected_root,
                    command,
                    env=env,
                    process_mode=process_mode,
                    argv=argv,
                    control_file=control_file,
                    response_file=response_file,
                ),
                host_working_directory=projected_root,
                working_directory="/workspace",
            )
        if not probe.ready:
            return None

        wsl_binary = shutil.which("wsl.exe") or shutil.which("wsl")
        if not wsl_binary:
            return None

        linux_root = _windows_path_to_wsl_path(projected_root)
        merged_env = _merge_strict_backend_env(
            {
                "AI_IDE_RUNNER_MODE": "strict",
                "AI_IDE_STRICT_BACKEND": "wsl",
                "AI_IDE_STRICT_PREVIEW": "1",
                "AI_IDE_SANDBOX_ROOT": "/workspace",
                "HOME": "/tmp/ai-ide-home",
                "TMPDIR": "/tmp",
                "XDG_CACHE_HOME": "/tmp/ai-ide-cache",
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            },
            env,
        )
        linux_env_parts = [
            "env",
            "-i",
        ]
        for name, value in merged_env.items():
            linux_env_parts.append(f"{name}={value}")
        linux_env = " ".join(shlex.quote(part) for part in linux_env_parts)
        wrapped_command = (
            "mkdir -p /tmp/ai-ide-home /tmp/ai-ide-cache && "
            f"cd {shlex.quote(linux_root)} && "
            f"exec {linux_env} sh -lc {shlex.quote(command)}"
        )
        return StrictBackendInvocation(
            backend="wsl",
            command=[wsl_binary, "-e", "sh", "-lc", wrapped_command],
            host_working_directory=projected_root,
            working_directory=linux_root,
        )

    def strict_backend_visible_path(self, host_path: Path, backend: str) -> str:
        if backend == "wsl":
            return _windows_path_to_wsl_path(host_path)
        if backend == "restricted-host-helper":
            return encode_visible_host_path_token(host_path)
        return super().strict_backend_visible_path(host_path, backend)

    def strict_backend_fixture_expectations(self, backend: str) -> StrictBackendFixtureExpectations:
        if backend == "restricted-host-helper":
            helper_root = Path(tempfile.gettempdir()) / "ai_ide_strict_helper"
            return StrictBackendFixtureExpectations(
                sandbox_root="/workspace",
                home_prefix=str(helper_root),
                cache_prefix=str(helper_root),
            )
        return super().strict_backend_fixture_expectations(backend)


class LinuxPlatformAdapter(PlatformAdapter):
    name = "linux"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform_name=self.name,
            projection_supported=True,
            strict_sandbox_available=True,
            strict_sandbox_strategy="rootless-container-plus-landlock",
        )

    def cache_root(self) -> Path:
        return super().cache_root()

    def cache_root_candidates(self) -> list[Path]:
        candidates = []
        base = os.environ.get("XDG_CACHE_HOME")
        if base:
            candidates.append(Path(base) / "ai_ide_runtime")
        else:
            candidates.append(Path.home() / ".cache" / "ai_ide_runtime")
        candidates.extend(super().cache_root_candidates())
        return candidates

    def strict_probe(self) -> StrictSandboxProbe:
        if shutil.which("bwrap"):
            return StrictSandboxProbe(
                platform_name=self.name,
                ready=True,
                backend="bwrap",
                status_code="ready",
                detail="bubblewrap detected for strict backend execution",
            )
        if shutil.which("unshare"):
            return StrictSandboxProbe(
                platform_name=self.name,
                ready=False,
                backend="unshare",
                status_code="not_wired",
                detail="unshare detected but no strict backend executor is wired yet",
            )
        return StrictSandboxProbe(
            platform_name=self.name,
            ready=False,
            backend="none",
            status_code="not_found",
            detail="no supported strict sandbox backend detected",
        )

    def strict_backend_invocation(
        self,
        command: str,
        projected_root: Path,
        env: dict[str, str] | None = None,
        *,
        process_mode: bool = False,
        argv: list[str] | None = None,
        control_file: Path | None = None,
        response_file: Path | None = None,
    ) -> StrictBackendInvocation | None:
        probe = self.strict_probe()
        env = _merge_strict_backend_env({}, env)
        if probe.backend == "bwrap" and probe.ready:
            base_env_args = [
                "--setenv",
                "AI_IDE_RUNNER_MODE",
                "strict",
                "--setenv",
                "AI_IDE_STRICT_BACKEND",
                "bwrap",
                "--setenv",
                "AI_IDE_STRICT_PREVIEW",
                "1",
                "--setenv",
                "AI_IDE_SANDBOX_ROOT",
                "/workspace",
                "--setenv",
                "HOME",
                "/tmp/ai-ide-home",
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--setenv",
                "XDG_CACHE_HOME",
                "/tmp/ai-ide-cache",
                "--setenv",
                "PATH",
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            ]
            extra_env_args: list[str] = []
            for name, value in env.items():
                extra_env_args.extend(["--setenv", name, value])
            read_only_binds = _build_linux_bwrap_read_only_binds()
            return StrictBackendInvocation(
                backend="bwrap",
                command=[
                    "bwrap",
                    "--die-with-parent",
                    "--unshare-net",
                    "--bind",
                    str(projected_root),
                    "/workspace",
                    *read_only_binds,
                    "--tmpfs",
                    "/tmp",
                    "--dir",
                    "/tmp/ai-ide-home",
                    "--dir",
                    "/tmp/ai-ide-cache",
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                    "--chdir",
                    "/workspace",
                    "--clearenv",
                    *base_env_args,
                    *extra_env_args,
                    "sh",
                    "-lc",
                    command,
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )
        return None


class MacOSPlatformAdapter(PlatformAdapter):
    name = "macos"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform_name=self.name,
            projection_supported=True,
            strict_sandbox_available=True,
            strict_sandbox_strategy="managed-linux-vm-plus-native-app-sandbox",
        )

    def cache_root(self) -> Path:
        return super().cache_root()

    def cache_root_candidates(self) -> list[Path]:
        return [
            Path.home() / "Library" / "Caches" / "ai_ide_runtime",
            *super().cache_root_candidates(),
        ]

    def strict_probe(self) -> StrictSandboxProbe:
        return StrictSandboxProbe(
            platform_name=self.name,
            ready=False,
            backend="native-helper",
            status_code="not_wired",
            detail="strict sandbox requires a native helper or managed VM integration",
        )


def get_platform_adapter(system_name: str | None = None) -> PlatformAdapter:
    system_name = system_name or platform.system()
    if system_name == "Windows":
        return WindowsPlatformAdapter()
    if system_name == "Linux":
        return LinuxPlatformAdapter()
    if system_name == "Darwin":
        return MacOSPlatformAdapter()
    return PlatformAdapter()


def _probe_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=10,
    )


def _build_windows_restricted_host_helper_command(
    helper_command: list[str],
    projected_root: Path,
    command: str,
    *,
    env: dict[str, str] | None = None,
    process_mode: bool = False,
    argv: list[str] | None = None,
    control_file: Path | None = None,
    response_file: Path | None = None,
) -> list[str]:
    env = dict(env or {})
    blocked_read_roots = env.pop("AI_IDE_BLOCKED_READ_ROOTS", None)
    sandbox_root = Path(tempfile.gettempdir()) / "ai_ide_strict_helper" / hashlib.sha256(
        str(projected_root.resolve()).encode("utf-8")
    ).hexdigest()[:12]
    home_dir = sandbox_root / "home"
    temp_dir = sandbox_root / "tmp"
    cache_dir = sandbox_root / "cache"
    request_env = _merge_strict_backend_env(
        {
        "AI_IDE_RUNNER_MODE": "strict",
        "AI_IDE_STRICT_BACKEND": "restricted-host-helper",
        "AI_IDE_STRICT_PREVIEW": "1",
        "AI_IDE_SANDBOX_ROOT": "/workspace",
        "AI_IDE_BOUNDARY_SCOPE": DEFAULT_STRICT_BOUNDARY_SCOPE,
        "HOME": str(home_dir),
        "TMPDIR": str(temp_dir),
        "XDG_CACHE_HOME": str(cache_dir),
        },
        env,
    )
    if blocked_read_roots:
        request_env["AI_IDE_BLOCKED_READ_ROOTS"] = blocked_read_roots
    return build_helper_command(
        helper_command,
        WindowsStrictHelperRequest(
            workspace=projected_root,
            cwd="/workspace",
            environment=request_env,
            command=command if (not process_mode and not argv) else None,
            argv=tuple(argv or ()),
            stdio_proxy=process_mode,
            control_file=control_file if process_mode else None,
            response_file=response_file if process_mode else None,
        ),
    )


def _windows_path_to_wsl_path(path: Path) -> str:
    resolved = str(path.resolve())
    drive, remainder = resolved[0], resolved[2:]
    normalized = remainder.replace("\\", "/").lstrip("/")
    return f"/mnt/{drive.lower()}/{normalized}"


def _build_linux_bwrap_read_only_binds() -> list[str]:
    binds: list[str] = []
    for path in _existing_system_bind_paths(
        [
            "/usr",
            "/bin",
            "/lib",
            "/lib64",
            "/sbin",
            "/nix/store",
            "/run/current-system/sw",
        ]
    ):
        binds.extend(["--ro-bind", path, path])
    return binds


def _existing_system_bind_paths(candidates: list[str]) -> list[str]:
    return [candidate for candidate in candidates if Path(candidate).exists()]


def _ascii_safe(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _merge_strict_backend_env(
    base: dict[str, str],
    overlay: dict[str, str] | None,
) -> dict[str, str]:
    merged = dict(base)
    if not overlay:
        return merged
    for name, value in overlay.items():
        if name.lower() in STRICT_RESERVED_ENV_KEYS:
            continue
        merged[name] = value
    return merged


def _classify_wsl_probe_failure(detail: str) -> str:
    normalized = detail.lower()
    if "accessdenied" in normalized or "access denied" in normalized:
        return "access_denied"
    if "not found" in normalized or "could not be found" in normalized:
        return "not_found"
    return "probe_failed"
