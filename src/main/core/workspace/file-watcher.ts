import fs from "fs";
import path from "path";
import { BrowserWindow } from "electron";
import { DEFAULT_IGNORE } from "./constants";

export class FileWatcher {
  private watchers = new Map<string, fs.FSWatcher>();
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;

  watch(dirPath: string): void {
    this.close();

    try {
      const watcher = fs.watch(
        dirPath,
        { recursive: true },
        (eventType, filename) => {
          if (!filename) return;
          // Skip ignored directories
          const parts = filename.split(path.sep);
          if (parts.some((p) => DEFAULT_IGNORE.has(p))) return;
          if (parts[0]?.startsWith(".")) return;

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
          if (DEFAULT_IGNORE.has(filename)) return;
          this.notifyChange(dirPath, eventType, filename);
        });
        this.watchers.set(dirPath, watcher);
      } catch {
        // Give up silently
      }
    }
  }

  private notifyChange(
    rootPath: string,
    eventType: string,
    filename: string
  ): void {
    // Debounce: batch rapid changes
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = setTimeout(() => {
      const windows = BrowserWindow.getAllWindows();
      for (const win of windows) {
        if (!win.isDestroyed()) {
          win.webContents.send("workspace:changed", {
            rootPath,
            eventType,
            filename,
          });
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
