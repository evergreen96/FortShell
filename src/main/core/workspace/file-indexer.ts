import fs from "fs";
import path from "path";
import ignore, { type Ignore } from "ignore";
import { DEFAULT_IGNORE } from "./constants";

export type FileEntry = {
  name: string;
  path: string;
  isDirectory: boolean;
  children?: FileEntry[];
};

export type WorkspaceSearchOptions = {
  query?: string;
  extensions?: string[];
  includeDirectories?: boolean;
  limit?: number;
};

export type WorkspaceSearchResult = {
  name: string;
  path: string;
  relativePath: string;
  isDirectory: boolean;
};

function loadGitignore(rootPath: string): Ignore | null {
  const gitignorePath = path.join(rootPath, ".gitignore");
  try {
    if (fs.existsSync(gitignorePath)) {
      const content = fs.readFileSync(gitignorePath, "utf-8");
      return ignore().add(content);
    }
  } catch {}
  return null;
}

function shouldIgnoreEntry(
  entry: fs.Dirent,
  dirPath: string,
  rootPath: string,
  ig: Ignore | null
): boolean {
  if (DEFAULT_IGNORE.has(entry.name)) return true;

  if (ig) {
    const relativePath = path.relative(rootPath, path.join(dirPath, entry.name));
    const posixPath = relativePath.replace(/\\/g, "/");
    const testPath = entry.isDirectory() ? `${posixPath}/` : posixPath;
    if (ig.ignores(testPath)) return true;
  }

  return false;
}

/**
 * Index a directory tree. Used for initial load (depth=1) and lazy expand.
 * Set maxDepth=1 for lazy loading (only immediate children).
 */
export function indexDirectory(
  dirPath: string,
  depth: number = 0,
  maxDepth: number = 1,
  rootPath?: string,
  ig?: Ignore | null
): FileEntry[] {
  if (depth > maxDepth) return [];

  // Load .gitignore at root level, resolve symlinks for consistency with policy engine
  if (depth === 0) {
    try { dirPath = fs.realpathSync(dirPath); } catch {}
    rootPath = dirPath;
    ig = loadGitignore(dirPath);
  }

  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dirPath, { withFileTypes: true });
  } catch {
    return [];
  }

  const result: FileEntry[] = [];

  // Sort: directories first, then alphabetical
  entries.sort((a, b) => {
    if (a.isDirectory() !== b.isDirectory()) {
      return a.isDirectory() ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  });

  for (const entry of entries) {
    if (rootPath && shouldIgnoreEntry(entry, dirPath, rootPath, ig ?? null)) continue;

    const fullPath = path.join(dirPath, entry.name);
    const fileEntry: FileEntry = {
      name: entry.name,
      path: fullPath,
      isDirectory: entry.isDirectory(),
    };

    if (entry.isDirectory() && depth < maxDepth) {
      fileEntry.children = indexDirectory(fullPath, depth + 1, maxDepth, rootPath, ig);
    }

    result.push(fileEntry);
  }

  return result;
}

/**
 * Expand a single directory — returns its immediate children only.
 */
export function expandDirectory(dirPath: string, rootPath: string): FileEntry[] {
  const ig = loadGitignore(rootPath);
  try { dirPath = fs.realpathSync(dirPath); } catch {}
  return indexDirectory(dirPath, 0, 1, rootPath, ig);
}

function normalizeExtensionToken(token: string): string | null {
  const normalized = token.trim().toLowerCase();
  if (!normalized) return null;
  return normalized.startsWith(".") ? normalized : `.${normalized}`;
}

function matchesExtension(name: string, extension: string): boolean {
  const lowerName = name.toLowerCase();
  return (
    lowerName === extension ||
    lowerName.endsWith(extension) ||
    lowerName.startsWith(`${extension}.`)
  );
}

function searchDirectory(
  dirPath: string,
  rootPath: string,
  ig: Ignore | null,
  options: Required<WorkspaceSearchOptions>,
  normalizedExtensions: string[],
  results: WorkspaceSearchResult[]
): void {
  if (results.length >= options.limit) return;

  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dirPath, { withFileTypes: true });
  } catch {
    return;
  }

  entries.sort((a, b) => {
    if (a.isDirectory() !== b.isDirectory()) {
      return a.isDirectory() ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  });

  for (const entry of entries) {
    if (shouldIgnoreEntry(entry, dirPath, rootPath, ig)) continue;

    const fullPath = path.join(dirPath, entry.name);
    const relativePath = path.relative(rootPath, fullPath).replace(/\\/g, "/");
    const lowerName = entry.name.toLowerCase();
    const lowerRelativePath = relativePath.toLowerCase();
    const matchesQuery =
      !options.query ||
      lowerName.includes(options.query) ||
      lowerRelativePath.includes(options.query);
    const matchesExtensions =
      normalizedExtensions.length === 0 ||
      (!entry.isDirectory() &&
        normalizedExtensions.some((extension) => matchesExtension(entry.name, extension)));
    const includeEntry = entry.isDirectory() ? options.includeDirectories : true;

    if (includeEntry && matchesQuery && matchesExtensions) {
      results.push({
        name: entry.name,
        path: fullPath,
        relativePath,
        isDirectory: entry.isDirectory(),
      });
      if (results.length >= options.limit) return;
    }

    if (entry.isDirectory()) {
      searchDirectory(fullPath, rootPath, ig, options, normalizedExtensions, results);
      if (results.length >= options.limit) return;
    }
  }
}

export function searchWorkspace(
  dirPath: string,
  options: WorkspaceSearchOptions = {}
): WorkspaceSearchResult[] {
  let rootPath = dirPath;
  try { rootPath = fs.realpathSync(dirPath); } catch {}

  const normalizedOptions: Required<WorkspaceSearchOptions> = {
    includeDirectories: options.includeDirectories ?? true,
    limit: options.limit ?? 50,
    query: options.query?.trim().toLowerCase() ?? "",
    extensions: options.extensions ?? [],
  };
  const normalizedExtensions = normalizedOptions.extensions
    .map(normalizeExtensionToken)
    .filter((value): value is string => Boolean(value));
  const ig = loadGitignore(rootPath);
  const results: WorkspaceSearchResult[] = [];

  searchDirectory(rootPath, rootPath, ig, normalizedOptions, normalizedExtensions, results);
  return results;
}
