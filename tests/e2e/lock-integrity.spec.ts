import { test, expect, _electron as electron, ElectronApplication, Page } from "@playwright/test";
import fs from "fs";
import os from "os";
import path from "path";

type LaunchedApp = {
  app: ElectronApplication;
  page: Page;
  workspaceDir: string;
  userDataDir: string;
};

type SessionRuntime = {
  terminalId: string;
  trustState: string;
};

type TerminalCommandResult = {
  output: string;
  exitCode: number;
};

function shellQuote(input: string): string {
  return `'${input.replace(/'/g, `'\\''`)}'`;
}

function escapeRegExp(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function installRendererHarness(page: Page): Promise<void> {
  await page.evaluate(() => {
    const globalState = window as typeof window & {
      __lockHarness?: {
        installed: boolean;
        buffers: Record<string, string>;
        sessionPayload: { sessions: SessionRuntime[]; policyRevision: number } | null;
      };
    };

    if (globalState.__lockHarness?.installed) {
      return;
    }

    globalState.__lockHarness = {
      installed: true,
      buffers: {},
      sessionPayload: null,
    };

    window.electronAPI.onTerminalData((id, data) => {
      if (!globalState.__lockHarness) {
        return;
      }
      globalState.__lockHarness.buffers[id] =
        (globalState.__lockHarness.buffers[id] ?? "") + data;
    });

    window.electronAPI.onTerminalSessionState((payload) => {
      if (!globalState.__lockHarness) {
        return;
      }
      globalState.__lockHarness.sessionPayload = payload;
    });
  });
}

async function launchAppForWorkspace(workspaceDir: string): Promise<LaunchedApp> {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-e2e-user-data-"));
  const recentWorkspacesPath = path.join(userDataDir, "recent-workspaces.json");
  fs.mkdirSync(userDataDir, { recursive: true });
  fs.writeFileSync(
    recentWorkspacesPath,
    JSON.stringify([workspaceDir], null, 2),
    "utf-8"
  );

  const app = await electron.launch({
    args: [path.join(process.cwd(), "dist-main/index.js")],
    env: {
      ...process.env,
      NODE_ENV: "test",
      FORTSHELL_USER_DATA_DIR: userDataDir,
    },
  });

  const page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await installRendererHarness(page);
  await page.getByRole("button", { name: path.basename(workspaceDir) }).click();
  await page.waitForTimeout(900);

  return {
    app,
    page,
    workspaceDir,
    userDataDir,
  };
}

async function cleanupLaunchedApp(launchedApp: LaunchedApp): Promise<void> {
  await launchedApp.app.close();
  fs.rmSync(launchedApp.workspaceDir, { recursive: true, force: true });
  fs.rmSync(launchedApp.userDataDir, { recursive: true, force: true });
}

async function getSessions(page: Page): Promise<SessionRuntime[]> {
  return page.evaluate(() => {
    const globalState = window as typeof window & {
      __lockHarness?: {
        sessionPayload: { sessions: SessionRuntime[] } | null;
      };
    };

    return globalState.__lockHarness?.sessionPayload?.sessions ?? [];
  });
}

async function waitForFirstSession(page: Page): Promise<string> {
  await expect.poll(async () => (await getSessions(page)).length, { timeout: 5000 }).toBeGreaterThan(0);
  const sessions = await getSessions(page);
  return sessions[0].terminalId;
}

async function createDedicatedTerminal(page: Page, cwd: string): Promise<string> {
  const result = await page.evaluate(async (workspacePath) => {
    return window.electronAPI.terminalCreate({ cwd: workspacePath });
  }, cwd);

  const terminalId = result.id;
  await expect.poll(async () => {
    const sessions = await getSessions(page);
    return sessions.some((session) => session.terminalId === terminalId);
  }, { timeout: 5000 }).toBe(true);
  await page.waitForTimeout(1000);
  return terminalId;
}

async function waitForSessionTrustState(
  page: Page,
  terminalId: string,
  trustState: string
): Promise<void> {
  await expect.poll(async () => {
    const sessions = await getSessions(page);
    return sessions.find((session) => session.terminalId === terminalId)?.trustState ?? null;
  }, { timeout: 2000 }).toBe(trustState);
}

async function restartTerminal(page: Page, terminalId: string): Promise<string> {
  const result = await page.evaluate(async (targetId) => {
    return window.electronAPI.terminalRestart(targetId);
  }, terminalId);

  expect(result.ok).toBe(true);
  expect(result.replacement?.newTerminalId).toBeTruthy();

  const newTerminalId = result.replacement!.newTerminalId;
  await expect.poll(async () => {
    const sessions = await getSessions(page);
    return sessions.some((session) => session.terminalId === newTerminalId);
  }, { timeout: 5000 }).toBe(true);

  return newTerminalId;
}

async function runTerminalCommand(
  page: Page,
  terminalId: string,
  command: string,
  marker = `__FT_${Date.now()}_${Math.random().toString(16).slice(2)}__`
): Promise<TerminalCommandResult> {
  await page.evaluate(({ targetId, cmd }) => {
    const globalState = window as typeof window & {
      __lockHarness?: {
        buffers: Record<string, string>;
      };
    };

    if (!globalState.__lockHarness) {
      throw new Error("lock harness is not installed");
    }

    globalState.__lockHarness.buffers[targetId] = "";
    window.electronAPI.terminalWrite(targetId, `${cmd}\n`);
  }, {
    targetId: terminalId,
    cmd: command,
  });

  await expect.poll(async () => {
    return page.evaluate((targetId) => {
      const globalState = window as typeof window & {
        __lockHarness?: {
          buffers: Record<string, string>;
        };
      };

      return globalState.__lockHarness?.buffers[targetId] ?? "";
    }, terminalId);
  }, { timeout: 10000 }).toMatch(new RegExp(`${escapeRegExp(marker)}:EXIT:\\d+`));

  const output = await page.evaluate((targetId) => {
    const globalState = window as typeof window & {
      __lockHarness?: {
        buffers: Record<string, string>;
      };
    };

    return globalState.__lockHarness?.buffers[targetId] ?? "";
  }, terminalId);

  const exitMatch = output.match(new RegExp(`${escapeRegExp(marker)}:EXIT:(\\d+)`));
  if (!exitMatch) {
    throw new Error(`Missing terminal exit marker in output:\n${output}`);
  }

  return {
    output,
    exitCode: Number(exitMatch[1]),
  };
}

async function runCatFile(page: Page, terminalId: string, filePath: string): Promise<TerminalCommandResult> {
  const marker = `__FT_${Date.now()}_${Math.random().toString(16).slice(2)}__`;
  const command = `(cat -- ${shellQuote(filePath)}; cmd_status=$?; printf "\\n${marker}:EXIT:%s\\n" "$cmd_status") 2>&1`;
  return runTerminalCommand(page, terminalId, command, marker);
}

async function runWriteFile(
  page: Page,
  terminalId: string,
  filePath: string,
  content: string
): Promise<TerminalCommandResult> {
  const marker = `__FT_${Date.now()}_${Math.random().toString(16).slice(2)}__`;
  const command = `(printf %s ${shellQuote(content)} > ${shellQuote(filePath)}; cmd_status=$?; printf "\\n${marker}:EXIT:%s\\n" "$cmd_status") 2>&1`;
  return runTerminalCommand(page, terminalId, command, marker);
}

async function runMoveFile(
  page: Page,
  terminalId: string,
  sourcePath: string,
  destinationPath: string
): Promise<TerminalCommandResult> {
  const marker = `__FT_${Date.now()}_${Math.random().toString(16).slice(2)}__`;
  const command = `(mv -- ${shellQuote(sourcePath)} ${shellQuote(destinationPath)}; cmd_status=$?; printf "\\n${marker}:EXIT:%s\\n" "$cmd_status") 2>&1`;
  return runTerminalCommand(page, terminalId, command, marker);
}

async function waitForCompiledPath(
  page: Page,
  relativePath: string,
  present: boolean
): Promise<void> {
  await expect.poll(async () => {
    const compiled = await page.evaluate(async () => window.electronAPI.protectionListCompiled());
    return compiled.some((entry) => entry.relativePath === relativePath);
  }, { timeout: 2000 }).toBe(present);
}

async function openExplorer(page: Page): Promise<void> {
  await page.getByLabel("Explorer").click();
  await page.waitForTimeout(120);
}

async function openProtection(page: Page): Promise<void> {
  await page.getByLabel("Protection").click();
  await page.waitForTimeout(120);
}

async function waitForExplorerLock(
  page: Page,
  relativePath: string,
  locked: boolean
): Promise<void> {
  await openExplorer(page);
  await expect.poll(async () => {
    return page.evaluate((targetPath) => {
      const rows = Array.from(document.querySelectorAll<HTMLElement>(".filetree-node"));
      const row = rows.find((candidate) => candidate.dataset.relativePath === targetPath);
      if (!row) {
        return false;
      }
      return row.querySelector(".filetree-lock") !== null;
    }, relativePath);
  }, { timeout: 2000 }).toBe(locked);
}

async function waitForProtectionPath(
  page: Page,
  relativePath: string,
  present: boolean
): Promise<void> {
  await openProtection(page);
  await expect.poll(async () => {
    return page.evaluate((targetPath) => {
      return Array.from(document.querySelectorAll(".protection-path-primary")).some(
        (element) => element.textContent === targetPath
      );
    }, relativePath);
  }, { timeout: 2000 }).toBe(present);
}

function expectBlocked(result: TerminalCommandResult, secretContent: string): void {
  expect(result.exitCode).not.toBe(0);
  expect(result.output).not.toContain(secretContent);
  expect(result.output.toLowerCase()).toContain("operation not permitted");
}

function expectAllowed(result: TerminalCommandResult, secretContent: string): void {
  expect(result.exitCode).toBe(0);
  expect(result.output).toContain(secretContent);
}

function expectCommandSucceeded(result: TerminalCommandResult): void {
  expect(result.exitCode).toBe(0);
  expect(result.output.toLowerCase()).not.toContain("operation not permitted");
}

test.describe("Lock Integrity", () => {
  test("manual path rules stay synchronized across terminal, explorer, and protection views", async () => {
    const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-manual-lock-"));
    const targetPath = path.join(workspaceDir, "manual.txt");
    const renamedPath = path.join(workspaceDir, "manual-renamed.txt");
    const publicPath = path.join(workspaceDir, "public.txt");
    const secretContent = "MANUAL_SECRET";
    const publicContent = "MANUAL_PUBLIC";
    fs.writeFileSync(targetPath, secretContent);
    fs.writeFileSync(publicPath, publicContent);

    const launched = await launchAppForWorkspace(workspaceDir);
    try {
      await waitForFirstSession(launched.page);
      let terminalId = await createDedicatedTerminal(launched.page, workspaceDir);
      let secondaryTerminalId = await createDedicatedTerminal(launched.page, workspaceDir);

      expectAllowed(await runCatFile(launched.page, terminalId, targetPath), secretContent);

      await launched.page.evaluate(async (filePath) => {
        await window.electronAPI.policySet(filePath);
      }, targetPath);

      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForSessionTrustState(launched.page, secondaryTerminalId, "stale-policy");
      await waitForCompiledPath(launched.page, "manual.txt", true);
      await waitForExplorerLock(launched.page, "manual.txt", true);
      await waitForProtectionPath(launched.page, "manual.txt", true);

      terminalId = await restartTerminal(launched.page, terminalId);
      secondaryTerminalId = await restartTerminal(launched.page, secondaryTerminalId);
      await waitForSessionTrustState(launched.page, terminalId, "protected");
      await waitForSessionTrustState(launched.page, secondaryTerminalId, "protected");
      expectBlocked(await runCatFile(launched.page, terminalId, targetPath), secretContent);
      expectBlocked(await runWriteFile(launched.page, terminalId, targetPath, "MUTATED_SECRET"), "MUTATED_SECRET");
      expectBlocked(await runMoveFile(launched.page, secondaryTerminalId, targetPath, renamedPath), secretContent);
      expectAllowed(await runCatFile(launched.page, secondaryTerminalId, publicPath), publicContent);
      expectCommandSucceeded(
        await runWriteFile(launched.page, secondaryTerminalId, publicPath, "PUBLIC_MUTATED")
      );
      expect(fs.readFileSync(publicPath, "utf-8")).toBe("PUBLIC_MUTATED");

      await launched.page.evaluate(async (filePath) => {
        await window.electronAPI.policyRemove(filePath);
      }, targetPath);

      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForSessionTrustState(launched.page, secondaryTerminalId, "stale-policy");
      await waitForCompiledPath(launched.page, "manual.txt", false);
      await waitForExplorerLock(launched.page, "manual.txt", false);
      await waitForProtectionPath(launched.page, "manual.txt", false);

      terminalId = await restartTerminal(launched.page, terminalId);
      secondaryTerminalId = await restartTerminal(launched.page, secondaryTerminalId);
      expectAllowed(await runCatFile(launched.page, terminalId, targetPath), secretContent);
      expectCommandSucceeded(
        await runWriteFile(launched.page, secondaryTerminalId, targetPath, "RESTORED_SECRET")
      );
      expect(fs.readFileSync(targetPath, "utf-8")).toBe("RESTORED_SECRET");
    } finally {
      await cleanupLaunchedApp(launched);
    }
  });

  test("manual path rules track delete, recreate, and rename back into the locked path", async () => {
    const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-manual-lifecycle-"));
    const targetPath = path.join(workspaceDir, "manual.txt");
    const movedPath = path.join(workspaceDir, "moved.txt");
    fs.writeFileSync(targetPath, "MANUAL_LIFECYCLE");

    const launched = await launchAppForWorkspace(workspaceDir);
    try {
      await waitForFirstSession(launched.page);
      let terminalId = await createDedicatedTerminal(launched.page, workspaceDir);

      await launched.page.evaluate(async (filePath) => {
        await window.electronAPI.policySet(filePath);
      }, targetPath);

      await waitForCompiledPath(launched.page, "manual.txt", true);

      fs.renameSync(targetPath, movedPath);
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, "manual.txt", false);
      await waitForExplorerLock(launched.page, "manual.txt", false);
      await waitForProtectionPath(launched.page, "manual.txt", false);

      fs.renameSync(movedPath, targetPath);
      await waitForCompiledPath(launched.page, "manual.txt", true);
      await waitForExplorerLock(launched.page, "manual.txt", true);
      await waitForProtectionPath(launched.page, "manual.txt", true);

      fs.rmSync(targetPath, { force: true });
      await waitForCompiledPath(launched.page, "manual.txt", false);
      await waitForExplorerLock(launched.page, "manual.txt", false);
      await waitForProtectionPath(launched.page, "manual.txt", false);

      fs.writeFileSync(targetPath, "MANUAL_RECREATED");
      await waitForCompiledPath(launched.page, "manual.txt", true);
      await waitForExplorerLock(launched.page, "manual.txt", true);
      await waitForProtectionPath(launched.page, "manual.txt", true);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectBlocked(await runCatFile(launched.page, terminalId, targetPath), "MANUAL_RECREATED");
    } finally {
      await cleanupLaunchedApp(launched);
    }
  });

  test("env-file preset tracks create, delete, and remove while keeping terminal access synchronized", async () => {
    const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-preset-lock-"));
    const envPath = path.join(workspaceDir, ".env");
    const envLocalPath = path.join(workspaceDir, ".env.local");
    const notesPath = path.join(workspaceDir, "notes.txt");
    const secretContent = "PRESET_SECRET";
    fs.writeFileSync(notesPath, "PRESET_PUBLIC");

    const launched = await launchAppForWorkspace(workspaceDir);
    try {
      await waitForFirstSession(launched.page);
      let terminalId = await createDedicatedTerminal(launched.page, workspaceDir);

      await launched.page.evaluate(async () => {
        await window.electronAPI.protectionApplyPreset("env-files");
      });

      fs.writeFileSync(envPath, secretContent);
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, ".env", true);
      await waitForExplorerLock(launched.page, ".env", true);
      await waitForProtectionPath(launched.page, ".env", true);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectBlocked(await runCatFile(launched.page, terminalId, envPath), secretContent);
      expectBlocked(await runWriteFile(launched.page, terminalId, envPath, "PRESET_MUTATED"), "PRESET_MUTATED");
      expectAllowed(await runCatFile(launched.page, terminalId, notesPath), "PRESET_PUBLIC");
      expectCommandSucceeded(await runWriteFile(launched.page, terminalId, notesPath, "PRESET_PUBLIC_MUTATED"));
      expect(fs.readFileSync(notesPath, "utf-8")).toBe("PRESET_PUBLIC_MUTATED");

      fs.rmSync(envPath, { force: true });
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, ".env", false);
      await waitForExplorerLock(launched.page, ".env", false);
      await waitForProtectionPath(launched.page, ".env", false);

      fs.writeFileSync(envPath, secretContent);
      await waitForCompiledPath(launched.page, ".env", true);
      await waitForExplorerLock(launched.page, ".env", true);
      await waitForProtectionPath(launched.page, ".env", true);

      fs.renameSync(envPath, envLocalPath);
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, ".env", false);
      await waitForCompiledPath(launched.page, ".env.local", true);
      await waitForExplorerLock(launched.page, ".env.local", true);
      await waitForProtectionPath(launched.page, ".env.local", true);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectBlocked(await runCatFile(launched.page, terminalId, envLocalPath), secretContent);

      const presetRuleId = await launched.page.evaluate(async () => {
        const rules = await window.electronAPI.protectionListRules();
        return rules.find((rule) => rule.kind === "preset" && rule.presetId === "env-files")?.id ?? null;
      });
      expect(presetRuleId).toBeTruthy();

      await launched.page.evaluate(async (ruleId) => {
        await window.electronAPI.protectionRemoveRule(ruleId);
      }, presetRuleId);

      await waitForCompiledPath(launched.page, ".env", false);
      await waitForCompiledPath(launched.page, ".env.local", false);
      await waitForExplorerLock(launched.page, ".env", false);
      await waitForExplorerLock(launched.page, ".env.local", false);
      await waitForProtectionPath(launched.page, ".env", false);
      await waitForProtectionPath(launched.page, ".env.local", false);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectAllowed(await runCatFile(launched.page, terminalId, envLocalPath), secretContent);
    } finally {
      await cleanupLaunchedApp(launched);
    }
  });

  test("extension rules cover dotfile env variants and react to rename", async () => {
    const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-extension-lock-"));
    const sourcePath = path.join(workspaceDir, ".env.local");
    const renamedPath = path.join(workspaceDir, "notes.txt");
    const publicPath = path.join(workspaceDir, "public.txt");
    const secretContent = "EXTENSION_SECRET";
    fs.writeFileSync(publicPath, "EXTENSION_PUBLIC");

    const launched = await launchAppForWorkspace(workspaceDir);
    try {
      await waitForFirstSession(launched.page);
      let terminalId = await createDedicatedTerminal(launched.page, workspaceDir);

      await launched.page.evaluate(async () => {
        await window.electronAPI.protectionAddExtensionRule([".env"]);
      });

      fs.writeFileSync(sourcePath, secretContent);
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, ".env.local", true);
      await waitForExplorerLock(launched.page, ".env.local", true);
      await waitForProtectionPath(launched.page, ".env.local", true);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectBlocked(await runCatFile(launched.page, terminalId, sourcePath), secretContent);
      expectBlocked(await runWriteFile(launched.page, terminalId, sourcePath, "EXTENSION_MUTATED"), "EXTENSION_MUTATED");
      expectAllowed(await runCatFile(launched.page, terminalId, publicPath), "EXTENSION_PUBLIC");
      expectCommandSucceeded(
        await runWriteFile(launched.page, terminalId, publicPath, "EXTENSION_PUBLIC_MUTATED")
      );
      expect(fs.readFileSync(publicPath, "utf-8")).toBe("EXTENSION_PUBLIC_MUTATED");

      fs.rmSync(sourcePath, { force: true });
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, ".env.local", false);
      await waitForExplorerLock(launched.page, ".env.local", false);
      await waitForProtectionPath(launched.page, ".env.local", false);

      fs.writeFileSync(sourcePath, secretContent);
      await waitForCompiledPath(launched.page, ".env.local", true);
      await waitForExplorerLock(launched.page, ".env.local", true);
      await waitForProtectionPath(launched.page, ".env.local", true);

      fs.renameSync(sourcePath, renamedPath);
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, ".env.local", false);
      await waitForExplorerLock(launched.page, ".env.local", false);
      await waitForProtectionPath(launched.page, ".env.local", false);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectAllowed(await runCatFile(launched.page, terminalId, renamedPath), secretContent);

      const extensionRuleId = await launched.page.evaluate(async () => {
        const rules = await window.electronAPI.protectionListRules();
        return rules.find((rule) => rule.kind === "extension" && rule.extensions.includes(".env"))?.id ?? null;
      });
      expect(extensionRuleId).toBeTruthy();

      await launched.page.evaluate(async (ruleId) => {
        await window.electronAPI.protectionRemoveRule(ruleId);
      }, extensionRuleId);

      fs.renameSync(renamedPath, sourcePath);
      await waitForCompiledPath(launched.page, ".env.local", false);
      await waitForExplorerLock(launched.page, ".env.local", false);
      await waitForProtectionPath(launched.page, ".env.local", false);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectAllowed(await runCatFile(launched.page, terminalId, sourcePath), secretContent);
    } finally {
      await cleanupLaunchedApp(launched);
    }
  });

  test("directory rules protect existing and new nested files and release moved files", async () => {
    const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-directory-lock-"));
    const secretsDir = path.join(workspaceDir, "secrets");
    const publicDir = path.join(workspaceDir, "public");
    fs.mkdirSync(secretsDir, { recursive: true });
    fs.mkdirSync(publicDir, { recursive: true });

    const existingSecretPath = path.join(secretsDir, "db.txt");
    const newSecretPath = path.join(secretsDir, "fresh.txt");
    const movedPath = path.join(publicDir, "db.txt");
    const publicPath = path.join(publicDir, "notes.txt");
    fs.writeFileSync(existingSecretPath, "DIRECTORY_SECRET");
    fs.writeFileSync(publicPath, "DIRECTORY_PUBLIC");

    const launched = await launchAppForWorkspace(workspaceDir);
    try {
      await waitForFirstSession(launched.page);
      let terminalId = await createDedicatedTerminal(launched.page, workspaceDir);

      await launched.page.evaluate(async (dirPath) => {
        await window.electronAPI.protectionAddDirectoryRule(dirPath);
      }, secretsDir);

      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, "secrets", true);
      await waitForCompiledPath(launched.page, "secrets/db.txt", true);
      await waitForExplorerLock(launched.page, "secrets", true);
      await waitForProtectionPath(launched.page, "secrets/db.txt", true);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectBlocked(await runCatFile(launched.page, terminalId, existingSecretPath), "DIRECTORY_SECRET");
      expectBlocked(await runWriteFile(launched.page, terminalId, existingSecretPath, "DIR_MUTATED"), "DIR_MUTATED");
      expectAllowed(await runCatFile(launched.page, terminalId, publicPath), "DIRECTORY_PUBLIC");
      expectCommandSucceeded(
        await runWriteFile(launched.page, terminalId, publicPath, "DIRECTORY_PUBLIC_MUTATED")
      );
      expect(fs.readFileSync(publicPath, "utf-8")).toBe("DIRECTORY_PUBLIC_MUTATED");

      fs.writeFileSync(newSecretPath, "NEW_DIRECTORY_SECRET");
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, "secrets/fresh.txt", true);
      await waitForProtectionPath(launched.page, "secrets/fresh.txt", true);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectBlocked(await runCatFile(launched.page, terminalId, newSecretPath), "NEW_DIRECTORY_SECRET");

      fs.renameSync(existingSecretPath, movedPath);
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, "secrets/db.txt", false);
      await waitForProtectionPath(launched.page, "secrets/db.txt", false);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectAllowed(await runCatFile(launched.page, terminalId, movedPath), "DIRECTORY_SECRET");

      fs.rmSync(newSecretPath, { force: true });
      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, "secrets/fresh.txt", false);
      await waitForProtectionPath(launched.page, "secrets/fresh.txt", false);

      const directoryRuleId = await launched.page.evaluate(async () => {
        const rules = await window.electronAPI.protectionListRules();
        return rules.find((rule) => rule.kind === "directory" && rule.targetPath === "secrets")?.id ?? null;
      });
      expect(directoryRuleId).toBeTruthy();

      await launched.page.evaluate(async (ruleId) => {
        await window.electronAPI.protectionRemoveRule(ruleId);
      }, directoryRuleId);

      await waitForCompiledPath(launched.page, "secrets", false);
      await waitForCompiledPath(launched.page, "secrets/fresh.txt", false);

      fs.writeFileSync(newSecretPath, "NEW_DIRECTORY_SECRET");

      terminalId = await restartTerminal(launched.page, terminalId);
      expectAllowed(await runCatFile(launched.page, terminalId, newSecretPath), "NEW_DIRECTORY_SECRET");
    } finally {
      await cleanupLaunchedApp(launched);
    }
  });

  test("manual path rules block symlink traversal from the FortShell terminal", async () => {
    const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-symlink-lock-"));
    const targetPath = path.join(workspaceDir, "secret.txt");
    const symlinkPath = path.join(workspaceDir, "link.txt");
    const secretContent = "SYMLINK_SECRET";
    fs.writeFileSync(targetPath, secretContent);
    fs.symlinkSync(targetPath, symlinkPath);

    const launched = await launchAppForWorkspace(workspaceDir);
    try {
      await waitForFirstSession(launched.page);
      let terminalId = await createDedicatedTerminal(launched.page, workspaceDir);

      expectAllowed(await runCatFile(launched.page, terminalId, symlinkPath), secretContent);

      await launched.page.evaluate(async (filePath) => {
        await window.electronAPI.policySet(filePath);
      }, targetPath);

      await waitForSessionTrustState(launched.page, terminalId, "stale-policy");
      await waitForCompiledPath(launched.page, "secret.txt", true);
      await waitForProtectionPath(launched.page, "secret.txt", true);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectBlocked(await runCatFile(launched.page, terminalId, symlinkPath), secretContent);

      await launched.page.evaluate(async (filePath) => {
        await window.electronAPI.policyRemove(filePath);
      }, targetPath);

      await waitForCompiledPath(launched.page, "secret.txt", false);
      await waitForProtectionPath(launched.page, "secret.txt", false);

      terminalId = await restartTerminal(launched.page, terminalId);
      expectAllowed(await runCatFile(launched.page, terminalId, symlinkPath), secretContent);
    } finally {
      await cleanupLaunchedApp(launched);
    }
  });
});
