import { contextBridge, ipcRenderer } from "electron";
import type { TerminalSessionRuntime } from "./core/terminal/session-runtime";
import type {
  CompiledProtectionEntry,
  ProtectionPresetId,
  ProtectionRule,
  ProtectionWorkspacePolicy,
} from "./core/policy/protection-rules";

type TerminalSessionStatePayload = {
  sessions: TerminalSessionRuntime[];
  policyRevision: number;
};

type TerminalSessionReplacement = {
  oldTerminalId: string;
  newTerminalId: string;
  displayName: string;
  layoutSlotKey?: string;
};

type TerminalSessionActionResult = {
  ok: boolean;
  replacement?: TerminalSessionReplacement;
  reason?: string;
};

type TerminalBulkRestartResult = {
  replacements: TerminalSessionReplacement[];
  skippedTerminalIds: string[];
};

type TerminalCloseFailedResult = {
  closed: boolean;
  terminalId: string;
  reason?: string;
};

contextBridge.exposeInMainWorld("electronAPI", {
  // Terminal
  terminalCreate: (opts: {
    shell?: string;
    cols?: number;
    rows?: number;
    cwd?: string;
    displayName?: string;
    layoutSlotKey?: string;
  }) => ipcRenderer.invoke("terminal:create", opts),
  terminalWrite: (id: string, data: string) =>
    ipcRenderer.send("terminal:write", id, data),
  terminalResize: (id: string, cols: number, rows: number) =>
    ipcRenderer.send("terminal:resize", id, cols, rows),
  terminalDestroy: (id: string) =>
    ipcRenderer.invoke("terminal:destroy", id),
  onTerminalSessionState: (
    callback: (payload: TerminalSessionStatePayload) => void
  ) => {
    const handler = (_event: Electron.IpcRendererEvent, payload: TerminalSessionStatePayload) =>
      callback(payload);
    ipcRenderer.on("terminal:session-state", handler);
    return () => {
      ipcRenderer.removeListener("terminal:session-state", handler);
    };
  },
  terminalRestart: (id: string): Promise<TerminalSessionActionResult> =>
    ipcRenderer.invoke("terminal:restart", id),
  terminalRestartAllStale: () =>
    ipcRenderer.invoke("terminal:restart-all-stale") as Promise<TerminalBulkRestartResult>,
  terminalRetryProtected: (id: string): Promise<TerminalSessionActionResult> =>
    ipcRenderer.invoke("terminal:retry-protected", id),
  terminalCloseFailed: (id: string): Promise<TerminalCloseFailedResult> =>
    ipcRenderer.invoke("terminal:close-failed", id),

  // Terminal data listener
  onTerminalData: (
    callback: (id: string, data: string) => void
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      id: string,
      data: string
    ) => callback(id, data);
    ipcRenderer.on("terminal:data", handler);
    return () => {
      ipcRenderer.removeListener("terminal:data", handler);
    };
  },

  // Terminal profiles
  terminalProfiles: () => ipcRenderer.invoke("terminal:profiles"),

  // Terminal exit listener
  onTerminalExit: (
    callback: (id: string, exitCode: number) => void
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      id: string,
      exitCode: number
    ) => callback(id, exitCode);
    ipcRenderer.on("terminal:exit", handler);
    return () => {
      ipcRenderer.removeListener("terminal:exit", handler);
    };
  },

  // Workspace
  openFolder: () => ipcRenderer.invoke("workspace:open"),
  workspaceSetRoot: (dirPath: string) =>
    ipcRenderer.invoke("workspace:set-root", dirPath),
  workspaceRecent: () => ipcRenderer.invoke("workspace:recent"),
  workspaceFiles: (dirPath: string) =>
    ipcRenderer.invoke("workspace:files", dirPath),
  workspaceExpand: (dirPath: string, rootPath: string) =>
    ipcRenderer.invoke("workspace:expand", dirPath, rootPath),
  workspaceSearch: (
    dirPath: string,
    options: {
      query?: string;
      extensions?: string[];
      includeDirectories?: boolean;
      limit?: number;
    }
  ) => ipcRenderer.invoke("workspace:search", dirPath, options),
  workspaceDescribe: (paths: string[]) =>
    ipcRenderer.invoke("workspace:describe", paths),
  // Policy
  policySet: (filePath: string) =>
    ipcRenderer.invoke("policy:set", filePath),
  policyRemove: (filePath: string) =>
    ipcRenderer.invoke("policy:remove", filePath),
  policyList: () => ipcRenderer.invoke("policy:list"),
  policyCheck: (filePath: string) =>
    ipcRenderer.invoke("policy:check", filePath),
  protectionListRules: () => ipcRenderer.invoke("protection:list-rules") as Promise<ProtectionRule[]>,
  protectionListCompiled: () =>
    ipcRenderer.invoke("protection:list-compiled") as Promise<CompiledProtectionEntry[]>,
  protectionApplyPreset: (presetId: ProtectionPresetId) =>
    ipcRenderer.invoke("protection:apply-preset", presetId),
  protectionAddExtensionRule: (extensions: string[]) =>
    ipcRenderer.invoke("protection:add-extension-rule", extensions),
  protectionAddDirectoryRule: (targetPath: string) =>
    ipcRenderer.invoke("protection:add-directory-rule", targetPath),
  protectionRemoveRule: (ruleId: string) =>
    ipcRenderer.invoke("protection:remove-rule", ruleId) as Promise<boolean>,
  protectionImport: (filePath: string) =>
    ipcRenderer.invoke("protection:import", filePath),
  protectionExport: () =>
    ipcRenderer.invoke("protection:export") as Promise<ProtectionWorkspacePolicy | null>,

  // Workspace change listener
  onWorkspaceChanged: (
    callback: (data: { rootPath: string; eventType: string; filename: string }) => void
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      data: { rootPath: string; eventType: string; filename: string }
    ) => callback(data);
    ipcRenderer.on("workspace:changed", handler);
    return () => {
      ipcRenderer.removeListener("workspace:changed", handler);
    };
  },

  // Policy change listener
  onPolicyChanged: (callback: () => void) => {
    const handler = () => callback();
    ipcRenderer.on("policy:changed", handler);
    return () => {
      ipcRenderer.removeListener("policy:changed", handler);
    };
  },

  // App menu events
  onToggleSettings: (callback: () => void) => {
    const handler = () => callback();
    ipcRenderer.on("app:toggle-settings", handler);
    return () => {
      ipcRenderer.removeListener("app:toggle-settings", handler);
    };
  },

  // Config
  configGet: () => ipcRenderer.invoke("config:get"),
  configSet: (partial: Record<string, unknown>) =>
    ipcRenderer.invoke("config:set", partial),

  // Platform info
  platform: process.platform,
  appName: (() => { try { return require("../../package.json").productName; } catch { return "FortShell"; } })(),
});
