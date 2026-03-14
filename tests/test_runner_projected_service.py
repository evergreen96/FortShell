from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.runner import RunnerResult
from backend.runner_projected_service import RunnerProjectedService


class _FakeDecision:
    def __init__(self, allowed: bool, reason: str = "") -> None:
        self.allowed = allowed
        self.reason = reason


class _FakeCommandGuard:
    def __init__(self, decision: _FakeDecision) -> None:
        self.decision = decision

    def evaluate(self, mode: str, command: str):
        return self.decision


class _FakeProjectionManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.internal_runtime_dir = root / ".runtime"
        self.materialize_calls: list[str] = []

    def materialize(self, session_id: str):
        self.materialize_calls.append(session_id)
        return type("Manifest", (), {"root": self.root / session_id})()


class _FakeSessionManager:
    current_session_id = "sess-1234"


class RunnerProjectedServiceTests(unittest.TestCase):
    def test_run_blocks_when_guard_denies_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = RunnerProjectedService(
                _FakeProjectionManager(Path(temp_dir)),
                _FakeSessionManager(),
                _FakeCommandGuard(_FakeDecision(False, "blocked by projected guard")),
                run_subprocess=lambda *args, **kwargs: None,
                start_subprocess=lambda *args, **kwargs: None,
                blocked_result_factory=lambda mode, reason: RunnerResult(
                    mode=mode,
                    backend="guard",
                    returncode=126,
                    stdout="",
                    stderr=f"blocked: {reason}",
                    working_directory="",
                ),
                blocked_launch_factory=lambda mode, reason: None,
                argv_to_command=lambda argv: " ".join(argv),
            )

            result = service.run("cat secrets/token.txt")

        self.assertEqual(126, result.returncode)
        self.assertIn("blocked by projected guard", result.stderr)

    def test_run_materializes_projection_and_executes_in_manifest_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            projection_manager = _FakeProjectionManager(root)
            captured: dict[str, object] = {}

            def run_subprocess(command, working_directory, **kwargs):
                captured["command"] = command
                captured["working_directory"] = working_directory
                captured["kwargs"] = kwargs
                return RunnerResult(
                    mode="projected",
                    backend="projected",
                    returncode=0,
                    stdout="ok\n",
                    stderr="",
                    working_directory=str(working_directory),
                )

            service = RunnerProjectedService(
                projection_manager,
                _FakeSessionManager(),
                _FakeCommandGuard(_FakeDecision(True)),
                run_subprocess=run_subprocess,
                start_subprocess=lambda *args, **kwargs: None,
                blocked_result_factory=lambda mode, reason: None,
                blocked_launch_factory=lambda mode, reason: None,
                argv_to_command=lambda argv: " ".join(argv),
            )

            result = service.run("ls")

        self.assertEqual("projected", result.mode)
        self.assertEqual(["sess-1234"], projection_manager.materialize_calls)
        self.assertEqual("ls", captured["command"])
        self.assertEqual(root / "sess-1234", captured["working_directory"])

    def test_run_process_passes_environment_overlay_to_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured: dict[str, object] = {}

            def run_subprocess(command, working_directory, **kwargs):
                captured["command"] = command
                captured["working_directory"] = working_directory
                captured["kwargs"] = kwargs
                return RunnerResult(
                    mode="projected",
                    backend="projected",
                    returncode=0,
                    stdout="ok\n",
                    stderr="",
                    working_directory=str(working_directory),
                )

            service = RunnerProjectedService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                _FakeCommandGuard(_FakeDecision(True)),
                run_subprocess=run_subprocess,
                start_subprocess=lambda *args, **kwargs: None,
                blocked_result_factory=lambda mode, reason: None,
                blocked_launch_factory=lambda mode, reason: None,
                argv_to_command=lambda argv: " ".join(argv),
            )

            service.run_process(["python", "-V"], env={"AI_IDE_AGENT_KIND": "codex"})

        self.assertEqual(["python", "-V"], captured["command"])
        self.assertFalse(captured["kwargs"]["shell"])
        self.assertEqual("codex", captured["kwargs"]["env"]["AI_IDE_AGENT_KIND"])

    def test_start_process_returns_blocked_launch_when_guard_denies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = RunnerProjectedService(
                _FakeProjectionManager(Path(temp_dir)),
                _FakeSessionManager(),
                _FakeCommandGuard(_FakeDecision(False, "blocked by projected guard")),
                run_subprocess=lambda *args, **kwargs: None,
                start_subprocess=lambda *args, **kwargs: None,
                blocked_result_factory=lambda mode, reason: RunnerResult(
                    mode=mode,
                    backend="guard",
                    returncode=126,
                    stdout="",
                    stderr=f"blocked: {reason}",
                    working_directory="",
                ),
                blocked_launch_factory=lambda mode, reason: {
                    "started": False,
                    "mode": mode,
                    "blocked": reason,
                },
                argv_to_command=lambda argv: " ".join(argv),
            )

            launch = service.start_process(["python", "-V"])

        self.assertFalse(launch["started"])
        self.assertEqual("projected", launch["mode"])
        self.assertIn("blocked by projected guard", launch["blocked"])


if __name__ == "__main__":
    unittest.main()
