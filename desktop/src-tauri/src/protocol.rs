//! Desktop sidecar JSON-line protocol types.

use serde::{Deserialize, Serialize};

/// Outgoing request to the Python sidecar.
#[derive(Debug, Serialize)]
pub struct SidecarRequest {
    #[serde(rename = "type")]
    pub msg_type: &'static str,
    pub id: String,
    pub method: String,
    pub params: serde_json::Value,
}

impl SidecarRequest {
    pub fn new(id: String, method: String, params: serde_json::Value) -> Self {
        Self {
            msg_type: "request",
            id,
            method,
            params,
        }
    }

    pub fn to_json_line(&self) -> String {
        serde_json::to_string(self).expect("SidecarRequest serialization cannot fail")
    }
}

/// Incoming message from the Python sidecar (response or event).
#[derive(Debug, Deserialize)]
#[serde(tag = "type")]
pub enum SidecarMessage {
    #[serde(rename = "response")]
    Response {
        id: String,
        ok: bool,
        result: Option<serde_json::Value>,
        error: Option<SidecarError>,
    },
    #[serde(rename = "event")]
    Event {
        event: String,
        payload: serde_json::Value,
    },
}

#[derive(Debug, Deserialize, Clone)]
pub struct SidecarError {
    pub code: String,
    pub message: String,
}
