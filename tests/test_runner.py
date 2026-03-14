from __future__ import annotations

import os
import shutil
import sys
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.windows.platforms import StrictSandboxProbe, get_platform_adapter
from core.policy import PolicyEngine
from backend.projection import ProjectedWorkspaceManager
from backend.runner import RunnerManager, RunnerResult, _build_strict_environment
from backend.session import SessionManager
from backend.strict_backend_validator import StrictBackendValidationResult
from backend.windows.windows_strict_helper_resolution import WINDOWS_STRICT_HELPER_RUST_DEV


LISTING_COMMAND = 'python -c "import os; print(chr(10).join(sorted(os.listdir(\'.\'))))"'
_RUST_DEV_HELPER_READ_BOUNDARY_CACHE: dict[Path, bool] = {}


def _rust_dev_helper_supports_read_boundary(workspace: Path) -> bool:
    workspace = workspace.resolve()
    cached = _RUST_DEV_HELPER_READ_BOUNDARY_CACHE.get(workspace)
    if cached is not None:
        return cached

    repo_root = Path(__file__).resolve().parents[1]
    helper_root = workspace.parent / "helper-capability"
    command = [
        "cargo",
        "run",
        "--quiet",
        "--manifest-path",
        str((repo_root / "rust" / "Cargo.toml").resolve()),
        "-p",
        "ai-ide-windows-helper",
        "--",
        "--workspace",
        str(workspace),
        "--cwd",
        "/workspace",
        "--setenv",
        "AI_IDE_SANDBOX_ROOT",
        "/workspace",
        "--setenv",
        "HOME",
        str((helper_root / "home").resolve()),
        "--setenv",
        "XDG_CACHE_HOME",
        str((helper_root / "cache").resolve()),
        "--setenv",
        "TMPDIR",
        str((helper_root / "tmp").resolve()),
        "--command",
        "echo __AI_IDE_FIXTURE__ .ai_ide_strict_fixture.txt",
    ]
    output = subprocess.run(command, capture_output=True, text=True, check=False)
    supported = (
        "__AI_IDE_FIXTURE__ restricted_token=enabled" in output.stdout
        and "__AI_IDE_FIXTURE__ read_boundary=enabled" in output.stdout
    )
    _RUST_DEV_HELPER_READ_BOUNDARY_CACHE[workspace] = supported
    return supported


class RunnerManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.root = self.base / "project"
        self.runtime_root = self.base / "runtime"
        self.root.mkdir()
        (self.root / "safe").mkdir()
        (self.root / "secrets").mkdir()
        (self.root / "safe" / "todo.txt").write_text("visible", encoding="utf-8")
        (self.root / "secrets" / "token.txt").write_text("hidden", encoding="utf-8")
        self.policy = PolicyEngine(self.root)
        self.policy.add_deny_rule("secrets/**")
        self.sessions = SessionManager(self.policy)
        self.sessions.ensure_fresh_execution_session()
        self.projection = ProjectedWorkspaceManager(self.root, self.policy, self.runtime_root)
        self.runners = RunnerManager(self.root, self.projection, self.sessions, get_platform_adapter("Windows"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_projected_mode_executes_inside_filtered_workspace(self) -> None:
        self.runners.set_mode("projected")

        result = self.runners.run(LISTING_COMMAND)

        self.assertEqual("projected", result.mode)
        self.assertIn("safe", result.stdout)
        self.assertNotIn("secrets", result.stdout)

    def test_projected_process_run_passes_environment_overlay(self) -> None:
        result = self.runners.run_process_in_mode(
            "projected",
            [sys.executable, "-c", "import os; print(os.environ['AI_IDE_AGENT_KIND'])"],
            env={"AI_IDE_AGENT_KIND": "codex"},
        )

        self.assertEqual("projected", result.mode)
        self.assertIn("codex", result.stdout)

    def test_host_mode_shows_current_security_gap(self) -> None:
        self.runners.set_mode("host")

        result = self.runners.run(LISTING_COMMAND)

        self.assertEqual("host", result.mode)
        self.assertIn("safe", result.stdout)
        self.assertIn("secrets", result.stdout)

    def test_projected_mode_blocks_simple_relative_escape_to_host_project(self) -> None:
        self.runners.set_mode("projected")

        result = self.runners.run(
            'python -c "from pathlib import Path; target = Path(\'../../project/secrets/token.txt\'); '
            'print(target.exists()); print(target.read_text() if target.exists() else \'missing\')"'
        )

        self.assertIn("False", result.stdout)
        self.assertIn("missing", result.stdout)

    def test_projected_mode_blocks_obvious_absolute_host_path_reference(self) -> None:
        self.runners.set_mode("projected")
        secret_path = str((self.root / "secrets" / "token.txt").resolve()).replace("\\", "\\\\")

        result = self.runners.run(
            'python -c "from pathlib import Path; target = Path(r\''
            + secret_path
            + '\'); print(target.exists()); print(target.read_text() if target.exists() else \'missing\')"'
        )

        self.assertEqual(126, result.returncode)
        self.assertIn("blocked", result.stderr)

    def test_projected_mode_blocks_computed_absolute_host_path_escape(self) -> None:
        self.runners.set_mode("projected")
        secret_path = str((self.root / "secrets" / "token.txt").resolve())
        path_codes = ",".join(str(ord(char)) for char in secret_path)

        result = self.runners.run(
            'python -c "from pathlib import Path; '
            f'codes=[{path_codes}]; '
            'target = Path(\'\'.join(chr(code) for code in codes)); '
            'print(target.exists()); print(target.read_text() if target.exists() else \'missing\')"'
        )

        self.assertEqual(126, result.returncode)
        self.assertIn("blocked", result.stderr)

    def test_strict_mode_blocks_obvious_network_commands(self) -> None:
        self.runners.set_mode("strict")

        result = self.runners.run("curl https://example.com")

        self.assertEqual(126, result.returncode)
        self.assertIn("network-capable", result.stderr)

    def test_strict_mode_blocks_python_interpreter_in_preview_fallback(self) -> None:
        self.runners.set_mode("strict")

        with patch.object(self.runners.platform_adapter, "strict_backend_invocation", return_value=None):
            result = self.runners.run('python -c "print(1)"')

        self.assertEqual(126, result.returncode)
        self.assertIn("interpreter", result.stderr)

    def test_build_strict_environment_scrubs_secret_environment_variables(self) -> None:

        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "VISIBLE_FLAG": "1"}, clear=False):
            env = _build_strict_environment()

        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("VISIBLE_FLAG", env)
        self.assertEqual("1", env["AI_IDE_STRICT_PREVIEW"])

    def test_strict_mode_passes_blocked_read_roots_to_helper_backend(self) -> None:
        self.runners.set_mode("strict")
        invocation = type(
            "Invocation",
            (),
            {
                "backend": "restricted-host-helper",
                "command": [sys.executable, "-c", "print('backend-ok')"],
                "host_working_directory": self.projection.projection_root(self.sessions.current_session_id),
                "working_directory": "/workspace",
            },
        )()

        with patch.object(
            self.runners.platform_adapter,
            "strict_backend_invocation",
            return_value=invocation,
        ) as strict_backend_invocation:
            with patch.object(
                self.runners.strict_runner.strict_service.strict_backend_validator,
                "validate",
                return_value=StrictBackendValidationResult(True),
            ):
                with patch("subprocess.run", return_value=subprocess.CompletedProcess(invocation.command, 0, "backend-ok\n", "")):
                    result = self.runners.run("echo backend-ok")

        self.assertEqual("restricted-host-helper", result.backend)
        _, kwargs = strict_backend_invocation.call_args
        env = kwargs["env"]
        blocked_roots = set(filter(None, env["AI_IDE_BLOCKED_READ_ROOTS"].split(os.pathsep)))
        self.assertIn(str(self.root.resolve()), blocked_roots)
        self.assertIn(str((self.root / ".ai-ide").resolve()), blocked_roots)
        self.assertIn(str((self.root / ".ai_ide_runtime").resolve()), blocked_roots)
        self.assertIn(str((self.projection.internal_runtime_dir / "processes").resolve()), blocked_roots)
        self.assertIn(str((self.projection.internal_runtime_dir / "controls").resolve()), blocked_roots)

    def test_strict_mode_uses_backend_when_platform_invocation_is_available(self) -> None:
        self.runners.set_mode("strict")
        completed = subprocess.CompletedProcess(
            args=["wsl.exe", "-e", "sh"],
            returncode=0,
            stdout="backend-ok\n",
            stderr="",
        )

        with patch.object(
            self.runners.platform_adapter,
            "strict_backend_invocation",
            return_value=type(
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
                            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
                            "sh -lc 'printf backend-ok'"
                        ),
                    ],
                    "host_working_directory": self.projection.projection_root(self.sessions.current_session_id),
                    "working_directory": "/mnt/c/projection",
                },
            )(),
        ):
            with patch.object(
                self.runners.strict_runner.strict_service.strict_backend_validator,
                "validate",
                return_value=StrictBackendValidationResult(True),
            ):
                with patch("subprocess.run", return_value=completed):
                    result = self.runners.run("printf backend-ok")

        self.assertEqual("strict", result.mode)
        self.assertEqual("wsl", result.backend)
        self.assertEqual(0, result.returncode)
        self.assertEqual("/mnt/c/projection", result.working_directory)

    def test_runner_blocks_stale_execution_session_id_at_boundary(self) -> None:
        stale_session_id = self.sessions.current_session_id
        self.policy.add_deny_rule("other/**")
        self.sessions.ensure_fresh_execution_session()

        result = self.runners.run_in_mode(
            "projected",
            LISTING_COMMAND,
            execution_session_id=stale_session_id,
        )

        self.assertEqual(126, result.returncode)
        self.assertIn("stale", result.stderr)

    def test_strict_mode_falls_back_to_preview_when_backend_launch_fails(self) -> None:
        self.runners.set_mode("strict")
        projection_root = self.projection.projection_root(self.sessions.current_session_id)

        with patch.object(
            self.runners.platform_adapter,
            "strict_backend_invocation",
            return_value=type(
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
                            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
                            "sh -lc 'printf backend-ok'"
                        ),
                    ],
                    "host_working_directory": projection_root,
                    "working_directory": "/mnt/c/projection",
                },
            )(),
        ):
            with patch.object(
                self.runners.strict_runner.strict_service.strict_backend_validator,
                "validate",
                return_value=StrictBackendValidationResult(True),
            ):
                with patch(
                    "backend.runner._run_subprocess",
                    side_effect=[
                        OSError("wsl.exe unavailable"),
                        RunnerResult(
                            mode="strict",
                            backend="strict-preview",
                            returncode=0,
                            stdout="fallback-ok\n",
                            stderr="",
                            working_directory=str(projection_root),
                        ),
                    ],
                ):
                    result = self.runners.run("printf backend-ok")

        self.assertEqual("strict-preview", result.backend)
        self.assertIn("fallback-ok", result.stdout)
        self.assertIn("backend launch failed (wsl)", result.stderr)

    def test_runner_status_marks_wsl_backend_invalid_until_filesystem_isolation_exists(self) -> None:
        projection_root = self.projection.projection_root(self.sessions.current_session_id)

        with patch.object(
            self.runners.platform_adapter,
            "strict_probe",
            return_value=StrictSandboxProbe("windows", True, "wsl", "ready", "ready"),
        ):
            with patch.object(
                self.runners.platform_adapter,
                "strict_backend_invocation",
                return_value=type(
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
                                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
                                "sh -lc 'printf backend-ok'"
                            ),
                        ],
                        "host_working_directory": projection_root,
                        "working_directory": "/mnt/c/projection",
                    },
                )(),
            ):
                payload = self.runners.backend_status()

        self.assertFalse(payload["strict_backend_ready"])
        self.assertEqual("invalid_contract", payload["strict_backend_status"])
        self.assertIn("host filesystem mounts", payload["strict_backend_detail"])

    def test_strict_mode_can_launch_configured_windows_helper_stub(self) -> None:
        self.runners.set_mode("strict")
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        helper_command = f"{sys.executable} {helper_script}"

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
            result = self.runners.run('python -c "print(\'helper-ok\')"')

        self.assertEqual("strict", result.mode)
        self.assertEqual("restricted-host-helper", result.backend)
        self.assertEqual(0, result.returncode)
        self.assertIn("helper-ok", result.stdout)

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_strict_mode_can_launch_rust_dev_helper(self) -> None:
        self.runners.set_mode("strict")

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
            result = self.runners.run("echo helper-rust-ok")

        self.assertEqual("strict", result.mode)
        self.assertEqual("restricted-host-helper", result.backend)
        self.assertEqual(0, result.returncode)
        self.assertIn("helper-rust-ok", result.stdout)

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_strict_mode_run_process_can_use_rust_dev_helper(self) -> None:
        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
            result = self.runners.run_process_in_mode(
                "strict",
                [sys.executable, "-c", "print('helper-rust-run-process')"],
                env={"AI_IDE_AGENT_KIND": "codex"},
            )

        self.assertEqual("strict", result.mode)
        self.assertEqual("restricted-host-helper", result.backend)
        self.assertEqual(0, result.returncode)
        self.assertIn("helper-rust-run-process", result.stdout)

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_strict_mode_rust_dev_helper_blocks_host_internal_runtime_reads(self) -> None:
        if not _rust_dev_helper_supports_read_boundary(self.root):
            self.skipTest("rust-dev helper read boundary unavailable on this host")
        blocked_root = self.projection.internal_runtime_dir / "controls"
        blocked_root.mkdir(parents=True, exist_ok=True)
        blocked_file = blocked_root / "host-secret.txt"
        blocked_file.write_text("hidden", encoding="utf-8")
        blocked_codes = ",".join(str(ord(ch)) for ch in str(blocked_file.resolve()))
        probe_code = (
            "from pathlib import Path\n"
            f"target = Path(''.join(chr(code) for code in [{blocked_codes}]))\n"
            "try:\n"
            "    print(target.read_text())\n"
            "except PermissionError:\n"
            "    print('blocked=denied')\n"
            "except OSError as error:\n"
            "    print(f\"blocked=oserror:{getattr(error, 'winerror', None)}\")\n"
        )

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
            result = self.runners.run_process_in_mode(
                "strict",
                [sys.executable, "-c", probe_code],
            )

        self.assertEqual("restricted-host-helper", result.backend)
        self.assertEqual(0, result.returncode)
        self.assertTrue(
            "blocked=denied" in result.stdout or "blocked=oserror:5" in result.stdout,
            result.stdout,
        )

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_strict_mode_run_process_blocks_host_project_root_reads_with_rust_dev_helper(self) -> None:
        if not _rust_dev_helper_supports_read_boundary(self.root):
            self.skipTest("rust-dev helper read boundary unavailable on this host")
        host_secret = self.root / "secrets" / "token.txt"
        script = (
            "import os\n"
            "from pathlib import Path\n"
            "safe = Path('safe/todo.txt').read_text(encoding='utf-8').strip()\n"
            "target = Path(os.environ['HOST_SECRET_PATH'])\n"
            "try:\n"
            "    target.read_text(encoding='utf-8')\n"
            "    print('host=allowed')\n"
            "except PermissionError:\n"
            "    print('host=denied')\n"
            "except OSError as exc:\n"
            "    print(f'host=oserror:{getattr(exc, \"winerror\", exc.errno)}')\n"
            "print(f'safe={safe}')\n"
        )

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
            result = self.runners.run_process_in_mode(
                "strict",
                [sys.executable, "-c", script],
                env={"HOST_SECRET_PATH": str(host_secret.resolve())},
            )

        self.assertEqual("strict", result.mode)
        self.assertEqual("restricted-host-helper", result.backend)
        self.assertEqual(0, result.returncode)
        self.assertIn("host=denied", result.stdout)
        self.assertIn("safe=visible", result.stdout)

    def test_runner_validate_passes_with_configured_windows_helper_stub(self) -> None:
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        helper_command = f"{sys.executable} {helper_script}"

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
            report = self.runners.validate_strict_backend()

        self.assertEqual("passed", report.status)
        self.assertEqual("restricted-host-helper", report.backend)
        self.assertTrue(report.ready)
        self.assertTrue(all(check.passed for check in report.checks))

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_runner_validate_passes_with_rust_dev_helper(self) -> None:
        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
            report = self.runners.validate_strict_backend()

        self.assertEqual("passed", report.status, report.to_json())
        self.assertEqual("restricted-host-helper", report.backend)
        self.assertTrue(report.ready)
        self.assertTrue(all(check.passed for check in report.checks))

    def test_strict_mode_start_process_can_stream_through_configured_windows_helper_stub(self) -> None:
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        helper_command = f"{sys.executable} {helper_script}"

        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": helper_command}):
            launch = self.runners.start_process_in_mode(
                "strict",
                [
                    sys.executable,
                    "-u",
                    "-c",
                    (
                        "import sys, time; "
                        "print('helper-stream-start', flush=True); "
                        "line=sys.stdin.readline().strip(); "
                        "print(f'helper-stream:{line}', flush=True); "
                        "time.sleep(0.2)"
                    ),
                ],
            )

        self.assertTrue(launch.started)
        assert launch.handle is not None
        handle = launch.handle
        try:
            self.assertEqual("restricted-host-helper", handle.backend)
            assert handle.stdin_file is not None
            handle.stdin_file.write("hello-runner\n")
            handle.stdin_file.flush()
            handle.process.wait(timeout=5)
            handle.stdout_file.close()
            handle.stderr_file.close()
            stdout = handle.stdout_path.read_text(encoding="utf-8")
            stderr = handle.stderr_path.read_text(encoding="utf-8")
        finally:
            if handle.process.poll() is None:
                handle.process.terminate()
                handle.process.wait(timeout=5)
            if not handle.stdout_file.closed:
                handle.stdout_file.close()
            if not handle.stderr_file.closed:
                handle.stderr_file.close()
            if handle.stdin_file is not None and not handle.stdin_file.closed:
                handle.stdin_file.close()

        self.assertIn("helper-stream-start", stdout)
        self.assertIn("helper-stream:hello-runner", stdout)
        self.assertEqual("", stderr)

    @unittest.skipUnless(shutil.which("cargo"), "cargo required for rust-dev helper tests")
    def test_strict_mode_start_process_can_stream_through_rust_dev_helper(self) -> None:
        with patch.dict("os.environ", {"AI_IDE_WINDOWS_STRICT_HELPER": WINDOWS_STRICT_HELPER_RUST_DEV}):
            launch = self.runners.start_process_in_mode(
                "strict",
                [
                    sys.executable,
                    "-u",
                    "-c",
                    (
                        "import sys, time; "
                        "print('helper-rust-stream-start', flush=True); "
                        "line=sys.stdin.readline().strip(); "
                        "print(f'helper-rust-stream:{line}', flush=True); "
                        "time.sleep(0.2)"
                    ),
                ],
            )

        self.assertTrue(launch.started)
        assert launch.handle is not None
        handle = launch.handle
        try:
            self.assertEqual("restricted-host-helper", handle.backend)
            assert handle.stdin_file is not None
            deadline = time.time() + 20
            while time.time() < deadline:
                if handle.stdout_path.exists():
                    stdout_snapshot = handle.stdout_path.read_text(encoding="utf-8")
                    if "helper-rust-stream-start" in stdout_snapshot:
                        break
                time.sleep(0.1)
            handle.stdin_file.write("hello-rust-runner\n")
            handle.stdin_file.flush()
            handle.process.wait(timeout=20)
            handle.stdout_file.close()
            handle.stderr_file.close()
            stdout = handle.stdout_path.read_text(encoding="utf-8")
            stderr = handle.stderr_path.read_text(encoding="utf-8")
        finally:
            if handle.process.poll() is None:
                handle.process.terminate()
                handle.process.wait(timeout=5)
            if not handle.stdout_file.closed:
                handle.stdout_file.close()
            if not handle.stderr_file.closed:
                handle.stderr_file.close()
            if handle.stdin_file is not None and not handle.stdin_file.closed:
                handle.stdin_file.close()

        self.assertIn("helper-rust-stream-start", stdout)
        self.assertIn("helper-rust-stream:hello-rust-runner", stdout)
        self.assertEqual("", stderr)


if __name__ == "__main__":
    unittest.main()
