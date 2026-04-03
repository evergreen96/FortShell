import crypto from "crypto";
import fs from "fs";
import path from "path";
import {
  BUILT_IN_PRESETS,
  type ProtectionPresetId,
  type ProtectionRule,
  type ProtectionRuleSource,
} from "../policy/protection-rules";
import { resolveRealPath } from "../utils";

type StoredPolicyFileV2 = {
  version: number;
  workspaceRoot: string;
  protected: string[];
  updatedAt: string;
};

type StoredPolicyFileV3 = {
  version: 3;
  workspaceRoot: string;
  rules: ProtectionRule[];
  updatedAt: string;
};

type StoredPolicyFile = StoredPolicyFileV2 | StoredPolicyFileV3;

const VALID_RULE_KINDS = new Set(["path", "directory", "extension", "preset"]);
const VALID_RULE_SOURCES = new Set([
  "manual",
  "directory",
  "extension",
  "preset",
  "import",
]);
const VALID_PRESET_IDS = new Set<string>(BUILT_IN_PRESETS.map((preset) => preset.id));

function getDefaultPolicyStoreDir(): string {
  const { app } = require("electron") as typeof import("electron");
  return path.join(app.getPath("userData"), "policies");
}

export function getPolicyStoreDir(policyStoreDir?: string): string {
  return policyStoreDir ?? getDefaultPolicyStoreDir();
}

export function getWorkspacePolicyId(workspaceRoot: string): string {
  return crypto
    .createHash("sha256")
    .update(resolveRealPath(workspaceRoot))
    .digest("hex");
}

export function getWorkspacePolicyPath(
  workspaceRoot: string,
  policyStoreDir?: string
): string {
  return path.join(
    getPolicyStoreDir(policyStoreDir),
    "workspaces",
    `${getWorkspacePolicyId(workspaceRoot)}.json`
  );
}

export function getLegacyPolicyPath(workspaceRoot: string): string {
  return path.join(resolveRealPath(workspaceRoot), ".fortshell", "policy.json");
}

function isRelativeToRoot(rootPath: string, candidatePath: string): boolean {
  const relative = path.relative(rootPath, candidatePath);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function toStoredPath(workspaceRoot: string, targetPath: string): string {
  const rootPath = resolveRealPath(workspaceRoot);
  const normalizedPath = resolveRealPath(targetPath);

  if (!isRelativeToRoot(rootPath, normalizedPath)) {
    return normalizedPath;
  }

  const relativePath = path.relative(rootPath, normalizedPath);
  return relativePath === "" ? "." : relativePath.replace(/\\/g, "/");
}

function fromStoredPath(workspaceRoot: string, storedPath: string): string {
  if (path.isAbsolute(storedPath)) {
    return resolveRealPath(storedPath);
  }

  const rootPath = resolveRealPath(workspaceRoot);
  return storedPath === "."
    ? rootPath
    : resolveRealPath(path.join(rootPath, storedPath));
}

function parseStoredRule(rule: unknown): ProtectionRule | null {
  if (!rule || typeof rule !== "object") {
    return null;
  }

  const candidate = rule as Record<string, unknown>;
  const id = typeof candidate.id === "string" ? candidate.id : "";
  const kind = typeof candidate.kind === "string" ? candidate.kind : "";
  const source = typeof candidate.source === "string" ? candidate.source : "";
  const createdAt =
    typeof candidate.createdAt === "string" ? candidate.createdAt : undefined;
  const updatedAt =
    typeof candidate.updatedAt === "string" ? candidate.updatedAt : undefined;

  if (
    !id ||
    !VALID_RULE_KINDS.has(kind) ||
    !VALID_RULE_SOURCES.has(source)
  ) {
    return null;
  }

  if ((kind === "path" || kind === "directory") && typeof candidate.targetPath === "string") {
    if (candidate.targetPath.trim().length === 0) {
      return null;
    }

    return {
      id,
      kind,
      source: source as ProtectionRuleSource,
      targetPath: candidate.targetPath,
      createdAt,
      updatedAt,
    };
  }

  if (kind === "extension" && Array.isArray(candidate.extensions)) {
    const extensions = candidate.extensions.filter(
      (entry: unknown): entry is string => typeof entry === "string" && entry.trim().length > 0
    );
    if (extensions.length !== candidate.extensions.length || extensions.length === 0) {
      return null;
    }

    return {
      id,
      kind,
      source: source as ProtectionRuleSource,
      extensions,
      createdAt,
      updatedAt,
    };
  }

  if (
    kind === "preset" &&
    typeof candidate.presetId === "string" &&
    VALID_PRESET_IDS.has(candidate.presetId)
  ) {
    return {
      id,
      kind,
      source: source as ProtectionRuleSource,
      presetId: candidate.presetId as ProtectionPresetId,
      createdAt,
      updatedAt,
    };
  }

  return null;
}

function parseStoredPolicy(raw: string, sourcePath: string): StoredPolicyFile | null {
  try {
    const data = JSON.parse(raw);

    if (Array.isArray(data.rules)) {
      return {
        version: 3,
        workspaceRoot: typeof data.workspaceRoot === "string" ? data.workspaceRoot : "",
        rules: data.rules
          .map((entry: unknown) => parseStoredRule(entry))
          .filter((entry: ProtectionRule | null): entry is ProtectionRule => Boolean(entry)),
        updatedAt: typeof data.updatedAt === "string" ? data.updatedAt : "",
      };
    }

    if (!Array.isArray(data.protected)) {
      return null;
    }

    return {
      version: Number(data.version) || 1,
      workspaceRoot: typeof data.workspaceRoot === "string" ? data.workspaceRoot : "",
      protected: data.protected.filter((entry: unknown): entry is string => typeof entry === "string"),
      updatedAt: typeof data.updatedAt === "string" ? data.updatedAt : "",
    };
  } catch (err) {
    console.warn(`[policy] Failed to load policy from ${sourcePath}:`, err);
    return null;
  }
}

function removeLegacyPolicyFile(workspaceRoot: string): void {
  const legacyPath = getLegacyPolicyPath(workspaceRoot);
  if (!fs.existsSync(legacyPath)) return;

  try {
    fs.rmSync(legacyPath, { force: true });
  } catch (err) {
    console.warn(`[policy] Failed to remove legacy policy file ${legacyPath}:`, err);
    return;
  }

  const legacyDir = path.dirname(legacyPath);
  try {
    if (fs.existsSync(legacyDir) && fs.readdirSync(legacyDir).length === 0) {
      fs.rmdirSync(legacyDir);
    }
  } catch {}
}

function toStoredRule(workspaceRoot: string, rule: ProtectionRule): ProtectionRule {
  if (rule.kind === "path" || rule.kind === "directory") {
    return {
      ...rule,
      targetPath: path.isAbsolute(rule.targetPath)
        ? toStoredPath(workspaceRoot, rule.targetPath)
        : rule.targetPath.replace(/\\/g, "/"),
    };
  }

  return { ...rule };
}

function fromStoredRule(workspaceRoot: string, rule: ProtectionRule): ProtectionRule {
  if (rule.kind === "path" || rule.kind === "directory") {
    return {
      ...rule,
      targetPath: path.isAbsolute(rule.targetPath)
        ? toStoredPath(workspaceRoot, rule.targetPath)
        : rule.targetPath.replace(/\\/g, "/"),
    };
  }

  return { ...rule };
}

function createMigratedRule(
  workspaceRoot: string,
  storedPath: string,
  timestamp: string
): ProtectionRule {
  const absolutePath = fromStoredPath(workspaceRoot, storedPath);
  let kind: ProtectionRule["kind"] = "path";

  try {
    if (fs.statSync(absolutePath).isDirectory()) {
      kind = "directory";
    }
  } catch {}

  return {
    id: crypto.randomUUID(),
    kind,
    source: "manual",
    targetPath: toStoredPath(workspaceRoot, absolutePath),
    createdAt: timestamp,
    updatedAt: timestamp,
  };
}

export function saveWorkspacePolicy(
  workspaceRoot: string,
  rules: readonly ProtectionRule[],
  policyStoreDir?: string
): void {
  const filePath = getWorkspacePolicyPath(workspaceRoot, policyStoreDir);
  const dirPath = path.dirname(filePath);
  const normalizedRoot = resolveRealPath(workspaceRoot);

  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }

  const data: StoredPolicyFileV3 = {
    version: 3,
    workspaceRoot: normalizedRoot,
    rules: rules.map((rule) => toStoredRule(normalizedRoot, rule)),
    updatedAt: new Date().toISOString(),
  };

  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf-8");
  removeLegacyPolicyFile(normalizedRoot);
}

export function loadWorkspacePolicy(
  workspaceRoot: string,
  policyStoreDir?: string
): ProtectionRule[] {
  const normalizedRoot = resolveRealPath(workspaceRoot);
  const filePath = getWorkspacePolicyPath(normalizedRoot, policyStoreDir);

  if (fs.existsSync(filePath)) {
    const data = parseStoredPolicy(fs.readFileSync(filePath, "utf-8"), filePath);
    removeLegacyPolicyFile(normalizedRoot);
    if (!data) return [];

    if ("rules" in data) {
      return data.rules.map((rule) => fromStoredRule(normalizedRoot, rule));
    }

    const migratedRules = data.protected.map((entry) =>
      createMigratedRule(normalizedRoot, entry, data.updatedAt || new Date().toISOString())
    );
    saveWorkspacePolicy(normalizedRoot, migratedRules, policyStoreDir);
    return migratedRules;
  }

  const legacyPath = getLegacyPolicyPath(normalizedRoot);
  if (!fs.existsSync(legacyPath)) {
    return [];
  }

  const data = parseStoredPolicy(fs.readFileSync(legacyPath, "utf-8"), legacyPath);
  if (!data || "rules" in data) return [];

  const migratedRules = data.protected.map((entry) =>
    createMigratedRule(normalizedRoot, entry, data.updatedAt || new Date().toISOString())
  );
  saveWorkspacePolicy(normalizedRoot, migratedRules, policyStoreDir);
  return migratedRules;
}
