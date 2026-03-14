use std::error::Error;
use std::fmt;
use std::path::{Component, Path, PathBuf};

use ai_ide_protocol::PolicyState;

const ROOT_PATH: &str = ".";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PolicyDecision {
    pub relative_path: String,
    pub allowed: bool,
    pub matched_rule: Option<String>,
}

impl PolicyDecision {
    pub fn denied_rule(&self) -> Option<&str> {
        self.matched_rule.as_deref()
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PolicyError {
    OutsideRoot { root: PathBuf, path: PathBuf },
}

impl fmt::Display for PolicyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PolicyError::OutsideRoot { root, path } => {
                write!(
                    f,
                    "path '{}' escapes policy root '{}'",
                    path.display(),
                    root.display()
                )
            }
        }
    }
}

impl Error for PolicyError {}

pub fn normalize_rule(rule: &str) -> String {
    rule.trim().replace('\\', "/")
}

pub struct PolicyEngine {
    root: PathBuf,
    state: PolicyState,
}

impl PolicyEngine {
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self {
            root: normalize_absolute_path(root.as_ref()),
            state: PolicyState::default(),
        }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn state(&self) -> &PolicyState {
        &self.state
    }

    pub fn replace_state(&mut self, state: PolicyState) {
        self.state = PolicyState {
            deny_globs: state
                .deny_globs
                .into_iter()
                .map(|rule| normalize_rule(&rule))
                .filter(|rule| !rule.is_empty())
                .collect(),
            version: state.version.max(1),
        };
    }

    pub fn add_deny_rule(&mut self, rule: &str) -> bool {
        let normalized = normalize_rule(rule);
        if normalized.is_empty() || self.state.deny_globs.contains(&normalized) {
            return false;
        }
        self.state.deny_globs.push(normalized);
        self.state.version += 1;
        true
    }

    pub fn remove_deny_rule(&mut self, rule: &str) -> bool {
        let normalized = normalize_rule(rule);
        let Some(index) = self
            .state
            .deny_globs
            .iter()
            .position(|existing| existing == &normalized)
        else {
            return false;
        };
        self.state.deny_globs.remove(index);
        self.state.version += 1;
        true
    }

    pub fn is_allowed(&self, path: impl AsRef<Path>) -> bool {
        self.evaluate(path).allowed
    }

    pub fn evaluate(&self, path: impl AsRef<Path>) -> PolicyDecision {
        match self.relative_path(path.as_ref()) {
            Some(relative_path) => {
                let matched_rule = self
                    .state
                    .deny_globs
                    .iter()
                    .find(|rule| matches_rule(&relative_path, rule))
                    .cloned();

                PolicyDecision {
                    relative_path,
                    allowed: matched_rule.is_none(),
                    matched_rule,
                }
            }
            None => PolicyDecision {
                relative_path: String::new(),
                allowed: true,
                matched_rule: None,
            },
        }
    }

    pub fn relative_path(&self, path: &Path) -> Option<String> {
        let resolved = if path.is_absolute() {
            normalize_absolute_path(path)
        } else {
            normalize_absolute_path(&self.root.join(path))
        };

        let relative = resolved.strip_prefix(&self.root).ok()?;
        let text = relative.to_string_lossy().replace('\\', "/");
        if text.is_empty() {
            Some(ROOT_PATH.to_owned())
        } else {
            Some(text)
        }
    }
}

fn matches_rule(relative_path: &str, rule: &str) -> bool {
    if relative_path == ROOT_PATH {
        return false;
    }

    if let Some(prefix) = rule.strip_suffix("/**") {
        return matches_prefix(relative_path, prefix.trim_end_matches('/'));
    }

    if let Some(prefix) = rule.strip_suffix('/') {
        return matches_prefix(relative_path, prefix);
    }

    if !has_wildcards(rule) {
        return matches_prefix(relative_path, rule.trim_end_matches('/'));
    }

    glob_match(relative_path, rule)
}

fn matches_prefix(relative_path: &str, prefix: &str) -> bool {
    !prefix.is_empty()
        && (relative_path == prefix
            || relative_path
                .strip_prefix(prefix)
                .is_some_and(|suffix| suffix.starts_with('/')))
}

fn has_wildcards(rule: &str) -> bool {
    rule.contains('*') || rule.contains('?') || rule.contains('[')
}

fn normalize_absolute_path(path: &Path) -> PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    };
    normalize_components(&absolute)
}

fn normalize_components(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();

    for component in path.components() {
        match component {
            Component::Prefix(prefix) => normalized.push(prefix.as_os_str()),
            Component::RootDir => normalized.push(component.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                if !normalized.pop() {
                    normalized.push(component.as_os_str());
                }
            }
            Component::Normal(part) => normalized.push(part),
        }
    }

    normalized
}

fn glob_match(text: &str, pattern: &str) -> bool {
    let text_chars = text.chars().collect::<Vec<_>>();
    let pattern_chars = pattern.chars().collect::<Vec<_>>();
    let mut memo = vec![vec![None; pattern_chars.len() + 1]; text_chars.len() + 1];
    glob_match_inner(&text_chars, &pattern_chars, 0, 0, &mut memo)
}

fn glob_match_inner(
    text: &[char],
    pattern: &[char],
    text_index: usize,
    pattern_index: usize,
    memo: &mut [Vec<Option<bool>>],
) -> bool {
    if let Some(cached) = memo[text_index][pattern_index] {
        return cached;
    }

    let matched = if pattern_index == pattern.len() {
        text_index == text.len()
    } else {
        match pattern[pattern_index] {
            '*' => {
                let next_pattern = skip_stars(pattern, pattern_index);
                next_pattern == pattern.len()
                    || (text_index..=text.len()).any(|next_text| {
                        glob_match_inner(text, pattern, next_text, next_pattern, memo)
                    })
            }
            '?' => {
                text_index < text.len()
                    && glob_match_inner(text, pattern, text_index + 1, pattern_index + 1, memo)
            }
            '[' => {
                if text_index == text.len() {
                    false
                } else if let Some((class_match, next_pattern)) =
                    match_character_class(pattern, pattern_index, text[text_index])
                {
                    class_match
                        && glob_match_inner(text, pattern, text_index + 1, next_pattern, memo)
                } else {
                    text[text_index] == '['
                        && glob_match_inner(text, pattern, text_index + 1, pattern_index + 1, memo)
                }
            }
            expected => {
                text_index < text.len()
                    && text[text_index] == expected
                    && glob_match_inner(text, pattern, text_index + 1, pattern_index + 1, memo)
            }
        }
    };

    memo[text_index][pattern_index] = Some(matched);
    matched
}

fn skip_stars(pattern: &[char], mut index: usize) -> usize {
    while index < pattern.len() && pattern[index] == '*' {
        index += 1;
    }
    index
}

fn match_character_class(pattern: &[char], start: usize, current: char) -> Option<(bool, usize)> {
    let mut index = start + 1;
    let mut negate = false;
    let mut matched = false;
    let mut first = true;
    let mut previous_literal: Option<char> = None;

    if index < pattern.len() && matches!(pattern[index], '!' | '^') {
        negate = true;
        index += 1;
    }

    while index < pattern.len() {
        let token = pattern[index];
        if token == ']' && !first {
            return Some(((if negate { !matched } else { matched }), index + 1));
        }
        first = false;

        if token == '-'
            && previous_literal.is_some()
            && index + 1 < pattern.len()
            && pattern[index + 1] != ']'
        {
            let end = pattern[index + 1];
            let start = previous_literal.take().unwrap();
            if is_in_range(current, start, end) {
                matched = true;
            }
            index += 2;
            continue;
        }

        if token == current {
            matched = true;
        }
        previous_literal = Some(token);
        index += 1;
    }

    None
}

fn is_in_range(value: char, start: char, end: char) -> bool {
    if start <= end {
        start <= value && value <= end
    } else {
        end <= value && value <= start
    }
}
