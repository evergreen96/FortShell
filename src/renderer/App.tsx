import { useEffect, useState, useCallback, useRef } from "react";
import { destroyTerminalCache } from "./components/Terminal/TerminalPane";
import { TerminalWorkspace } from "./components/Layout/TerminalWorkspace";
import { FileTree } from "./components/FileTree/FileTree";
import { ProtectionCenter } from "./components/Protection/ProtectionCenter";
import { Welcome } from "./components/Welcome/Welcome";
import { Settings, type AppConfig } from "./components/Settings/Settings";
import type { TerminalLayoutMode } from "./lib/terminalLayout";
import type { ShellProfile } from "./lib/types";
import "./lib/types";
import "./styles/filetree.css";
import "./styles/welcome.css";
import "./styles/settings.css";

type TerminalInfo = {
  id: string;
  name: string;
  status: "active" | "exited";
  stalePolicy?: boolean;
};

const LAYOUT_MODES: TerminalLayoutMode[] = ["horizontal", "vertical", "grid"];
const LAYOUT_LABELS: Record<TerminalLayoutMode, string> = {
  horizontal: "Horizontal",
  vertical: "Vertical",
  grid: "Grid",
};

function ExplorerRailIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 7.75A1.75 1.75 0 0 1 4.75 6h4.12c.46 0 .9.18 1.22.5l1.16 1.16c.14.14.33.22.53.22h7.5A1.75 1.75 0 0 1 21 9.62v6.63A1.75 1.75 0 0 1 19.25 18H4.75A1.75 1.75 0 0 1 3 16.25z" />
    </svg>
  );
}

function ProtectionRailIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3.25 5.75 5.9v5.33c0 4.16 2.63 7.98 6.25 9.52 3.62-1.54 6.25-5.36 6.25-9.52V5.9z" />
      <path d="M12 8.2a1.8 1.8 0 0 1 1.8 1.8v.65h.2a.9.9 0 0 1 .9.9v2.95a.9.9 0 0 1-.9.9H10a.9.9 0 0 1-.9-.9v-2.95a.9.9 0 0 1 .9-.9h.2V10A1.8 1.8 0 0 1 12 8.2Zm0 1.3a.5.5 0 0 0-.5.5v.65h1V10a.5.5 0 0 0-.5-.5Z" />
    </svg>
  );
}

export function App() {
  const [terminals, setTerminals] = useState<TerminalInfo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ShellProfile[]>([]);
  const [editingTabId, setEditingTabId] = useState<string | null>(null);
  const [layoutMode, setLayoutMode] = useState<TerminalLayoutMode>("horizontal");
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [protectedPaths, setProtectedPaths] = useState<Set<string>>(new Set());
  const [sidebarVisible, setSidebarVisible] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(250);
  const [showSettings, setShowSettings] = useState(false);
  const [fontSize, setFontSize] = useState(14);
  const [sidebarTab, setSidebarTab] = useState<"explorer" | "protection">("explorer");
  const sidebarDragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  // Menu bar Settings toggle
  useEffect(() => {
    const unlisten = window.electronAPI.onToggleSettings(() => {
      setShowSettings((prev) => !prev);
    });
    return unlisten;
  }, []);

  useEffect(() => {
    window.electronAPI.terminalProfiles().then(setProfiles);
    // Load saved config
    window.electronAPI.configGet().then((c: any) => {
      if (c.fontSize) setFontSize(c.fontSize);
      if (c.sidebarWidth) setSidebarWidth(c.sidebarWidth);
      if (c.defaultLayout) setLayoutMode(c.defaultLayout);
    });
  }, []);

  useEffect(() => {
    const unlisten = window.electronAPI.onTerminalExit((id) => {
      setTerminals((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: "exited" as const } : t))
      );
    });
    return unlisten;
  }, []);

  useEffect(() => {
    if (workspacePath) {
      window.electronAPI.policyList().then((paths) => {
        setProtectedPaths(new Set(paths));
      });
    }
  }, [workspacePath]);

  // Listen for policy changes — mark existing terminals as stale
  useEffect(() => {
    const unlisten = window.electronAPI.onPolicyChanged(() => {
      setTerminals((prev) =>
        prev.map((t) => (t.status === "active" ? { ...t, stalePolicy: true } : t))
      );
      // Refresh protected paths list
      window.electronAPI.policyList().then((paths) => {
        setProtectedPaths(new Set(paths));
      });
    });
    return unlisten;
  }, []);

  async function openFolder() {
    const path = await window.electronAPI.openFolder();
    if (path) setWorkspacePath(path);
  }

  async function handleSelectRecent(path: string) {
    const resolvedPath = await window.electronAPI.workspaceSetRoot(path);
    setWorkspacePath(resolvedPath);
  }

  const createTerminal = useCallback(
    async (profileId?: string) => {
      const profile = profileId
        ? profiles.find((p) => p.id === profileId)
        : profiles.find((p) => p.isDefault) || profiles[0];

      const result = await window.electronAPI.terminalCreate({
        shell: profile?.command,
        cwd: workspacePath || undefined,
      });
      setTerminals((prev) => [
        ...prev,
        { id: result.id, name: result.name, status: "active" },
      ]);
      setActiveId(result.id);
    },
    [profiles, workspacePath]
  );

  async function destroyTerminal(id: string) {
    await window.electronAPI.terminalDestroy(id);
    destroyTerminalCache(id);
    setTerminals((prev) => {
      const next = prev.filter((t) => t.id !== id);
      if (activeId === id) {
        setActiveId(next.length > 0 ? next[next.length - 1].id : null);
      }
      return next;
    });
  }

  async function restartTerminal(id: string) {
    const terminal = terminals.find((t) => t.id === id);
    if (!terminal) return;
    await destroyTerminal(id);
    await createTerminal();
  }

  function handleConfigChange(config: AppConfig) {
    if (config.fontSize) setFontSize(config.fontSize);
    if (config.sidebarWidth) setSidebarWidth(config.sidebarWidth);
    if (config.defaultLayout) setLayoutMode(config.defaultLayout as TerminalLayoutMode);
    // Update CSS variable for terminals
    document.documentElement.style.setProperty("--terminal-font-size", `${config.fontSize || 14}px`);
  }

  async function handleProtect(filePath: string) {
    await window.electronAPI.policySet(filePath);
    // Refresh from engine to get realpath-resolved paths
    const paths = await window.electronAPI.policyList();
    setProtectedPaths(new Set(paths));
  }

  async function handleUnprotect(filePath: string) {
    await window.electronAPI.policyRemove(filePath);
    const paths = await window.electronAPI.policyList();
    setProtectedPaths(new Set(paths));
  }

  async function handleProtectMany(filePaths: string[]): Promise<number> {
    const uniquePaths = Array.from(new Set(filePaths));
    if (uniquePaths.length === 0) return 0;

    const results = await Promise.all(
      uniquePaths.map(async (filePath) => {
        try {
          return await window.electronAPI.policySet(filePath);
        } catch {
          return false;
        }
      })
    );

    const protectedCount = results.filter(Boolean).length;
    if (protectedCount > 0) {
      const paths = await window.electronAPI.policyList();
      setProtectedPaths(new Set(paths));
    }

    return protectedCount;
  }

  // Sidebar drag resize
  function handleSidebarDragStart(e: React.MouseEvent) {
    e.preventDefault();
    sidebarDragRef.current = { startX: e.clientX, startWidth: sidebarWidth };
    const onMove = (ev: MouseEvent) => {
      if (!sidebarDragRef.current) return;
      const delta = ev.clientX - sidebarDragRef.current.startX;
      const newWidth = Math.max(150, Math.min(600, sidebarDragRef.current.startWidth + delta));
      setSidebarWidth(newWidth);
    };
    const onUp = () => {
      sidebarDragRef.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // Keyboard shortcuts
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Ctrl+`: focus terminal
      if (e.ctrlKey && !e.shiftKey && e.key === "`") {
        e.preventDefault();
        const xterm = document.querySelector(".xterm-helper-textarea") as HTMLTextAreaElement | null;
        if (xterm) xterm.focus();
        return;
      }
      // Cmd+B (macOS) / Ctrl+B: toggle sidebar
      if ((e.metaKey || e.ctrlKey) && e.key === "b") {
        e.preventDefault();
        setSidebarVisible((prev) => !prev);
        return;
      }
      // Ctrl+Shift+`: new terminal
      if (e.ctrlKey && e.shiftKey && e.key === "`") {
        e.preventDefault();
        createTerminal();
        return;
      }
      // Ctrl+W: close active terminal
      if (e.ctrlKey && e.key === "w") {
        e.preventDefault();
        if (activeId) destroyTerminal(activeId);
        return;
      }
      // Ctrl+Tab: next terminal
      if (e.ctrlKey && e.key === "Tab") {
        e.preventDefault();
        if (terminals.length <= 1) return;
        const idx = terminals.findIndex((t) => t.id === activeId);
        const nextIdx = (idx + 1) % terminals.length;
        setActiveId(terminals[nextIdx].id);
        return;
      }
      // Cmd+, or Ctrl+,: toggle settings
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        setShowSettings((prev) => !prev);
        return;
      }
      // Ctrl+1/2/3: switch layout
      if (e.ctrlKey && ["1", "2", "3"].includes(e.key)) {
        e.preventDefault();
        const modeIdx = parseInt(e.key) - 1;
        if (modeIdx < LAYOUT_MODES.length) {
          setLayoutMode(LAYOUT_MODES[modeIdx]);
        }
        return;
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeId, terminals, createTerminal]);

  // Auto-create first terminal when workspace is set
  useEffect(() => {
    if (terminals.length === 0 && profiles.length > 0 && workspacePath) {
      createTerminal();
    }
  }, [profiles, workspacePath, createTerminal]);

  const workspaceName = workspacePath?.split(/[/\\]/).pop() || "workspace";
  const isExplorerView = sidebarTab === "explorer";
  const showExplorerPane = isExplorerView && sidebarVisible;

  // Welcome screen when no workspace is open
  if (!workspacePath) {
    return (
      <div className="app">
        <Welcome onOpenFolder={openFolder} onSelectRecent={handleSelectRecent} />
        {showSettings && (
          <Settings
            onClose={() => setShowSettings(false)}
            profiles={profiles}
            onConfigChange={handleConfigChange}
            onProfilesChanged={() => {
              window.electronAPI.terminalProfiles().then(setProfiles);
            }}
          />
        )}
      </div>
    );
  }

  return (
    <div className="app">
      <div className="app-header">
        <div className="app-toolbar">
          <div className="app-brand">
            <div className="app-brand-mark">FS</div>
            <div className="app-brand-copy">
              <h1>{window.electronAPI.appName}</h1>
              <p>terminal-first command shell</p>
            </div>
          </div>
          <div className="app-context">
            <span className="app-context-label">Workspace / Root</span>
            <span className="app-context-value" title={workspacePath}>
              {workspaceName}
            </span>
          </div>
          <div className="app-actions">
            {isExplorerView && terminals.length > 1 && (
              <div className="layout-toggle-group">
                {LAYOUT_MODES.map((mode) => (
                  <button
                    key={mode}
                    className={`layout-toggle ${layoutMode === mode ? "layout-toggle-active" : ""}`}
                    onClick={() => setLayoutMode(mode)}
                    title={`Layout: ${LAYOUT_LABELS[mode]}`}
                  >
                    {LAYOUT_LABELS[mode]}
                  </button>
                ))}
              </div>
            )}
            <button
              className="tab-new"
              onClick={() => {
                createTerminal();
              }}
            >
              + New Terminal
            </button>
          </div>
        </div>
        {isExplorerView && (
          <div className="tab-strip">
            <div className="tab-bar">
              {terminals.map((t) => (
                <div
                  key={t.id}
                  className={`tab ${t.id === activeId ? "tab-active" : ""} ${t.status === "exited" ? "tab-exited" : ""} ${t.stalePolicy ? "tab-stale" : ""}`}
                  onClick={() => setActiveId(t.id)}
                >
                  {t.stalePolicy && (
                    <span
                      className="tab-stale-icon"
                      title="Policy changed — restart to apply"
                      onClick={(e) => {
                        e.stopPropagation();
                        restartTerminal(t.id);
                      }}
                    >
                      &#x21bb;
                    </span>
                  )}
                  {editingTabId === t.id ? (
                    <input
                      className="tab-label-input"
                      defaultValue={t.name}
                      maxLength={50}
                      autoFocus
                      onBlur={(e) => {
                        const newName = e.target.value.trim() || t.name;
                        setTerminals((prev) =>
                          prev.map((term) =>
                            term.id === t.id ? { ...term, name: newName } : term
                          )
                        );
                        setEditingTabId(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.currentTarget.blur();
                        if (e.key === "Escape") {
                          e.currentTarget.value = t.name;
                          e.currentTarget.blur();
                        }
                      }}
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <span
                      className="tab-label"
                      onDoubleClick={(e) => {
                        e.stopPropagation();
                        setEditingTabId(t.id);
                      }}
                    >
                      {t.name}
                    </span>
                  )}
                  <span
                    className="tab-close"
                    onClick={(e) => {
                      e.stopPropagation();
                      destroyTerminal(t.id);
                    }}
                  >
                    &times;
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="app-body">
        <aside className="nav-rail" aria-label="Primary navigation">
          <button
            className={`nav-rail-button ${isExplorerView ? "nav-rail-button-active" : ""}`}
            title="Explorer"
            aria-label="Explorer"
            onClick={() => setSidebarTab("explorer")}
          >
            <ExplorerRailIcon />
          </button>
          <button
            className={`nav-rail-button ${!isExplorerView ? "nav-rail-button-active" : ""}`}
            title="Protection"
            aria-label="Protection"
            onClick={() => setSidebarTab("protection")}
          >
            <ProtectionRailIcon />
            {protectedPaths.size > 0 && (
              <span className="nav-rail-badge">{protectedPaths.size}</span>
            )}
          </button>
        </aside>
        {showExplorerPane && (
          <>
            <aside
              className="explorer-pane"
              style={{ width: sidebarWidth, minWidth: sidebarWidth }}
            >
              <FileTree
                rootPath={workspacePath}
                protectedPaths={protectedPaths}
                onProtect={handleProtect}
                onUnprotect={handleUnprotect}
                onOpenFolder={openFolder}
              />
            </aside>
            <div className="sidebar-resize-handle" onMouseDown={handleSidebarDragStart} />
          </>
        )}
        <main className={`content-area ${isExplorerView ? "" : "content-area-protection"}`}>
          {isExplorerView ? (
            <TerminalWorkspace
              terminals={terminals}
              activeId={activeId}
              layoutMode={layoutMode}
              onSelectTerminal={setActiveId}
              fontSize={fontSize}
            />
          ) : (
            <ProtectionCenter
              rootPath={workspacePath}
              protectedPaths={protectedPaths}
              onProtect={handleProtect}
              onProtectMany={handleProtectMany}
              onUnprotect={handleUnprotect}
            />
          )}
        </main>
      </div>
      {showSettings && (
        <Settings
          onClose={() => setShowSettings(false)}
          profiles={profiles}
          onConfigChange={handleConfigChange}
          onProfilesChanged={() => {
            window.electronAPI.terminalProfiles().then(setProfiles);
          }}
        />
      )}
    </div>
  );
}
