import path from "path";
import type { PolicyEnforcer } from "../../platform/types";
import { resolveRealPath } from "../utils";
import { loadWorkspacePolicy, saveWorkspacePolicy } from "../config/policy-store";

type PolicyEngineOptions = {
  policyStoreDir?: string;
};

export class PolicyEngine {
  private protectedPaths = new Set<string>();
  private projectRoot: string | null = null;
  private enforcer: PolicyEnforcer | null = null;
  private readonly policyStoreDir?: string;

  constructor(options: PolicyEngineOptions = {}) {
    this.policyStoreDir = options.policyStoreDir;
  }

  setEnforcer(enforcer: PolicyEnforcer): void {
    this.enforcer = enforcer;
  }

  async setProjectRoot(root: string): Promise<void> {
    if (this.enforcer?.isAvailable()) {
      try {
        await this.enforcer.cleanup();
      } catch (err) {
        console.warn(`[policy] Failed to reset previous policy state:`, err);
      }
    }

    this.projectRoot = resolveRealPath(root);
    this.load();

    if (this.enforcer?.isAvailable()) {
      for (const p of this.protectedPaths) {
        try {
          await this.enforcer.applyProtection(p);
        } catch (err) {
          console.warn(`[policy] Failed to re-apply protection for ${p}:`, err);
        }
      }
    }
  }

  async protect(filePath: string): Promise<boolean> {
    const normalized = resolveRealPath(filePath);
    if (this.protectedPaths.has(normalized)) return false;

    if (this.enforcer?.isAvailable()) {
      await this.enforcer.applyProtection(normalized);
    }

    this.protectedPaths.add(normalized);
    this.save();
    return true;
  }

  async unprotect(filePath: string): Promise<boolean> {
    const normalized = resolveRealPath(filePath);
    if (!this.protectedPaths.has(normalized)) return false;

    if (this.enforcer?.isAvailable()) {
      await this.enforcer.removeProtection(normalized);
    }

    this.protectedPaths.delete(normalized);
    this.save();
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
    if (!this.projectRoot) return;

    try {
      saveWorkspacePolicy(this.projectRoot, this.protectedPaths, this.policyStoreDir);
    } catch (err) {
      console.error(`[policy] Failed to save policy:`, err);
    }
  }

  private load(): void {
    this.protectedPaths = new Set();
    if (!this.projectRoot) return;

    try {
      this.protectedPaths = loadWorkspacePolicy(
        this.projectRoot,
        this.policyStoreDir
      );
    } catch (err) {
      console.warn(`[policy] Failed to load policy:`, err);
    }
  }
}
