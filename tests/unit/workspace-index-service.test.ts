import fs from "fs/promises";
import os from "os";
import path from "path";
import { afterEach, describe, expect, test } from "vitest";
import { WorkspaceIndexService } from "../../src/main/core/workspace/workspace-index-service";

const tempDirs: string[] = [];

afterEach(async () => {
  await Promise.all(
    tempDirs.splice(0).map((dirPath) =>
      fs.rm(dirPath, { recursive: true, force: true })
    )
  );
});

describe("WorkspaceIndexService", () => {
  test("warms an in-memory inventory and serves workspace search results", async () => {
    const rootDir = await fs.mkdtemp(path.join(os.tmpdir(), "fortshell-index-"));
    tempDirs.push(rootDir);
    await fs.mkdir(path.join(rootDir, "config"), { recursive: true });
    await fs.writeFile(path.join(rootDir, ".env"), "A=1\n", "utf8");
    await fs.writeFile(path.join(rootDir, "config", "app.json"), "{}\n", "utf8");

    const service = new WorkspaceIndexService();
    await service.setRoot(rootDir);
    await service.warm();

    const envResults = service.search({
      query: "env",
      includeDirectories: true,
      limit: 10,
    });
    const configResults = service.search({
      query: "config",
      includeDirectories: true,
      limit: 10,
    });

    expect(envResults.some((entry) => entry.relativePath === ".env")).toBe(true);
    expect(configResults.some((entry) => entry.relativePath === "config/app.json")).toBe(true);
  });

  test("returns empty search results before warm and rewarms after stale invalidation", async () => {
    const rootDir = await fs.mkdtemp(path.join(os.tmpdir(), "fortshell-index-"));
    tempDirs.push(rootDir);
    await fs.writeFile(path.join(rootDir, "alpha.txt"), "ok\n", "utf8");

    const service = new WorkspaceIndexService();
    await service.setRoot(rootDir);

    expect(
      service.search({ query: "alpha", includeDirectories: true, limit: 10 })
    ).toEqual([]);
    expect(service.getState()).toBe("cold");

    await service.warm();
    expect(service.getState()).toBe("ready");
    expect(
      service.search({ query: "alpha", includeDirectories: true, limit: 10 })
    ).toHaveLength(1);

    await fs.writeFile(path.join(rootDir, "beta.txt"), "ok\n", "utf8");
    service.markStale();

    expect(service.getState()).toBe("stale");
    expect(
      service.search({ query: "beta", includeDirectories: true, limit: 10 })
    ).toEqual([]);

    await service.warm();
    expect(service.getState()).toBe("ready");
    expect(
      service.search({ query: "beta", includeDirectories: true, limit: 10 })
    ).toHaveLength(1);
  });
});
