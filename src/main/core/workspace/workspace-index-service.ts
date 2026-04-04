import fs from "fs";
import {
  listWorkspaceEntries,
  searchWorkspaceEntries,
  type WorkspaceSearchOptions,
  type WorkspaceSearchResult,
} from "./file-indexer";

export type WorkspaceIndexState = "cold" | "warming" | "ready" | "stale";

export class WorkspaceIndexService {
  private rootPath: string | null = null;
  private entries: WorkspaceSearchResult[] = [];
  private state: WorkspaceIndexState = "cold";
  private warmPromise: Promise<void> | null = null;

  async setRoot(rootPath: string): Promise<void> {
    try {
      this.rootPath = fs.realpathSync(rootPath);
    } catch {
      this.rootPath = rootPath;
    }
    this.entries = [];
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
      this.entries = listWorkspaceEntries(this.rootPath!);
      this.state = "ready";
      this.warmPromise = null;
    })();

    return this.warmPromise;
  }

  search(options: WorkspaceSearchOptions = {}): WorkspaceSearchResult[] {
    if (this.state !== "ready") {
      return [];
    }

    return searchWorkspaceEntries(this.entries, options);
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
}
