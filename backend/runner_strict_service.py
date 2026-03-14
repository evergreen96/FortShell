from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import uuid

from backend.windows.platforms import DEFAULT_STRICT_BOUNDARY_SCOPE
from backend.runner_models import RunnerProcessControl, RunnerProcessStopPolicy
from backend.strict_backend_validator import StrictBackendValidator


class RunnerStrictService:
    def __init__(
        self,
        projection_manager,
        session_manager,
        platform_adapter,
        command_guard,
        *,
        run_subprocess,
        start_subprocess,
        blocked_result_factory,
        build_strict_environment,
        merge_environment,
        argv_to_command,
        strict_backend_validator=None,
    ) -> None:
        self.projection_manager = projection_manager
        self.session_manager = session_manager
        self.platform_adapter = platform_adapter
        self.command_guard = command_guard
        self._run_subprocess = run_subprocess
        self._start_subprocess = start_subprocess
        self._blocked_result_factory = blocked_result_factory
        self._build_strict_environment = build_strict_environment
        self._merge_environment = merge_environment
        self._argv_to_command = argv_to_command
        self.strict_backend_validator = strict_backend_validator or StrictBackendValidator()

    def boundary_scope(self) -> str:
        return DEFAULT_STRICT_BOUNDARY_SCOPE

    def run(self, command: str):
        decision = self.command_guard.evaluate("strict", command)
        if not decision.allowed:
            return self._blocked_result_factory("strict", decision.reason)

        manifest = self.projection_manager.materialize(self.session_manager.current_session_id)
        strict_env = self._strict_backend_environment(manifest.root)
        invocation = self.platform_adapter.strict_backend_invocation(
            command,
            manifest.root,
            env=strict_env,
        )
        launch_failure_notice = ""
        if invocation is not None:
            validation = self.strict_backend_validator.validate(invocation, projected_root=manifest.root)
            if not validation.valid:
                launch_failure_notice = (
                    f"backend validation failed ({invocation.backend}): {validation.reason}"
                )
            else:
                try:
                    return self._run_subprocess(
                        invocation.command,
                        invocation.host_working_directory,
                        mode="strict",
                        backend=invocation.backend,
                        reported_working_directory=invocation.working_directory,
                        shell=False,
                    )
                except OSError as exc:
                    launch_failure_notice = f"backend launch failed ({invocation.backend}): {exc}"

        preview_decision = self.command_guard.evaluate("strict-preview", command)
        if not preview_decision.allowed:
            blocked = self._blocked_result_factory("strict", preview_decision.reason)
            return self._with_launch_failure_notice(blocked, launch_failure_notice)

        result = self._run_subprocess(
            command,
            manifest.root,
            mode="strict",
            backend="strict-preview",
            env=self._build_strict_environment(),
        )
        return self._with_launch_failure_notice(result, launch_failure_notice)

    def run_process(self, argv: list[str], env: dict[str, str] | None = None):
        command = self._argv_to_command(argv)
        decision = self.command_guard.evaluate("strict", command)
        if not decision.allowed:
            return self._blocked_result_factory("strict", decision.reason)

        manifest = self.projection_manager.materialize(self.session_manager.current_session_id)
        strict_env = self._strict_backend_environment(manifest.root, env)
        invocation = self.platform_adapter.strict_backend_invocation(
            command,
            manifest.root,
            env=strict_env,
            argv=argv,
        )
        launch_failure_notice = ""
        if invocation is not None:
            validation = self.strict_backend_validator.validate(invocation, projected_root=manifest.root)
            if not validation.valid:
                launch_failure_notice = (
                    f"backend validation failed ({invocation.backend}): {validation.reason}"
                )
            else:
                try:
                    return self._run_subprocess(
                        invocation.command,
                        invocation.host_working_directory,
                        mode="strict",
                        backend=invocation.backend,
                        reported_working_directory=invocation.working_directory,
                        shell=False,
                    )
                except OSError as exc:
                    launch_failure_notice = f"backend launch failed ({invocation.backend}): {exc}"

        preview_decision = self.command_guard.evaluate("strict-preview", command)
        if not preview_decision.allowed:
            blocked = self._blocked_result_factory("strict", preview_decision.reason)
            return self._with_launch_failure_notice(blocked, launch_failure_notice)

        result = self._run_subprocess(
            argv,
            manifest.root,
            mode="strict",
            backend="strict-preview",
            env=self._merge_environment(self._build_strict_environment(), env),
            shell=False,
        )
        return self._with_launch_failure_notice(result, launch_failure_notice)

    def start_process(self, argv: list[str], env: dict[str, str] | None = None):
        command = self._argv_to_command(argv)
        decision = self.command_guard.evaluate("strict", command)
        if not decision.allowed:
            return self._blocked_launch_result("strict", decision.reason)

        manifest = self.projection_manager.materialize(self.session_manager.current_session_id)
        artifact_root = self.projection_manager.internal_runtime_dir / "processes"
        control = self._control_for_backend("restricted-host-helper", artifact_root)
        strict_env = self._strict_backend_environment(manifest.root, env)
        invocation = self.platform_adapter.strict_backend_invocation(
            command,
            manifest.root,
            env=strict_env,
            process_mode=True,
            argv=argv,
            control_file=control.control_file if control.kind != "none" else None,
            response_file=control.response_file if control.kind != "none" else None,
        )
        launch_failure_notice = ""
        if invocation is not None:
            validation = self.strict_backend_validator.validate(invocation, projected_root=manifest.root)
            if not validation.valid:
                launch_failure_notice = (
                    f"backend validation failed ({invocation.backend}): {validation.reason}"
                )
            else:
                try:
                    handle = self._start_subprocess(
                        invocation.command,
                        invocation.host_working_directory,
                        mode="strict",
                        backend=invocation.backend,
                        reported_working_directory=invocation.working_directory,
                        shell=False,
                        artifact_root=artifact_root,
                        stop_policy=self._stop_policy_for_backend(invocation.backend),
                        control=control if invocation.backend == "restricted-host-helper" else None,
                    )
                    return self._started_launch(handle)
                except OSError as exc:
                    launch_failure_notice = f"backend launch failed ({invocation.backend}): {exc}"

        preview_decision = self.command_guard.evaluate("strict-preview", command)
        if not preview_decision.allowed:
            blocked = self._blocked_launch_result("strict", preview_decision.reason)
            if launch_failure_notice and blocked.result is not None:
                blocked.result = self._with_launch_failure_notice(blocked.result, launch_failure_notice)
            return blocked

        handle = self._start_subprocess(
            argv,
            manifest.root,
            mode="strict",
            backend="strict-preview",
            env=self._merge_environment(self._build_strict_environment(), env),
            shell=False,
            artifact_root=artifact_root,
        )
        if launch_failure_notice:
            handle = replace(handle, backend="strict-preview")
        return self._started_launch(handle)

    @staticmethod
    def _started_launch(handle):
        from backend.runner_models import RunnerLaunchResult

        return RunnerLaunchResult(started=True, handle=handle)

    def _blocked_launch_result(self, mode: str, reason: str):
        from backend.runner_models import RunnerLaunchResult

        return RunnerLaunchResult(started=False, result=self._blocked_result_factory(mode, reason))

    def _strict_backend_environment(
        self, projected_root: Path, env: dict[str, str] | None = None
    ) -> dict[str, str]:
        merged = dict(env or {})
        source_project_root = getattr(
            self.projection_manager,
            "project_root",
            getattr(self.projection_manager, "root", projected_root),
        )
        runtime_root = self.projection_manager.internal_runtime_dir.resolve()
        runtime_controls = (runtime_root / "controls").resolve()
        runtime_processes = (runtime_root / "processes").resolve()
        runtime_controls.mkdir(parents=True, exist_ok=True)
        runtime_processes.mkdir(parents=True, exist_ok=True)
        blocked_read_roots = [
            Path(source_project_root).resolve(),
            (Path(source_project_root) / ".ai-ide").resolve(),
            (Path(source_project_root) / ".ai_ide_runtime").resolve(),
            runtime_controls,
            runtime_processes,
        ]
        if runtime_root.exists():
            blocked_read_roots.extend(
                path.resolve()
                for path in runtime_root.iterdir()
                if path.name != "projections"
            )
        merged["AI_IDE_BLOCKED_READ_ROOTS"] = os.pathsep.join(
            str(root) for root in dict.fromkeys(blocked_read_roots)
        )
        return merged

    @staticmethod
    def _with_launch_failure_notice(result, launch_failure_notice: str):
        if not launch_failure_notice:
            return result
        stderr = f"{launch_failure_notice}\n{result.stderr}".strip()
        return replace(result, stderr=stderr)

    @staticmethod
    def _stop_policy_for_backend(backend: str) -> RunnerProcessStopPolicy | None:
        if backend == "restricted-host-helper":
            return RunnerProcessStopPolicy(
                close_stdin_first=True,
                stdin_close_grace_seconds=0.5,
                terminate_timeout_seconds=5.0,
            )
        return None

    @staticmethod
    def _control_for_backend(backend: str, artifact_root):
        if backend != "restricted-host-helper":
            return RunnerProcessControl()
        control_root = artifact_root / "controls"
        control_root.mkdir(parents=True, exist_ok=True)
        return RunnerProcessControl(
            kind="file",
            control_file=control_root / f"helper-stop-{uuid.uuid4().hex[:8]}.txt",
            response_file=control_root / f"helper-status-{uuid.uuid4().hex[:8]}.json",
            stop_command="stop",
            kill_command="kill",
            status_command="status",
        )
