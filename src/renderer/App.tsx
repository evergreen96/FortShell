import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { destroyTerminalCache } from "./components/Terminal/TerminalPane";
import { TerminalWorkspace } from "./components/Layout/TerminalWorkspace";
import { FileTree } from "./components/FileTree/FileTree";
import { ProtectionCenter } from "./components/Protection/ProtectionCenter";
import { Welcome } from "./components/Welcome/Welcome";
import { Settings, type AppConfig } from "./components/Settings/Settings";
import {
  getStaleSessions,
  type TerminalLayoutMode,
} from "./lib/terminalLayout";
import {
  applyTerminalReplacement,
  applyTerminalReplacements,
  reconcileActiveTerminalId,
  type TerminalTabState,
} from "./lib/terminalSessionState";
import type {
  CompiledProtectionEntry,
  PolicyChangedPayload,
  ProtectionMutationResult,
  ProtectionPreset,
  ProtectionRule,
  ShellProfile,
  TerminalSessionMeta,
  TerminalSessionReplacement,
  TerminalTrustState,
} from "./lib/types";
import {
  getProtectionRuleRemovalToastMessage,
  shouldApplyProtectionRefreshResult,
  shouldRefreshForPolicyChange,
} from "./lib/protection-refresh";
import { buildExplorerProtectedPathSet } from "./lib/protection-paths";
import "./lib/types";
import "./styles/filetree.css";
import "./styles/welcome.css";
import "./styles/settings.css";

type TerminalInfo = TerminalTabState;

type TrustBadgeTone = "accent" | "warning" | "danger" | "muted";

type TerminalView = TerminalInfo & {
  trustState?: TerminalTrustState;
  trustLabel?: string;
  trustTone?: TrustBadgeTone;
  trustTitle?: string;
};

type ToastState = {
  id: number;
  message: string;
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

function getTrustBadge(
  session: TerminalSessionMeta | undefined,
  status: TerminalInfo["status"],
): { label: string; tone: TrustBadgeTone; title: string } | null {
  if (status === "exited") {
    return {
      label: "exited",
      tone: "muted",
      title: "Session exited",
    };
  }

  if (!session) {
    return null;
  }

  switch (session.trustState) {
    case "protected":
      return {
        label: "protected",
        tone: "accent",
        title: "Protected session",
      };
    case "unprotected":
      return {
        label: "unprotected",
        tone: "muted",
        title: "Unprotected session",
      };
    case "stale-policy":
      return {
        label: "stale policy",
        tone: "warning",
        title: "Policy changed. Restart to apply the latest protection state.",
      };
    case "fallback":
      return {
        label: "fallback",
        tone: "warning",
        title: session.launchFailureReason
          ? `Protected launch fell back: ${session.launchFailureReason}`
          : "Protected launch fell back to a plain shell.",
      };
    case "launch-failed":
      return {
        label: "launch failed",
        tone: "danger",
        title: session.launchFailureReason
          ? `Protected launch failed: ${session.launchFailureReason}`
          : "Protected launch failed.",
      };
    case "exited":
      return {
        label: "exited",
        tone: "muted",
        title: "Session exited",
      };
    default:
      return null;
  }
}

export function App() {
  const [terminals, setTerminals] = useState<TerminalInfo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ShellProfile[]>([]);
  const [editingTabId, setEditingTabId] = useState<string | null>(null);
  const [layoutMode, setLayoutMode] = useState<TerminalLayoutMode>("horizontal");
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [protectionPresets, setProtectionPresets] = useState<ProtectionPreset[]>([]);
  const [protectionRules, setProtectionRules] = useState<ProtectionRule[]>([]);
  const [compiledProtections, setCompiledProtections] = useState<CompiledProtectionEntry[]>([]);
  const [focusedSourceRuleId, setFocusedSourceRuleId] = useState<string | null>(null);
  const [sidebarVisible, setSidebarVisible] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(250);
  const [showSettings, setShowSettings] = useState(false);
  const [fontSize, setFontSize] = useState(14);
  const [sidebarTab, setSidebarTab] = useState<"explorer" | "protection">("explorer");
  const [sessionState, setSessionState] = useState<{
    sessions: TerminalSessionMeta[];
    policyRevision: number;
  }>({
    sessions: [],
    policyRevision: 0,
  });
  const [toast, setToast] = useState<ToastState | null>(null);
  const sidebarDragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const nextLayoutSlotRef = useRef(1);
  const toastTimeoutRef = useRef<number | null>(null);
  const bootstrappedWorkspaceRef = useRef<string | null>(null);
  const latestProtectionRefreshRequestRef = useRef(0);
  const workspacePathRef = useRef<string | null>(null);

  const createLayoutSlotKey = useCallback(() => {
    const nextValue = nextLayoutSlotRef.current++;
    return `slot-${nextValue}`;
  }, []);

  const showToast = useCallback((message: string) => {
    if (toastTimeoutRef.current !== null) {
      window.clearTimeout(toastTimeoutRef.current);
    }

    setToast({
      id: Date.now(),
      message,
    });

    toastTimeoutRef.current = window.setTimeout(() => {
      setToast(null);
      toastTimeoutRef.current = null;
    }, 2200);
  }, []);

  useEffect(() => {
    workspacePathRef.current = workspacePath;
  }, [workspacePath]);

  useEffect(() => {
    return () => {
      if (toastTimeoutRef.current !== null) {
        window.clearTimeout(toastTimeoutRef.current);
      }
    };
  }, []);

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
    const unlisten = window.electronAPI.onTerminalSessionState((payload) => {
      setSessionState(payload);
    });
    return unlisten;
  }, []);

  const refreshProtectionState = useCallback(async () => {
    const requestedWorkspacePath = workspacePath;
    const requestId = latestProtectionRefreshRequestRef.current + 1;
    latestProtectionRefreshRequestRef.current = requestId;

    if (!requestedWorkspacePath) {
      if (
        shouldApplyProtectionRefreshResult({
          requestedWorkspacePath,
          currentWorkspacePath: workspacePathRef.current,
          requestId,
          latestRequestId: latestProtectionRefreshRequestRef.current,
        })
      ) {
        setProtectionPresets([]);
        setProtectionRules([]);
        setCompiledProtections([]);
      }
      return;
    }

    const [presets, rules, compiled] = await Promise.all([
      window.electronAPI.protectionListPresets(),
      window.electronAPI.protectionListRules(),
      window.electronAPI.protectionListCompiled(),
    ]);

    if (
      !shouldApplyProtectionRefreshResult({
        requestedWorkspacePath,
        currentWorkspacePath: workspacePathRef.current,
        requestId,
        latestRequestId: latestProtectionRefreshRequestRef.current,
      })
    ) {
      return;
    }

    setProtectionPresets(presets);
    setProtectionRules(rules);
    setCompiledProtections(compiled);
  }, [workspacePath]);

  useEffect(() => {
    setTerminals((prev) => {
      const next = [...prev];
      let changed = false;

      for (let index = 0; index < next.length; index += 1) {
        const terminal = next[index];
        const session = sessionState.sessions.find(
          (candidate) => candidate.terminalId === terminal.id
        );

        if (!session || terminal.status === "active") {
          continue;
        }

        next[index] = {
          ...terminal,
          status: "active",
        };
        changed = true;
      }

      return changed ? next : prev;
    });
  }, [sessionState.sessions]);

  useEffect(() => {
    refreshProtectionState();
  }, [refreshProtectionState]);

  // Listen for policy changes and refresh protection state shown in the tree and console.
  useEffect(() => {
    const unlisten = window.electronAPI.onPolicyChanged((payload: PolicyChangedPayload) => {
      if (
        shouldRefreshForPolicyChange({
          eventWorkspacePath: payload.workspacePath,
          currentWorkspacePath: workspacePathRef.current,
        })
      ) {
        refreshProtectionState();
      }
    });
    return unlisten;
  }, [refreshProtectionState]);

  async function openFolder() {
    const path = await window.electronAPI.openFolder();
    if (path) {
      setWorkspacePath(path);
      setFocusedSourceRuleId(null);
    }
  }

  async function handleSelectRecent(path: string) {
    const resolvedPath = await window.electronAPI.workspaceSetRoot(path);
    setWorkspacePath(resolvedPath);
    setFocusedSourceRuleId(null);
  }

  const createTerminal = useCallback(
    async (profileId?: string, slotKey?: string) => {
      const profile = profileId
        ? profiles.find((p) => p.id === profileId)
        : profiles.find((p) => p.isDefault) || profiles[0];
      const nextSlotKey = slotKey ?? createLayoutSlotKey();

      const result = await window.electronAPI.terminalCreate({
        shell: profile?.command,
        cwd: workspacePath || undefined,
        layoutSlotKey: nextSlotKey,
      });
      setTerminals((prev) =>
        prev.some((terminal) => terminal.id === result.id)
          ? prev
          : [
              ...prev,
              { id: result.id, name: result.name, status: "active", slotKey: nextSlotKey },
            ]
      );
      setActiveId(result.id);
      return result.id;
    },
    [createLayoutSlotKey, profiles, workspacePath]
  );

  const removeTerminalLocally = useCallback((id: string) => {
    destroyTerminalCache(id);
    setTerminals((prev) => {
      const next = prev.filter((terminal) => terminal.id !== id);
      setActiveId((currentActiveId) => {
        if (currentActiveId !== id) {
          return currentActiveId;
        }
        return next.length > 0 ? next[next.length - 1].id : null;
      });
      return next;
    });
  }, []);

  const applyReplacement = useCallback((replacement: TerminalSessionReplacement) => {
    destroyTerminalCache(replacement.oldTerminalId);
    setTerminals((prev) => applyTerminalReplacement(prev, replacement));
    setActiveId((currentActiveId) =>
      reconcileActiveTerminalId(currentActiveId, [replacement])
    );
  }, []);

  const applyReplacementBatch = useCallback((replacements: TerminalSessionReplacement[]) => {
    for (const replacement of replacements) {
      destroyTerminalCache(replacement.oldTerminalId);
    }

    setTerminals((prev) => applyTerminalReplacements(prev, replacements));
    setActiveId((currentActiveId) => {
      return reconcileActiveTerminalId(currentActiveId, replacements);
    });
  }, []);

  async function destroyTerminal(id: string) {
    await window.electronAPI.terminalDestroy(id);
    removeTerminalLocally(id);
  }

  async function restartTerminal(id: string) {
    const result = await window.electronAPI.terminalRestart(id);
    if (result.ok && result.replacement) {
      applyReplacement(result.replacement);
      showToast("Session restarted");
      return;
    }

    showToast("Restart unavailable");
  }

  async function restartAllStaleSessions() {
    const result = await window.electronAPI.terminalRestartAllStale();
    if (result.replacements.length > 0) {
      applyReplacementBatch(result.replacements);
      const label = result.replacements.length === 1 ? "session" : "sessions";
      showToast(`Restarted ${result.replacements.length} stale ${label}`);
      return;
    }

    showToast("No stale sessions");
  }

  async function retryProtectedSession(id: string) {
    const result = await window.electronAPI.terminalRetryProtected(id);
    if (result.ok && result.replacement) {
      applyReplacement(result.replacement);
      showToast("Protected retry started");
      return;
    }

    showToast("Retry unavailable");
  }

  async function closeFailedSession(id: string) {
    const result = await window.electronAPI.terminalCloseFailed(id);
    if (result.closed) {
      removeTerminalLocally(id);
      showToast("Failed session closed");
      return;
    }

    showToast("Close unavailable");
  }

  function handleConfigChange(config: AppConfig) {
    if (config.fontSize) setFontSize(config.fontSize);
    if (config.sidebarWidth) setSidebarWidth(config.sidebarWidth);
    if (config.defaultLayout) setLayoutMode(config.defaultLayout as TerminalLayoutMode);
    // Update CSS variable for terminals
    document.documentElement.style.setProperty("--terminal-font-size", `${config.fontSize || 14}px`);
  }

  async function handleProtect(filePath: string) {
    const changed = await window.electronAPI.policySet(filePath);
    await refreshProtectionState();
    showToast(changed ? "Path protected" : "Path already protected");
  }

  async function handleUnprotect(filePath: string) {
    const changed = await window.electronAPI.policyRemove(filePath);
    await refreshProtectionState();
    showToast(changed ? "Path unprotected" : "Path was not protected");
  }

  async function handleApplyPreset(
    presetId: Parameters<typeof window.electronAPI.protectionApplyPreset>[0]
  ): Promise<ProtectionMutationResult> {
    const result = await window.electronAPI.protectionApplyPreset(presetId);
    await refreshProtectionState();
    showToast(result.changed ? "Preset applied" : "Preset already applied");
    return result;
  }

  async function handleAddExtensionRule(
    extensions: string[]
  ): Promise<ProtectionMutationResult> {
    const result = await window.electronAPI.protectionAddExtensionRule(extensions);
    await refreshProtectionState();
    showToast(result.changed ? "Batch rule added" : "Batch rule already active");
    return result;
  }

  async function handleAddManualRule(targetPath: string): Promise<boolean> {
    const changed = await window.electronAPI.policySet(targetPath);
    await refreshProtectionState();
    showToast(changed ? "Direct rule added" : "Path already protected");
    return changed;
  }

  async function handleRemoveProtectionRule(ruleId: string): Promise<boolean> {
    try {
      const removed = await window.electronAPI.protectionRemoveRule(ruleId);
      await refreshProtectionState();
      showToast(getProtectionRuleRemovalToastMessage({ removed }));
      return removed;
    } catch (error) {
      await refreshProtectionState();
      showToast(getProtectionRuleRemovalToastMessage({ error }));
      throw error;
    }
  }

  const handleViewProtection = useCallback((ruleId: string) => {
    setSidebarTab("protection");
    setFocusedSourceRuleId(ruleId);
  }, []);

  function handleFocusSource(ruleId: string) {
    setFocusedSourceRuleId(null);
    window.setTimeout(() => {
      setFocusedSourceRuleId(ruleId);
    }, 0);
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
    if (!workspacePath) {
      bootstrappedWorkspaceRef.current = null;
      return;
    }

    if (bootstrappedWorkspaceRef.current === workspacePath || profiles.length === 0) {
      return;
    }

    bootstrappedWorkspaceRef.current = workspacePath;

    if (terminals.length === 0) {
      createTerminal();
    }
  }, [profiles, workspacePath, createTerminal]);

  const workspaceName = workspacePath?.split(/[/\\]/).pop() || "workspace";
  const isExplorerView = sidebarTab === "explorer";
  const showExplorerPane = isExplorerView && sidebarVisible;
  const sessionStateById = new Map<string, TerminalSessionMeta>(
    sessionState.sessions.map((session) => [session.terminalId, session])
  );
  const staleSessions = getStaleSessions(sessionState.sessions);
  const terminalViews: TerminalView[] = terminals.map((terminal) => {
    const session = sessionStateById.get(terminal.id);
    const badge = getTrustBadge(session, terminal.status);

    return {
      ...terminal,
      trustState: session?.trustState ?? (terminal.status === "exited" ? "exited" : undefined),
      trustLabel: badge?.label,
      trustTone: badge?.tone,
      trustTitle: badge?.title,
    };
  });
  const explorerProtectedPaths = useMemo(
    () => buildExplorerProtectedPathSet(compiledProtections),
    [compiledProtections]
  );

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
            {isExplorerView && (
              <button
                className="app-header-action app-header-action-warning"
                disabled={staleSessions.length === 0}
                onClick={() => {
                  restartAllStaleSessions();
                }}
                title={
                  staleSessions.length > 0
                    ? `Restart ${staleSessions.length} stale protected session${staleSessions.length === 1 ? "" : "s"}`
                    : "No stale sessions"
                }
              >
                Restart All Stale Sessions
              </button>
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
              {terminalViews.map((t) => (
                <div
                  key={t.id}
                  className={`tab ${t.id === activeId ? "tab-active" : ""} ${t.status === "exited" ? "tab-exited" : ""} ${t.trustTone ? `tab-trust-${t.trustTone}` : ""}`}
                  onClick={() => setActiveId(t.id)}
                >
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
                  {t.trustLabel && (
                    <span
                      className={`tab-trust-badge tab-trust-badge-${t.trustTone ?? "muted"}`}
                      title={t.trustTitle}
                    >
                      {t.trustLabel}
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
            {compiledProtections.length > 0 && (
              <span className="nav-rail-badge">{compiledProtections.length}</span>
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
                protectedPaths={explorerProtectedPaths}
                compiledEntries={compiledProtections}
                onProtect={handleProtect}
                onUnprotect={handleUnprotect}
                onViewProtection={handleViewProtection}
                onOpenFolder={openFolder}
              />
            </aside>
            <div className="sidebar-resize-handle" onMouseDown={handleSidebarDragStart} />
          </>
        )}
        <main className={`content-area ${isExplorerView ? "" : "content-area-protection"}`}>
          {isExplorerView ? (
            <TerminalWorkspace
              terminals={terminalViews}
              activeId={activeId}
              layoutMode={layoutMode}
              onSelectTerminal={setActiveId}
              onRestartTerminal={restartTerminal}
              onRetryProtected={retryProtectedSession}
              onCloseFailed={closeFailedSession}
              fontSize={fontSize}
            />
          ) : (
            <ProtectionCenter
              rootPath={workspacePath}
              presets={protectionPresets}
              rules={protectionRules}
              compiledEntries={compiledProtections}
              focusedSourceRuleId={focusedSourceRuleId}
              onApplyPreset={handleApplyPreset}
              onAddExtensionRule={handleAddExtensionRule}
              onAddManualPath={handleAddManualRule}
              onRemoveRule={handleRemoveProtectionRule}
              onFocusSource={handleFocusSource}
              onClearFocusedSource={() => setFocusedSourceRuleId(null)}
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
      {toast && (
        <div className="app-toast-region">
          <div className="app-toast">{toast.message}</div>
        </div>
      )}
    </div>
  );
}
