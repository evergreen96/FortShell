import type { TreeNode } from "../lib/workspaceTree";
import type { WorkspaceEntry } from "../lib/types";
import { FileTree } from "./FileTree";

type WorkspaceSidebarProps = {
  tree: TreeNode[];
  workspaceEntries: WorkspaceEntry[];
  selectedEntry: WorkspaceEntry | null;
  expandedDirectories: Set<string>;
  denyGlobs: string[];
  onSelectPath: (path: string, isDir: boolean) => void;
  onHideRule: (rule: string | null) => void;
  onAllowRule: (rule: string) => void;
};

export function WorkspaceSidebar({
  tree,
  workspaceEntries,
  selectedEntry,
  expandedDirectories,
  denyGlobs,
  onSelectPath,
  onHideRule,
  onAllowRule,
}: WorkspaceSidebarProps) {
  return (
    <aside className="sidebar">
      <section className="panel file-tree-panel">
        <header className="panel-header">
          <div>
            <p className="panel-kicker">File Tree</p>
            <h2>Workspace</h2>
          </div>
          <span className="panel-count">{workspaceEntries.length}</span>
        </header>
        {workspaceEntries.length === 0 ? (
          <div className="empty-card">No visible entries.</div>
        ) : (
          <FileTree
            tree={tree}
            selectedPath={selectedEntry?.path ?? null}
            expandedDirectories={expandedDirectories}
            onSelectPath={onSelectPath}
          />
        )}
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <p className="panel-kicker">Policy Settings</p>
            <h2>Selected scope</h2>
          </div>
        </header>

        {selectedEntry ? (
          <div className="selection-card">
            <strong>{selectedEntry.display_name}</strong>
            <p>{selectedEntry.path}</p>
            <p>
              {selectedEntry.suggested_deny_rule
                ? `Suggested rule: ${selectedEntry.suggested_deny_rule}`
                : "This item is outside policy-managed workspace scope."}
            </p>
            <button
              type="button"
              className="primary-button"
              disabled={selectedEntry.suggested_deny_rule === null}
              onClick={() => onHideRule(selectedEntry.suggested_deny_rule)}
            >
              Hide from AI
            </button>
          </div>
        ) : (
          <div className="empty-card">Select an item to mutate policy.</div>
        )}

        <div className="rule-list">
          {denyGlobs.map((rule) => (
            <div key={rule} className="rule-row">
              <code>{rule}</code>
              <button type="button" className="secondary-button" onClick={() => onAllowRule(rule)}>
                Allow
              </button>
            </div>
          ))}
          {denyGlobs.length === 0 ? <div className="empty-card">No deny rules configured.</div> : null}
        </div>
      </section>
    </aside>
  );
}
