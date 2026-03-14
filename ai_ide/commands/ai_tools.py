from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_ide.app import AIIdeApp


def handle_ai_command(app: "AIIdeApp", parts: list[str], raw: str) -> str:
    if len(parts) < 2:
        raise ValueError("Usage: ai ls|read|write|grep ...")

    subcommand = parts[1]
    if subcommand == "ls" and len(parts) >= 3:
        items = app.broker.list_dir(parts[2])
        return "\n".join(items) if items else "(empty)"

    if subcommand == "read" and len(parts) >= 3:
        return app.broker.read_file(parts[2])

    if subcommand == "write" and len(parts) >= 4:
        if parts[2] == "--direct":
            raise ValueError("Direct host writes moved to `unsafe write <file> <text>`")
        path = parts[2]
        text = raw.split(path, 1)[1].strip()
        try:
            proposal = app.reviews.stage_write(
                path,
                text,
                session_id=app.sessions.current_session_id,
                agent_session_id=app.sessions.current_agent_session_id,
            )
        except PermissionError as exc:
            app.broker.record_runtime_action(
                "review.stage",
                path,
                allowed=False,
                detail=str(exc),
                count_as_write=True,
            )
            raise
        app.broker.record_runtime_action(
            "review.stage",
            proposal.target,
            allowed=True,
            detail=f"proposal_id={proposal.proposal_id} source=ai.write",
            count_as_write=True,
        )
        app.review_events.publish_proposal_event(
            "review.proposal.staged",
            proposal,
            {"source": "ai.write"},
        )
        return f"staged proposal_id={proposal.proposal_id} target={proposal.target}"

    if subcommand == "grep" and len(parts) >= 3:
        target = parts[3] if len(parts) >= 4 else "."
        matches = app.broker.grep(parts[2], target)
        return "\n".join(matches) if matches else "(no matches)"

    raise ValueError("Usage: ai ls <dir> | ai read <file> | ai write <file> <text> | ai grep <pattern> [dir]")
