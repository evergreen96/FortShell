import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  // Terminal
  terminalCreate: (opts: {
    shell?: string;
    cols?: number;
    rows?: number;
    cwd?: string;
  }) => ipcRenderer.invoke("terminal:create", opts),
  terminalWrite: (id: string, data: string) =>
    ipcRenderer.send("terminal:write", id, data),
  terminalResize: (id: string, cols: number, rows: number) =>
    ipcRenderer.send("terminal:resize", id, cols, rows),
  terminalDestroy: (id: string) =>
    ipcRenderer.invoke("terminal:destroy", id),

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
  // Policy
  policySet: (filePath: string) =>
    ipcRenderer.invoke("policy:set", filePath),
  policyRemove: (filePath: string) =>
    ipcRenderer.invoke("policy:remove", filePath),
  policyList: () => ipcRenderer.invoke("policy:list"),
  policyCheck: (filePath: string) =>
    ipcRenderer.invoke("policy:check", filePath),

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
