import { test, expect, _electron as electron, ElectronApplication, Page } from "@playwright/test";
import path from "path";
import fs from "fs";
import os from "os";

let app: ElectronApplication;
let page: Page;
let tmpDir: string;

test.beforeAll(async () => {
  // Create test workspace
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-term-e2e-"));
  fs.writeFileSync(path.join(tmpDir, "public.txt"), "PUBLIC CONTENT");
  fs.writeFileSync(path.join(tmpDir, "secret.txt"), "SECRET CONTENT");

  app = await electron.launch({
    args: [path.join(__dirname, "../../dist-main/index.js")],
    env: {
      ...process.env,
      NODE_ENV: "test",
    },
  });
  page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
});

test.afterAll(async () => {
  if (app) await app.close();
  if (tmpDir) fs.rmSync(tmpDir, { recursive: true, force: true });
});

test.describe("Terminal Integration", () => {
  test("should have terminal IPC handlers registered", async () => {
    // Verify terminal creation works via renderer's electronAPI
    const hasAPI = await page.evaluate(() => {
      const api = (window as any).electronAPI;
      return typeof api.terminalCreate === "function";
    });
    expect(hasAPI).toBe(true);
  });

  test("app should have correct window properties", async () => {
    const windowState = await app.evaluate(({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows()[0];
      if (!win) return null;
      return {
        title: win.getTitle(),
        width: win.getBounds().width,
        height: win.getBounds().height,
        isVisible: win.isVisible(),
      };
    });

    expect(windowState).not.toBeNull();
    expect(windowState!.isVisible).toBe(true);
    expect(windowState!.width).toBeGreaterThanOrEqual(800);
    expect(windowState!.height).toBeGreaterThanOrEqual(600);
  });

  test("should have context isolation enabled", async () => {
    const hasElectronAPI = await page.evaluate(() => {
      return typeof (window as any).electronAPI !== "undefined";
    });
    expect(hasElectronAPI).toBe(true);
  });

  test("electronAPI should expose expected methods", async () => {
    const methods = await page.evaluate(() => {
      const api = (window as any).electronAPI;
      return {
        hasProtectionListRules: typeof api.protectionListRules === "function",
        hasProtectionListCompiled: typeof api.protectionListCompiled === "function",
        hasProtectionApplyPreset: typeof api.protectionApplyPreset === "function",
        hasProtectionAddExtensionRule:
          typeof api.protectionAddExtensionRule === "function",
        hasProtectionAddDirectoryRule:
          typeof api.protectionAddDirectoryRule === "function",
        hasProtectionImport: typeof api.protectionImport === "function",
        hasProtectionExport: typeof api.protectionExport === "function",
        hasTerminalCreate: typeof api.terminalCreate === "function",
        hasTerminalWrite: typeof api.terminalWrite === "function",
        hasTerminalResize: typeof api.terminalResize === "function",
        hasTerminalDestroy: typeof api.terminalDestroy === "function",
        hasOnTerminalSessionState: typeof api.onTerminalSessionState === "function",
        hasTerminalRestart: typeof api.terminalRestart === "function",
        hasTerminalRestartAllStale: typeof api.terminalRestartAllStale === "function",
        hasTerminalRetryProtected: typeof api.terminalRetryProtected === "function",
        hasTerminalCloseFailed: typeof api.terminalCloseFailed === "function",
        hasPolicySet: typeof api.policySet === "function",
        hasPolicyRemove: typeof api.policyRemove === "function",
        hasPolicyList: typeof api.policyList === "function",
        hasOnPolicyChanged: typeof api.onPolicyChanged === "function",
        hasOpenFolder: typeof api.openFolder === "function",
        platform: api.platform,
      };
    });

    expect(methods.hasProtectionListRules).toBe(true);
    expect(methods.hasProtectionListCompiled).toBe(true);
    expect(methods.hasProtectionApplyPreset).toBe(true);
    expect(methods.hasProtectionAddExtensionRule).toBe(true);
    expect(methods.hasProtectionAddDirectoryRule).toBe(true);
    expect(methods.hasProtectionImport).toBe(true);
    expect(methods.hasProtectionExport).toBe(true);
    expect(methods.hasTerminalCreate).toBe(true);
    expect(methods.hasTerminalWrite).toBe(true);
    expect(methods.hasTerminalResize).toBe(true);
    expect(methods.hasTerminalDestroy).toBe(true);
    expect(methods.hasOnTerminalSessionState).toBe(true);
    expect(methods.hasTerminalRestart).toBe(true);
    expect(methods.hasTerminalRestartAllStale).toBe(true);
    expect(methods.hasTerminalRetryProtected).toBe(true);
    expect(methods.hasTerminalCloseFailed).toBe(true);
    expect(methods.hasPolicySet).toBe(true);
    expect(methods.hasPolicyRemove).toBe(true);
    expect(methods.hasPolicyList).toBe(true);
    expect(methods.hasOnPolicyChanged).toBe(true);
    expect(methods.hasOpenFolder).toBe(true);
    expect(methods.platform).toBe("darwin");
  });
});

test.describe("Policy Integration", () => {
  test("should set and list policies via IPC", async () => {
    const filePath = path.join(tmpDir, "secret.txt");

    // Set policy
    const setResult = await page.evaluate(async (fp) => {
      return await (window as any).electronAPI.policySet(fp);
    }, filePath);
    expect(setResult).toBe(true);

    // List policies
    const list = await page.evaluate(async () => {
      return await (window as any).electronAPI.policyList();
    });
    const realPath = fs.realpathSync(filePath);
    expect(list).toContain(realPath);

    // Remove policy
    const removeResult = await page.evaluate(async (fp) => {
      return await (window as any).electronAPI.policyRemove(fp);
    }, filePath);
    expect(removeResult).toBe(true);

    // Verify removed
    const listAfter = await page.evaluate(async () => {
      return await (window as any).electronAPI.policyList();
    });
    expect(listAfter).not.toContain(realPath);
  });

  test("should not duplicate protections", async () => {
    const filePath = path.join(tmpDir, "secret.txt");

    const r1 = await page.evaluate(async (fp) => {
      return await (window as any).electronAPI.policySet(fp);
    }, filePath);
    expect(r1).toBe(true);

    const r2 = await page.evaluate(async (fp) => {
      return await (window as any).electronAPI.policySet(fp);
    }, filePath);
    expect(r2).toBe(false);

    // Cleanup
    await page.evaluate(async (fp) => {
      return await (window as any).electronAPI.policyRemove(fp);
    }, filePath);
  });
});
