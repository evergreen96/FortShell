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

export type ProtectionCompiledEntryType = "file" | "folder";

export type ProtectionCompiledEntryStatus = "shielded";

export type ProtectionRuleKind = "path" | "directory" | "extension" | "preset";

export type ProtectionRuleSource = "manual" | "directory" | "extension" | "preset" | "import";

export type ProtectionPresetId =
  | "env-files"
  | "secrets"
  | "private-configs"
  | "certificates"
  | "keys"
  | "database-configs";

export type ProtectionPresetRule =
  | { kind: "path"; value: string }
  | { kind: "directory"; value: string }
  | { kind: "extension"; value: string | string[] };

export type ProtectionPreset = {
  id: ProtectionPresetId;
  label: string;
  description: string;
  rules: readonly ProtectionPresetRule[];
};

export type ProtectionRuleBase = {
  id: string;
  source: ProtectionRuleSource;
  createdAt?: string;
  updatedAt?: string;
};

export type ProtectionPathRule = ProtectionRuleBase & {
  kind: "path";
  targetPath: string;
};

export type ProtectionDirectoryRule = ProtectionRuleBase & {
  kind: "directory";
  targetPath: string;
};

export type ProtectionExtensionRule = ProtectionRuleBase & {
  kind: "extension";
  extensions: string[];
};

export type ProtectionPresetRuleRef = ProtectionRuleBase & {
  kind: "preset";
  presetId: ProtectionPresetId;
};

export type ProtectionRule =
  | ProtectionPathRule
  | ProtectionDirectoryRule
  | ProtectionExtensionRule
  | ProtectionPresetRuleRef;

export type CompiledProtectionEntry = WorkspaceSearchResult & {
  type: ProtectionCompiledEntryType;
  status: ProtectionCompiledEntryStatus;
  canRemoveDirectly: boolean;
  sourceRuleId: string;
  sourceKind: ProtectionRuleKind;
  sourceLabel: string;
};

export type ProtectionWorkspacePolicy = {
  version: 3;
  workspaceRoot: string;
  rules: readonly ProtectionRule[];
  updatedAt: string;
};

export type ProtectionMutationResult = {
  changed: boolean;
  reason?: string;
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

export type TerminalSessionReplacement = {
  oldTerminalId: string;
  newTerminalId: string;
  displayName: string;
  layoutSlotKey?: string;
};

export type TerminalSessionActionResult = {
  ok: boolean;
  replacement?: TerminalSessionReplacement;
  reason?: string;
};

export type TerminalBulkRestartResult = {
  replacements: TerminalSessionReplacement[];
  skippedTerminalIds: string[];
};

export type TerminalCloseFailedResult = {
  closed: boolean;
  terminalId: string;
  reason?: string;
};

export type ElectronAPI = {
  terminalCreate: (opts: {
    shell?: string;
    cols?: number;
    rows?: number;
    cwd?: string;
    displayName?: string;
    layoutSlotKey?: string;
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
  terminalRestart: (id: string) => Promise<TerminalSessionActionResult>;
  terminalRestartAllStale: () => Promise<TerminalBulkRestartResult>;
  terminalRetryProtected: (id: string) => Promise<TerminalSessionActionResult>;
  terminalCloseFailed: (id: string) => Promise<TerminalCloseFailedResult>;
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
  protectionListRules: () => Promise<ProtectionRule[]>;
  protectionListCompiled: () => Promise<CompiledProtectionEntry[]>;
  protectionApplyPreset: (presetId: ProtectionPresetId) => Promise<ProtectionMutationResult>;
  protectionAddExtensionRule: (extensions: string[]) => Promise<ProtectionMutationResult>;
  protectionAddDirectoryRule: (targetPath: string) => Promise<ProtectionMutationResult>;
  protectionRemoveRule: (ruleId: string) => Promise<boolean>;
  protectionImport: (filePath: string) => Promise<ProtectionMutationResult>;
  protectionExport: () => Promise<ProtectionWorkspacePolicy | null>;
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
