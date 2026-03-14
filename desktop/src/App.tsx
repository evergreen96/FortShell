import { useEffect, useMemo, useReducer } from "react";

import { EditorPane } from "./components/EditorPane";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { TerminalPane } from "./components/TerminalPane";
import { WorkspaceSidebar } from "./components/WorkspaceSidebar";
import {
  applyEditorProposal,
  createTerminal,
  loadDesktopShell,
  loadEditorFile,
  mutatePolicy,
  rejectEditorProposal,
  resolveApiBase,
  saveEditorFile,
  runTerminalCommand,
  stageEditorChange,
} from "./lib/api";
import { appReducer, initialState } from "./lib/appState";
import { buildTree, directoryPaths } from "./lib/workspaceTree";

function formatTime(value: Date | null): string {
  if (value === null) {
    return "Not synced";
  }
  return value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function App() {
  const [state, dispatch] = useReducer(appReducer, initialState);

  const {
    snapshot,
    targetDraft,
    selectedPath,
    selectedTerminalId,
    expandedDirectories,
    editorFile,
    editorDraft,
    stagedProposal,
    renderedPreview,
    terminalCommand,
    terminalOutputById,
    flash,
    loadState,
    lastSyncedAt,
  } = state;

  const workspaceEntries = snapshot?.workspace_panel.workspace.entries ?? [];
  const tree = useMemo(() => buildTree(workspaceEntries), [workspaceEntries]);
  const selectedEntry =
    workspaceEntries.find((entry) => entry.path === selectedPath) ?? workspaceEntries[0] ?? null;
  const selectedTerminal =
    snapshot?.terminals.items.find((t) => t.terminal_id === selectedTerminalId) ??
    snapshot?.terminals.items.find((t) => t.terminal_id === snapshot.terminals.active_terminal_id) ??
    snapshot?.terminals.items[0] ??
    null;
  const fileDirty = editorFile !== null && editorDraft !== editorFile.content;
  const terminalOutput = selectedTerminal !== null ? terminalOutputById[selectedTerminal.terminal_id] ?? "" : "";
  const terminalTranscript =
    terminalOutput.length > 0
      ? [`$ ${terminalCommand}`, terminalOutput]
      : selectedTerminal?.command_history.length
        ? selectedTerminal.command_history
        : selectedTerminal?.inbox ?? [];

  useEffect(() => {
    void refreshDesktop(".");
  }, []);

  // Global keyboard shortcuts: Ctrl+1 = file tree, Ctrl+2 = editor, Ctrl+3 = terminal
  useEffect(() => {
    function handleGlobalKeyDown(event: KeyboardEvent) {
      if (!event.ctrlKey || event.shiftKey || event.altKey || event.metaKey) return;
      switch (event.key) {
        case "1": {
          event.preventDefault();
          const tree = document.querySelector<HTMLElement>(".file-tree-viewport");
          tree?.focus();
          return;
        }
        case "2": {
          event.preventDefault();
          // Monaco's actual editable textarea lives inside .monaco-editor
          const monacoTextarea = document.querySelector<HTMLTextAreaElement>(".monaco-editor .inputarea");
          if (monacoTextarea) {
            monacoTextarea.focus();
          } else {
            // Fallback: focus the container so Monaco picks it up
            const surface = document.querySelector<HTMLElement>(".monaco-surface");
            surface?.focus();
          }
          return;
        }
        case "3": {
          event.preventDefault();
          const input = document.querySelector<HTMLElement>(".terminal-command-form input");
          input?.focus();
          return;
        }
        default:
          return;
      }
    }
    document.addEventListener("keydown", handleGlobalKeyDown);
    return () => document.removeEventListener("keydown", handleGlobalKeyDown);
  }, []);

  // Auto-dismiss flash/error banner after 5 seconds
  useEffect(() => {
    if (!flash) return;
    const timer = setTimeout(() => dispatch({ type: "SET_FLASH", message: "" }), 5000);
    return () => clearTimeout(timer);
  }, [flash]);

  useEffect(() => {
    if (selectedEntry === null || selectedEntry.is_dir) {
      dispatch({ type: "EDITOR_CLEARED" });
      return;
    }
    void loadEditor(selectedEntry.path);
  }, [selectedEntry?.path, selectedEntry?.is_dir]);

  async function refreshDesktop(target: string) {
    try {
      const nextSnapshot = await loadDesktopShell(target);
      const dirs = directoryPaths(nextSnapshot.workspace_panel.workspace.entries);
      dispatch({ type: "DESKTOP_LOADED", snapshot: nextSnapshot, dirs, selectedPath });
    } catch (error) {
      dispatch({ type: "DESKTOP_ERROR", message: error instanceof Error ? error.message : "Failed to load desktop shell" });
    }
  }

  async function loadEditor(path: string) {
    try {
      const file = await loadEditorFile(path);
      dispatch({ type: "EDITOR_LOADED", file });
    } catch (error) {
      dispatch({ type: "EDITOR_ERROR", message: error instanceof Error ? error.message : "Failed to load editor file" });
    }
  }

  async function mutateRule(action: "deny" | "allow", rule: string | null) {
    if (snapshot === null || rule === null) return;
    try {
      await mutatePolicy(action, snapshot.workspace_panel.target, rule);
      await refreshDesktop(snapshot.workspace_panel.target);
      dispatch({ type: "SET_FLASH", message: action === "deny" ? `Hidden ${rule}` : `Restored ${rule}` });
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : "Failed to mutate policy" });
    }
  }

  async function stageCurrentEditor() {
    if (selectedEntry === null || selectedEntry.is_dir) return;
    try {
      const response = await stageEditorChange({ target: selectedEntry.path, content: editorDraft });
      dispatch({ type: "STAGED", proposal: response.proposal, rendered: response.rendered, message: `Staged ${response.proposal.proposal_id}` });
      await refreshDesktop(targetDraft.trim() || ".");
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : "Failed to stage editor change" });
    }
  }

  async function saveCurrentEditor() {
    if (selectedEntry === null || selectedEntry.is_dir) return;
    try {
      const response = await saveEditorFile({ target: selectedEntry.path, content: editorDraft });
      dispatch({
        type: "SAVED",
        file: {
          kind: "editor_file",
          target: response.target,
          path: response.path,
          managed: response.managed,
          byte_size: response.byte_size,
          content: response.content,
          proposal: response.proposal,
          rendered: response.rendered,
        },
        message: `Saved ${response.path}`,
      });
      await refreshDesktop(targetDraft.trim() || ".");
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : "Failed to save editor file" });
    }
  }

  async function resolveStaged(action: "apply" | "reject") {
    if (stagedProposal === null) return;
    try {
      const response =
        action === "apply"
          ? await applyEditorProposal({ proposal_id: stagedProposal.proposal_id })
          : await rejectEditorProposal({ proposal_id: stagedProposal.proposal_id });
      dispatch({ type: "RESOLVED", message: `${action === "apply" ? "Applied" : "Rejected"} ${response.proposal.proposal_id}` });
      await refreshDesktop(targetDraft.trim() || ".");
      if (selectedEntry && !selectedEntry.is_dir) {
        await loadEditor(selectedEntry.path);
      }
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : `Failed to ${action} staged change` });
    }
  }

  async function createTerminalSession(transport: "runner" | "host", runnerMode?: "projected" | "strict") {
    try {
      const created = await createTerminal({
        name:
          transport === "host"
            ? `unfiltered-${(snapshot?.terminals.count ?? 0) + 1}`
            : runnerMode === "strict"
              ? `strict-shell-${(snapshot?.terminals.count ?? 0) + 1}`
              : `managed-shell-${(snapshot?.terminals.count ?? 0) + 1}`,
        transport,
        runner_mode: runnerMode,
      });
      await refreshDesktop(targetDraft.trim() || ".");
      dispatch({ type: "SELECT_TERMINAL", terminalId: created.terminal.terminal_id });
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : "Failed to create terminal" });
    }
  }

  async function createPtyTerminal() {
    try {
      const created = await createTerminal({
        name: `pty-${(snapshot?.terminals.count ?? 0) + 1}`,
        transport: "runner",
        runner_mode: "projected",
        io_mode: "pty",
      });
      await refreshDesktop(targetDraft.trim() || ".");
      dispatch({ type: "SELECT_TERMINAL", terminalId: created.terminal.terminal_id });
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : "Failed to create PTY terminal" });
    }
  }

  async function runSelectedTerminal() {
    if (selectedTerminal === null || terminalCommand.trim().length === 0) return;
    if (selectedTerminal.status !== "active") return;
    try {
      const result = await runTerminalCommand({
        terminal_id: selectedTerminal.terminal_id,
        command: terminalCommand.trim(),
      });
      dispatch({ type: "TERMINAL_OUTPUT", terminalId: result.terminal.terminal_id, output: result.output });
      await refreshDesktop(targetDraft.trim() || ".");
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : "Failed to run terminal command" });
    }
  }

  async function relaunchTerminal(staleTerminal: import("./lib/types").TerminalInspection) {
    const transport = staleTerminal.transport === "host" ? "host" as const : "runner" as const;
    const runnerMode = staleTerminal.runner_mode === "strict" ? "strict" as const : "projected" as const;
    const count = (snapshot?.terminals.count ?? 0) + 1;
        const name = transport === "host"
      ? `unfiltered-${count}`
      : runnerMode === "strict"
        ? `strict-shell-${count}`
        : `managed-shell-${count}`;
    try {
      const created = await createTerminal({ name, transport, runner_mode: transport === "runner" ? runnerMode : undefined });
      await refreshDesktop(targetDraft.trim() || ".");
      dispatch({ type: "SELECT_TERMINAL", terminalId: created.terminal.terminal_id });
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : "Failed to relaunch terminal" });
    }
  }

  async function relaunchAllManaged() {
    const staleManaged = (snapshot?.terminals.items ?? []).filter(
      (t) => t.status !== "active" && t.transport !== "host",
    );
    if (staleManaged.length === 0) return;
    let lastCreatedId: string | null = null;
    const baseCount = snapshot?.terminals.count ?? 0;
    try {
      for (let i = 0; i < staleManaged.length; i++) {
        const terminal = staleManaged[i];
        const transport = terminal.transport === "host" ? "host" as const : "runner" as const;
        const runnerMode = terminal.runner_mode === "strict" ? "strict" as const : "projected" as const;
        const count = baseCount + i + 1;
        const name = transport === "host"
          ? `unfiltered-${count}`
          : runnerMode === "strict"
            ? `strict-shell-${count}`
            : `managed-shell-${count}`;
        const created = await createTerminal({ name, transport, runner_mode: transport === "runner" ? runnerMode : undefined });
        lastCreatedId = created.terminal.terminal_id;
      }
      await refreshDesktop(targetDraft.trim() || ".");
      if (lastCreatedId) {
        dispatch({ type: "SELECT_TERMINAL", terminalId: lastCreatedId });
      }
    } catch (error) {
      dispatch({ type: "ACTION_ERROR", message: error instanceof Error ? error.message : "Failed to relaunch terminals" });
    }
  }

  const banner =
    flash ||
    (snapshot?.workspace_panel.workspace_index.stale
      ? `Workspace index is stale: ${snapshot.workspace_panel.workspace_index.stale_reasons.join(", ")}`
      : "");

  return (
    <div className="desktop-shell">
      <main className="shell-frame">
        <header className="topbar">
          <div className="topbar-brand">
            <span className="topbar-dot" />
            <strong>AI IDE Desktop</strong>
          </div>
          <div className="topbar-center">
            <span className="workspace-pill">Workspace: {targetDraft || "."}</span>
          </div>
          <div className="topbar-signals">
            <span className="signal-pill">
              {snapshot?.workspace_panel.policy.deny_globs.length ?? 0} rules
            </span>
            <span className="signal-pill">{snapshot?.terminals.count ?? 0} terminals</span>
            <span className="signal-pill">{editorFile?.path ?? "select a file"}</span>
          </div>
        </header>

        {banner ? (
          <section className={`banner ${loadState === "error" ? "banner-error" : "banner-info"}`}>
            {banner}
          </section>
        ) : null}

        <section className="toolbar-strip">
          <form
            className="target-form"
            onSubmit={(event) => {
              event.preventDefault();
              void refreshDesktop(targetDraft.trim() || ".");
            }}
          >
            <label htmlFor="target">Workspace target</label>
            <div className="target-row">
              <input
                id="target"
                type="text"
                value={targetDraft}
                onChange={(event) => dispatch({ type: "SET_TARGET_DRAFT", value: event.target.value })}
                placeholder="."
              />
              <button type="submit" className="primary-button">
                Refresh
              </button>
            </div>
            <p className="target-hint">
              API <code>{resolveApiBase()}</code> - last sync {formatTime(lastSyncedAt)}
            </p>
          </form>
        </section>

        <section className="product-grid">
          <ErrorBoundary>
            <WorkspaceSidebar
              tree={tree}
              workspaceEntries={workspaceEntries}
              selectedEntry={selectedEntry}
              expandedDirectories={expandedDirectories}
              denyGlobs={snapshot?.workspace_panel.policy.deny_globs ?? []}
              onSelectPath={(path, isDir) => dispatch({ type: "SELECT_PATH", path, isDir })}
              onHideRule={(rule) => { void mutateRule("deny", rule); }}
              onAllowRule={(rule) => { void mutateRule("allow", rule); }}
            />
          </ErrorBoundary>

          <section className="main-stage">
            <ErrorBoundary>
              <TerminalPane
                terminals={snapshot?.terminals.items ?? []}
                selectedTerminal={selectedTerminal}
                terminalCommand={terminalCommand}
                terminalTranscript={terminalTranscript}
                onSelectTerminal={(id) => dispatch({ type: "SELECT_TERMINAL", terminalId: id })}
                onTerminalCommandChange={(value) => dispatch({ type: "SET_TERMINAL_COMMAND", value })}
                onCreateManaged={() => { void createTerminalSession("runner", "projected"); }}
                onCreateStrict={() => { void createTerminalSession("runner", "strict"); }}
                onCreateUnsafe={() => { void createTerminalSession("host"); }}
                onCreatePty={() => { void createPtyTerminal(); }}
                onRunSelected={() => { void runSelectedTerminal(); }}
                onRelaunchTerminal={(terminal) => { void relaunchTerminal(terminal); }}
                onRelaunchAllManaged={() => { void relaunchAllManaged(); }}
              />
            </ErrorBoundary>

            <ErrorBoundary>
              <EditorPane
                selectedEntry={selectedEntry}
                editorFile={editorFile}
                editorDraft={editorDraft}
                fileDirty={fileDirty}
                stagedProposal={stagedProposal}
                renderedPreview={renderedPreview}
                onEditorDraftChange={(value) => dispatch({ type: "SET_EDITOR_DRAFT", value })}
                onReload={() => {
                  if (selectedEntry && !selectedEntry.is_dir) {
                    void loadEditor(selectedEntry.path);
                  }
                }}
                onSave={() => { void saveCurrentEditor(); }}
                onStage={() => { void stageCurrentEditor(); }}
                onResolve={(action) => { void resolveStaged(action); }}
              />
            </ErrorBoundary>
          </section>
        </section>
      </main>
    </div>
  );
}
