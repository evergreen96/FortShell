import fs from "fs";
import path from "path";
import type { WorkspaceProtectionEntry } from "../policy/protection-rules";
import {
  createWorkspaceSearchResult,
  listWorkspaceEntries,
  searchWorkspaceEntries,
  type WorkspaceSearchOptions,
  type WorkspaceSearchResult,
} from "./file-indexer";

export type WorkspaceIndexState = "cold" | "warming" | "ready" | "stale";

type IndexedWorkspaceEntry = {
  search: WorkspaceSearchResult;
  protection: WorkspaceProtectionEntry;
};

function toWorkspaceProtectionEntry(entry: WorkspaceSearchResult): WorkspaceProtectionEntry {
  return {
    path: entry.path,
    relativePath: entry.relativePath,
    name: entry.name,
    ext: entry.isDirectory ? "" : path.extname(entry.name),
    isDirectory: entry.isDirectory,
  };
}

export class WorkspaceIndexService {
  private rootPath: string | null = null;
  private entries = new Map<string, IndexedWorkspaceEntry>();
  private childrenByParent = new Map<string, Set<string>>();
  private state: WorkspaceIndexState = "cold";
  private warmPromise: Promise<void> | null = null;

  private clearEntries(): void {
    this.entries.clear();
    this.childrenByParent.clear();
  }

  private addEntry(entry: WorkspaceSearchResult): void {
    const parent = path.posix.dirname(entry.relativePath);
    const normalizedParent = parent === "" ? "." : parent;
    const indexedEntry: IndexedWorkspaceEntry = {
      search: entry,
      protection: toWorkspaceProtectionEntry(entry),
    };
    this.entries.set(entry.relativePath, indexedEntry);

    let siblings = this.childrenByParent.get(normalizedParent);
    if (!siblings) {
      siblings = new Set<string>();
      this.childrenByParent.set(normalizedParent, siblings);
    }
    siblings.add(entry.relativePath);
  }

  private removeEntry(relativePath: string): WorkspaceSearchResult | null {
    const entry = this.entries.get(relativePath);
    if (!entry) {
      return null;
    }

    this.entries.delete(relativePath);
    const parent = path.posix.dirname(relativePath);
    const normalizedParent = parent === "" ? "." : parent;
    const siblings = this.childrenByParent.get(normalizedParent);
    if (siblings) {
      siblings.delete(relativePath);
      if (siblings.size === 0) {
        this.childrenByParent.delete(normalizedParent);
      }
    }

    return entry.search;
  }

  async setRoot(rootPath: string): Promise<void> {
    try {
      this.rootPath = fs.realpathSync(rootPath);
    } catch {
      this.rootPath = rootPath;
    }
    this.clearEntries();
    this.state = "cold";
    this.warmPromise = null;
  }

  async warm(): Promise<void> {
    if (!this.rootPath) {
      return;
    }

    if (this.warmPromise) {
      return this.warmPromise;
    }

    this.state = "warming";
    this.warmPromise = (async () => {
      this.clearEntries();
      for (const entry of listWorkspaceEntries(this.rootPath!)) {
        this.addEntry(entry);
      }
      this.state = "ready";
      this.warmPromise = null;
    })();

    return this.warmPromise;
  }

  search(options: WorkspaceSearchOptions = {}): WorkspaceSearchResult[] {
    if (this.state !== "ready") {
      return [];
    }

    return searchWorkspaceEntries(
      Array.from(this.entries.values(), (entry) => entry.search),
      options
    );
  }

  markStale(): void {
    if (!this.rootPath) {
      return;
    }
    this.state = "stale";
    this.warmPromise = null;
  }

  getState(): WorkspaceIndexState {
    return this.state;
  }

  getRootPath(): string | null {
    return this.rootPath;
  }

  getEntries(): WorkspaceSearchResult[] {
    return Array.from(this.entries.values(), (entry) => entry.search).sort((left, right) =>
      left.relativePath.localeCompare(right.relativePath)
    );
  }

  getProtectionEntries(): WorkspaceProtectionEntry[] {
    return Array.from(this.entries.values(), (entry) => ({ ...entry.protection })).sort(
      (left, right) => left.relativePath.localeCompare(right.relativePath)
    );
  }

  listDirectory(dirPath?: string): WorkspaceSearchResult[] {
    if (!this.rootPath || this.state !== "ready") {
      return [];
    }

    let normalizedDirPath = dirPath;
    if (normalizedDirPath) {
      try {
        normalizedDirPath = fs.realpathSync(normalizedDirPath);
      } catch {
        // Keep original path when the directory no longer exists.
      }
    }

    const normalizedRelativePath = !normalizedDirPath
      ? "."
      : normalizedDirPath === this.rootPath
        ? "."
        : path.relative(this.rootPath, normalizedDirPath).replace(/\\/g, "/") || ".";
    const parentRelativePath =
      normalizedRelativePath === "." ? "." : normalizedRelativePath.replace(/\/+$/, "");

    const results: WorkspaceSearchResult[] = [];
    for (const relativePath of this.childrenByParent.get(parentRelativePath) ?? []) {
      const entry = this.entries.get(relativePath);
      if (entry) {
        results.push(entry.search);
      }
    }

    results.sort((left, right) => {
      if (left.isDirectory !== right.isDirectory) {
        return left.isDirectory ? -1 : 1;
      }
      return left.name.localeCompare(right.name);
    });

    return results;
  }

  handleChange(filename: string): {
    changedEntries: WorkspaceSearchResult[];
    removedEntries: WorkspaceSearchResult[];
  } {
    if (!this.rootPath || this.state !== "ready") {
      this.markStale();
      return { changedEntries: [], removedEntries: [] };
    }

    const relativePath = filename
      .replace(/\\/g, "/")
      .replace(/^\.\/+/, "")
      .replace(/^\/+/, "")
      .replace(/\/+$/, "");
    if (!relativePath) {
      this.markStale();
      return { changedEntries: [], removedEntries: [] };
    }

    const removedEntries: WorkspaceSearchResult[] = [];
    for (const candidatePath of Array.from(this.entries.keys())) {
      if (
        candidatePath === relativePath ||
        candidatePath.startsWith(`${relativePath}/`)
      ) {
        const removedEntry = this.removeEntry(candidatePath);
        if (removedEntry) {
          removedEntries.push(removedEntry);
        }
      }
    }

    const changedEntries: WorkspaceSearchResult[] = [];
    const fullPath = path.join(this.rootPath, relativePath);

    try {
      const stats = fs.statSync(fullPath);
      if (stats.isDirectory()) {
        const directoryEntry = createWorkspaceSearchResult(this.rootPath, fullPath, true);
        this.addEntry(directoryEntry);
        changedEntries.push(directoryEntry);

        for (const entry of listWorkspaceEntries(this.rootPath, fullPath)) {
          this.addEntry(entry);
          changedEntries.push(entry);
        }
      } else {
        const entry = createWorkspaceSearchResult(this.rootPath, fullPath, false);
        this.addEntry(entry);
        changedEntries.push(entry);
      }
    } catch {
      // Treat as removal only.
    }

    return { changedEntries, removedEntries };
  }
}
