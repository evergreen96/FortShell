use std::collections::BTreeMap;
use std::io;
use std::path::{Path, PathBuf};

use crate::filesystem_boundary::{
    helper_mutable_roots,
};
use crate::path_alias_guard::path_is_hardlink_alias_under_root;
use crate::path_policy::{
    path_targets_internal_workspace_root, path_traverses_reparse_point_under_root,
    path_traverses_workspace_reparse_point, path_uses_windows_alternate_data_stream,
    path_uses_windows_reserved_device_name, path_uses_windows_root_relative_form,
    relative_path_targets_internal_workspace_root,
};

#[derive(Clone, Debug, PartialEq, Eq)]
struct ShellToken {
    value: String,
    quoted: bool,
}

pub fn validate_child_argv_access(
    workspace: &Path,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    argv: &[String],
) -> io::Result<()> {
    let Some(program) = argv.first() else {
        return Ok(());
    };
    reject_nested_shell_program(program, "helper executable")?;
    if is_windows_unc_or_device_path(program) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("helper executable path must not use UNC or device form: {program}"),
        ));
    }
    if path_uses_windows_root_relative_form(program) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("helper executable path must not use root-relative form: {program}"),
        ));
    }
    if is_windows_drive_relative_path(program) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("helper executable path must not use drive-relative form: {program}"),
        ));
    }
    if path_uses_windows_reserved_device_name(Path::new(program)) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("helper executable path must not use Windows reserved device names: {program}"),
        ));
    }
    if looks_like_explicit_path(program)
        && path_uses_windows_alternate_data_stream(Path::new(program))
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("helper executable path must not use alternate data streams: {program}"),
        ));
    }
    if looks_like_explicit_path(program) {
        if !Path::new(program).is_absolute()
            && relative_path_targets_internal_workspace_root(Path::new(program))
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper executable path must not target internal workspace metadata: {program}"
                ),
            ));
        }
        let candidate = absolutize_program_path(cwd, Path::new(program));
        if path_targets_internal_workspace_root(workspace, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper executable path must not target internal workspace metadata: {}",
                    candidate.display()
                ),
            ));
        }
        if path_traverses_mutable_root_reparse_point(workspace, environment, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper executable path must not traverse workspace or helper-owned reparse points: {}",
                    candidate.display()
                ),
            ));
        }
        if path_is_hardlink_alias_under_any_allowed_root(workspace, environment, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper executable path must not use hardlink aliases under workspace or helper-owned roots: {}",
                    candidate.display()
                ),
            ));
        }
        let resolved = candidate
            .canonicalize()
            .unwrap_or_else(|_| candidate.clone());
        if path_is_under_helper_mutable_roots(environment, &resolved) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper executable path must not target helper-owned roots: {}",
                    resolved.display()
                ),
            ));
        }
    }
    let resolved_program = if looks_like_explicit_path(program) {
        let candidate = absolutize_program_path(cwd, Path::new(program));
        Some(strip_windows_local_device_prefix(
            &candidate.canonicalize().unwrap_or(candidate),
        ))
    } else {
        resolve_program_via_path_lookup(program, environment)
            .map(|resolved| strip_windows_local_device_prefix(&resolved))
    };
    if let Some(resolved_program) = resolved_program {
        if is_windows_shell_script_program(&resolved_program) {
            if !looks_like_explicit_path(program) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!(
                        "helper executable must use explicit workspace batch-script paths in strict helper mode: {}",
                        resolved_program.display()
                    ),
                ));
            }
            if shell_script_tokens_contain_unsafe_batch_arguments(argv) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "helper executable must not pass unsafe batch arguments in strict helper mode",
                ));
            }
        }
    }

    for argument in argv.iter().skip(1) {
        let candidate_text = candidate_path_text_from_argument(argument).unwrap_or(argument);
        if is_windows_unc_or_device_path(candidate_text) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper argv path must not use UNC or device form: {argument}"),
            ));
        }
        if path_uses_windows_root_relative_form(candidate_text) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper argv path must not use root-relative form: {argument}"),
            ));
        }
        if is_windows_drive_relative_path(candidate_text) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper argv path must not use drive-relative form: {argument}"),
            ));
        }
        if path_uses_windows_reserved_device_name(Path::new(candidate_text)) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper argv path must not use Windows reserved device names: {argument}"),
            ));
        }
        if looks_like_explicit_path(candidate_text)
            && path_uses_windows_alternate_data_stream(Path::new(candidate_text))
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper argv path must not use alternate data streams: {argument}"),
            ));
        }
        let Some(candidate) = explicit_candidate_path_argument(cwd, argument) else {
            continue;
        };
        if !Path::new(candidate_text).is_absolute()
            && relative_path_targets_internal_workspace_root(Path::new(candidate_text))
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("helper argv path must not target internal workspace metadata: {argument}"),
            ));
        }
        if path_targets_internal_workspace_root(workspace, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper argv path must not target internal workspace metadata: {}",
                    candidate.display()
                ),
            ));
        }
        if path_traverses_mutable_root_reparse_point(workspace, environment, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper argv path must not traverse workspace or helper-owned reparse points: {}",
                    candidate.display()
                ),
            ));
        }
        if path_is_hardlink_alias_under_any_allowed_root(workspace, environment, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper argv path must not use hardlink aliases under workspace or helper-owned roots: {}",
                    candidate.display()
                ),
            ));
        }
        let resolved = candidate
            .canonicalize()
            .unwrap_or_else(|_| candidate.clone());
        if path_is_under_helper_mutable_roots(environment, &resolved) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "helper argv path must not target helper-owned roots: {}",
                    resolved.display()
                ),
            ));
        }
    }
    Ok(())
}

pub fn validate_shell_command_text(
    workspace: &Path,
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    command: &str,
) -> io::Result<()> {
    if contains_unterminated_shell_quote(command) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "shell command must not use unterminated quotes in strict helper mode",
        ));
    }
    if contains_unquoted_shell_escape(command) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "shell command must not use cmd escape syntax (^) in strict helper mode",
        ));
    }
    let command_tokens = shell_command_tokens(command);
    let command_index = if command_tokens
        .first()
        .is_some_and(|token| token.eq_ignore_ascii_case("call"))
    {
        1
    } else {
        0
    };
    if let Some(invoked) = command_tokens.get(command_index) {
        if is_disallowed_shell_state_builtin(invoked) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "shell command must not use stateful shell builtins in strict helper mode: {invoked}"
                ),
            ));
        }
    }
    if let Some(operator) = first_shell_control_operator(command) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "shell command must not use shell control operators in strict helper mode: {operator}"
            ),
        ));
    }
    for token in shell_command_tokens(command) {
        let token_text = expand_shell_path_token(
            token.trim_matches(|ch| ch == '"' || ch == '\''),
            environment,
        )?;
        let candidate_text = candidate_path_text_from_argument(&token_text).unwrap_or(&token_text);
        if candidate_text.is_empty() || candidate_text.starts_with(crate::HELPER_HOST_PATH_SCHEME) {
            continue;
        }
        if is_windows_unc_or_device_path(&candidate_text) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("shell command path must not use UNC or device form: {candidate_text}"),
            ));
        }
        if path_uses_windows_root_relative_form(&candidate_text) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("shell command path must not use root-relative form: {candidate_text}"),
            ));
        }
        if is_windows_drive_relative_path(&candidate_text) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("shell command path must not use drive-relative form: {candidate_text}"),
            ));
        }
        if path_uses_windows_reserved_device_name(Path::new(&candidate_text)) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "shell command path must not use Windows reserved device names: {candidate_text}"
                ),
            ));
        }
        if looks_like_shell_path_literal(&candidate_text)
            && path_uses_windows_alternate_data_stream(Path::new(&candidate_text))
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("shell command path must not use alternate data streams: {candidate_text}"),
            ));
        }
        if contains_parent_escape(&candidate_text) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "shell command path must not escape helper roots with parent traversal: {candidate_text}"
                ),
            ));
        }
        let Some(candidate) = shell_candidate_path(cwd, &candidate_text) else {
            continue;
        };
        if !Path::new(&candidate_text).is_absolute()
            && relative_path_targets_internal_workspace_root(Path::new(&candidate_text))
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "shell command path must not target internal workspace metadata: {}",
                    candidate.display()
                ),
            ));
        }
        if path_targets_internal_workspace_root(workspace, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "shell command path must not target internal workspace metadata: {}",
                    candidate.display()
                ),
            ));
        }
        if path_traverses_mutable_root_reparse_point(workspace, environment, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "shell command path must not traverse workspace or helper-owned reparse points: {}",
                    candidate.display()
                ),
            ));
        }
        if path_is_hardlink_alias_under_any_allowed_root(workspace, environment, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "shell command path must not use hardlink aliases under workspace or helper-owned roots: {}",
                    candidate.display()
                ),
            ));
        }
        if path_is_under_helper_mutable_roots(environment, &candidate) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "shell command path must not target helper-owned roots: {}",
                    candidate.display()
                ),
            ));
        }
    }

    for segment in shell_command_segment_tokens(command) {
        let command_index = if segment
            .first()
            .is_some_and(|token| token.value.eq_ignore_ascii_case("call"))
        {
            1
        } else {
            0
        };
        if let Some(token) = segment.get(command_index) {
            if shell_token_uses_environment_expansion(&token.value) {
                let expanded = expand_shell_path_token(&token.value, environment)?;
                if expanded_shell_token_requires_shell_mode(token, &expanded) {
                    return Err(io::Error::new(
                        io::ErrorKind::PermissionDenied,
                        "shell command invoked program must not expand into shell-sensitive syntax in strict helper mode",
                    ));
                }
            }
        }
        let segment_values = segment
            .iter()
            .map(|token| token.value.clone())
            .collect::<Vec<_>>();
        if segment_values
            .first()
            .is_some_and(|token| token.trim_start().starts_with('@'))
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "shell command must not use command echo suppression prefix in strict helper mode",
            ));
        }
        if segment_values
            .first()
            .is_some_and(|token| token.eq_ignore_ascii_case("call"))
            && shell_segment_invoked_program(&segment_values).is_some_and(is_allowed_shell_builtin)
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "shell command must not wrap helper-local builtins with call in strict helper mode",
            ));
        }
        let expanded_segment = segment_values
            .iter()
            .map(|token| {
                expand_shell_path_token(
                    token.trim_matches(|ch| ch == '"' || ch == '\''),
                    environment,
                )
            })
            .collect::<io::Result<Vec<_>>>()?;
        let command_index = if expanded_segment
            .first()
            .is_some_and(|token| token.eq_ignore_ascii_case("call"))
        {
            1
        } else {
            0
        };
        if let Some(invoked) = shell_segment_invoked_program(&expanded_segment) {
            if invoked.eq_ignore_ascii_case("start") {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "shell command must not use start in strict helper mode",
                ));
            }
            if is_batch_label_reference(invoked) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "shell command must not use batch-label control flow in strict helper mode",
                ));
            }
            reject_nested_shell_program(invoked, "shell command")?;
            if is_disallowed_shell_state_builtin(invoked) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!(
                        "shell command must not use stateful shell builtins in strict helper mode: {invoked}"
                    ),
                ));
            }
            if is_disallowed_shell_filesystem_builtin(invoked) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!(
                        "shell command must not use filesystem builtins in strict helper mode: {invoked}"
                    ),
                ));
            }
            if is_allowed_shell_builtin(invoked) {
                continue;
            }
            if shell_segment_arguments_expand_to_control_operators(
                &segment,
                &expanded_segment,
                command_index,
            ) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "shell command arguments must not expand into shell control operators in strict helper mode",
                ));
            }
            let Some(resolved) = resolve_shell_invoked_program(cwd, environment, invoked) else {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!(
                        "shell command must resolve invoked program under helper-owned PATH roots: {invoked}"
                    ),
                ));
            };
            if is_windows_shell_script_program(&resolved) {
                if !looks_like_explicit_path(&expanded_segment[command_index]) {
                    return Err(io::Error::new(
                        io::ErrorKind::PermissionDenied,
                        format!(
                            "shell command must use explicit workspace batch-script paths in strict helper mode: {}",
                            resolved.display()
                        ),
                    ));
                }
                if shell_script_tokens_contain_unsafe_batch_arguments(
                    &expanded_segment[command_index..],
                ) {
                    return Err(io::Error::new(
                        io::ErrorKind::PermissionDenied,
                        "shell command must not pass unsafe batch arguments in strict helper mode",
                    ));
                }
            } else if !is_windows_native_direct_launch_program(&resolved) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!(
                        "shell command must not rely on file-association or shell execution for non-batch scripts: {}",
                        resolved.display()
                    ),
                ));
            }
        }
    }
    Ok(())
}

pub fn direct_argv_candidate_for_shell_command(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    command: &str,
) -> Option<Vec<String>> {
    if contains_unterminated_shell_quote(command) {
        return None;
    }
    if contains_unquoted_shell_escape(command) {
        return None;
    }
    if first_shell_control_operator(command).is_some() {
        return None;
    }
    let mut segments = shell_command_segment_tokens(command);
    if segments.len() != 1 {
        return None;
    }
    let mut segment = segments.pop()?;
    if segment.is_empty() {
        return None;
    }
    let mut command_index = 0usize;
    let invoked = shell_segment_invoked_program(
        &segment
            .iter()
            .map(|token| token.value.clone())
            .collect::<Vec<_>>(),
    )?
    .to_string();
    if segment
        .first()
        .is_some_and(|token| token.value.eq_ignore_ascii_case("call"))
    {
        command_index = 1;
    }
    let expanded_invoked = if shell_token_uses_environment_expansion(&invoked) {
        let expanded = expand_shell_path_token(&invoked, environment).ok()?;
        if expanded_shell_token_requires_shell_mode(&segment[command_index], &expanded) {
            return None;
        }
        expanded
    } else {
        invoked.clone()
    };
    for token in segment.iter_mut().skip(command_index + 1) {
        if !shell_token_uses_environment_expansion(&token.value) {
            continue;
        }
        let expanded = expand_shell_path_token(&token.value, environment).ok()?;
        if expanded_shell_token_requires_shell_mode(token, &expanded) {
            return None;
        }
        token.value = expanded;
    }
    if is_allowed_shell_builtin(&expanded_invoked)
        || is_disallowed_shell_state_builtin(&expanded_invoked)
        || is_disallowed_shell_filesystem_builtin(&expanded_invoked)
    {
        return None;
    }
    segment[command_index].value =
        resolve_direct_program_for_shell_command(cwd, environment, &expanded_invoked)?;
    if command_index > 0 {
        segment.remove(0);
    }
    Some(segment.into_iter().map(|token| token.value).collect())
}

pub fn helper_echo_output_for_shell_command(
    environment: &BTreeMap<String, String>,
    command: &str,
) -> Option<String> {
    if contains_unterminated_shell_quote(command) {
        return None;
    }
    if contains_unquoted_shell_escape(command) {
        return None;
    }
    if first_shell_control_operator(command).is_some() {
        return None;
    }
    let mut segments = shell_command_segments(command);
    if segments.len() != 1 {
        return None;
    }
    let segment = segments.pop()?;
    if segment
        .first()
        .is_some_and(|token| token.eq_ignore_ascii_case("call"))
    {
        return None;
    }
    let invoked = shell_segment_invoked_program(&segment)?;
    if !invoked.eq_ignore_ascii_case("echo") || segment.len() <= 1 {
        return None;
    }
    let expanded = segment
        .iter()
        .skip(1)
        .map(|token| expand_shell_path_token(token, environment))
        .collect::<io::Result<Vec<_>>>()
        .ok()?;
    Some(expanded.join(" "))
}

pub fn structured_shell_script_candidate_for_shell_command(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    command: &str,
) -> Option<Vec<String>> {
    if contains_unterminated_shell_quote(command) {
        return None;
    }
    if contains_unquoted_shell_escape(command) {
        return None;
    }
    if first_shell_control_operator(command).is_some() {
        return None;
    }
    let mut segments = shell_command_segment_tokens(command);
    if segments.len() != 1 {
        return None;
    }
    let segment = segments.pop()?;
    if segment.is_empty() {
        return None;
    }
    let mut command_index = 0usize;
    if segment
        .first()
        .is_some_and(|token| token.value.eq_ignore_ascii_case("call"))
    {
        command_index = 1;
    }
    let invoked_token = segment.get(command_index)?;
    let invoked = if shell_token_uses_environment_expansion(&invoked_token.value) {
        let expanded = expand_shell_path_token(&invoked_token.value, environment).ok()?;
        if expanded_shell_token_requires_shell_mode(invoked_token, &expanded) {
            return None;
        }
        expanded
    } else {
        invoked_token.value.clone()
    };
    let expanded_segment = segment
        .iter()
        .map(|token| expand_shell_path_token(&token.value, environment))
        .collect::<io::Result<Vec<_>>>()
        .ok()?;
    if invoked.eq_ignore_ascii_case("start") || is_allowed_shell_builtin(&invoked) {
        return None;
    }
    if !looks_like_explicit_path(&expanded_segment[command_index]) {
        return None;
    }
    if shell_script_tokens_contain_unsafe_batch_arguments(&expanded_segment[command_index..]) {
        return None;
    }
    let mut candidate = expanded_segment;
    let resolved = resolve_structured_shell_script_for_shell_command(cwd, environment, &invoked)?;
    candidate[command_index] = resolved;
    if command_index > 0 {
        candidate.remove(0);
    }
    Some(candidate)
}

fn expand_shell_path_token(
    token: &str,
    environment: &BTreeMap<String, String>,
) -> io::Result<String> {
    if !token.contains('%') {
        return Ok(token.to_string());
    }
    let mut output = String::new();
    let chars = token.chars().collect::<Vec<_>>();
    let mut index = 0usize;
    while index < chars.len() {
        let delimiter = chars[index];
        if delimiter != '%' {
            output.push(delimiter);
            index += 1;
            continue;
        }
        let mut end = index + 1;
        while end < chars.len() && chars[end] != delimiter {
            end += 1;
        }
        if end >= chars.len() {
            output.push(delimiter);
            index += 1;
            continue;
        }
        let name = chars[index + 1..end].iter().collect::<String>();
        let Some(value) = environment_lookup(environment, &name) else {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("shell command path uses unknown environment reference: {name}"),
            ));
        };
        output.push_str(&value);
        index = end + 1;
    }
    Ok(output)
}

fn looks_like_explicit_path(program: &str) -> bool {
    let path = Path::new(program);
    path.is_absolute()
        || is_windows_unc_or_device_path(program)
        || program.contains('\\')
        || program.contains('/')
}

fn reject_nested_shell_program(program: &str, context: &str) -> io::Result<()> {
    if is_nested_shell_program(program) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{context} must not launch nested shell programs: {program}"),
        ));
    }
    Ok(())
}

fn looks_like_shell_path_literal(token: &str) -> bool {
    let path = Path::new(token);
    path.is_absolute()
        || is_windows_unc_or_device_path(token)
        || is_windows_drive_relative_path(token)
        || token.starts_with(".\\")
        || token.starts_with("./")
        || token.contains('\\')
        || token[1..].contains('/')
}

fn is_windows_unc_or_device_path(token: &str) -> bool {
    token.starts_with("\\\\")
        || token.starts_with("//")
        || token.starts_with(r"\??\")
        || token.starts_with(r"\\?\")
        || token.starts_with(r"\\.\")
}

fn is_windows_drive_relative_path(token: &str) -> bool {
    let bytes = token.as_bytes();
    if bytes.len() < 3 || bytes[1] != b':' || !bytes[0].is_ascii_alphabetic() {
        return false;
    }
    let separator = bytes[2];
    separator != b'\\' && separator != b'/'
}

fn absolutize_program_path(cwd: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        cwd.join(path)
    }
}

fn explicit_candidate_path_argument(cwd: &Path, argument: &str) -> Option<PathBuf> {
    let candidate_text = candidate_path_text_from_argument(argument)?;
    let candidate = absolutize_program_path(cwd, Path::new(candidate_text));
    if looks_like_explicit_path(candidate_text)
        || relative_path_targets_internal_workspace_root(Path::new(candidate_text))
        || candidate.exists()
    {
        return Some(candidate);
    }
    None
}

fn candidate_path_text_from_argument(argument: &str) -> Option<&str> {
    if let Some(value) = response_file_value(argument) {
        return Some(value);
    }
    if let Some(value) = attached_option_value(argument) {
        return Some(value);
    }
    if argument.starts_with('-') {
        return None;
    }
    Some(argument)
}

fn response_file_value(argument: &str) -> Option<&str> {
    let value = trim_wrapping_quotes(argument.strip_prefix('@')?);
    if value.is_empty() || value.contains("://") {
        return None;
    }
    Some(value)
}

fn attached_option_value(argument: &str) -> Option<&str> {
    let trimmed = if let Some(value) = argument.strip_prefix("--") {
        value
    } else if let Some(value) = argument.strip_prefix('-') {
        value
    } else if let Some(value) = argument.strip_prefix('/') {
        value
    } else {
        return None;
    };
    if trimmed.is_empty() {
        return None;
    }
    let separator_index = trimmed.find(['=', ':'])?;
    let value = trim_wrapping_quotes(trimmed.get(separator_index + 1..)?);
    if value.is_empty() || value.contains("://") {
        return None;
    }
    Some(value)
}

fn trim_wrapping_quotes(value: &str) -> &str {
    if value.len() >= 2 {
        let bytes = value.as_bytes();
        let first = bytes[0];
        let last = bytes[value.len() - 1];
        if (first == b'"' && last == b'"') || (first == b'\'' && last == b'\'') {
            return &value[1..value.len() - 1];
        }
    }
    value
}

fn shell_candidate_path(cwd: &Path, token: &str) -> Option<PathBuf> {
    let candidate = absolutize_program_path(cwd, Path::new(token));
    if looks_like_shell_path_literal(token)
        || relative_path_targets_internal_workspace_root(Path::new(token))
        || candidate.exists()
    {
        return Some(candidate);
    }
    None
}

fn shell_command_segments(command: &str) -> Vec<Vec<String>> {
    let mut segments = Vec::new();
    let mut current_segment = Vec::new();
    let mut buffer = String::new();
    let mut quote: Option<char> = None;
    for ch in command.chars() {
        match quote {
            Some(delimiter) => {
                if ch == delimiter {
                    quote = None;
                } else {
                    buffer.push(ch);
                }
            }
            None => match ch {
                '"' | '\'' => quote = Some(ch),
                ' ' | '\t' => {
                    if !buffer.is_empty() {
                        current_segment.push(std::mem::take(&mut buffer));
                    }
                }
                '\r' | '\n' | '|' | '&' | ';' | '<' | '>' | '(' | ')' => {
                    if !buffer.is_empty() {
                        current_segment.push(std::mem::take(&mut buffer));
                    }
                    if !current_segment.is_empty() {
                        segments.push(std::mem::take(&mut current_segment));
                    }
                }
                _ => buffer.push(ch),
            },
        }
    }
    if !buffer.is_empty() {
        current_segment.push(buffer);
    }
    if !current_segment.is_empty() {
        segments.push(current_segment);
    }
    segments
}

fn shell_command_segment_tokens(command: &str) -> Vec<Vec<ShellToken>> {
    let mut segments = Vec::new();
    let mut current_segment = Vec::new();
    let mut buffer = String::new();
    let mut buffer_quoted = false;
    let mut quote: Option<char> = None;
    for ch in command.chars() {
        match quote {
            Some(delimiter) => {
                if ch == delimiter {
                    quote = None;
                } else {
                    buffer.push(ch);
                }
            }
            None => match ch {
                '"' | '\'' => {
                    quote = Some(ch);
                    buffer_quoted = true;
                }
                ' ' | '\t' => {
                    if !buffer.is_empty() {
                        current_segment.push(ShellToken {
                            value: std::mem::take(&mut buffer),
                            quoted: buffer_quoted,
                        });
                        buffer_quoted = false;
                    }
                }
                '\r' | '\n' | '|' | '&' | ';' | '<' | '>' | '(' | ')' => {
                    if !buffer.is_empty() {
                        current_segment.push(ShellToken {
                            value: std::mem::take(&mut buffer),
                            quoted: buffer_quoted,
                        });
                        buffer_quoted = false;
                    }
                    if !current_segment.is_empty() {
                        segments.push(std::mem::take(&mut current_segment));
                    }
                }
                _ => buffer.push(ch),
            },
        }
    }
    if !buffer.is_empty() {
        current_segment.push(ShellToken {
            value: buffer,
            quoted: buffer_quoted,
        });
    }
    if !current_segment.is_empty() {
        segments.push(current_segment);
    }
    segments
}

fn shell_token_uses_environment_expansion(token: &str) -> bool {
    token.contains('%')
}

fn expanded_shell_token_requires_shell_mode(token: &ShellToken, expanded: &str) -> bool {
    expanded.contains(['\t', '\r', '\n', '"', '\''])
        || first_shell_control_operator(expanded).is_some()
        || (expanded.contains(' ') && !token.quoted)
}

fn shell_segment_arguments_expand_to_control_operators(
    segment: &[ShellToken],
    expanded_segment: &[String],
    command_index: usize,
) -> bool {
    segment
        .iter()
        .zip(expanded_segment.iter())
        .skip(command_index + 1)
        .any(|(token, expanded)| {
            shell_token_uses_environment_expansion(&token.value)
                && first_shell_control_operator(expanded).is_some()
        })
}

fn first_shell_control_operator(command: &str) -> Option<&'static str> {
    let chars = command.chars().collect::<Vec<_>>();
    let mut quote: Option<char> = None;
    let mut index = 0usize;
    while index < chars.len() {
        let ch = chars[index];
        match quote {
            Some(delimiter) => {
                if ch == delimiter {
                    quote = None;
                }
                index += 1;
            }
            None => match ch {
                '"' | '\'' => {
                    quote = Some(ch);
                    index += 1;
                }
                '&' => {
                    if chars.get(index + 1) == Some(&'&') {
                        return Some("&&");
                    }
                    return Some("&");
                }
                '|' => {
                    if chars.get(index + 1) == Some(&'|') {
                        return Some("||");
                    }
                    return Some("|");
                }
                ';' => return Some(";"),
                '<' => {
                    if chars.get(index + 1) == Some(&'<') {
                        return Some("<<");
                    }
                    return Some("<");
                }
                '>' => {
                    if chars.get(index + 1) == Some(&'>') {
                        return Some(">>");
                    }
                    return Some(">");
                }
                '(' => return Some("("),
                ')' => return Some(")"),
                _ => index += 1,
            },
        }
    }
    None
}

fn contains_unquoted_shell_escape(command: &str) -> bool {
    let mut quote: Option<char> = None;
    for ch in command.chars() {
        match quote {
            Some(delimiter) => {
                if ch == delimiter {
                    quote = None;
                }
            }
            None => match ch {
                '"' | '\'' => quote = Some(ch),
                '^' => return true,
                _ => {}
            },
        }
    }
    false
}

fn contains_unterminated_shell_quote(command: &str) -> bool {
    let mut quote: Option<char> = None;
    for ch in command.chars() {
        match quote {
            Some(delimiter) => {
                if ch == delimiter {
                    quote = None;
                }
            }
            None => {
                if matches!(ch, '"' | '\'') {
                    quote = Some(ch);
                }
            }
        }
    }
    quote.is_some()
}

fn shell_segment_invoked_program<'a>(segment: &'a [String]) -> Option<&'a str> {
    let mut tokens = segment.iter().map(String::as_str);
    let first = tokens.next()?;
    if first.eq_ignore_ascii_case("call") {
        return tokens.next();
    }
    Some(first)
}

fn is_nested_shell_program(program: &str) -> bool {
    let normalized = normalize_program_name(program);
    matches!(
        normalized.as_str(),
        "cmd"
            | "cmd.exe"
            | "command"
            | "command.com"
            | "powershell"
            | "powershell.exe"
            | "pwsh"
            | "pwsh.exe"
            | "wsl"
            | "wsl.exe"
            | "bash"
            | "bash.exe"
            | "sh"
            | "sh.exe"
            | "zsh"
            | "zsh.exe"
            | "fish"
            | "fish.exe"
    )
}

fn normalize_program_name(program: &str) -> String {
    Path::new(program)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(program)
        .to_ascii_lowercase()
}

fn is_batch_label_reference(program: &str) -> bool {
    let trimmed = program.trim();
    trimmed.len() > 1 && trimmed.starts_with(':') && !looks_like_explicit_path(trimmed)
}

fn is_allowed_shell_builtin(program: &str) -> bool {
    matches!(normalize_program_name(program).as_str(), "echo")
}

fn is_disallowed_shell_state_builtin(program: &str) -> bool {
    matches!(
        normalize_program_name(program).as_str(),
        "assoc"
            | "break"
            | "cd"
            | "chcp"
            | "chdir"
            | "cls"
            | "color"
            | "date"
            | "dpath"
            | "endlocal"
            | "exit"
            | "ftype"
            | "for"
            | "goto"
            | "if"
            | "path"
            | "pause"
            | "popd"
            | "prompt"
            | "pushd"
            | "set"
            | "setlocal"
            | "shift"
            | "time"
            | "title"
            | "verify"
    )
}

fn is_disallowed_shell_filesystem_builtin(program: &str) -> bool {
    matches!(
        normalize_program_name(program).as_str(),
        "copy"
            | "del"
            | "erase"
            | "dir"
            | "mklink"
            | "md"
            | "mkdir"
            | "move"
            | "rd"
            | "ren"
            | "rename"
            | "rmdir"
            | "type"
            | "vol"
    )
}

fn resolve_direct_program_for_shell_command(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    invoked: &str,
) -> Option<String> {
    if let Some(candidate) = shell_candidate_path(cwd, invoked) {
        let normalized = strip_windows_local_device_prefix(&candidate);
        if is_windows_native_direct_launch_program(&normalized) {
            return Some(normalized.to_string_lossy().to_string());
        }
        return None;
    }
    let resolved =
        strip_windows_local_device_prefix(&resolve_program_via_path_lookup(invoked, environment)?);
    if is_windows_native_direct_launch_program(&resolved) {
        return Some(resolved.to_string_lossy().to_string());
    }
    None
}

fn resolve_structured_shell_script_for_shell_command(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    invoked: &str,
) -> Option<String> {
    if let Some(candidate) = shell_candidate_path(cwd, invoked) {
        let normalized = strip_windows_local_device_prefix(&candidate);
        if is_windows_shell_script_program(&normalized) {
            return Some(normalized.to_string_lossy().to_string());
        }
        return None;
    }
    let resolved =
        strip_windows_local_device_prefix(&resolve_program_via_path_lookup(invoked, environment)?);
    if is_windows_shell_script_program(&resolved) {
        return Some(resolved.to_string_lossy().to_string());
    }
    None
}

fn resolve_shell_invoked_program(
    cwd: &Path,
    environment: &BTreeMap<String, String>,
    invoked: &str,
) -> Option<PathBuf> {
    if let Some(candidate) = shell_candidate_path(cwd, invoked) {
        return Some(strip_windows_local_device_prefix(&candidate));
    }
    resolve_program_via_path_lookup(invoked, environment)
        .map(|resolved| strip_windows_local_device_prefix(&resolved))
}

fn is_windows_native_direct_launch_program(path: &Path) -> bool {
    matches!(
        path.extension()
            .and_then(|value| value.to_str())
            .map(|value| value.to_ascii_lowercase())
            .as_deref(),
        Some("exe") | Some("com")
    )
}

fn is_windows_shell_script_program(path: &Path) -> bool {
    matches!(
        path.extension()
            .and_then(|value| value.to_str())
            .map(|value| value.to_ascii_lowercase())
            .as_deref(),
        Some("cmd") | Some("bat")
    )
}

fn strip_windows_local_device_prefix(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(stripped) = text.strip_prefix(r"\\?\") {
        if stripped.as_bytes().get(1) == Some(&b':') {
            return PathBuf::from(stripped);
        }
    }
    if let Some(stripped) = text.strip_prefix(r"\??\") {
        if stripped.as_bytes().get(1) == Some(&b':') {
            return PathBuf::from(stripped);
        }
    }
    path.to_path_buf()
}

fn shell_script_tokens_contain_unsafe_batch_arguments(tokens: &[String]) -> bool {
    tokens.iter().skip(1).any(|token| {
        token.contains('%')
            || token.contains('!')
            || token.contains('&')
            || token.contains('|')
            || token.contains('<')
            || token.contains('>')
            || token.contains('(')
            || token.contains(')')
            || token.contains('^')
    })
}

fn path_traverses_mutable_root_reparse_point(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    candidate: &Path,
) -> bool {
    if path_traverses_workspace_reparse_point(workspace, candidate) {
        return true;
    }
    for root in helper_mutable_roots(environment) {
        if path_traverses_reparse_point_under_root(&root, candidate) {
            return true;
        }
    }
    false
}

fn path_is_hardlink_alias_under_any_allowed_root(
    workspace: &Path,
    environment: &BTreeMap<String, String>,
    candidate: &Path,
) -> bool {
    if path_is_hardlink_alias_under_root(workspace, candidate) {
        return true;
    }
    for root in helper_mutable_roots(environment) {
        if path_is_hardlink_alias_under_root(&root, candidate) {
            return true;
        }
    }
    false
}

fn path_is_under_helper_mutable_roots(
    environment: &BTreeMap<String, String>,
    candidate: &Path,
) -> bool {
    helper_mutable_roots(environment)
        .into_iter()
        .any(|root| candidate.starts_with(root))
}

fn contains_parent_escape(token: &str) -> bool {
    token == ".." || token.contains("..\\") || token.contains("../")
}

fn environment_lookup(environment: &BTreeMap<String, String>, name: &str) -> Option<String> {
    environment
        .iter()
        .find(|(key, _)| key.eq_ignore_ascii_case(name))
        .map(|(_, value)| value.clone())
}

fn resolve_program_via_path_lookup(
    program: &str,
    environment: &BTreeMap<String, String>,
) -> Option<PathBuf> {
    let Some(path_value) = environment
        .get("PATH")
        .or_else(|| environment.get("Path"))
        .or_else(|| environment.get("path"))
    else {
        return None;
    };
    let extensions = path_lookup_extensions(environment);
    for directory in std::env::split_paths(path_value) {
        for candidate in path_lookup_candidates(&directory, program, &extensions) {
            if candidate.exists() {
                return Some(candidate.canonicalize().unwrap_or(candidate));
            }
        }
    }
    None
}

fn path_lookup_candidates(directory: &Path, program: &str, extensions: &[String]) -> Vec<PathBuf> {
    let base = directory.join(program);
    if Path::new(program).extension().is_some() {
        return vec![base];
    }
    let mut candidates = vec![base.clone()];
    for extension in extensions {
        candidates.push(directory.join(format!("{program}{extension}")));
    }
    candidates
}

fn path_lookup_extensions(environment: &BTreeMap<String, String>) -> Vec<String> {
    environment
        .get("PATHEXT")
        .or_else(|| environment.get("Pathext"))
        .or_else(|| environment.get("pathext"))
        .map(|value| {
            value
                .split(';')
                .filter_map(|part| {
                    let trimmed = part.trim();
                    if trimmed.is_empty() {
                        None
                    } else if trimmed.starts_with('.') {
                        Some(trimmed.to_ascii_lowercase())
                    } else {
                        Some(format!(".{}", trimmed.to_ascii_lowercase()))
                    }
                })
                .collect()
        })
        .unwrap_or_else(|| {
            vec![
                ".com".to_string(),
                ".exe".to_string(),
                ".bat".to_string(),
                ".cmd".to_string(),
            ]
        })
}

fn shell_command_tokens(command: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut buffer = String::new();
    let mut quote: Option<char> = None;
    for ch in command.chars() {
        match quote {
            Some(delimiter) => {
                if ch == delimiter {
                    quote = None;
                } else {
                    buffer.push(ch);
                }
            }
            None => match ch {
                '"' | '\'' => quote = Some(ch),
                ' ' | '\t' | '\r' | '\n' | '|' | '&' | ';' | '<' | '>' | '(' | ')' => {
                    if !buffer.is_empty() {
                        tokens.push(std::mem::take(&mut buffer));
                    }
                }
                _ => buffer.push(ch),
            },
        }
    }
    if !buffer.is_empty() {
        tokens.push(buffer);
    }
    tokens
}

#[cfg(test)]
mod tests {
    use super::{
        direct_argv_candidate_for_shell_command, first_shell_control_operator,
        helper_echo_output_for_shell_command, shell_command_segments, shell_command_tokens,
        structured_shell_script_candidate_for_shell_command, validate_child_argv_access,
        validate_shell_command_text,
    };
    use std::collections::BTreeMap;
    use std::io;
    use std::path::{Path, PathBuf};

    #[test]
    fn bare_program_names_are_allowed() {
        let env = BTreeMap::new();
        let result = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &env,
            &["python".to_string()],
        );
        assert!(result.is_ok());
    }

    #[test]
    fn workspace_relative_programs_are_allowed() {
        let mut env = BTreeMap::new();
        let workspace = std::env::temp_dir().join("helper-workspace-root");
        let home = std::env::temp_dir().join("helper-home-root");
        env.insert("HOME".to_string(), home.display().to_string());
        let result = validate_child_argv_access(
            &workspace,
            &workspace,
            &env,
            &["tools/runner.cmd".to_string()],
        );
        assert!(result.is_ok());
    }

    #[test]
    fn explicit_paths_outside_allowed_roots_are_allowed() {
        let mut env = BTreeMap::new();
        let base = std::env::temp_dir();
        let workspace = base.join("helper-workspace-root");
        env.insert(
            "HOME".to_string(),
            base.join("helper-home-root").display().to_string(),
        );
        env.insert(
            "TMPDIR".to_string(),
            base.join("helper-tmp-root").display().to_string(),
        );
        env.insert(
            "XDG_CACHE_HOME".to_string(),
            base.join("helper-cache-root").display().to_string(),
        );
        let outside = base.join("outside-workspace").join("evil.cmd");

        validate_child_argv_access(
            &workspace,
            &workspace,
            &env,
            &[outside.display().to_string()],
        )
        .unwrap();
    }

    #[test]
    fn explicit_paths_matching_path_lookup_are_allowed() {
        let base = std::env::temp_dir().join(format!("helper-path-allow-{}", std::process::id()));
        std::fs::create_dir_all(&base).unwrap();
        let tool_path = base.join("tool.exe");
        std::fs::write(&tool_path, b"").unwrap();

        let mut env = BTreeMap::new();
        env.insert("PATH".to_string(), base.display().to_string());

        let result = validate_child_argv_access(
            &std::env::temp_dir().join("workspace-root"),
            &std::env::temp_dir().join("workspace-root"),
            &env,
            &[tool_path.display().to_string()],
        );

        assert!(result.is_ok());
        let _ = std::fs::remove_file(&tool_path);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn explicit_workspace_batch_program_rejects_unsafe_batch_arguments() {
        let base =
            std::env::temp_dir().join(format!("helper-argv-batch-unsafe-{}", std::process::id()));
        let workspace = base.join("workspace");
        std::fs::create_dir_all(workspace.join("tools")).unwrap();
        let tool = workspace.join("tools").join("runner.cmd");
        std::fs::write(&tool, b"@echo off\r\necho helper\r\n").unwrap();

        let error = validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &[tool.display().to_string(), "%WORKSPACE_FLAG%".to_string()],
        )
        .expect_err("batch argv should reject unsafe batch arguments");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("unsafe batch arguments"));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn bare_batch_program_resolved_from_workspace_path_is_rejected_in_argv_mode() {
        let workspace =
            std::env::temp_dir().join(format!("helper-argv-batch-path-{}", std::process::id()));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("helper-tool.cmd");
        std::fs::write(&tool, b"@echo off\r\necho helper-tool\r\n").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".CMD;.EXE".to_string()),
        ]);

        let error =
            validate_child_argv_access(&workspace, &workspace, &env, &["helper-tool".to_string()])
                .expect_err("bare batch script should be rejected in argv mode");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(
            error
                .to_string()
                .contains("explicit workspace batch-script paths")
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn explicit_existing_script_argument_outside_allowed_roots_is_allowed() {
        let base =
            std::env::temp_dir().join(format!("helper-argv-path-reject-{}", std::process::id()));
        let workspace = base.join("workspace");
        let outside = base.join("outside");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        let outside_script = outside.join("script.py");
        std::fs::write(&outside_script, b"print('outside')\n").unwrap();

        validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &["python".to_string(), outside_script.display().to_string()],
        )
        .unwrap();
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn explicit_existing_script_argument_under_internal_workspace_root_is_rejected() {
        let base = std::env::temp_dir().join(format!(
            "helper-argv-internal-reject-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let internal = workspace.join(".ai_ide_runtime");
        std::fs::create_dir_all(&internal).unwrap();
        let internal_script = internal.join("script.py");
        std::fs::write(&internal_script, b"print('hidden')\n").unwrap();

        let error = validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &["python".to_string(), internal_script.display().to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("internal workspace metadata"));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn bare_existing_script_argument_under_internal_workspace_root_is_rejected() {
        let base = std::env::temp_dir().join(format!(
            "helper-argv-internal-bare-reject-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let internal = workspace.join(".ai_ide_runtime");
        std::fs::create_dir_all(&internal).unwrap();

        let error = validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &["python".to_string(), ".ai_ide_runtime".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("internal workspace metadata"));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn trailing_dot_internal_workspace_argument_is_rejected() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-argv-internal-dot-reject-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&workspace).unwrap();

        let error = validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &["python".to_string(), ".ai_ide_runtime.".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("internal workspace metadata"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn alternate_data_stream_script_arguments_are_rejected() {
        let workspace =
            std::env::temp_dir().join(format!("helper-argv-ads-reject-{}", std::process::id()));
        std::fs::create_dir_all(&workspace).unwrap();

        let error = validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &["python".to_string(), r".\script.py:secret".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("alternate data streams"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn drive_relative_program_paths_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[r"C:tool.cmd".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("drive-relative form"));
    }

    #[test]
    fn root_relative_program_paths_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[r"\Users\Public\outside-tool.exe".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("root-relative form"));
    }

    #[test]
    fn unc_program_paths_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[r"\\server\share\tool.cmd".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("UNC or device form"));
    }

    #[test]
    fn reserved_device_program_paths_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &["NUL".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("Windows reserved device names"));
    }

    #[test]
    fn nested_shell_programs_are_rejected_in_argv_mode() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[
                "powershell.exe".to_string(),
                "-Command".to_string(),
                "echo nope".to_string(),
            ],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("nested shell programs"));
    }

    #[test]
    fn wsl_programs_are_rejected_in_argv_mode() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[
                "wsl.exe".to_string(),
                "sh".to_string(),
                "-lc".to_string(),
                "echo nope".to_string(),
            ],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("nested shell programs"));
    }

    #[test]
    fn command_com_is_rejected_in_argv_mode() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[
                "command.com".to_string(),
                "/c".to_string(),
                "echo nope".to_string(),
            ],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("nested shell programs"));
    }

    #[test]
    fn drive_relative_script_arguments_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &["python".to_string(), r"C:outside.py".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("drive-relative form"));
    }

    #[test]
    fn root_relative_script_arguments_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[
                "python".to_string(),
                r"\Users\Public\outside.py".to_string(),
            ],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("root-relative form"));
    }

    #[test]
    fn forward_slash_root_relative_script_arguments_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &["python".to_string(), "/Users/Public/outside.py".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("root-relative form"));
    }

    #[test]
    fn unc_script_arguments_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[
                "python".to_string(),
                r"\\server\share\outside.py".to_string(),
            ],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("UNC or device form"));
    }

    #[test]
    fn reserved_device_script_arguments_are_rejected() {
        let error = validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &["python".to_string(), "NUL.txt".to_string()],
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("Windows reserved device names"));
    }

    #[test]
    fn attached_option_value_script_arguments_outside_workspace_are_allowed() {
        validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[
                "python".to_string(),
                r"--config=C:\Users\Public\outside.py".to_string(),
            ],
        )
        .unwrap();
    }

    #[test]
    fn attached_option_value_workspace_paths_are_allowed() {
        let base = std::env::temp_dir().join(format!(
            "helper-argv-option-workspace-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        std::fs::create_dir_all(workspace.join("cfg")).unwrap();
        let config = workspace.join("cfg").join("tool.toml");
        std::fs::write(&config, b"ok").unwrap();

        let result = validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &[
                "python".to_string(),
                format!(r"--config={}", config.display()),
            ],
        );

        assert!(result.is_ok());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn quoted_attached_option_value_script_arguments_outside_workspace_are_allowed() {
        validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[
                "python".to_string(),
                r#"--config="C:\Users\Public\outside.py""#.to_string(),
            ],
        )
        .unwrap();
    }

    #[test]
    fn quoted_attached_option_value_workspace_paths_are_allowed() {
        let base = std::env::temp_dir().join(format!(
            "helper-argv-option-quoted-workspace-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        std::fs::create_dir_all(workspace.join("cfg")).unwrap();
        let config = workspace.join("cfg").join("tool.toml");
        std::fs::write(&config, b"ok").unwrap();

        let result = validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &[
                "python".to_string(),
                format!(r#"--config="{}""#, config.display()),
            ],
        );

        assert!(result.is_ok());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn response_file_script_arguments_outside_workspace_are_allowed() {
        validate_child_argv_access(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            &[
                "python".to_string(),
                r"@C:\Users\Public\outside.rsp".to_string(),
            ],
        )
        .unwrap();
    }

    #[test]
    fn response_file_workspace_paths_are_allowed() {
        let base = std::env::temp_dir().join(format!(
            "helper-argv-response-workspace-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        std::fs::create_dir_all(workspace.join("cfg")).unwrap();
        let response_file = workspace.join("cfg").join("args.rsp");
        std::fs::write(&response_file, b"--flag").unwrap();

        let result = validate_child_argv_access(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &[
                "python".to_string(),
                format!("@{}", response_file.display()),
            ],
        );

        assert!(result.is_ok());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn shell_command_tokens_split_simple_shell_syntax() {
        assert_eq!(
            vec!["echo", "hello", "type", ".\\note.txt"],
            shell_command_tokens("echo hello && type .\\note.txt")
        );
    }

    #[test]
    fn shell_command_allows_absolute_out_of_root_literal() {
        validate_shell_command_text(
            &std::env::temp_dir().join("workspace-root"),
            &std::env::temp_dir().join("workspace-root"),
            &BTreeMap::new(),
            r"echo C:\Users\Public\secret.txt",
        )
        .unwrap();
    }

    #[test]
    fn shell_command_allows_attached_option_value_path_outside_workspace() {
        validate_shell_command_text(
            &PathBuf::from(r"C:\workspace"),
            &PathBuf::from(r"C:\workspace"),
            &BTreeMap::new(),
            r"echo --config=C:\Users\Public\outside.py",
        )
        .unwrap();
    }

    #[test]
    fn shell_command_allows_attached_option_value_path_under_workspace() {
        let base = std::env::temp_dir().join(format!(
            "helper-shell-option-workspace-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        std::fs::create_dir_all(workspace.join("cfg")).unwrap();
        let config = workspace.join("cfg").join("tool.toml");
        std::fs::write(&config, b"ok").unwrap();

        let result = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            &format!(r"echo --config={}", config.display()),
        );

        assert!(result.is_ok());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn shell_command_allows_response_file_path_outside_workspace() {
        let base = std::env::temp_dir().join(format!(
            "helper-shell-response-outside-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(bin.join("python.exe"), b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        validate_shell_command_text(
            &workspace,
            &workspace,
            &env,
            r"python @C:\Users\Public\outside.rsp",
        )
        .unwrap();

        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn shell_command_allows_response_file_path_under_workspace() {
        let base = std::env::temp_dir().join(format!(
            "helper-shell-response-workspace-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let bin = workspace.join("bin");
        std::fs::create_dir_all(workspace.join("cfg")).unwrap();
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(bin.join("python.exe"), b"").unwrap();
        let response_file = workspace.join("cfg").join("args.rsp");
        std::fs::write(&response_file, b"--flag").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let result = validate_shell_command_text(
            &workspace,
            &workspace,
            &env,
            &format!(r"python @{}", response_file.display()),
        );

        assert!(result.is_ok());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn shell_command_rejects_parent_traversal_literal() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type ..\secret.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
    }

    #[test]
    fn shell_command_rejects_drive_relative_literal() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type C:secret.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("drive-relative form"));
    }

    #[test]
    fn shell_command_rejects_root_relative_literal() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type \Users\Public\secret.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("root-relative form"));
    }

    #[test]
    fn shell_command_rejects_forward_slash_root_relative_literal() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            "type /Users/Public/secret.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("root-relative form"));
    }

    #[test]
    fn shell_command_rejects_alternate_data_stream_literal() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type .\file.txt:secret",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("alternate data streams"));
    }

    #[test]
    fn shell_command_rejects_unc_literal() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type \\server\share\secret.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("UNC or device form"));
    }

    #[test]
    fn shell_command_rejects_reserved_device_literal() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), r"type NUL")
                .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("Windows reserved device names"));
    }

    #[test]
    fn shell_command_rejects_internal_workspace_metadata_literal() {
        let workspace = PathBuf::from(r"C:\workspace");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type .ai_ide_runtime\secret.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("internal workspace metadata"));
    }

    #[test]
    fn shell_command_rejects_bare_internal_workspace_metadata_literal() {
        let base = std::env::temp_dir().join(format!(
            "helper-shell-internal-bare-reject-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        std::fs::create_dir_all(workspace.join(".ai_ide_runtime")).unwrap();

        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"dir .ai_ide_runtime",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("internal workspace metadata"));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn shell_command_rejects_trailing_dot_internal_workspace_metadata_literal() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-internal-dot-reject-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&workspace).unwrap();

        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"dir .ai_ide_runtime.",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("internal workspace metadata"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_bare_hardlink_alias_in_workspace() {
        let base = std::env::temp_dir().join(format!(
            "helper-shell-hardlink-bare-reject-{}",
            std::process::id()
        ));
        let workspace = base.join("workspace");
        let outside = base.join("outside");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        let target = outside.join("trace.log");
        let alias = workspace.join("trace.log");
        std::fs::write(&target, b"alias").unwrap();
        std::fs::hard_link(&target, &alias).unwrap();

        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type trace.log",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("hardlink aliases"));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn shell_command_rejects_nested_shell_invocation() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"powershell -NoLogo -Command echo nope",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("nested shell programs"));
    }

    #[test]
    fn shell_command_rejects_wsl_nested_shell_invocation() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"wsl.exe sh -lc echo nope",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("nested shell programs"));
    }

    #[test]
    fn shell_command_rejects_command_com_nested_shell_invocation() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"command.com /c echo nope",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("nested shell programs"));
    }

    #[test]
    fn shell_command_rejects_start_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"start helper.cmd",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("must not use start"));
    }

    #[test]
    fn shell_command_rejects_dir_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "dir")
            .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("filesystem builtins"));
    }

    #[test]
    fn shell_command_rejects_type_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "type")
            .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("filesystem builtins"));
    }

    #[test]
    fn shell_command_rejects_copy_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "copy a b")
                .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("filesystem builtins"));
    }

    #[test]
    fn shell_command_rejects_del_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "del note.txt")
                .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("filesystem builtins"));
    }

    #[test]
    fn shell_command_rejects_mkdir_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "mkdir build")
                .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("filesystem builtins"));
    }

    #[test]
    fn shell_command_rejects_mklink_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"mklink linked note.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("filesystem builtins"));
    }

    #[test]
    fn shell_command_rejects_control_operator_sequences() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"echo one && echo two",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("shell control operators"));
    }

    #[test]
    fn shell_command_rejects_unquoted_caret_escape() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"python print_arg.py hello^ world",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("escape syntax"));
    }

    #[test]
    fn shell_command_rejects_unterminated_quote() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r#"python "script.py"#,
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("unterminated quotes"));
    }

    #[test]
    fn shell_command_rejects_redirection_operator() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type note.txt > out.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("shell control operators"));
    }

    #[test]
    fn shell_command_rejects_call_nested_shell_invocation() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"call cmd /c echo nope",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("nested shell programs"));
    }

    #[test]
    fn shell_command_allows_simple_command_without_paths() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let result =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "echo helper-ok");

        assert!(result.is_ok());
    }

    #[test]
    fn simple_non_builtin_shell_command_has_direct_argv_candidate() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-direct-candidate-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);
        let candidate =
            direct_argv_candidate_for_shell_command(&workspace, &env, r#"python -c "print('ok')""#)
                .expect("direct argv candidate");
        assert!(
            candidate[0].ends_with(r"bin\python.exe"),
            "candidate was: {candidate:?}"
        );
        assert_eq!(
            vec!["-c".to_string(), "print('ok')".to_string()],
            candidate[1..]
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn builtin_shell_command_stays_in_shell_mode() {
        assert_eq!(
            None,
            direct_argv_candidate_for_shell_command(
                Path::new(r"C:\workspace"),
                &BTreeMap::new(),
                "echo helper-ok"
            )
        );
    }

    #[test]
    fn shell_only_assoc_builtin_does_not_lower_into_direct_argv_even_when_path_lookup_finds_exe() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-assoc-direct-argv-reject-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(bin.join("assoc.exe"), b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        assert_eq!(
            None,
            direct_argv_candidate_for_shell_command(&workspace, &env, r"assoc .py=helper")
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn env_expanded_ftype_builtin_does_not_lower_into_direct_argv_even_when_path_lookup_finds_exe()
    {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-ftype-direct-argv-reject-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(bin.join("ftype.exe"), b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
            ("WORKSPACE_SHELL_ONLY".to_string(), "ftype".to_string()),
        ]);

        assert_eq!(
            None,
            direct_argv_candidate_for_shell_command(
                &workspace,
                &env,
                r#"call %WORKSPACE_SHELL_ONLY% helper=python "%1""#,
            )
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn explicit_shell_script_command_has_structured_shell_candidate() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-script-candidate-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("helper-tool.cmd");
        std::fs::write(&tool, b"@echo off\r\necho helper\r\n").unwrap();
        let candidate = structured_shell_script_candidate_for_shell_command(
            &workspace,
            &BTreeMap::new(),
            r".\bin\helper-tool.cmd arg1",
        )
        .expect("structured shell candidate");
        assert!(
            candidate[0].ends_with(r"bin\helper-tool.cmd"),
            "candidate was: {candidate:?}"
        );
        assert_eq!(vec!["arg1".to_string()], candidate[1..]);
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn bare_shell_script_command_from_path_stays_rejected() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-script-path-reject-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("helper-tool.cmd");
        std::fs::write(&tool, b"@echo off\r\necho helper\r\n").unwrap();
        let env = BTreeMap::from([("PATH".to_string(), bin.display().to_string())]);
        assert_eq!(
            None,
            structured_shell_script_candidate_for_shell_command(&workspace, &env, "helper-tool")
        );
        let error =
            validate_shell_command_text(&workspace, &workspace, &env, "helper-tool").unwrap_err();
        assert!(
            error
                .to_string()
                .contains("explicit workspace batch-script paths")
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn env_expanded_call_shell_script_has_structured_shell_candidate() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-script-env-candidate-{}",
            std::process::id()
        ));
        let script_dir = workspace.join("tools");
        std::fs::create_dir_all(&script_dir).unwrap();
        let tool = script_dir.join("helper-tool.cmd");
        std::fs::write(&tool, b"@echo off\r\necho helper\r\n").unwrap();
        let env = BTreeMap::from([("WORKSPACE_TOOL".to_string(), tool.display().to_string())]);
        let candidate = structured_shell_script_candidate_for_shell_command(
            &workspace,
            &env,
            r#"call %WORKSPACE_TOOL% arg1"#,
        )
        .expect("structured shell candidate");
        assert_eq!(tool.to_string_lossy().to_string(), candidate[0]);
        assert_eq!(vec!["arg1".to_string()], candidate[1..]);
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn unquoted_env_expanded_shell_script_with_whitespace_is_rejected() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-script-env-space-reject-{}",
            std::process::id()
        ));
        let script_dir = workspace.join("tool dir");
        std::fs::create_dir_all(&script_dir).unwrap();
        let tool = script_dir.join("helper-tool.cmd");
        std::fs::write(&tool, b"@echo off\r\necho helper\r\n").unwrap();
        let env = BTreeMap::from([("WORKSPACE_TOOL".to_string(), tool.display().to_string())]);

        assert_eq!(
            None,
            structured_shell_script_candidate_for_shell_command(
                &workspace,
                &env,
                r#"call %WORKSPACE_TOOL% arg1"#,
            )
        );

        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &env,
            r#"call %WORKSPACE_TOOL% arg1"#,
        )
        .unwrap_err();

        assert!(error.to_string().contains("shell-sensitive syntax"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn quoted_env_expanded_shell_script_with_whitespace_has_structured_shell_candidate() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-script-env-space-quoted-{}",
            std::process::id()
        ));
        let script_dir = workspace.join("tool dir");
        std::fs::create_dir_all(&script_dir).unwrap();
        let tool = script_dir.join("helper-tool.cmd");
        std::fs::write(&tool, b"@echo off\r\necho helper\r\n").unwrap();
        let env = BTreeMap::from([("WORKSPACE_TOOL".to_string(), tool.display().to_string())]);

        let candidate = structured_shell_script_candidate_for_shell_command(
            &workspace,
            &env,
            r#"call "%WORKSPACE_TOOL%" arg1"#,
        )
        .expect("structured shell candidate");

        assert_eq!(tool.to_string_lossy().to_string(), candidate[0]);
        assert_eq!(vec!["arg1".to_string()], candidate[1..]);
        assert!(
            validate_shell_command_text(
                &workspace,
                &workspace,
                &env,
                r#"call "%WORKSPACE_TOOL%" arg1"#,
            )
            .is_ok()
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn structured_shell_script_candidate_rejects_unsafe_batch_arguments() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-script-unsafe-arg-reject-{}",
            std::process::id()
        ));
        let script_dir = workspace.join("tools");
        std::fs::create_dir_all(&script_dir).unwrap();
        let tool = script_dir.join("helper-tool.cmd");
        std::fs::write(&tool, b"@echo off\r\necho helper\r\n").unwrap();
        let env = BTreeMap::from([
            ("WORKSPACE_TOOL".to_string(), tool.display().to_string()),
            ("WORKSPACE_FLAG".to_string(), "100%done".to_string()),
        ]);
        assert_eq!(
            None,
            structured_shell_script_candidate_for_shell_command(
                &workspace,
                &env,
                r#"call %WORKSPACE_TOOL% "%WORKSPACE_FLAG%""#,
            )
        );
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &env,
            r#"call %WORKSPACE_TOOL% "%WORKSPACE_FLAG%""#,
        )
        .unwrap_err();
        assert!(error.to_string().contains("unsafe batch arguments"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn structured_shell_script_candidate_rejects_shell_metacharacter_arguments() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-script-metachar-reject-{}",
            std::process::id()
        ));
        let script_dir = workspace.join("tools");
        std::fs::create_dir_all(&script_dir).unwrap();
        let tool = script_dir.join("helper-tool.cmd");
        std::fs::write(&tool, b"@echo off\r\necho %1\r\n").unwrap();
        let env = BTreeMap::from([("WORKSPACE_TOOL".to_string(), tool.display().to_string())]);

        assert_eq!(
            None,
            structured_shell_script_candidate_for_shell_command(
                &workspace,
                &env,
                r#"call %WORKSPACE_TOOL% "hello & dir""#,
            )
        );

        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &env,
            r#"call %WORKSPACE_TOOL% "hello & dir""#,
        )
        .unwrap_err();

        assert!(error.to_string().contains("unsafe batch arguments"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn builtin_shell_script_candidate_stays_in_shell_mode() {
        assert_eq!(
            None,
            structured_shell_script_candidate_for_shell_command(
                Path::new(r"C:\workspace"),
                &BTreeMap::new(),
                "echo helper-ok"
            )
        );
    }

    #[test]
    fn env_expanding_shell_command_stays_in_shell_mode() {
        let env = BTreeMap::from([(
            "WORKSPACE_PYTHON".to_string(),
            r"C:\workspace\bin\python.exe".to_string(),
        )]);
        assert_eq!(
            None,
            direct_argv_candidate_for_shell_command(
                Path::new(r"C:\workspace"),
                &env,
                r#"python -c "%HOME%""#
            )
        );
    }

    #[test]
    fn env_expanded_executable_shell_command_has_direct_argv_candidate() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-env-exe-candidate-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("WORKSPACE_PYTHON".to_string(), tool.display().to_string()),
        ]);
        let candidate = direct_argv_candidate_for_shell_command(
            &workspace,
            &env,
            r#"%WORKSPACE_PYTHON% -c "print('ok')""#,
        )
        .expect("direct argv candidate");
        assert_eq!(tool.to_string_lossy().to_string(), candidate[0]);
        assert_eq!(
            vec!["-c".to_string(), "print('ok')".to_string()],
            candidate[1..]
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn unquoted_env_expanded_executable_with_whitespace_is_rejected() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-env-exe-space-reject-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin dir");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([("WORKSPACE_PYTHON".to_string(), tool.display().to_string())]);

        assert_eq!(
            None,
            direct_argv_candidate_for_shell_command(
                &workspace,
                &env,
                r#"%WORKSPACE_PYTHON% -c "print('ok')""#,
            )
        );

        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &env,
            r#"%WORKSPACE_PYTHON% -c "print('ok')""#,
        )
        .unwrap_err();

        assert!(error.to_string().contains("shell-sensitive syntax"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn quoted_env_expanded_executable_with_whitespace_has_direct_argv_candidate() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-env-exe-space-quoted-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin dir");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([("WORKSPACE_PYTHON".to_string(), tool.display().to_string())]);

        let candidate = direct_argv_candidate_for_shell_command(
            &workspace,
            &env,
            r#""%WORKSPACE_PYTHON%" -c "print('ok')""#,
        )
        .expect("direct argv candidate");

        assert_eq!(tool.to_string_lossy().to_string(), candidate[0]);
        assert_eq!(
            vec!["-c".to_string(), "print('ok')".to_string()],
            candidate[1..]
        );
        assert!(
            validate_shell_command_text(
                &workspace,
                &workspace,
                &env,
                r#""%WORKSPACE_PYTHON%" -c "print('ok')""#,
            )
            .is_ok()
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn call_wrapped_native_executable_shell_command_has_direct_argv_candidate() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-call-exe-candidate-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);
        let candidate = direct_argv_candidate_for_shell_command(
            &workspace,
            &env,
            r#"call python -c "print('ok')""#,
        )
        .expect("direct argv candidate");
        assert!(
            candidate[0].ends_with(r"bin\python.exe"),
            "candidate was: {candidate:?}"
        );
        assert_eq!(
            vec!["-c".to_string(), "print('ok')".to_string()],
            candidate[1..]
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn env_expanded_simple_argument_keeps_native_shell_command_on_direct_argv_path() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-env-arg-candidate-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
            ("WORKSPACE_FLAG".to_string(), "helper-ok".to_string()),
        ]);
        let candidate = direct_argv_candidate_for_shell_command(
            &workspace,
            &env,
            r#"python print_arg.py %WORKSPACE_FLAG%"#,
        )
        .expect("direct argv candidate");
        assert!(
            candidate[0].ends_with(r"bin\python.exe"),
            "candidate was: {candidate:?}"
        );
        assert_eq!(
            vec!["print_arg.py".to_string(), "helper-ok".to_string()],
            candidate[1..]
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn env_expanded_argument_with_whitespace_stays_in_shell_mode() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-env-arg-whitespace-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
            ("WORKSPACE_FLAG".to_string(), "helper ok".to_string()),
        ]);
        assert_eq!(
            None,
            direct_argv_candidate_for_shell_command(
                &workspace,
                &env,
                r#"python print_arg.py %WORKSPACE_FLAG%"#,
            )
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn quoted_env_expanded_argument_with_whitespace_keeps_direct_argv_candidate() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-env-arg-quoted-whitespace-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
            ("WORKSPACE_FLAG".to_string(), "helper ok".to_string()),
        ]);
        let candidate = direct_argv_candidate_for_shell_command(
            &workspace,
            &env,
            r#"python print_arg.py "%WORKSPACE_FLAG%""#,
        )
        .expect("direct argv candidate");
        assert!(
            candidate[0].ends_with(r"bin\python.exe"),
            "candidate was: {candidate:?}"
        );
        assert_eq!(
            vec!["print_arg.py".to_string(), "helper ok".to_string()],
            candidate[1..]
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn caret_escaped_shell_command_stays_out_of_direct_argv_path() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-caret-direct-argv-reject-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        assert_eq!(
            None,
            direct_argv_candidate_for_shell_command(
                &workspace,
                &env,
                r#"python print_arg.py hello^ world"#,
            )
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn unterminated_quote_shell_command_stays_out_of_direct_argv_path() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-quote-direct-argv-reject-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        assert_eq!(
            None,
            direct_argv_candidate_for_shell_command(&workspace, &env, r#"python "script.py"#,)
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn helper_echo_output_for_shell_command_expands_simple_arguments() {
        let env = BTreeMap::from([("WORKSPACE_FLAG".to_string(), "helper-ok".to_string())]);
        assert_eq!(
            Some("helper-ok tail".to_string()),
            helper_echo_output_for_shell_command(&env, "echo %WORKSPACE_FLAG% tail")
        );
    }

    #[test]
    fn helper_echo_output_for_shell_command_keeps_expanded_metacharacters_literal() {
        let env = BTreeMap::from([("WORKSPACE_FLAG".to_string(), "left & right".to_string())]);
        assert_eq!(
            Some("left & right".to_string()),
            helper_echo_output_for_shell_command(&env, "echo %WORKSPACE_FLAG%")
        );
    }

    #[test]
    fn helper_echo_output_for_shell_command_treats_bang_refs_literally() {
        let env = BTreeMap::from([("WORKSPACE_FLAG".to_string(), "helper-ok".to_string())]);
        assert_eq!(
            Some("!WORKSPACE_FLAG!".to_string()),
            helper_echo_output_for_shell_command(&env, "echo !WORKSPACE_FLAG!")
        );
    }

    #[test]
    fn helper_echo_output_for_shell_command_rejects_unquoted_caret_escape() {
        let env = BTreeMap::new();
        assert_eq!(
            None,
            helper_echo_output_for_shell_command(&env, "echo hello^ world")
        );
    }

    #[test]
    fn helper_echo_output_for_shell_command_rejects_unterminated_quote() {
        let env = BTreeMap::new();
        assert_eq!(
            None,
            helper_echo_output_for_shell_command(&env, r#"echo "hello"#)
        );
    }

    #[test]
    fn shell_command_does_not_lower_bang_expanded_programs_into_direct_argv() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-bang-direct-argv-reject-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let python = bin.join("python.exe");
        std::fs::write(&python, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
            ("WORKSPACE_PYTHON".to_string(), python.display().to_string()),
        ]);

        let candidate = direct_argv_candidate_for_shell_command(
            &workspace,
            &env,
            r#"!WORKSPACE_PYTHON! -c "print('ok')""#,
        );
        assert!(candidate.is_none());

        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &env,
            r#"!WORKSPACE_PYTHON! -c "print('ok')""#,
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(
            error
                .to_string()
                .contains("must resolve invoked program under helper-owned PATH roots")
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_does_not_lower_bang_expanded_batch_scripts() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-bang-batch-reject-{}",
            std::process::id()
        ));
        let script = workspace.join("tool.cmd");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::write(&script, b"@echo off\r\necho helper-tool\r\n").unwrap();
        let env = BTreeMap::from([("WORKSPACE_TOOL".to_string(), script.display().to_string())]);

        let candidate = structured_shell_script_candidate_for_shell_command(
            &workspace,
            &env,
            "call !WORKSPACE_TOOL! arg1",
        );
        assert!(candidate.is_none());

        let error =
            validate_shell_command_text(&workspace, &workspace, &env, "call !WORKSPACE_TOOL! arg1")
                .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(
            error
                .to_string()
                .contains("must resolve invoked program under helper-owned PATH roots")
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_env_expanded_control_operator_argument_for_native_program() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-env-control-arg-reject-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("python.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
            ("WORKSPACE_FLAG".to_string(), "left & right".to_string()),
        ]);

        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &env,
            r#"python print_arg.py %WORKSPACE_FLAG%"#,
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("shell control operators"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_allows_echo_with_env_expanded_control_operator_argument() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let env = BTreeMap::from([("WORKSPACE_FLAG".to_string(), "left & right".to_string())]);

        let result =
            validate_shell_command_text(&workspace, &workspace, &env, r#"echo %WORKSPACE_FLAG%"#);

        assert!(result.is_ok());
    }

    #[test]
    fn helper_echo_output_for_call_wrapped_echo_stays_rejected() {
        let env = BTreeMap::new();
        assert_eq!(
            None,
            helper_echo_output_for_shell_command(&env, "call echo helper-ok")
        );
    }

    #[test]
    fn shell_command_rejects_non_batch_script_file_association_launch() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-file-association-reject-{}",
            std::process::id()
        ));
        let script = workspace.join("helper.py");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::write(&script, b"print('helper')\n").unwrap();

        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "helper.py")
                .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("file-association"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_unresolved_bare_program() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            "missing-helper-program",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(
            error
                .to_string()
                .contains("must resolve invoked program under helper-owned PATH roots")
        );
    }

    #[test]
    fn shell_command_rejects_call_wrapped_echo_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root-call-echo");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "call echo hi")
                .expect_err("call-wrapped echo should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("helper-local builtins"));
    }

    #[test]
    fn shell_command_rejects_echo_suppression_prefix_on_builtin() {
        let workspace = std::env::temp_dir().join("workspace-root-at-echo");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "@echo hi")
                .expect_err("command echo suppression prefix should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("echo suppression prefix"));
    }

    #[test]
    fn shell_command_rejects_echo_suppression_prefix_on_native_program() {
        let workspace =
            std::env::temp_dir().join(format!("workspace-root-at-python-{}", std::process::id()));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(bin.join("python.exe"), b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let error = validate_shell_command_text(&workspace, &workspace, &env, "@python -c pass")
            .expect_err("command echo suppression prefix should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("echo suppression prefix"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_cd() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-cd");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "cd")
            .expect_err("stateful cd builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_setlocal() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-setlocal");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            "setlocal enableextensions",
        )
        .expect_err("stateful setlocal builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_assoc() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-assoc");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"assoc .py=helper",
        )
        .expect_err("stateful assoc builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_break() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-break");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "break")
            .expect_err("stateful break builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_chcp() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-chcp");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "chcp 65001")
                .expect_err("stateful chcp builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_dpath() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-dpath");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"dpath C:\tools",
        )
        .expect_err("stateful dpath builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_ftype() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-ftype");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r#"ftype helper="python" "%1""#,
        )
        .expect_err("stateful ftype builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_exit() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-exit");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "exit")
            .expect_err("stateful exit builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_verify() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-verify");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "verify on")
                .expect_err("stateful verify builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_for() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-for");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "for")
            .expect_err("stateful for builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_if() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-if");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r#"if exist a.txt echo ok"#,
        )
        .expect_err("stateful if builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_goto() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-goto");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "goto :again")
                .expect_err("stateful goto builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_batch_label_control_flow() {
        let workspace = std::env::temp_dir().join("workspace-root-batch-label");
        let error =
            validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "call :again")
                .expect_err("batch label control flow should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("batch-label control flow"));
    }

    #[test]
    fn shell_command_rejects_direct_batch_label_control_flow() {
        let workspace = std::env::temp_dir().join("workspace-root-direct-batch-label");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), ":again")
            .expect_err("direct batch label control flow should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("batch-label control flow"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_shift() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-shift");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "shift")
            .expect_err("stateful shift builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_pause() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-pause");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "pause")
            .expect_err("stateful pause builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_title() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-title");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            "title helper-window",
        )
        .expect_err("stateful title builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_cls() {
        let workspace = std::env::temp_dir().join("workspace-root-stateful-cls");
        let error = validate_shell_command_text(&workspace, &workspace, &BTreeMap::new(), "cls")
            .expect_err("stateful cls builtin should be rejected");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_date_even_when_path_lookup_finds_exe() {
        let workspace =
            std::env::temp_dir().join(format!("helper-shell-stateful-date-{}", std::process::id()));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("date.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let error = validate_shell_command_text(&workspace, &workspace, &env, "date /t")
            .expect_err("stateful date builtin should be rejected before PATH lookup");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_call_wrapped_stateful_shell_builtin_date_even_when_path_lookup_finds_exe()
     {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-stateful-call-date-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("date.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let error = validate_shell_command_text(&workspace, &workspace, &env, "call date /t")
            .expect_err("call-wrapped stateful date builtin should be rejected before PATH lookup");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_stateful_shell_builtin_time_even_when_path_lookup_finds_exe() {
        let workspace =
            std::env::temp_dir().join(format!("helper-shell-stateful-time-{}", std::process::id()));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("time.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let error = validate_shell_command_text(&workspace, &workspace, &env, "time /t")
            .expect_err("stateful time builtin should be rejected before PATH lookup");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_filesystem_shell_builtin_vol_even_when_path_lookup_finds_exe() {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-filesystem-vol-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("vol.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let error = validate_shell_command_text(&workspace, &workspace, &env, "vol")
            .expect_err("filesystem vol builtin should be rejected before PATH lookup");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("filesystem builtins"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_call_wrapped_filesystem_shell_builtin_vol_even_when_path_lookup_finds_exe()
     {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-filesystem-call-vol-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("vol.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let error = validate_shell_command_text(&workspace, &workspace, &env, "call vol")
            .expect_err(
                "call-wrapped filesystem vol builtin should be rejected before PATH lookup",
            );

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("filesystem builtins"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_call_wrapped_stateful_shell_builtin_time_even_when_path_lookup_finds_exe()
     {
        let workspace = std::env::temp_dir().join(format!(
            "helper-shell-stateful-call-time-{}",
            std::process::id()
        ));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("time.exe");
        std::fs::write(&tool, b"").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".EXE;.CMD".to_string()),
        ]);

        let error = validate_shell_command_text(&workspace, &workspace, &env, "call time /t")
            .expect_err("call-wrapped stateful time builtin should be rejected before PATH lookup");

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
        assert!(error.to_string().contains("stateful shell builtins"));
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_rejects_bare_batch_program_resolved_from_workspace_path() {
        let workspace =
            std::env::temp_dir().join(format!("helper-shell-path-lookup-{}", std::process::id()));
        let bin = workspace.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let tool = bin.join("helper-tool.cmd");
        std::fs::write(&tool, "@echo off\r\necho helper-tool\r\n").unwrap();
        let env = BTreeMap::from([
            ("PATH".to_string(), bin.display().to_string()),
            ("PATHEXT".to_string(), ".CMD;.EXE".to_string()),
        ]);

        let error = validate_shell_command_text(&workspace, &workspace, &env, "helper-tool")
            .expect_err("bare batch script should be rejected");
        assert!(
            error
                .to_string()
                .contains("explicit workspace batch-script paths")
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn shell_command_allows_workspace_env_expansion_under_allowed_roots() {
        let base = std::env::temp_dir().join(format!("helper-shell-expand-{}", std::process::id()));
        let workspace = base.join("workspace");
        std::fs::create_dir_all(&workspace).unwrap();
        let script = workspace.join("tool.cmd");
        std::fs::write(&script, "@echo off\r\necho helper-tool\r\n").unwrap();
        let mut env = BTreeMap::new();
        env.insert("WORKSPACE_TOOL".to_string(), script.display().to_string());

        let result =
            validate_shell_command_text(&workspace, &workspace, &env, r"call %WORKSPACE_TOOL%");

        assert!(result.is_ok());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn shell_command_rejects_unknown_env_expansion_in_path_literal() {
        let workspace = std::env::temp_dir().join("workspace-root");
        let error = validate_shell_command_text(
            &workspace,
            &workspace,
            &BTreeMap::new(),
            r"type %MISSING%\secret.txt",
        )
        .unwrap_err();

        assert_eq!(io::ErrorKind::PermissionDenied, error.kind());
    }

    #[test]
    fn shell_command_segments_preserve_command_boundaries() {
        assert_eq!(
            vec![
                vec!["echo".to_string(), "one".to_string()],
                vec!["call".to_string(), "helper.cmd".to_string()],
                vec!["type".to_string(), ".\\note.txt".to_string()],
            ],
            shell_command_segments("echo one && call helper.cmd | type .\\note.txt")
        );
    }

    #[test]
    fn shell_control_operator_detection_ignores_quoted_literals() {
        assert_eq!(None, first_shell_control_operator(r#"echo "a && b""#));
        assert_eq!(
            Some("&&"),
            first_shell_control_operator("echo one && echo two")
        );
        assert_eq!(
            Some(">"),
            first_shell_control_operator("type note.txt > out.txt")
        );
    }
}
