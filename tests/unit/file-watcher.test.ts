import path from "path";
import { describe, expect, it } from "vitest";
import { shouldIgnoreWorkspaceChange } from "../../src/main/core/workspace/file-watcher";

describe("shouldIgnoreWorkspaceChange", () => {
  it("allows hidden files while still ignoring ignored directories", () => {
    expect(shouldIgnoreWorkspaceChange(".env")).toBe(false);
    expect(shouldIgnoreWorkspaceChange(".env.local")).toBe(false);
    expect(shouldIgnoreWorkspaceChange(path.posix.join(".aws", "config"))).toBe(false);
    expect(shouldIgnoreWorkspaceChange(path.win32.join(".aws", "config"))).toBe(false);
    expect(shouldIgnoreWorkspaceChange(path.posix.join(".git", "config"))).toBe(true);
    expect(shouldIgnoreWorkspaceChange(path.win32.join(".git", "config"))).toBe(true);
    expect(shouldIgnoreWorkspaceChange(path.posix.join("node_modules", "pkg", "index.js"))).toBe(true);
    expect(shouldIgnoreWorkspaceChange(path.win32.join("node_modules", "pkg", "index.js"))).toBe(true);
  });
});
