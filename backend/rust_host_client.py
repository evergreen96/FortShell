from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, IO, Sequence

from backend.rust_host_protocol import (
    RustHostProtocolError,
    RustHostResponseEnvelope,
    parse_response_envelope,
    to_json_line,
)


class RustHostTransportError(RuntimeError):
    pass


class RustHostRemoteError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_rust_host_command(
    root: Path,
    *,
    default_agent_kind: str = "default",
    policy_store_path: Path | None = None,
    review_store_path: Path | None = None,
    workspace_index_store_path: Path | None = None,
    broker_store_path: Path | None = None,
    base_command: Sequence[str] | None = None,
) -> list[str]:
    command = list(base_command) if base_command is not None else _default_base_command()
    command.extend(["--root", str(root), "--default-agent-kind", default_agent_kind])
    if policy_store_path is not None:
        command.extend(["--policy-store", str(policy_store_path)])
    if review_store_path is not None:
        command.extend(["--review-store", str(review_store_path)])
    if workspace_index_store_path is not None:
        command.extend(["--workspace-index-store", str(workspace_index_store_path)])
    if broker_store_path is not None:
        command.extend(["--broker-store", str(broker_store_path)])
    return command


class RustHostClient:
    def __init__(
        self,
        root: Path,
        *,
        default_agent_kind: str = "default",
        policy_store_path: Path | None = None,
        review_store_path: Path | None = None,
        workspace_index_store_path: Path | None = None,
        broker_store_path: Path | None = None,
        base_command: Sequence[str] | None = None,
        process_factory: Callable[..., object] | None = None,
    ) -> None:
        self.root = root
        self.default_agent_kind = default_agent_kind
        self.policy_store_path = policy_store_path
        self.review_store_path = review_store_path
        self.workspace_index_store_path = workspace_index_store_path
        self.broker_store_path = broker_store_path
        self.base_command = list(base_command) if base_command is not None else None
        self._process_factory = process_factory or subprocess.Popen
        self._process: object | None = None

    def send(self, payload: dict[str, object]) -> RustHostResponseEnvelope:
        process = self._ensure_process()
        stdin = getattr(process, "stdin", None)
        stdout = getattr(process, "stdout", None)
        if stdin is None or stdout is None:
            raise RustHostTransportError("Rust host process is missing stdio pipes")

        stdin.write(to_json_line(payload) + "\n")
        stdin.flush()

        line = stdout.readline()
        if not line:
            self._process = None
            raise RustHostTransportError(self._unexpected_eof_message(process))

        try:
            return parse_response_envelope(line)
        except RustHostProtocolError:
            self._process = None
            raise

    def request_ok(self, payload: dict[str, object], *, expected_type: str | None = None) -> object:
        response = self.send(payload)
        if not response.ok:
            if response.error is None:
                raise RustHostProtocolError("Rust host returned a non-ok response without an error payload")
            raise RustHostRemoteError(response.error.code, response.error.message)
        if expected_type is not None and response.response_type != expected_type:
            raise RustHostProtocolError(
                f"Expected response type {expected_type}, got {response.response_type}"
            )
        return response.data

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return

        for pipe_name in ("stdin", "stdout", "stderr"):
            pipe = getattr(process, pipe_name, None)
            if pipe is not None:
                pipe.close()

        if getattr(process, "poll", lambda: 0)() is not None:
            return

        terminate = getattr(process, "terminate", None)
        wait = getattr(process, "wait", None)
        kill = getattr(process, "kill", None)
        if terminate is not None:
            terminate()
        if wait is None:
            return
        try:
            wait(timeout=1)
        except subprocess.TimeoutExpired:
            if kill is not None:
                kill()
            wait(timeout=1)

    def __enter__(self) -> "RustHostClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_process(self) -> object:
        if self._process is not None and getattr(self._process, "poll", lambda: None)() is None:
            return self._process

        command = build_rust_host_command(
            self.root,
            default_agent_kind=self.default_agent_kind,
            policy_store_path=self.policy_store_path,
            review_store_path=self.review_store_path,
            workspace_index_store_path=self.workspace_index_store_path,
            broker_store_path=self.broker_store_path,
            base_command=self.base_command,
        )
        self._process = self._process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        return self._process

    def _unexpected_eof_message(self, process: object) -> str:
        stderr = getattr(process, "stderr", None)
        stderr_text = ""
        if stderr is not None:
            remainder = stderr.read()
            stderr_text = remainder.strip()
        returncode = getattr(process, "poll", lambda: None)()
        message = "Rust host closed before sending a response"
        if returncode is not None:
            message += f" (returncode={returncode})"
        if stderr_text:
            message += f": {stderr_text}"
        return message


def _default_base_command() -> list[str]:
    manifest_path = Path(__file__).resolve().parent.parent / "rust" / "Cargo.toml"
    return [
        "cargo",
        "run",
        "--quiet",
        "--manifest-path",
        str(manifest_path),
        "-p",
        "ai-ide-adapter",
        "--",
    ]
