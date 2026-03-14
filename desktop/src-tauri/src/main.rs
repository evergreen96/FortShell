#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod protocol;
mod sidecar;

use sidecar::SidecarManager;
use std::sync::Mutex;
use tauri::{Manager, State};

/// Tauri-managed state holding the sidecar reference.
struct AppState {
    sidecar: Mutex<Option<SidecarManager>>,
}

/// IPC command: send a request to the Python sidecar and return the result.
#[tauri::command]
fn sidecar_request(
    method: &str,
    params: serde_json::Value,
    state: State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    let guard = state
        .sidecar
        .lock()
        .map_err(|e| format!("Lock poisoned: {e}"))?;
    let sidecar = guard
        .as_ref()
        .ok_or_else(|| "Sidecar not started".to_string())?;
    sidecar.request(method, params)
}

fn main() {
    tauri::Builder::default()
        .manage(AppState {
            sidecar: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![sidecar_request])
        .setup(|app| {
            let python = std::env::var("AI_IDE_PYTHON").unwrap_or_else(|_| "python".into());
            let project_root =
                std::env::var("AI_IDE_PROJECT_ROOT").unwrap_or_else(|_| ".".into());
            let runtime_root = std::env::var("AI_IDE_RUNTIME_ROOT").ok();

            let handle = app.handle().clone();
            match SidecarManager::spawn(
                &python,
                &project_root,
                runtime_root.as_deref(),
                handle,
            ) {
                Ok(mgr) => {
                    let state = app.state::<AppState>();
                    *state.sidecar.lock().unwrap() = Some(mgr);
                    eprintln!("[tauri] Python sidecar started (project_root={project_root})");
                }
                Err(e) => {
                    eprintln!("[tauri] WARNING: Failed to start sidecar: {e}");
                    eprintln!("[tauri] Desktop will run without sidecar (HTTP fallback only)");
                }
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running ai-ide-desktop");
}
