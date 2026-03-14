import { useRef, useCallback } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor } from "monaco-editor";

import type { EditorFileSnapshot, ReviewProposal, WorkspaceEntry } from "../lib/types";

type EditorPaneProps = {
  selectedEntry: WorkspaceEntry | null;
  editorFile: EditorFileSnapshot | null;
  editorDraft: string;
  fileDirty: boolean;
  stagedProposal: ReviewProposal | null;
  renderedPreview: string;
  onEditorDraftChange: (value: string) => void;
  onReload: () => void;
  onSave: () => void;
  onStage: () => void;
  onResolve: (action: "apply" | "reject") => void;
};

const LANGUAGE_MAP: Record<string, string> = {
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  py: "python",
  rs: "rust",
  go: "go",
  java: "java",
  c: "c",
  cpp: "cpp",
  h: "c",
  hpp: "cpp",
  cs: "csharp",
  rb: "ruby",
  php: "php",
  swift: "swift",
  kt: "kotlin",
  scala: "scala",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  ps1: "powershell",
  sql: "sql",
  html: "html",
  htm: "html",
  css: "css",
  scss: "scss",
  less: "less",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  xml: "xml",
  md: "markdown",
  txt: "plaintext",
  dockerfile: "dockerfile",
  makefile: "makefile",
  graphql: "graphql",
  gql: "graphql",
};

function inferLanguage(path: string): string {
  const name = path.split("/").pop()?.toLowerCase() ?? "";
  if (name === "dockerfile") return "dockerfile";
  if (name === "makefile") return "makefile";
  const ext = name.split(".").pop() ?? "";
  return LANGUAGE_MAP[ext] ?? "plaintext";
}

export function EditorPane({
  selectedEntry,
  editorFile,
  editorDraft,
  fileDirty,
  stagedProposal,
  renderedPreview,
  onEditorDraftChange,
  onReload,
  onSave,
  onStage,
  onResolve,
}: EditorPaneProps) {
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);

  const handleEditorMount: OnMount = useCallback((editorInstance) => {
    editorRef.current = editorInstance;
  }, []);

  const handleEditorChange = useCallback(
    (value: string | undefined) => {
      onEditorDraftChange(value ?? "");
    },
    [onEditorDraftChange],
  );

  const language = editorFile ? inferLanguage(editorFile.path) : "plaintext";

  return (
    <section className="panel editor-panel">
      <header className="panel-header">
        <div>
          <p className="panel-kicker">Code Editor</p>
          <h2>{editorFile?.path ?? selectedEntry?.display_name ?? "Select a file"}</h2>
        </div>
        <div className="editor-flags">
          {editorFile ? (
            <span className="flag flag-lang">{language}</span>
          ) : null}
          <span className={`flag ${editorFile?.managed ? "flag-managed" : "flag-unmanaged"}`}>
            {editorFile ? (editorFile.managed ? "managed" : "outside") : "no-file"}
          </span>
          <span className={`flag ${fileDirty ? "flag-dirty" : "flag-clean"}`}>
            {fileDirty ? "modified" : "clean"}
          </span>
        </div>
      </header>

      {selectedEntry === null ? (
        <div className="empty-card">Select a file from the tree to start editing.</div>
      ) : selectedEntry.is_dir ? (
        <div className="empty-card">Choose a file, not a directory, to open the editor.</div>
      ) : editorFile === null ? (
        <div className="empty-card">Loading file...</div>
      ) : (
        <>
          <div className="editor-actions">
            <button type="button" className="secondary-button" onClick={onReload}>
              Reload
            </button>
            <button type="button" className="primary-button" disabled={!fileDirty} onClick={onSave}>
              Save
            </button>
            <button type="button" className="secondary-button" disabled={!fileDirty} onClick={onStage}>
              Stage change
            </button>
            <button
              type="button"
              className="secondary-button"
              disabled={stagedProposal === null}
              onClick={() => onResolve("apply")}
            >
              Apply staged
            </button>
            <button
              type="button"
              className="secondary-button"
              disabled={stagedProposal === null}
              onClick={() => onResolve("reject")}
            >
              Reject staged
            </button>
          </div>

          <div className="editor-workbench">
            <div className="monaco-surface">
              <Editor
                height="100%"
                language={language}
                value={editorDraft}
                theme="vs-dark"
                onChange={handleEditorChange}
                onMount={handleEditorMount}
                options={{
                  fontSize: 14,
                  fontFamily: "'Cascadia Mono', 'Consolas', 'SFMono-Regular', monospace",
                  minimap: { enabled: true },
                  scrollBeyondLastLine: false,
                  wordWrap: "on",
                  lineNumbers: "on",
                  renderLineHighlight: "line",
                  automaticLayout: true,
                  tabSize: 2,
                  bracketPairColorization: { enabled: true },
                  smoothScrolling: true,
                  cursorBlinking: "smooth",
                  padding: { top: 8, bottom: 8 },
                }}
              />
            </div>

            <aside className="editor-sidecar">
              <div className="sidecar-header">
                <strong>Staged Preview</strong>
                <span>{stagedProposal?.proposal_id ?? "no proposal"}</span>
              </div>
              {stagedProposal ? (
                <>
                  <div className="stage-banner">
                    <strong>{stagedProposal.status}</strong>
                    <span>{stagedProposal.updated_at}</span>
                  </div>
                  <pre className="diff-preview">{renderedPreview}</pre>
                </>
              ) : (
                <div className="empty-card">
                  Stage a change to see the review diff here before applying it.
                </div>
              )}
            </aside>
          </div>
        </>
      )}
    </section>
  );
}
