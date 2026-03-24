import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

type TerminalPaneProps = {
  terminalId: string;
  isActive: boolean;
  fontSize?: number;
};

type CachedTerminal = {
  term: Terminal;
  fitAddon: FitAddon;
  opened: boolean;
  unlisten: (() => void) | null;
  resizeTimeout: ReturnType<typeof setTimeout> | null;
};

// Cache terminal instances so they survive re-renders and tab switches
const terminalCache = new Map<string, CachedTerminal>();

export function TerminalPane({ terminalId, isActive, fontSize: fontSizeProp }: TerminalPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  // Setup terminal instance (once per terminalId)
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let cached = terminalCache.get(terminalId);

    if (!cached) {
      // Read CSS variables for theme consistency
      const styles = getComputedStyle(document.documentElement);
      const bg = styles.getPropertyValue("--bg-primary").trim() || "#1e1e2e";
      const fg = styles.getPropertyValue("--text-primary").trim() || "#cdd6f4";
      const sel = styles.getPropertyValue("--bg-surface").trim() || "#45475a";
      const accent = styles.getPropertyValue("--accent").trim() || "#89b4fa";

      const cssFontSize = styles.getPropertyValue("--terminal-font-size").trim();
      const fontSize = cssFontSize ? parseInt(cssFontSize) : 14;

      const term = new Terminal({
        cursorBlink: true,
        fontSize,
        fontFamily: "'Cascadia Code', 'Consolas', 'Courier New', monospace",
        theme: {
          background: bg,
          foreground: fg,
          cursor: accent,
          selectionBackground: sel,
        },
      });

      const fitAddon = new FitAddon();
      term.loadAddon(fitAddon);

      cached = { term, fitAddon, opened: false, unlisten: null, resizeTimeout: null };
      terminalCache.set(terminalId, cached);

      // Wire input: xterm → main process → PTY
      term.onData((data) => {
        window.electronAPI.terminalWrite(terminalId, data);
      });

      // Wire resize: xterm → main process → PTY (debounced)
      const cachedRef = cached;
      term.onResize(({ cols, rows }) => {
        if (cachedRef.resizeTimeout) clearTimeout(cachedRef.resizeTimeout);
        cachedRef.resizeTimeout = setTimeout(() => {
          cachedRef.resizeTimeout = null;
          window.electronAPI.terminalResize(terminalId, cols, rows);
        }, 150);
      });

      // Wire output: main process → xterm
      cached.unlisten = window.electronAPI.onTerminalData((id, data) => {
        if (id === terminalId) {
          term.write(data);
        }
      });
    }

    // Open terminal in DOM (only once)
    if (!cached.opened) {
      cached.term.open(container);
      cached.opened = true;
      cached.fitAddon.fit();
    } else {
      // Re-attach to DOM on tab switch
      if (cached.term.element && cached.term.element.parentElement !== container) {
        container.appendChild(cached.term.element);
      }
      cached.fitAddon.fit();
    }

    if (isActive) {
      cached.term.focus();
    }

    // Handle container resize
    const observer = new ResizeObserver(() => {
      const c = terminalCache.get(terminalId);
      if (c) {
        try {
          c.fitAddon.fit();
        } catch {
          // ignore fit errors during transitions
        }
      }
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
    };
  }, [terminalId, isActive]);

  // Apply font size changes to existing terminal instances
  useEffect(() => {
    if (!fontSizeProp) return;
    const cached = terminalCache.get(terminalId);
    if (cached) {
      cached.term.options.fontSize = fontSizeProp;
      try { cached.fitAddon.fit(); } catch {}
    }
  }, [fontSizeProp, terminalId]);

  return (
    <div
      ref={containerRef}
      className={`terminal-pane ${isActive ? "terminal-pane-active" : ""}`}
      style={{ width: "100%", height: "100%", overflow: "hidden" }}
    />
  );
}

// Cleanup function for when a terminal is destroyed
export function destroyTerminalCache(terminalId: string): void {
  const cached = terminalCache.get(terminalId);
  if (cached) {
    if (cached.unlisten) cached.unlisten();
    if (cached.resizeTimeout) clearTimeout(cached.resizeTimeout);
    cached.term.dispose();
    terminalCache.delete(terminalId);
  }
}
