use ai_ide_core::review_render::ReviewRenderService;
use ai_ide_protocol::{ProposalStatus, WriteProposal};

#[test]
fn render_proposal_includes_metadata_and_unified_diff_headers() {
    let proposal = WriteProposal {
        proposal_id: "rev-00000001".to_owned(),
        target: "src/app.py".to_owned(),
        session_id: "sess-00000001".to_owned(),
        agent_session_id: "agent-00000001".to_owned(),
        created_at: "1970-01-01T00:00:01Z".to_owned(),
        updated_at: "1970-01-01T00:00:02Z".to_owned(),
        status: ProposalStatus::Pending,
        base_sha256: Some("abc123".to_owned()),
        base_text: Some("print('old')\n".to_owned()),
        proposed_text: "print('new')\n".to_owned(),
    };

    let rendered = ReviewRenderService::render_proposal(&proposal);

    assert!(rendered.contains("proposal_id=rev-00000001"));
    assert!(rendered.contains("--- a/src/app.py"));
    assert!(rendered.contains("+++ b/src/app.py"));
    assert!(rendered.contains("-print('old')"));
    assert!(rendered.contains("+print('new')"));
}

#[test]
fn render_proposal_returns_no_diff_for_identical_text() {
    let proposal = WriteProposal {
        proposal_id: "rev-00000002".to_owned(),
        target: "notes/todo.txt".to_owned(),
        session_id: "sess-00000001".to_owned(),
        agent_session_id: "agent-00000001".to_owned(),
        created_at: "1970-01-01T00:00:01Z".to_owned(),
        updated_at: "1970-01-01T00:00:02Z".to_owned(),
        status: ProposalStatus::Pending,
        base_sha256: Some("abc123".to_owned()),
        base_text: Some("same\n".to_owned()),
        proposed_text: "same\n".to_owned(),
    };

    let rendered = ReviewRenderService::render_proposal(&proposal);

    assert!(rendered.ends_with("(no diff)"));
}
