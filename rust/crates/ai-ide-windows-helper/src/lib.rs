use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

mod boundary_lock;
mod child_environment;
mod execution_guard;
mod filesystem_boundary;
mod low_integrity;
mod path_alias_guard;
mod path_policy;
mod process_containment;
mod restricted_token;
pub mod runtime;
#[cfg(windows)]
pub use low_integrity::capture_label_security_descriptor;

pub const FIXTURE_MARKER_PREFIX: &str = "__AI_IDE_FIXTURE__";
pub const HELPER_HOST_PATH_SCHEME: &str = "aiide-helper://host-path/";
pub const STDIO_PROXY_FLAG: &str = "--stdio-proxy";
pub const CONTROL_FILE_FLAG: &str = "--control-file";
pub const RESPONSE_FILE_FLAG: &str = "--response-file";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowsStrictHelperRequest {
    pub workspace: PathBuf,
    pub cwd: String,
    pub environment: BTreeMap<String, String>,
    pub command: Option<String>,
    pub argv: Vec<String>,
    pub stdio_proxy: bool,
    pub control_file: Option<PathBuf>,
    pub response_file: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum HelperControlCommand {
    Stop,
    Kill,
    Status,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WindowsStrictHelperControlMessage {
    #[serde(default = "default_protocol_version")]
    pub version: u32,
    pub command: HelperControlCommand,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub backend: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum HelperStatusState {
    Running,
    Exited,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WindowsStrictHelperStatusMessage {
    #[serde(default = "default_protocol_version")]
    pub version: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub backend: Option<String>,
    pub state: HelperStatusState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub returncode: Option<i32>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HelperRequestParseError {
    detail: String,
}

impl HelperRequestParseError {
    fn new(detail: impl Into<String>) -> Self {
        Self {
            detail: detail.into(),
        }
    }
}

impl fmt::Display for HelperRequestParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.detail)
    }
}

impl std::error::Error for HelperRequestParseError {}

pub fn parse_helper_request_args<I, S>(
    args: I,
) -> Result<WindowsStrictHelperRequest, HelperRequestParseError>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let values = args
        .into_iter()
        .map(|item| item.as_ref().to_string())
        .collect::<Vec<_>>();
    let mut index = 0usize;
    let mut workspace = None;
    let mut cwd = None;
    let mut environment = BTreeMap::new();
    let mut command = None;
    let mut argv = Vec::new();
    let mut stdio_proxy = false;
    let mut control_file = None;
    let mut response_file = None;

    while index < values.len() {
        let current = &values[index];
        if current == "--workspace" {
            workspace = Some(absolutize_path(Path::new(&take_value(
                &values,
                &mut index,
                "--workspace",
            )?))?);
        } else if current == "--cwd" {
            cwd = Some(take_value(&values, &mut index, "--cwd")?);
        } else if current == "--setenv" {
            let key = take_value(&values, &mut index, "--setenv")?;
            let value = take_value(&values, &mut index, "--setenv")?;
            environment.insert(key, value);
        } else if current == "--command" {
            command = Some(take_value(&values, &mut index, "--command")?);
        } else if current == "--argv" {
            argv.push(take_value(&values, &mut index, "--argv")?);
        } else if let Some(value) = current.strip_prefix("--argv=") {
            argv.push(value.to_string());
        } else if current == STDIO_PROXY_FLAG {
            stdio_proxy = true;
        } else if current == CONTROL_FILE_FLAG {
            control_file = Some(absolutize_path(Path::new(&take_value(
                &values,
                &mut index,
                CONTROL_FILE_FLAG,
            )?))?);
        } else if current == RESPONSE_FILE_FLAG {
            response_file = Some(absolutize_path(Path::new(&take_value(
                &values,
                &mut index,
                RESPONSE_FILE_FLAG,
            )?))?);
        } else {
            return Err(HelperRequestParseError::new(format!(
                "unknown argument: {current}"
            )));
        }
        index += 1;
    }

    if argv.is_empty() && command.is_none() {
        return Err(HelperRequestParseError::new(
            "expected --command or at least one --argv value",
        ));
    }

    Ok(WindowsStrictHelperRequest {
        workspace: workspace.ok_or_else(|| HelperRequestParseError::new("missing --workspace"))?,
        cwd: cwd.ok_or_else(|| HelperRequestParseError::new("missing --cwd"))?,
        environment,
        command,
        argv,
        stdio_proxy,
        control_file,
        response_file,
    })
}

pub fn encode_visible_host_path_token(path: &Path) -> io::Result<String> {
    let absolute = absolutize_path(path)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error.to_string()))?;
    Ok(format!(
        "{HELPER_HOST_PATH_SCHEME}{}",
        encode_base64_urlsafe_no_pad(absolute.to_string_lossy().as_bytes())
    ))
}

pub fn write_helper_control_message(
    path: &Path,
    message: &WindowsStrictHelperControlMessage,
) -> io::Result<()> {
    write_json_file(path, message)
}

pub fn read_helper_control_message(path: &Path) -> Option<WindowsStrictHelperControlMessage> {
    read_json_file(path)
}

pub fn write_helper_status_message(
    path: &Path,
    message: &WindowsStrictHelperStatusMessage,
) -> io::Result<()> {
    write_json_file(path, message)
}

pub fn read_helper_status_message(path: &Path) -> Option<WindowsStrictHelperStatusMessage> {
    read_json_file(path)
}

fn default_protocol_version() -> u32 {
    1
}

fn take_value(
    values: &[String],
    index: &mut usize,
    flag: &str,
) -> Result<String, HelperRequestParseError> {
    *index += 1;
    values
        .get(*index)
        .cloned()
        .ok_or_else(|| HelperRequestParseError::new(format!("missing value for {flag}")))
}

fn absolutize_path(path: &Path) -> Result<PathBuf, HelperRequestParseError> {
    if path.is_absolute() {
        return Ok(path.to_path_buf());
    }
    let current = std::env::current_dir().map_err(|error| {
        HelperRequestParseError::new(format!("failed to read current dir: {error}"))
    })?;
    Ok(current.join(path))
}

fn write_json_file<T: Serialize>(path: &Path, value: &T) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temp_dir = helper_protocol_temp_directory(path);
    fs::create_dir_all(&temp_dir)?;
    let temp_path = temporary_path_for(path);
    let encoded = serde_json::to_vec_pretty(value)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    fs::write(&temp_path, encoded)?;
    let result = replace_file(&temp_path, path);
    let _ = fs::remove_dir(&temp_dir);
    result
}

fn read_json_file<T: for<'de> Deserialize<'de>>(path: &Path) -> Option<T> {
    let content = fs::read(path).ok()?;
    serde_json::from_slice(&content).ok()
}

pub fn helper_protocol_temp_directory(path: &Path) -> PathBuf {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("helper");
    let sanitized = sanitize_protocol_temp_component(file_name);
    parent.join(format!(".ai_ide_protocol_tmp_{sanitized}"))
}

fn temporary_path_for(path: &Path) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let file_name = format!(
        "{}.{}.{}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("helper"),
        std::process::id(),
        nonce
    );
    helper_protocol_temp_directory(path).join(file_name)
}

fn sanitize_protocol_temp_component(value: &str) -> String {
    let sanitized = value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '.' | '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .collect::<String>();
    if sanitized.is_empty() {
        "helper".to_string()
    } else {
        sanitized
    }
}

fn replace_file(temp_path: &Path, target_path: &Path) -> io::Result<()> {
    match fs::rename(temp_path, target_path) {
        Ok(()) => Ok(()),
        Err(rename_error) => {
            if target_path.exists() {
                fs::remove_file(target_path)?;
                fs::rename(temp_path, target_path)
            } else {
                Err(rename_error)
            }
        }
    }
}

fn encode_base64_urlsafe_no_pad(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut output = String::with_capacity((input.len() * 4).div_ceil(3));
    let mut index = 0usize;
    while index < input.len() {
        let remaining = input.len() - index;
        let b0 = input[index];
        let b1 = if remaining > 1 { input[index + 1] } else { 0 };
        let b2 = if remaining > 2 { input[index + 2] } else { 0 };

        let sextet0 = (b0 >> 2) as usize;
        let sextet1 = (((b0 & 0b0000_0011) << 4) | (b1 >> 4)) as usize;
        let sextet2 = (((b1 & 0b0000_1111) << 2) | (b2 >> 6)) as usize;
        let sextet3 = (b2 & 0b0011_1111) as usize;

        output.push(TABLE[sextet0] as char);
        output.push(TABLE[sextet1] as char);
        if remaining > 1 {
            output.push(TABLE[sextet2] as char);
        }
        if remaining > 2 {
            output.push(TABLE[sextet3] as char);
        }
        index += 3;
    }
    output
}
