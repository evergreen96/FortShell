from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.windows.platforms import StrictBackendInvocation
from backend.strict_backend_validator import StrictBackendValidator


class StrictBackendValidatorTests(unittest.TestCase):
    def test_rejects_wsl_invocation_without_filesystem_isolation(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="wsl",
                command=[
                    "wsl.exe",
                    "-e",
                    "sh",
                    "-lc",
                    (
                        "mkdir -p /tmp/ai-ide-home /tmp/ai-ide-cache && "
                        "cd /mnt/c/project && "
                        "exec env -i AI_IDE_STRICT_BACKEND=wsl AI_IDE_SANDBOX_ROOT=/workspace "
                        "HOME=/tmp/ai-ide-home TMPDIR=/tmp XDG_CACHE_HOME=/tmp/ai-ide-cache "
                        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
                        "sh -lc 'printf ok'"
                    ),
                ],
                host_working_directory=projected_root,
                working_directory="/mnt/c/project",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertFalse(result.valid)
        self.assertIn("host filesystem mounts", result.reason)

    def test_rejects_bwrap_invocation_without_writable_workspace_bind(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="bwrap",
                command=[
                    "bwrap",
                    "--die-with-parent",
                    "--unshare-net",
                    "--ro-bind",
                    str(projected_root),
                    "/workspace",
                    "--ro-bind",
                    "/usr",
                    "/usr",
                    "--clearenv",
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
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
                    "--setenv",
                    "HOME",
                    "/tmp/ai-ide-home",
                    "--setenv",
                    "TMPDIR",
                    "/tmp",
                    "--setenv",
                    "XDG_CACHE_HOME",
                    "/tmp/ai-ide-cache",
                    "--chdir",
                    "/workspace",
                    "sh",
                    "-lc",
                    "printf ok",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertFalse(result.valid)
        self.assertIn("writable /workspace bind", result.reason)

    def test_accepts_restricted_host_helper_invocation_contract(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="restricted-host-helper",
                command=[
                    "C:/tools/ai-ide-restricted-host-helper.exe",
                    "--workspace",
                    str(projected_root),
                    "--cwd",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_RUNNER_MODE",
                    "strict",
                    "--setenv",
                    "AI_IDE_STRICT_BACKEND",
                    "restricted-host-helper",
                    "--setenv",
                    "AI_IDE_STRICT_PREVIEW",
                    "1",
                    "--setenv",
                    "AI_IDE_SANDBOX_ROOT",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
                    "--setenv",
                    "HOME",
                    "C:/Temp/ai-ide-strict/home",
                    "--setenv",
                    "TMPDIR",
                    "C:/Temp/ai-ide-strict/tmp",
                    "--setenv",
                    "XDG_CACHE_HOME",
                    "C:/Temp/ai-ide-strict/cache",
                    "--command",
                    "printf ok",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertTrue(result.valid)

    def test_accepts_restricted_host_helper_one_shot_argv_contract(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="restricted-host-helper",
                command=[
                    "C:/tools/ai-ide-restricted-host-helper.exe",
                    "--workspace",
                    str(projected_root),
                    "--cwd",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_RUNNER_MODE",
                    "strict",
                    "--setenv",
                    "AI_IDE_STRICT_BACKEND",
                    "restricted-host-helper",
                    "--setenv",
                    "AI_IDE_STRICT_PREVIEW",
                    "1",
                    "--setenv",
                    "AI_IDE_SANDBOX_ROOT",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
                    "--setenv",
                    "HOME",
                    "C:/Temp/ai-ide-strict/home",
                    "--setenv",
                    "TMPDIR",
                    "C:/Temp/ai-ide-strict/tmp",
                    "--setenv",
                    "XDG_CACHE_HOME",
                    "C:/Temp/ai-ide-strict/cache",
                    "--argv=python",
                    "--argv=-c",
                    "--argv=print('ok')",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertTrue(result.valid)

    def test_accepts_restricted_host_helper_invocation_with_stdio_proxy(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="restricted-host-helper",
                command=[
                    "C:/tools/ai-ide-restricted-host-helper.exe",
                    "--workspace",
                    str(projected_root),
                    "--cwd",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_RUNNER_MODE",
                    "strict",
                    "--setenv",
                    "AI_IDE_STRICT_BACKEND",
                    "restricted-host-helper",
                    "--setenv",
                    "AI_IDE_STRICT_PREVIEW",
                    "1",
                    "--setenv",
                    "AI_IDE_SANDBOX_ROOT",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
                    "--setenv",
                    "HOME",
                    "C:/Temp/ai-ide-strict/home",
                    "--setenv",
                    "TMPDIR",
                    "C:/Temp/ai-ide-strict/tmp",
                    "--setenv",
                    "XDG_CACHE_HOME",
                    "C:/Temp/ai-ide-strict/cache",
                    "--control-file",
                    str(projected_root / ".control" / "stop.txt"),
                    "--response-file",
                    str(projected_root / ".control" / "status.json"),
                    "--stdio-proxy",
                    "--command",
                    "python -u -V",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertTrue(result.valid)

    def test_rejects_restricted_host_helper_process_mode_without_control_file(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="restricted-host-helper",
                command=[
                    "C:/tools/ai-ide-restricted-host-helper.exe",
                    "--workspace",
                    str(projected_root),
                    "--cwd",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_RUNNER_MODE",
                    "strict",
                    "--setenv",
                    "AI_IDE_STRICT_BACKEND",
                    "restricted-host-helper",
                    "--setenv",
                    "AI_IDE_STRICT_PREVIEW",
                    "1",
                    "--setenv",
                    "AI_IDE_SANDBOX_ROOT",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
                    "--setenv",
                    "HOME",
                    "C:/Temp/ai-ide-strict/home",
                    "--setenv",
                    "TMPDIR",
                    "C:/Temp/ai-ide-strict/tmp",
                    "--setenv",
                    "XDG_CACHE_HOME",
                    "C:/Temp/ai-ide-strict/cache",
                    "--stdio-proxy",
                    "--argv=python",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertFalse(result.valid)
        self.assertIn("control-file", result.reason)

    def test_rejects_restricted_host_helper_process_mode_without_response_file(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="restricted-host-helper",
                command=[
                    "C:/tools/ai-ide-restricted-host-helper.exe",
                    "--workspace",
                    str(projected_root),
                    "--cwd",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_RUNNER_MODE",
                    "strict",
                    "--setenv",
                    "AI_IDE_STRICT_BACKEND",
                    "restricted-host-helper",
                    "--setenv",
                    "AI_IDE_STRICT_PREVIEW",
                    "1",
                    "--setenv",
                    "AI_IDE_SANDBOX_ROOT",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
                    "--setenv",
                    "HOME",
                    "C:/Temp/ai-ide-strict/home",
                    "--setenv",
                    "TMPDIR",
                    "C:/Temp/ai-ide-strict/tmp",
                    "--setenv",
                    "XDG_CACHE_HOME",
                    "C:/Temp/ai-ide-strict/cache",
                    "--control-file",
                    str(projected_root / ".control" / "stop.txt"),
                    "--stdio-proxy",
                    "--argv=python",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertFalse(result.valid)
        self.assertIn("response-file", result.reason)

    def test_rejects_restricted_host_helper_reserved_env_override(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="restricted-host-helper",
                command=[
                    "C:/tools/ai-ide-restricted-host-helper.exe",
                    "--workspace",
                    str(projected_root),
                    "--cwd",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_RUNNER_MODE",
                    "strict",
                    "--setenv",
                    "AI_IDE_STRICT_BACKEND",
                    "restricted-host-helper",
                    "--setenv",
                    "AI_IDE_STRICT_PREVIEW",
                    "1",
                    "--setenv",
                    "AI_IDE_SANDBOX_ROOT",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
                    "--setenv",
                    "HOME",
                    "C:/Temp/ai-ide-strict/home",
                    "--setenv",
                    "TMPDIR",
                    "C:/Temp/ai-ide-strict/tmp",
                    "--setenv",
                    "XDG_CACHE_HOME",
                    "C:/Temp/ai-ide-strict/cache",
                    "--setenv",
                    "PATH",
                    "C:/Users/Public",
                    "--command",
                    "printf ok",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertFalse(result.valid)
        self.assertIn("must not override PATH", result.reason)

    def test_rejects_restricted_host_helper_current_directory_search_override(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="restricted-host-helper",
                command=[
                    "C:/tools/ai-ide-restricted-host-helper.exe",
                    "--workspace",
                    str(projected_root),
                    "--cwd",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_RUNNER_MODE",
                    "strict",
                    "--setenv",
                    "AI_IDE_STRICT_BACKEND",
                    "restricted-host-helper",
                    "--setenv",
                    "AI_IDE_STRICT_PREVIEW",
                    "1",
                    "--setenv",
                    "AI_IDE_SANDBOX_ROOT",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
                    "--setenv",
                    "HOME",
                    "C:/Temp/ai-ide-strict/home",
                    "--setenv",
                    "TMPDIR",
                    "C:/Temp/ai-ide-strict/tmp",
                    "--setenv",
                    "XDG_CACHE_HOME",
                    "C:/Temp/ai-ide-strict/cache",
                    "--setenv",
                    "NoDefaultCurrentDirectoryInExePath",
                    "0",
                    "--command",
                    "printf ok",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertFalse(result.valid)
        self.assertIn("must not override NoDefaultCurrentDirectoryInExePath", result.reason)

    def test_rejects_bwrap_duplicate_reserved_env_override(self) -> None:
        validator = StrictBackendValidator()
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir)
            invocation = StrictBackendInvocation(
                backend="bwrap",
                command=[
                    "bwrap",
                    "--die-with-parent",
                    "--unshare-net",
                    "--bind",
                    str(projected_root),
                    "/workspace",
                    "--ro-bind",
                    "/usr",
                    "/usr",
                    "--clearenv",
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
                    "AI_IDE_BOUNDARY_SCOPE",
                    "workspace-only",
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
                    "--setenv",
                    "HOME",
                    "/tmp/evil-home",
                    "--chdir",
                    "/workspace",
                    "sh",
                    "-lc",
                    "printf ok",
                ],
                host_working_directory=projected_root,
                working_directory="/workspace",
            )

            result = validator.validate(invocation, projected_root=projected_root)

        self.assertFalse(result.valid)
        self.assertIn("duplicate env contract: HOME", result.reason)


if __name__ == "__main__":
    unittest.main()
