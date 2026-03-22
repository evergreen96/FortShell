import fs from "fs";
import path from "path";
import { app } from "electron";

const MAX_RECENT = 10;

function getConfigPath(): string {
  return path.join(app.getPath("userData"), "recent-workspaces.json");
}

export function getRecentWorkspaces(): string[] {
  try {
    const raw = fs.readFileSync(getConfigPath(), "utf-8");
    const data = JSON.parse(raw);
    if (Array.isArray(data)) return data;
  } catch {}
  return [];
}

export function addRecentWorkspace(dirPath: string): void {
  const recent = getRecentWorkspaces().filter((p) => p !== dirPath);
  recent.unshift(dirPath);
  if (recent.length > MAX_RECENT) recent.length = MAX_RECENT;

  const configDir = path.dirname(getConfigPath());
  if (!fs.existsSync(configDir)) {
    fs.mkdirSync(configDir, { recursive: true });
  }
  fs.writeFileSync(getConfigPath(), JSON.stringify(recent, null, 2), "utf-8");
}
