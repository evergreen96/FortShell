//! Python sidecar process manager.
//!
//! Spawns a long-lived Python process that speaks the desktop sidecar
//! JSON-line protocol over stdin/stdout.  A background reader thread
//! routes response lines to pending oneshot channels and forwards
//! event lines as Tauri app events.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use tauri::{AppHandle, Emitter};

use crate::protocol::{SidecarMessage, SidecarRequest};

/// A pending request waiting for its response.
type PendingSender = std::sync::mpsc::Sender<Result<serde_json::Value, String>>;

/// Shared state for the sidecar process.
struct SidecarInner {
    stdin: ChildStdin,
    pending: HashMap<String, PendingSender>,
}

pub struct SidecarManager {
    inner: Arc<Mutex<SidecarInner>>,
    next_id: AtomicU64,
    _child: Child, // kept alive for the process lifetime
}

impl SidecarManager {
    /// Spawn the Python sidecar and start the stdout reader thread.
    pub fn spawn(
        python: &str,
        project_root: &str,
        runtime_root: Option<&str>,
        app_handle: AppHandle,
    ) -> Result<Self, String> {
        let mut args = vec![
            "-m".to_string(),
            "backend.desktop_sidecar".to_string(),
            "--project-root".to_string(),
            project_root.to_string(),
        ];
        if let Some(rt) = runtime_root {
            args.push("--runtime-root".to_string());
            args.push(rt.to_string());
        }

        let mut child = Command::new(python)
            .args(&args)
            .current_dir(project_root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit()) // Python logs go to Tauri's stderr
            .spawn()
            .map_err(|e| format!("Failed to spawn Python sidecar: {e}"))?;

        let stdin = child.stdin.take().ok_or("Failed to open sidecar stdin")?;
        let stdout = child.stdout.take().ok_or("Failed to open sidecar stdout")?;

        let inner = Arc::new(Mutex::new(SidecarInner {
            stdin,
            pending: HashMap::new(),
        }));

        // Spawn stdout reader thread
        let reader_inner = Arc::clone(&inner);
        std::thread::Builder::new()
            .name("sidecar-reader".into())
            .spawn(move || {
                Self::reader_loop(BufReader::new(stdout), reader_inner, app_handle);
            })
            .map_err(|e| format!("Failed to spawn reader thread: {e}"))?;

        Ok(Self {
            inner,
            next_id: AtomicU64::new(1),
            _child: child,
        })
    }

    /// Send a request and wait for the response.
    pub fn request(
        &self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        let id = format!("req-{}", self.next_id.fetch_add(1, Ordering::Relaxed));
        let req = SidecarRequest::new(id.clone(), method.to_string(), params);
        let line = req.to_json_line();

        let (tx, rx) = std::sync::mpsc::channel();

        {
            let mut guard = self.inner.lock().map_err(|e| format!("Lock poisoned: {e}"))?;
            guard.pending.insert(id.clone(), tx);
            writeln!(guard.stdin, "{}", line).map_err(|e| format!("Failed to write to sidecar stdin: {e}"))?;
            guard.stdin.flush().map_err(|e| format!("Failed to flush sidecar stdin: {e}"))?;
        }

        // Wait for response (with timeout)
        rx.recv_timeout(std::time::Duration::from_secs(30))
            .map_err(|e| format!("Sidecar response timeout or channel closed: {e}"))?
    }

    /// Background reader: parse stdout lines, route responses, emit events.
    fn reader_loop(
        reader: BufReader<std::process::ChildStdout>,
        inner: Arc<Mutex<SidecarInner>>,
        app_handle: AppHandle,
    ) {
        for line_result in reader.lines() {
            let line = match line_result {
                Ok(l) => l,
                Err(_) => break, // EOF or I/O error
            };
            if line.trim().is_empty() {
                continue;
            }

            let message: SidecarMessage = match serde_json::from_str(&line) {
                Ok(m) => m,
                Err(e) => {
                    eprintln!("[sidecar-reader] Failed to parse line: {e}");
                    continue;
                }
            };

            match message {
                SidecarMessage::Response { id, ok, result, error } => {
                    let mut guard = match inner.lock() {
                        Ok(g) => g,
                        Err(_) => break,
                    };
                    if let Some(sender) = guard.pending.remove(&id) {
                        let value = if ok {
                            Ok(result.unwrap_or(serde_json::Value::Null))
                        } else {
                            let msg = error
                                .map(|e| format!("{}: {}", e.code, e.message))
                                .unwrap_or_else(|| "Unknown error".into());
                            Err(msg)
                        };
                        let _ = sender.send(value);
                    }
                }
                SidecarMessage::Event { event, payload } => {
                    // Emit as Tauri event for the frontend to listen on
                    let _ = app_handle.emit(&event, payload);
                }
            }
        }
    }
}
