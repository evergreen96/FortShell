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
