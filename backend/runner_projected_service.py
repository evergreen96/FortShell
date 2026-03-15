from __future__ import annotations


class RunnerProjectedService:
    def __init__(
        self,
        fs_backend,
        session_manager,
        command_guard,
        *,
        run_subprocess,
        start_subprocess,
        blocked_result_factory,
        blocked_launch_factory,
        argv_to_command,
    ) -> None:
        self.fs_backend = fs_backend
        self.session_manager = session_manager
        self.command_guard = command_guard
        self._run_subprocess = run_subprocess
        self._start_subprocess = start_subprocess
        self._blocked_result_factory = blocked_result_factory
        self._blocked_launch_factory = blocked_launch_factory
        self._argv_to_command = argv_to_command

    def _ensure_mount(self):
        sid = self.session_manager.current_session_id
        if self.fs_backend.mount_root is not None:
            return self.fs_backend.mount_root
        return self.fs_backend.mount(sid).mount_root

    def run(self, command: str):
        decision = self.command_guard.evaluate("projected", command)
        if not decision.allowed:
            return self._blocked_result_factory("projected", decision.reason)
        root = self._ensure_mount()
        return self._run_subprocess(command, root, mode="projected", backend="projected")

    def run_process(self, argv: list[str], env: dict[str, str] | None = None):
        command = self._argv_to_command(argv)
        decision = self.command_guard.evaluate("projected", command)
        if not decision.allowed:
            return self._blocked_result_factory("projected", decision.reason)
        root = self._ensure_mount()
        return self._run_subprocess(
            argv,
            root,
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
        root = self._ensure_mount()
        handle = self._start_subprocess(
            argv,
            root,
            mode="projected",
            backend="projected",
            env=env,
            shell=False,
            artifact_root=self.fs_backend.internal_runtime_dir / "processes",
        )
        return self._started_launch(handle)

    def refresh(self):
        sid = self.session_manager.current_session_id
        return self.fs_backend.mount(sid)

    @staticmethod
    def _started_launch(handle):
        from backend.runner_models import RunnerLaunchResult

        return RunnerLaunchResult(started=True, handle=handle)
