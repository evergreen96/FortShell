from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.runner_models import RunnerResult
from backend.strict_backend_fixture_service import StrictBackendFixtureService
from backend.strict_backend_health_service import StrictBackendHealth


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


class StrictBackendFixtureServiceTests(unittest.TestCase):
    def test_run_reports_passed_when_backend_fixture_checks_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_root = base / "project"
            projection_root = base / "runtime" / "projections"
            project_root.mkdir()
            (project_root / "secrets").mkdir()
            (project_root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
            session_projection_root = projection_root / "sess-1234"
            session_projection_root.mkdir(parents=True)

            def strict_runner_run(command: str) -> RunnerResult:
                (session_projection_root / ".ai_ide_strict_fixture.txt").write_text("fixture", encoding="utf-8")
                return RunnerResult(
                    mode="strict",
                    backend="wsl",
                    returncode=0,
                    stdout=(
                        "__AI_IDE_FIXTURE__ sandbox=/workspace\n"
                        "__AI_IDE_FIXTURE__ home=/tmp/ai-ide-home\n"
                        "__AI_IDE_FIXTURE__ cache=/tmp/ai-ide-cache\n"
                        "__AI_IDE_FIXTURE__ restricted_token=enabled\n"
                        "__AI_IDE_FIXTURE__ write_boundary=enabled\n"
                        "__AI_IDE_FIXTURE__ read_boundary=enabled\n"
                        "__AI_IDE_FIXTURE__ denied_relative=hidden\n"
                        "__AI_IDE_FIXTURE__ denied_direct=hidden\n"
                        "__AI_IDE_FIXTURE__ direct_write=blocked\n"
                    ),
                    stderr="",
                    working_directory="/mnt/c/projection",
                )

            service = StrictBackendFixtureService(
                project_root=project_root,
                fs_backend=_FakeProjectionManager(projection_root),
                session_manager=_FakeSessionManager(),
                strict_backend_health_provider=lambda: StrictBackendHealth(
                    platform_name="windows",
                    backend="wsl",
                    probe_ready=True,
                    probe_status="ready",
                    probe_detail="ready",
                    contract_valid=True,
                    contract_status="valid",
                    contract_detail="contract valid",
                    ready=True,
                ),
                strict_runner_run=strict_runner_run,
                strict_backend_visible_path=lambda host_path, backend: "/mnt/c/project/.ai-ide/strict-backend-denied.txt",
            )

            report = service.run()

        self.assertEqual("passed", report.status)
        self.assertTrue(all(check.passed for check in report.checks))
        self.assertEqual("enabled", report.restricted_token_status)
        self.assertEqual("enabled", report.write_boundary_status)
        self.assertEqual("enabled", report.read_boundary_status)

    def test_run_skips_when_backend_health_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            service = StrictBackendFixtureService(
                project_root=base / "project",
                fs_backend=_FakeProjectionManager(base / "runtime" / "projections"),
                session_manager=_FakeSessionManager(),
                strict_backend_health_provider=lambda: StrictBackendHealth(
                    platform_name="windows",
                    backend="wsl",
                    probe_ready=False,
                    probe_status="not_found",
                    probe_detail="not found",
                    contract_valid=False,
                    contract_status="skipped",
                    contract_detail="probe not ready",
                    ready=False,
                ),
                strict_runner_run=lambda command: (_ for _ in ()).throw(AssertionError("should not run")),
                strict_backend_visible_path=lambda host_path, backend: str(host_path),
            )

            report = service.run()

        self.assertEqual("skipped", report.status)
        self.assertEqual("not found", report.reason)

    def test_run_fails_when_direct_host_path_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_root = base / "project"
            projection_root = base / "runtime" / "projections"
            project_root.mkdir()
            session_projection_root = projection_root / "sess-1234"
            session_projection_root.mkdir(parents=True)

            def strict_runner_run(command: str) -> RunnerResult:
                (session_projection_root / ".ai_ide_strict_fixture.txt").write_text("fixture", encoding="utf-8")
                return RunnerResult(
                    mode="strict",
                    backend="bwrap",
                    returncode=0,
                    stdout=(
                        "__AI_IDE_FIXTURE__ sandbox=/workspace\n"
                        "__AI_IDE_FIXTURE__ home=/tmp/ai-ide-home\n"
                        "__AI_IDE_FIXTURE__ cache=/tmp/ai-ide-cache\n"
                        "__AI_IDE_FIXTURE__ denied_relative=hidden\n"
                        "__AI_IDE_FIXTURE__ denied_direct=visible\n"
                        "__AI_IDE_FIXTURE__ direct_write=blocked\n"
                    ),
                    stderr="",
                    working_directory="/workspace",
                )

            service = StrictBackendFixtureService(
                project_root=project_root,
                fs_backend=_FakeProjectionManager(projection_root),
                session_manager=_FakeSessionManager(),
                strict_backend_health_provider=lambda: StrictBackendHealth(
                    platform_name="linux",
                    backend="bwrap",
                    probe_ready=True,
                    probe_status="ready",
                    probe_detail="ready",
                    contract_valid=True,
                    contract_status="valid",
                    contract_detail="contract valid",
                    ready=True,
                ),
                strict_runner_run=strict_runner_run,
                strict_backend_visible_path=lambda host_path, backend: str(host_path),
            )

            report = service.run()

        self.assertEqual("failed", report.status)
        self.assertEqual("denied_direct_path_hidden failed", report.reason)

    def test_run_fails_when_direct_host_write_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_root = base / "project"
            projection_root = base / "runtime" / "projections"
            project_root.mkdir()
            session_projection_root = projection_root / "sess-1234"
            session_projection_root.mkdir(parents=True)

            def strict_runner_run(command: str) -> RunnerResult:
                (session_projection_root / ".ai_ide_strict_fixture.txt").write_text("fixture", encoding="utf-8")
                (project_root / ".ai-ide" / "strict-backend-direct-write.txt").write_text(
                    "sandbox-write",
                    encoding="utf-8",
                )
                return RunnerResult(
                    mode="strict",
                    backend="bwrap",
                    returncode=0,
                    stdout=(
                        "__AI_IDE_FIXTURE__ sandbox=/workspace\n"
                        "__AI_IDE_FIXTURE__ home=/tmp/ai-ide-home\n"
                        "__AI_IDE_FIXTURE__ cache=/tmp/ai-ide-cache\n"
                        "__AI_IDE_FIXTURE__ denied_relative=hidden\n"
                        "__AI_IDE_FIXTURE__ denied_direct=hidden\n"
                        "__AI_IDE_FIXTURE__ direct_write=allowed\n"
                    ),
                    stderr="",
                    working_directory="/workspace",
                )

            service = StrictBackendFixtureService(
                project_root=project_root,
                fs_backend=_FakeProjectionManager(projection_root),
                session_manager=_FakeSessionManager(),
                strict_backend_health_provider=lambda: StrictBackendHealth(
                    platform_name="linux",
                    backend="bwrap",
                    probe_ready=True,
                    probe_status="ready",
                    probe_detail="ready",
                    contract_valid=True,
                    contract_status="valid",
                    contract_detail="contract valid",
                    ready=True,
                ),
                strict_runner_run=strict_runner_run,
                strict_backend_visible_path=lambda host_path, backend: str(host_path),
            )

            report = service.run()

        self.assertEqual("failed", report.status)
        self.assertEqual("direct_write_blocked failed", report.reason)

    def test_run_passes_with_restricted_host_helper_expectations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_root = base / "project"
            projection_root = base / "runtime" / "projections"
            project_root.mkdir()
            session_projection_root = projection_root / "sess-1234"
            session_projection_root.mkdir(parents=True)

            def strict_runner_run(command: str) -> RunnerResult:
                (session_projection_root / ".ai_ide_strict_fixture.txt").write_text("fixture", encoding="utf-8")
                return RunnerResult(
                    mode="strict",
                    backend="restricted-host-helper",
                    returncode=0,
                    stdout=(
                        "__AI_IDE_FIXTURE__ sandbox=/workspace\n"
                        f"__AI_IDE_FIXTURE__ home={base / 'helper-root' / 'home'}\n"
                        f"__AI_IDE_FIXTURE__ cache={base / 'helper-root' / 'cache'}\n"
                        "__AI_IDE_FIXTURE__ denied_relative=hidden\n"
                        "__AI_IDE_FIXTURE__ denied_direct=hidden\n"
                        "__AI_IDE_FIXTURE__ direct_write=blocked\n"
                    ),
                    stderr="",
                    working_directory="/workspace",
                )

            service = StrictBackendFixtureService(
                project_root=project_root,
                fs_backend=_FakeProjectionManager(projection_root),
                session_manager=_FakeSessionManager(),
                strict_backend_health_provider=lambda: StrictBackendHealth(
                    platform_name="windows",
                    backend="restricted-host-helper",
                    probe_ready=True,
                    probe_status="ready",
                    probe_detail="ready",
                    contract_valid=True,
                    contract_status="valid",
                    contract_detail="contract valid",
                    ready=True,
                ),
                strict_runner_run=strict_runner_run,
                strict_backend_visible_path=lambda host_path, backend: str(host_path),
                strict_backend_fixture_expectations=lambda backend: type(
                    "Expectations",
                    (),
                    {
                        "sandbox_root": "/workspace",
                        "home_prefix": str(base / "helper-root"),
                        "cache_prefix": str(base / "helper-root"),
                    },
                )(),
            )

            report = service.run()

        self.assertEqual("passed", report.status)
        self.assertTrue(all(check.passed for check in report.checks))


if __name__ == "__main__":
    unittest.main()
