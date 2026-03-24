import {
  writeFileSync,
  mkdtempSync,
  existsSync,
  statSync,
  rmSync,
} from "fs";
import { join } from "path";
import { tmpdir } from "os";
import path from "path";
import type { PolicyEnforcer } from "../types";
import { resolveRealPath } from "../../core/utils";

/**
 * Resolve the path to the sandbox-wrapper binary.
 * Uses our native C wrapper that calls sandbox_init_with_parameters() directly,
 * avoiding the deprecated sandbox-exec CLI tool.
 *
 * Falls back to sandbox-exec if the wrapper binary is not found.
 */
function findSandboxBinary(): { binary: string; useWrapper: boolean } {
  // Check for our native wrapper next to the app
  const resourcesPath = process.resourcesPath || "";
  // __dirname inside asar: replace .asar with .asar.unpacked for native binaries
  const unpackedDir = __dirname.replace(/app\.asar/, "app.asar.unpacked");
  const candidates = [
    join(resourcesPath, "app.asar.unpacked/native/darwin/sandbox-wrapper"),      // packaged (resourcesPath)
    join(unpackedDir, "../../../native/darwin/sandbox-wrapper"),                  // packaged (__dirname)
    join(__dirname, "../../../native/darwin/sandbox-wrapper"),                    // dev
  ];

  for (const p of candidates) {
    if (existsSync(p)) {
      return { binary: p, useWrapper: true };
    }
  }

  // Fallback to sandbox-exec
  return { binary: "sandbox-exec", useWrapper: false };
}

/**
 * macOS Seatbelt enforcement.
 *
 * Dynamically generates SBPL profiles that deny access to protected files.
 * Uses sandbox_init_with_parameters() via native wrapper binary, falling back
 * to sandbox-exec if the wrapper is not available.
 *
 * No admin privileges required. Kernel-enforced (TrustedBSD MAC framework).
 */
export class DarwinSeatbeltEnforcer implements PolicyEnforcer {
  private protectedFiles = new Set<string>();
  private profileDir: string | null = null;
  private sandboxBin = findSandboxBinary();

  isAvailable(): boolean {
    if (process.platform !== "darwin") return false;
    return true; // Seatbelt kernel API is always available on macOS
  }

  async applyProtection(filePath: string): Promise<void> {
    this.protectedFiles.add(resolveRealPath(filePath));
  }

  async removeProtection(filePath: string): Promise<void> {
    this.protectedFiles.delete(resolveRealPath(filePath));
  }

  async cleanup(): Promise<void> {
    this.protectedFiles.clear();
    if (this.profileDir) {
      try {
        rmSync(this.profileDir, { recursive: true, force: true });
      } catch {}
      this.profileDir = null;
    }
  }

  /**
   * Generate a Seatbelt profile and return spawn args for sandboxed shell.
   * Returns null if no files are protected (no sandbox needed).
   */
  getSandboxedSpawnArgs(shell: string): { command: string; args: string[] } | null {
    if (this.protectedFiles.size === 0) return null;

    try {
      const profilePath = this.generateProfile();
      if (this.sandboxBin.useWrapper) {
        // Native wrapper: sandbox-wrapper <profile> <shell>
        return {
          command: this.sandboxBin.binary,
          args: [profilePath, shell],
        };
      }
      // Fallback: sandbox-exec -f <profile> -- <shell>
      return {
        command: this.sandboxBin.binary,
        args: ["-f", profilePath, "--", shell],
      };
    } catch (err) {
      console.error("[seatbelt] Failed to generate profile, skipping sandbox:", err);
      return null;
    }
  }

  /**
   * Generate a .sb profile that denies access to protected files/directories.
   * Uses "literal" for files and "subpath" for directories.
   */
  generateProfile(): string {
    if (!this.profileDir) {
      this.profileDir = mkdtempSync(join(tmpdir(), "fortshell-sb-"));
    }

    const denyRules = Array.from(this.protectedFiles)
      .map((p) => {
        const escaped = p.replace(/\\/g, "\\\\").replace(/"/g, '\\"');

        // Use subpath for directories, literal for files
        let matchType = "literal";
        try {
          if (statSync(p).isDirectory()) {
            matchType = "subpath";
          }
        } catch {
          // If stat fails, treat as literal (file might not exist yet)
        }

        // file-read-data: blocks content reads but allows ls/stat (metadata)
        // file-write*: blocks all write operations including chmod
        return [
          `(deny file-read-data (${matchType} "${escaped}"))`,
          `(deny file-write* (${matchType} "${escaped}"))`,
        ].join("\n");
      })
      .join("\n");

    const profile = `(version 1)
(allow default)
${denyRules}
`;

    const profilePath = join(this.profileDir, "policy.sb");
    writeFileSync(profilePath, profile, "utf-8");
    return profilePath;
  }

  getProtectedPaths(): Set<string> {
    return new Set(this.protectedFiles);
  }
}
