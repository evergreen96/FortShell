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
    if (DEFAULT_IGNORE.has(entry.name)) continue;
    if (entry.name.startsWith(".") && depth === 0) continue;

    // Check .gitignore
    if (ig && rootPath) {
      const relativePath = path.relative(rootPath, path.join(dirPath, entry.name));
      const posixPath = relativePath.replace(/\\/g, "/");
      const testPath = entry.isDirectory() ? posixPath + "/" : posixPath;
      if (ig.ignores(testPath)) continue;
    }

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
