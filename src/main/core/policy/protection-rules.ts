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

export type WorkspaceProtectionEntry = {
  path: string;
  relativePath: string;
  name: string;
  ext: string;
  isDirectory: boolean;
};

export type CompiledProtectionEntry = WorkspaceProtectionEntry & {
  type: "file" | "directory";
  status: "shielded";
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

export const BUILT_IN_PRESETS = [
  {
    id: "env-files",
    label: "Env Files",
    description: "Protect common environment files and local overrides.",
    rules: [
      { kind: "path", value: ".env" },
      { kind: "path", value: ".env.local" },
      { kind: "path", value: ".env.production" },
      { kind: "path", value: ".env.development" },
      { kind: "path", value: ".env.test" },
    ],
  },
  {
    id: "secrets",
    label: "Secrets",
    description: "Protect common secret files and secret-bearing directories.",
    rules: [
      { kind: "directory", value: "secrets" },
      { kind: "path", value: ".secrets" },
      { kind: "path", value: ".envrc" },
      { kind: "path", value: ".npmrc" },
      { kind: "path", value: ".pypirc" },
    ],
  },
  {
    id: "private-configs",
    label: "Private Configs",
    description: "Protect local config files that often contain credentials.",
    rules: [
      { kind: "path", value: ".aws/config" },
      { kind: "path", value: ".aws/credentials" },
      { kind: "path", value: ".git-credentials" },
      { kind: "path", value: ".netrc" },
    ],
  },
  {
    id: "certificates",
    label: "Certificates",
    description: "Protect certificate and certificate bundle files.",
    rules: [{ kind: "extension", value: [".crt", ".cer", ".pem", ".der"] }],
  },
  {
    id: "keys",
    label: "Keys",
    description: "Protect common private key and key bundle files.",
    rules: [{ kind: "extension", value: [".key", ".pem", ".p12", ".pfx"] }],
  },
  {
    id: "database-configs",
    label: "Database Configs",
    description: "Protect database configuration and local database files.",
    rules: [
      { kind: "path", value: "database.yml" },
      { kind: "path", value: "database.yaml" },
      { kind: "path", value: "db/config.yml" },
      { kind: "path", value: "db/config.yaml" },
      { kind: "extension", value: [".db", ".sqlite", ".sqlite3"] },
    ],
  },
] satisfies readonly ProtectionPreset[];

export function getProtectionPresetById(
  presetId: ProtectionPresetId,
  presetCatalog: readonly ProtectionPreset[]
): ProtectionPreset | undefined {
  return presetCatalog.find((preset) => preset.id === presetId);
}

export function getProtectionRuleSourceLabel(
  rule: ProtectionRule,
  presetCatalog: readonly ProtectionPreset[]
): string {
  if (rule.kind === "preset") {
    const preset = getProtectionPresetById(rule.presetId, presetCatalog);
    return preset ? `${preset.label} Preset` : "Preset Rule";
  }

  if (rule.kind === "extension") {
    return "Extension Rule";
  }

  if (rule.kind === "directory") {
    return "Directory Rule";
  }

  return "Manual Rule";
}
