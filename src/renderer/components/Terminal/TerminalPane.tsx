import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  createBufferedTerminalTarget,
  flushBufferedTerminalTarget,
  routeTerminalOutput,
} from "./terminalOutputBuffer";

type TerminalPaneProps = {
  terminalId: string;
  isActive: boolean;
  fontSize?: number;
};

type CachedTerminal = {
  term: Terminal;
  fitAddon: FitAddon;
  opened: boolean;
  write: (data: string) => void;
  resizeTimeout: ReturnType<typeof setTimeout> | null;
  observerFitTimeout: ReturnType<typeof setTimeout> | null;
  fitFrame: number | null;
  deferredFit: boolean;
  pendingOutput: string;
};

// Cache terminal instances so they survive re-renders and tab switches
const terminalCache = new Map<string, CachedTerminal>();
const WINDOW_RESIZE_END_EVENT = "fortshell:window-resize-end";
let terminalDataUnlisten: (() => void) | null = null;

function isWindowResizing(): boolean {
  return document.body.classList.contains("is-window-resizing");
}

function scheduleFit(cached: CachedTerminal, options: { force?: boolean } = {}): void {
  if (!options.force && isWindowResizing()) {
    cached.deferredFit = true;
    return;
  }

  cached.deferredFit = false;
  if (cached.observerFitTimeout) {
    clearTimeout(cached.observerFitTimeout);
    cached.observerFitTimeout = null;
  }
  if (cached.fitFrame !== null) {
    cancelAnimationFrame(cached.fitFrame);
  }

  cached.fitFrame = requestAnimationFrame(() => {
    cached.fitFrame = requestAnimationFrame(() => {
      cached.fitFrame = null;
      try {
        cached.fitAddon.fit();
      } catch {
        // Ignore fit errors when the pane is temporarily detached or mid-layout.
      }
    });
  });
}

function flushPendingOutput(cached: CachedTerminal): void {
  flushBufferedTerminalTarget(cached);
}

function ensureTerminalDataListener(): void {
  if (terminalDataUnlisten || typeof window === "undefined") {
    return;
  }

  terminalDataUnlisten = window.electronAPI.onTerminalData((id, data) => {
    const cached = terminalCache.get(id);

    routeTerminalOutput({
      terminalId: id,
      data,
      target: cached,
      windowResizing: isWindowResizing(),
    });
  });
}

export function TerminalPane({ terminalId, isActive, fontSize: fontSizeProp }: TerminalPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  // Setup terminal instance (once per terminalId)
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let cached = terminalCache.get(terminalId);

    if (!cached) {
      ensureTerminalDataListener();

      // Read CSS variables for theme consistency
      const styles = getComputedStyle(document.documentElement);
      const bg = styles.getPropertyValue("--terminal-bg").trim() || "#0a0e14";
      const fg = styles.getPropertyValue("--terminal-fg").trim() || "#dfe2eb";
      const sel = styles.getPropertyValue("--terminal-selection").trim() || "rgba(0, 218, 243, 0.18)";
      const accent = styles.getPropertyValue("--accent").trim() || "#00daf3";
      const black = styles.getPropertyValue("--terminal-black").trim() || "#181c22";
      const brightBlack = styles.getPropertyValue("--terminal-bright-black").trim() || "#3c494c";
      const red = styles.getPropertyValue("--terminal-red").trim() || "#ff978c";
      const green = styles.getPropertyValue("--terminal-green").trim() || "#7be0aa";
      const yellow = styles.getPropertyValue("--terminal-yellow").trim() || "#ffd799";
      const blue = styles.getPropertyValue("--terminal-blue").trim() || "#67d6ff";
      const magenta = styles.getPropertyValue("--terminal-magenta").trim() || "#c5b7ff";
      const cyan = styles.getPropertyValue("--terminal-cyan").trim() || "#00daf3";
      const white = styles.getPropertyValue("--terminal-white").trim() || "#dfe2eb";
      const brightWhite = styles.getPropertyValue("--terminal-bright-white").trim() || "#f5f7fb";

      const cssFontSize = styles.getPropertyValue("--terminal-font-size").trim();
      const fontSize = cssFontSize ? parseInt(cssFontSize) : 14;
      const fontFamily =
        styles.getPropertyValue("--font-terminal").trim() ||
        "'JetBrains Mono', 'SF Mono', Menlo, Monaco, Consolas, monospace";

      const term = new Terminal({
        cursorBlink: true,
        fontSize,
        fontFamily,
        theme: {
          background: bg,
          foreground: fg,
          cursor: accent,
          selectionBackground: sel,
          black,
          brightBlack,
          red,
          brightRed: red,
          green,
          brightGreen: green,
          yellow,
          brightYellow: yellow,
          blue,
          brightBlue: blue,
          magenta,
          brightMagenta: magenta,
          cyan,
          brightCyan: cyan,
          white,
          brightWhite,
        },
      });

      const fitAddon = new FitAddon();
      term.loadAddon(fitAddon);

      cached = {
        ...createBufferedTerminalTarget((data) => {
          term.write(data);
        }),
        term,
        fitAddon,
        opened: false,
        resizeTimeout: null,
        observerFitTimeout: null,
        fitFrame: null,
        deferredFit: false,
      };
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
    }

    // Open terminal in DOM (only once)
    if (!cached.opened) {
      cached.term.open(container);
      cached.opened = true;
      flushPendingOutput(cached);
      scheduleFit(cached, { force: true });
    } else {
      // Re-attach to DOM on tab switch
      if (cached.term.element && cached.term.element.parentElement !== container) {
        container.appendChild(cached.term.element);
      }
      scheduleFit(cached, { force: true });
    }

    if (isActive) {
      cached.term.focus();
    }

    // Handle container resize
    const observer = new ResizeObserver(() => {
      const c = terminalCache.get(terminalId);
      if (!c) return;

      if (isWindowResizing()) {
        c.deferredFit = true;
        return;
      }

      if (c.observerFitTimeout) clearTimeout(c.observerFitTimeout);
      c.observerFitTimeout = setTimeout(() => {
        c.observerFitTimeout = null;
        scheduleFit(c);
      }, 32);
    });
    observer.observe(container);

    const handleWindowResizeEnd = () => {
      const c = terminalCache.get(terminalId);
      if (!c) return;

      flushPendingOutput(c);
      if (c.deferredFit) {
        scheduleFit(c, { force: true });
      }
    };
    window.addEventListener(WINDOW_RESIZE_END_EVENT, handleWindowResizeEnd);

    return () => {
      observer.disconnect();
      window.removeEventListener(WINDOW_RESIZE_END_EVENT, handleWindowResizeEnd);
      if (cached?.observerFitTimeout) {
        clearTimeout(cached.observerFitTimeout);
        cached.observerFitTimeout = null;
      }
      if (cached?.fitFrame !== null) {
        cancelAnimationFrame(cached.fitFrame);
        cached.fitFrame = null;
      }
    };
  }, [terminalId, isActive]);

  // Apply font size changes to existing terminal instances
  useEffect(() => {
    if (!fontSizeProp) return;
    const cached = terminalCache.get(terminalId);
    if (cached) {
      cached.term.options.fontSize = fontSizeProp;
      scheduleFit(cached, { force: true });
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
    if (cached.resizeTimeout) clearTimeout(cached.resizeTimeout);
    if (cached.observerFitTimeout) clearTimeout(cached.observerFitTimeout);
    if (cached.fitFrame !== null) cancelAnimationFrame(cached.fitFrame);
    cached.term.dispose();
    terminalCache.delete(terminalId);
  }
}
