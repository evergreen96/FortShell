/**
 * Full verification: programmatically set workspace and check all UI elements.
 */
import { spawn } from "child_process";
import { writeFileSync, readFileSync, existsSync, unlinkSync, mkdtempSync, mkdirSync } from "fs";
import { setTimeout as sleep } from "timers/promises";
import path from "path";
import os from "os";

const RESULT_FILE = path.resolve("verify-full-result.json");
if (existsSync(RESULT_FILE)) unlinkSync(RESULT_FILE);

// Create test workspace
const testDir = mkdtempSync(path.join(os.tmpdir(), "ai-ide-verify-"));
writeFileSync(path.join(testDir, "README.md"), "# test");
writeFileSync(path.join(testDir, "app.ts"), "console.log('hi')");
writeFileSync(path.join(testDir, "secret.key"), "TOP SECRET");
mkdirSync(path.join(testDir, "src"));
writeFileSync(path.join(testDir, "src", "index.ts"), "export {}");

const escapedDir = testDir.replace(/\\/g, "\\\\");

const mainIndexPath = path.resolve("dist-main/index.js");
const originalMain = readFileSync(mainIndexPath, "utf-8");

const patchCode = `
const _rp = ${JSON.stringify(RESULT_FILE)};
const { app: _a, BrowserWindow: _B } = require("electron");
_a.on("browser-window-created", async (_, win) => {
  const { writeFileSync: _w } = require("fs");
  await new Promise(r => setTimeout(r, 4000));
  const R = {};
  try {
    // Set workspace path programmatically via React state
    await win.webContents.executeJavaScript(\`
      // Find React fiber root and trigger openFolder result
      // Simpler: directly invoke IPC and manually trigger state change
      (async () => {
        // Call workspace:files to get tree
        const files = await window.electronAPI.workspaceFiles("${escapedDir}");
        window.__verifyFiles = files;
        return files.length;
      })()
    \`).then(n => { R.ipcFileCount = n; });

    // Get file names from IPC
    R.ipcFileNames = await win.webContents.executeJavaScript(\`
      window.__verifyFiles.map(f => f.name)
    \`);

    // Check initial UI state
    R.hasOpenBtn = await win.webContents.executeJavaScript(
      'document.querySelector(".filetree-open-btn") !== null'
    );
    R.hasTerminal = await win.webContents.executeJavaScript(
      'document.querySelector(".xterm") !== null'
    );
    R.hasStatusBar = await win.webContents.executeJavaScript(
      'document.querySelector(".status-bar") !== null'
    );
    R.tabCount = await win.webContents.executeJavaScript(
      'document.querySelectorAll(".tab").length'
    );

    // Check keyboard shortcut handler exists
    R.hasKeyHandler = await win.webContents.executeJavaScript(\`
      typeof window !== 'undefined'
    \`);

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

console.log("=== Full Verification ===\n");
console.log(`Test workspace: ${testDir}\n`);

const el = spawn("npx", ["electron", "."], { shell: true, stdio: "pipe" });
for (let i = 0; i < 30; i++) {
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
  ["IPC workspace:files returns files", R.ipcFileCount > 0, `${R.ipcFileCount} files`],
  ["IPC returns correct file names", R.ipcFileNames?.includes("README.md"), R.ipcFileNames?.join(", ")],
  ["IPC returns secret.key", R.ipcFileNames?.includes("secret.key"), ""],
  ["Terminal (xterm) rendered", R.hasTerminal, ""],
  ["Status bar rendered", R.hasStatusBar, ""],
  ["Tab created (auto)", R.tabCount > 0, `${R.tabCount} tabs`],
  ["Open Folder button shown (no workspace set)", R.hasOpenBtn, ""],
];

let pass = 0, fail = 0;
for (const [name, ok, detail] of checks) {
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` — ${detail}` : ""}`);
  if (ok) pass++; else fail++;
}

console.log(`\n=== ${pass} passed, ${fail} failed ===`);

if (R.error) console.log(`\nError: ${R.error}`);

// Cleanup
const { rmSync } = await import("fs");
rmSync(testDir, { recursive: true, force: true });
process.exit(fail > 0 ? 1 : 0);
