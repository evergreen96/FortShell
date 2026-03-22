import fs from "fs";
import path from "path";
import { app } from "electron";

export type CustomProfile = {
  label: string;
  command: string;
};

export type AppConfig = {
  defaultShell: string | null;
  fontSize: number;
  theme: "dark";
  sidebarWidth: number;
  defaultLayout: "horizontal" | "vertical" | "grid";
  customProfiles: CustomProfile[];
};

const DEFAULTS: AppConfig = {
  defaultShell: null,
  fontSize: 14,
  theme: "dark",
  sidebarWidth: 250,
  defaultLayout: "horizontal",
  customProfiles: [],
};

function getConfigPath(): string {
  return path.join(app.getPath("userData"), "config.json");
}

export function loadConfig(): AppConfig {
  try {
    const raw = fs.readFileSync(getConfigPath(), "utf-8");
    const data = JSON.parse(raw);
    return { ...DEFAULTS, ...data };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveConfig(config: Partial<AppConfig>): AppConfig {
  const current = loadConfig();
  const merged = { ...current, ...config };
  const configDir = path.dirname(getConfigPath());
  if (!fs.existsSync(configDir)) {
    fs.mkdirSync(configDir, { recursive: true });
  }
  fs.writeFileSync(getConfigPath(), JSON.stringify(merged, null, 2), "utf-8");
  return merged;
}
