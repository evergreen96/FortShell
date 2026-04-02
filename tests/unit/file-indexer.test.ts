import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { indexDirectory, searchWorkspace } from "../../src/main/core/workspace/file-indexer";
import fs from "fs";
import path from "path";
import os from "os";

describe("indexDirectory", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-test-"));

    // Create test structure
    fs.writeFileSync(path.join(tmpDir, "README.md"), "# Hello");
    fs.writeFileSync(path.join(tmpDir, "app.ts"), "console.log('hi')");
    fs.writeFileSync(path.join(tmpDir, ".env.production"), "SECRET=1");
    fs.mkdirSync(path.join(tmpDir, "src"));
    fs.writeFileSync(path.join(tmpDir, "src", "index.ts"), "export {}");
    fs.mkdirSync(path.join(tmpDir, "src", "config"));
    fs.writeFileSync(path.join(tmpDir, "src", "config", "secrets.json"), "{}");
    fs.mkdirSync(path.join(tmpDir, "node_modules"));
    fs.writeFileSync(
      path.join(tmpDir, "node_modules", "pkg.json"),
      "{}"
    );
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("should list files and directories", () => {
    const entries = indexDirectory(tmpDir);
    const names = entries.map((e) => e.name);

    expect(names).toContain("src");
    expect(names).toContain("README.md");
    expect(names).toContain("app.ts");
  });

  it("should exclude node_modules", () => {
    const entries = indexDirectory(tmpDir);
    const names = entries.map((e) => e.name);
    expect(names).not.toContain("node_modules");
  });

  it("should sort directories first", () => {
    const entries = indexDirectory(tmpDir);
    const firstDir = entries.findIndex((e) => e.isDirectory);
    const firstFile = entries.findIndex((e) => !e.isDirectory);
    expect(firstDir).toBeLessThan(firstFile);
  });

  it("should recurse into subdirectories", () => {
    const entries = indexDirectory(tmpDir);
    const srcEntry = entries.find((e) => e.name === "src");
    expect(srcEntry).toBeDefined();
    expect(srcEntry!.children).toBeDefined();
    expect(srcEntry!.children!.some((c) => c.name === "index.ts")).toBe(true);
  });

  it("should respect maxDepth", () => {
    const entries = indexDirectory(tmpDir, 0, 0);
    const srcEntry = entries.find((e) => e.name === "src");
    expect(srcEntry?.children).toBeUndefined();
  });

  it("should search nested files and directories", () => {
    const results = searchWorkspace(tmpDir, {
      query: "config",
      includeDirectories: true,
      limit: 20,
    });

    expect(results.some((entry) => entry.relativePath === "src/config")).toBe(true);
    expect(results.some((entry) => entry.relativePath === "src/config/secrets.json")).toBe(true);
  });

  it("should match dotfile-style extension patterns", () => {
    const results = searchWorkspace(tmpDir, {
      extensions: [".env", ".json"],
      includeDirectories: false,
      limit: 20,
    });
    const relativePaths = results.map((entry) => entry.relativePath);

    expect(relativePaths).toContain(".env.production");
    expect(relativePaths).toContain("src/config/secrets.json");
  });
});
