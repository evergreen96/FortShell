/**
 * Lightweight mock API server for frontend testing.
 * Run: node mock-server.mjs
 * Serves on http://127.0.0.1:8765
 */
import http from "node:http";

let terminalCounter = 0;
const terminals = [];

function makeTerminal(name, transport, runnerMode) {
  terminalCounter++;
  const id = `term-${terminalCounter}`;
  const terminal = {
    terminal_id: id,
    name: name || `shell-${terminalCounter}`,
    created_at: new Date().toISOString(),
    transport: transport || "runner",
    runner_mode: runnerMode || null,
    status: "active",
    stale_reason: null,
    execution_session_id: transport === "host" ? null : `sess-${terminalCounter}`,
    bound_agent_run_id: null,
    command_history: [],
    inbox: [],
    inbox_entries: [],
    bound_run: null,
  };
  terminals.push(terminal);
  return terminal;
}

const workspaceEntries = [
  { path: "src", name: "src", is_dir: true, display_name: "src", display_path: "src", suggested_deny_rule: "src/**" },
  { path: "src/main.js", name: "main.js", is_dir: false, display_name: "main.js", display_path: "src/main.js", suggested_deny_rule: "src/main.js" },
  { path: "src/app.rs", name: "app.rs", is_dir: false, display_name: "app.rs", display_path: "src/app.rs", suggested_deny_rule: "src/app.rs" },
  { path: "src/utils.py", name: "utils.py", is_dir: false, display_name: "utils.py", display_path: "src/utils.py", suggested_deny_rule: "src/utils.py" },
  { path: "docs", name: "docs", is_dir: true, display_name: "docs", display_path: "docs", suggested_deny_rule: "docs/**" },
  { path: "docs/readme.md", name: "readme.md", is_dir: false, display_name: "readme.md", display_path: "docs/readme.md", suggested_deny_rule: "docs/readme.md" },
  { path: "package.json", name: "package.json", is_dir: false, display_name: "package.json", display_path: "package.json", suggested_deny_rule: "package.json" },
  { path: "config.yaml", name: "config.yaml", is_dir: false, display_name: "config.yaml", display_path: "config.yaml", suggested_deny_rule: "config.yaml" },
];

const fileContents = {
  "src/main.js": 'console.log("hello world");\n\nfunction greet(name) {\n  return `Hello, ${name}!`;\n}\n\ngreet("AI IDE");\n',
  "src/app.rs": 'fn main() {\n    println!("Hello from Rust!");\n    let x = 42;\n    println!("The answer is {}", x);\n}\n',
  "src/utils.py": 'def add(a: int, b: int) -> int:\n    """Add two numbers."""\n    return a + b\n\n\ndef greet(name: str) -> str:\n    return f"Hello, {name}!"\n',
  "docs/readme.md": "# AI IDE\n\nA workspace-aware IDE with policy-managed terminals.\n\n## Features\n- File tree navigation\n- Multi-terminal support\n- Policy-based access control\n",
  "package.json": '{\n  "name": "test-workspace",\n  "version": "1.0.0"\n}\n',
  "config.yaml": "project:\n  name: ai-ide-test\n  version: 0.1.0\n\nsettings:\n  theme: dark\n  auto_save: true\n",
};

const denyGlobs = [];
let policyVersion = 1;

function desktopShellSnapshot() {
  const activeTerminal = terminals.find((t) => t.status === "active") || terminals[0] || null;
  return {
    kind: "desktop_shell",
    target: ".",
    workspace_panel: {
      kind: "workspace_panel",
      target: ".",
      workspace: { entries: workspaceEntries },
      policy: {
        kind: "policy",
        version: policyVersion,
        deny_globs: [...denyGlobs],
        execution_session_id: "exec-001",
        agent_session_id: "agent-001",
      },
      session: { execution_session_id: "exec-001", agent_session_id: "agent-001" },
      workspace_index: {
        policy_version: policyVersion,
        stale: false,
        stale_reasons: [],
        entry_count: workspaceEntries.length,
        file_count: workspaceEntries.filter((e) => !e.is_dir).length,
        directory_count: workspaceEntries.filter((e) => e.is_dir).length,
      },
    },
    terminals: {
      count: terminals.length,
      active_terminal_id: activeTerminal?.terminal_id || null,
      items: terminals.map((t) => ({ ...t })),
    },
  };
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString();
      try {
        resolve(JSON.parse(raw || "{}"));
      } catch {
        resolve({});
      }
    });
  });
}

function sendJson(res, data, status = 200) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  });
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");

  if (req.method === "OPTIONS") {
    sendJson(res, {}, 204);
    return;
  }

  // GET /api/desktop-shell
  if (req.method === "GET" && url.pathname === "/api/desktop-shell") {
    sendJson(res, desktopShellSnapshot());
    return;
  }

  // GET /api/editor/file
  if (req.method === "GET" && url.pathname === "/api/editor/file") {
    const target = url.searchParams.get("target") || "";
    const content = fileContents[target];
    if (content === undefined) {
      sendJson(res, { error: `File not found: ${target}` }, 404);
      return;
    }
    sendJson(res, {
      kind: "editor_file",
      target,
      path: target,
      managed: true,
      byte_size: Buffer.byteLength(content),
      content,
      proposal: null,
      rendered: null,
    });
    return;
  }

  // POST /api/terminal/create
  if (req.method === "POST" && url.pathname === "/api/terminal/create") {
    const body = await readBody(req);
    const terminal = makeTerminal(body.name, body.transport, body.runner_mode);
    sendJson(res, { kind: "terminal_create", terminal });
    return;
  }

  // POST /api/terminal/run
  if (req.method === "POST" && url.pathname === "/api/terminal/run") {
    const body = await readBody(req);
    const terminal = terminals.find((t) => t.terminal_id === body.terminal_id);
    if (!terminal) {
      sendJson(res, { error: "Terminal not found" }, 404);
      return;
    }
    if (terminal.status !== "active") {
      sendJson(res, { error: `Terminal ${terminal.terminal_id} is ${terminal.status}: ${terminal.stale_reason}` }, 400);
      return;
    }
    terminal.command_history.push(body.command);

    // Simulate command output
    let output = "";
    const cmd = (body.command || "").trim();
    if (cmd === "ls" || cmd === "dir") {
      output = "src/\ndocs/\npackage.json\nconfig.yaml";
    } else if (cmd.startsWith("echo ")) {
      output = cmd.slice(5);
    } else if (cmd === "pwd") {
      output = "/workspace/ai-ide-test";
    } else if (cmd === "whoami") {
      output = "ai-ide-user";
    } else if (cmd === "date") {
      output = new Date().toString();
    } else if (cmd === "git status") {
      output = "On branch main\nnothing to commit, working tree clean";
    } else if (cmd === "python --version") {
      output = "Python 3.12.0";
    } else {
      output = `[mock] executed: ${cmd}`;
    }

    sendJson(res, { kind: "terminal_run", terminal: { ...terminal }, output });
    return;
  }

  // POST /api/policy/deny — also marks managed terminals as stale
  if (req.method === "POST" && url.pathname === "/api/policy/deny") {
    const body = await readBody(req);
    const rule = (body.rule || "").trim();
    if (!rule) {
      sendJson(res, { error: "Expected non-empty rule" }, 400);
      return;
    }
    if (!denyGlobs.includes(rule)) {
      denyGlobs.push(rule);
      policyVersion++;
      // Mark all runner terminals as stale (simulates policy change behavior)
      for (const t of terminals) {
        if (t.transport === "runner" && t.status === "active") {
          t.status = "stale";
          t.stale_reason = `Policy changed (v${policyVersion}): added deny rule "${rule}"`;
        }
      }
    }
    sendJson(res, {
      kind: "workspace_panel_policy_change",
      change: {
        kind: "policy_change",
        action: "add",
        rule,
        changed: true,
        policy_version: policyVersion,
        execution_session_id: "exec-001",
        agent_session_id: "agent-001",
      },
      panel: desktopShellSnapshot().workspace_panel,
    });
    return;
  }

  // POST /api/policy/allow
  if (req.method === "POST" && url.pathname === "/api/policy/allow") {
    const body = await readBody(req);
    const rule = (body.rule || "").trim();
    const idx = denyGlobs.indexOf(rule);
    if (idx !== -1) {
      denyGlobs.splice(idx, 1);
      policyVersion++;
    }
    sendJson(res, {
      kind: "workspace_panel_policy_change",
      change: {
        kind: "policy_change",
        action: "remove",
        rule,
        changed: idx !== -1,
        policy_version: policyVersion,
        execution_session_id: "exec-001",
        agent_session_id: "agent-001",
      },
      panel: desktopShellSnapshot().workspace_panel,
    });
    return;
  }

  // POST /api/editor/save
  if (req.method === "POST" && url.pathname === "/api/editor/save") {
    const body = await readBody(req);
    fileContents[body.target] = body.content;
    sendJson(res, {
      kind: "editor_save",
      target: body.target,
      path: body.target,
      managed: true,
      byte_size: body.content.length,
      content: body.content,
      proposal: null,
      rendered: null,
    });
    return;
  }

  // POST /api/editor/stage
  if (req.method === "POST" && url.pathname === "/api/editor/stage") {
    const body = await readBody(req);
    const proposalId = `prop-${Date.now()}`;
    sendJson(res, {
      kind: "editor_stage",
      proposal: {
        proposal_id: proposalId,
        target: body.target,
        session_id: "exec-001",
        agent_session_id: "agent-001",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        status: "pending",
        base_sha256: null,
        base_text: fileContents[body.target] || "",
        proposed_text: body.content,
      },
      rendered: `--- ${body.target}\n+++ ${body.target} (staged)\n@@ change @@\n${body.content.slice(0, 200)}`,
    });
    return;
  }

  // POST /api/editor/apply, /api/editor/reject
  if (req.method === "POST" && (url.pathname === "/api/editor/apply" || url.pathname === "/api/editor/reject")) {
    const body = await readBody(req);
    const action = url.pathname.endsWith("/apply") ? "applied" : "rejected";
    sendJson(res, {
      kind: url.pathname.endsWith("/apply") ? "editor_apply" : "editor_reject",
      proposal: {
        proposal_id: body.proposal_id,
        target: ".",
        session_id: "exec-001",
        agent_session_id: "agent-001",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        status: action,
        base_sha256: null,
        base_text: null,
        proposed_text: "",
      },
      rendered: `[${action}]`,
    });
    return;
  }

  sendJson(res, { error: "Not found" }, 404);
});

server.listen(8765, "127.0.0.1", () => {
  console.log("Mock API server running at http://127.0.0.1:8765");
  console.log("Start the frontend with: cd desktop && npm run dev");
  console.log("");
  console.log("Test flow:");
  console.log("  1. Create terminals (Managed, Strict, Unsafe)");
  console.log("  2. Run commands in them");
  console.log("  3. Add a deny rule via the sidebar to trigger policy change");
  console.log("     -> Managed/Strict terminals will become STALE");
  console.log("  4. See stale banner, disabled input, Relaunch button");
  console.log("  5. Click Relaunch to create a new terminal");
});
