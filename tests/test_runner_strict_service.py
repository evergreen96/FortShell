from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.runner import RunnerResult
from ai_ide.runner_strict_service import RunnerStrictService
from ai_ide.strict_backend_validator import StrictBackendValidationResult


class _FakeDecision:
    def __init__(self, allowed: bool, reason: str = "") -> None:
        self.allowed = allowed
        self.reason = reason


class _FakeCommandGuard:
    def __init__(self, decisions: dict[str, _FakeDecision]) -> None:
        self.decisions = decisions

    def evaluate(self, mode: str, command: str):
        return self.decisions[mode]


class _FakeProjectionManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.internal_runtime_dir = root / ".runtime"

    def materialize(self, session_id: str):
        return type("Manifest", (), {"root": self.root / session_id})()


class _FakeSessionManager:
    current_session_id = "sess-1234"


class _FakePlatformAdapter:
    def __init__(self, invocation) -> None:
        self.invocation = invocation
        self.last_invocation_kwargs = None

    def strict_backend_invocation(
        self,
        command: str,
        projected_root: Path,
        env=None,
        *,
        process_mode: bool = False,
        argv=None,
        control_file=None,
        response_file=None,
    ):
        self.last_invocation_kwargs = {
            "command": command,
            "projected_root": projected_root,
            "env": env,
            "process_mode": process_mode,
            "argv": argv,
            "control_file": control_file,
            "response_file": response_file,
        }
        return self.invocation


class RunnerStrictServiceTests(unittest.TestCase):
    def test_run_blocks_when_strict_guard_denies_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = RunnerStrictService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                _FakePlatformAdapter(None),
                _FakeCommandGuard(
                    {
                        "strict": _FakeDecision(False, "blocked by strict guard"),
                        "strict-preview": _FakeDecision(True),
                    }
                ),
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
                build_strict_environment=lambda: {"AI_IDE_STRICT_PREVIEW": "1"},
                merge_environment=lambda base, overlay: {**base, **(overlay or {})},
                argv_to_command=lambda argv: " ".join(argv),
            )

            result = service.run("curl https://example.com")

        self.assertEqual(126, result.returncode)
        self.assertIn("blocked by strict guard", result.stderr)

    def test_run_falls_back_to_preview_and_appends_backend_failure_notice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            projected_root = root / "sess-1234"
            invocation = type(
                "Invocation",
                (),
                {
                    "backend": "wsl",
                    "command": [
                        "wsl.exe",
                        "-e",
                        "sh",
                        "-lc",
                        (
                            "mkdir -p /tmp/ai-ide-home /tmp/ai-ide-cache && "
                            "cd /mnt/c/projection && "
                            "exec env -i AI_IDE_STRICT_BACKEND=wsl AI_IDE_SANDBOX_ROOT=/workspace "
                            "HOME=/tmp/ai-ide-home TMPDIR=/tmp XDG_CACHE_HOME=/tmp/ai-ide-cache "
                            "sh -lc 'printf backend-ok'"
                        ),
                    ],
                    "host_working_directory": projected_root,
                    "working_directory": "/mnt/c/projection",
                },
            )()
            calls: list[tuple[object, Path, dict[str, object]]] = []

            def run_subprocess(command, working_directory, **kwargs):
                calls.append((command, working_directory, kwargs))
                if len(calls) == 1:
                    raise OSError("wsl.exe unavailable")
                return RunnerResult(
                    mode="strict",
                    backend="strict-preview",
                    returncode=0,
                    stdout="preview-ok\n",
                    stderr="",
                    working_directory=str(working_directory),
                )

            service = RunnerStrictService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                _FakePlatformAdapter(invocation),
                _FakeCommandGuard(
                    {
                        "strict": _FakeDecision(True),
                        "strict-preview": _FakeDecision(True),
                    }
                ),
                run_subprocess=run_subprocess,
                start_subprocess=lambda *args, **kwargs: None,
                blocked_result_factory=lambda mode, reason: RunnerResult(
                    mode=mode,
                    backend="guard",
                    returncode=126,
                    stdout="",
                    stderr=f"blocked: {reason}",
                    working_directory="",
                ),
                build_strict_environment=lambda: {"AI_IDE_STRICT_PREVIEW": "1"},
                merge_environment=lambda base, overlay: {**base, **(overlay or {})},
                argv_to_command=lambda argv: " ".join(argv),
            )

            with patch.object(
                service.strict_backend_validator,
                "validate",
                return_value=StrictBackendValidationResult(True),
            ):
                result = service.run("printf backend-ok")

        self.assertEqual("strict-preview", result.backend)
        self.assertIn("preview-ok", result.stdout)
        self.assertIn("backend launch failed (wsl)", result.stderr)
        self.assertEqual("printf backend-ok", calls[1][0])

    def test_run_process_uses_preview_environment_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured: dict[str, object] = {}

            def run_subprocess(command, working_directory, **kwargs):
                captured["command"] = command
                captured["working_directory"] = working_directory
                captured["kwargs"] = kwargs
                return RunnerResult(
                    mode="strict",
                    backend="strict-preview",
                    returncode=0,
                    stdout="ok\n",
                    stderr="",
                    working_directory=str(working_directory),
                )

            service = RunnerStrictService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                _FakePlatformAdapter(None),
                _FakeCommandGuard(
                    {
                        "strict": _FakeDecision(True),
                        "strict-preview": _FakeDecision(True),
                    }
                ),
                run_subprocess=run_subprocess,
                start_subprocess=lambda *args, **kwargs: None,
                blocked_result_factory=lambda mode, reason: RunnerResult(
                    mode=mode,
                    backend="guard",
                    returncode=126,
                    stdout="",
                    stderr=f"blocked: {reason}",
                    working_directory="",
                ),
                build_strict_environment=lambda: {"AI_IDE_STRICT_PREVIEW": "1"},
                merge_environment=lambda base, overlay: {**base, **(overlay or {})},
                argv_to_command=lambda argv: " ".join(argv),
            )

            service.run_process(["python", "-V"], env={"AI_IDE_AGENT_KIND": "codex"})

        self.assertEqual(["python", "-V"], captured["command"])
        self.assertEqual("codex", captured["kwargs"]["env"]["AI_IDE_AGENT_KIND"])
        self.assertEqual("1", captured["kwargs"]["env"]["AI_IDE_STRICT_PREVIEW"])
        self.assertFalse(captured["kwargs"]["shell"])

    def test_run_process_passes_argv_to_backend_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            platform_adapter = _FakePlatformAdapter(None)
            service = RunnerStrictService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                platform_adapter,
                _FakeCommandGuard(
                    {
                        "strict": _FakeDecision(True),
                        "strict-preview": _FakeDecision(True),
                    }
                ),
                run_subprocess=lambda *args, **kwargs: RunnerResult(
                    mode="strict",
                    backend="strict-preview",
                    returncode=0,
                    stdout="ok\n",
                    stderr="",
                    working_directory=str(root),
                ),
                start_subprocess=lambda *args, **kwargs: None,
                blocked_result_factory=lambda mode, reason: RunnerResult(
                    mode=mode,
                    backend="guard",
                    returncode=126,
                    stdout="",
                    stderr=f"blocked: {reason}",
                    working_directory="",
                ),
                build_strict_environment=lambda: {"AI_IDE_STRICT_PREVIEW": "1"},
                merge_environment=lambda base, overlay: {**base, **(overlay or {})},
                argv_to_command=lambda argv: " ".join(argv),
            )

            service.run_process(["python", "-c", "print('helper-argv')"], env={"AI_IDE_AGENT_KIND": "codex"})

        assert platform_adapter.last_invocation_kwargs is not None
        self.assertEqual(
            ["python", "-c", "print('helper-argv')"],
            platform_adapter.last_invocation_kwargs["argv"],
        )
        self.assertEqual("codex", platform_adapter.last_invocation_kwargs["env"]["AI_IDE_AGENT_KIND"])
        blocked_read_roots = platform_adapter.last_invocation_kwargs["env"]["AI_IDE_BLOCKED_READ_ROOTS"].split(
            os.pathsep
        )
        self.assertIn(str(root.resolve()), blocked_read_roots)
        self.assertIn(str((root / ".ai-ide").resolve()), blocked_read_roots)
        self.assertIn(str((root / ".ai_ide_runtime").resolve()), blocked_read_roots)
        self.assertIn(str((root / ".runtime" / "processes").resolve()), blocked_read_roots)
        self.assertIn(str((root / ".runtime" / "controls").resolve()), blocked_read_roots)

    def test_run_falls_back_to_preview_when_backend_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invocation = type(
                "Invocation",
                (),
                {
                    "backend": "wsl",
                    "command": ["sh", "-lc", "printf backend-ok"],
                    "host_working_directory": root,
                    "working_directory": "/workspace",
                },
            )()
            calls: list[tuple[object, Path, dict[str, object]]] = []

            def run_subprocess(command, working_directory, **kwargs):
                calls.append((command, working_directory, kwargs))
                return RunnerResult(
                    mode="strict",
                    backend="strict-preview",
                    returncode=0,
                    stdout="preview-ok\n",
                    stderr="",
                    working_directory=str(working_directory),
                )

            service = RunnerStrictService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                _FakePlatformAdapter(invocation),
                _FakeCommandGuard(
                    {
                        "strict": _FakeDecision(True),
                        "strict-preview": _FakeDecision(True),
                    }
                ),
                run_subprocess=run_subprocess,
                start_subprocess=lambda *args, **kwargs: None,
                blocked_result_factory=lambda mode, reason: RunnerResult(
                    mode=mode,
                    backend="guard",
                    returncode=126,
                    stdout="",
                    stderr=f"blocked: {reason}",
                    working_directory="",
                ),
                build_strict_environment=lambda: {"AI_IDE_STRICT_PREVIEW": "1"},
                merge_environment=lambda base, overlay: {**base, **(overlay or {})},
                argv_to_command=lambda argv: " ".join(argv),
            )

            result = service.run("printf backend-ok")

        self.assertEqual("strict-preview", result.backend)
        self.assertEqual("printf backend-ok", calls[0][0])
        self.assertIn("backend validation failed (wsl)", result.stderr)

    def test_start_process_returns_blocked_launch_when_preview_guard_denies_after_backend_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            projected_root = root / "sess-1234"
            invocation = type(
                "Invocation",
                (),
                {
                    "backend": "wsl",
                    "command": [
                        "wsl.exe",
                        "-e",
                        "sh",
                        "-lc",
                        (
                            "mkdir -p /tmp/ai-ide-home /tmp/ai-ide-cache && "
                            "cd /mnt/c/projection && "
                            "exec env -i AI_IDE_STRICT_BACKEND=wsl AI_IDE_SANDBOX_ROOT=/workspace "
                            "HOME=/tmp/ai-ide-home TMPDIR=/tmp XDG_CACHE_HOME=/tmp/ai-ide-cache "
                            "sh -lc 'printf backend-ok'"
                        ),
                    ],
                    "host_working_directory": projected_root,
                    "working_directory": "/mnt/c/projection",
                },
            )()

            service = RunnerStrictService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                _FakePlatformAdapter(invocation),
                _FakeCommandGuard(
                    {
                        "strict": _FakeDecision(True),
                        "strict-preview": _FakeDecision(False, "preview interpreter blocked"),
                    }
                ),
                run_subprocess=lambda *args, **kwargs: None,
                start_subprocess=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("wsl.exe unavailable")),
                blocked_result_factory=lambda mode, reason: RunnerResult(
                    mode=mode,
                    backend="guard",
                    returncode=126,
                    stdout="",
                    stderr=f"blocked: {reason}",
                    working_directory="",
                ),
                build_strict_environment=lambda: {"AI_IDE_STRICT_PREVIEW": "1"},
                merge_environment=lambda base, overlay: {**base, **(overlay or {})},
                argv_to_command=lambda argv: " ".join(argv),
            )

            with patch.object(
                service.strict_backend_validator,
                "validate",
                return_value=StrictBackendValidationResult(True),
            ):
                launch = service.start_process(["python", "-V"])

        self.assertFalse(launch.started)
        self.assertIsNotNone(launch.result)
        assert launch.result is not None
        self.assertIn("preview interpreter blocked", launch.result.stderr)
        self.assertIn("backend launch failed (wsl)", launch.result.stderr)

    def test_start_process_returns_blocked_launch_when_backend_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invocation = type(
                "Invocation",
                (),
                {
                    "backend": "bwrap",
                    "command": ["bwrap", "--clearenv"],
                    "host_working_directory": root,
                    "working_directory": "/workspace",
                },
            )()

            service = RunnerStrictService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                _FakePlatformAdapter(invocation),
                _FakeCommandGuard(
                    {
                        "strict": _FakeDecision(True),
                        "strict-preview": _FakeDecision(False, "preview interpreter blocked"),
                    }
                ),
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
                build_strict_environment=lambda: {"AI_IDE_STRICT_PREVIEW": "1"},
                merge_environment=lambda base, overlay: {**base, **(overlay or {})},
                argv_to_command=lambda argv: " ".join(argv),
            )

            launch = service.start_process(["python", "-V"])

        self.assertFalse(launch.started)
        self.assertIsNotNone(launch.result)
        assert launch.result is not None
        self.assertIn("preview interpreter blocked", launch.result.stderr)
        self.assertIn("backend validation failed (bwrap)", launch.result.stderr)

    def test_start_process_assigns_graceful_stop_policy_for_restricted_host_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            projected_root = root / "sess-1234"
            invocation = type(
                "Invocation",
                (),
                {
                    "backend": "restricted-host-helper",
                    "command": ["helper.exe", "--stdio-proxy", "--argv=python"],
                    "host_working_directory": projected_root,
                    "working_directory": "/workspace",
                },
            )()
            captured: dict[str, object] = {}

            def start_subprocess(command, working_directory, **kwargs):
                captured["command"] = command
                captured["working_directory"] = working_directory
                captured["kwargs"] = kwargs
                return type(
                    "Handle",
                    (),
                    {
                        "backend": kwargs["backend"],
                        "process": type("Proc", (), {"pid": 1234})(),
                    },
                )()

            service = RunnerStrictService(
                _FakeProjectionManager(root),
                _FakeSessionManager(),
                _FakePlatformAdapter(invocation),
                _FakeCommandGuard(
                    {
                        "strict": _FakeDecision(True),
                        "strict-preview": _FakeDecision(True),
                    }
                ),
                run_subprocess=lambda *args, **kwargs: None,
                start_subprocess=start_subprocess,
                blocked_result_factory=lambda mode, reason: RunnerResult(
                    mode=mode,
                    backend="guard",
                    returncode=126,
                    stdout="",
                    stderr=f"blocked: {reason}",
                    working_directory="",
                ),
                build_strict_environment=lambda: {"AI_IDE_STRICT_PREVIEW": "1"},
                merge_environment=lambda base, overlay: {**base, **(overlay or {})},
                argv_to_command=lambda argv: " ".join(argv),
            )

            with patch.object(
                service.strict_backend_validator,
                "validate",
                return_value=StrictBackendValidationResult(True),
            ):
                launch = service.start_process(["python", "-u"])

        self.assertTrue(launch.started)
        stop_policy = captured["kwargs"]["stop_policy"]
        control = captured["kwargs"]["control"]
        self.assertTrue(stop_policy.close_stdin_first)
        self.assertGreater(stop_policy.stdin_close_grace_seconds, 0)
        self.assertEqual("file", control.kind)
        self.assertIsNotNone(control.control_file)
        self.assertIsNotNone(control.response_file)
        self.assertEqual("kill", control.kill_command)
        self.assertEqual("status", control.status_command)


if __name__ == "__main__":
    unittest.main()
