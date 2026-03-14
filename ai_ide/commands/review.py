from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_ide.app import AIIdeApp


def handle_review_command(app: "AIIdeApp", parts: list[str], raw: str) -> str:
    valid_statuses = {"pending", "applied", "rejected", "conflict"}
    if len(parts) < 2:
        raise ValueError("Usage: review list|stage|show|apply|reject ...")

    app.sync_review_state()
    subcommand = parts[1]
    if subcommand == "list":
        status = None
        if len(parts) >= 3 and parts[2] != "all":
            status = parts[2]
        if status is not None and status not in valid_statuses:
            raise ValueError("Usage: review list [pending|applied|rejected|conflict|all] [limit]")
        limit = int(parts[3]) if len(parts) >= 4 else 20
        proposals = app.reviews.list_proposals(status=status, limit=limit)
        if not proposals:
            return "(no proposals)"
        return "\n".join(
            f"{item.proposal_id} status={item.status} target={item.target} updated_at={item.updated_at}"
            for item in proposals
        )

    if subcommand == "stage" and len(parts) >= 4:
        target = parts[2]
        proposed_text = raw.split(target, 1)[1].strip()
        try:
            proposal = app.stage_review(
                target,
                proposed_text,
                session_id=app.sessions.current_session_id,
                agent_session_id=app.sessions.current_agent_session_id,
            )
        except PermissionError as exc:
            app.broker.record_runtime_action(
                "review.stage",
                target,
                allowed=False,
                detail=str(exc),
            )
            raise
        app.broker.record_runtime_action(
            "review.stage",
            proposal.target,
            allowed=True,
            detail=f"proposal_id={proposal.proposal_id} source=review.stage",
        )
        app.review_events.publish_proposal_event(
            "review.proposal.staged",
            proposal,
            {"source": "review.stage"},
        )
        return f"staged proposal_id={proposal.proposal_id} target={proposal.target}"

    if subcommand == "show" and len(parts) >= 3:
        return app.render_review(parts[2])

    if subcommand == "apply" and len(parts) >= 3:
        proposal_id = parts[2]
        try:
            proposal = app.apply_review(proposal_id)
        except RuntimeError as exc:
            conflicted = app.reviews.get_proposal(proposal_id)
            app.broker.record_runtime_action(
                "review.apply",
                conflicted.target,
                allowed=False,
                detail=f"proposal_id={conflicted.proposal_id} reason=conflict",
            )
            app.review_events.publish_proposal_event(
                "review.proposal.conflict",
                conflicted,
                {"source": "review.apply"},
            )
            raise
        app.broker.record_runtime_action(
            "review.apply",
            proposal.target,
            allowed=True,
            detail=f"proposal_id={proposal.proposal_id}",
        )
        app.review_events.publish_proposal_event(
            "review.proposal.applied",
            proposal,
            {"source": "review.apply"},
        )
        app.sync_workspace_index_cache()
        app.note_workspace_visibility_change("review.apply", target=proposal.target)
        return f"applied proposal_id={proposal.proposal_id} target={proposal.target}"

    if subcommand == "reject" and len(parts) >= 3:
        proposal = app.reject_review(parts[2])
        app.broker.record_runtime_action(
            "review.reject",
            proposal.target,
            allowed=True,
            detail=f"proposal_id={proposal.proposal_id}",
        )
        app.review_events.publish_proposal_event(
            "review.proposal.rejected",
            proposal,
            {"source": "review.reject"},
        )
        return f"rejected proposal_id={proposal.proposal_id} target={proposal.target}"

    raise ValueError("Usage: review list [status|all] [limit] | review stage <file> <text> | review show <id> | review apply <id> | review reject <id>")
