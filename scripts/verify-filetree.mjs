/**
 * Verify file tree works by programmatically setting a workspace path.
 */
import { spawn } from "child_process";
import { writeFileSync, readFileSync, existsSync, unlinkSync, mkdtempSync, mkdirSync } from "fs";
import { setTimeout as sleep } from "timers/promises";
import path from "path";
import os from "os";

const RESULT_FILE = path.resolve("verify-filetree-result.json");
if (existsSync(RESULT_FILE)) unlinkSync(RESULT_FILE);

// Create a test workspace
const testDir = mkdtempSync(path.join(os.tmpdir(), "ai-ide-verify-"));
writeFileSync(path.join(testDir, "README.md"), "# test");
writeFileSync(path.join(testDir, "app.ts"), "console.log('hi')");
mkdirSync(path.join(testDir, "src"));
writeFileSync(path.join(testDir, "src", "index.ts"), "export {}");

console.log(`Test workspace: ${testDir}`);

// Patch main to auto-open workspace
const mainIndexPath = path.resolve("dist-main/index.js");
const originalMain = readFileSync(mainIndexPath, "utf-8");

const patchCode = `
const _resultPath = ${JSON.stringify(RESULT_FILE)};
const _testDir = ${JSON.stringify(testDir.replace(/\\/g, "\\\\"))};
const { app: _a2, BrowserWindow: _B2 } = require("electron");
_a2.on("browser-window-created", async (_, win) => {
  const { writeFileSync: _wf } = require("fs");
  await new Promise(r => setTimeout(r, 5000));
  const results = {};
  try {
    // Simulate opening a folder by sending IPC
    await win.webContents.executeJavaScript(\`
      (async () => {
        // Directly call the workspace:files IPC and check result
        const files = await window.electronAPI.workspaceFiles("${testDir.replace(/\\/g, "\\\\\\\\")}");
        window._testFiles = files;
        return files.length;
      })()
    \`).then(count => { results.fileCount = count; });

    // Check if FileTree has open folder button (should show since no workspace set)
    results.hasOpenBtn = await win.webContents.executeJavaScript(
      'document.querySelector(".filetree-open-btn") !== null'
    );

    // Check sidebar content
    results.sidebarHTML = await win.webContents.executeJavaScript(
      'document.querySelector(".sidebar")?.innerHTML?.substring(0, 200) || "empty"'
    );

    results.success = true;
  } catch(err) {
    results.success = false;
    results.error = String(err);
  }
  _wf(_resultPath, JSON.stringify(results, null, 2));
  process.exit(0);
});
`;

writeFileSync(mainIndexPath, patchCode + "\n" + originalMain);

console.log("[...] Launching Electron...");
const electron = spawn("npx", ["electron", "."], { shell: true, stdio: "pipe" });

for (let i = 0; i < 30; i++) {
  await sleep(500);
  if (existsSync(RESULT_FILE)) break;
}

electron.kill();
await sleep(1000);
writeFileSync(mainIndexPath, originalMain);

if (!existsSync(RESULT_FILE)) {
  console.log("[FAIL] Timed out");
  process.exit(1);
}

const results = JSON.parse(readFileSync(RESULT_FILE, "utf-8"));
unlinkSync(RESULT_FILE);

console.log("\nResults:");
console.log(JSON.stringify(results, null, 2));

// Cleanup
const { rmSync } = await import("fs");
rmSync(testDir, { recursive: true, force: true });
