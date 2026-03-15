from __future__ import annotations

from pathlib import Path

from backend.command_guard import CommandGuard
from backend.windows.platforms import PlatformAdapter
from backend.runner_dispatch_service import RunnerDispatchService
from backend.runner_environment_service import RunnerEnvironmentService
from backend.runner_host_service import RunnerHostService
from backend.runner_models import (
    RunnerLaunchResult,
    RunnerProcessControl,
    RunnerProcessHandle,
    RunnerProcessStopPolicy,
    RunnerResult,
)
from backend.runner_process_service import RunnerProcessService
from backend.runner_projected_service import RunnerProjectedService
from backend.projection import ProjectionManifest
from core.filtered_fs_backend import FilteredFSBackend
from backend.runner_status_service import RunnerStatusService
from backend.strict_backend_fixture_service import StrictBackendFixtureService
from backend.strict_backend_health_service import StrictBackendHealthService
from backend.strict_backend_validation_cache import StrictBackendValidationCache
from backend.runner_strict_service import RunnerStrictService
from backend.session import SessionManager
from backend.workspace_index_snapshot_builder import WorkspaceIndexSnapshotBuilder

_RUNNER_PROCESS_SERVICE = RunnerProcessService()
_RUNNER_ENVIRONMENT_SERVICE = RunnerEnvironmentService()


class HostRunner:
    def __init__(self, project_root: Path) -> None:
        self.host_service = RunnerHostService(
            project_root,
            run_subprocess=lambda *args, **kwargs: _run_subprocess(*args, **kwargs),
            start_subprocess=lambda *args, **kwargs: _start_subprocess(*args, **kwargs),
        )

    def run(self, command: str) -> RunnerResult:
        return self.host_service.run(command)

    def run_process(self, argv: list[str], env: dict[str, str] | None = None) -> RunnerResult:
        return self.host_service.run_process(argv, env=env)

    def start_process(self, argv: list[str], env: dict[str, str] | None = None) -> RunnerLaunchResult:
        return self.host_service.start_process(argv, env=env)


class ProjectedRunner:
    def __init__(
        self,
        projection_manager: FilteredFSBackend,
        session_manager: SessionManager,
        platform_adapter: PlatformAdapter,
        command_guard: CommandGuard,
    ) -> None:
        self.projected_service = RunnerProjectedService(
            projection_manager,
            session_manager,
            command_guard,
            run_subprocess=lambda *args, **kwargs: _run_subprocess(*args, **kwargs),
            start_subprocess=lambda *args, **kwargs: _start_subprocess(*args, **kwargs),
            blocked_result_factory=lambda mode, reason: _blocked_result(mode, reason),
            blocked_launch_factory=lambda mode, reason: RunnerLaunchResult(
                started=False,
                result=_blocked_result(mode, reason),
            ),
            argv_to_command=lambda argv: _RUNNER_ENVIRONMENT_SERVICE.argv_to_command(argv),
        )

    def run(self, command: str) -> RunnerResult:
        return self.projected_service.run(command)

    def run_process(self, argv: list[str], env: dict[str, str] | None = None) -> RunnerResult:
        return self.projected_service.run_process(argv, env=env)

    def start_process(self, argv: list[str], env: dict[str, str] | None = None) -> RunnerLaunchResult:
        return self.projected_service.start_process(argv, env=env)

    def refresh(self) -> ProjectionManifest:
        return self.projected_service.refresh()


class StrictRunner:
    def __init__(
        self,
        projection_manager: FilteredFSBackend,
        session_manager: SessionManager,
        platform_adapter: PlatformAdapter,
        command_guard: CommandGuard,
    ) -> None:
        self.strict_service = RunnerStrictService(
            projection_manager,
            session_manager,
            platform_adapter,
            command_guard,
            run_subprocess=lambda *args, **kwargs: _run_subprocess(*args, **kwargs),
            start_subprocess=lambda *args, **kwargs: _start_subprocess(*args, **kwargs),
            blocked_result_factory=lambda mode, reason: _blocked_result(mode, reason),
            build_strict_environment=lambda: _RUNNER_ENVIRONMENT_SERVICE.build_strict_environment(),
            merge_environment=lambda base, overlay: _RUNNER_ENVIRONMENT_SERVICE.merge_environment(base, overlay),
            argv_to_command=lambda argv: _RUNNER_ENVIRONMENT_SERVICE.argv_to_command(argv),
        )

    def run(self, command: str) -> RunnerResult:
        return self.strict_service.run(command)

    def run_process(self, argv: list[str], env: dict[str, str] | None = None) -> RunnerResult:
        return self.strict_service.run_process(argv, env=env)

    def start_process(self, argv: list[str], env: dict[str, str] | None = None) -> RunnerLaunchResult:
        return self.strict_service.start_process(argv, env=env)


class RunnerManager:
    def __init__(
        self,
        project_root: Path,
        projection_manager: FilteredFSBackend,
        session_manager: SessionManager,
        platform_adapter: PlatformAdapter,
        *,
        workspace_signature_provider=None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.projection_manager = projection_manager
        self.session_manager = session_manager
        self.platform_adapter = platform_adapter
        self.mode = "projected"
        self.command_guard = CommandGuard(self.project_root)
        self.host_runner = HostRunner(self.project_root)
        self.projected_runner = ProjectedRunner(
            projection_manager=self.projection_manager,
            session_manager=self.session_manager,
            platform_adapter=self.platform_adapter,
            command_guard=self.command_guard,
        )
        self.strict_runner = StrictRunner(
            projection_manager=self.projection_manager,
            session_manager=self.session_manager,
            platform_adapter=self.platform_adapter,
            command_guard=self.command_guard,
        )
        self.strict_backend_health = StrictBackendHealthService(
            self.platform_adapter,
            self.projection_manager,
            self.session_manager,
            self.strict_runner.strict_service.strict_backend_validator,
        )
        self.workspace_index_builder = WorkspaceIndexSnapshotBuilder(
            self.project_root,
            self.projection_manager.workspace_access,
        )
        self.workspace_signature_provider = (
            workspace_signature_provider or self.workspace_index_builder.build_signature
        )
        self.strict_backend_validation = StrictBackendValidationCache()
        self.status_service = RunnerStatusService(
            self.platform_adapter,
            strict_backend_health_provider=self.strict_backend_health.health,
            strict_backend_validation_provider=self.strict_backend_validation.snapshot,
            strict_boundary_scope_provider=self.strict_runner.strict_service.boundary_scope,
            execution_session_id_provider=lambda: self.session_manager.current_session_id,
            workspace_signature_provider=self.workspace_signature_provider,
        )
        self.strict_backend_fixture = StrictBackendFixtureService(
            project_root=self.project_root,
            projection_manager=self.projection_manager,
            session_manager=self.session_manager,
            strict_backend_health_provider=self.strict_backend_health.health,
            strict_runner_run=self.strict_runner.run,
            strict_backend_visible_path=self.platform_adapter.strict_backend_visible_path,
            strict_backend_fixture_expectations=self.platform_adapter.strict_backend_fixture_expectations,
        )
        self.dispatch_service = RunnerDispatchService(
            self.session_manager,
            host_runner=self.host_runner,
            projected_runner=self.projected_runner,
            strict_runner=self.strict_runner,
            blocked_result_factory=_blocked_result,
            blocked_launch_factory=lambda mode, reason: RunnerLaunchResult(
                started=False,
                result=_blocked_result(mode, reason),
            ),
        )

    def set_mode(self, mode: str) -> str:
        self.mode = self.dispatch_service.validate_mode(mode)
        return self.mode

    def status(self) -> str:
        return self.status_service.status_text(self.mode)

    def probe(self) -> str:
        return self.status_service.probe_text()

    def backend_status(self) -> dict[str, str | bool]:
        return self.status_service.status_payload(self.mode)

    def backend_status_json(self) -> str:
        return self.status_service.status_json(self.mode)

    def validate_strict_backend(self):
        report = self.strict_backend_fixture.run()
        self.strict_backend_validation.record(
            session_id=self.session_manager.current_session_id,
            backend=report.backend,
            ready=report.ready,
            status=report.status,
            reason=report.reason,
            workspace_signature=self.workspace_signature_provider(),
            restricted_token_status=report.restricted_token_status,
            write_boundary_status=report.write_boundary_status,
            read_boundary_status=report.read_boundary_status,
        )
        return report

    def refresh_projection(self) -> ProjectionManifest:
        return self.projected_runner.refresh()

    def run(self, command: str) -> RunnerResult:
        return self.run_in_mode(self.mode, command)

    def run_process(self, argv: list[str], env: dict[str, str] | None = None) -> RunnerResult:
        return self.run_process_in_mode(self.mode, argv, env=env)

    def start_process(self, argv: list[str], env: dict[str, str] | None = None) -> RunnerLaunchResult:
        return self.start_process_in_mode(self.mode, argv, env=env)

    def run_in_mode(
        self,
        mode: str,
        command: str,
        execution_session_id: str | None = None,
    ) -> RunnerResult:
        return self.dispatch_service.run(mode, command, execution_session_id=execution_session_id)

    def run_process_in_mode(
        self,
        mode: str,
        argv: list[str],
        execution_session_id: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RunnerResult:
        return self.dispatch_service.run_process(
            mode,
            argv,
            execution_session_id=execution_session_id,
            env=env,
        )

    def start_process_in_mode(
        self,
        mode: str,
        argv: list[str],
        execution_session_id: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RunnerLaunchResult:
        return self.dispatch_service.start_process(
            mode,
            argv,
            execution_session_id=execution_session_id,
            env=env,
        )


def _run_subprocess(
    command: str | list[str],
    working_directory: Path,
    mode: str,
    backend: str,
    env: dict[str, str] | None = None,
    reported_working_directory: str | None = None,
    shell: bool = True,
) -> RunnerResult:
    return _RUNNER_PROCESS_SERVICE.run_subprocess(
        command,
        working_directory,
        mode=mode,
        backend=backend,
        env=env,
        reported_working_directory=reported_working_directory,
        shell=shell,
    )


def _start_subprocess(
    command: str | list[str],
    working_directory: Path,
    mode: str,
    backend: str,
    env: dict[str, str] | None = None,
    reported_working_directory: str | None = None,
    shell: bool = True,
    artifact_root: Path | None = None,
    stop_policy: RunnerProcessStopPolicy | None = None,
    control: RunnerProcessControl | None = None,
) -> RunnerProcessHandle:
    return _RUNNER_PROCESS_SERVICE.start_subprocess(
        command,
        working_directory,
        mode=mode,
        backend=backend,
        env=env,
        reported_working_directory=reported_working_directory,
        shell=shell,
        artifact_root=artifact_root,
        stop_policy=stop_policy,
        control=control,
    )


def _blocked_result(mode: str, reason: str) -> RunnerResult:
    return RunnerResult(
        mode=mode,
        backend="guard",
        returncode=126,
        stdout="",
        stderr=f"blocked: {reason}",
        working_directory="",
    )


def _build_strict_environment() -> dict[str, str]:
    return _RUNNER_ENVIRONMENT_SERVICE.build_strict_environment()


def _argv_to_command(argv: list[str]) -> str:
    return _RUNNER_ENVIRONMENT_SERVICE.argv_to_command(argv)


def _merge_environment(base: dict[str, str], overlay: dict[str, str] | None) -> dict[str, str]:
    return _RUNNER_ENVIRONMENT_SERVICE.merge_environment(base, overlay)
