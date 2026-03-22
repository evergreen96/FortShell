/**
 * UI verification: checks Welcome screen, then triggers workspace open.
 */
import { spawn } from "child_process";
import { writeFileSync, readFileSync, existsSync, unlinkSync, mkdtempSync, mkdirSync } from "fs";
import { setTimeout as sleep } from "timers/promises";
import path from "path";
import os from "os";

const RESULT_FILE = path.resolve("verify-ui-result.json");
if (existsSync(RESULT_FILE)) unlinkSync(RESULT_FILE);

// Create test workspace
const testDir = mkdtempSync(path.join(os.tmpdir(), "ai-ide-verify-"));
writeFileSync(path.join(testDir, "README.md"), "# test");
writeFileSync(path.join(testDir, "app.ts"), "console.log('hi')");
mkdirSync(path.join(testDir, "src"));
writeFileSync(path.join(testDir, "src", "index.ts"), "export {}");

const mainIndexPath = path.resolve("dist-main/index.js");
const originalMain = readFileSync(mainIndexPath, "utf-8");

const escapedDir = testDir.replace(/\\/g, "\\\\");

const patchCode = `
const _rp = ${JSON.stringify(RESULT_FILE)};
const { app: _a, BrowserWindow: _B } = require("electron");
_a.on("browser-window-created", async (_, win) => {
  const { writeFileSync: _w } = require("fs");
  await new Promise(r => setTimeout(r, 4000));
  const R = {};
  try {
    // Phase 1: Welcome screen
    R.hasWelcome = await win.webContents.executeJavaScript(
      'document.querySelector(".welcome") !== null'
    );
    R.hasWelcomeTitle = await win.webContents.executeJavaScript(
      'document.querySelector(".welcome-title")?.textContent || ""'
    );
    R.hasOpenBtn = await win.webContents.executeJavaScript(
      'document.querySelector(".welcome-open-btn") !== null'
    );

    // Phase 2: Override the IPC handler to bypass dialog, then click
    const { ipcMain } = require("electron");
    ipcMain.removeHandler("workspace:open");
    ipcMain.handle("workspace:open", async () => "${escapedDir}");
    await win.webContents.executeJavaScript(\`
      document.querySelector(".welcome-open-btn")?.click()
    \`);

    await new Promise(r => setTimeout(r, 5000));

    // Phase 3: Check workspace UI
    R.hasHeader = await win.webContents.executeJavaScript(
      'document.querySelector(".app-header") !== null'
    );
    R.hasTerminalArea = await win.webContents.executeJavaScript(
      'document.querySelector(".terminal-area") !== null'
    );
    R.hasSidebar = await win.webContents.executeJavaScript(
      'document.querySelector(".sidebar") !== null'
    );
    R.hasStatusBar = await win.webContents.executeJavaScript(
      'document.querySelector(".status-bar") !== null'
    );
    R.hasTabBar = await win.webContents.executeJavaScript(
      'document.querySelector(".tab-bar") !== null'
    );
    R.hasTab = await win.webContents.executeJavaScript(
      'document.querySelectorAll(".tab").length > 0'
    );
    R.hasXterm = await win.webContents.executeJavaScript(
      'document.querySelector(".xterm") !== null'
    );
    R.hasXtermScreen = await win.webContents.executeJavaScript(
      'document.querySelector(".xterm-screen") !== null'
    );
    R.hasFileTree = await win.webContents.executeJavaScript(
      'document.querySelector(".filetree") !== null'
    );
    R.hasResizeHandle = await win.webContents.executeJavaScript(
      'document.querySelector(".sidebar-resize-handle") !== null'
    );
    R.title = await win.webContents.executeJavaScript(
      'document.querySelector(".app-header h1")?.textContent || ""'
    );

    R.success = true;
  } catch(e) {
    R.success = false;
    R.error = String(e);
  }
  _w(_rp, JSON.stringify(R, null, 2));
  process.exit(0);
});
`;

writeFileSync(mainIndexPath, patchCode + "\n" + originalMain);

console.log("=== UI Verification ===\n");
const el = spawn("npx", ["electron", "."], { shell: true, stdio: "pipe" });

for (let i = 0; i < 40; i++) {
  await sleep(500);
  if (existsSync(RESULT_FILE)) break;
}
el.kill();
await sleep(1000);
writeFileSync(mainIndexPath, originalMain);

if (!existsSync(RESULT_FILE)) {
  console.log("[FAIL] Timed out");
  process.exit(1);
}

const R = JSON.parse(readFileSync(RESULT_FILE, "utf-8"));
unlinkSync(RESULT_FILE);

const checks = [
  // Welcome
  ["Welcome screen shown", R.hasWelcome],
  ["Welcome title is 'AI-IDE'", R.hasWelcomeTitle === "AI-IDE"],
  ["Open Folder button present", R.hasOpenBtn],
  // After workspace open
  ["App header visible", R.hasHeader],
  ["Terminal area visible", R.hasTerminalArea],
  ["Sidebar visible", R.hasSidebar],
  ["Status bar visible", R.hasStatusBar],
  ["Tab bar visible", R.hasTabBar],
  ["Terminal tab created", R.hasTab],
  ["xterm.js rendered", R.hasXterm],
  ["xterm screen present", R.hasXtermScreen],
  ["File tree rendered", R.hasFileTree],
  ["Sidebar resize handle present", R.hasResizeHandle],
  ["Title is 'AI-IDE v2'", R.title === "AI-IDE v2"],
];

let pass = 0, fail = 0;
for (const [name, ok] of checks) {
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}`);
  if (ok) pass++; else fail++;
}
console.log(`\n=== ${pass} passed, ${fail} failed ===`);
if (R.error) console.log(`\nError: ${R.error}`);

const { rmSync } = await import("fs");
rmSync(testDir, { recursive: true, force: true });
process.exit(fail > 0 ? 1 : 0);
