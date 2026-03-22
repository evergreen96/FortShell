import os from "os";
import { getSandboxedSpawnArgs } from "../../platform/index";
import type { PolicyEnforcer } from "../../platform/types";

function getPty(): typeof import("node-pty") {
  return require("node-pty");
}

export type PtySession = {
  id: string;
  name: string;
  ptyProcess: import("node-pty").IPty;
};

let nextId = 1;

export class PtyManager {
  private sessions = new Map<string, PtySession>();
  private enforcer: PolicyEnforcer | null = null;

  setEnforcer(enforcer: PolicyEnforcer): void {
    this.enforcer = enforcer;
  }

  getDefaultShell(): string {
    return process.env.SHELL || "/bin/zsh";
  }

  create(opts: {
    shell?: string;
    cols?: number;
    rows?: number;
    cwd?: string;
    webContents?: Electron.WebContents;
  }): { id: string; name: string } {
    const nodePty = getPty();
    const id = `term-${nextId++}`;
    const shell = opts.shell || this.getDefaultShell();
    const shellName = shell.split(/[/\\]/).pop() || shell;
    const cols = opts.cols || 80;
    const rows = opts.rows || 24;
    const cwd = opts.cwd || os.homedir();

    const baseOpts = {
      name: "xterm-256color",
      cols,
      rows,
      cwd,
      env: process.env as Record<string, string>,
    };

    // Try sandboxed spawn (macOS sandbox-exec, Linux Landlock)
    const hasEnforcer = !!this.enforcer;
    const sandboxArgs = this.enforcer
      ? getSandboxedSpawnArgs(shell, this.enforcer)
      : null;

    let ptyProcess: import("node-pty").IPty;
    if (sandboxArgs) {
      try {
        ptyProcess = nodePty.spawn(sandboxArgs.command, sandboxArgs.args, baseOpts);
        console.log(`[pty] Sandboxed: ${id} → ${sandboxArgs.command}`);
      } catch (err) {
        console.warn(`[pty] Sandbox spawn failed, falling back to plain shell:`, err);
        ptyProcess = nodePty.spawn(shell, [], baseOpts);
      }
    } else {
      ptyProcess = nodePty.spawn(shell, [], baseOpts);
      console.log(`[pty] Created: ${id}`);
    }

    const session: PtySession = { id, ptyProcess, name: `${shellName} (${id})` };
    this.sessions.set(id, session);

    if (opts.webContents) {
      ptyProcess.onData((data) => {
        if (!opts.webContents!.isDestroyed()) {
          opts.webContents!.send("terminal:data", id, data);
        }
      });
      ptyProcess.onExit(({ exitCode }) => {
        if (!opts.webContents!.isDestroyed()) {
          opts.webContents!.send("terminal:exit", id, exitCode);
        }
        this.sessions.delete(id);
      });
    }

    return { id, name: session.name };
  }

  write(id: string, data: string): void {
    this.sessions.get(id)?.ptyProcess.write(data);
  }

  resize(id: string, cols: number, rows: number): void {
    this.sessions.get(id)?.ptyProcess.resize(cols, rows);
  }

  destroy(id: string): boolean {
    const session = this.sessions.get(id);
    if (!session) return false;
    session.ptyProcess.kill();
    this.sessions.delete(id);
    return true;
  }

  destroyAll(): void {
    for (const [id] of this.sessions) {
      this.destroy(id);
    }
  }

  list(): Array<{ id: string; name: string }> {
    return Array.from(this.sessions.values()).map((s) => ({
      id: s.id,
      name: s.name,
    }));
  }
}
