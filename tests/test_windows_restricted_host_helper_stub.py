from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from threading import Thread
import time
from unittest.mock import patch

from backend.windows.windows_restricted_host_helper_stub import main
from backend.windows.windows_strict_helper_protocol import (
    STDIO_PROXY_FLAG,
    WindowsStrictHelperControlMessage,
    read_helper_status_message,
    write_helper_control_message,
)


class WindowsRestrictedHostHelperStubTests(unittest.TestCase):
    def test_stub_proxies_stdout_stderr_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()

            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "--cwd",
                        "/workspace",
                        "--command",
                        f'{sys.executable} -c "import sys; print(\'out\'); print(\'err\', file=sys.stderr); raise SystemExit(7)"',
                    ]
                )

        self.assertEqual(7, code)
        self.assertIn("out", stdout_buffer.getvalue())
        self.assertIn("err", stderr_buffer.getvalue())

    def test_stub_emulates_fixture_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()

            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "--cwd",
                        "/workspace",
                        "--setenv",
                        "AI_IDE_SANDBOX_ROOT",
                        "/workspace",
                        "--setenv",
                        "HOME",
                        str(Path(temp_dir) / "helper-root" / "home"),
                        "--setenv",
                        "XDG_CACHE_HOME",
                        str(Path(temp_dir) / "helper-root" / "cache"),
                        "--command",
                        "__AI_IDE_FIXTURE__ sandbox=%s .ai_ide_strict_fixture.txt",
                    ]
                )

            self.assertEqual(0, code)
            self.assertIn("__AI_IDE_FIXTURE__ sandbox=/workspace", stdout_buffer.getvalue())
            self.assertIn("__AI_IDE_FIXTURE__ denied_relative=hidden", stdout_buffer.getvalue())
            self.assertIn("__AI_IDE_FIXTURE__ direct_write=blocked", stdout_buffer.getvalue())
            self.assertEqual("", stderr_buffer.getvalue())
            self.assertTrue((workspace / ".ai_ide_strict_fixture.txt").exists())

    def test_stub_stdio_proxy_forwards_stdin_and_streams_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()

            with (
                patch("sys.stdin", io.StringIO("hello-helper\n")),
                redirect_stdout(stdout_buffer),
                redirect_stderr(stderr_buffer),
            ):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "--cwd",
                        "/workspace",
                        STDIO_PROXY_FLAG,
                        f"--argv={sys.executable}",
                        "--argv=-u",
                        "--argv=-c",
                        "--argv="
                        + (
                            "import sys; line=sys.stdin.readline().strip(); "
                            "print(f'proxy:{line}', flush=True); "
                            "print('proxy-err', file=sys.stderr, flush=True)"
                        ),
                    ]
                )

        self.assertEqual(0, code)
        self.assertIn("proxy:hello-helper", stdout_buffer.getvalue())
        self.assertIn("proxy-err", stderr_buffer.getvalue())

    def test_stub_runs_one_shot_argv_without_shell_wrapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()

            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "--cwd",
                        "/workspace",
                        f"--argv={sys.executable}",
                        "--argv=-c",
                        "--argv=print('argv-helper-out')",
                    ]
                )

        self.assertEqual(0, code)
        self.assertIn("argv-helper-out", stdout_buffer.getvalue())
        self.assertEqual("", stderr_buffer.getvalue())

    def test_stub_control_file_stops_argv_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            control_file = workspace / ".control" / "stop.txt"
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()

            def write_stop() -> None:
                time.sleep(0.2)
                write_helper_control_message(
                    control_file,
                    WindowsStrictHelperControlMessage(
                        command="stop",
                        run_id="test-run",
                        backend="restricted-host-helper",
                    ),
                )

            writer = Thread(target=write_stop, daemon=True)
            writer.start()
            with (
                patch("sys.stdin", io.StringIO("")),
                redirect_stdout(stdout_buffer),
                redirect_stderr(stderr_buffer),
            ):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "--cwd",
                        "/workspace",
                        STDIO_PROXY_FLAG,
                        "--setenv",
                        "AI_IDE_HELPER_STUB_CONTROL_MARKER",
                        "1",
                        "--control-file",
                        str(control_file),
                        f"--argv={sys.executable}",
                        "--argv=-u",
                        "--argv=-c",
                        "--argv=import time; print('watching', flush=True); time.sleep(5)",
                    ]
                )
            writer.join(timeout=1)

        self.assertIsInstance(code, int)
        self.assertIn("watching", stdout_buffer.getvalue())
        self.assertIn("__AI_IDE_HELPER__ control-stop", stderr_buffer.getvalue())

    def test_stub_control_file_kills_argv_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            control_file = workspace / ".control" / "stop.txt"
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()

            def write_kill() -> None:
                time.sleep(0.2)
                write_helper_control_message(
                    control_file,
                    WindowsStrictHelperControlMessage(
                        command="kill",
                        run_id="test-run",
                        backend="restricted-host-helper",
                    ),
                )

            writer = Thread(target=write_kill, daemon=True)
            writer.start()
            with (
                patch("sys.stdin", io.StringIO("")),
                redirect_stdout(stdout_buffer),
                redirect_stderr(stderr_buffer),
            ):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "--cwd",
                        "/workspace",
                        STDIO_PROXY_FLAG,
                        "--setenv",
                        "AI_IDE_HELPER_STUB_CONTROL_MARKER",
                        "1",
                        "--control-file",
                        str(control_file),
                        f"--argv={sys.executable}",
                        "--argv=-u",
                        "--argv=-c",
                        "--argv=import time; print('watching', flush=True); time.sleep(5)",
                    ]
                )
            writer.join(timeout=1)

        self.assertNotEqual(0, code)
        self.assertIn("watching", stdout_buffer.getvalue())
        self.assertIn("__AI_IDE_HELPER__ control-kill", stderr_buffer.getvalue())

    def test_stub_control_file_reports_status_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            control_file = workspace / ".control" / "control.json"
            response_file = workspace / ".control" / "status.json"
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()

            def write_status() -> None:
                time.sleep(0.2)
                write_helper_control_message(
                    control_file,
                    WindowsStrictHelperControlMessage(
                        command="status",
                        request_id="status-1234",
                        run_id="test-run",
                        backend="restricted-host-helper",
                    ),
                )
                deadline = time.time() + 1
                while time.time() < deadline:
                    response = read_helper_status_message(response_file)
                    if response is not None:
                        return
                    time.sleep(0.02)

            writer = Thread(target=write_status, daemon=True)
            writer.start()
            with (
                patch("sys.stdin", io.StringIO("")),
                redirect_stdout(stdout_buffer),
                redirect_stderr(stderr_buffer),
            ):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "--cwd",
                        "/workspace",
                        STDIO_PROXY_FLAG,
                        "--setenv",
                        "AI_IDE_HELPER_STUB_CONTROL_MARKER",
                        "1",
                        "--control-file",
                        str(control_file),
                        "--response-file",
                        str(response_file),
                        f"--argv={sys.executable}",
                        "--argv=-u",
                        "--argv=-c",
                        "--argv=import time; print('watching', flush=True); time.sleep(1)",
                    ]
                )
            writer.join(timeout=1)
            response = read_helper_status_message(response_file)

        self.assertEqual(0, code)
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual("status-1234", response.request_id)
        self.assertEqual("running", response.state)
        self.assertIsInstance(response.pid, int)
        self.assertIn("__AI_IDE_HELPER__ control-status", stderr_buffer.getvalue())
