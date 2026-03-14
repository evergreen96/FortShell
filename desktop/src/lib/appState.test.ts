import { describe, it, expect } from "vitest";
import { appReducer, initialState } from "./appState";
import type { DesktopShellSnapshot } from "./types";

function makeSnapshot(overrides?: Partial<DesktopShellSnapshot>): DesktopShellSnapshot {
  return {
    kind: "desktop_shell",
    target: ".",
    workspace_panel: {
      kind: "workspace_panel",
      target: ".",
      workspace: {
        entries: [
          { path: "src", name: "src", is_dir: true, display_name: "src", display_path: "src", suggested_deny_rule: "src/**" },
          { path: "src/main.js", name: "main.js", is_dir: false, display_name: "main.js", display_path: "src/main.js", suggested_deny_rule: "src/main.js" },
        ],
      },
      policy: {
        kind: "policy",
        version: 1,
        deny_globs: [],
        execution_session_id: "exec-1",
        agent_session_id: "agent-1",
      },
      session: { execution_session_id: "exec-1", agent_session_id: "agent-1" },
      workspace_index: {
        policy_version: 1,
        stale: false,
        stale_reasons: [],
        entry_count: 2,
        file_count: 1,
        directory_count: 1,
      },
    },
    terminals: {
      count: 0,
      active_terminal_id: null,
      items: [],
    },
    ...overrides,
  };
}

describe("appReducer", () => {
  it("DESKTOP_LOADED sets snapshot and moves to ready state", () => {
    const snapshot = makeSnapshot();
    const next = appReducer(initialState, {
      type: "DESKTOP_LOADED",
      snapshot,
      dirs: new Set(["src"]),
      selectedPath: null,
    });
    expect(next.loadState).toBe("ready");
    expect(next.snapshot).toBe(snapshot);
    expect(next.selectedPath).toBe("src");
    expect(next.lastSyncedAt).toBeInstanceOf(Date);
  });

  it("DESKTOP_ERROR sets flash and error state", () => {
    const next = appReducer(initialState, {
      type: "DESKTOP_ERROR",
      message: "connection refused",
    });
    expect(next.loadState).toBe("error");
    expect(next.flash).toBe("connection refused");
  });

  it("SELECT_PATH on file does not toggle expandedDirectories", () => {
    const state = { ...initialState, expandedDirectories: new Set(["src"]) };
    const next = appReducer(state, { type: "SELECT_PATH", path: "src/main.js", isDir: false });
    expect(next.selectedPath).toBe("src/main.js");
    expect(next.expandedDirectories).toEqual(new Set(["src"]));
  });

  it("SELECT_PATH on directory toggles expandedDirectories", () => {
    const state = { ...initialState, expandedDirectories: new Set<string>() };
    const next1 = appReducer(state, { type: "SELECT_PATH", path: "src", isDir: true });
    expect(next1.expandedDirectories.has("src")).toBe(true);

    const next2 = appReducer(next1, { type: "SELECT_PATH", path: "src", isDir: true });
    expect(next2.expandedDirectories.has("src")).toBe(false);
  });

  it("SELECT_TERMINAL updates selectedTerminalId", () => {
    const next = appReducer(initialState, { type: "SELECT_TERMINAL", terminalId: "term-1" });
    expect(next.selectedTerminalId).toBe("term-1");
  });

  it("TERMINAL_OUTPUT stores per-terminal output", () => {
    const next = appReducer(initialState, {
      type: "TERMINAL_OUTPUT",
      terminalId: "term-1",
      output: "hello world",
    });
    expect(next.terminalOutputById["term-1"]).toBe("hello world");
  });

  it("EDITOR_LOADED sets file and draft", () => {
    const file = {
      kind: "editor_file" as const,
      target: "src/main.js",
      path: "src/main.js",
      managed: true,
      byte_size: 10,
      content: "console.log('hi');",
      proposal: null,
      rendered: null,
    };
    const next = appReducer(initialState, { type: "EDITOR_LOADED", file });
    expect(next.editorFile).toBe(file);
    expect(next.editorDraft).toBe("console.log('hi');");
  });

  it("EDITOR_CLEARED resets editor state", () => {
    const state = {
      ...initialState,
      editorFile: { kind: "editor_file" as const, target: "x", path: "x", managed: true, byte_size: 1, content: "x", proposal: null, rendered: null },
      editorDraft: "modified",
    };
    const next = appReducer(state, { type: "EDITOR_CLEARED" });
    expect(next.editorFile).toBeNull();
    expect(next.editorDraft).toBe("");
  });

  it("SET_FLASH and ACTION_ERROR both update flash", () => {
    const a = appReducer(initialState, { type: "SET_FLASH", message: "done" });
    expect(a.flash).toBe("done");

    const b = appReducer(initialState, { type: "ACTION_ERROR", message: "fail" });
    expect(b.flash).toBe("fail");
    expect(b.loadState).toBe("error");
  });

  it("DESKTOP_LOADED preserves selectedTerminalId when terminal still exists", () => {
    const snapshot = makeSnapshot({
      terminals: {
        count: 1,
        active_terminal_id: "term-1",
        items: [{
          terminal_id: "term-1",
          name: "shell-1",
          created_at: "",
          transport: "runner",
          io_mode: "command",
          runner_mode: "projected",
          status: "active",
          stale_reason: null,
          execution_session_id: "sess-1",
          bound_agent_run_id: null,
          command_history: [],
          inbox: [],
          inbox_entries: [],
          bound_run: null,
        }],
      },
    });
    const state = { ...initialState, selectedTerminalId: "term-1" };
    const next = appReducer(state, {
      type: "DESKTOP_LOADED",
      snapshot,
      dirs: new Set(["src"]),
      selectedPath: null,
    });
    expect(next.selectedTerminalId).toBe("term-1");
  });
});
