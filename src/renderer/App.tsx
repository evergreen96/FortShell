import { useEffect, useState, useCallback, useRef } from "react";
import {
  TerminalPane,
  destroyTerminalCache,
} from "./components/Terminal/TerminalPane";
import { TerminalWorkspace } from "./components/Layout/TerminalWorkspace";
import { FileTree } from "./components/FileTree/FileTree";
import { StatusBar } from "./components/StatusBar/StatusBar";
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
  horizontal: "\u2500",
  vertical: "\u2502",
  grid: "\u253C",
};

export function App() {
  const [terminals, setTerminals] = useState<TerminalInfo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ShellProfile[]>([]);
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const [editingTabId, setEditingTabId] = useState<string | null>(null);
  const [layoutMode, setLayoutMode] = useState<TerminalLayoutMode>("horizontal");
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [protectedPaths, setProtectedPaths] = useState<Set<string>>(new Set());
  const [sidebarVisible, setSidebarVisible] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(250);
  const [showSettings, setShowSettings] = useState(false);
  const [fontSize, setFontSize] = useState(14);
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
    await window.electronAPI.workspaceSetRoot(path);
    setWorkspacePath(path);
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
      setShowProfileMenu(false);
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

  const cycleLayout = useCallback(() => {
    setLayoutMode((prev) => {
      const idx = LAYOUT_MODES.indexOf(prev);
      return LAYOUT_MODES[(idx + 1) % LAYOUT_MODES.length];
    });
  }, []);

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
  }, [profiles, workspacePath]);

  const useSplitLayout = terminals.length > 1;

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
        <h1>{window.electronAPI.appName}</h1>
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
          <div className="tab-new-wrapper">
            <button
              className="tab-new"
              onClick={() => {
                if (profiles.length <= 1) {
                  createTerminal();
                } else {
                  setShowProfileMenu((prev) => !prev);
                }
              }}
            >
              + New
            </button>
            {showProfileMenu && (
              <div className="profile-menu">
                {profiles.map((p) => (
                  <button
                    key={p.id}
                    className="profile-menu-item"
                    onClick={() => createTerminal(p.id)}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          {terminals.length > 1 && (
            <button
              className="layout-toggle"
              onClick={cycleLayout}
              title={`Layout: ${layoutMode}`}
            >
              {LAYOUT_LABELS[layoutMode]}
            </button>
          )}
        </div>
      </div>
      <div className="app-body">
        {sidebarVisible && (
          <>
            <aside className="sidebar" style={{ width: sidebarWidth, minWidth: sidebarWidth }}>
              <FileTree
                rootPath={workspacePath}
                protectedPaths={protectedPaths}
                onProtect={handleProtect}
                onUnprotect={handleUnprotect}
                onOpenFolder={openFolder}
              />
            </aside>
            <div
              className="sidebar-resize-handle"
              onMouseDown={handleSidebarDragStart}
            />
          </>
        )}
        <main className="terminal-area">
          {useSplitLayout ? (
            <TerminalWorkspace
              terminals={terminals}
              activeId={activeId}
              layoutMode={layoutMode}
              onSelectTerminal={setActiveId}
              fontSize={fontSize}
            />
          ) : (
            terminals.map((t) => (
              <div
                key={t.id}
                style={{
                  display: t.id === activeId ? "flex" : "none",
                  width: "100%",
                  height: "100%",
                }}
              >
                <TerminalPane
                  terminalId={t.id}
                  isActive={t.id === activeId}
                  fontSize={fontSize}
                />
              </div>
            ))
          )}
        </main>
      </div>
      <StatusBar
        terminalCount={terminals.length}
        layoutMode={layoutMode}
        workspacePath={workspacePath}
        protectedCount={protectedPaths.size}
        platform={window.electronAPI.platform}
      />
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
