import { _electron as electron } from "@playwright/test";
import fs from "fs";
import os from "os";
import path from "path";

async function main() {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "fortshell-shot-"));
  const tmpDir = path.join(tempRoot, "demo_workspace");
  const userDataDir = path.join(os.homedir(), "Library", "Application Support", "Electron");
  const recentPath = path.join(userDataDir, "recent-workspaces.json");
  const previousRecent = fs.existsSync(recentPath)
    ? fs.readFileSync(recentPath, "utf-8")
    : null;

  fs.mkdirSync(tmpDir, { recursive: true });
  fs.writeFileSync(path.join(tmpDir, "public.txt"), "PUBLIC CONTENT\n");
  fs.writeFileSync(path.join(tmpDir, "secret.env"), "API_KEY=super-secret\n");
  fs.writeFileSync(path.join(tmpDir, "README.local.md"), "# temp\n");
  fs.mkdirSync(path.join(tmpDir, "src"));
  fs.writeFileSync(path.join(tmpDir, "src", "main.ts"), 'console.log("hello")\n');

  fs.mkdirSync(userDataDir, { recursive: true });
  const nextRecent = [tmpDir];
  fs.writeFileSync(recentPath, JSON.stringify(nextRecent, null, 2));
  console.log("[capture] prepared temp workspace", tmpDir);

  const app = await electron.launch({
    args: [path.join(process.cwd(), "dist-main/index.js")],
    env: { ...process.env, NODE_ENV: "test" },
  });

  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    console.log("[capture] window ready");
    await app.evaluate(({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows()[0];
      if (!win) return;
      win.setBounds({ width: 1440, height: 920 });
      win.center();
    });
    console.log("[capture] resized window");

    await page.waitForSelector(".welcome-recent-item", { timeout: 10000 });
    const recentCount = await page.locator(".welcome-recent-item").count();
    console.log("[capture] recent items", recentCount);
    const recentTexts = await page.locator(".welcome-recent-item").allTextContents();
    console.log("[capture] recent texts", JSON.stringify(recentTexts));
    const targetRecent = page.locator(".welcome-recent-item").first();
    await targetRecent.click();
    console.log("[capture] clicked recent workspace");
    await page.waitForSelector(".filetree-root-name", { timeout: 10000 });
    await page.waitForTimeout(1200);
    console.log("[capture] explorer ready");

    const secretPath = path.join(tmpDir, "secret.env");
    await page.evaluate(async (filePath) => {
      await window.electronAPI.policySet(filePath);
    }, secretPath);
    await page.waitForSelector(".nav-rail-badge", { timeout: 10000 });
    await page.waitForTimeout(600);
    console.log("[capture] protection applied");

    const stale = page.locator(".tab-stale-icon").first();
    if (await stale.count()) {
      await stale.click();
      await page.waitForTimeout(1200);
    }
    console.log("[capture] terminal restarted");

    const helper = page.locator(".xterm-helper-textarea").first();
    await helper.focus();
    await page.keyboard.type("cat secret.env");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(1200);
    console.log("[capture] terminal command complete");

    await page.screenshot({
      path: path.join(process.cwd(), "assets", "screenshot.png"),
    });
    console.log("[capture] saved explorer screenshot");

    await page.locator('button[aria-label="Protection"]').click();
    await page.waitForSelector(".protection-center", { timeout: 10000 });
    await page.waitForTimeout(600);
    await page.screenshot({
      path: path.join(process.cwd(), "assets", "protection-center.png"),
    });
    console.log("[capture] saved protection screenshot");
  } finally {
    await app.close();
    if (previousRecent === null) {
      fs.rmSync(recentPath, { force: true });
    } else {
      fs.writeFileSync(recentPath, previousRecent);
    }
    fs.rmSync(tempRoot, { recursive: true, force: true });
    console.log("[capture] cleaned up");
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
