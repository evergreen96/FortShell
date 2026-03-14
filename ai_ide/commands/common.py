from __future__ import annotations

HELP_TEXT = (
    "Commands: help, status[json], policy show|add|remove, session show[json], "
    "agent show|rotate|list|registry|plan|transport|exec|start|send|poll|stop|history|watch|watches|inbox|unwatch|gc (many support json), "
    "events list|tail|cursor|ack|pull|compact|gc, "
    "audit list, "
    "review list|stage|show|apply|reject, "
    "workspace list|tree|grep|panel|index, ai ls|read|write|grep, unsafe write, term new|list|show|run|msg|watch|watches|inbox|attach|input|gc, "
    "runner show|info|probe|validate|mode|refresh|exec, metrics[json], exit"
)


def parse_terminal_new_args(current_runner_mode: str, args: list[str]) -> tuple[str, str | None, str | None]:
    transport = "runner"
    runner_mode = current_runner_mode if current_runner_mode in {"projected", "strict"} else "projected"
    name = None
    index = 0

    while index < len(args):
        token = args[index]
        if token == "--host":
            transport = "host"
            runner_mode = None
            index += 1
            continue
        if token == "--mode":
            if index + 1 >= len(args):
                raise ValueError("Usage: term new [--host] [--mode <projected|strict>] [name]")
            runner_mode = args[index + 1]
            index += 2
            continue
        name = token
        index += 1

    if transport == "runner" and runner_mode not in {"projected", "strict"}:
        raise ValueError("Runner terminals support only projected or strict mode; use --host for host shell")
    return transport, runner_mode, name


def parse_agent_exec_args(args: list[str]) -> tuple[str | None, list[str]]:
    mode = None
    extra_args: list[str] = []
    index = 0

    while index < len(args):
        token = args[index]
        if token == "--mode":
            if index + 1 >= len(args):
                raise ValueError("Usage: agent exec [--mode <projected|strict>] [-- <args...>]")
            mode = args[index + 1]
            index += 2
            continue
        if token == "--":
            extra_args.extend(args[index + 1 :])
            break
        extra_args.append(token)
        index += 1

    if mode is not None and mode not in {"projected", "strict"}:
        raise ValueError("Agent execution supports only projected or strict mode")
    return mode, extra_args


def parse_agent_transport_args(args: list[str]) -> tuple[str | None, str | None]:
    agent_kind = None
    mode = None
    index = 0

    while index < len(args):
        token = args[index]
        if token == "--mode":
            if index + 1 >= len(args):
                raise ValueError("Usage: agent transport [kind] [--mode <projected|strict>]")
            mode = args[index + 1]
            index += 2
            continue
        if agent_kind is not None:
            raise ValueError("Usage: agent transport [kind] [--mode <projected|strict>]")
        agent_kind = token
        index += 1

    if mode is not None and mode not in {"projected", "strict"}:
        raise ValueError("Agent execution supports only projected or strict mode")
    return agent_kind, mode


def parse_event_query_args(
    parts: list[str],
    start_index: int,
) -> tuple[int, str | None, str | None, str | None]:
    limit = int(parts[start_index]) if len(parts) > start_index else 20
    kind_prefix = optional_cli_filter(parts[start_index + 1]) if len(parts) > start_index + 1 else None
    source_type = optional_cli_filter(parts[start_index + 2]) if len(parts) > start_index + 2 else None
    source_id = optional_cli_filter(parts[start_index + 3]) if len(parts) > start_index + 3 else None
    if source_id is not None and source_type is None:
        raise ValueError("source_id requires source_type; use 'none' for kind_prefix/source_type placeholders")
    return limit, kind_prefix, source_type, source_id


def optional_cli_filter(value: str) -> str | None:
    return None if value.lower() in {"none", "-"} else value


def format_runtime_events(events) -> str:
    rows = []
    for event in events:
        rows.append(
            f"{event.event_id} kind={event.kind} source={event.source_type}:{event.source_id} "
            f"exec={event.execution_session_id or '(none)'} payload={event.payload}"
        )
    return "\n".join(rows) if rows else "(no events)"
