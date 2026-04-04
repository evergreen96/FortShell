import fs from "fs";
import path from "path";
import {
  createWorkspaceSearchResult,
  listWorkspaceEntries,
  searchWorkspaceEntries,
  type WorkspaceSearchOptions,
  type WorkspaceSearchResult,
} from "./file-indexer";

export type WorkspaceIndexState = "cold" | "warming" | "ready" | "stale";

export class WorkspaceIndexService {
  private rootPath: string | null = null;
  private entries = new Map<string, WorkspaceSearchResult>();
  private state: WorkspaceIndexState = "cold";
  private warmPromise: Promise<void> | null = null;

  async setRoot(rootPath: string): Promise<void> {
    try {
      this.rootPath = fs.realpathSync(rootPath);
    } catch {
      this.rootPath = rootPath;
    }
    this.entries.clear();
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
      this.entries = new Map(
        listWorkspaceEntries(this.rootPath!).map((entry) => [entry.relativePath, entry])
      );
      this.state = "ready";
      this.warmPromise = null;
    })();

    return this.warmPromise;
  }

  search(options: WorkspaceSearchOptions = {}): WorkspaceSearchResult[] {
    if (this.state !== "ready") {
      return [];
    }

    return searchWorkspaceEntries(this.getEntries(), options);
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
    return Array.from(this.entries.values()).sort((left, right) =>
      left.relativePath.localeCompare(right.relativePath)
    );
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
    for (const [candidatePath, entry] of this.entries.entries()) {
      if (
        candidatePath === relativePath ||
        candidatePath.startsWith(`${relativePath}/`)
      ) {
        removedEntries.push(entry);
        this.entries.delete(candidatePath);
      }
    }

    const changedEntries: WorkspaceSearchResult[] = [];
    const fullPath = path.join(this.rootPath, relativePath);

    try {
      const stats = fs.statSync(fullPath);
      if (stats.isDirectory()) {
        const directoryEntry = createWorkspaceSearchResult(this.rootPath, fullPath, true);
        this.entries.set(directoryEntry.relativePath, directoryEntry);
        changedEntries.push(directoryEntry);

        for (const entry of listWorkspaceEntries(this.rootPath, fullPath)) {
          this.entries.set(entry.relativePath, entry);
          changedEntries.push(entry);
        }
      } else {
        const entry = createWorkspaceSearchResult(this.rootPath, fullPath, false);
        this.entries.set(entry.relativePath, entry);
        changedEntries.push(entry);
      }
    } catch {
      // Treat as removal only.
    }

    return { changedEntries, removedEntries };
  }
}
