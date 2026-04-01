import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { PolicyEngine } from "../../src/main/core/policy/policy-engine";
import {
  getLegacyPolicyPath,
  getWorkspacePolicyPath,
} from "../../src/main/core/config/policy-store";
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

  it("should not duplicate protections", async () => {
    const filePath = path.join(tmpDir, "file.txt");
    fs.writeFileSync(filePath, "data");

    const result1 = await engine.protect(filePath);
    const result2 = await engine.protect(filePath);
    expect(result1).toBe(true);
    expect(result2).toBe(false);
    expect(engine.list().length).toBe(1);
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
