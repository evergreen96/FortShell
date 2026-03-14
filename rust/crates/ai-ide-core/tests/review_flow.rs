use ai_ide_core::review::{ReviewError, ReviewManager};
use ai_ide_protocol::ProposalStatus;

#[test]
fn apply_marks_conflict_when_base_content_changed() {
    let mut manager = ReviewManager::new();
    let proposal = manager.stage_write(
        "src/app.rs",
        Some("old"),
        "new",
        "sess-00000001",
        "agent-00000001",
    );

    let error = manager
        .apply_proposal(&proposal.proposal_id, Some("different"))
        .expect_err("proposal should conflict");

    assert_eq!(
        error,
        ReviewError::Conflict {
            proposal_id: proposal.proposal_id.clone()
        }
    );
    assert_eq!(
        manager.get_proposal(&proposal.proposal_id).unwrap().status,
        ProposalStatus::Conflict
    );
}

#[test]
fn reject_updates_status_without_mutating_other_fields() {
    let mut manager = ReviewManager::new();
    let proposal = manager.stage_write(
        "src/app.rs",
        None,
        "created",
        "sess-00000001",
        "agent-00000001",
    );

    let rejected = manager
        .reject_proposal(&proposal.proposal_id)
        .expect("proposal should reject cleanly");

    assert_eq!(rejected.status, ProposalStatus::Rejected);
    assert_eq!(rejected.target, "src/app.rs");
    assert_eq!(rejected.proposed_text, "created");
    assert_eq!(manager.count_proposals(Some(ProposalStatus::Rejected)), 1);
}

#[test]
fn stage_write_populates_base_sha256_when_base_content_exists() {
    let mut manager = ReviewManager::new();

    let proposal = manager.stage_write(
        "src/app.rs",
        Some("old"),
        "new",
        "sess-00000001",
        "agent-00000001",
    );

    assert_eq!(
        proposal.base_sha256.as_deref(),
        Some("cba06b5736faf67e54b07b561eae94395e774c517a7d910a54369e1263ccfbd4")
    );
}

#[test]
fn restored_proposals_keep_counters_monotonic() {
    let restored = ai_ide_protocol::WriteProposal {
        proposal_id: "rev-00000005".to_owned(),
        target: "src/app.rs".to_owned(),
        session_id: "sess-00000001".to_owned(),
        agent_session_id: "agent-00000001".to_owned(),
        created_at: "1970-01-01T00:00:05Z".to_owned(),
        updated_at: "1970-01-01T00:00:06Z".to_owned(),
        status: ProposalStatus::Pending,
        base_sha256: None,
        base_text: Some("old".to_owned()),
        proposed_text: "new".to_owned(),
    };
    let mut manager = ReviewManager::from_proposals(vec![restored]);

    let proposal = manager.stage_write(
        "src/next.rs",
        None,
        "created",
        "sess-00000002",
        "agent-00000002",
    );

    assert_eq!(proposal.proposal_id, "rev-00000006");
    assert_eq!(proposal.created_at, "1970-01-01T00:00:07Z");
}
