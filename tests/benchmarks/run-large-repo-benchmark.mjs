import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { createRequire } from "node:module";
import {
  createBenchmarkReportLines,
  generateLargeWorkspace,
} from "./lib/large-repo-benchmark.mjs";

const require = createRequire(import.meta.url);
const {
  searchWorkspace,
  indexDirectory,
  expandDirectory,
} = require("../../dist-main/core/workspace/file-indexer.js");
const { WorkspaceIndexService } = require("../../dist-main/core/workspace/workspace-index-service.js");
const { PolicyEngine } = require("../../dist-main/core/policy/policy-engine.js");

async function measure(label, fn) {
  const startedAt = performance.now();
  const result = await fn();
  return {
    label,
    durationMs: Math.round((performance.now() - startedAt) * 100) / 100,
    result,
  };
}

async function runSearchBenchmarks(rootDir) {
  const workspaceIndexService = new WorkspaceIndexService();
  await workspaceIndexService.setRoot(rootDir);
  await workspaceIndexService.warm();
  const searches = [
    { label: "search:env", query: "env" },
    { label: "search:config", query: "config" },
    { label: "search:ext:.env", query: "", extensions: [".env"] },
  ];

  const results = [];
  for (const search of searches) {
    results.push(
      await measure(search.label, async () =>
        searchWorkspace(rootDir, {
          query: search.query,
          extensions: search.extensions,
          includeDirectories: true,
          limit: 50,
        })
      )
    );
    results.push(
      await measure(`${search.label}:indexed`, async () =>
        workspaceIndexService.search({
          query: search.query,
          extensions: search.extensions,
          includeDirectories: true,
          limit: 50,
        })
      )
    );
  }

  return results.map((entry) => ({
    label: entry.label,
    durationMs: entry.durationMs,
    count: Array.isArray(entry.result) ? entry.result.length : 0,
  }));
}

async function createPolicyEngine(rootDir, suffix) {
  const policyStoreDir = path.join(os.tmpdir(), `fortshell-bench-policy-${suffix}`);
  const engine = new PolicyEngine({ policyStoreDir });
  await engine.setProjectRoot(rootDir);
  return { engine, policyStoreDir };
}

async function runOpenAndFileTreeBenchmarks(rootDir, sizeLabel) {
  const indexService = new WorkspaceIndexService();
  await indexService.setRoot(rootDir);
  await indexService.warm();

  const dynamic = await createPolicyEngine(rootDir, `${sizeLabel}-open-dynamic`);
  await dynamic.engine.applyPreset("env-files");
  await dynamic.engine.cleanup();

  const openNoRules = await measure("open:workspace:no-rules", async () => {
    const engine = new PolicyEngine({
      policyStoreDir: path.join(os.tmpdir(), `fortshell-bench-policy-${sizeLabel}-open-no-rules`),
    });
    await engine.setProjectRoot(rootDir);
    await engine.cleanup();
    return true;
  });

  const openDynamic = await measure("open:workspace:env-files", async () => {
    const engine = new PolicyEngine({ policyStoreDir: dynamic.policyStoreDir });
    engine.setWorkspaceProtectionEntries(rootDir, indexService.getProtectionEntries());
    await engine.setProjectRoot(rootDir);
    await engine.cleanup();
    return true;
  });

  const packagesDir = path.join(rootDir, "packages");
  const rootFallback = await measure("filetree:root", async () => indexDirectory(rootDir));
  const rootIndexed = await measure("filetree:root:indexed", async () =>
    indexService.listDirectory(rootDir)
  );
  const expandFallback = await measure("filetree:expand:packages", async () =>
    expandDirectory(packagesDir, rootDir)
  );
  const expandIndexed = await measure("filetree:expand:packages:indexed", async () =>
    indexService.listDirectory(packagesDir)
  );

  return [
    openNoRules,
    openDynamic,
    rootFallback,
    rootIndexed,
    expandFallback,
    expandIndexed,
  ].map((entry) => ({
    label: entry.label,
    durationMs: entry.durationMs,
    count: Array.isArray(entry.result) ? entry.result.length : 1,
  }));
}

async function runCompileBenchmarks(rootDir, sizeLabel) {
  const workspaceIndexService = new WorkspaceIndexService();
  await workspaceIndexService.setRoot(rootDir);
  await workspaceIndexService.warm();

  const { engine: extensionEngine } = await createPolicyEngine(rootDir, `${sizeLabel}-extension`);
  extensionEngine.setWorkspaceProtectionEntries(
    rootDir,
    workspaceIndexService.getProtectionEntries()
  );
  const extensionApply = await measure("compile:apply:extension:.env", async () => {
    await extensionEngine.addExtensionRule([".env"]);
    return extensionEngine.listCompiledEntries();
  });

  const newExtensionFile = path.join(rootDir, ".env.bench");
  await fs.writeFile(newExtensionFile, "BENCH=1\n", "utf8");
  const extensionRecompute = await measure(
    "compile:recompute:create:.env.bench",
    async () => {
      const delta = workspaceIndexService.handleChange(".env.bench");
      extensionEngine.updateWorkspaceEntriesForDelta(
        rootDir,
        delta.changedEntries,
        delta.removedEntries
      );
      await extensionEngine.recomputeDynamicRulesForEntries(
        delta.changedEntries,
        delta.removedEntries
      );
      return extensionEngine.listCompiledEntries();
    }
  );
  await extensionEngine.cleanup();

  const { engine: presetEngine } = await createPolicyEngine(rootDir, `${sizeLabel}-preset`);
  presetEngine.setWorkspaceProtectionEntries(
    rootDir,
    workspaceIndexService.getProtectionEntries()
  );
  const presetApply = await measure("compile:apply:preset:env-files", async () => {
    await presetEngine.applyPreset("env-files");
    return presetEngine.listCompiledEntries();
  });

  const newPresetFile = path.join(rootDir, ".env.production");
  await fs.writeFile(newPresetFile, "BENCH=1\n", "utf8");
  const presetRecompute = await measure(
    "compile:recompute:create:.env.production",
    async () => {
      const delta = workspaceIndexService.handleChange(".env.production");
      presetEngine.updateWorkspaceEntriesForDelta(
        rootDir,
        delta.changedEntries,
        delta.removedEntries
      );
      await presetEngine.recomputeDynamicRulesForEntries(
        delta.changedEntries,
        delta.removedEntries
      );
      return presetEngine.listCompiledEntries();
    }
  );
  await presetEngine.cleanup();

  return [extensionApply, extensionRecompute, presetApply, presetRecompute].map((entry) => ({
    label: entry.label,
    durationMs: entry.durationMs,
    count: Array.isArray(entry.result) ? entry.result.length : 0,
  }));
}

async function main() {
  const sizeLabel = process.argv[2] ?? "5k";
  const rootDir = path.join(os.tmpdir(), `fortshell-large-bench-${sizeLabel}`);

  const fixtureRun = await measure("fixture:generate", async () =>
    generateLargeWorkspace(rootDir, sizeLabel)
  );
  const openAndTreeResults = await runOpenAndFileTreeBenchmarks(rootDir, sizeLabel);
  const searchResults = await runSearchBenchmarks(rootDir);
  const compileResults = await runCompileBenchmarks(rootDir, sizeLabel);

  const lines = createBenchmarkReportLines({
    sizeLabel,
    fileCount: fixtureRun.result.fileCount,
    fixtureGenerationMs: fixtureRun.durationMs,
    searchResults: [...openAndTreeResults, ...searchResults],
    compileResults,
  });

  for (const line of lines) {
    console.log(line);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
