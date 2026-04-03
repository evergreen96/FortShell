import os from "os";
import { getSandboxedSpawnArgs } from "../../platform/index";
import type { PolicyEnforcer } from "../../platform/types";
import {
  createSessionRuntime,
  markLaunchFallback,
  markLaunchFailed,
  markPolicyRevisionChanged,
  type TerminalSessionRuntime,
} from "./session-runtime";

function getPty(): typeof import("node-pty") {
  return require("node-pty");
}

export type TerminalCreateOptions = {
  shell?: string;
  cols?: number;
  rows?: number;
  cwd?: string;
  displayName?: string;
  layoutSlotKey?: string;
  webContents?: Electron.WebContents;
};

export type PtySession = {
  id: string;
  name: string;
  ptyProcess: import("node-pty").IPty | null;
  runtime: TerminalSessionRuntime;
  lastCreateOptions: TerminalCreateOptions;
};

export type TerminalSessionReplacement = {
  oldTerminalId: string;
  newTerminalId: string;
  displayName: string;
  layoutSlotKey?: string;
};

export type TerminalSessionActionResult = {
  ok: boolean;
  replacement?: TerminalSessionReplacement;
  reason?: string;
};

export type TerminalBulkRestartResult = {
  replacements: TerminalSessionReplacement[];
  skippedTerminalIds: string[];
};

export type TerminalCloseFailedResult = {
  closed: boolean;
  terminalId: string;
  reason?: string;
};

let nextId = 1;

export class PtyManager {
  private sessions = new Map<string, PtySession>();
  private enforcer: PolicyEnforcer | null = null;
  private policyRevision = 0;
  private readonly sessionStateListeners = new Set<() => void>();

  setEnforcer(enforcer: PolicyEnforcer): void {
    this.enforcer = enforcer;
  }

  setPolicyRevision(policyRevision: number): void {
    this.policyRevision = policyRevision;
  }

  getSessions(): TerminalSessionRuntime[] {
    return Array.from(this.sessions.values()).map((session) => session.runtime);
  }

  getSession(id: string): PtySession | undefined {
    return this.sessions.get(id);
  }

  onSessionStateChanged(listener: () => void): () => void {
    this.sessionStateListeners.add(listener);
    return () => {
      this.sessionStateListeners.delete(listener);
    };
  }

  markPolicyRevisionChanged(policyRevision: number): void {
    this.policyRevision = policyRevision;

    for (const session of this.sessions.values()) {
      session.runtime = markPolicyRevisionChanged(session.runtime, policyRevision);
    }

    this.notifySessionStateChanged();
  }

  getDefaultShell(): string {
    return process.env.SHELL || "/bin/zsh";
  }

  create(opts: TerminalCreateOptions): { id: string; name: string } {
    const nodePty = getPty();
    const id = `term-${nextId++}`;
    const shell = opts.shell || this.getDefaultShell();
    const shellName = shell.split(/[/\\]/).pop() || shell;
    const displayName = opts.displayName || `${shellName} (${id})`;
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

    let ptyProcess: import("node-pty").IPty | null = null;
    let runtime = createSessionRuntime({
      terminalId: id,
      displayName,
      shell: shellName,
      policyRevision: this.policyRevision,
      launchMode: "sandboxed",
      layoutSlotKey: opts.layoutSlotKey,
    });

    if (sandboxArgs) {
      try {
        ptyProcess = nodePty.spawn(sandboxArgs.command, sandboxArgs.args, baseOpts);
        console.log(`[pty] Sandboxed: ${id} → ${sandboxArgs.command}`);
      } catch (err) {
        console.warn(`[pty] Sandbox spawn failed, falling back to plain shell:`, err);
        runtime = markLaunchFallback(
          runtime,
          "sandbox spawn failed",
          err instanceof Error ? err.message : String(err)
        );

        try {
          ptyProcess = nodePty.spawn(shell, ["-l"], baseOpts);
        } catch (fallbackErr) {
          runtime = markLaunchFailed({
            terminalId: id,
            displayName,
            shell: shellName,
            policyRevision: this.policyRevision,
            launchFailureReason: "plain shell spawn failed",
            launchFailureDetail:
              fallbackErr instanceof Error ? fallbackErr.message : String(fallbackErr),
          });
        }
      }
    } else {
      runtime = markLaunchFallback(
        runtime,
        this.enforcer ? "sandbox unavailable" : "policy enforcement unavailable"
      );

      try {
        ptyProcess = nodePty.spawn(shell, ["-l"], baseOpts);
        console.log(`[pty] Created: ${id}`);
      } catch (err) {
        runtime = markLaunchFailed({
          terminalId: id,
          displayName,
          shell: shellName,
          policyRevision: this.policyRevision,
          launchFailureReason: "plain shell spawn failed",
          launchFailureDetail: err instanceof Error ? err.message : String(err),
        });
      }
    }

    const session: PtySession = {
      id,
      ptyProcess,
      name: displayName,
      runtime,
      lastCreateOptions: { ...opts },
    };
    this.sessions.set(id, session);
    this.notifySessionStateChanged();

    if (opts.webContents && ptyProcess) {
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
        this.notifySessionStateChanged();
      });
    }

    return { id, name: session.name };
  }

  write(id: string, data: string): void {
    this.sessions.get(id)?.ptyProcess?.write(data);
  }

  resize(id: string, cols: number, rows: number): void {
    this.sessions.get(id)?.ptyProcess?.resize(cols, rows);
  }

  destroy(id: string): boolean {
    const session = this.sessions.get(id);
    if (!session) return false;
    session.ptyProcess?.kill();
    this.sessions.delete(id);
    this.notifySessionStateChanged();
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

  restart(id: string): TerminalSessionActionResult {
    const session = this.sessions.get(id);
    if (!session) {
      return { ok: false, reason: "session-not-found" };
    }

    return this.replaceSession(session);
  }

  restartAllStale(): TerminalBulkRestartResult {
    const staleSessionIds = Array.from(this.sessions.values())
      .filter((session) => session.runtime.trustState === "stale-policy")
      .map((session) => session.id);

    const replacements: TerminalSessionReplacement[] = [];
    const skippedTerminalIds: string[] = [];

    for (const id of staleSessionIds) {
      const result = this.restart(id);
      if (result.ok && result.replacement) {
        replacements.push(result.replacement);
      } else {
        skippedTerminalIds.push(id);
      }
    }

    return { replacements, skippedTerminalIds };
  }

  retryProtected(id: string): TerminalSessionActionResult {
    const session = this.sessions.get(id);
    if (!session) {
      return { ok: false, reason: "session-not-found" };
    }

    if (
      session.runtime.trustState !== "fallback" &&
      session.runtime.trustState !== "launch-failed"
    ) {
      return { ok: false, reason: "session-not-retryable" };
    }

    return this.replaceSession(session);
  }

  closeFailed(id: string): TerminalCloseFailedResult {
    const session = this.sessions.get(id);
    if (!session) {
      return { closed: false, terminalId: id, reason: "session-not-found" };
    }

    if (session.runtime.trustState !== "launch-failed") {
      return { closed: false, terminalId: id, reason: "session-not-closeable" };
    }

    return {
      closed: this.destroy(id),
      terminalId: id,
    };
  }

  private notifySessionStateChanged(): void {
    for (const listener of this.sessionStateListeners) {
      listener();
    }
  }

  private replaceSession(session: PtySession): TerminalSessionActionResult {
    const oldTerminalId = session.id;
    const nextLayoutSlotKey =
      session.runtime.layoutSlotKey ??
      session.lastCreateOptions.layoutSlotKey ??
      oldTerminalId;

    const nextCreateOptions: TerminalCreateOptions = {
      ...session.lastCreateOptions,
      displayName: session.name,
      layoutSlotKey: nextLayoutSlotKey,
    };

    this.destroy(oldTerminalId);
    const replacement = this.create(nextCreateOptions);

    return {
      ok: true,
      replacement: {
        oldTerminalId,
        newTerminalId: replacement.id,
        displayName: replacement.name,
        layoutSlotKey: nextLayoutSlotKey,
      },
    };
  }
}
