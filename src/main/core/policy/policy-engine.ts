import crypto from "crypto";
import fs from "fs";
import path from "path";
import type { PolicyEnforcer } from "../../platform/types";
import { loadWorkspacePolicy, saveWorkspacePolicy } from "../config/policy-store";
import { resolveRealPath } from "../utils";
import { searchWorkspace } from "../workspace/file-indexer";
import { compileProtectionRules } from "./protection-compiler";
import {
  BUILT_IN_PRESETS,
  type CompiledProtectionEntry,
  type ProtectionRule,
} from "./protection-rules";

type PolicyEngineOptions = {
  policyStoreDir?: string;
};

function isRelativeToRoot(rootPath: string, candidatePath: string): boolean {
  const relative = path.relative(rootPath, candidatePath);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function toStoredTargetPath(projectRoot: string | null, targetPath: string): string {
  const normalizedTarget = resolveRealPath(targetPath);
  if (!projectRoot) {
    return normalizedTarget;
  }

  const normalizedRoot = resolveRealPath(projectRoot);
  if (!isRelativeToRoot(normalizedRoot, normalizedTarget)) {
    return normalizedTarget;
  }

  const relativePath = path.relative(normalizedRoot, normalizedTarget);
  return relativePath === "" ? "." : relativePath.replace(/\\/g, "/");
}

function resolveRuleTargetPath(
  projectRoot: string | null,
  rule: ProtectionRule
): string | null {
  if (rule.kind !== "path" && rule.kind !== "directory") {
    return null;
  }

  if (path.isAbsolute(rule.targetPath) || !projectRoot) {
    return resolveRealPath(rule.targetPath);
  }

  const normalizedRoot = resolveRealPath(projectRoot);
  return rule.targetPath === "."
    ? normalizedRoot
    : resolveRealPath(path.join(normalizedRoot, rule.targetPath));
}

function isDirectoryPath(filePath: string): boolean {
  try {
    return fs.statSync(resolveRealPath(filePath)).isDirectory();
  } catch {
    return false;
  }
}

function cloneRule(rule: ProtectionRule): ProtectionRule {
  if (rule.kind === "extension") {
    return { ...rule, extensions: [...rule.extensions] };
  }

  return { ...rule };
}

function buildWorkspaceEntries(projectRoot: string) {
  const normalizedRoot = resolveRealPath(projectRoot);
  const rootName = path.basename(normalizedRoot) || normalizedRoot;
  const searchResults = searchWorkspace(normalizedRoot, {
    includeDirectories: true,
    limit: Number.MAX_SAFE_INTEGER,
  });

  return [
    {
      path: normalizedRoot,
      relativePath: ".",
      name: rootName,
      ext: "",
      isDirectory: true,
    },
    ...searchResults.map((entry) => ({
      path: resolveRealPath(entry.path),
      relativePath: entry.relativePath,
      name: entry.name,
      ext: entry.isDirectory ? "" : path.extname(entry.name),
      isDirectory: entry.isDirectory,
    })),
  ];
}

export class PolicyEngine {
  private rules: ProtectionRule[] = [];
  private compiledEntries: CompiledProtectionEntry[] = [];
  private protectedPaths = new Set<string>();
  private protectedDirectories = new Set<string>();
  private projectRoot: string | null = null;
  private enforcer: PolicyEnforcer | null = null;
  private readonly policyStoreDir?: string;
  private policyRevision = 0;

  constructor(options: PolicyEngineOptions = {}) {
    this.policyStoreDir = options.policyStoreDir;
  }

  setEnforcer(enforcer: PolicyEnforcer): void {
    this.enforcer = enforcer;
  }

  getPolicyRevision(): number {
    return this.policyRevision;
  }

  listRules(): ProtectionRule[] {
    return this.rules.map((rule) => cloneRule(rule));
  }

  listCompiledEntries(): CompiledProtectionEntry[] {
    return this.compiledEntries.map((entry) => ({ ...entry }));
  }

  async setProjectRoot(root: string): Promise<void> {
    const previousContext = this.getEffectivePolicyContext();

    if (this.enforcer?.isAvailable()) {
      try {
        await this.enforcer.cleanup();
      } catch (err) {
        console.warn(`[policy] Failed to reset previous policy state:`, err);
      }
    }

    this.projectRoot = resolveRealPath(root);
    this.load();

    const nextContext = this.getEffectivePolicyContext();
    if (previousContext !== null && previousContext !== nextContext) {
      this.bumpPolicyRevision();
    }

    if (this.enforcer?.isAvailable()) {
      for (const protectedPath of this.list()) {
        try {
          await this.enforcer.applyProtection(protectedPath);
        } catch (err) {
          console.warn(`[policy] Failed to re-apply protection for ${protectedPath}:`, err);
        }
      }
    }
  }

  async protect(filePath: string): Promise<boolean> {
    const normalized = resolveRealPath(filePath);
    if (this.isProtected(normalized)) return false;

    if (this.enforcer?.isAvailable()) {
      await this.enforcer.applyProtection(normalized);
    }

    const previousContext = this.getEffectivePolicyContext();
    this.rules.push({
      id: crypto.randomUUID(),
      kind: isDirectoryPath(normalized) ? "directory" : "path",
      source: "manual",
      targetPath: toStoredTargetPath(this.projectRoot, normalized),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
    this.recompileEffectivePolicy();

    if (previousContext !== this.getEffectivePolicyContext()) {
      this.bumpPolicyRevision();
    }

    this.save();
    return true;
  }

  async unprotect(filePath: string): Promise<boolean> {
    const normalized = resolveRealPath(filePath);
    const nextRules = this.rules.filter((rule) => {
      if (rule.source !== "manual") {
        return true;
      }

      return resolveRuleTargetPath(this.projectRoot, rule) !== normalized;
    });

    if (nextRules.length === this.rules.length) return false;

    if (this.enforcer?.isAvailable()) {
      await this.enforcer.removeProtection(normalized);
    }

    const previousContext = this.getEffectivePolicyContext();
    this.rules = nextRules;
    this.recompileEffectivePolicy();

    if (previousContext !== this.getEffectivePolicyContext()) {
      this.bumpPolicyRevision();
    }

    this.save();
    return true;
  }

  isProtected(filePath: string): boolean {
    const normalized = resolveRealPath(filePath);
    if (this.protectedPaths.has(normalized)) return true;

    for (const protectedDirectory of this.protectedDirectories) {
      if (normalized.startsWith(protectedDirectory + path.sep)) {
        return true;
      }
    }

    return false;
  }

  list(): string[] {
    return this.rules
      .filter(
        (rule) =>
          rule.source === "manual" && (rule.kind === "path" || rule.kind === "directory")
      )
      .map((rule) => resolveRuleTargetPath(this.projectRoot, rule))
      .filter((entry): entry is string => Boolean(entry));
  }

  async cleanup(): Promise<void> {
    if (this.enforcer) {
      await this.enforcer.cleanup();
    }
  }

  private save(): void {
    if (!this.projectRoot) return;

    try {
      saveWorkspacePolicy(this.projectRoot, this.rules, this.policyStoreDir);
    } catch (err) {
      console.error(`[policy] Failed to save policy:`, err);
    }
  }

  private load(): void {
    this.rules = [];
    this.compiledEntries = [];
    this.protectedPaths = new Set();
    this.protectedDirectories = new Set();
    if (!this.projectRoot) return;

    try {
      this.rules = loadWorkspacePolicy(this.projectRoot, this.policyStoreDir);
      this.recompileEffectivePolicy();
    } catch (err) {
      console.warn(`[policy] Failed to load policy:`, err);
    }
  }

  private recompileEffectivePolicy(): void {
    this.compiledEntries = [];
    this.protectedPaths = new Set();
    this.protectedDirectories = new Set();

    for (const rule of this.rules) {
      const directTarget = resolveRuleTargetPath(this.projectRoot, rule);
      if (!directTarget) continue;

      this.protectedPaths.add(directTarget);
      if (rule.kind === "directory") {
        this.protectedDirectories.add(directTarget);
      }
    }

    if (!this.projectRoot) {
      return;
    }

    try {
      this.compiledEntries = compileProtectionRules({
        workspaceRoot: this.projectRoot,
        rules: this.rules,
        presetCatalog: BUILT_IN_PRESETS,
        workspaceEntries: buildWorkspaceEntries(this.projectRoot),
      });
    } catch (err) {
      console.warn(`[policy] Failed to compile protection rules:`, err);
      this.compiledEntries = [];
      return;
    }

    for (const entry of this.compiledEntries) {
      const normalizedPath = resolveRealPath(entry.path);
      this.protectedPaths.add(normalizedPath);
      if (entry.isDirectory) {
        this.protectedDirectories.add(normalizedPath);
      }
    }
  }

  private bumpPolicyRevision(): void {
    this.policyRevision += 1;
  }

  private getEffectivePolicyContext(): string | null {
    if (!this.projectRoot) {
      return null;
    }

    const protectedEntries = Array.from(this.protectedPaths).sort();
    return JSON.stringify({
      projectRoot: this.projectRoot,
      protectedEntries,
    });
  }
}
