// Wait for both tsc output and vite dev server, then launch Electron
import { execSync, spawn } from "child_process";
import { existsSync } from "fs";
import path from "path";

const mainJs = path.resolve("dist-main/index.js");
const viteUrl = "http://localhost:5173";

async function waitForFile(filePath, timeoutMs = 30000) {
  const start = Date.now();
  while (!existsSync(filePath)) {
    if (Date.now() - start > timeoutMs) {
      throw new Error(`Timeout waiting for ${filePath}`);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
}

async function waitForServer(url, timeoutMs = 30000) {
  const start = Date.now();
  while (true) {
    try {
      await fetch(url);
      return;
    } catch {
      if (Date.now() - start > timeoutMs) {
        throw new Error(`Timeout waiting for ${url}`);
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }
}

async function main() {
  console.log("[launcher] Waiting for tsc and vite...");
  await Promise.all([waitForFile(mainJs), waitForServer(viteUrl)]);
  console.log("[launcher] Starting Electron...");

  const electron = spawn(
    "npx",
    ["electron", "."],
    {
      stdio: "inherit",
      env: { ...process.env, VITE_DEV_SERVER_URL: viteUrl },
      shell: true,
    }
  );

  electron.on("close", (code) => process.exit(code ?? 0));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
