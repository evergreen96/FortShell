from __future__ import annotations

import unittest

from backend.runner_dispatch_service import RunnerDispatchService


class _FakeRunner:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[tuple[str, object, object]] = []

    def run(self, command: str):
        self.calls.append(("run", command, None))
        return f"{self.label}:run:{command}"

    def run_process(self, argv: list[str], env: dict[str, str] | None = None):
        self.calls.append(("run_process", list(argv), env))
        return {"runner": self.label, "argv": list(argv), "env": env}

    def start_process(self, argv: list[str], env: dict[str, str] | None = None):
        self.calls.append(("start_process", list(argv), env))
        return {"started": True, "runner": self.label, "argv": list(argv), "env": env}


class _FakeSessionManager:
    def __init__(self, current_execution_session_id: str = "sess-current") -> None:
        self.current_execution_session_id = current_execution_session_id

    def is_current_execution_session(self, session_id: str) -> bool:
        return session_id == self.current_execution_session_id


class RunnerDispatchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host_runner = _FakeRunner("host")
        self.projected_runner = _FakeRunner("projected")
        self.strict_runner = _FakeRunner("strict")
        self.service = RunnerDispatchService(
            _FakeSessionManager(),
            host_runner=self.host_runner,
            projected_runner=self.projected_runner,
            strict_runner=self.strict_runner,
            blocked_result_factory=lambda mode, reason: {"mode": mode, "blocked": reason},
            blocked_launch_factory=lambda mode, reason: {
                "started": False,
                "mode": mode,
                "blocked": reason,
            },
        )

    def test_validate_mode_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            self.service.validate_mode("mystery")

    def test_run_dispatches_to_selected_runner(self) -> None:
        result = self.service.run("projected", "echo hello")

        self.assertEqual("projected:run:echo hello", result)
        self.assertEqual([("run", "echo hello", None)], self.projected_runner.calls)

    def test_run_process_dispatches_env_to_selected_runner(self) -> None:
        result = self.service.run_process(
            "strict",
            ["python", "-V"],
            env={"AI_IDE_AGENT_KIND": "codex"},
        )

        self.assertEqual("strict", result["runner"])
        self.assertEqual({"AI_IDE_AGENT_KIND": "codex"}, result["env"])
        self.assertEqual([("run_process", ["python", "-V"], {"AI_IDE_AGENT_KIND": "codex"})], self.strict_runner.calls)

    def test_stale_execution_session_returns_blocked_result(self) -> None:
        result = self.service.run("projected", "echo hello", execution_session_id="sess-stale")

        self.assertEqual("projected", result["mode"])
        self.assertIn("stale", result["blocked"])
        self.assertEqual([], self.projected_runner.calls)

    def test_stale_execution_session_returns_blocked_launch_result(self) -> None:
        result = self.service.start_process("strict", ["python", "-V"], execution_session_id="sess-stale")

        self.assertFalse(result["started"])
        self.assertEqual("strict", result["mode"])
        self.assertIn("stale", result["blocked"])
        self.assertEqual([], self.strict_runner.calls)


if __name__ == "__main__":
    unittest.main()
