from __future__ import annotations


class RunnerDispatchService:
    def __init__(
        self,
        session_manager,
        *,
        host_runner,
        projected_runner,
        strict_runner,
        blocked_result_factory,
        blocked_launch_factory,
    ) -> None:
        self.session_manager = session_manager
        self._runners = {
            "host": host_runner,
            "projected": projected_runner,
            "strict": strict_runner,
        }
        self._blocked_result_factory = blocked_result_factory
        self._blocked_launch_factory = blocked_launch_factory

    def validate_mode(self, mode: str) -> str:
        if mode not in self._runners:
            raise ValueError("Mode must be 'host', 'projected', or 'strict'")
        return mode

    def run(self, mode: str, command: str, execution_session_id: str | None = None):
        runner = self._resolve_runner(mode, execution_session_id)
        if isinstance(runner, tuple):
            blocked_mode, reason = runner
            return self._blocked_result_factory(blocked_mode, reason)
        return runner.run(command)

    def run_process(
        self,
        mode: str,
        argv: list[str],
        execution_session_id: str | None = None,
        env: dict[str, str] | None = None,
    ):
        runner = self._resolve_runner(mode, execution_session_id)
        if isinstance(runner, tuple):
            blocked_mode, reason = runner
            return self._blocked_result_factory(blocked_mode, reason)
        return runner.run_process(argv, env=env)

    def start_process(
        self,
        mode: str,
        argv: list[str],
        execution_session_id: str | None = None,
        env: dict[str, str] | None = None,
    ):
        runner = self._resolve_runner(mode, execution_session_id)
        if isinstance(runner, tuple):
            blocked_mode, reason = runner
            return self._blocked_launch_factory(blocked_mode, reason)
        return runner.start_process(argv, env=env)

    def _resolve_runner(self, mode: str, execution_session_id: str | None):
        validated_mode = self.validate_mode(mode)
        if execution_session_id is not None and not self.session_manager.is_current_execution_session(execution_session_id):
            return validated_mode, f"execution session {execution_session_id} is stale"
        return self._runners[validated_mode]
