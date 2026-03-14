from __future__ import annotations

import json
from typing import TYPE_CHECKING

from backend.commands.common import parse_agent_exec_args, parse_agent_transport_args

if TYPE_CHECKING:
    from backend.app import AIIdeApp


def handle_agent_command(app: "AIIdeApp", parts: list[str], raw: str) -> str:
    if len(parts) < 2:
        raise ValueError(
            "Usage: agent show|rotate|list|registry|plan|transport|exec|start|send|poll|stop|history|watch|watches|inbox|unwatch|gc"
        )

    subcommand = parts[1]
    if subcommand == "show":
        session = app.sessions.current_agent_session
        probe = app.agents.probe(session.agent_kind)
        launch_plan = app.agents.launch_plan(session.agent_kind)
        transport_plan = app.agent_runtime.describe_transport(session.agent_kind)
        if len(parts) >= 3 and parts[2] == "json":
            return json.dumps(
                {
                    "session": session.to_dict(),
                    "probe": probe.to_dict(),
                    "launch_plan": launch_plan.to_dict(),
                    "transport_plan": transport_plan.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        launcher_label = launch_plan.launcher or probe.launcher_hint or "(none)"
        return (
            f"agent_session_id={session.agent_session_id} "
            f"execution_session_id={session.execution_session_id} "
            f"agent_kind={session.agent_kind} status={session.status} "
            f"adapter_status={probe.status_code} adapter_available={str(probe.available).lower()} "
            f"transport={probe.transport} io_pref={probe.io_mode_preference} "
            f"provider={transport_plan.provider_name} pty={str(transport_plan.supports_pty).lower()} "
            f"io={transport_plan.resolved_io_mode} transport_status={transport_plan.transport_status} "
            f"launchable={str(transport_plan.launchable).lower()} launcher={launcher_label}"
        )

    if subcommand == "rotate":
        agent_kind = parts[2] if len(parts) >= 3 else None
        if agent_kind is not None:
            app.agents.get(agent_kind)
        session = app.rotate_agent(agent_kind)
        return (
            f"rotated_agent_session={session.agent_session_id} "
            f"execution_session_id={session.execution_session_id} "
            f"agent_kind={session.agent_kind}"
        )

    if subcommand == "list":
        sessions = app.sessions.list_agent_sessions(app.sessions.current_session_id)
        if len(parts) >= 3 and parts[2] == "json":
            return json.dumps([session.to_dict() for session in sessions], indent=2, sort_keys=True)
        rows = []
        for session in sessions:
            rows.append(
                f"{session.agent_session_id} exec={session.execution_session_id} "
                f"kind={session.agent_kind} status={session.status}"
            )
        return "\n".join(rows) if rows else "(no agent sessions)"

    if subcommand == "registry":
        probes = app.agents.probe_all()
        if len(parts) >= 3 and parts[2] == "json":
            return json.dumps([probe.to_dict() for probe in probes], indent=2, sort_keys=True)
        rows = []
        for probe in probes:
            launcher_label = probe.launcher or probe.launcher_hint or "(none)"
            rows.append(
                f"{probe.kind} available={str(probe.available).lower()} "
                f"status={probe.status_code} transport={probe.transport} "
                f"tty={str(probe.requires_tty).lower()} io_pref={probe.io_mode_preference} "
                f"launcher={launcher_label}"
            )
        return "\n".join(rows) if rows else "(no agent adapters)"

    if subcommand == "plan":
        wants_json = len(parts) >= 3 and parts[-1] == "json"
        plan_parts = parts[2:-1] if wants_json else parts[2:]
        agent_kind = plan_parts[0] if plan_parts else app.sessions.current_agent_session.agent_kind
        plan = app.agents.launch_plan(agent_kind)
        transport_plan = app.agent_runtime.describe_transport(agent_kind)
        if wants_json:
            return json.dumps(
                {
                    "launch_plan": plan.to_dict(),
                    "transport_plan": transport_plan.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        argv_label = " ".join(plan.argv) if plan.argv else "(unavailable)"
        return (
            f"agent_kind={plan.kind} available={str(plan.available).lower()} "
            f"status={plan.status_code} transport={plan.transport} "
            f"tty={str(plan.requires_tty).lower()} io_pref={plan.io_mode_preference} "
            f"provider={transport_plan.provider_name} pty={str(transport_plan.supports_pty).lower()} "
            f"io={transport_plan.resolved_io_mode} transport_status={transport_plan.transport_status} "
            f"launchable={str(transport_plan.launchable).lower()} argv={argv_label}"
        )

    if subcommand == "transport":
        wants_json = len(parts) >= 3 and parts[-1] == "json"
        transport_parts = parts[2:-1] if wants_json else parts[2:]
        agent_kind, mode = parse_agent_transport_args(transport_parts)
        transport_plan = app.agent_runtime.describe_transport(agent_kind, mode=mode)
        if wants_json:
            return json.dumps(transport_plan.to_dict(), indent=2, sort_keys=True)
        launcher_label = transport_plan.launcher or "(none)"
        return (
            f"agent_kind={transport_plan.agent_kind} mode={transport_plan.runner_mode} "
            f"adapter_available={str(transport_plan.adapter_available).lower()} "
            f"adapter_status={transport_plan.adapter_status} transport={transport_plan.transport} "
            f"tty={str(transport_plan.requires_tty).lower()} requested_io={transport_plan.requested_io_mode} "
            f"provider={transport_plan.provider_name} pty={str(transport_plan.supports_pty).lower()} "
            f"io={transport_plan.resolved_io_mode} transport_status={transport_plan.transport_status} "
            f"launchable={str(transport_plan.launchable).lower()} launcher={launcher_label} "
            f"detail={transport_plan.detail}"
        )

    if subcommand == "exec":
        mode, extra_args = parse_agent_exec_args(parts[2:])
        execution = app.agent_runtime.execute_current(extra_args or None, mode=mode)
        output = (execution.result.stdout + execution.result.stderr).strip() or "(no output)"
        return (
            f"[agent_session={execution.record.agent_session_id} "
            f"kind={execution.record.agent_kind} mode={execution.record.runner_mode} "
            f"io={execution.record.io_mode} transport_status={execution.record.transport_status} "
            f"backend={execution.record.backend} code={execution.record.returncode} "
            f"status={execution.record.status}]\n{output}"
        )

    if subcommand == "start":
        mode, extra_args = parse_agent_exec_args(parts[2:])
        record = app.agent_runtime.start_current(extra_args or None, mode=mode)
        argv_label = " ".join(record.argv) if record.argv else "(unavailable)"
        return (
            f"started_run={record.run_id} agent_session={record.agent_session_id} "
            f"kind={record.agent_kind} mode={record.runner_mode} io={record.io_mode} "
            f"transport_status={record.transport_status} backend={record.backend} "
            f"status={record.status} pid={record.pid or '(none)'} argv={argv_label}"
        )

    if subcommand == "poll" and len(parts) >= 3:
        inspection = app.agent_runtime.inspect_run(parts[2])
        if len(parts) >= 4 and parts[3] == "json":
            return json.dumps(inspection.to_dict(), indent=2, sort_keys=True)
        record = inspection.record
        process = inspection.process
        output = (record.stdout + record.stderr).strip() or "(no output)"
        return (
            f"[run_id={record.run_id} kind={record.agent_kind} mode={record.runner_mode} "
            f"io={record.io_mode} transport_status={record.transport_status} "
            f"backend={record.backend} status={record.status} code={record.returncode} "
            f"pid={record.pid or '(none)'} process_source={process.source} "
            f"process_state={process.state} process_pid={process.pid or '(none)'} "
            f"process_code={process.returncode if process.returncode is not None else '(none)'}]\n{output}"
        )

    if subcommand == "send" and len(parts) >= 4:
        run_id = parts[2]
        message = raw.split(run_id, 1)[1].strip()
        record = app.agent_runtime.send_input(run_id, message)
        return (
            f"sent_input run_id={record.run_id} kind={record.agent_kind} "
            f"status={record.status} bytes={len(message) + 1}"
        )

    if subcommand == "stop" and len(parts) >= 3:
        record = app.agent_runtime.stop_run(parts[2], reason="requested by user")
        output = (record.stdout + record.stderr).strip() or "(no output)"
        return (
            f"[run_id={record.run_id} kind={record.agent_kind} mode={record.runner_mode} "
            f"backend={record.backend} status={record.status} code={record.returncode} "
            f"pid={record.pid or '(none)'}]\n{output}"
        )

    if subcommand == "history":
        wants_json = len(parts) >= 3 and parts[-1] == "json"
        history_parts = parts[2:-1] if wants_json else parts[2:]
        scope = history_parts[0] if history_parts else "auto"
        if scope not in {"auto", "current", "all"}:
            raise ValueError("Usage: agent history [auto|current|all] [json]")
        current_runs = app.agent_runtime.list_run_inspections(app.sessions.current_session_id)
        if scope == "current":
            inspections = current_runs
        elif scope == "all":
            inspections = app.agent_runtime.list_run_inspections()
        else:
            inspections = current_runs or app.agent_runtime.list_run_inspections()
        if wants_json:
            return json.dumps([inspection.to_dict() for inspection in inspections], indent=2, sort_keys=True)
        rows = []
        for inspection in inspections:
            record = inspection.record
            process = inspection.process
            argv_label = " ".join(record.argv) if record.argv else "(unavailable)"
            rows.append(
                f"{record.run_id} agent_session={record.agent_session_id} "
                f"kind={record.agent_kind} mode={record.runner_mode} "
                f"io={record.io_mode} transport_status={record.transport_status} "
                f"backend={record.backend} code={record.returncode} "
                f"status={record.status} process_source={process.source} "
                f"process_state={process.state} process_pid={process.pid or '(none)'} "
                f"process_code={process.returncode if process.returncode is not None else '(none)'} "
                f"argv={argv_label}"
            )
        return "\n".join(rows) if rows else "(no agent runs)"

    if subcommand == "watch" and len(parts) >= 3:
        run_id = parts[2]
        wants_json = len(parts) >= 4 and parts[-1] == "json"
        watch_parts = parts[3:-1] if wants_json else parts[3:]
        replay = "--replay" in watch_parts
        label_parts = [token for token in watch_parts if token != "--replay"]
        watch = app.agent_runtime.watch_run(run_id, name=label_parts[0] if label_parts else None, replay=replay)
        if wants_json:
            return json.dumps(watch.to_dict(), indent=2, sort_keys=True)
        return (
            f"watch_id={watch.watch_id} run_id={watch.run_id} "
            f"consumer={watch.consumer_id} name={watch.name}"
        )

    if subcommand == "watches":
        wants_json = len(parts) >= 3 and parts[-1] == "json"
        watch_parts = parts[2:-1] if wants_json else parts[2:]
        run_id = watch_parts[0] if watch_parts else None
        watches = app.agent_runtime.list_watches(run_id)
        if wants_json:
            return json.dumps([watch.to_dict() for watch in watches], indent=2, sort_keys=True)
        rows = []
        for watch in watches:
            rows.append(
                f"{watch.watch_id} run_id={watch.run_id} consumer={watch.consumer_id} "
                f"name={watch.name} updated_at={watch.updated_at or watch.created_at}"
            )
        return "\n".join(rows) if rows else "(no agent watches)"

    if subcommand == "inbox" and len(parts) >= 3:
        wants_json = len(parts) >= 4 and parts[-1] == "json"
        inbox_parts = parts[3:-1] if wants_json else parts[3:]
        limit = int(inbox_parts[0]) if inbox_parts else 20
        events = app.agent_runtime.pull_watch(parts[2], limit=limit)
        if wants_json:
            return json.dumps([event.to_dict() for event in events], indent=2, sort_keys=True)
        rows = []
        for event in events:
            rows.append(
                f"{event.event_id} kind={event.kind} source={event.source_type}:{event.source_id} "
                f"payload={event.payload}"
            )
        return "\n".join(rows) if rows else "(empty inbox)"

    if subcommand == "unwatch" and len(parts) >= 3:
        app.agent_runtime.unwatch_run(parts[2])
        return f"unwatched {parts[2]}"

    if subcommand == "gc" and len(parts) >= 3:
        removed = app.agent_runtime.cleanup_stale_watches(int(parts[2]))
        return f"removed_agent_watches={removed}"

    raise ValueError(
        "Usage: agent show [json] | agent rotate [kind] | agent list [json] | agent registry [json] | "
        "agent plan [kind] [json] | agent transport [kind] [--mode <projected|strict>] [json] | "
        "agent exec [--mode <projected|strict>] [-- <args...>] | "
        "agent start [--mode <projected|strict>] [-- <args...>] | agent send <run_id> <text> | agent poll <run_id> [json] | "
        "agent stop <run_id> | agent history [auto|current|all] [json] | agent watch <run_id> [name] [--replay] [json] | "
        "agent watches [run_id] [json] | agent inbox <watch_id> [limit] [json] | agent unwatch <watch_id> | "
        "agent gc <max_age_seconds>"
    )
