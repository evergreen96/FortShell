import { useCallback, useEffect, useMemo, useRef } from "react";

import type { TreeNode } from "../lib/workspaceTree";

type FileTreeProps = {
  tree: TreeNode[];
  selectedPath: string | null;
  expandedDirectories: Set<string>;
  onSelectPath: (path: string, isDir: boolean) => void;
};

/** Flatten the tree into a visible-row list (only expanded children are included). */
function flattenVisible(
  nodes: TreeNode[],
  expandedDirectories: Set<string>,
  depth: number = 0,
): FlatRow[] {
  const rows: FlatRow[] = [];
  for (const node of nodes) {
    const expanded = node.isDir ? expandedDirectories.has(node.path) : false;
    rows.push({ node, depth, expanded });
    if (node.isDir && expanded) {
      rows.push(...flattenVisible(node.children, expandedDirectories, depth + 1));
    }
  }
  return rows;
}

type FlatRow = {
  node: TreeNode;
  depth: number;
  expanded: boolean;
};

const ROW_HEIGHT = 32;
const OVERSCAN = 8;

export function FileTree({
  tree,
  selectedPath,
  expandedDirectories,
  onSelectPath,
}: FileTreeProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const focusIndexRef = useRef(0);

  const rows = useMemo(
    () => flattenVisible(tree, expandedDirectories),
    [tree, expandedDirectories],
  );

  // Keep focusIndex in sync with selectedPath
  useEffect(() => {
    const idx = rows.findIndex((r) => r.node.path === selectedPath);
    if (idx >= 0) focusIndexRef.current = idx;
  }, [selectedPath, rows]);

  const scrollToIndex = useCallback(
    (index: number) => {
      const container = containerRef.current;
      if (!container) return;
      const top = index * ROW_HEIGHT;
      const bottom = top + ROW_HEIGHT;
      if (top < container.scrollTop) {
        container.scrollTop = top;
      } else if (bottom > container.scrollTop + container.clientHeight) {
        container.scrollTop = bottom - container.clientHeight;
      }
    },
    [],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (rows.length === 0) return;

      let idx = focusIndexRef.current;

      switch (event.key) {
        case "ArrowDown":
          event.preventDefault();
          idx = Math.min(idx + 1, rows.length - 1);
          break;
        case "ArrowUp":
          event.preventDefault();
          idx = Math.max(idx - 1, 0);
          break;
        case "ArrowRight":
          event.preventDefault();
          if (rows[idx]?.node.isDir && !expandedDirectories.has(rows[idx].node.path)) {
            onSelectPath(rows[idx].node.path, true);
            return;
          }
          // If already expanded, move into first child
          if (idx + 1 < rows.length && rows[idx + 1].depth > rows[idx].depth) {
            idx = idx + 1;
          }
          break;
        case "ArrowLeft":
          event.preventDefault();
          if (rows[idx]?.node.isDir && expandedDirectories.has(rows[idx].node.path)) {
            // Collapse current dir
            onSelectPath(rows[idx].node.path, true);
            return;
          }
          // Move to parent
          {
            const currentDepth = rows[idx]?.depth ?? 0;
            for (let i = idx - 1; i >= 0; i--) {
              if (rows[i].depth < currentDepth && rows[i].node.isDir) {
                idx = i;
                break;
              }
            }
          }
          break;
        case "Enter":
        case " ":
          event.preventDefault();
          if (rows[idx]) {
            onSelectPath(rows[idx].node.path, rows[idx].node.isDir);
          }
          return;
        default:
          return;
      }

      focusIndexRef.current = idx;
      scrollToIndex(idx);
      if (rows[idx]) {
        onSelectPath(rows[idx].node.path, rows[idx].node.isDir);
      }
    },
    [rows, expandedDirectories, onSelectPath, scrollToIndex],
  );

  // Virtual scrolling state
  const [scrollTop, setScrollTop] = React.useState(0);
  const containerHeight = containerRef.current?.clientHeight ?? 400;
  const totalHeight = rows.length * ROW_HEIGHT;

  const startIndex = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const endIndex = Math.min(
    rows.length,
    Math.ceil((scrollTop + containerHeight) / ROW_HEIGHT) + OVERSCAN,
  );
  const visibleRows = rows.slice(startIndex, endIndex);
  const offsetY = startIndex * ROW_HEIGHT;

  const handleScroll = useCallback(() => {
    if (containerRef.current) {
      setScrollTop(containerRef.current.scrollTop);
    }
  }, []);

  return (
    <div
      ref={containerRef}
      className="file-tree-viewport"
      onScroll={handleScroll}
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role="tree"
      aria-label="File tree"
    >
      <div className="file-tree-spacer" style={{ height: totalHeight }}>
        <div className="file-tree-rows" style={{ transform: `translateY(${offsetY}px)` }}>
          {visibleRows.map((row) => (
            <FileTreeRow
              key={row.node.path}
              row={row}
              selected={row.node.path === selectedPath}
              focused={rows.indexOf(row) === focusIndexRef.current}
              onSelect={onSelectPath}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// Need React import for useState
import React from "react";

type FileTreeRowProps = {
  row: FlatRow;
  selected: boolean;
  focused: boolean;
  onSelect: (path: string, isDir: boolean) => void;
};

const FileTreeRow = React.memo(function FileTreeRow({
  row,
  selected,
  onSelect,
}: FileTreeRowProps) {
  const { node, depth, expanded } = row;

  return (
    <button
      type="button"
      className={`tree-item ${selected ? "tree-item-active" : ""}`}
      style={{
        height: ROW_HEIGHT,
        paddingLeft: `${12 + depth * 16}px`,
      }}
      onClick={() => onSelect(node.path, node.isDir)}
      role="treeitem"
      aria-expanded={node.isDir ? expanded : undefined}
      aria-selected={selected}
    >
      <span className={`tree-icon ${node.isDir ? "tree-icon-dir" : "tree-icon-file"}`}>
        {node.isDir ? (expanded ? fileIcon("dir-open") : fileIcon("dir")) : fileIcon(fileExt(node.name))}
      </span>
      <span className="tree-label">{node.displayName}</span>
      <span className={`tree-state ${node.suggestedRule ? "tree-state-managed" : "tree-state-unmanaged"}`}>
        {node.suggestedRule ? "managed" : node.isDir ? "group" : "outside"}
      </span>
    </button>
  );
});

function fileExt(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function fileIcon(ext: string): string {
  switch (ext) {
    case "dir":
      return "\u{1F4C1}"; // 📁
    case "dir-open":
      return "\u{1F4C2}"; // 📂
    case "js":
    case "jsx":
    case "mjs":
      return "\u{1F7E8}"; // 🟨
    case "ts":
    case "tsx":
      return "\u{1F7E6}"; // 🟦
    case "py":
      return "\u{1F40D}"; // 🐍
    case "rs":
      return "\u{2699}";  // ⚙
    case "json":
      return "\u{1F4CB}"; // 📋
    case "md":
      return "\u{1F4DD}"; // 📝
    case "css":
    case "scss":
    case "less":
      return "\u{1F3A8}"; // 🎨
    case "html":
    case "htm":
      return "\u{1F310}"; // 🌐
    case "yaml":
    case "yml":
    case "toml":
      return "\u{2699}";  // ⚙
    case "sh":
    case "bash":
    case "ps1":
      return "\u{1F4BB}"; // 💻
    case "go":
      return "\u{1F439}"; // 🐹
    default:
      return "\u{1F4C4}"; // 📄
  }
}
