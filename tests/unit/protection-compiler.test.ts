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
});
