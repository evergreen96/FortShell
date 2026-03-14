import type {
  DesktopShellSnapshot,
  EditorFileSnapshot,
  ReviewProposal,
} from "./types";

export type LoadState = "idle" | "ready" | "error";

export type AppState = {
  snapshot: DesktopShellSnapshot | null;
  targetDraft: string;
  selectedPath: string | null;
  selectedTerminalId: string | null;
  expandedDirectories: Set<string>;
  editorFile: EditorFileSnapshot | null;
  editorDraft: string;
  stagedProposal: ReviewProposal | null;
  renderedPreview: string;
  terminalCommand: string;
  terminalOutputById: Record<string, string>;
  flash: string;
  loadState: LoadState;
  lastSyncedAt: Date | null;
};

export const initialState: AppState = {
  snapshot: null,
  targetDraft: ".",
  selectedPath: null,
  selectedTerminalId: null,
  expandedDirectories: new Set(),
  editorFile: null,
  editorDraft: "",
  stagedProposal: null,
  renderedPreview: "",
  terminalCommand: "git status",
  terminalOutputById: {},
  flash: "",
  loadState: "idle",
  lastSyncedAt: null,
};

export type AppAction =
  | { type: "DESKTOP_LOADED"; snapshot: DesktopShellSnapshot; dirs: Set<string>; selectedPath: string | null }
  | { type: "DESKTOP_ERROR"; message: string }
  | { type: "SET_TARGET_DRAFT"; value: string }
  | { type: "SELECT_PATH"; path: string; isDir: boolean }
  | { type: "SELECT_TERMINAL"; terminalId: string }
  | { type: "EDITOR_LOADED"; file: EditorFileSnapshot }
  | { type: "EDITOR_CLEARED" }
  | { type: "EDITOR_ERROR"; message: string }
  | { type: "SET_EDITOR_DRAFT"; value: string }
  | { type: "SAVED"; file: EditorFileSnapshot; message: string }
  | { type: "STAGED"; proposal: ReviewProposal; rendered: string; message: string }
  | { type: "RESOLVED"; message: string }
  | { type: "SET_TERMINAL_COMMAND"; value: string }
  | { type: "TERMINAL_OUTPUT"; terminalId: string; output: string }
  | { type: "SET_FLASH"; message: string }
  | { type: "ACTION_ERROR"; message: string };

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "DESKTOP_LOADED": {
      const { snapshot, dirs, selectedPath: currentSelected } = action;
      const entries = snapshot.workspace_panel.workspace.entries;

      const nextSelectedPath =
        currentSelected && entries.some((e) => e.path === currentSelected)
          ? currentSelected
          : entries[0]?.path ?? null;

      const nextTerminalId =
        state.selectedTerminalId &&
        snapshot.terminals.items.some((t) => t.terminal_id === state.selectedTerminalId)
          ? state.selectedTerminalId
          : snapshot.terminals.active_terminal_id ?? snapshot.terminals.items[0]?.terminal_id ?? null;

      let nextExpanded: Set<string>;
      if (state.expandedDirectories.size === 0) {
        nextExpanded = new Set(dirs);
      } else {
        nextExpanded = new Set<string>();
        state.expandedDirectories.forEach((p) => {
          if (dirs.has(p)) nextExpanded.add(p);
        });
      }
      if (nextSelectedPath) {
        ancestorPaths(nextSelectedPath).forEach((p) => nextExpanded.add(p));
      }

      return {
        ...state,
        snapshot,
        targetDraft: snapshot.workspace_panel.target,
        selectedPath: nextSelectedPath,
        selectedTerminalId: nextTerminalId,
        expandedDirectories: nextExpanded,
        flash: "",
        loadState: "ready",
        lastSyncedAt: new Date(),
      };
    }

    case "DESKTOP_ERROR":
      return { ...state, flash: action.message, loadState: "error" };

    case "SET_TARGET_DRAFT":
      return { ...state, targetDraft: action.value };

    case "SELECT_PATH": {
      if (!action.isDir) {
        return { ...state, selectedPath: action.path };
      }
      const next = new Set(state.expandedDirectories);
      if (next.has(action.path)) {
        next.delete(action.path);
      } else {
        next.add(action.path);
      }
      return { ...state, selectedPath: action.path, expandedDirectories: next };
    }

    case "SELECT_TERMINAL":
      return { ...state, selectedTerminalId: action.terminalId };

    case "EDITOR_LOADED":
      return {
        ...state,
        editorFile: action.file,
        editorDraft: action.file.content,
        stagedProposal: action.file.proposal,
        renderedPreview: action.file.rendered ?? "",
      };

    case "EDITOR_CLEARED":
      return { ...state, editorFile: null, editorDraft: "", stagedProposal: null, renderedPreview: "" };

    case "EDITOR_ERROR":
      return { ...state, flash: action.message, loadState: "error" };

    case "SET_EDITOR_DRAFT":
      return { ...state, editorDraft: action.value };

    case "SAVED":
      return {
        ...state,
        editorFile: action.file,
        editorDraft: action.file.content,
        stagedProposal: action.file.proposal,
        renderedPreview: action.file.rendered ?? "",
        flash: action.message,
      };

    case "STAGED":
      return { ...state, stagedProposal: action.proposal, renderedPreview: action.rendered, flash: action.message };

    case "RESOLVED":
      return { ...state, stagedProposal: null, renderedPreview: "", flash: action.message };

    case "SET_TERMINAL_COMMAND":
      return { ...state, terminalCommand: action.value };

    case "TERMINAL_OUTPUT":
      return {
        ...state,
        terminalOutputById: { ...state.terminalOutputById, [action.terminalId]: action.output },
      };

    case "SET_FLASH":
      return { ...state, flash: action.message };

    case "ACTION_ERROR":
      return { ...state, flash: action.message, loadState: "error" };

    default:
      return state;
  }
}

function ancestorPaths(filePath: string): string[] {
  const parts = filePath.split("/");
  const ancestors: string[] = [];
  for (let i = 1; i < parts.length; i++) {
    ancestors.push(parts.slice(0, i).join("/"));
  }
  return ancestors;
}
