from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from threading import Thread

# Ensure project root is on sys.path for standalone execution
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from backend.windows.windows_strict_helper_protocol import (
        FIXTURE_MARKER_PREFIX,
        read_helper_control_message,
        parse_helper_args,
        write_helper_status_message,
        WindowsStrictHelperStatusMessage,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution path
    from windows_strict_helper_protocol import (
        FIXTURE_MARKER_PREFIX,
        WindowsStrictHelperStatusMessage,
        parse_helper_args,
        read_helper_control_message,
        write_helper_status_message,
    )


def _map_workspace_path(workspace: Path, logical_cwd: str) -> Path:
    if logical_cwd == "/workspace":
        return workspace
    prefix = "/workspace/"
    if logical_cwd.startswith(prefix):
        suffix = logical_cwd[len(prefix) :].replace("/", os.sep)
        return workspace / suffix
    raise ValueError(f"unsupported logical cwd: {logical_cwd}")


def _is_fixture_command(command: str | None) -> bool:
    if command is None:
        return False
    return FIXTURE_MARKER_PREFIX in command and ".ai_ide_strict_fixture.txt" in command


def _run_fixture_emulation(cwd: Path, env: dict[str, str]) -> int:
    fixture_path = cwd / ".ai_ide_strict_fixture.txt"
    fixture_path.write_text("fixture", encoding="utf-8")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        print(f"{FIXTURE_MARKER_PREFIX} sandbox={env.get('AI_IDE_SANDBOX_ROOT', '')}")
        print(f"{FIXTURE_MARKER_PREFIX} home={env.get('HOME', '')}")
        print(f"{FIXTURE_MARKER_PREFIX} cache={env.get('XDG_CACHE_HOME', '')}")
        print(f"{FIXTURE_MARKER_PREFIX} denied_relative=hidden")
        print(f"{FIXTURE_MARKER_PREFIX} denied_direct=hidden")
        print(f"{FIXTURE_MARKER_PREFIX} direct_write=blocked")
    sys.stdout.write(buffer.getvalue())
    return 0


def _forward_output(reader, writer) -> None:
    try:
        for line in iter(reader.readline, ""):
            writer.write(line)
            writer.flush()
    finally:
        reader.close()


def _forward_input(source, child_stdin) -> None:
    try:
        for line in iter(source.readline, ""):
            try:
                child_stdin.write(line)
                child_stdin.flush()
            except (BrokenPipeError, OSError):
                break
    finally:
        try:
            child_stdin.close()
        except OSError:
            pass


def _run_with_stdio_proxy(
    cwd: Path,
    env: dict[str, str],
    command: str,
    control_file: Path | None,
    response_file: Path | None,
) -> int:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        shell=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = Thread(target=_forward_output, args=(process.stdout, sys.stdout), daemon=True)
    stderr_thread = Thread(target=_forward_output, args=(process.stderr, sys.stderr), daemon=True)
    stdin_thread = Thread(target=_forward_input, args=(sys.stdin, process.stdin), daemon=True)
    control_thread = Thread(
        target=_watch_control_file,
        args=(
            process,
            process.stdin,
            control_file,
            response_file,
            env.get("AI_IDE_HELPER_STUB_CONTROL_MARKER") == "1",
        ),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    stdin_thread.start()
    control_thread.start()
    returncode = process.wait()
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    control_thread.join(timeout=1)
    return returncode


def _watch_control_file(
    process,
    child_stdin,
    control_file: Path | None,
    response_file: Path | None,
    emit_marker: bool,
) -> None:
    if control_file is None:
        return
    last_request_key: tuple[str | None, str, str | None, str | None] | None = None
    while process.poll() is None:
        if control_file.exists():
            message = read_helper_control_message(control_file)
            if message is not None:
                request_key = (message.request_id, message.command, message.run_id, message.backend)
                if request_key == last_request_key:
                    time.sleep(0.05)
                    continue
                last_request_key = request_key
                command = message.command.lower()
                if command == "status" and response_file is not None:
                    write_helper_status_message(
                        response_file,
                        WindowsStrictHelperStatusMessage(
                            request_id=message.request_id,
                            run_id=message.run_id,
                            backend=message.backend,
                            state="running",
                            pid=process.pid,
                        ),
                    )
                    if emit_marker:
                        sys.stderr.write("__AI_IDE_HELPER__ control-status\n")
                        sys.stderr.flush()
                    time.sleep(0.05)
                    continue
                if command == "stop":
                    if emit_marker:
                        sys.stderr.write("__AI_IDE_HELPER__ control-stop\n")
                        sys.stderr.flush()
                    try:
                        child_stdin.close()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                    return
                if command == "kill":
                    if emit_marker:
                        sys.stderr.write("__AI_IDE_HELPER__ control-kill\n")
                        sys.stderr.flush()
                    process.kill()
                    return
        time.sleep(0.05)


def _run_argv_with_stdio_proxy(
    cwd: Path,
    env: dict[str, str],
    argv: tuple[str, ...],
    control_file: Path | None,
    response_file: Path | None,
) -> int:
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        shell=False,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = Thread(target=_forward_output, args=(process.stdout, sys.stdout), daemon=True)
    stderr_thread = Thread(target=_forward_output, args=(process.stderr, sys.stderr), daemon=True)
    stdin_thread = Thread(target=_forward_input, args=(sys.stdin, process.stdin), daemon=True)
    control_thread = Thread(
        target=_watch_control_file,
        args=(
            process,
            process.stdin,
            control_file,
            response_file,
            env.get("AI_IDE_HELPER_STUB_CONTROL_MARKER") == "1",
        ),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    stdin_thread.start()
    control_thread.start()
    returncode = process.wait()
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    control_thread.join(timeout=1)
    return returncode


def _run_argv_once(cwd: Path, env: dict[str, str], argv: tuple[str, ...]) -> int:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        shell=False,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout:
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
    if completed.stderr:
        sys.stderr.write(completed.stderr)
        sys.stderr.flush()
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    request = parse_helper_args(list(sys.argv[1:] if argv is None else argv))
    workspace = request.workspace
    cwd = _map_workspace_path(workspace, request.cwd)
    env = dict(os.environ)
    env.update(request.environment)

    if _is_fixture_command(request.command):
        return _run_fixture_emulation(cwd, env)
    if request.stdio_proxy:
        if request.argv:
            return _run_argv_with_stdio_proxy(
                cwd,
                env,
                request.argv,
                request.control_file,
                request.response_file,
            )
        assert request.command is not None
        return _run_with_stdio_proxy(
            cwd,
            env,
            request.command,
            request.control_file,
            request.response_file,
        )
    if request.argv:
        return _run_argv_once(cwd, env, request.argv)
    assert request.command is not None

    completed = subprocess.run(
        request.command,
        cwd=cwd,
        env=env,
        shell=True,
        text=True,
        capture_output=True,
    )
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
