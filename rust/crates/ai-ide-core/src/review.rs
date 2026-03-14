use ai_ide_protocol::{ProposalStatus, WriteProposal};
use sha2::{Digest, Sha256};

use crate::time::mock_timestamp;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewError {
    Conflict { proposal_id: String },
    NotPending { proposal_id: String },
    UnknownProposal { proposal_id: String },
}

pub struct ReviewManager {
    proposals: Vec<WriteProposal>,
    proposal_counter: u64,
    timestamp_counter: u64,
}

impl Default for ReviewManager {
    fn default() -> Self {
        Self::new()
    }
}

impl ReviewManager {
    pub fn new() -> Self {
        Self::from_proposals(Vec::new())
    }

    pub fn from_proposals(proposals: Vec<WriteProposal>) -> Self {
        let proposal_counter = proposals
            .iter()
            .filter_map(|proposal| parse_proposal_counter(&proposal.proposal_id))
            .max()
            .unwrap_or(proposals.len() as u64);
        let timestamp_counter = proposals
            .iter()
            .flat_map(|proposal| [&proposal.created_at, &proposal.updated_at])
            .filter_map(|timestamp| parse_mock_timestamp(timestamp))
            .max()
            .unwrap_or((proposals.len() as u64).saturating_mul(2));

        Self {
            proposals,
            proposal_counter,
            timestamp_counter,
        }
    }

    pub fn proposals(&self) -> &[WriteProposal] {
        &self.proposals
    }

    pub fn stage_write(
        &mut self,
        target: impl Into<String>,
        current_content: Option<&str>,
        proposed_text: impl Into<String>,
        session_id: impl Into<String>,
        agent_session_id: impl Into<String>,
    ) -> WriteProposal {
        let proposal = WriteProposal {
            proposal_id: self.next_proposal_id(),
            target: target.into(),
            session_id: session_id.into(),
            agent_session_id: agent_session_id.into(),
            created_at: self.next_timestamp(),
            updated_at: self.next_timestamp(),
            status: ProposalStatus::Pending,
            base_sha256: sha256(current_content),
            base_text: current_content.map(str::to_owned),
            proposed_text: proposed_text.into(),
        };
        self.proposals.push(proposal.clone());
        proposal
    }

    pub fn list_proposals(
        &self,
        status: Option<ProposalStatus>,
        limit: usize,
    ) -> Vec<WriteProposal> {
        let filtered = self
            .proposals
            .iter()
            .filter(|proposal| status.is_none_or(|value| proposal.status == value))
            .cloned()
            .collect::<Vec<_>>();
        let start = filtered.len().saturating_sub(limit);
        filtered[start..].to_vec()
    }

    pub fn count_proposals(&self, status: Option<ProposalStatus>) -> usize {
        self.proposals
            .iter()
            .filter(|proposal| status.is_none_or(|value| proposal.status == value))
            .count()
    }

    pub fn get_proposal(&self, proposal_id: &str) -> Option<&WriteProposal> {
        self.proposals
            .iter()
            .find(|proposal| proposal.proposal_id == proposal_id)
    }

    pub fn apply_proposal(
        &mut self,
        proposal_id: &str,
        current_content: Option<&str>,
    ) -> Result<WriteProposal, ReviewError> {
        let proposal = self.get_proposal(proposal_id).cloned().ok_or_else(|| {
            ReviewError::UnknownProposal {
                proposal_id: proposal_id.to_owned(),
            }
        })?;
        if proposal.status != ProposalStatus::Pending {
            return Err(ReviewError::NotPending {
                proposal_id: proposal.proposal_id,
            });
        }
        if proposal.base_text.as_deref() != current_content {
            self.replace_status(proposal_id, ProposalStatus::Conflict)?;
            return Err(ReviewError::Conflict {
                proposal_id: proposal_id.to_owned(),
            });
        }
        self.replace_status(proposal_id, ProposalStatus::Applied)
    }

    pub fn reject_proposal(&mut self, proposal_id: &str) -> Result<WriteProposal, ReviewError> {
        let proposal = self.get_proposal(proposal_id).cloned().ok_or_else(|| {
            ReviewError::UnknownProposal {
                proposal_id: proposal_id.to_owned(),
            }
        })?;
        if proposal.status != ProposalStatus::Pending {
            return Err(ReviewError::NotPending {
                proposal_id: proposal.proposal_id,
            });
        }
        self.replace_status(proposal_id, ProposalStatus::Rejected)
    }

    pub fn mark_conflict(&mut self, proposal_id: &str) -> Result<WriteProposal, ReviewError> {
        self.replace_status(proposal_id, ProposalStatus::Conflict)
    }

    fn replace_status(
        &mut self,
        proposal_id: &str,
        status: ProposalStatus,
    ) -> Result<WriteProposal, ReviewError> {
        let index = self
            .proposals
            .iter()
            .position(|proposal| proposal.proposal_id == proposal_id)
            .ok_or_else(|| ReviewError::UnknownProposal {
                proposal_id: proposal_id.to_owned(),
            })?;
        let mut updated = self.proposals[index].clone();
        updated.status = status;
        updated.updated_at = self.next_timestamp();
        self.proposals[index] = updated.clone();
        Ok(updated)
    }

    fn next_proposal_id(&mut self) -> String {
        self.proposal_counter += 1;
        format!("rev-{:08x}", self.proposal_counter)
    }

    fn next_timestamp(&mut self) -> String {
        self.timestamp_counter += 1;
        mock_timestamp(self.timestamp_counter)
    }
}

fn sha256(text: Option<&str>) -> Option<String> {
    text.map(|value| {
        let mut hasher = Sha256::new();
        hasher.update(value.as_bytes());
        format!("{:x}", hasher.finalize())
    })
}

fn parse_proposal_counter(proposal_id: &str) -> Option<u64> {
    proposal_id.strip_prefix("rev-").and_then(|suffix| {
        if suffix.is_empty() {
            None
        } else {
            u64::from_str_radix(suffix, 16).ok()
        }
    })
}

fn parse_mock_timestamp(timestamp: &str) -> Option<u64> {
    if timestamp.len() != 20 || !timestamp.starts_with("1970-01-01T") || !timestamp.ends_with('Z') {
        return None;
    }

    let hours = timestamp[11..13].parse::<u64>().ok()?;
    let minutes = timestamp[14..16].parse::<u64>().ok()?;
    let seconds = timestamp[17..19].parse::<u64>().ok()?;
    Some(hours * 3600 + minutes * 60 + seconds)
}
