import fs from "fs";
import path from "path";
import type { PolicyEnforcer } from "../../platform/types";

function resolveRealPath(filePath: string): string {
  const resolved = path.resolve(filePath);
  try {
    return fs.realpathSync(resolved);
  } catch {
    return resolved;
  }
}

export class PolicyEngine {
  private protectedPaths = new Set<string>();
  private projectRoot: string | null = null;
  private enforcer: PolicyEnforcer | null = null;

  setEnforcer(enforcer: PolicyEnforcer): void {
    this.enforcer = enforcer;
  }

  setProjectRoot(root: string): void {
    this.projectRoot = root;
    this.load();
    // Re-apply enforcement for loaded paths
    if (this.enforcer?.isAvailable()) {
      for (const p of this.protectedPaths) {
        this.enforcer.applyProtection(p).catch(() => {});
      }
    }
  }

  private get policyFilePath(): string | null {
    if (!this.projectRoot) return null;
    return path.join(this.projectRoot, ".fortshell", "policy.json");
  }

  async protect(filePath: string): Promise<boolean> {
    const normalized = resolveRealPath(filePath);
    if (this.protectedPaths.has(normalized)) return false;
    this.protectedPaths.add(normalized);
    this.save();

    if (this.enforcer?.isAvailable()) {
      await this.enforcer.applyProtection(normalized);
    }

    return true;
  }

  async unprotect(filePath: string): Promise<boolean> {
    const normalized = resolveRealPath(filePath);
    if (!this.protectedPaths.has(normalized)) return false;
    this.protectedPaths.delete(normalized);
    this.save();

    if (this.enforcer?.isAvailable()) {
      await this.enforcer.removeProtection(normalized);
    }

    return true;
  }

  isProtected(filePath: string): boolean {
    const normalized = resolveRealPath(filePath);
    if (this.protectedPaths.has(normalized)) return true;
    for (const p of this.protectedPaths) {
      if (normalized.startsWith(p + path.sep)) return true;
    }
    return false;
  }

  list(): string[] {
    return Array.from(this.protectedPaths);
  }

  async cleanup(): Promise<void> {
    if (this.enforcer) {
      await this.enforcer.cleanup();
    }
  }

  private save(): void {
    const filePath = this.policyFilePath;
    if (!filePath) return;

    try {
      const dir = path.dirname(filePath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }

      const data = {
        version: 1,
        protected: Array.from(this.protectedPaths),
      };
      fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf-8");
    } catch (err) {
      console.error(`[policy] Failed to save policy:`, err);
    }
  }

  private load(): void {
    const filePath = this.policyFilePath;
    if (!filePath || !fs.existsSync(filePath)) return;

    try {
      const raw = fs.readFileSync(filePath, "utf-8");
      const data = JSON.parse(raw);
      if (Array.isArray(data.protected)) {
        this.protectedPaths = new Set(
          data.protected.map((p: string) => resolveRealPath(p))
        );
      }
    } catch (err) {
      console.warn(`[policy] Failed to load policy from ${filePath}:`, err);
    }
  }
}
