export type ShellProfile = {
  id: string;
  label: string;
  command: string;
  args: string[];
  isDefault: boolean;
};

export type ElectronAPI = {
  terminalCreate: (opts: {
    shell?: string;
    cols?: number;
    rows?: number;
    cwd?: string;
  }) => Promise<{ id: string; name: string }>;
  terminalWrite: (id: string, data: string) => void;
  terminalResize: (id: string, cols: number, rows: number) => void;
  terminalDestroy: (id: string) => Promise<boolean>;
  terminalProfiles: () => Promise<ShellProfile[]>;
  onTerminalData: (
    callback: (id: string, data: string) => void
  ) => () => void;
  onTerminalExit: (
    callback: (id: string, exitCode: number) => void
  ) => () => void;
  openFolder: () => Promise<string | null>;
  workspaceSetRoot: (dirPath: string) => Promise<string>;
  workspaceRecent: () => Promise<string[]>;
  onWorkspaceChanged: (
    callback: (data: { rootPath: string; eventType: string; filename: string }) => void
  ) => () => void;
  workspaceFiles: (dirPath: string) => Promise<any[]>;
  workspaceExpand: (dirPath: string, rootPath: string) => Promise<any[]>;
  policySet: (filePath: string) => Promise<boolean>;
  policyRemove: (filePath: string) => Promise<boolean>;
  policyList: () => Promise<string[]>;
  onPolicyChanged: (callback: () => void) => () => void;
  onToggleSettings: (callback: () => void) => () => void;
  configGet: () => Promise<Record<string, unknown>>;
  configSet: (partial: Record<string, unknown>) => Promise<Record<string, unknown>>;
  platform: string;
  appName: string;
};

declare global {
  interface Window {
    electronAPI: ElectronAPI;
  }
}
