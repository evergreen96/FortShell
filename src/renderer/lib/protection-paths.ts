import type { CompiledProtectionEntry } from "./types";

function normalizeProtectionPath(filePath: string): string {
  return filePath.replace(/\\/g, "/").replace(/\/+$/, "");
}

export function buildExplorerProtectedPathSet(
  compiledEntries: readonly Pick<CompiledProtectionEntry, "path">[]
): Set<string> {
  return new Set(compiledEntries.map((entry) => normalizeProtectionPath(entry.path)));
}

type ExplorerContextMenuAction =
  | { kind: "protect" }
  | { kind: "remove"; sourceRuleId: string }
  | { kind: "view-protection"; sourceRuleId: string };

export function isPathProtected(filePath: string, protectedPaths: ReadonlySet<string>): boolean {
  const normalizedFilePath = normalizeProtectionPath(filePath);

  if (protectedPaths.has(normalizedFilePath)) {
    return true;
  }

  for (const protectedPath of protectedPaths) {
    if (normalizedFilePath.startsWith(`${protectedPath}/`)) {
      return true;
    }

    if (protectedPath.startsWith(`${normalizedFilePath}/`)) {
      return true;
    }
  }

  return false;
}

function findExactCompiledProtection(
  filePath: string,
  compiledEntries: readonly Pick<
    CompiledProtectionEntry,
    "path" | "canRemoveDirectly" | "sourceRuleId"
  >[]
): (Pick<CompiledProtectionEntry, "path" | "canRemoveDirectly" | "sourceRuleId"> & {
  path: string;
}) | null {
  const normalizedFilePath = normalizeProtectionPath(filePath);

  for (const entry of compiledEntries) {
    if (normalizeProtectionPath(entry.path) === normalizedFilePath) {
      return entry;
    }
  }

  return null;
}

function findCoveringCompiledProtection(
  filePath: string,
  compiledEntries: readonly Pick<
    CompiledProtectionEntry,
    "path" | "canRemoveDirectly" | "sourceRuleId"
  >[]
): (Pick<CompiledProtectionEntry, "path" | "canRemoveDirectly" | "sourceRuleId"> & {
  path: string;
}) | null {
  const normalizedFilePath = normalizeProtectionPath(filePath);
  let bestMatch: (Pick<CompiledProtectionEntry, "path" | "canRemoveDirectly" | "sourceRuleId"> & {
    path: string;
  }) | null = null;

  for (const entry of compiledEntries) {
    if (normalizeProtectionPath(entry.path) === normalizedFilePath) {
      continue;
    }

    const normalizedEntryPath = normalizeProtectionPath(entry.path);
    if (normalizedFilePath.startsWith(`${normalizedEntryPath}/`)) {
      if (!bestMatch || normalizedEntryPath.length > normalizeProtectionPath(bestMatch.path).length) {
        bestMatch = entry;
      }
    }
  }

  return bestMatch;
}

export function getExplorerContextMenuAction(
  filePath: string,
  compiledEntries: readonly Pick<
    CompiledProtectionEntry,
    "path" | "canRemoveDirectly" | "sourceRuleId"
  >[]
): ExplorerContextMenuAction {
  const exactMatch = findExactCompiledProtection(filePath, compiledEntries);
  if (exactMatch?.canRemoveDirectly) {
    return { kind: "remove", sourceRuleId: exactMatch.sourceRuleId };
  }

  const coveringMatch = exactMatch ?? findCoveringCompiledProtection(filePath, compiledEntries);
  if (coveringMatch) {
    return { kind: "view-protection", sourceRuleId: coveringMatch.sourceRuleId };
  }

  return { kind: "protect" };
}
