import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";

type FileEntry = {
  name: string;
  path: string;
  isDirectory: boolean;
  children?: FileEntry[];
};

type FileTreeProps = {
  rootPath: string | null;
  protectedPaths: Set<string>;
  onProtect: (path: string) => void;
  onUnprotect: (path: string) => void;
  onOpenFolder: () => void;
};

export function FileTree({
  rootPath,
  protectedPaths,
  onProtect,
  onUnprotect,
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
          {isPathProtected(contextMenu.entry.path, protectedPaths) ? (
            <button
              onClick={() => {
                onUnprotect(contextMenu.entry.path);
                setContextMenu(null);
              }}
            >
              Remove Protection
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

function isPathProtected(filePath: string, protectedPaths: Set<string>): boolean {
  if (protectedPaths.has(filePath)) return true;
  for (const p of protectedPaths) {
    // This file is inside a protected directory
    if (filePath.startsWith(p + "/")) return true;
    // This directory contains a protected file
    if (p.startsWith(filePath + "/")) return true;
  }
  return false;
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
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleToggle}
        onContextMenu={(e) => onContextMenu(e, entry)}
      >
        <span className={`filetree-icon ${entry.isDirectory ? "filetree-icon-directory" : "filetree-icon-file"}`}>
          {entry.isDirectory ? (loading ? "\u22EF" : expanded ? "\u25BE" : "\u25B8") : " "}
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
