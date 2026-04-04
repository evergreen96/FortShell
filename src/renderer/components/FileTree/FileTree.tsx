import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { getExplorerContextMenuAction, isPathProtected } from "../../lib/protection-paths";
import type { CompiledProtectionEntry } from "../../lib/types";

type FileEntry = {
  name: string;
  path: string;
  isDirectory: boolean;
  children?: FileEntry[];
};

type FileTreeProps = {
  rootPath: string | null;
  protectedPaths: Set<string>;
  compiledEntries: CompiledProtectionEntry[];
  onProtect: (path: string) => void;
  onUnprotect: (path: string) => void;
  onViewProtection: (ruleId: string) => void;
  onOpenFolder: () => void;
};

export type FileTreeIconVariant = "folder" | "file";

export function getFileTreeRelativePath(rootPath: string, entryPath: string): string {
  const normalizedRoot = rootPath.replace(/\\/g, "/").replace(/\/+$/, "");
  const normalizedEntry = entryPath.replace(/\\/g, "/");

  if (normalizedEntry === normalizedRoot) {
    return normalizedEntry.split("/").pop() ?? normalizedEntry;
  }

  if (normalizedEntry.startsWith(`${normalizedRoot}/`)) {
    return normalizedEntry.slice(normalizedRoot.length + 1);
  }

  return normalizedEntry;
}

export function getFileTreeIconVariant({
  isDirectory,
  expanded,
  loading,
}: {
  isDirectory: boolean;
  expanded: boolean;
  loading: boolean;
}): FileTreeIconVariant {
  if (!isDirectory) {
    return "file";
  }
  void expanded;
  void loading;
  return "folder";
}

export function FileTree({
  rootPath,
  protectedPaths,
  compiledEntries,
  onProtect,
  onUnprotect,
  onViewProtection,
  onOpenFolder,
}: FileTreeProps) {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    entry: FileEntry;
  } | null>(null);

  const refreshTree = useCallback(() => {
    if (!rootPath) {
      setEntries([]);
      return;
    }
    window.electronAPI.workspaceFiles(rootPath).then(setEntries);
  }, [rootPath]);

  useEffect(() => {
    refreshTree();
    if (!rootPath) return;
    const unlisten = window.electronAPI.onWorkspaceChanged(() => {
      refreshTree();
    });
    return unlisten;
  }, [rootPath, refreshTree]);

  // Close context menu on click outside
  useEffect(() => {
    if (!contextMenu) return;
    const handler = () => setContextMenu(null);
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, [contextMenu]);

  const contextMenuAction = contextMenu
    ? getExplorerContextMenuAction(contextMenu.entry.path, compiledEntries)
    : null;

  if (!rootPath) {
    return (
      <div className="filetree-empty">
        <button
          className="filetree-open-btn"
          onClick={onOpenFolder}
        >
          Open Folder
        </button>
      </div>
    );
  }

  return (
    <div className="filetree">
      <div className="filetree-header">
        <span className="filetree-eyebrow">Project Explorer</span>
        <div className="filetree-header-row">
          <span className="filetree-root-name">{rootPath.split(/[/\\]/).pop()}</span>
          <span className="filetree-root-meta">{entries.length} items</span>
        </div>
      </div>
      <div className="filetree-list">
        {entries.map((entry) => (
          <FileTreeNode
            key={entry.path}
            entry={entry}
            depth={0}
            rootPath={rootPath}
            protectedPaths={protectedPaths}
            onContextMenu={(e, entry) => {
              e.preventDefault();
              setContextMenu({ x: e.clientX, y: e.clientY, entry });
            }}
          />
        ))}
      </div>
      {contextMenu && createPortal(
        <div
          className="filetree-context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          {contextMenuAction?.kind === "remove" ? (
            <button
              onClick={() => {
                onUnprotect(contextMenu.entry.path);
                setContextMenu(null);
              }}
            >
              Remove Protection
            </button>
          ) : contextMenuAction?.kind === "view-protection" ? (
            <button
              onClick={() => {
                onViewProtection(contextMenuAction.sourceRuleId);
                setContextMenu(null);
              }}
            >
              View Protection
            </button>
          ) : (
            <button
              onClick={() => {
                onProtect(contextMenu.entry.path);
                setContextMenu(null);
              }}
            >
              Protect
            </button>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}

function FileTreeNode({
  entry,
  depth,
  rootPath,
  protectedPaths,
  onContextMenu,
}: {
  entry: FileEntry;
  depth: number;
  rootPath: string;
  protectedPaths: Set<string>;
  onContextMenu: (e: React.MouseEvent, entry: FileEntry) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<FileEntry[] | undefined>(entry.children);
  const [loading, setLoading] = useState(false);
  const isProtected = isPathProtected(entry.path, protectedPaths);
  const iconVariant = getFileTreeIconVariant({
    isDirectory: entry.isDirectory,
    expanded,
    loading,
  });
  const relativePath = getFileTreeRelativePath(rootPath, entry.path);

  async function handleToggle() {
    if (!entry.isDirectory) return;
    if (!expanded && !children) {
      setLoading(true);
      try {
        const result = await window.electronAPI.workspaceExpand(entry.path, rootPath);
        setChildren(result);
      } catch {
        setChildren([]);
      }
      setLoading(false);
    }
    setExpanded((prev) => !prev);
  }

  return (
    <div>
      <div
        className={`filetree-node ${isProtected ? "filetree-node-protected" : ""}`}
        data-relative-path={relativePath}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleToggle}
        onContextMenu={(e) => onContextMenu(e, entry)}
      >
        <span className={`filetree-icon filetree-icon-${iconVariant}`} aria-hidden="true">
          <FileTreeIcon variant={iconVariant} />
        </span>
        <span className="filetree-name">{entry.name}</span>
        {isProtected && <span className="filetree-lock" title="Protected">LOCK</span>}
      </div>
      {expanded && children && (
        <div>
          {children.map((child) => (
            <FileTreeNode
              key={child.path}
              entry={child}
              depth={depth + 1}
              rootPath={rootPath}
              protectedPaths={protectedPaths}
              onContextMenu={onContextMenu}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FileTreeIcon({ variant }: { variant: FileTreeIconVariant }) {
  if (variant === "file") {
    return (
      <svg
        viewBox="0 0 16 16"
        className="filetree-icon-svg"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M4 1.75h5.05l2.95 2.95v8.05a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 12.75v-9.5A1.5 1.5 0 0 1 4 1.75Z"
          fill="currentColor"
          opacity="0.88"
        />
        <path
          d="M9.05 1.75V4.7H12"
          fill="currentColor"
          opacity="0.55"
        />
        <path
          d="M5.15 7.05H9.95M5.15 9.2H9.95M5.15 11.35H8.55"
          stroke="currentColor"
          strokeOpacity="0.72"
          strokeLinecap="round"
          strokeWidth="1"
        />
      </svg>
    );
  }

  return (
    <svg
      viewBox="0 0 16 16"
      className="filetree-icon-svg"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M1.75 4.45A1.7 1.7 0 0 1 3.45 2.75h2.1l1.12 1.2h5.88a1.7 1.7 0 0 1 1.7 1.7v5.9a1.7 1.7 0 0 1-1.7 1.7H3.45a1.7 1.7 0 0 1-1.7-1.7Z"
        fill="currentColor"
        opacity="0.88"
      />
      <path
        d="M1.75 5.35h12.5"
        stroke="currentColor"
        strokeOpacity="0.42"
        strokeWidth="1"
      />
    </svg>
  );
}
