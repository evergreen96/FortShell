/**
 * Platform-agnostic interface for file policy enforcement.
 * Each OS implements this differently:
 * - Windows: Restricted Token + Integrity Label
 * - Linux: Landlock + seccomp
 * - macOS: sandbox-exec (Seatbelt)
 */
export interface PolicyEnforcer {
  /** Apply protection to a file/directory so restricted processes can't access it */
  applyProtection(filePath: string): Promise<void>;
  /** Remove protection from a file/directory */
  removeProtection(filePath: string): Promise<void>;
  /** Check if enforcement is available on this platform */
  isAvailable(): boolean;
  /** Clean up all applied protections (e.g., on app exit) */
  cleanup(): Promise<void>;
  /** Get sandboxed spawn args for a shell (optional — not all platforms support this) */
  getSandboxedSpawnArgs?(shell: string): { command: string; args: string[] } | null;
}

export interface RestrictedSpawner {
  /** Spawn a shell process with restricted permissions */
  spawnRestricted(opts: {
    shell: string;
    args: string[];
    cwd: string;
    cols: number;
    rows: number;
    env: Record<string, string>;
  }): any; // Returns platform-specific PTY handle
  /** Check if restricted spawning is available */
  isAvailable(): boolean;
}
