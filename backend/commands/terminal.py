from __future__ import annotations

import json
from typing import TYPE_CHECKING

from backend.commands.common import parse_terminal_new_args

if TYPE_CHECKING:
    from backend.app import AIIdeApp


def handle_terminal_command(app: "AIIdeApp", parts: list[str], raw: str) -> str:
    if len(parts) < 2:
        raise ValueError("Usage: term new|list|show|run|msg|watch|watches|inbox|attach|input|gc ...")

    subcommand = parts[1]
    if subcommand == "new":
        transport, runner_mode, name = parse_terminal_new_args(app.runners.mode, parts[2:])
        execution_session_id = app.sessions.current_session_id if transport == "runner" else None
        terminal = app.terminals.create_terminal(
            name,
            execution_session_id=execution_session_id,
            transport=transport,
            runner_mode=runner_mode,
        )
        exec_label = terminal.execution_session_id or "(host)"
        unsafe_text = " unsafe=true" if terminal.transport == "host" else ""
        mode_label = terminal.runner_mode or "host"
        return (
            f"created {terminal.terminal_id} ({terminal.name}) "
            f"transport={terminal.transport} mode={mode_label} status={terminal.status} "
            f"exec={exec_label}{unsafe_text}"
        )

    if subcommand == "list":
        inspections = app.terminals.list_terminal_inspections()
        if len(parts) >= 3 and parts[2] == "json":
            return json.dumps([inspection.to_dict() for inspection in inspections], indent=2, sort_keys=True)
        rows = []
        for inspection in inspections:
            terminal = inspection.session
            inbox_entries = terminal.snapshot_inbox()
            command_history = terminal.snapshot_command_history()
            exec_label = terminal.execution_session_id or "(host)"
            mode_label = terminal.runner_mode or "host"
            unsafe_text = " unsafe=true" if terminal.transport == "host" else ""
            bound_run = inspection.bound_run
            row = (
                f"{terminal.terminal_id} name={terminal.name} "
                f"transport={terminal.transport} mode={mode_label} "
                f"status={terminal.status} exec={exec_label} cmds={len(command_history)} "
                f"inbox={len(inbox_entries)} bound_run={bound_run.run_id if bound_run is not None else '(none)'}"
            )
            if bound_run is not None:
                row += (
                    f" bound_run_status={bound_run.status} bound_run_backend={bound_run.backend} "
                    f"bound_run_process_source={bound_run.process_source} "
                    f"bound_run_process_state={bound_run.process_state} "
                    f"bound_run_process_pid={bound_run.process_pid or '(none)'} "
                    f"bound_run_process_code={bound_run.process_returncode if bound_run.process_returncode is not None else '(none)'}"
                )
            row += unsafe_text
            rows.append(row)
        return "\n".join(rows) if rows else "(no terminals)"

    if subcommand == "show" and len(parts) >= 3:
        inspection = app.terminals.inspect_terminal(parts[2])
        if len(parts) >= 4 and parts[3] == "json":
            return json.dumps(inspection.to_dict(), indent=2, sort_keys=True)
        terminal = inspection.session
        exec_label = terminal.execution_session_id or "(host)"
        mode_label = terminal.runner_mode or "host"
        unsafe_text = " unsafe=true" if terminal.transport == "host" else ""
        response = (
            f"{terminal.terminal_id} name={terminal.name} transport={terminal.transport} "
            f"mode={mode_label} status={terminal.status} exec={exec_label} "
            f"cmds={len(terminal.snapshot_command_history())} inbox={len(terminal.snapshot_inbox())}"
        )
        if inspection.bound_run is not None:
            bound_run = inspection.bound_run
            response += (
                f" bound_run={bound_run.run_id} bound_run_status={bound_run.status} "
                f"bound_run_backend={bound_run.backend} "
                f"bound_run_process_source={bound_run.process_source} "
                f"bound_run_process_state={bound_run.process_state} "
                f"bound_run_process_pid={bound_run.process_pid or '(none)'} "
                f"bound_run_process_code={bound_run.process_returncode if bound_run.process_returncode is not None else '(none)'}"
            )
        return response + unsafe_text

    if subcommand == "run" and len(parts) >= 4:
        terminal_id = parts[2]
        command_text = raw.split(terminal_id, 1)[1].strip()
        return app.terminals.run_command(terminal_id, command_text)

    if subcommand == "msg" and len(parts) >= 5:
        src_terminal_id = parts[2]
        dst_terminal_id = parts[3]
        message = raw.split(dst_terminal_id, 1)[1].strip()
        app.terminals.send_message(src_terminal_id, dst_terminal_id, message)
        return "message sent"

    if subcommand == "watch" and len(parts) >= 4:
        watch_parts = parts[2:]
        wants_json = bool(watch_parts and watch_parts[-1] == "json")
        if wants_json:
            watch_parts = watch_parts[:-1]
        if len(watch_parts) < 2:
            raise ValueError("Usage: term watch <id> <kind_prefix> [source_type] [source_id] [json]")
        terminal_id = watch_parts[0]
        kind_prefix = watch_parts[1]
        source_type = watch_parts[2] if len(watch_parts) >= 3 else None
        source_id = watch_parts[3] if len(watch_parts) >= 4 else None
        watch = app.terminals.watch_events(
            terminal_id,
            kind_prefix=kind_prefix,
            source_type=source_type,
            source_id=source_id,
        )
        if wants_json:
            return json.dumps(watch.to_dict(), indent=2, sort_keys=True)
        return (
            f"watching terminal={terminal_id} subscription={watch.watch_id} "
            f"kind_prefix={kind_prefix} source_type={source_type or '(any)'} "
            f"source_id={source_id or '(any)'}"
        )

    if subcommand == "watches" and len(parts) >= 3:
        terminal_id = parts[2]
        watches = app.terminals.list_watch_snapshots(terminal_id)
        if len(parts) >= 4 and parts[3] == "json":
            return json.dumps([watch.to_dict() for watch in watches], indent=2, sort_keys=True)
        rows = []
        for watch in watches:
            rows.append(
                f"{watch.watch_id} consumer={watch.consumer_id} kind_prefix={watch.kind_prefix or '(any)'} "
                f"source_type={watch.source_type or '(any)'} source_id={watch.source_id or '(any)'} "
                f"bridge={str(watch.bridge).lower()}"
            )
        return "\n".join(rows) if rows else "(no watches)"

    if subcommand == "inbox" and len(parts) >= 3:
        terminal_id = parts[2]
        if len(parts) >= 4 and parts[3] == "json":
            return json.dumps(app.terminals.read_inbox_snapshot(terminal_id).to_dict(), indent=2, sort_keys=True)
        messages = app.terminals.read_inbox(terminal_id)
        return "\n".join(messages) if messages else "(empty inbox)"

    if subcommand == "attach" and len(parts) >= 4:
        terminal_id = parts[2]
        run_id = parts[3]
        wants_json = len(parts) >= 5 and parts[4] == "json"
        app.terminals.attach_to_agent_run(terminal_id, run_id)
        inspection = app.terminals.inspect_terminal(terminal_id)
        if wants_json:
            return json.dumps(inspection.to_dict(), indent=2, sort_keys=True)
        terminal = inspection.session
        response = (
            f"attached terminal={terminal.terminal_id} run_id={terminal.bound_agent_run_id} "
            f"exec={terminal.execution_session_id or '(host)'}"
        )
        if inspection.bound_run is not None:
            bound_run = inspection.bound_run
            response += (
                f" run_status={bound_run.status} run_backend={bound_run.backend} "
                f"process_source={bound_run.process_source} process_state={bound_run.process_state} "
                f"process_pid={bound_run.process_pid or '(none)'} "
                f"process_code={bound_run.process_returncode if bound_run.process_returncode is not None else '(none)'}"
            )
        return response

    if subcommand == "input" and len(parts) >= 4:
        terminal_id = parts[2]
        message = raw.split(terminal_id, 1)[1].strip()
        run_id = app.terminals.send_input_to_agent(terminal_id, message)
        return f"sent_terminal_input terminal={terminal_id} run_id={run_id} bytes={len(message) + 1}"

    if subcommand == "gc" and len(parts) >= 3:
        removed = app.terminals.cleanup_stale_watches(int(parts[2]))
        return f"removed_terminal_watches={removed}"

    raise ValueError(
        "Usage: term new [--host] [--mode <projected|strict>] [name] | "
        "term list [json] | term show <id> [json] | term run <id> <command> | term msg <from> <to> <message> | "
        "term watch <id> <kind_prefix> [source_type] [source_id] [json] | term watches <id> [json] | "
        "term inbox <id> [json] | term attach <id> <run_id> [json] | term input <id> <text> | "
        "term gc <max_age_seconds>"
    )
