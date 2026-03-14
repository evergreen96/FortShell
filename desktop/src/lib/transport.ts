/**
 * Transport abstraction - isolates the frontend from HTTP vs Tauri IPC.
 *
 * In Tauri desktop mode:  invoke("sidecar_request") + listen()
 * In browser dev mode:    fetch() + EventSource (existing HTTP fallback)
 */

// ---------------------------------------------------------------------------
// API base URL
// ---------------------------------------------------------------------------

const DEFAULT_API_BASE = "http://127.0.0.1:8765";

export function resolveApiBase(): string {
  const configured = import.meta.env.VITE_AI_IDE_API_BASE?.trim();
  return configured && configured.length > 0 ? configured.replace(/\/+$/, "") : DEFAULT_API_BASE;
}

// ---------------------------------------------------------------------------
// Transport interface
// ---------------------------------------------------------------------------

export interface DesktopTransport {
  /** Send a request and wait for a response. */
  request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
  /** Subscribe to a push event. Returns an unsubscribe function. */
  listen(event: string, handler: (payload: unknown) => void): Promise<() => void>;
  /** Transport kind for debugging. */
  kind: "http" | "tauri";
}

// ---------------------------------------------------------------------------
// HTTP transport (browser dev / fallback)
// ---------------------------------------------------------------------------

/** Map sidecar method names to HTTP endpoint + verb. */
const HTTP_METHOD_MAP: Record<string, { path: string; verb: "GET" | "POST"; queryKey?: string }> = {
  "desktop_shell.snapshot": { path: "/api/desktop-shell", verb: "GET", queryKey: "target" },
  "workspace_panel.snapshot": { path: "/api/workspace-panel", verb: "GET", queryKey: "target" },
  "editor.file": { path: "/api/editor/file", verb: "GET", queryKey: "target" },
  "editor.save": { path: "/api/editor/save", verb: "POST" },
  "review.render": { path: "/api/review/render", verb: "GET", queryKey: "proposal_id" },
  "editor.stage": { path: "/api/editor/stage", verb: "POST" },
  "editor.apply": { path: "/api/editor/apply", verb: "POST" },
  "editor.reject": { path: "/api/editor/reject", verb: "POST" },
  "review.apply": { path: "/api/review/apply", verb: "POST" },
  "review.reject": { path: "/api/review/reject", verb: "POST" },
  "policy.deny": { path: "/api/policy/deny", verb: "POST" },
  "policy.allow": { path: "/api/policy/allow", verb: "POST" },
  "terminal.create": { path: "/api/terminal/create", verb: "POST" },
  "terminal.run": { path: "/api/terminal/run", verb: "POST" },
  "terminal.pty.write": { path: "/api/terminal/pty/write", verb: "POST" },
  "terminal.pty.resize": { path: "/api/terminal/pty/resize", verb: "POST" },
};

const DEFAULT_TIMEOUT_MS = 30_000;

function createHttpTransport(): DesktopTransport {
  const apiBase = resolveApiBase();

  async function request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
    const mapping = HTTP_METHOD_MAP[method];
    if (!mapping) {
      throw new Error(`Unknown method for HTTP transport: ${method}`);
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

    try {
      let url = `${apiBase}${mapping.path}`;
      const init: RequestInit = {
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
      };

      if (mapping.verb === "GET") {
        if (mapping.queryKey && params?.[mapping.queryKey] != null) {
          url += `?${mapping.queryKey}=${encodeURIComponent(String(params[mapping.queryKey]))}`;
        }
        init.method = "GET";
      } else {
        init.method = "POST";
        init.body = JSON.stringify(params ?? {});
      }

      const response = await fetch(url, init);
      const payload = await response.json();

      if (!response.ok) {
        const message =
          typeof payload === "object" && payload !== null && "error" in payload && typeof payload.error === "string"
            ? payload.error
            : `HTTP ${response.status}`;
        throw new Error(message);
      }
      return payload as T;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new Error(`Request timed out after ${DEFAULT_TIMEOUT_MS / 1000}s: ${method}`);
      }
      throw error;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async function listen(event: string, handler: (payload: unknown) => void): Promise<() => void> {
    // HTTP transport: PTY streaming uses EventSource
    if (event === "terminal.pty.data" || event === "terminal.pty.close") {
      // For HTTP, PTY events are handled via EventSource in TerminalPane directly.
      // This is a no-op here; the component manages its own EventSource.
      return () => {};
    }
    return () => {};
  }

  return { request, listen, kind: "http" };
}

// ---------------------------------------------------------------------------
// Tauri transport
// ---------------------------------------------------------------------------

async function createTauriTransport(): Promise<DesktopTransport> {
  const { invoke } = await import("@tauri-apps/api/core");
  const { listen: tauriListen } = await import("@tauri-apps/api/event");

  async function request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
    const result = await invoke<T>("sidecar_request", {
      method,
      params: params ?? {},
    });
    return result;
  }

  async function listen(event: string, handler: (payload: unknown) => void): Promise<() => void> {
    const unlisten = await tauriListen(event, (ev) => {
      handler(ev.payload);
    });
    return unlisten;
  }

  return { request, listen, kind: "tauri" };
}

// ---------------------------------------------------------------------------
// Transport detection & singleton
// ---------------------------------------------------------------------------

/** Detect whether we're running inside Tauri. */
function isTauriEnvironment(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

let _transport: DesktopTransport | null = null;

/** Get or create the singleton transport. */
export async function getTransport(): Promise<DesktopTransport> {
  if (_transport) return _transport;

  if (isTauriEnvironment()) {
    try {
      _transport = await createTauriTransport();
      console.log("[transport] Using Tauri transport");
    } catch (e) {
      // Fallback if Tauri APIs fail to load
      console.warn("[transport] Tauri detected but failed to load, falling back to HTTP:", e);
      _transport = createHttpTransport();
    }
  } else {
    console.log("[transport] Using HTTP transport (no Tauri detected)");
    _transport = createHttpTransport();
  }
  return _transport;
}

/** Synchronous access after initial setup (returns HTTP if not yet initialized). */
export function getTransportSync(): DesktopTransport {
  if (_transport) return _transport;
  _transport = createHttpTransport();
  return _transport;
}
