from __future__ import annotations


class RunnerProjectedService:
    def __init__(
        self,
        projection_manager,
        session_manager,
        command_guard,
        *,
        run_subprocess,
        start_subprocess,
        blocked_result_factory,
        blocked_launch_factory,
        argv_to_command,
    ) -> None:
        self.projection_manager = projection_manager
        self.session_manager = session_manager
        self.command_guard = command_guard
        self._run_subprocess = run_subprocess
        self._start_subprocess = start_subprocess
        self._blocked_result_factory = blocked_result_factory
        self._blocked_launch_factory = blocked_launch_factory
        self._argv_to_command = argv_to_command

    def run(self, command: str):
        decision = self.command_guard.evaluate("projected", command)
        if not decision.allowed:
            return self._blocked_result_factory("projected", decision.reason)
        manifest = self.projection_manager.materialize(self.session_manager.current_session_id)
        return self._run_subprocess(command, manifest.root, mode="projected", backend="projected")

    def run_process(self, argv: list[str], env: dict[str, str] | None = None):
        command = self._argv_to_command(argv)
        decision = self.command_guard.evaluate("projected", command)
        if not decision.allowed:
            return self._blocked_result_factory("projected", decision.reason)
        manifest = self.projection_manager.materialize(self.session_manager.current_session_id)
        return self._run_subprocess(
            argv,
            manifest.root,
            mode="projected",
            backend="projected",
            env=env,
            shell=False,
        )

    def start_process(self, argv: list[str], env: dict[str, str] | None = None):
        command = self._argv_to_command(argv)
        decision = self.command_guard.evaluate("projected", command)
        if not decision.allowed:
            return self._blocked_launch_factory("projected", decision.reason)
        manifest = self.projection_manager.materialize(self.session_manager.current_session_id)
        handle = self._start_subprocess(
            argv,
            manifest.root,
            mode="projected",
            backend="projected",
            env=env,
            shell=False,
            artifact_root=self.projection_manager.internal_runtime_dir / "processes",
        )
        return self._started_launch(handle)

    def refresh(self):
        return self.projection_manager.materialize(self.session_manager.current_session_id)

    @staticmethod
    def _started_launch(handle):
        from backend.runner_models import RunnerLaunchResult

        return RunnerLaunchResult(started=True, handle=handle)
