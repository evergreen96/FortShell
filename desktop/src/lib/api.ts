import type {
  DesktopShellSnapshot,
  EditorFileSnapshot,
  EditorSaveResponse,
  EditorStageResponse,
  PtyResizeRequest,
  PtyWriteRequest,
  WorkspacePanelMutation,
} from "./types";
import type { TerminalInspection } from "./types";
import { getTransport, resolveApiBase } from "./transport";

export { resolveApiBase };

// ---------------------------------------------------------------------------
// Public API functions — delegate to the transport singleton.
// In Tauri mode these go through invoke(); in dev mode through HTTP fetch().
// ---------------------------------------------------------------------------

export async function loadDesktopShell(target: string): Promise<DesktopShellSnapshot> {
  return (await getTransport()).request<DesktopShellSnapshot>("desktop_shell.snapshot", { target });
}

export async function mutatePolicy(
  action: "deny" | "allow",
  target: string,
  rule: string,
): Promise<WorkspacePanelMutation> {
  return (await getTransport()).request<WorkspacePanelMutation>(`policy.${action}`, { target, rule });
}

export async function createTerminal(payload: {
  name?: string;
  transport?: "runner" | "host";
  runner_mode?: "projected" | "strict";
  io_mode?: "command" | "pty";
}): Promise<{ kind: "terminal_create"; terminal: TerminalInspection }> {
  return (await getTransport()).request<{ kind: "terminal_create"; terminal: TerminalInspection }>(
    "terminal.create",
    payload as Record<string, unknown>,
  );
}

export async function runTerminalCommand(payload: {
  terminal_id: string;
  command: string;
}): Promise<{ kind: "terminal_run"; terminal: TerminalInspection; output: string }> {
  return (await getTransport()).request<{ kind: "terminal_run"; terminal: TerminalInspection; output: string }>(
    "terminal.run",
    payload as Record<string, unknown>,
  );
}

export async function loadEditorFile(target: string): Promise<EditorFileSnapshot> {
  return (await getTransport()).request<EditorFileSnapshot>("editor.file", { target });
}

export async function saveEditorFile(payload: {
  target: string;
  content: string;
}): Promise<EditorSaveResponse> {
  return (await getTransport()).request<EditorSaveResponse>(
    "editor.save",
    payload as Record<string, unknown>,
  );
}

export async function stageEditorChange(payload: {
  target: string;
  content: string;
}): Promise<EditorStageResponse> {
  return (await getTransport()).request<EditorStageResponse>(
    "editor.stage",
    payload as Record<string, unknown>,
  );
}

export async function applyEditorProposal(payload: {
  proposal_id: string;
}): Promise<EditorStageResponse> {
  return (await getTransport()).request<EditorStageResponse>(
    "editor.apply",
    payload as Record<string, unknown>,
  );
}

export async function rejectEditorProposal(payload: {
  proposal_id: string;
}): Promise<EditorStageResponse> {
  return (await getTransport()).request<EditorStageResponse>(
    "editor.reject",
    payload as Record<string, unknown>,
  );
}

export async function writePty(payload: PtyWriteRequest): Promise<{ kind: "pty_write"; ok: boolean }> {
  return (await getTransport()).request<{ kind: "pty_write"; ok: boolean }>(
    "terminal.pty.write",
    payload as Record<string, unknown>,
  );
}

export async function resizePty(payload: PtyResizeRequest): Promise<{ kind: "pty_resize"; ok: boolean }> {
  return (await getTransport()).request<{ kind: "pty_resize"; ok: boolean }>(
    "terminal.pty.resize",
    payload as Record<string, unknown>,
  );
}

/** SSE stream for PTY output — HTTP mode only. In Tauri mode, PTY data arrives via events. */
export function connectPtyStream(terminalId: string): EventSource {
  return new EventSource(`${resolveApiBase()}/api/terminal/pty/stream?terminal_id=${encodeURIComponent(terminalId)}`);
}

// ---------------------------------------------------------------------------
// Transport re-exports
// ---------------------------------------------------------------------------

export { getTransport, getTransportSync } from "./transport";
export type { DesktopTransport } from "./transport";
