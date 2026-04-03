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

type PolicySnapshot = {
  compiledEntries: CompiledProtectionEntry[];
  enforcerTargets: string[];
  protectedDirectories: Set<string>;
  protectedPaths: Set<string>;
  rules: ProtectionRule[];
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

  async addPathRule(filePath: string): Promise<{ changed: boolean; reason?: string }> {
    const normalized = resolveRealPath(filePath);
    if (this.isProtected(normalized)) {
      return { changed: false, reason: "already-protected" };
    }

    const previousContext = this.getEffectivePolicyContext();
    const previousEnforcerTargets = this.getEnforcerTargetsForRules(this.rules, this.compiledEntries);
    const nextRule: ProtectionRule = {
      id: crypto.randomUUID(),
      kind: isDirectoryPath(normalized) ? "directory" : "path",
      source: "manual",
      targetPath: toStoredTargetPath(this.projectRoot, normalized),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    const nextRules = [
      ...this.rules,
      nextRule,
    ];
    const nextState = this.buildPolicySnapshot(nextRules);

    await this.applyAddedEnforcerTargets(previousEnforcerTargets, nextState.enforcerTargets);

    this.commitPolicySnapshot(nextState);

    if (previousContext !== this.getEffectivePolicyContext()) {
      this.bumpPolicyRevision();
    }

    this.save();
    return { changed: true };
  }

  async removeRule(ruleId: string): Promise<boolean> {
    const nextRules = this.rules.filter((rule) => rule.id !== ruleId);
    if (nextRules.length === this.rules.length) {
      return false;
    }

    const previousContext = this.getEffectivePolicyContext();
    const previousEnforcerTargets = this.getEnforcerTargetsForRules(this.rules, this.compiledEntries);
    const nextState = this.buildPolicySnapshot(nextRules);

    await this.removeDroppedEnforcerTargets(previousEnforcerTargets, nextState.enforcerTargets);

    this.commitPolicySnapshot(nextState);

    if (previousContext !== this.getEffectivePolicyContext()) {
      this.bumpPolicyRevision();
    }

    this.save();
    return true;
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

    await this.applyAllEnforcerTargets();
  }

  async protect(filePath: string): Promise<boolean> {
    const result = await this.addPathRule(filePath);
    return result.changed;
  }

  async unprotect(filePath: string): Promise<boolean> {
    const normalized = resolveRealPath(filePath);
    const manualRule = this.rules.find(
      (rule) =>
        rule.source === "manual" &&
        resolveRuleTargetPath(this.projectRoot, rule) === normalized
    );
    if (!manualRule) {
      return false;
    }

    return this.removeRule(manualRule.id);
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
    return Array.from(this.protectedPaths).sort();
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
      this.commitPolicySnapshot(this.buildPolicySnapshot(this.rules));
    } catch (err) {
      console.warn(`[policy] Failed to load policy:`, err);
    }
  }

  private buildPolicySnapshot(rules: ProtectionRule[]): PolicySnapshot {
    const compiledEntries: CompiledProtectionEntry[] = [];
    const protectedPaths = new Set<string>();
    const protectedDirectories = new Set<string>();
    const normalizedRules = rules.map((rule) => cloneRule(rule));

    for (const rule of normalizedRules) {
      const directTarget = resolveRuleTargetPath(this.projectRoot, rule);
      if (!directTarget) continue;

      protectedPaths.add(directTarget);
      if (rule.kind === "directory") {
        protectedDirectories.add(directTarget);
      }
    }

    if (!this.projectRoot) {
      return {
        compiledEntries,
        enforcerTargets: this.getEnforcerTargetsForRules(normalizedRules, compiledEntries),
        protectedDirectories,
        protectedPaths,
        rules: normalizedRules,
      };
    }

    try {
      compiledEntries.push(...compileProtectionRules({
        workspaceRoot: this.projectRoot,
        rules: normalizedRules,
        presetCatalog: BUILT_IN_PRESETS,
        workspaceEntries: buildWorkspaceEntries(this.projectRoot),
      }));
    } catch (err) {
      console.warn(`[policy] Failed to compile protection rules:`, err);
      return {
        compiledEntries: [],
        enforcerTargets: this.getEnforcerTargetsForRules(normalizedRules, []),
        protectedDirectories,
        protectedPaths,
        rules: normalizedRules,
      };
    }

    for (const entry of compiledEntries) {
      const normalizedPath = resolveRealPath(entry.path);
      protectedPaths.add(normalizedPath);
      if (entry.isDirectory) {
        protectedDirectories.add(normalizedPath);
      }
    }

    return {
      compiledEntries,
      enforcerTargets: this.getEnforcerTargetsForRules(normalizedRules, compiledEntries),
      protectedDirectories,
      protectedPaths,
      rules: normalizedRules,
    };
  }

  private commitPolicySnapshot(snapshot: PolicySnapshot): void {
    this.rules = snapshot.rules;
    this.compiledEntries = snapshot.compiledEntries;
    this.protectedPaths = snapshot.protectedPaths;
    this.protectedDirectories = snapshot.protectedDirectories;
  }

  private getEnforcerTargetsForRules(
    rules: readonly ProtectionRule[],
    compiledEntries: readonly CompiledProtectionEntry[]
  ): string[] {
    const targets = new Set<string>();
    const ruleById = new Map(rules.map((rule) => [rule.id, rule]));

    for (const rule of rules) {
      const directTarget = resolveRuleTargetPath(this.projectRoot, rule);
      if (directTarget) {
        targets.add(directTarget);
      }
    }

    for (const entry of compiledEntries) {
      const sourceRule = ruleById.get(entry.sourceRuleId);
      if (!sourceRule) continue;
      if (sourceRule.kind === "path" || sourceRule.kind === "directory") {
        continue;
      }

      targets.add(resolveRealPath(entry.path));
    }

    return Array.from(targets).sort();
  }

  private async applyAllEnforcerTargets(): Promise<void> {
    if (!this.enforcer?.isAvailable()) {
      return;
    }

    for (const protectedPath of this.getEnforcerTargetsForRules(this.rules, this.compiledEntries)) {
      try {
        await this.enforcer.applyProtection(protectedPath);
      } catch (err) {
        console.warn(`[policy] Failed to re-apply protection for ${protectedPath}:`, err);
      }
    }
  }

  private async applyAddedEnforcerTargets(
    previousTargets: string[],
    nextTargets: string[]
  ): Promise<void> {
    if (!this.enforcer?.isAvailable()) {
      return;
    }

    const previous = new Set(previousTargets);
    for (const protectedPath of nextTargets) {
      if (previous.has(protectedPath)) {
        continue;
      }

      await this.enforcer.applyProtection(protectedPath);
    }
  }

  private async removeDroppedEnforcerTargets(
    previousTargets: string[],
    nextTargets: string[]
  ): Promise<void> {
    if (!this.enforcer?.isAvailable()) {
      return;
    }

    const next = new Set(nextTargets);
    for (const protectedPath of previousTargets) {
      if (next.has(protectedPath)) {
        continue;
      }

      await this.enforcer.removeProtection(protectedPath);
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
