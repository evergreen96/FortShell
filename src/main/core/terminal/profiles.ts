import { existsSync } from "fs";
import { execSync } from "child_process";

export type ShellProfile = {
  id: string;
  label: string;
  command: string;
  args: string[];
  isDefault: boolean;
};

function which(cmd: string): boolean {
  try {
    if (process.platform === "win32") {
      execSync(`where ${cmd}`, { stdio: "ignore" });
    } else {
      execSync(`which ${cmd}`, { stdio: "ignore" });
    }
    return true;
  } catch {
    return false;
  }
}

function getWindowsProfiles(): ShellProfile[] {
  const profiles: ShellProfile[] = [];

  // PowerShell 7+
  if (which("pwsh")) {
    profiles.push({
      id: "pwsh",
      label: "PowerShell 7",
      command: "pwsh.exe",
      args: ["-NoLogo"],
      isDefault: false,
    });
  }

  // Windows PowerShell 5
  profiles.push({
    id: "powershell",
    label: "Windows PowerShell",
    command: "powershell.exe",
    args: ["-NoLogo"],
    isDefault: true,
  });

  // Command Prompt
  profiles.push({
    id: "cmd",
    label: "Command Prompt",
    command: "cmd.exe",
    args: [],
    isDefault: false,
  });

  // Git Bash
  const gitBashPaths = [
    "C:\\Program Files\\Git\\bin\\bash.exe",
    "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
  ];
  for (const p of gitBashPaths) {
    if (existsSync(p)) {
      profiles.push({
        id: "git-bash",
        label: "Git Bash",
        command: p,
        args: ["--login"],
        isDefault: false,
      });
      break;
    }
  }

  // WSL
  if (which("wsl")) {
    profiles.push({
      id: "wsl",
      label: "WSL",
      command: "wsl.exe",
      args: [],
      isDefault: false,
    });
  }

  return profiles;
}

function getUnixProfiles(): ShellProfile[] {
  const profiles: ShellProfile[] = [];
  const defaultShell = process.env.SHELL || "/bin/bash";

  if (which("zsh")) {
    profiles.push({
      id: "zsh",
      label: "zsh",
      command: "zsh",
      args: [],
      isDefault: defaultShell.endsWith("zsh"),
    });
  }

  if (which("bash")) {
    profiles.push({
      id: "bash",
      label: "bash",
      command: "bash",
      args: [],
      isDefault: defaultShell.endsWith("bash"),
    });
  }

  if (which("fish")) {
    profiles.push({
      id: "fish",
      label: "fish",
      command: "fish",
      args: [],
      isDefault: defaultShell.endsWith("fish"),
    });
  }

  // Ensure at least one is default
  if (profiles.length > 0 && !profiles.some((p) => p.isDefault)) {
    profiles[0].isDefault = true;
  }

  return profiles;
}

export function detectProfiles(customProfiles?: { label: string; command: string }[]): ShellProfile[] {
  const profiles = process.platform === "win32"
    ? getWindowsProfiles()
    : getUnixProfiles();

  if (customProfiles) {
    for (const cp of customProfiles) {
      if (!cp.command) continue;
      // Accept absolute paths or commands found in PATH
      if (cp.command.startsWith("/") ? existsSync(cp.command) : which(cp.command)) {
        profiles.push({
          id: `custom-${cp.label.toLowerCase().replace(/\s+/g, "-")}`,
          label: cp.label,
          command: cp.command,
          args: [],
          isDefault: false,
        });
      }
    }
  }

  return profiles;
}

export function getDefaultProfile(profiles: ShellProfile[]): ShellProfile | undefined {
  return profiles.find((p) => p.isDefault) || profiles[0];
}
