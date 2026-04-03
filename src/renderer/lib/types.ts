export type ShellProfile = {
  id: string;
  label: string;
  command: string;
  args: string[];
  isDefault: boolean;
};

export type WorkspaceSearchResult = {
  name: string;
  path: string;
  relativePath: string;
  isDirectory: boolean;
};

export type TerminalTrustState =
  | "protected"
  | "unprotected"
  | "stale-policy"
  | "fallback"
  | "launch-failed"
  | "exited";

export type TerminalSessionMeta = {
  terminalId: string;
  displayName: string;
  shell: string;
  trustState: TerminalTrustState;
  launchMode: "sandboxed" | "plain-shell-fallback" | "launch-failed";
  policyRevision: number;
  startedAt: string;
  layoutSlotKey?: string;
  staleReason?: "policy-changed";
  launchFailureReason?: string;
  launchFailureDetail?: string;
  launchRetryable?: boolean;
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
  onTerminalSessionState: (
    callback: (payload: {
      sessions: TerminalSessionMeta[];
      policyRevision: number;
    }) => void
  ) => () => void;
  onTerminalData: (
    callback: (id: string, data: string) => void
  ) => () => void;
  onTerminalExit: (
    callback: (id: string, exitCode: number) => void
  ) => () => void;
  terminalRestart: (id: string) => Promise<any>;
  terminalRestartAllStale: () => Promise<any>;
  terminalRetryProtected: (id: string) => Promise<any>;
  terminalCloseFailed: (id: string) => Promise<any>;
  openFolder: () => Promise<string | null>;
  workspaceSetRoot: (dirPath: string) => Promise<string>;
  workspaceRecent: () => Promise<string[]>;
  onWorkspaceChanged: (
    callback: (data: { rootPath: string; eventType: string; filename: string }) => void
  ) => () => void;
  workspaceFiles: (dirPath: string) => Promise<any[]>;
  workspaceExpand: (dirPath: string, rootPath: string) => Promise<any[]>;
  workspaceSearch: (
    dirPath: string,
    options: {
      query?: string;
      extensions?: string[];
      includeDirectories?: boolean;
      limit?: number;
    }
  ) => Promise<WorkspaceSearchResult[]>;
  workspaceDescribe: (
    paths: string[]
  ) => Promise<Array<{ path: string; isDirectory: boolean }>>;
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
