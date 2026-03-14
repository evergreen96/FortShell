from __future__ import annotations

import argparse
import base64
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

try:
    from ai_ide.atomic_files import atomic_replace
except ModuleNotFoundError:  # pragma: no cover - direct script execution path
    from atomic_files import atomic_replace

FIXTURE_MARKER_PREFIX = "__AI_IDE_FIXTURE__"
HELPER_HOST_PATH_SCHEME = "aiide-helper://host-path/"
STDIO_PROXY_FLAG = "--stdio-proxy"
CONTROL_FILE_FLAG = "--control-file"
RESPONSE_FILE_FLAG = "--response-file"
HELPER_CONTROL_COMMANDS = frozenset({"stop", "kill", "status"})
HELPER_STATUS_STATES = frozenset({"running", "exited"})


@dataclass(frozen=True)
class WindowsStrictHelperRequest:
    workspace: Path
    cwd: str
    environment: dict[str, str]
    command: str | None = None
    argv: tuple[str, ...] = ()
    stdio_proxy: bool = False
    control_file: Path | None = None
    response_file: Path | None = None


@dataclass(frozen=True)
class WindowsStrictHelperControlMessage:
    version: int = 1
    command: str = "stop"
    request_id: str | None = None
    run_id: str | None = None
    backend: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": self.version,
            "command": self.command,
        }
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        if self.run_id is not None:
            payload["run_id"] = self.run_id
        if self.backend is not None:
            payload["backend"] = self.backend
        return payload


@dataclass(frozen=True)
class WindowsStrictHelperStatusMessage:
    version: int = 1
    request_id: str | None = None
    run_id: str | None = None
    backend: str | None = None
    state: str = "running"
    pid: int | None = None
    returncode: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": self.version,
            "state": self.state,
        }
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        if self.run_id is not None:
            payload["run_id"] = self.run_id
        if self.backend is not None:
            payload["backend"] = self.backend
        if self.pid is not None:
            payload["pid"] = self.pid
        if self.returncode is not None:
            payload["returncode"] = self.returncode
        return payload


def build_helper_command(
    helper_command: list[str],
    request: WindowsStrictHelperRequest,
) -> list[str]:
    command = [
        *helper_command,
        "--workspace",
        str(request.workspace),
        "--cwd",
        request.cwd,
    ]
    for name, value in request.environment.items():
        command.extend(["--setenv", name, value])
    if request.stdio_proxy:
        command.append(STDIO_PROXY_FLAG)
    if request.control_file is not None:
        command.extend([CONTROL_FILE_FLAG, str(request.control_file)])
    if request.response_file is not None:
        command.extend([RESPONSE_FILE_FLAG, str(request.response_file)])
    if request.argv:
        for arg in request.argv:
            command.append(f"--argv={arg}")
    elif request.command is not None:
        command.extend(["--command", request.command])
    else:
        raise ValueError("helper request must provide either argv or command")
    return command


def parse_helper_args(argv: list[str]) -> WindowsStrictHelperRequest:
    parser = argparse.ArgumentParser(prog="windows_restricted_host_helper")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--setenv", action="append", nargs=2, default=[])
    parser.add_argument("--argv", action="append", default=[])
    parser.add_argument(STDIO_PROXY_FLAG, action="store_true", dest="stdio_proxy")
    parser.add_argument(CONTROL_FILE_FLAG)
    parser.add_argument(RESPONSE_FILE_FLAG)
    parser.add_argument("--command")
    args = parser.parse_args(argv)
    if not args.argv and args.command is None:
        parser.error("expected --command or at least one --argv value")
    return WindowsStrictHelperRequest(
        workspace=Path(args.workspace).resolve(),
        cwd=args.cwd,
        command=args.command,
        argv=tuple(args.argv),
        environment={name: value for name, value in args.setenv},
        stdio_proxy=args.stdio_proxy,
        control_file=Path(args.control_file).resolve() if args.control_file else None,
        response_file=Path(args.response_file).resolve() if args.response_file else None,
    )


def encode_visible_host_path_token(path: Path) -> str:
    encoded = base64.urlsafe_b64encode(str(path.resolve()).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{HELPER_HOST_PATH_SCHEME}{encoded}"


def write_helper_control_message(path: Path, message: WindowsStrictHelperControlMessage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temp_path.write_text(json.dumps(message.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    atomic_replace(temp_path, path)


def read_helper_control_message(path: Path) -> WindowsStrictHelperControlMessage | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version", 1)
    command = payload.get("command")
    if not isinstance(version, int) or not isinstance(command, str):
        return None
    if command not in HELPER_CONTROL_COMMANDS:
        return None
    request_id = payload.get("request_id")
    run_id = payload.get("run_id")
    backend = payload.get("backend")
    if request_id is not None and not isinstance(request_id, str):
        return None
    if run_id is not None and not isinstance(run_id, str):
        return None
    if backend is not None and not isinstance(backend, str):
        return None
    return WindowsStrictHelperControlMessage(
        version=version,
        command=command,
        request_id=request_id,
        run_id=run_id,
        backend=backend,
    )


def write_helper_status_message(path: Path, message: WindowsStrictHelperStatusMessage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temp_path.write_text(json.dumps(message.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    atomic_replace(temp_path, path)


def read_helper_status_message(path: Path) -> WindowsStrictHelperStatusMessage | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version", 1)
    state = payload.get("state")
    if not isinstance(version, int) or not isinstance(state, str):
        return None
    request_id = payload.get("request_id")
    run_id = payload.get("run_id")
    backend = payload.get("backend")
    pid = payload.get("pid")
    returncode = payload.get("returncode")
    if state not in HELPER_STATUS_STATES:
        return None
    if request_id is not None and not isinstance(request_id, str):
        return None
    if run_id is not None and not isinstance(run_id, str):
        return None
    if backend is not None and not isinstance(backend, str):
        return None
    if pid is not None and not isinstance(pid, int):
        return None
    if returncode is not None and not isinstance(returncode, int):
        return None
    return WindowsStrictHelperStatusMessage(
        version=version,
        request_id=request_id,
        run_id=run_id,
        backend=backend,
        state=state,
        pid=pid,
        returncode=returncode,
    )
