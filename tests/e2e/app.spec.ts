import { test, expect, _electron as electron, ElectronApplication, Page } from "@playwright/test";
import path from "path";
import fs from "fs";
import os from "os";

let app: ElectronApplication;
let page: Page;
let userDataDir: string;

test.beforeAll(async () => {
  userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-app-user-data-"));
  app = await electron.launch({
    args: [path.join(__dirname, "../../dist-main/index.js")],
    env: {
      ...process.env,
      NODE_ENV: "test",
      FORTSHELL_USER_DATA_DIR: userDataDir,
    },
  });
  page = await app.firstWindow();
  // Wait for initial render
  await page.waitForLoadState("domcontentloaded");
});

test.afterAll(async () => {
  if (app) await app.close();
  if (userDataDir) fs.rmSync(userDataDir, { recursive: true, force: true });
});

test.describe("App Launch", () => {
  test("should show the app window", async () => {
    const title = await page.title();
    expect(title).toBeTruthy();
  });

  test("should show welcome screen", async () => {
    // The welcome screen should be visible when no workspace is open
    const welcome = page.locator(".welcome");
    await expect(welcome).toBeVisible();
  });
});

test.describe("Workspace", () => {
  let tmpDir: string;

  test.beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-ide-e2e-"));
    fs.writeFileSync(path.join(tmpDir, "test.txt"), "hello");
    fs.mkdirSync(path.join(tmpDir, "src"));
    fs.writeFileSync(path.join(tmpDir, "src", "main.ts"), "console.log('hi')");
  });

  test.afterAll(() => {
    if (tmpDir) fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  test("should open workspace via IPC", async () => {
    // Directly set workspace path via evaluate in main process
    await app.evaluate(async ({ BrowserWindow }, dirPath) => {
      const win = BrowserWindow.getAllWindows()[0];
      if (win) {
        win.webContents.send("test:set-workspace", dirPath);
      }
    }, tmpDir);

    // Give the app a moment to process
    await page.waitForTimeout(500);
  });
});

test.describe("Terminal", () => {
  test("should have terminal-related UI elements", async () => {
    // Check that the app rendered (header, tab bar, or terminal area)
    const appEl = page.locator(".app");
    await expect(appEl).toBeVisible();
  });
});
