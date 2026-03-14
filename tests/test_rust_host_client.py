from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.models import PolicyState
from backend.rust_host_client import (
    RustHostClient,
    RustHostRemoteError,
    RustHostTransportError,
    build_rust_host_command,
)
from backend.rust_host_protocol import (
    RustHostSnapshot,
    policy_add_deny_rule_request,
    snapshot_request,
)


class _FakePipe:
    def __init__(self, *, lines: list[str] | None = None, read_text: str = "") -> None:
        self.lines = list(lines or [])
        self.read_text = read_text
        self.writes: list[str] = []
        self.closed = False

    def write(self, text: str) -> int:
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        return None

    def readline(self) -> str:
        if self.lines:
            return self.lines.pop(0)
        return ""

    def read(self) -> str:
        return self.read_text

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, stdout_lines: list[str], *, stderr_text: str = "", returncode: int | None = None) -> None:
        self.stdin = _FakePipe()
        self.stdout = _FakePipe(lines=stdout_lines)
        self.stderr = _FakePipe(read_text=stderr_text)
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.wait_calls: list[int] = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: int | None = None) -> int:
        self.wait_calls.append(timeout or 0)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class RustHostClientTests(unittest.TestCase):
    def test_build_rust_host_command_includes_manifest_and_optional_store_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command = build_rust_host_command(
                root,
                default_agent_kind="codex",
                policy_store_path=root / ".runtime" / "policy.json",
                review_store_path=root / ".runtime" / "reviews.json",
                workspace_index_store_path=root / ".runtime" / "workspace" / "index.json",
                broker_store_path=root / ".runtime" / "broker" / "state.json",
            )

        self.assertEqual("cargo", command[0])
        self.assertIn("--manifest-path", command)
        self.assertIn("ai-ide-adapter", command)
        self.assertIn("--root", command)
        self.assertIn("--default-agent-kind", command)
        self.assertIn("--policy-store", command)
        self.assertIn("--review-store", command)
        self.assertIn("--workspace-index-store", command)
        self.assertIn("--broker-store", command)

    def test_request_ok_writes_json_line_and_returns_typed_payload(self) -> None:
        response_text = (
            '{"error":null,"ok":true,"response":{"type":"snapshot","data":'
            '{"policy_state":{"deny_globs":[],"version":1},'
            '"execution_session":{"session_id":"sess-00000001","policy_version":1,'
            '"created_at":"1970-01-01T00:00:01Z","status":"active","rotated_from":null},'
            '"agent_session":{"agent_session_id":"agent-00000001","execution_session_id":"sess-00000001",'
            '"agent_kind":"codex","created_at":"1970-01-01T00:00:02Z","status":"active","rotated_from":null},'
            '"review_count":0,"pending_review_count":0}}}\n'
        )
        process = _FakeProcess([response_text])
        factory_calls: list[list[str]] = []

        def factory(command, **kwargs):
            factory_calls.append(command)
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            client = RustHostClient(
                Path(temp_dir),
                default_agent_kind="codex",
                process_factory=factory,
            )
            data = client.request_ok(snapshot_request(), expected_type="snapshot")

        self.assertEqual(1, len(factory_calls))
        self.assertIsInstance(data, RustHostSnapshot)
        self.assertEqual(PolicyState([], version=1), data.policy_state)
        self.assertEqual('{"type": "snapshot"}\n', process.stdin.writes[0])

    def test_request_ok_raises_remote_error_for_non_ok_envelope(self) -> None:
        process = _FakeProcess(
            [
                '{"ok":false,"response":null,"error":{"code":"review_blocked_by_policy","message":"Blocked by policy: secrets/token.txt"}}\n'
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            client = RustHostClient(Path(temp_dir), process_factory=lambda *args, **kwargs: process)
            with self.assertRaises(RustHostRemoteError) as ctx:
                client.request_ok(policy_add_deny_rule_request("secrets/**"))

        self.assertEqual("review_blocked_by_policy", ctx.exception.code)
        self.assertEqual("Blocked by policy: secrets/token.txt", ctx.exception.message)

    def test_send_raises_transport_error_on_unexpected_eof(self) -> None:
        process = _FakeProcess([], stderr_text="startup failed", returncode=1)

        with tempfile.TemporaryDirectory() as temp_dir:
            client = RustHostClient(Path(temp_dir), process_factory=lambda *args, **kwargs: process)
            with self.assertRaises(RustHostTransportError) as ctx:
                client.send(snapshot_request())

        self.assertIn("returncode=1", str(ctx.exception))
        self.assertIn("startup failed", str(ctx.exception))

    def test_close_terminates_running_process(self) -> None:
        process = _FakeProcess([], returncode=None)

        with tempfile.TemporaryDirectory() as temp_dir:
            client = RustHostClient(Path(temp_dir), process_factory=lambda *args, **kwargs: process)
            client._process = process
            client.close()

        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertTrue(process.terminated)
        self.assertEqual([1], process.wait_calls)


if __name__ == "__main__":
    unittest.main()
