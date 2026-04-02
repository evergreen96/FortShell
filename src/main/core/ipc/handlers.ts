import fs from "fs";
import { ipcMain, dialog, BrowserWindow } from "electron";
import { PtyManager } from "../terminal/pty-manager";
import { detectProfiles } from "../terminal/profiles";
import {
  indexDirectory,
  expandDirectory,
  searchWorkspace,
  type WorkspaceSearchOptions,
} from "../workspace/file-indexer";
import { FileWatcher } from "../workspace/file-watcher";
import { PolicyEngine } from "../policy/policy-engine";
import { getRecentWorkspaces, addRecentWorkspace } from "../config/recent-workspaces";
import { loadConfig, saveConfig, type AppConfig } from "../config/app-config";

function notifyPolicyChanged(): void {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) {
      win.webContents.send("policy:changed");
    }
  }
}

function safeHandle(
  channel: string,
  handler: (event: Electron.IpcMainInvokeEvent, ...args: any[]) => any
): void {
  ipcMain.handle(channel, async (event, ...args) => {
    try {
      return await handler(event, ...args);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[ipc] ${channel} error:`, message);
      throw new Error(message);
    }
  });
}

export function registerIpcHandlers(
  ptyManager: PtyManager,
  policyEngine: PolicyEngine,
  fileWatcher: FileWatcher
): void {
  // Terminal
  safeHandle("terminal:create", (event, opts) => {
    const webContents = event.sender;
    return ptyManager.create({ ...opts, webContents });
  });

  ipcMain.on("terminal:write", (_event, id: string, data: string) => {
    try { ptyManager.write(id, data); } catch {}
  });

  ipcMain.on(
    "terminal:resize",
    (_event, id: string, cols: number, rows: number) => {
      try { ptyManager.resize(id, cols, rows); } catch {}
    }
  );

  safeHandle("terminal:destroy", (_event, id: string) => {
    return ptyManager.destroy(id);
  });

  safeHandle("terminal:profiles", () => {
    const config = loadConfig();
    return detectProfiles(config.customProfiles);
  });

  // Workspace
  safeHandle("workspace:open", async () => {
    const win = BrowserWindow.getFocusedWindow();
    if (!win) return null;
    const result = await dialog.showOpenDialog(win, {
      properties: ["openDirectory"],
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    const dirPath = result.filePaths[0];
    const resolvedPath = fs.realpathSync(dirPath);
    await policyEngine.setProjectRoot(resolvedPath);
    fileWatcher.watch(resolvedPath);
    addRecentWorkspace(resolvedPath);
    return resolvedPath;
  });

  safeHandle("workspace:set-root", async (_event, dirPath: string) => {
    const resolvedPath = fs.realpathSync(dirPath);
    await policyEngine.setProjectRoot(resolvedPath);
    fileWatcher.watch(resolvedPath);
    addRecentWorkspace(resolvedPath);
    return resolvedPath;
  });

  safeHandle("workspace:recent", () => {
    return getRecentWorkspaces();
  });

  safeHandle("workspace:files", (_event, dirPath: string) => {
    return indexDirectory(dirPath);
  });

  safeHandle("workspace:expand", (_event, dirPath: string, rootPath: string) => {
    return expandDirectory(dirPath, rootPath);
  });

  safeHandle("workspace:search", (_event, dirPath: string, options: WorkspaceSearchOptions) => {
    return searchWorkspace(dirPath, options);
  });

  safeHandle("workspace:describe", (_event, paths: string[]) => {
    return paths.map((targetPath) => {
      let isDirectory = false;
      try {
        isDirectory = fs.statSync(targetPath).isDirectory();
      } catch {}
      return { path: targetPath, isDirectory };
    });
  });

  // Policy
  safeHandle("policy:set", async (_event, filePath: string) => {
    const result = await policyEngine.protect(filePath);
    if (result) notifyPolicyChanged();
    return result;
  });

  safeHandle("policy:remove", async (_event, filePath: string) => {
    const result = await policyEngine.unprotect(filePath);
    if (result) notifyPolicyChanged();
    return result;
  });

  safeHandle("policy:list", () => {
    return policyEngine.list();
  });

  safeHandle("policy:check", (_event, filePath: string) => {
    return policyEngine.isProtected(filePath);
  });

  // Config
  safeHandle("config:get", () => {
    return loadConfig();
  });

  safeHandle("config:set", (_event, partial: Record<string, unknown>) => {
    return saveConfig(partial as any);
  });
}
