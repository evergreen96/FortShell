import type { WorkspaceEntry } from "./types";

export type TreeNode = {
  path: string;
  name: string;
  displayName: string;
  isDir: boolean;
  suggestedRule: string | null;
  children: TreeNode[];
};

export function buildTree(entries: WorkspaceEntry[]): TreeNode[] {
  const root: TreeNode = {
    path: "",
    name: "",
    displayName: "",
    isDir: true,
    suggestedRule: null,
    children: [],
  };

  // Map-based lookup for O(1) child access instead of O(n) linear scan
  const childMap = new Map<string, Map<string, TreeNode>>();
  childMap.set("", new Map());

  for (const entry of entries) {
    const parts = entry.path.split("/").filter((part) => part.length > 0);
    let current = root;
    let currentPath = "";
    for (let index = 0; index < parts.length; index += 1) {
      const part = parts[index];
      const nextPath = currentPath ? `${currentPath}/${part}` : part;
      const isLeaf = index === parts.length - 1;

      let siblings = childMap.get(currentPath);
      if (!siblings) {
        siblings = new Map();
        childMap.set(currentPath, siblings);
      }

      let child = siblings.get(nextPath);
      if (!child) {
        child = {
          path: nextPath,
          name: part,
          displayName: isLeaf ? entry.display_name : `${part}/`,
          isDir: isLeaf ? entry.is_dir : true,
          suggestedRule: isLeaf ? entry.suggested_deny_rule : null,
          children: [],
        };
        siblings.set(nextPath, child);
        current.children.push(child);
        childMap.set(nextPath, new Map());
      }
      if (isLeaf) {
        child.displayName = entry.display_name;
        child.isDir = entry.is_dir;
        child.suggestedRule = entry.suggested_deny_rule;
      }
      current = child;
      currentPath = nextPath;
    }
  }

  sortChildren(root.children);
  return root.children;
}

export function directoryPaths(entries: WorkspaceEntry[]): Set<string> {
  return new Set(entries.filter((entry) => entry.is_dir).map((entry) => entry.path));
}

export function ancestorDirectories(path: string): string[] {
  const parts = path.split("/").filter((part) => part.length > 0);
  const values: string[] = [];
  for (let index = 1; index < parts.length; index += 1) {
    values.push(parts.slice(0, index).join("/"));
  }
  return values;
}

function sortChildren(nodes: TreeNode[]) {
  nodes.sort((left, right) => {
    if (left.isDir !== right.isDir) {
      return left.isDir ? -1 : 1;
    }
    return left.name.localeCompare(right.name);
  });
  for (const node of nodes) {
    sortChildren(node.children);
  }
}
