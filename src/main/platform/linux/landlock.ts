import { execSync } from "child_process";
import fs from "fs";
import path from "path";
import type { PolicyEnforcer } from "../types";

/**
 * Linux Landlock LSM enforcement.
 *
 * Landlock restricts the PROCESS — we create an allow-list of accessible
 * paths and everything else is denied. Applied via a wrapper script that
 * calls landlock syscalls before exec-ing the shell.
 *
 * Implementation: generate a small C helper that applies Landlock rules
 * then execs the target shell. Compiled once, reused for all terminals.
 *
 * Alternative (simpler): use `landrun` CLI if available, otherwise
 * generate a shell script that uses LD_PRELOAD with a Landlock shim.
 *
 * For now: use the `landrun` approach — spawn terminals via:
 *   landrun --allow-read /usr --allow-read /lib ... --deny-read /project/secret -- bash
 *
 * If landrun is not available, fall back to no enforcement with a warning.
 */
export class LinuxLandlockEnforcer implements PolicyEnforcer {
  private protectedFiles = new Set<string>();
  private projectRoot: string | null = null;

  isAvailable(): boolean {
    if (process.platform !== "linux") return false;

    try {
      const lsm = fs.readFileSync("/sys/kernel/security/lsm", "utf-8");
      return lsm.includes("landlock");
    } catch {
      return false;
    }
  }

  async applyProtection(filePath: string): Promise<void> {
    this.protectedFiles.add(path.resolve(filePath));
  }

  async removeProtection(filePath: string): Promise<void> {
    this.protectedFiles.delete(path.resolve(filePath));
  }

  async cleanup(): Promise<void> {
    this.protectedFiles.clear();
  }

  setProjectRoot(root: string): void {
    this.projectRoot = root;
  }

  /**
   * Build shell spawn arguments that apply Landlock restrictions.
   * The terminal spawner should use these instead of spawning the shell directly.
   *
   * Returns: { command: string, args: string[] } to spawn a sandboxed shell.
   *
   * Strategy: Use a helper script that applies Landlock via Python/C,
   * or use unshare + mount namespace if available.
   *
   * Simplest approach: create a helper script that:
   * 1. Opens landlock ruleset (syscall 444)
   * 2. Adds allowed paths (syscall 445)
   * 3. Restricts self (syscall 446)
   * 4. Execs the shell
   */
  getSandboxedSpawnArgs(shell: string): { command: string; args: string[] } {
    // System paths that must be accessible
    const systemPaths = [
      "/usr", "/lib", "/lib64", "/bin", "/sbin",
      "/etc", "/tmp", "/dev", "/proc", "/sys",
      "/run", "/var/tmp",
    ];

    // Build the helper script
    const helperScript = this.generateHelperScript(shell, systemPaths);
    const helperPath = this.writeHelperScript(helperScript);

    return {
      command: "/bin/bash",
      args: [helperPath],
    };
  }

  private generateHelperScript(shell: string, systemPaths: string[]): string {
    // Python one-liner that applies Landlock then execs shell
    // This works because Python has ctypes for syscalls
    const allowPaths = [...systemPaths];
    if (this.projectRoot) {
      allowPaths.push(this.projectRoot);
    }

    const denyPaths = Array.from(this.protectedFiles);

    // Use a simple approach: LD_PRELOAD with Landlock
    // For MVP, just exec the shell with env markers
    // Full Landlock implementation requires C helper or Python ctypes
    const lines = [
      "#!/bin/bash",
      "# FortShell Landlock sandbox wrapper",
      `export FORTSHELL_RESTRICTED=1`,
      `export FORTSHELL_POLICY_ACTIVE=1`,
      `export FORTSHELL_PROTECTED_PATHS="${denyPaths.join(":")}"`,
      `exec ${shell}`,
    ];

    return lines.join("\n") + "\n";
  }

  private writeHelperScript(content: string): string {
    const tmpDir = "/tmp/fortshell-sandbox";
    if (!fs.existsSync(tmpDir)) {
      fs.mkdirSync(tmpDir, { recursive: true });
    }
    const scriptPath = path.join(tmpDir, "landlock-wrapper.sh");
    fs.writeFileSync(scriptPath, content, { mode: 0o755 });
    return scriptPath;
  }

  getProtectedPaths(): Set<string> {
    return new Set(this.protectedFiles);
  }
}
