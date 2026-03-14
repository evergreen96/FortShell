import { useEffect, useRef, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import type { TerminalInspection } from "../lib/types";
import { writePty, resizePty, connectPtyStream, getTransportSync } from "../lib/api";

type TerminalPaneProps = {
  terminals: TerminalInspection[];
  selectedTerminal: TerminalInspection | null;
  terminalCommand: string;
  terminalTranscript: string[];
  onSelectTerminal: (terminalId: string) => void;
  onTerminalCommandChange: (value: string) => void;
  onCreateManaged: () => void;
  onCreateStrict: () => void;
  onCreateUnsafe: () => void;
  onCreatePty: () => void;
  onRunSelected: () => void;
  onRelaunchTerminal: (terminal: TerminalInspection) => void;
  onRelaunchAllManaged: () => void;
};

// ANSI escape helpers
const ANSI = {
  blue: (text: string) => `\x1b[38;2;121;188;255m${text}\x1b[0m`,
  yellow: (text: string) => `\x1b[33m${text}\x1b[0m`,
} as const;

type XtermCacheEntry = {
  term: Terminal;
  fit: FitAddon;
  lastLineCount: number;
  staleMarked: boolean;
  sseSource: EventSource | null;
  tauriUnlistenData: (() => void) | null;
  tauriUnlistenClose: (() => void) | null;
  onDataDisposable: { dispose: () => void } | null;
  onResizeDisposable: { dispose: () => void } | null;
};

/** Per-terminal xterm instance cache - survives re-renders, preserves scroll history */
const xtermCache = new Map<string, XtermCacheEntry>();

/** Clean up PTY-specific resources for a cache entry */
function cleanupPtyConnections(entry: XtermCacheEntry): void {
  if (entry.sseSource) {
    entry.sseSource.close();
    entry.sseSource = null;
  }
  if (entry.tauriUnlistenData) {
    entry.tauriUnlistenData();
    entry.tauriUnlistenData = null;
  }
  if (entry.tauriUnlistenClose) {
    entry.tauriUnlistenClose();
    entry.tauriUnlistenClose = null;
  }
  if (entry.onDataDisposable) {
    entry.onDataDisposable.dispose();
    entry.onDataDisposable = null;
  }
  if (entry.onResizeDisposable) {
    entry.onResizeDisposable.dispose();
    entry.onResizeDisposable = null;
  }
}

export function TerminalPane({
  terminals,
  selectedTerminal,
  terminalCommand,
  terminalTranscript,
  onSelectTerminal,
  onTerminalCommandChange,
  onCreateManaged,
  onCreateStrict,
  onCreateUnsafe,
  onCreatePty,
  onRunSelected,
  onRelaunchTerminal,
  onRelaunchAllManaged,
}: TerminalPaneProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const isStale = selectedTerminal !== null && selectedTerminal.status !== "active";
  const isPty = selectedTerminal?.io_mode === "pty";
  const hasStaleManagedTerminals = terminals.some(
    (t) => t.status !== "active" && t.transport !== "host",
  );

  // Clean up xterm cache entries for terminals that no longer exist
  useEffect(() => {
    const activeIds = new Set(terminals.map((t) => t.terminal_id));
    for (const [id, cached] of xtermCache) {
      if (!activeIds.has(id)) {
        cleanupPtyConnections(cached);
        cached.term.dispose();
        xtermCache.delete(id);
      }
    }
  }, [terminals]);

  // Attach / detach xterm when selectedTerminal changes
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !selectedTerminal) return;

    const terminalId = selectedTerminal.terminal_id;
    let cached = xtermCache.get(terminalId);

    if (!cached) {
      const term = new Terminal({
        theme: {
          background: "#010204",
          foreground: "#e0e8f2",
          cursor: "#79bcff",
          selectionBackground: "rgba(58, 134, 255, 0.3)",
          black: "#1a1e24",
          red: "#ff6b6b",
          green: "#00d676",
          yellow: "#ffb000",
          blue: "#79bcff",
          magenta: "#c084fc",
          cyan: "#67efb1",
          white: "#e0e8f2",
        },
        fontFamily: "'Cascadia Mono', 'Consolas', 'SFMono-Regular', monospace",
        fontSize: 13,
        lineHeight: 1.4,
        cursorBlink: true,
        scrollback: 5000,
        convertEol: selectedTerminal.io_mode !== "pty", // PTY sends its own CR/LF
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      cached = {
        term,
        fit,
        lastLineCount: 0,
        staleMarked: false,
        sseSource: null,
        tauriUnlistenData: null,
        tauriUnlistenClose: null,
        onDataDisposable: null,
        onResizeDisposable: null,
      };
      xtermCache.set(terminalId, cached);
    }

    // Clear container and attach
    container.innerHTML = "";
    cached.term.open(container);
    cached.fit.fit();

    // If this terminal is stale and hasn't been marked yet, write stale marker
    const terminalData = terminals.find((t) => t.terminal_id === terminalId);
    if (terminalData && terminalData.status !== "active" && !cached.staleMarked) {
      cached.term.writeln("");
      cached.term.writeln(ANSI.yellow("--- Terminal stale (read-only) ---"));
      if (terminalData.stale_reason) {
        cached.term.writeln(ANSI.yellow(terminalData.stale_reason));
      }
      cached.staleMarked = true;
    }

    // Set up PTY mode connections
    if (selectedTerminal.io_mode === "pty" && selectedTerminal.status === "active") {
      setupPtyConnections(cached, terminalId);
    }

    const observer = new ResizeObserver(() => {
      cached!.fit.fit();
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
    };
  }, [selectedTerminal?.terminal_id, terminals]);

  // Write new transcript lines into xterm (command mode only)
  useEffect(() => {
    if (!selectedTerminal || selectedTerminal.io_mode === "pty") return;
    const cached = xtermCache.get(selectedTerminal.terminal_id);
    if (!cached) return;

    const newLines = terminalTranscript.slice(cached.lastLineCount);
    for (const line of newLines) {
      // First line is the command prompt
      if (cached.lastLineCount === 0 && terminalTranscript.indexOf(line) === 0) {
        cached.term.writeln(ANSI.blue(line));
      } else {
        cached.term.writeln(line);
      }
    }
    cached.lastLineCount = terminalTranscript.length;
  }, [selectedTerminal?.terminal_id, terminalTranscript]);

  const handleSubmit = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      if (!isStale && !isPty) {
        // Reset line count so next output writes fresh
        if (selectedTerminal) {
          const cached = xtermCache.get(selectedTerminal.terminal_id);
          if (cached) cached.lastLineCount = 0;
        }
        onRunSelected();
      }
    },
    [isStale, isPty, selectedTerminal, onRunSelected],
  );

  return (
    <section className="panel terminal-panel">
      <header className="panel-header">
        <div>
          <p className="panel-kicker">Terminal</p>
          <h2>Multi Terminal</h2>
        </div>
        <div className="terminal-actions">
          {hasStaleManagedTerminals ? (
            <button type="button" className="relaunch-all-button" onClick={onRelaunchAllManaged}>
              Relaunch all managed
            </button>
          ) : null}
          <button type="button" className="secondary-button" onClick={onCreateManaged}>
            + Managed
          </button>
          <button type="button" className="secondary-button" onClick={onCreateStrict}>
            + Strict
          </button>
          <button type="button" className="secondary-button" onClick={onCreateUnsafe}>
            + Unfiltered
          </button>
          <button type="button" className="secondary-button" onClick={onCreatePty}>
            + PTY
          </button>
        </div>
      </header>

      <div className="terminal-tabs">
        {terminals.map((terminal) => (
          <button
            key={terminal.terminal_id}
            type="button"
            className={`terminal-tab ${terminalTone(terminal)} ${
              terminal.terminal_id === selectedTerminal?.terminal_id ? "terminal-tab-active" : ""
            }`}
            onClick={() => onSelectTerminal(terminal.terminal_id)}
          >
            <span className="terminal-dot" />
            <span>{terminal.name}</span>
            {terminal.status !== "active" ? (
              <span className="terminal-stale-badge">stale</span>
            ) : null}
            <small>{terminalMode(terminal)}</small>
          </button>
        ))}
        {terminals.length === 0 ? <div className="empty-card">No terminals yet.</div> : null}
      </div>

      {selectedTerminal ? (
        <>
          <div className="terminal-meta">
            <span>Status: {selectedTerminal.status}</span>
            <span>Transport: {selectedTerminal.transport}</span>
            <span>I/O: {selectedTerminal.io_mode}</span>
            <span>Session: {selectedTerminal.execution_session_id ?? "(host)"}</span>
            <span>History: {selectedTerminal.command_history.length}</span>
          </div>

          {isStale ? (
            <div className="terminal-stale-banner">
              <div className="terminal-stale-info">
                <strong>Terminal stale</strong>
                <span>{selectedTerminal.stale_reason ?? "Policy changed - this terminal is read-only."}</span>
              </div>
              <button
                type="button"
                className="relaunch-button"
                onClick={() => onRelaunchTerminal(selectedTerminal)}
              >
                Relaunch
              </button>
            </div>
          ) : null}

          {/* Command input form - hidden for PTY terminals */}
          {!isPty ? (
            <form className="terminal-command-form" onSubmit={handleSubmit}>
              <input
                type="text"
                value={terminalCommand}
                onChange={(event) => onTerminalCommandChange(event.target.value)}
                placeholder={isStale ? "Terminal is stale - relaunch to continue" : "Type a command and press Enter"}
                disabled={isStale}
              />
              <button type="submit" className="primary-button" disabled={isStale}>
                Run
              </button>
            </form>
          ) : null}

          <div className="xterm-container" ref={containerRef} />
        </>
      ) : (
        <div className="empty-card">Create a terminal to start the multi-terminal workspace.</div>
      )}
    </section>
  );
}

/** Decode base64 PTY data and write to xterm */
function writePtyChunk(cached: XtermCacheEntry, b64: string): void {
  try {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    cached.term.write(bytes);
  } catch {
    cached.term.write(b64);
  }
}

/** Wire up SSE or Tauri events + xterm onData/onResize for a PTY terminal */
function setupPtyConnections(cached: XtermCacheEntry, terminalId: string): void {
  // Avoid duplicate connections
  if (cached.sseSource || cached.tauriUnlistenData) return;

  const transport = getTransportSync();

  if (transport.kind === "tauri") {
    // Tauri mode: listen for push events from the Rust sidecar reader
    transport.listen("terminal.pty.data", (payload: unknown) => {
      const p = payload as { terminal_id?: string; data_b64?: string };
      if (p.terminal_id === terminalId && p.data_b64) {
        writePtyChunk(cached, p.data_b64);
      }
    }).then((unlisten) => { cached.tauriUnlistenData = unlisten; });

    transport.listen("terminal.pty.close", (payload: unknown) => {
      const p = payload as { terminal_id?: string };
      if (p.terminal_id === terminalId) {
        cached.term.writeln("");
        cached.term.writeln(ANSI.yellow("--- PTY session ended ---"));
        cleanupPtyConnections(cached);
      }
    }).then((unlisten) => { cached.tauriUnlistenClose = unlisten; });
  } else {
    // HTTP mode: use EventSource (SSE)
    const source = connectPtyStream(terminalId);
    cached.sseSource = source;

    source.onmessage = (event) => {
      writePtyChunk(cached, event.data);
    };

    source.addEventListener("close", () => {
      cached.term.writeln("");
      cached.term.writeln(ANSI.yellow("--- PTY session ended ---"));
      cleanupPtyConnections(cached);
    });

    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        cleanupPtyConnections(cached);
      }
    };
  }

  // xterm onData: keyboard input - PTY write
  cached.onDataDisposable = cached.term.onData((data) => {
    writePty({ terminal_id: terminalId, data }).catch(() => {});
  });

  // xterm onResize: terminal resize - PTY resize
  cached.onResizeDisposable = cached.term.onResize(({ cols, rows }) => {
    resizePty({ terminal_id: terminalId, cols, rows }).catch(() => {});
  });

  // Focus the terminal for immediate keyboard input
  cached.term.focus();
}

function terminalMode(terminal: TerminalInspection): string {
  if (terminal.io_mode === "pty") {
    return "pty";
  }
  if (terminal.transport === "host") {
    return "unfiltered";
  }
  return terminal.runner_mode ?? terminal.transport;
}

function terminalTone(terminal: TerminalInspection): string {
  if (terminal.transport === "host") {
    return "terminal-tab-unsafe";
  }
  if (terminal.status !== "active") {
    return "terminal-tab-stale";
  }
  return "terminal-tab-managed";
}
