export type WorkspaceEntry = {
  path: string;
  name: string;
  is_dir: boolean;
  display_name: string;
  display_path: string;
  suggested_deny_rule: string | null;
};

export type WorkspacePanelSnapshot = {
  kind: "workspace_panel";
  target: string;
  workspace: {
    entries: WorkspaceEntry[];
  };
  policy: {
    kind: "policy";
    version: number;
    deny_globs: string[];
    execution_session_id: string;
    agent_session_id: string;
  };
  session: {
    execution_session_id: string;
    agent_session_id: string;
  };
  workspace_index: {
    policy_version: number;
    stale: boolean;
    stale_reasons: string[];
    entry_count: number;
    file_count: number;
    directory_count: number;
  };
};

export type WorkspacePanelMutation = {
  kind: "workspace_panel_policy_change";
  change: {
    kind: "policy_change";
    action: "add" | "remove";
    rule: string;
    changed: boolean;
    policy_version: number;
    execution_session_id: string;
    agent_session_id: string;
  };
  panel: WorkspacePanelSnapshot;
};

export type ReviewProposal = {
  proposal_id: string;
  target: string;
  session_id: string;
  agent_session_id: string;
  created_at: string;
  updated_at: string;
  status: string;
  base_sha256: string | null;
  base_text: string | null;
  proposed_text: string;
};

export type TerminalInspection = {
  terminal_id: string;
  name: string;
  created_at: string;
  transport: string;
  runner_mode: string | null;
  status: string;
  stale_reason: string | null;
  execution_session_id: string | null;
  bound_agent_run_id: string | null;
  io_mode: "command" | "pty";
  command_history: string[];
  inbox: string[];
  inbox_entries: Array<{
    kind: string;
    text: string;
    created_at: string;
    event_kind?: string | null;
    src_terminal_id?: string | null;
  }>;
  bound_run: {
    run_id: string;
    status: string;
    backend: string;
    process_source: string;
    process_state: string;
    process_pid: number | null;
    process_returncode: number | null;
  } | null;
};

export type PtyWriteRequest = { terminal_id: string; data: string };
export type PtyResizeRequest = { terminal_id: string; cols: number; rows: number };

export type DesktopShellSnapshot = {
  kind: "desktop_shell";
  target: string;
  workspace_panel: WorkspacePanelSnapshot;
  terminals: {
    count: number;
    active_terminal_id: string | null;
    items: TerminalInspection[];
  };
};

export type EditorFileSnapshot = {
  kind: "editor_file";
  target: string;
  path: string;
  managed: boolean;
  byte_size: number;
  content: string;
  proposal: ReviewProposal | null;
  rendered: string | null;
};

export type EditorSaveResponse = {
  kind: "editor_save";
  target: string;
  path: string;
  managed: boolean;
  byte_size: number;
  content: string;
  proposal: ReviewProposal | null;
  rendered: string | null;
};

export type EditorStageResponse = {
  kind: "editor_stage" | "editor_apply" | "editor_reject";
  proposal: ReviewProposal;
  rendered: string;
};
