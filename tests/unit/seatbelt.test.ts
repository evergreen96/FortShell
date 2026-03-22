import { describe, it, expect, afterEach } from "vitest";
import { DarwinSeatbeltEnforcer } from "../../src/main/platform/darwin/seatbelt";
import fs from "fs";
import path from "path";
import os from "os";
import { execSync } from "child_process";

// Skip on non-macOS
const isMac = process.platform === "darwin";
const describeIf = isMac ? describe : describe.skip;

describeIf("DarwinSeatbeltEnforcer", () => {
  let enforcer: DarwinSeatbeltEnforcer;
  let tmpDir: string;

  function setup() {
    enforcer = new DarwinSeatbeltEnforcer();
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-sb-test-"));
  }

  afterEach(async () => {
    if (enforcer) await enforcer.cleanup();
    if (tmpDir) fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("should be available on macOS", () => {
    setup();
    expect(enforcer.isAvailable()).toBe(true);
  });

  it("should return null when no files are protected", () => {
    setup();
    const args = enforcer.getSandboxedSpawnArgs("/bin/zsh");
    expect(args).toBeNull();
  });

  it("should resolve paths through symlinks", async () => {
    setup();
    const filePath = path.join(tmpDir, "secret.txt");
    fs.writeFileSync(filePath, "secret");

    await enforcer.applyProtection(filePath);
    const paths = enforcer.getProtectedPaths();
    const realPath = fs.realpathSync(filePath);
    expect(paths.has(realPath)).toBe(true);
  });

  it("should use literal for files and subpath for directories", async () => {
    setup();
    const filePath = path.join(tmpDir, "file.txt");
    const dirPath = path.join(tmpDir, "dir");
    fs.writeFileSync(filePath, "data");
    fs.mkdirSync(dirPath);

    await enforcer.applyProtection(filePath);
    await enforcer.applyProtection(dirPath);

    const profile = fs.readFileSync(enforcer.generateProfile(), "utf-8");
    expect(profile).toContain("(deny file-read-data (literal");
    expect(profile).toContain("(deny file-read-data (subpath");
  });

  it("should block file read via sandbox-exec", async () => {
    setup();
    const filePath = path.join(tmpDir, "secret.txt");
    fs.writeFileSync(filePath, "TOP SECRET");

    await enforcer.applyProtection(filePath);
    const args = enforcer.getSandboxedSpawnArgs("/bin/cat");
    expect(args).not.toBeNull();

    // Sandboxed cat should fail
    let blocked = false;
    try {
      execSync(`${args!.command} ${args!.args.map((a) => `"${a}"`).join(" ")} "${filePath}"`, {
        encoding: "utf-8",
        timeout: 5000,
      });
    } catch {
      blocked = true;
    }
    expect(blocked).toBe(true);

    // Unsandboxed cat should succeed
    const content = execSync(`cat "${filePath}"`, { encoding: "utf-8" });
    expect(content.trim()).toBe("TOP SECRET");
  });

  it("should allow ls of parent when individual file is protected", async () => {
    setup();
    const filePath = path.join(tmpDir, "secret.txt");
    fs.writeFileSync(filePath, "SECRET");
    fs.writeFileSync(path.join(tmpDir, "public.txt"), "PUBLIC");

    await enforcer.applyProtection(filePath);
    const args = enforcer.getSandboxedSpawnArgs("/bin/ls");
    expect(args).not.toBeNull();

    const output = execSync(
      `${args!.command} ${args!.args.map((a) => `"${a}"`).join(" ")} "${tmpDir}"`,
      { encoding: "utf-8", timeout: 5000 }
    );
    expect(output).toContain("secret.txt");
    expect(output).toContain("public.txt");
  });

  it("should block symlink traversal", async () => {
    setup();
    const filePath = path.join(tmpDir, "secret.txt");
    const linkPath = path.join(tmpDir, "link.txt");
    fs.writeFileSync(filePath, "SECRET");
    fs.symlinkSync(filePath, linkPath);

    await enforcer.applyProtection(filePath);
    const args = enforcer.getSandboxedSpawnArgs("/bin/cat");

    let blocked = false;
    try {
      execSync(`${args!.command} ${args!.args.map((a) => `"${a}"`).join(" ")} "${linkPath}"`, {
        encoding: "utf-8",
        timeout: 5000,
      });
    } catch {
      blocked = true;
    }
    expect(blocked).toBe(true);
  });
});
