use ai_ide_protocol::WriteProposal;

pub struct ReviewRenderService;

impl ReviewRenderService {
    pub fn render_proposal(proposal: &WriteProposal) -> String {
        let diff_text = render_diff(proposal);
        format!(
            "proposal_id={} target={} status={} session_id={} agent_session_id={}\n{}",
            proposal.proposal_id,
            proposal.target,
            proposal.status,
            proposal.session_id,
            proposal.agent_session_id,
            diff_text,
        )
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum DiffOp {
    Equal(String),
    Delete(String),
    Insert(String),
}

fn render_diff(proposal: &WriteProposal) -> String {
    let old_lines = split_lines(proposal.base_text.as_deref());
    let new_lines = split_lines(Some(&proposal.proposed_text));
    if old_lines == new_lines {
        return "(no diff)".to_owned();
    }

    let mut lines = vec![
        format!("--- a/{}", proposal.target),
        format!("+++ b/{}", proposal.target),
        format!(
            "@@ {} {} @@",
            hunk_range(old_lines.len(), true),
            hunk_range(new_lines.len(), false)
        ),
    ];
    for op in diff_ops(&old_lines, &new_lines) {
        match op {
            DiffOp::Equal(line) => lines.push(format!(" {}", line)),
            DiffOp::Delete(line) => lines.push(format!("-{}", line)),
            DiffOp::Insert(line) => lines.push(format!("+{}", line)),
        }
    }
    lines.join("\n")
}

fn split_lines(text: Option<&str>) -> Vec<String> {
    text.unwrap_or_default()
        .lines()
        .map(str::to_owned)
        .collect()
}

fn hunk_range(length: usize, is_old: bool) -> String {
    let prefix = if is_old { '-' } else { '+' };
    if length == 0 {
        return format!("{prefix}0,0");
    }
    format!("{prefix}1,{length}")
}

fn diff_ops(old_lines: &[String], new_lines: &[String]) -> Vec<DiffOp> {
    let mut dp = vec![vec![0usize; new_lines.len() + 1]; old_lines.len() + 1];
    for old_index in (0..old_lines.len()).rev() {
        for new_index in (0..new_lines.len()).rev() {
            dp[old_index][new_index] = if old_lines[old_index] == new_lines[new_index] {
                dp[old_index + 1][new_index + 1] + 1
            } else {
                dp[old_index + 1][new_index].max(dp[old_index][new_index + 1])
            };
        }
    }

    let mut ops = Vec::new();
    let mut old_index = 0usize;
    let mut new_index = 0usize;
    while old_index < old_lines.len() || new_index < new_lines.len() {
        if old_index < old_lines.len()
            && new_index < new_lines.len()
            && old_lines[old_index] == new_lines[new_index]
        {
            ops.push(DiffOp::Equal(old_lines[old_index].clone()));
            old_index += 1;
            new_index += 1;
            continue;
        }

        let delete_score = if old_index < old_lines.len() {
            dp[old_index + 1][new_index]
        } else {
            0
        };
        let insert_score = if new_index < new_lines.len() {
            dp[old_index][new_index + 1]
        } else {
            0
        };

        if old_index < old_lines.len()
            && (new_index == new_lines.len() || delete_score >= insert_score)
        {
            ops.push(DiffOp::Delete(old_lines[old_index].clone()));
            old_index += 1;
        } else if new_index < new_lines.len() {
            ops.push(DiffOp::Insert(new_lines[new_index].clone()));
            new_index += 1;
        }
    }

    ops
}
