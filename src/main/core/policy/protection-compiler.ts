import path from "path";
import {
  type CompiledProtectionEntry,
  type ProtectionExtensionRule,
  type ProtectionPreset,
  type ProtectionPresetRule,
  type ProtectionRule,
  type WorkspaceProtectionEntry,
  getProtectionPresetById,
  getProtectionRuleSourceLabel,
} from "./protection-rules";

type CompileProtectionRulesInput = {
  workspaceRoot: string;
  rules: readonly ProtectionRule[];
  presetCatalog: readonly ProtectionPreset[];
  workspaceEntries: readonly WorkspaceProtectionEntry[];
};

function normalizeExtensionToken(token: string): string {
  const normalized = token.trim().toLowerCase();
  return normalized.startsWith(".") ? normalized : `.${normalized}`;
}

function normalizeRulePath(workspaceRoot: string, targetPath: string): string | null {
  const absoluteTarget = path.isAbsolute(targetPath)
    ? path.resolve(targetPath)
    : path.resolve(workspaceRoot, targetPath);
  const relative = path.relative(workspaceRoot, absoluteTarget).replace(/\\/g, "/");
  if (relative.startsWith("..")) {
    return null;
  }
  return relative === "" ? "." : relative;
}

function matchesPathRule(
  workspaceRoot: string,
  targetPath: string,
  entry: WorkspaceProtectionEntry
): boolean {
  const normalizedTarget = normalizeRulePath(workspaceRoot, targetPath);
  if (!normalizedTarget) {
    return false;
  }

  return entry.relativePath === normalizedTarget;
}

function matchesDirectoryRule(
  workspaceRoot: string,
  targetPath: string,
  entry: WorkspaceProtectionEntry
): boolean {
  const normalizedTarget = normalizeRulePath(workspaceRoot, targetPath);
  if (!normalizedTarget) {
    return false;
  }

  if (normalizedTarget === ".") {
    return true;
  }

  return (
    entry.relativePath === normalizedTarget ||
    entry.relativePath.startsWith(`${normalizedTarget}/`)
  );
}

function matchesExtensionRule(
  rule: ProtectionExtensionRule,
  entry: WorkspaceProtectionEntry
): boolean {
  if (entry.isDirectory) {
    return false;
  }

  const entryExt = entry.ext.trim().toLowerCase();
  if (!entryExt) {
    return false;
  }

  const normalizedExtensions = rule.extensions.map(normalizeExtensionToken);
  return normalizedExtensions.includes(entryExt);
}

function compilePresetRule(
  workspaceRoot: string,
  presetRule: ProtectionPresetRule,
  entry: WorkspaceProtectionEntry
): boolean {
  if (presetRule.kind === "path") {
    return matchesPathRule(workspaceRoot, presetRule.value, entry);
  }

  if (presetRule.kind === "directory") {
    return matchesDirectoryRule(workspaceRoot, presetRule.value, entry);
  }

  const extensionRule: ProtectionExtensionRule = {
    id: "__preset_extension__",
    kind: "extension",
    source: "preset",
    extensions: Array.isArray(presetRule.value) ? presetRule.value : [presetRule.value],
  };
  return matchesExtensionRule(extensionRule, entry);
}

function compileRuleEntries(
  workspaceRoot: string,
  rule: ProtectionRule,
  presetCatalog: readonly ProtectionPreset[],
  workspaceEntries: readonly WorkspaceProtectionEntry[]
): CompiledProtectionEntry[] {
  const sourceLabel = getProtectionRuleSourceLabel(rule, presetCatalog);
  const directTarget =
    rule.kind === "path" || rule.kind === "directory"
      ? normalizeRulePath(workspaceRoot, rule.targetPath)
      : null;
  const matched: CompiledProtectionEntry[] = [];

  for (const entry of workspaceEntries) {
    let matches = false;

    if (rule.kind === "path") {
      matches = matchesPathRule(workspaceRoot, rule.targetPath, entry);
    } else if (rule.kind === "directory") {
      matches = matchesDirectoryRule(workspaceRoot, rule.targetPath, entry);
    } else if (rule.kind === "extension") {
      matches = matchesExtensionRule(rule, entry);
    } else {
      const preset = getProtectionPresetById(rule.presetId, presetCatalog);
      if (!preset) {
        throw new Error(`Unknown protection preset: ${rule.presetId}`);
      }

      matches = preset.rules.some((presetRule) =>
        compilePresetRule(workspaceRoot, presetRule, entry)
      );
    }

      if (matches) {
        matched.push({
          ...entry,
          type: entry.isDirectory ? "folder" : "file",
          status: "shielded",
          canRemoveDirectly:
            rule.source === "manual" && directTarget !== null && entry.relativePath === directTarget,
          sourceRuleId: rule.id,
          sourceKind: rule.kind,
          sourceLabel,
        });
      }
  }

  return matched;
}

export function compileProtectionRules(
  input: CompileProtectionRulesInput
): CompiledProtectionEntry[] {
  const workspaceRoot = path.resolve(input.workspaceRoot);
  const compiled = new Map<string, CompiledProtectionEntry>();

  for (const rule of input.rules) {
    const entries = compileRuleEntries(
      workspaceRoot,
      rule,
      input.presetCatalog,
      input.workspaceEntries
    );

    for (const entry of entries) {
      if (!compiled.has(entry.path)) {
        compiled.set(entry.path, entry);
      }
    }
  }

  return Array.from(compiled.values()).sort((left, right) =>
    left.relativePath.localeCompare(right.relativePath)
  );
}
