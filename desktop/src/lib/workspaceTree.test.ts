import { describe, it, expect } from "vitest";
import { buildTree, directoryPaths, ancestorDirectories } from "./workspaceTree";
import type { WorkspaceEntry } from "./types";

function entry(path: string, isDir: boolean): WorkspaceEntry {
  const name = path.split("/").pop()!;
  return {
    path,
    name,
    is_dir: isDir,
    display_name: isDir ? `${name}/` : name,
    display_path: path,
    suggested_deny_rule: isDir ? `${path}/**` : path,
  };
}

describe("buildTree", () => {
  it("builds tree from flat entries", () => {
    const entries = [
      entry("src", true),
      entry("src/main.js", false),
      entry("src/utils.py", false),
      entry("docs", true),
      entry("docs/readme.md", false),
    ];
    const tree = buildTree(entries);
    expect(tree.length).toBe(2);

    // Directories come first, sorted alphabetically
    expect(tree[0].name).toBe("docs");
    expect(tree[0].isDir).toBe(true);
    expect(tree[0].children.length).toBe(1);
    expect(tree[0].children[0].name).toBe("readme.md");

    expect(tree[1].name).toBe("src");
    expect(tree[1].isDir).toBe(true);
    expect(tree[1].children.length).toBe(2);
    // Files sorted: main.js, utils.py
    expect(tree[1].children[0].name).toBe("main.js");
    expect(tree[1].children[1].name).toBe("utils.py");
  });

  it("returns empty array for empty entries", () => {
    expect(buildTree([])).toEqual([]);
  });

  it("handles root-level files without directories", () => {
    const entries = [entry("package.json", false), entry("README.md", false)];
    const tree = buildTree(entries);
    expect(tree.length).toBe(2);
    expect(tree.every((n) => !n.isDir)).toBe(true);
  });
});

describe("directoryPaths", () => {
  it("returns only directory paths", () => {
    const entries = [
      entry("src", true),
      entry("src/main.js", false),
      entry("docs", true),
    ];
    const dirs = directoryPaths(entries);
    expect(dirs).toEqual(new Set(["src", "docs"]));
  });
});

describe("ancestorDirectories", () => {
  it("returns ancestor paths for nested file", () => {
    expect(ancestorDirectories("a/b/c/d.ts")).toEqual(["a", "a/b", "a/b/c"]);
  });

  it("returns empty for root file", () => {
    expect(ancestorDirectories("file.txt")).toEqual([]);
  });
});
