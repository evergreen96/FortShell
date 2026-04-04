import { describe, expect, it } from "vitest";
import { BUILT_IN_PRESETS } from "../../src/main/core/policy/protection-rules";
import { compileProtectionRules } from "../../src/main/core/policy/protection-compiler";

describe("compileProtectionRules", () => {
  it("compiles preset, extension, and directory rules into concrete workspace entries", () => {
    const entries = compileProtectionRules({
      workspaceRoot: "/repo",
      rules: [
        { id: "preset-1", kind: "preset", source: "preset", presetId: "env-files" },
        { id: "ext-1", kind: "extension", source: "extension", extensions: [".pem"] },
        { id: "dir-1", kind: "directory", source: "directory", targetPath: "/repo/secrets" },
      ],
      presetCatalog: BUILT_IN_PRESETS,
      workspaceEntries: [
        { path: "/repo/.env", relativePath: ".env", name: ".env", ext: "", isDirectory: false },
        { path: "/repo/cert.pem", relativePath: "cert.pem", name: "cert.pem", ext: ".pem", isDirectory: false },
        { path: "/repo/secrets", relativePath: "secrets", name: "secrets", ext: "", isDirectory: true },
        { path: "/repo/secrets/db.txt", relativePath: "secrets/db.txt", name: "db.txt", ext: ".txt", isDirectory: false },
      ],
    });

    expect(entries.map((entry) => entry.relativePath)).toEqual([
      ".env",
      "cert.pem",
      "secrets",
      "secrets/db.txt",
    ]);
    expect(entries.find((entry) => entry.relativePath === ".env")?.sourceLabel).toBe("Env Files Preset");
  });

  it("deduplicates overlapping rules and returns relativePath-sorted output", () => {
    const entries = compileProtectionRules({
      workspaceRoot: "/repo",
      rules: [
        { id: "dir-1", kind: "directory", source: "directory", targetPath: "/repo/secrets" },
        { id: "path-1", kind: "path", source: "manual", targetPath: "/repo/secrets/db.txt" },
        { id: "ext-1", kind: "extension", source: "extension", extensions: [".pem"] },
        { id: "preset-1", kind: "preset", source: "preset", presetId: "env-files" },
      ],
      presetCatalog: BUILT_IN_PRESETS,
      workspaceEntries: [
        { path: "/repo/.env", relativePath: ".env", name: ".env", ext: "", isDirectory: false },
        { path: "/repo/cert.pem", relativePath: "cert.pem", name: "cert.pem", ext: ".pem", isDirectory: false },
        { path: "/repo/secrets", relativePath: "secrets", name: "secrets", ext: "", isDirectory: true },
        { path: "/repo/secrets/db.txt", relativePath: "secrets/db.txt", name: "db.txt", ext: ".txt", isDirectory: false },
      ],
    });

    expect(entries.map((entry) => entry.relativePath)).toEqual([
      ".env",
      "cert.pem",
      "secrets",
      "secrets/db.txt",
    ]);
    expect(new Set(entries.map((entry) => entry.path)).size).toBe(entries.length);
  });

  it("includes subtree entries for directory rules", () => {
    const entries = compileProtectionRules({
      workspaceRoot: "/repo",
      rules: [
        { id: "dir-1", kind: "directory", source: "directory", targetPath: "/repo/secrets" },
      ],
      presetCatalog: BUILT_IN_PRESETS,
      workspaceEntries: [
        { path: "/repo/secrets", relativePath: "secrets", name: "secrets", ext: "", isDirectory: true },
        { path: "/repo/secrets/db.txt", relativePath: "secrets/db.txt", name: "db.txt", ext: ".txt", isDirectory: false },
        { path: "/repo/secrets/nested/api.key", relativePath: "secrets/nested/api.key", name: "api.key", ext: ".key", isDirectory: false },
      ],
    });

    expect(entries.map((entry) => entry.relativePath)).toEqual([
      "secrets",
      "secrets/db.txt",
      "secrets/nested/api.key",
    ]);
  });

  it("includes workspace children for a root-level directory rule", () => {
    const entries = compileProtectionRules({
      workspaceRoot: "/repo",
      rules: [
        { id: "dir-root", kind: "directory", source: "directory", targetPath: "/repo" },
      ],
      presetCatalog: BUILT_IN_PRESETS,
      workspaceEntries: [
        { path: "/repo/.env", relativePath: ".env", name: ".env", ext: "", isDirectory: false },
        { path: "/repo/src", relativePath: "src", name: "src", ext: "", isDirectory: true },
        { path: "/repo/src/app.ts", relativePath: "src/app.ts", name: "app.ts", ext: ".ts", isDirectory: false },
      ],
    });

    expect(entries.map((entry) => entry.relativePath)).toEqual([
      ".env",
      "src",
      "src/app.ts",
    ]);
  });

  it("marks only the directly added manual directory row as directly removable", () => {
    const entries = compileProtectionRules({
      workspaceRoot: "/repo",
      rules: [
        { id: "dir-1", kind: "directory", source: "manual", targetPath: "/repo/secrets" },
      ],
      presetCatalog: BUILT_IN_PRESETS,
      workspaceEntries: [
        { path: "/repo/secrets", relativePath: "secrets", name: "secrets", ext: "", isDirectory: true },
        { path: "/repo/secrets/db.txt", relativePath: "secrets/db.txt", name: "db.txt", ext: ".txt", isDirectory: false },
      ],
    });

    expect(
      entries.find((entry) => entry.relativePath === "secrets")
    ).toMatchObject({
      canRemoveDirectly: true,
      sourceLabel: "Manual Folder",
    });
    expect(
      entries.find((entry) => entry.relativePath === "secrets/db.txt")
    ).toMatchObject({
      canRemoveDirectly: false,
    });
  });

  it("marks only the directory root row from the directory rule API as directly removable", () => {
    const entries = compileProtectionRules({
      workspaceRoot: "/repo",
      rules: [
        { id: "dir-1", kind: "directory", source: "directory", targetPath: "/repo/secrets" },
      ],
      presetCatalog: BUILT_IN_PRESETS,
      workspaceEntries: [
        { path: "/repo/secrets", relativePath: "secrets", name: "secrets", ext: "", isDirectory: true },
        { path: "/repo/secrets/db.txt", relativePath: "secrets/db.txt", name: "db.txt", ext: ".txt", isDirectory: false },
      ],
    });

    expect(
      entries.find((entry) => entry.relativePath === "secrets")
    ).toMatchObject({
      canRemoveDirectly: true,
      sourceLabel: "Directory Folder",
    });
    expect(
      entries.find((entry) => entry.relativePath === "secrets/db.txt")
    ).toMatchObject({
      canRemoveDirectly: false,
    });
  });

  it("throws when a preset rule references an unknown preset id", () => {
    expect(() =>
      compileProtectionRules({
        workspaceRoot: "/repo",
        rules: [
          { id: "preset-missing", kind: "preset", source: "preset", presetId: "env-files-does-not-exist" as never },
        ],
        presetCatalog: BUILT_IN_PRESETS,
        workspaceEntries: [
          { path: "/repo/.env", relativePath: ".env", name: ".env", ext: "", isDirectory: false },
        ],
      })
    ).toThrow("Unknown protection preset: env-files-does-not-exist");
  });
});
