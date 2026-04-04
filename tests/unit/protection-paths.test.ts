import { describe, expect, it } from "vitest";
import {
  buildExplorerProtectedPathSet,
  getExplorerContextMenuAction,
  isPathProtected,
} from "../../src/renderer/lib/protection-paths";

describe("buildExplorerProtectedPathSet", () => {
  it("includes compiled preset and extension paths for Explorer badges", () => {
    const protectedPaths = buildExplorerProtectedPathSet([
      {
        name: ".env",
        path: "/repo/.env",
        relativePath: ".env",
        isDirectory: false,
        type: "file",
        status: "shielded",
        sourceLabel: "Env Files Preset",
        sourceRuleId: "preset-1",
        sourceKind: "preset",
        canRemoveDirectly: false,
      },
      {
        name: "cert.pem",
        path: "/repo/cert.pem",
        relativePath: "cert.pem",
        isDirectory: false,
        type: "file",
        status: "shielded",
        sourceLabel: "Extension Rule",
        sourceRuleId: "ext-1",
        sourceKind: "extension",
        canRemoveDirectly: false,
      },
      {
        name: "secrets",
        path: "/repo/secrets",
        relativePath: "secrets",
        isDirectory: true,
        type: "folder",
        status: "shielded",
        sourceLabel: "Directory Rule",
        sourceRuleId: "dir-1",
        sourceKind: "directory",
        canRemoveDirectly: true,
      },
    ]);

    expect(protectedPaths.has("/repo/.env")).toBe(true);
    expect(protectedPaths.has("/repo/cert.pem")).toBe(true);
    expect(isPathProtected("/repo/secrets/db.txt", protectedPaths)).toBe(true);
  });
});

describe("getExplorerContextMenuAction", () => {
  it("uses remove only for exact direct roots and view protection for generated or covered paths", () => {
    const compiledEntries = [
      {
        name: ".env",
        path: "/repo/.env",
        relativePath: ".env",
        isDirectory: false,
        type: "file",
        status: "shielded",
        sourceLabel: "Env Files Preset",
        sourceRuleId: "preset-1",
        sourceKind: "preset",
        canRemoveDirectly: false,
      },
      {
        name: "secrets",
        path: "/repo/secrets",
        relativePath: "secrets",
        isDirectory: true,
        type: "folder",
        status: "shielded",
        sourceLabel: "Directory Rule",
        sourceRuleId: "dir-1",
        sourceKind: "directory",
        canRemoveDirectly: true,
      },
      {
        name: "nested",
        path: "/repo/secrets/nested",
        relativePath: "secrets/nested",
        isDirectory: true,
        type: "folder",
        status: "shielded",
        sourceLabel: "Nested Directory Rule",
        sourceRuleId: "nested-1",
        sourceKind: "directory",
        canRemoveDirectly: true,
      },
    ];

    expect(getExplorerContextMenuAction("/repo/secrets", compiledEntries)).toEqual({
      kind: "remove",
      sourceRuleId: "dir-1",
    });
    expect(getExplorerContextMenuAction("/repo/secrets/db.txt", compiledEntries)).toEqual({
      kind: "view-protection",
      sourceRuleId: "dir-1",
    });
    expect(getExplorerContextMenuAction("/repo/secrets/nested/api.key", compiledEntries)).toEqual({
      kind: "view-protection",
      sourceRuleId: "nested-1",
    });
    expect(getExplorerContextMenuAction("/repo/.env", compiledEntries)).toEqual({
      kind: "view-protection",
      sourceRuleId: "preset-1",
    });
    expect(getExplorerContextMenuAction("/repo/notes.txt", compiledEntries)).toEqual({
      kind: "protect",
    });
  });
});
