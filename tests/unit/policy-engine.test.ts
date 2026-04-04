import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { PolicyEngine } from "../../src/main/core/policy/policy-engine";
import {
  getLegacyPolicyPath,
  getWorkspacePolicyPath,
} from "../../src/main/core/config/policy-store";
import type { PolicyEnforcer } from "../../src/main/platform/types";
import fs from "fs";
import path from "path";
import os from "os";

describe("PolicyEngine", () => {
  let engine: PolicyEngine;
  let tmpDir: string;
  let policyStoreDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-test-"));
    policyStoreDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-policy-store-"));
    engine = new PolicyEngine({ policyStoreDir });
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    fs.rmSync(policyStoreDir, { recursive: true, force: true });
  });

  it("should protect and unprotect files", async () => {
    const filePath = path.join(tmpDir, "secret.txt");
    fs.writeFileSync(filePath, "secret");

    expect(engine.isProtected(filePath)).toBe(false);

    await engine.protect(filePath);
    expect(engine.isProtected(filePath)).toBe(true);
    expect(engine.list()).toContain(fs.realpathSync(path.resolve(filePath)));

    await engine.unprotect(filePath);
    expect(engine.isProtected(filePath)).toBe(false);
  });

  it("should protect child paths when parent is protected", async () => {
    const dirPath = path.join(tmpDir, "secrets");
    fs.mkdirSync(dirPath);
    const childPath = path.join(dirPath, "key.pem");
    fs.writeFileSync(childPath, "key");

    await engine.protect(dirPath);
    expect(engine.isProtected(childPath)).toBe(true);
  });

  it("should persist and load policy", async () => {
    await engine.setProjectRoot(tmpDir);

    const filePath = path.join(tmpDir, "secret.txt");
    fs.writeFileSync(filePath, "secret");
    await engine.protect(filePath);
    expect(fs.existsSync(getWorkspacePolicyPath(tmpDir, policyStoreDir))).toBe(true);
    expect(fs.existsSync(getLegacyPolicyPath(tmpDir))).toBe(false);

    // New engine instance should load persisted policy
    const engine2 = new PolicyEngine({ policyStoreDir });
    await engine2.setProjectRoot(tmpDir);
    expect(engine2.isProtected(filePath)).toBe(true);
  });

  it("loads rule-based workspace policy files", async () => {
    const filePath = path.join(tmpDir, ".env");
    fs.writeFileSync(filePath, "secret");

    const policyPath = getWorkspacePolicyPath(tmpDir, policyStoreDir);
    fs.mkdirSync(path.dirname(policyPath), { recursive: true });
    fs.writeFileSync(
      policyPath,
      JSON.stringify({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [
          {
            id: "rule-1",
            kind: "path",
            source: "manual",
            targetPath: ".env",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "utf-8"
    );

    await engine.setProjectRoot(tmpDir);

    expect(engine.isProtected(filePath)).toBe(true);
  });

  it("drops persisted direct rules that resolve outside the current workspace", async () => {
    const insidePath = path.join(tmpDir, "inside.txt");
    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-outside-load-"));
    const outsidePath = path.join(outsideDir, "outside.txt");
    fs.writeFileSync(insidePath, "inside");
    fs.writeFileSync(outsidePath, "outside");

    const policyPath = getWorkspacePolicyPath(tmpDir, policyStoreDir);
    fs.mkdirSync(path.dirname(policyPath), { recursive: true });
    fs.writeFileSync(
      policyPath,
      JSON.stringify({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [
          {
            id: "inside-rule",
            kind: "path",
            source: "manual",
            targetPath: "inside.txt",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
          {
            id: "outside-rule",
            kind: "path",
            source: "manual",
            targetPath: outsidePath,
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "utf-8"
    );

    await engine.setProjectRoot(tmpDir);

    expect(engine.isProtected(insidePath)).toBe(true);
    expect(engine.isProtected(outsidePath)).toBe(false);
    expect(engine.listRules()).toHaveLength(1);
    expect(engine.listRules()[0]).toMatchObject({
      id: "inside-rule",
      kind: "path",
      targetPath: "inside.txt",
    });
  });

  it("persists manual protections as version 3 path rules", async () => {
    await engine.setProjectRoot(tmpDir);

    const filePath = path.join(tmpDir, "nested", "secret.txt");
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, "secret");

    await engine.protect(filePath);

    const storedPolicy = JSON.parse(
      fs.readFileSync(getWorkspacePolicyPath(tmpDir, policyStoreDir), "utf-8")
    );

    expect(storedPolicy).toMatchObject({
      version: 3,
      workspaceRoot: fs.realpathSync(tmpDir),
      rules: [
        {
          kind: "path",
          source: "manual",
          targetPath: "nested/secret.txt",
          createdAt: expect.any(String),
          updatedAt: expect.any(String),
        },
      ],
      updatedAt: expect.any(String),
    });
    expect(storedPolicy.protected).toBeUndefined();

    const rules = (
      engine as unknown as {
        listRules?: () => Array<Record<string, unknown>>;
      }
    ).listRules?.();

    expect(rules).toHaveLength(1);
    expect(rules?.[0]).toMatchObject({
      kind: "path",
      source: "manual",
      targetPath: "nested/secret.txt",
    });
  });

  it("removes a protection rule by id", async () => {
    await engine.setProjectRoot(tmpDir);

    const filePath = path.join(tmpDir, ".env");
    fs.writeFileSync(filePath, "secret");

    const applied = await engine.applyPreset("env-files");
    expect(applied.changed).toBe(true);

    const presetRuleId = engine.listRules()[0]?.id;
    expect(typeof presetRuleId).toBe("string");

    const removed = await engine.removeRule(presetRuleId!);

    expect(removed).toBe(true);
    expect(engine.listRules()).toHaveLength(0);
    expect(engine.isProtected(filePath)).toBe(false);
  });

  it("re-applies compiled non-manual protections when a workspace is reloaded", async () => {
    const filePath = path.join(tmpDir, ".env");
    fs.writeFileSync(filePath, "secret");

    const appliedPaths: string[] = [];
    const fakeEnforcer: PolicyEnforcer = {
      applyProtection: async (targetPath) => {
        appliedPaths.push(targetPath);
      },
      removeProtection: async () => {},
      isAvailable: () => true,
      cleanup: async () => {},
    };
    engine.setEnforcer(fakeEnforcer);

    const policyPath = getWorkspacePolicyPath(tmpDir, policyStoreDir);
    fs.mkdirSync(path.dirname(policyPath), { recursive: true });
    fs.writeFileSync(
      policyPath,
      JSON.stringify({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [
          {
            id: "rule-preset-env",
            kind: "preset",
            source: "preset",
            presetId: "env-files",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "utf-8"
    );

    await engine.setProjectRoot(tmpDir);

    expect(appliedPaths).toContain(fs.realpathSync(filePath));
  });

  it("does not commit a workspace switch when replaying the next snapshot fails", async () => {
    const previousFilePath = path.join(tmpDir, "previous.txt");
    fs.writeFileSync(previousFilePath, "previous");

    const nextDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-test-next-"));
    const nextEnvPath = path.join(nextDir, ".env");
    const nextSecretPath = path.join(nextDir, "secret.txt");
    fs.writeFileSync(nextEnvPath, "env");
    fs.writeFileSync(nextSecretPath, "secret");

    const appliedPaths = new Set<string>();
    let nextReplayAttempts = 0;
    const fakeEnforcer: PolicyEnforcer = {
      applyProtection: async (targetPath) => {
        if (targetPath.startsWith(fs.realpathSync(nextDir))) {
          nextReplayAttempts += 1;
          if (nextReplayAttempts === 2) {
            throw new Error("replay failed");
          }
        }
        appliedPaths.add(targetPath);
      },
      removeProtection: async (targetPath) => {
        appliedPaths.delete(targetPath);
      },
      isAvailable: () => true,
      cleanup: async () => {
        appliedPaths.clear();
      },
    };
    engine.setEnforcer(fakeEnforcer);

    try {
      await engine.setProjectRoot(tmpDir);
      await engine.protect(previousFilePath);

      const nextPolicyPath = getWorkspacePolicyPath(nextDir, policyStoreDir);
      fs.mkdirSync(path.dirname(nextPolicyPath), { recursive: true });
      fs.writeFileSync(
        nextPolicyPath,
        JSON.stringify({
          version: 3,
          workspaceRoot: fs.realpathSync(nextDir),
          rules: [
            {
              id: "rule-preset-env",
              kind: "preset",
              source: "preset",
              presetId: "env-files",
              createdAt: "2026-04-03T00:00:00.000Z",
              updatedAt: "2026-04-03T00:00:00.000Z",
            },
            {
              id: "rule-secret",
              kind: "path",
              source: "manual",
              targetPath: "secret.txt",
              createdAt: "2026-04-03T00:00:00.000Z",
              updatedAt: "2026-04-03T00:00:00.000Z",
            },
          ],
          updatedAt: "2026-04-03T00:00:00.000Z",
        }),
        "utf-8"
      );

      await expect(engine.setProjectRoot(nextDir)).rejects.toThrow("replay failed");
      expect(engine.isProtected(previousFilePath)).toBe(true);
      expect(engine.isProtected(nextEnvPath)).toBe(false);
      expect(engine.isProtected(nextSecretPath)).toBe(false);
      expect(engine.list()).toEqual([fs.realpathSync(previousFilePath)]);
      expect(appliedPaths).toEqual(new Set([fs.realpathSync(previousFilePath)]));
      expect(engine.getPolicyRevision()).toBe(1);
    } finally {
      fs.rmSync(nextDir, { recursive: true, force: true });
    }
  });

  it("falls back to an empty snapshot when rollback replay fails", async () => {
    const previousAPath = path.join(tmpDir, "previous-a.txt");
    const previousBPath = path.join(tmpDir, "previous-b.txt");
    fs.writeFileSync(previousAPath, "a");
    fs.writeFileSync(previousBPath, "b");

    const nextDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-test-next-"));
    const nextEnvPath = path.join(nextDir, ".env");
    const nextSecretPath = path.join(nextDir, "secret.txt");
    fs.writeFileSync(nextEnvPath, "env");
    fs.writeFileSync(nextSecretPath, "secret");

    const appliedPaths = new Set<string>();
    let nextReplayAttempts = 0;
    let restoringPrevious = false;
    let previousRestoreAttempts = 0;
    let cleanupCalls = 0;
    const fakeEnforcer: PolicyEnforcer = {
      applyProtection: async (targetPath) => {
        const nextDirRealPath = fs.realpathSync(nextDir);
        if (targetPath.startsWith(nextDirRealPath)) {
          nextReplayAttempts += 1;
          if (nextReplayAttempts === 2) {
            restoringPrevious = true;
            throw new Error("replay failed");
          }
        } else if (restoringPrevious) {
          previousRestoreAttempts += 1;
          if (previousRestoreAttempts === 2) {
            throw new Error("restore failed");
          }
        }

        appliedPaths.add(targetPath);
      },
      removeProtection: async (targetPath) => {
        appliedPaths.delete(targetPath);
      },
      isAvailable: () => true,
      cleanup: async () => {
        cleanupCalls += 1;
        appliedPaths.clear();
      },
    };
    engine.setEnforcer(fakeEnforcer);

    try {
      await engine.setProjectRoot(tmpDir);
      await engine.protect(previousAPath);
      await engine.protect(previousBPath);

      const nextPolicyPath = getWorkspacePolicyPath(nextDir, policyStoreDir);
      fs.mkdirSync(path.dirname(nextPolicyPath), { recursive: true });
      fs.writeFileSync(
        nextPolicyPath,
        JSON.stringify({
          version: 3,
          workspaceRoot: fs.realpathSync(nextDir),
          rules: [
            {
              id: "rule-preset-env",
              kind: "preset",
              source: "preset",
              presetId: "env-files",
              createdAt: "2026-04-03T00:00:00.000Z",
              updatedAt: "2026-04-03T00:00:00.000Z",
            },
            {
              id: "rule-secret",
              kind: "path",
              source: "manual",
              targetPath: "secret.txt",
              createdAt: "2026-04-03T00:00:00.000Z",
              updatedAt: "2026-04-03T00:00:00.000Z",
            },
          ],
          updatedAt: "2026-04-03T00:00:00.000Z",
        }),
        "utf-8"
      );

      await expect(engine.setProjectRoot(nextDir)).rejects.toThrow("restore failed");
      expect(cleanupCalls).toBeGreaterThanOrEqual(2);
      expect(engine.list()).toEqual([]);
      expect(engine.listRules()).toEqual([]);
      expect(engine.isProtected(previousAPath)).toBe(false);
      expect(engine.isProtected(previousBPath)).toBe(false);
      expect(engine.isProtected(nextEnvPath)).toBe(false);
      expect(engine.isProtected(nextSecretPath)).toBe(false);
      expect(engine.getPolicyRevision()).toBe(3);
      expect(
        JSON.parse(
          fs.readFileSync(
            getWorkspacePolicyPath(tmpDir, policyStoreDir),
            "utf-8"
          )
        )
      ).toMatchObject({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [],
      });
    } finally {
      fs.rmSync(nextDir, { recursive: true, force: true });
    }
  });

  it("adds and removes manual path rules through the rule APIs", async () => {
    await engine.setProjectRoot(tmpDir);

    const filePath = path.join(tmpDir, "api.key");
    fs.writeFileSync(filePath, "secret");

    const addResult = await engine.addPathRule(filePath);
    expect(addResult).toEqual({ changed: true });
    expect(engine.isProtected(filePath)).toBe(true);

    const rules = engine.listRules();
    expect(rules).toHaveLength(1);
    expect(rules[0]).toMatchObject({
      kind: "path",
      source: "manual",
      targetPath: "api.key",
    });

    expect(await engine.addPathRule(filePath)).toEqual({
      changed: false,
      reason: "already-protected",
    });

    expect(await engine.removeRule(rules[0].id)).toBe(true);
    expect(engine.isProtected(filePath)).toBe(false);
    expect(engine.listRules()).toEqual([]);
    expect(await engine.removeRule(rules[0].id)).toBe(false);
  });

  it("removes directly managed directory rules through the compatibility path", async () => {
    await engine.setProjectRoot(tmpDir);

    const secretsDir = path.join(tmpDir, "secrets");
    const secretFile = path.join(secretsDir, "db.txt");
    fs.mkdirSync(secretsDir);
    fs.writeFileSync(secretFile, "secret");

    expect(await engine.addDirectoryRule(secretsDir)).toEqual({ changed: true });
    expect(engine.list()).toEqual([fs.realpathSync(secretsDir)]);
    expect(await engine.unprotect(secretFile)).toBe(false);
    expect(await engine.unprotect(secretsDir)).toBe(true);
    expect(engine.list()).toEqual([]);
    expect(engine.isProtected(secretsDir)).toBe(false);
    expect(engine.isProtected(secretFile)).toBe(false);
  });

  it("rejects manual path rules outside the current workspace", async () => {
    await engine.setProjectRoot(tmpDir);

    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-outside-manual-"));
    const outsideFile = path.join(outsideDir, "manual.txt");
    fs.writeFileSync(outsideFile, "manual");

    expect(await engine.addPathRule(outsideFile)).toEqual({
      changed: false,
      reason: "outside-workspace",
    });
    expect(await engine.protect(outsideFile)).toBe(false);
    expect(engine.listRules()).toEqual([]);
    expect(engine.listCompiledEntries()).toEqual([]);
  });

  it("applies preset, extension, and directory rules through the rule APIs", async () => {
    await engine.setProjectRoot(tmpDir);

    const envPath = path.join(tmpDir, ".env");
    const certPath = path.join(tmpDir, "cert.pem");
    const secretsDir = path.join(tmpDir, "secrets");
    const secretFile = path.join(secretsDir, "db.txt");
    fs.writeFileSync(envPath, "env");
    fs.writeFileSync(certPath, "pem");
    fs.mkdirSync(secretsDir);
    fs.writeFileSync(secretFile, "secret");

    expect(await engine.applyPreset("env-files")).toEqual({ changed: true });
    expect(await engine.addExtensionRule([".pem"])).toEqual({ changed: true });
    expect(await engine.addDirectoryRule(secretsDir)).toEqual({ changed: true });

    expect(engine.listRules()).toHaveLength(3);
    expect(engine.listRules().map((rule) => rule.kind)).toEqual([
      "preset",
      "extension",
      "directory",
    ]);
    expect(engine.listRules().map((rule) => rule.source)).toEqual([
      "preset",
      "extension",
      "directory",
    ]);
    expect(engine.listCompiledEntries().map((entry) => entry.relativePath)).toEqual([
      ".env",
      "cert.pem",
      "secrets",
      "secrets/db.txt",
    ]);
    expect(engine.exportWorkspacePolicy()).toMatchObject({
      version: 3,
      workspaceRoot: fs.realpathSync(tmpDir),
      rules: expect.arrayContaining([
        expect.objectContaining({ kind: "preset", presetId: "env-files" }),
        expect.objectContaining({ kind: "extension", extensions: [".pem"] }),
        expect.objectContaining({
          kind: "directory",
          targetPath: "secrets",
        }),
      ]),
      updatedAt: expect.any(String),
    });
  });

  it("labels compiled protections with source-specific labels and folder types", async () => {
    await engine.setProjectRoot(tmpDir);

    const envPath = path.join(tmpDir, ".env");
    const certPath = path.join(tmpDir, "cert.pem");
    const secretsDir = path.join(tmpDir, "secrets");
    const secretFile = path.join(secretsDir, "db.txt");
    fs.writeFileSync(envPath, "env");
    fs.writeFileSync(certPath, "pem");
    fs.mkdirSync(secretsDir);
    fs.writeFileSync(secretFile, "secret");

    expect(await engine.applyPreset("env-files")).toEqual({ changed: true });
    expect(await engine.addExtensionRule([".pem"])).toEqual({ changed: true });
    expect(await engine.addDirectoryRule(secretsDir)).toEqual({ changed: true });

    const compiled = engine.listCompiledEntries();
    expect(compiled.find((entry) => entry.relativePath === ".env")).toMatchObject({
      type: "file",
      sourceLabel: "Env Files Preset",
    });
    expect(
      compiled.find((entry) => entry.relativePath === "cert.pem")
    ).toMatchObject({
      type: "file",
      sourceLabel: ".pem Rule",
    });
    expect(compiled.find((entry) => entry.relativePath === "secrets")).toMatchObject({
      type: "folder",
      sourceLabel: "Directory Folder",
    });
    expect(
      compiled.find((entry) => entry.relativePath === "secrets/db.txt")
    ).toMatchObject({
      type: "file",
      sourceLabel: "Directory Folder",
    });
  });

  it("imports relative rules from a different stored workspace root into the current workspace", async () => {
    await engine.setProjectRoot(tmpDir);

    const otherRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-other-root-"));
    const importedPath = path.join(tmpDir, "imports", "secret.txt");
    fs.mkdirSync(path.dirname(importedPath), { recursive: true });
    fs.writeFileSync(importedPath, "secret");

    const result = await engine.importWorkspacePolicy({
      version: 3,
      workspaceRoot: fs.realpathSync(otherRoot),
      rules: [
        {
          id: "imported-rule",
          kind: "path",
          source: "import",
          targetPath: "imports/secret.txt",
          createdAt: "2026-04-03T00:00:00.000Z",
          updatedAt: "2026-04-03T00:00:00.000Z",
        },
      ],
      updatedAt: "2026-04-03T00:00:00.000Z",
    });

    expect(result).toEqual({ changed: true });
    expect(engine.isProtected(importedPath)).toBe(true);
    expect(engine.listRules()).toHaveLength(1);
    expect(engine.listRules()[0]).toMatchObject({
      id: "imported-rule",
      kind: "path",
      source: "import",
      targetPath: "imports/secret.txt",
    });
    expect(engine.exportWorkspacePolicy()).toMatchObject({
      workspaceRoot: fs.realpathSync(tmpDir),
      rules: [
        expect.objectContaining({
          id: "imported-rule",
          targetPath: "imports/secret.txt",
        }),
      ],
    });
  });

  it("rejects directory rules outside the current workspace", async () => {
    await engine.setProjectRoot(tmpDir);

    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-outside-dir-"));

    const result = await engine.addDirectoryRule(outsideDir);

    expect(result).toEqual({
      changed: false,
      reason: "outside-workspace",
    });
    expect(engine.listRules()).toEqual([]);
  });

  it("rejects imported direct targets outside the current workspace", async () => {
    await engine.setProjectRoot(tmpDir);

    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-outside-import-"));
    const result = await engine.importWorkspacePolicy({
      version: 3,
      workspaceRoot: fs.realpathSync(tmpDir),
      rules: [
        {
          id: "outside-rule",
          kind: "path",
          source: "import",
          targetPath: path.join(outsideDir, "secret.txt"),
          createdAt: "2026-04-03T00:00:00.000Z",
          updatedAt: "2026-04-03T00:00:00.000Z",
        },
      ],
      updatedAt: "2026-04-03T00:00:00.000Z",
    });

    expect(result).toEqual({
      changed: false,
      reason: "outside-workspace",
    });
    expect(engine.listRules()).toEqual([]);
    expect(engine.listCompiledEntries()).toEqual([]);
  });

  it("replaces the workspace rule set when importing and exports the replacement", async () => {
    await engine.setProjectRoot(tmpDir);

    const originalFile = path.join(tmpDir, "original.txt");
    const importedFile = path.join(tmpDir, "imported.txt");
    fs.writeFileSync(originalFile, "original");
    fs.writeFileSync(importedFile, "imported");

    await engine.addPathRule(originalFile);

    expect(
      await engine.importWorkspacePolicy({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [
          {
            id: "imported-rule",
            kind: "path",
            source: "import",
            targetPath: "imported.txt",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      })
    ).toEqual({ changed: true });

    expect(engine.listRules()).toHaveLength(1);
    expect(engine.listRules()[0]).toMatchObject({
      id: "imported-rule",
      kind: "path",
      source: "import",
      targetPath: "imported.txt",
    });
    expect(engine.isProtected(originalFile)).toBe(false);
    expect(engine.isProtected(importedFile)).toBe(true);
    expect(engine.exportWorkspacePolicy()).toMatchObject({
      version: 3,
      workspaceRoot: fs.realpathSync(tmpDir),
      rules: [
        {
          id: "imported-rule",
          kind: "path",
          source: "import",
          targetPath: "imported.txt",
        },
      ],
    });
  });

  it("recomputes dynamic rules when the workspace changes", async () => {
    await engine.setProjectRoot(tmpDir);

    const certPath = path.join(tmpDir, "cert.pem");
    expect(await engine.addExtensionRule([".pem"])).toEqual({ changed: true });
    expect(engine.listCompiledEntries()).toEqual([]);

    fs.writeFileSync(certPath, "pem");

    expect(await engine.recomputeDynamicRules()).toBe(true);
    expect(engine.listCompiledEntries().map((entry) => entry.relativePath)).toEqual([
      "cert.pem",
    ]);
    expect(engine.isProtected(certPath)).toBe(true);
    expect(engine.getPolicyRevision()).toBe(1);
  });

  it("recomputes dynamic rules from indexed change deltas", async () => {
    await engine.setProjectRoot(tmpDir);

    const certPath = path.join(tmpDir, "cert.pem");

    expect(await engine.addExtensionRule([".pem"])).toEqual({ changed: true });
    engine.setWorkspaceEntries(tmpDir, []);

    fs.writeFileSync(certPath, "pem");
    const certEntry = {
      path: fs.realpathSync(certPath),
      relativePath: "cert.pem",
      name: "cert.pem",
      isDirectory: false,
    };
    engine.setWorkspaceEntries(tmpDir, [certEntry]);

    expect(await engine.recomputeDynamicRulesForEntries([certEntry], [])).toBe(true);
    expect(engine.listCompiledEntries().map((entry) => entry.relativePath)).toEqual([
      "cert.pem",
    ]);
    expect(engine.isProtected(certPath)).toBe(true);

    fs.rmSync(certPath);
    engine.setWorkspaceEntries(tmpDir, []);

    expect(await engine.recomputeDynamicRulesForEntries([], [certEntry])).toBe(true);
    expect(engine.listCompiledEntries()).toEqual([]);
    expect(engine.isProtected(certPath)).toBe(false);
  });

  it("updates cached workspace entries incrementally from indexed deltas", async () => {
    await engine.setProjectRoot(tmpDir);

    const certPath = path.join(tmpDir, "cert.pem");

    expect(await engine.addExtensionRule([".pem"])).toEqual({ changed: true });
    engine.setWorkspaceEntries(tmpDir, []);

    fs.writeFileSync(certPath, "pem");
    const certEntry = {
      path: fs.realpathSync(certPath),
      relativePath: "cert.pem",
      name: "cert.pem",
      isDirectory: false,
    };

    engine.updateWorkspaceEntriesForDelta(tmpDir, [certEntry], []);

    expect(await engine.recomputeDynamicRulesForEntries([certEntry], [])).toBe(true);
    expect(engine.listCompiledEntries().map((entry) => entry.relativePath)).toEqual([
      "cert.pem",
    ]);

    fs.rmSync(certPath);
    engine.updateWorkspaceEntriesForDelta(tmpDir, [], [certEntry]);

    expect(await engine.recomputeDynamicRulesForEntries([], [certEntry])).toBe(true);
    expect(engine.listCompiledEntries()).toEqual([]);
  });

  it("reports whether a workspace needs full inventory on open", async () => {
    expect(engine.requiresWorkspaceInventoryForRoot(tmpDir)).toBe(false);

    const policyPath = getWorkspacePolicyPath(tmpDir, policyStoreDir);
    fs.mkdirSync(path.dirname(policyPath), { recursive: true });
    fs.writeFileSync(
      policyPath,
      JSON.stringify({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [
          {
            id: "rule-preset-env",
            kind: "preset",
            source: "preset",
            presetId: "env-files",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "utf-8"
    );

    expect(engine.requiresWorkspaceInventoryForRoot(tmpDir)).toBe(true);
  });

  it("restores the previous enforcer state when replacing rules fails mid-diff", async () => {
    await engine.setProjectRoot(tmpDir);

    const originalPath = path.join(tmpDir, "original.txt");
    const replacementDir = path.join(tmpDir, "replacement");
    const replacementPath = path.join(replacementDir, "new.txt");
    fs.mkdirSync(replacementDir);
    fs.writeFileSync(originalPath, "original");
    fs.writeFileSync(replacementPath, "replacement");

    const appliedPaths = new Set<string>();
    const fakeEnforcer: PolicyEnforcer = {
      applyProtection: async (targetPath) => {
        appliedPaths.add(targetPath);
      },
      removeProtection: async (targetPath) => {
        if (targetPath === fs.realpathSync(originalPath)) {
          throw new Error("remove failed");
        }
        appliedPaths.delete(targetPath);
      },
      isAvailable: () => true,
      cleanup: async () => {
        appliedPaths.clear();
      },
    };
    engine.setEnforcer(fakeEnforcer);

    await engine.addPathRule(originalPath);
    expect(appliedPaths).toEqual(new Set([fs.realpathSync(originalPath)]));

    await expect(
      engine.importWorkspacePolicy({
        version: 3,
        workspaceRoot: path.join(os.tmpdir(), "ai-ide-elsewhere-root"),
        rules: [
          {
            id: "replacement-dir-rule",
            kind: "directory",
            source: "import",
            targetPath: "replacement",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      })
    ).rejects.toThrow("remove failed");
    expect(engine.listRules()).toHaveLength(1);
    expect(engine.listRules()[0]).toMatchObject({
      kind: "path",
      targetPath: "original.txt",
    });
    expect(engine.isProtected(originalPath)).toBe(true);
    expect(engine.isProtected(replacementPath)).toBe(false);
    expect(appliedPaths).toEqual(new Set([fs.realpathSync(originalPath)]));
  });

  it("falls back to an empty snapshot when replaceRules rollback replay fails", async () => {
    await engine.setProjectRoot(tmpDir);

    const previousAPath = path.join(tmpDir, "previous-a.txt");
    const previousBPath = path.join(tmpDir, "previous-b.txt");
    const nextPath = path.join(tmpDir, "next.txt");
    fs.writeFileSync(previousAPath, "a");
    fs.writeFileSync(previousBPath, "b");
    fs.writeFileSync(nextPath, "next");

    const appliedPaths = new Set<string>();
    let previousReplayAttempts = 0;
    let rollbackStarted = false;
    const fakeEnforcer: PolicyEnforcer = {
      applyProtection: async (targetPath) => {
        if (rollbackStarted) {
          previousReplayAttempts += 1;
          if (previousReplayAttempts === 2) {
            throw new Error("rollback replay failed");
          }
        }

        appliedPaths.add(targetPath);
      },
      removeProtection: async (targetPath) => {
        if (targetPath === fs.realpathSync(previousBPath)) {
          rollbackStarted = true;
          throw new Error("remove failed");
        }
        appliedPaths.delete(targetPath);
      },
      isAvailable: () => true,
      cleanup: async () => {
        appliedPaths.clear();
      },
    };
    engine.setEnforcer(fakeEnforcer);

    await engine.protect(previousAPath);
    await engine.protect(previousBPath);

    await expect(
      engine.importWorkspacePolicy({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [
          {
            id: "rule-previous-a",
            kind: "path",
            source: "import",
            targetPath: "previous-a.txt",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
          {
            id: "rule-next",
            kind: "path",
            source: "import",
            targetPath: "next.txt",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      })
    ).rejects.toThrow("rollback replay failed");

    expect(engine.list()).toEqual([]);
    expect(engine.listRules()).toEqual([]);
    expect(engine.isProtected(previousAPath)).toBe(false);
    expect(engine.isProtected(previousBPath)).toBe(false);
    expect(engine.isProtected(nextPath)).toBe(false);
    expect(appliedPaths).toEqual(new Set<string>());
    expect(engine.getPolicyRevision()).toBe(3);
    expect(
      JSON.parse(
        fs.readFileSync(getWorkspacePolicyPath(tmpDir, policyStoreDir), "utf-8")
      )
    ).toMatchObject({
      version: 3,
      workspaceRoot: fs.realpathSync(tmpDir),
      rules: [],
    });
  });

  it("restores the previous enforcer state when dynamic recompilation fails mid-diff", async () => {
    await engine.setProjectRoot(tmpDir);

    const envPath = path.join(tmpDir, ".env");
    const certPath = path.join(tmpDir, "cert.pem");
    fs.writeFileSync(envPath, "env");
    const envRealPath = fs.realpathSync(envPath);

    const appliedPaths = new Set<string>();
    let removeCount = 0;
    const fakeEnforcer: PolicyEnforcer = {
      applyProtection: async (targetPath) => {
        appliedPaths.add(targetPath);
      },
      removeProtection: async (targetPath) => {
        removeCount += 1;
        if (removeCount === 1) {
          throw new Error("recompute remove failed");
        }
        appliedPaths.delete(targetPath);
      },
      isAvailable: () => true,
      cleanup: async () => {
        appliedPaths.clear();
      },
    };
    engine.setEnforcer(fakeEnforcer);

    await engine.applyPreset("env-files");
    expect(appliedPaths).toContain(fs.realpathSync(envPath));

    await engine.addExtensionRule([".pem"]);
    fs.writeFileSync(certPath, "cert");
    fs.rmSync(envPath);
    await expect(engine.recomputeDynamicRules()).rejects.toThrow(
      "recompute remove failed"
    );
    expect(engine.listCompiledEntries().map((entry) => entry.path)).toEqual([
      envRealPath,
    ]);
    expect(engine.isProtected(certPath)).toBe(false);
    expect(appliedPaths).toEqual(new Set([envRealPath]));
    expect(engine.getPolicyRevision()).toBe(1);
  });

  it("does not mutate state when addPathRule enforcer application fails", async () => {
    await engine.setProjectRoot(tmpDir);

    const filePath = path.join(tmpDir, "blocked.txt");
    fs.writeFileSync(filePath, "secret");

    engine.setEnforcer({
      applyProtection: async () => {
        throw new Error("apply failed");
      },
      removeProtection: async () => {},
      isAvailable: () => true,
      cleanup: async () => {},
    });

    await expect(engine.addPathRule(filePath)).rejects.toThrow("apply failed");
    expect(engine.isProtected(filePath)).toBe(false);
    expect(engine.listRules()).toEqual([]);
    expect(engine.list()).toEqual([]);
    expect(engine.getPolicyRevision()).toBe(0);
    expect(fs.existsSync(getWorkspacePolicyPath(tmpDir, policyStoreDir))).toBe(false);
  });

  it("does not mutate state when removeRule enforcer removal fails", async () => {
    await engine.setProjectRoot(tmpDir);

    const filePath = path.join(tmpDir, "blocked-remove.txt");
    fs.writeFileSync(filePath, "secret");

    await engine.addPathRule(filePath);
    const [rule] = engine.listRules();

    engine.setEnforcer({
      applyProtection: async () => {},
      removeProtection: async () => {
        throw new Error("remove failed");
      },
      isAvailable: () => true,
      cleanup: async () => {},
    });

    await expect(engine.removeRule(rule.id)).rejects.toThrow("remove failed");
    expect(engine.isProtected(filePath)).toBe(true);
    expect(engine.listRules()).toHaveLength(1);
    expect(engine.listRules()[0]).toMatchObject({ id: rule.id });
    expect(engine.list()).toContain(fs.realpathSync(filePath));
    expect(engine.getPolicyRevision()).toBe(1);
  });

  it("keeps list focused on directly managed paths while compiled protections remain visible", async () => {
    const envPath = path.join(tmpDir, ".env");
    const manualPath = path.join(tmpDir, "manual.txt");
    fs.writeFileSync(envPath, "secret");
    fs.writeFileSync(manualPath, "manual");

    const policyPath = getWorkspacePolicyPath(tmpDir, policyStoreDir);
    fs.mkdirSync(path.dirname(policyPath), { recursive: true });
    fs.writeFileSync(
      policyPath,
      JSON.stringify({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [
          {
            id: "rule-preset-env",
            kind: "preset",
            source: "preset",
            presetId: "env-files",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
          {
            id: "rule-manual",
            kind: "path",
            source: "manual",
            targetPath: "manual.txt",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "utf-8"
    );

    await engine.setProjectRoot(tmpDir);

    expect(engine.list()).toEqual([fs.realpathSync(manualPath)]);
    expect(engine.isProtected(envPath)).toBe(true);
    expect(engine.isProtected(manualPath)).toBe(true);
    expect(
      engine.listCompiledEntries().map((entry) => entry.path)
    ).toContain(fs.realpathSync(envPath));
  });

  it("ignores invalid version 3 rules at load time", async () => {
    const validPath = path.join(tmpDir, "valid.txt");
    const pemPath = path.join(tmpDir, "cert.pem");
    fs.writeFileSync(validPath, "valid");
    fs.writeFileSync(pemPath, "pem");

    const policyPath = getWorkspacePolicyPath(tmpDir, policyStoreDir);
    fs.mkdirSync(path.dirname(policyPath), { recursive: true });
    fs.writeFileSync(
      policyPath,
      JSON.stringify({
        version: 3,
        workspaceRoot: fs.realpathSync(tmpDir),
        rules: [
          {
            id: "rule-valid",
            kind: "path",
            source: "manual",
            targetPath: "valid.txt",
            createdAt: "2026-04-03T00:00:00.000Z",
            updatedAt: "2026-04-03T00:00:00.000Z",
          },
          {
            id: "rule-invalid-kind",
            kind: "invalid-kind",
            source: "manual",
            targetPath: "ignored.txt",
          },
          {
            id: "rule-invalid-source",
            kind: "path",
            source: "invalid-source",
            targetPath: "ignored-source.txt",
          },
          {
            id: "rule-empty-target",
            kind: "path",
            source: "manual",
            targetPath: "",
          },
          {
            id: "rule-whitespace-target",
            kind: "directory",
            source: "manual",
            targetPath: "   ",
          },
          {
            id: "rule-invalid-preset",
            kind: "preset",
            source: "preset",
            presetId: "invalid-preset-id",
          },
          {
            id: "rule-invalid-extension",
            kind: "extension",
            source: "extension",
            extensions: [".pem", 42],
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "utf-8"
    );

    await engine.setProjectRoot(tmpDir);

    expect(engine.listRules()).toHaveLength(1);
    expect(engine.listRules()[0]).toMatchObject({
      id: "rule-valid",
      kind: "path",
      source: "manual",
      targetPath: "valid.txt",
    });
    expect(engine.isProtected(validPath)).toBe(true);
    expect(engine.isProtected(pemPath)).toBe(false);
  });

  it("should not duplicate protections", async () => {
    const filePath = path.join(tmpDir, "file.txt");
    fs.writeFileSync(filePath, "data");

    const result1 = await engine.protect(filePath);
    const result2 = await engine.protect(filePath);
    expect(result1).toBe(true);
    expect(result2).toBe(false);
    expect(engine.list().length).toBe(1);
  });

  it("increments policy revision only when effective protection changes", async () => {
    await engine.setProjectRoot(tmpDir);
    const filePath = path.join(tmpDir, "secret.txt");
    fs.writeFileSync(filePath, "secret");

    const initialRevision = engine.getPolicyRevision();
    expect(initialRevision).toBe(0);

    expect(await engine.protect(filePath)).toBe(true);
    expect(engine.getPolicyRevision()).toBe(1);

    expect(await engine.protect(filePath)).toBe(false);
    expect(engine.getPolicyRevision()).toBe(1);

    expect(await engine.unprotect(filePath)).toBe(true);
    expect(engine.getPolicyRevision()).toBe(2);
  });

  it("increments policy revision when switching workspaces", async () => {
    const otherDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-test-other-"));

    try {
      await engine.setProjectRoot(tmpDir);
      expect(engine.getPolicyRevision()).toBe(0);

      await engine.setProjectRoot(otherDir);
      expect(engine.getPolicyRevision()).toBe(1);

      await engine.setProjectRoot(tmpDir);
      expect(engine.getPolicyRevision()).toBe(2);
    } finally {
      fs.rmSync(otherDir, { recursive: true, force: true });
    }
  });

  it("does not increment policy revision for inherited protection no-ops", async () => {
    await engine.setProjectRoot(tmpDir);
    const protectedDir = path.join(tmpDir, "secrets");
    const childPath = path.join(protectedDir, "key.pem");
    fs.mkdirSync(protectedDir);
    fs.writeFileSync(childPath, "key");

    expect(await engine.protect(protectedDir)).toBe(true);
    expect(engine.getPolicyRevision()).toBe(1);

    expect(await engine.protect(childPath)).toBe(false);
    expect(engine.getPolicyRevision()).toBe(1);

    expect(await engine.unprotect(childPath)).toBe(false);
    expect(engine.getPolicyRevision()).toBe(1);
  });

  it("removes redundant child protections without incrementing policy revision", async () => {
    await engine.setProjectRoot(tmpDir);
    const protectedDir = path.join(tmpDir, "secrets");
    const childPath = path.join(protectedDir, "key.pem");
    fs.mkdirSync(protectedDir);
    fs.writeFileSync(childPath, "key");

    expect(await engine.protect(childPath)).toBe(true);
    expect(engine.getPolicyRevision()).toBe(1);

    expect(await engine.protect(protectedDir)).toBe(true);
    expect(engine.getPolicyRevision()).toBe(2);

    expect(await engine.unprotect(childPath)).toBe(true);
    expect(engine.getPolicyRevision()).toBe(2);
    expect(engine.isProtected(childPath)).toBe(true);
  });

  it("should migrate legacy per-project policy files", async () => {
    const filePath = path.join(tmpDir, "secret.txt");
    fs.writeFileSync(filePath, "secret");

    const legacyPath = getLegacyPolicyPath(tmpDir);
    fs.mkdirSync(path.dirname(legacyPath), { recursive: true });
    fs.writeFileSync(
      legacyPath,
      JSON.stringify({
        version: 1,
        protected: [fs.realpathSync(filePath)],
      }),
      "utf-8"
    );

    await engine.setProjectRoot(tmpDir);

    expect(engine.isProtected(filePath)).toBe(true);
    expect(fs.existsSync(getWorkspacePolicyPath(tmpDir, policyStoreDir))).toBe(true);
    expect(fs.existsSync(legacyPath)).toBe(false);

    const migratedPolicy = JSON.parse(
      fs.readFileSync(getWorkspacePolicyPath(tmpDir, policyStoreDir), "utf-8")
    );
    expect(migratedPolicy).toMatchObject({
      version: 3,
      workspaceRoot: fs.realpathSync(tmpDir),
      rules: [
        {
          kind: "path",
          source: "manual",
          targetPath: "secret.txt",
          createdAt: expect.any(String),
          updatedAt: expect.any(String),
        },
      ],
      updatedAt: expect.any(String),
    });
    expect(migratedPolicy.protected).toBeUndefined();
  });

  it("migrates version 2 workspace policy files into manual path rules on load", async () => {
    const filePath = path.join(tmpDir, "secret.txt");
    fs.writeFileSync(filePath, "secret");

    const policyPath = getWorkspacePolicyPath(tmpDir, policyStoreDir);
    fs.mkdirSync(path.dirname(policyPath), { recursive: true });
    fs.writeFileSync(
      policyPath,
      JSON.stringify({
        version: 2,
        workspaceRoot: fs.realpathSync(tmpDir),
        protected: ["secret.txt"],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "utf-8"
    );

    await engine.setProjectRoot(tmpDir);

    expect(engine.isProtected(filePath)).toBe(true);

    const migratedPolicy = JSON.parse(fs.readFileSync(policyPath, "utf-8"));
    expect(migratedPolicy).toMatchObject({
      version: 3,
      workspaceRoot: fs.realpathSync(tmpDir),
      rules: [
        {
          kind: "path",
          source: "manual",
          targetPath: "secret.txt",
          createdAt: expect.any(String),
          updatedAt: expect.any(String),
        },
      ],
      updatedAt: expect.any(String),
    });
    expect(migratedPolicy.protected).toBeUndefined();
  });

  it("should clear protections when switching to a workspace without policy", async () => {
    const filePath = path.join(tmpDir, "secret.txt");
    const otherDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-test-other-"));
    fs.writeFileSync(filePath, "secret");

    try {
      await engine.setProjectRoot(tmpDir);
      await engine.protect(filePath);
      expect(engine.isProtected(filePath)).toBe(true);

      await engine.setProjectRoot(otherDir);

      expect(engine.list()).toEqual([]);
      expect(engine.isProtected(filePath)).toBe(false);
    } finally {
      fs.rmSync(otherDir, { recursive: true, force: true });
    }
  });
});
