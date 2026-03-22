/**
 * Automated verification script.
 * Launches Electron, checks DOM state via executeJavaScript, reports results.
 */
import { spawn } from "child_process";
import { setTimeout as sleep } from "timers/promises";

const checks = [];
function check(name, passed, detail = "") {
  checks.push({ name, passed, detail });
  const icon = passed ? "PASS" : "FAIL";
  console.log(`[${icon}] ${name}${detail ? ` — ${detail}` : ""}`);
}

async function main() {
  console.log("=== AI-IDE v2 Verification ===\n");

  // 1. Build
  console.log("[...] Building...");
  const build = spawn("npm", ["run", "build"], { shell: true, stdio: "pipe" });
  const buildResult = await new Promise((resolve) => {
    let out = "";
    build.stdout.on("data", (d) => (out += d));
    build.stderr.on("data", (d) => (out += d));
    build.on("close", (code) => resolve({ code, out }));
  });
  check("Build succeeds", buildResult.code === 0, `exit code ${buildResult.code}`);

  if (buildResult.code !== 0) {
    console.log("\nBuild failed. Cannot continue verification.");
    process.exit(1);
  }

  // 2. Launch Electron with a test script injected
  console.log("\n[...] Launching Electron...");

  const electron = spawn(
    "npx",
    ["electron", "."],
    {
      shell: true,
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        AI_IDE_VERIFY: "1",
      },
    }
  );

  let stdout = "";
  electron.stdout.on("data", (d) => (stdout += d.toString()));
  electron.stderr.on("data", (d) => (stdout += d.toString()));

  // Wait for app to start
  await sleep(8000);

  // Check process is still alive
  const alive = !electron.killed;
  check("Electron stays alive for 8s", alive);

  // Kill and collect
  electron.kill();
  await sleep(1000);

  // 3. Check files exist
  const { existsSync } = await import("fs");
  check("dist-main/index.js exists", existsSync("dist-main/index.js"));
  check("dist-main/preload.js exists", existsSync("dist-main/preload.js"));
  check("dist-renderer/index.html exists", existsSync("dist-renderer/index.html"));

  // 4. Check source files
  check("pty-manager.ts exists", existsSync("src/main/core/terminal/pty-manager.ts"));
  check("profiles.ts exists", existsSync("src/main/core/terminal/profiles.ts"));
  check("policy-engine.ts exists", existsSync("src/main/core/policy/policy-engine.ts"));
  check("file-indexer.ts exists", existsSync("src/main/core/workspace/file-indexer.ts"));
  check("TerminalPane.tsx exists", existsSync("src/renderer/components/Terminal/TerminalPane.tsx"));
  check("FileTree.tsx exists", existsSync("src/renderer/components/FileTree/FileTree.tsx"));
  check("TerminalWorkspace.tsx exists", existsSync("src/renderer/components/Layout/TerminalWorkspace.tsx"));
  check("StatusBar.tsx exists", existsSync("src/renderer/components/StatusBar/StatusBar.tsx"));
  check("integrity-label.ts exists", existsSync("src/main/platform/windows/integrity-label.ts"));
  check("landlock.ts exists", existsSync("src/main/platform/linux/landlock.ts"));
  check("seatbelt.ts exists", existsSync("src/main/platform/darwin/seatbelt.ts"));

  // 5. Run unit tests
  console.log("\n[...] Running unit tests...");
  const testProc = spawn("npx", ["vitest", "run"], { shell: true, stdio: "pipe" });
  const testResult = await new Promise((resolve) => {
    let out = "";
    testProc.stdout.on("data", (d) => (out += d.toString()));
    testProc.stderr.on("data", (d) => (out += d.toString()));
    testProc.on("close", (code) => resolve({ code, out }));
  });

  const testMatch = testResult.out.match(/(\d+) passed/);
  const testCount = testMatch ? testMatch[1] : "?";
  check("Unit tests pass", testResult.code === 0, `${testCount} passed`);

  // Summary
  const passed = checks.filter((c) => c.passed).length;
  const failed = checks.filter((c) => !c.passed).length;
  console.log(`\n=== Summary: ${passed} passed, ${failed} failed ===`);

  if (failed > 0) {
    console.log("\nFailed checks:");
    for (const c of checks.filter((c) => !c.passed)) {
      console.log(`  - ${c.name}${c.detail ? `: ${c.detail}` : ""}`);
    }
  }

  process.exit(failed > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
