import { describe, expect, it } from "vitest";
import { shouldIgnoreWorkspaceChange } from "../../src/main/core/workspace/file-watcher";

describe("shouldIgnoreWorkspaceChange", () => {
  it("allows hidden files while still ignoring ignored directories", () => {
    expect(shouldIgnoreWorkspaceChange(".env")).toBe(false);
    expect(shouldIgnoreWorkspaceChange(".env.local")).toBe(false);
    expect(shouldIgnoreWorkspaceChange(".aws/config")).toBe(false);
    expect(shouldIgnoreWorkspaceChange(".git/config")).toBe(true);
    expect(shouldIgnoreWorkspaceChange("node_modules/pkg/index.js")).toBe(true);
  });
});
