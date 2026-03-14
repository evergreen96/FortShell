from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.windows_strict_helper_protocol import (
    CONTROL_FILE_FLAG,
    HELPER_STATUS_STATES,
    HELPER_HOST_PATH_SCHEME,
    HELPER_CONTROL_COMMANDS,
    RESPONSE_FILE_FLAG,
    STDIO_PROXY_FLAG,
    WindowsStrictHelperControlMessage,
    WindowsStrictHelperStatusMessage,
    WindowsStrictHelperRequest,
    build_helper_command,
    encode_visible_host_path_token,
    parse_helper_args,
    read_helper_control_message,
    read_helper_status_message,
    write_helper_control_message,
    write_helper_status_message,
)


class WindowsStrictHelperProtocolTests(unittest.TestCase):
    def test_build_helper_command_includes_env_and_stdio_proxy_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            command = build_helper_command(
                ["helper.exe"],
                WindowsStrictHelperRequest(
                    workspace=workspace,
                    cwd="/workspace",
                    command="python -V",
                    environment={"AI_IDE_STRICT_BACKEND": "restricted-host-helper"},
                    stdio_proxy=True,
                    control_file=workspace / ".control" / "stop.txt",
                    response_file=workspace / ".control" / "status.json",
                ),
            )

        self.assertEqual("helper.exe", command[0])
        self.assertIn("--workspace", command)
        self.assertIn(str(workspace), command)
        self.assertIn(STDIO_PROXY_FLAG, command)
        self.assertIn(CONTROL_FILE_FLAG, command)
        self.assertIn(RESPONSE_FILE_FLAG, command)
        self.assertEqual("python -V", command[-1])

    def test_parse_helper_args_roundtrips_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            request = parse_helper_args(
                [
                    "--workspace",
                    str(workspace),
                    "--cwd",
                    "/workspace",
                    "--setenv",
                    "AI_IDE_STRICT_BACKEND",
                    "restricted-host-helper",
                    STDIO_PROXY_FLAG,
                    "--command",
                    "python -V",
                ]
            )

        self.assertEqual(workspace.resolve(), request.workspace)
        self.assertEqual("/workspace", request.cwd)
        self.assertEqual("python -V", request.command)
        self.assertTrue(request.stdio_proxy)
        self.assertEqual("restricted-host-helper", request.environment["AI_IDE_STRICT_BACKEND"])

    def test_parse_helper_args_supports_argv_process_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            request = parse_helper_args(
                [
                    "--workspace",
                    str(workspace),
                    "--cwd",
                    "/workspace",
                    STDIO_PROXY_FLAG,
                    "--control-file",
                    str(workspace / ".control" / "stop.txt"),
                    "--response-file",
                    str(workspace / ".control" / "status.json"),
                    "--argv=python",
                    "--argv=-u",
                    "--argv=-V",
                ]
            )

        self.assertEqual(("python", "-u", "-V"), request.argv)
        self.assertIsNone(request.command)
        self.assertTrue(request.stdio_proxy)
        self.assertEqual((workspace / ".control" / "stop.txt").resolve(), request.control_file)
        self.assertEqual((workspace / ".control" / "status.json").resolve(), request.response_file)

    def test_build_helper_command_supports_one_shot_argv_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            command = build_helper_command(
                ["helper.exe"],
                WindowsStrictHelperRequest(
                    workspace=workspace,
                    cwd="/workspace",
                    environment={},
                    argv=("python", "-c", "print('argv-mode')"),
                ),
            )

        self.assertEqual(
            [
                "helper.exe",
                "--workspace",
                str(workspace),
                "--cwd",
                "/workspace",
                "--argv=python",
                "--argv=-c",
                "--argv=print('argv-mode')",
            ],
            command,
        )

    def test_encode_visible_host_path_token_uses_opaque_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host_path = Path(temp_dir) / "project" / "secret.txt"
            host_path.parent.mkdir(parents=True)
            host_path.write_text("secret", encoding="utf-8")

            token = encode_visible_host_path_token(host_path)

        self.assertTrue(token.startswith(HELPER_HOST_PATH_SCHEME))
        self.assertNotIn(str(host_path.resolve()).lower(), token.lower())

    def test_control_message_roundtrips_through_atomic_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_file = Path(temp_dir) / "control" / "stop.json"
            message = WindowsStrictHelperControlMessage(
                command="stop",
                run_id="proc-1234",
                backend="restricted-host-helper",
            )

            write_helper_control_message(control_file, message)
            restored = read_helper_control_message(control_file)

        self.assertEqual(message, restored)

    def test_read_control_message_returns_none_for_invalid_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_file = Path(temp_dir) / "control" / "stop.json"
            control_file.parent.mkdir(parents=True)
            control_file.write_text("not-json", encoding="utf-8")

            restored = read_helper_control_message(control_file)

        self.assertIsNone(restored)

    def test_read_control_message_rejects_unknown_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_file = Path(temp_dir) / "control" / "stop.json"
            control_file.parent.mkdir(parents=True)
            control_file.write_text('{"version": 1, "command": "reboot"}', encoding="utf-8")

            restored = read_helper_control_message(control_file)

        self.assertIsNone(restored)
        self.assertIn("kill", HELPER_CONTROL_COMMANDS)

    def test_status_message_roundtrips_through_atomic_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response_file = Path(temp_dir) / "control" / "status.json"
            message = WindowsStrictHelperStatusMessage(
                request_id="status-1234",
                run_id="proc-1234",
                backend="restricted-host-helper",
                state="running",
                pid=4242,
            )

            write_helper_status_message(response_file, message)
            restored = read_helper_status_message(response_file)

        self.assertEqual(message, restored)

    def test_read_status_message_rejects_unknown_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response_file = Path(temp_dir) / "control" / "status.json"
            response_file.parent.mkdir(parents=True)
            response_file.write_text('{"version": 1, "state": "paused"}', encoding="utf-8")

            restored = read_helper_status_message(response_file)

        self.assertIsNone(restored)
        self.assertIn("running", HELPER_STATUS_STATES)


if __name__ == "__main__":
    unittest.main()
