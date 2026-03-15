from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.windows.platforms import PlatformAdapter, StrictBackendInvocation, StrictSandboxProbe
from backend.strict_backend_health_service import StrictBackendHealthService
from backend.strict_backend_validator import StrictBackendValidator


class _FakeProjectionManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def projection_root(self, session_id: str) -> Path:
        return self.root / session_id

    @property
    def mount_root(self):
        return self.root / "sess-1234"

    def mount(self, session_id):
        from core.filtered_fs_backend import MountResult
        return MountResult(mount_root=self.root / "sess-1234", session_id=session_id, policy_version=1)


class _FakeSessionManager:
    current_session_id = "sess-1234"


class _ReadyPlatformAdapter(PlatformAdapter):
    name = "fake"

    def __init__(self, invocation: StrictBackendInvocation | None) -> None:
        self.invocation = invocation

    def strict_probe(self) -> StrictSandboxProbe:
        return StrictSandboxProbe(
            platform_name="fake",
            ready=True,
            backend="wsl",
            status_code="ready",
            detail="ready",
        )

    def strict_backend_invocation(self, command: str, projected_root: Path, env=None):
        return self.invocation


class StrictBackendHealthServiceTests(unittest.TestCase):
    def test_health_reports_invalid_contract_when_probe_ready_but_invocation_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = StrictBackendHealthService(
                _ReadyPlatformAdapter(None),
                _FakeProjectionManager(Path(temp_dir)),
                _FakeSessionManager(),
                StrictBackendValidator(),
            )

            health = service.health()

        self.assertFalse(health.ready)
        self.assertEqual("missing_invocation", health.contract_status)

    def test_health_reports_invalid_contract_for_current_wsl_mount_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()
            service = StrictBackendHealthService(
                _ReadyPlatformAdapter(
                    StrictBackendInvocation(
                        backend="wsl",
                        command=[
                            "wsl.exe",
                            "-e",
                            "sh",
                            "-lc",
                            (
                                "mkdir -p /tmp/ai-ide-home /tmp/ai-ide-cache && "
                                "cd /mnt/c/projection && "
                                "exec env -i AI_IDE_STRICT_BACKEND=wsl AI_IDE_SANDBOX_ROOT=/workspace "
                                "HOME=/tmp/ai-ide-home TMPDIR=/tmp XDG_CACHE_HOME=/tmp/ai-ide-cache "
                                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
                                "sh -lc 'printf ok'"
                            ),
                        ],
                        host_working_directory=projected_root / "sess-1234",
                        working_directory="/mnt/c/projection",
                    )
                ),
                _FakeProjectionManager(projected_root),
                _FakeSessionManager(),
                StrictBackendValidator(),
            )

            health = service.health()

        self.assertFalse(health.ready)
        self.assertEqual("invalid_contract", health.contract_status)
        self.assertIn("host filesystem mounts", health.contract_detail)

    def test_health_reports_ready_for_restricted_host_helper_contract(self) -> None:
        class _HelperReadyPlatformAdapter(PlatformAdapter):
            name = "windows"

            def strict_probe(self) -> StrictSandboxProbe:
                return StrictSandboxProbe(
                    platform_name="windows",
                    ready=True,
                    backend="restricted-host-helper",
                    status_code="ready",
                    detail="ready",
                )

            def strict_backend_invocation(self, command: str, projected_root: Path, env=None):
                return StrictBackendInvocation(
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
                        command,
                    ],
                    host_working_directory=projected_root,
                    working_directory="/workspace",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()
            service = StrictBackendHealthService(
                _HelperReadyPlatformAdapter(),
                _FakeProjectionManager(projected_root),
                _FakeSessionManager(),
                StrictBackendValidator(),
            )

            health = service.health()

        self.assertTrue(health.ready)
        self.assertEqual("valid", health.contract_status)


if __name__ == "__main__":
    unittest.main()
