import fs from "fs";
import path from "path";
import { BrowserWindow } from "electron";
import { DEFAULT_IGNORE } from "./constants";

type WorkspaceChangePayload = {
  rootPath: string;
  eventType: string;
  filename: string;
};

export function shouldIgnoreWorkspaceChange(filename: string): boolean {
  const parts = filename.split(path.sep);
  return parts.some((part) => DEFAULT_IGNORE.has(part));
}

export class FileWatcher {
  private watchers = new Map<string, fs.FSWatcher>();
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners = new Set<(payload: WorkspaceChangePayload) => void | Promise<void>>();

  watch(dirPath: string): void {
    this.close();

    try {
      const watcher = fs.watch(
        dirPath,
        { recursive: true },
        (eventType, filename) => {
          if (!filename) return;
          if (shouldIgnoreWorkspaceChange(filename)) return;

          this.notifyChange(dirPath, eventType, filename);
        }
      );

      this.watchers.set(dirPath, watcher);
    } catch {
      // fs.watch may not support recursive on all platforms
      // Fallback: watch just the root directory
      try {
        const watcher = fs.watch(dirPath, (eventType, filename) => {
          if (!filename) return;
          if (shouldIgnoreWorkspaceChange(filename)) return;
          this.notifyChange(dirPath, eventType, filename);
        });
        this.watchers.set(dirPath, watcher);
      } catch {
        // Give up silently
      }
    }
  }

  onWorkspaceChanged(
    listener: (payload: WorkspaceChangePayload) => void | Promise<void>
  ): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private notifyChange(
    rootPath: string,
    eventType: string,
    filename: string
  ): void {
    // Debounce: batch rapid changes
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = setTimeout(() => {
      const payload = { rootPath, eventType, filename };
      const windows = BrowserWindow.getAllWindows();
      for (const win of windows) {
        if (!win.isDestroyed()) {
          win.webContents.send("workspace:changed", payload);
        }
      }

      for (const listener of this.listeners) {
        try {
          void Promise.resolve(listener(payload)).catch((err) => {
            console.warn(`[workspace] Workspace change listener failed:`, err);
          });
        } catch (err) {
          console.warn(`[workspace] Workspace change listener failed:`, err);
        }
      }
    }, 300);
  }

  close(): void {
    for (const [, watcher] of this.watchers) {
      watcher.close();
    }
    this.watchers.clear();
    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }
  }
}
