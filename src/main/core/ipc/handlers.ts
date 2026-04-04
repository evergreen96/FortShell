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
import { WorkspaceIndexService } from "../workspace/workspace-index-service";
import { PolicyEngine } from "../policy/policy-engine";
import {
  BUILT_IN_PRESETS,
  type ProtectionPresetId,
} from "../policy/protection-rules";
import { getRecentWorkspaces, addRecentWorkspace } from "../config/recent-workspaces";
import { parseImportedWorkspacePolicy } from "../config/policy-store";
import { loadConfig, saveConfig, type AppConfig } from "../config/app-config";

function notifyPolicyChanged(workspacePath: string | null): void {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) {
      win.webContents.send("policy:changed", { workspacePath });
    }
  }
}

function notifyPolicyMutation(
  ptyManager: PtyManager,
  policyEngine: PolicyEngine,
  previousRevision: number
): void {
  notifyPolicyChanged(policyEngine.getProjectRoot());
  if (policyEngine.getPolicyRevision() !== previousRevision) {
    ptyManager.markPolicyRevisionChanged(policyEngine.getPolicyRevision());
    notifySessionStateChanged(ptyManager, policyEngine);
  }
}

function notifyPolicyMutationIfChanged(
  ptyManager: PtyManager,
  policyEngine: PolicyEngine,
  previousRevision: number
): void {
  if (policyEngine.getPolicyRevision() !== previousRevision) {
    notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
  }
}

function notifySessionStateChanged(
  ptyManager: PtyManager,
  policyEngine: PolicyEngine
): void {
  const payload = {
    sessions: ptyManager.getSessions(),
    policyRevision: policyEngine.getPolicyRevision(),
  };

  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) {
      win.webContents.send("terminal:session-state", payload);
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
  fileWatcher: FileWatcher,
  workspaceIndexService: WorkspaceIndexService
): void {
  ptyManager.setPolicyRevision(policyEngine.getPolicyRevision());
  ptyManager.onSessionStateChanged(() => {
    notifySessionStateChanged(ptyManager, policyEngine);
  });
  fileWatcher.onWorkspaceChanged(async (payload) => {
    if (workspaceIndexService.getRootPath() === payload.rootPath) {
      workspaceIndexService.markStale();
      void workspaceIndexService.warm();
    }

    const previousRevision = policyEngine.getPolicyRevision();
    try {
      const changed = await policyEngine.recomputeDynamicRules();
      if (changed) {
        notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
      }
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
  });

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

  safeHandle("terminal:restart", (_event, id: string) => {
    return ptyManager.restart(id);
  });

  safeHandle("terminal:restart-all-stale", () => {
    return ptyManager.restartAllStale();
  });

  safeHandle("terminal:retry-protected", (_event, id: string) => {
    return ptyManager.retryProtected(id);
  });

  safeHandle("terminal:close-failed", (_event, id: string) => {
    return ptyManager.closeFailed(id);
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
    const previousRevision = policyEngine.getPolicyRevision();
    try {
      await policyEngine.setProjectRoot(resolvedPath);
      notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
    await workspaceIndexService.setRoot(resolvedPath);
    void workspaceIndexService.warm();
    fileWatcher.watch(resolvedPath);
    addRecentWorkspace(resolvedPath);
    return resolvedPath;
  });

  safeHandle("workspace:set-root", async (_event, dirPath: string) => {
    const resolvedPath = fs.realpathSync(dirPath);
    const previousRevision = policyEngine.getPolicyRevision();
    try {
      await policyEngine.setProjectRoot(resolvedPath);
      notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
    await workspaceIndexService.setRoot(resolvedPath);
    void workspaceIndexService.warm();
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
    let resolvedPath = dirPath;
    try {
      resolvedPath = fs.realpathSync(dirPath);
    } catch {}

    if (workspaceIndexService.getRootPath() === resolvedPath) {
      const state = workspaceIndexService.getState();
      if (state === "ready") {
        return workspaceIndexService.search(options);
      }

      void workspaceIndexService.warm();
    }

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
    const previousRevision = policyEngine.getPolicyRevision();
    try {
      const result = await policyEngine.protect(filePath);
      if (result) {
        notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
      }
      return result;
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
  });

  safeHandle("policy:remove", async (_event, filePath: string) => {
    const previousRevision = policyEngine.getPolicyRevision();
    try {
      const result = await policyEngine.unprotect(filePath);
      if (result) {
        notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
      }
      return result;
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
  });

  safeHandle("policy:list", () => {
    return policyEngine.list();
  });

  safeHandle("policy:check", (_event, filePath: string) => {
    return policyEngine.isProtected(filePath);
  });

  // Protection rules
  safeHandle("protection:list-presets", () => {
    return BUILT_IN_PRESETS;
  });

  safeHandle("protection:list-rules", () => {
    return policyEngine.listRules();
  });

  safeHandle("protection:list-compiled", () => {
    return policyEngine.listCompiledEntries();
  });

  safeHandle("protection:apply-preset", async (_event, presetId: string) => {
    const previousRevision = policyEngine.getPolicyRevision();
    try {
      const result = await policyEngine.applyPreset(presetId as ProtectionPresetId);
      if (result.changed) {
        notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
      }
      return result;
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
  });

  safeHandle(
    "protection:add-extension-rule",
    async (_event, extensions: string[]) => {
      const previousRevision = policyEngine.getPolicyRevision();
      try {
        const result = await policyEngine.addExtensionRule(extensions);
        if (result.changed) {
          notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
        }
        return result;
      } catch (err) {
        notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
        throw err;
      }
    }
  );

  safeHandle("protection:add-directory-rule", async (_event, targetPath: string) => {
    const previousRevision = policyEngine.getPolicyRevision();
    try {
      const result = await policyEngine.addDirectoryRule(targetPath);
      if (result.changed) {
        notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
      }
      return result;
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
  });

  safeHandle("protection:remove-rule", async (_event, ruleId: string) => {
    const previousRevision = policyEngine.getPolicyRevision();
    try {
      const result = await policyEngine.removeRule(ruleId);
      if (result) {
        notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
      }
      return result;
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
  });

  safeHandle("protection:import", async (_event, filePath: string) => {
    const raw = fs.readFileSync(filePath, "utf-8");
    const parsed = parseImportedWorkspacePolicy(raw, filePath);
    if (!parsed) {
      throw new Error("Invalid protection policy file");
    }

    const previousRevision = policyEngine.getPolicyRevision();
    try {
      const result = await policyEngine.importWorkspacePolicy(parsed);
      if (result.changed) {
        notifyPolicyMutation(ptyManager, policyEngine, previousRevision);
      }
      return result;
    } catch (err) {
      notifyPolicyMutationIfChanged(ptyManager, policyEngine, previousRevision);
      throw err;
    }
  });

  safeHandle("protection:export", () => {
    return policyEngine.exportWorkspacePolicy();
  });

  // Config
  safeHandle("config:get", () => {
    return loadConfig();
  });

  safeHandle("config:set", (_event, partial: Record<string, unknown>) => {
    return saveConfig(partial as any);
  });
}
