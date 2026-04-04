import fs from "fs/promises";
import os from "os";
import path from "path";
import { afterEach, describe, expect, test } from "vitest";
import {
  createBenchmarkReportLines,
  generateLargeWorkspace,
} from "../../scripts/benchmarks/lib/large-repo-benchmark.mjs";

const tempDirs: string[] = [];

afterEach(async () => {
  await Promise.all(
    tempDirs.splice(0).map((dirPath) =>
      fs.rm(dirPath, { recursive: true, force: true })
    )
  );
});

describe("generateLargeWorkspace", () => {
  test("creates a deterministic workspace with benchmark-specific files", async () => {
    const rootDir = await fs.mkdtemp(
      path.join(os.tmpdir(), "fortshell-large-bench-test-")
    );
    tempDirs.push(rootDir);

    const summary = await generateLargeWorkspace(rootDir, "5k");

    expect(summary.sizeLabel).toBe("5k");
    expect(summary.fileCount).toBe(5000);
    await expect(fs.stat(path.join(rootDir, ".env"))).resolves.toMatchObject({
      isFile: expect.any(Function),
    });
    await expect(
      fs.stat(path.join(rootDir, "secrets", "api.key"))
    ).resolves.toMatchObject({
      isFile: expect.any(Function),
    });
    await expect(
      fs.stat(path.join(rootDir, "config", "private", "app.json"))
    ).resolves.toMatchObject({
      isFile: expect.any(Function),
    });
    await expect(
      fs.stat(path.join(rootDir, "packages", "pkg-000", "src", "file-00000.ts"))
    ).resolves.toMatchObject({
      isFile: expect.any(Function),
    });
  });
});

describe("createBenchmarkReportLines", () => {
  test("formats stable summary lines for fixture, search, and compile timings", () => {
    const lines = createBenchmarkReportLines({
      sizeLabel: "20k",
      fileCount: 20000,
      fixtureGenerationMs: 842.31,
      searchResults: [
        { label: "search:env", durationMs: 74.12, count: 12 },
        { label: "search:config", durationMs: 81.4, count: 50 },
      ],
      compileResults: [
        { label: "compile:extension:.env", durationMs: 96.5, count: 1 },
      ],
    });

    expect(lines).toEqual([
      "fixture=20k files=20000 generationMs=842.31",
      "search:env durationMs=74.12 count=12",
      "search:config durationMs=81.4 count=50",
      "compile:extension:.env durationMs=96.5 count=1",
    ]);
  });
});
