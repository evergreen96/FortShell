use std::env;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use ai_ide_adapter::{HostAdapter, HostAdapterInitError, HostConfig};
use ai_ide_protocol::HostResponseEnvelope;

struct CliArgs {
    root: PathBuf,
    default_agent_kind: String,
    policy_store: Option<PathBuf>,
    review_store: Option<PathBuf>,
    workspace_index_store: Option<PathBuf>,
    broker_store: Option<PathBuf>,
}

fn main() -> ExitCode {
    let args = match parse_args(env::args().skip(1)) {
        Ok(args) => args,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(2);
        }
    };

    let mut config = HostConfig::new(args.root, args.default_agent_kind);
    if let Some(path) = args.policy_store {
        config = config.with_policy_store_path(path);
    }
    if let Some(path) = args.review_store {
        config = config.with_review_store_path(path);
    }
    if let Some(path) = args.workspace_index_store {
        config = config.with_workspace_index_store_path(path);
    }
    if let Some(path) = args.broker_store {
        config = config.with_broker_store_path(path);
    }

    let mut host = match HostAdapter::new(config) {
        Ok(host) => host,
        Err(error) => {
            print_startup_error(error);
            return ExitCode::from(1);
        }
    };

    let stdin = io::stdin();
    let mut stdout = io::stdout();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(line) => line,
            Err(error) => {
                eprintln!("{error}");
                return ExitCode::from(1);
            }
        };
        if line.trim().is_empty() {
            continue;
        }

        let response = host.handle_request_json(&line);
        if writeln!(stdout, "{response}").is_err() {
            return ExitCode::from(1);
        }
        if stdout.flush().is_err() {
            return ExitCode::from(1);
        }
    }

    ExitCode::SUCCESS
}

fn parse_args<I>(args: I) -> Result<CliArgs, String>
where
    I: IntoIterator<Item = String>,
{
    let mut root = None;
    let mut default_agent_kind = "default".to_owned();
    let mut policy_store = None;
    let mut review_store = None;
    let mut workspace_index_store = None;
    let mut broker_store = None;
    let mut iter = args.into_iter();

    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--root" => {
                root = Some(PathBuf::from(next_value(&mut iter, "--root")?));
            }
            "--default-agent-kind" => {
                default_agent_kind = next_value(&mut iter, "--default-agent-kind")?;
            }
            "--policy-store" => {
                policy_store = Some(PathBuf::from(next_value(&mut iter, "--policy-store")?));
            }
            "--review-store" => {
                review_store = Some(PathBuf::from(next_value(&mut iter, "--review-store")?));
            }
            "--workspace-index-store" => {
                workspace_index_store = Some(PathBuf::from(next_value(
                    &mut iter,
                    "--workspace-index-store",
                )?));
            }
            "--broker-store" => {
                broker_store = Some(PathBuf::from(next_value(&mut iter, "--broker-store")?));
            }
            "--help" | "-h" => {
                return Err(usage());
            }
            value => {
                return Err(format!("Unknown argument: {value}\n{}", usage()));
            }
        }
    }

    let root = root.ok_or_else(usage)?;
    Ok(CliArgs {
        root,
        default_agent_kind,
        policy_store,
        review_store,
        workspace_index_store,
        broker_store,
    })
}

fn next_value<I>(iter: &mut I, flag: &str) -> Result<String, String>
where
    I: Iterator<Item = String>,
{
    iter.next()
        .ok_or_else(|| format!("Missing value for {flag}\n{}", usage()))
}

fn usage() -> String {
    "Usage: ai-ide-adapter --root <project-root> [--default-agent-kind <name>] [--policy-store <path>] [--review-store <path>] [--workspace-index-store <path>] [--broker-store <path>]".to_owned()
}

fn print_startup_error(error: HostAdapterInitError) {
    let payload = error.to_host_error();
    let envelope = HostResponseEnvelope::error(payload.code, payload.message);
    let _ = writeln!(io::stdout(), "{}", envelope.to_json());
}
