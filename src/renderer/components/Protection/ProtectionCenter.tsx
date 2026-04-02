import { useEffect, useMemo, useState } from "react";
import type { WorkspaceSearchResult } from "../../lib/types";

type ProtectionCenterProps = {
  rootPath: string;
  protectedPaths: Set<string>;
  onProtect: (filePath: string) => Promise<void>;
  onProtectMany: (filePaths: string[]) => Promise<number>;
  onUnprotect: (filePath: string) => void;
};

type ProtectionItem = {
  filePath: string;
  relativePath: string;
  scope: "workspace" | "external";
  isDirectory: boolean;
};

function toRelativePath(rootPath: string, targetPath: string): string {
  const normalizedRoot = rootPath.replace(/\\/g, "/").replace(/\/+$/, "");
  const normalizedTarget = targetPath.replace(/\\/g, "/");

  if (normalizedTarget === normalizedRoot) return ".";
  if (!normalizedTarget.startsWith(`${normalizedRoot}/`)) {
    return targetPath;
  }
  return normalizedTarget.slice(normalizedRoot.length + 1);
}

function isPathCovered(targetPath: string, protectedPaths: Set<string>): boolean {
  if (protectedPaths.has(targetPath)) return true;
  for (const filePath of protectedPaths) {
    if (targetPath.startsWith(`${filePath}/`) || filePath.startsWith(`${targetPath}/`)) {
      return true;
    }
  }
  return false;
}

function normalizeExtensions(input: string): string[] {
  return Array.from(
    new Set(
      input
        .split(",")
        .map((token) => token.trim().toLowerCase())
        .filter(Boolean)
        .map((token) => (token.startsWith(".") ? token : `.${token}`))
    )
  );
}

function buildItems(
  rootPath: string,
  protectedPaths: Set<string>,
  typeMap: Map<string, boolean>
): ProtectionItem[] {
  return Array.from(protectedPaths)
    .map((filePath) => {
      const relativePath = toRelativePath(rootPath, filePath);
      return {
        filePath,
        relativePath,
        scope: relativePath === filePath ? "external" : "workspace",
        isDirectory: typeMap.get(filePath) ?? false,
      };
    })
    .sort((a, b) => a.relativePath.localeCompare(b.relativePath));
}

export function ProtectionCenter({
  rootPath,
  protectedPaths,
  onProtect,
  onProtectMany,
  onUnprotect,
}: ProtectionCenterProps) {
  const [extensionInput, setExtensionInput] = useState(".env, .json");
  const [batchFeedback, setBatchFeedback] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchFeedback, setSearchFeedback] = useState("");
  const [searchResults, setSearchResults] = useState<WorkspaceSearchResult[]>([]);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [loadingBatch, setLoadingBatch] = useState(false);
  const [typeMap, setTypeMap] = useState<Map<string, boolean>>(new Map());

  useEffect(() => {
    let disposed = false;

    if (protectedPaths.size === 0) {
      setTypeMap(new Map());
      return () => {
        disposed = true;
      };
    }

    window.electronAPI
      .workspaceDescribe(Array.from(protectedPaths))
      .then((entries) => {
        if (disposed) return;
        setTypeMap(new Map(entries.map((entry) => [entry.path, entry.isDirectory])));
      });

    return () => {
      disposed = true;
    };
  }, [protectedPaths]);

  useEffect(() => {
    let disposed = false;
    const trimmed = searchQuery.trim();

    if (!trimmed) {
      setSearchResults([]);
      setLoadingSearch(false);
      return () => {
        disposed = true;
      };
    }

    setLoadingSearch(true);
    const timeoutId = window.setTimeout(() => {
      window.electronAPI
        .workspaceSearch(rootPath, {
          query: trimmed,
          includeDirectories: true,
          limit: 10,
        })
        .then((results) => {
          if (disposed) return;
          setSearchResults(results.filter((entry) => !isPathCovered(entry.path, protectedPaths)));
          setLoadingSearch(false);
        })
        .catch(() => {
          if (disposed) return;
          setSearchResults([]);
          setLoadingSearch(false);
        });
    }, 120);

    return () => {
      disposed = true;
      window.clearTimeout(timeoutId);
    };
  }, [protectedPaths, rootPath, searchQuery]);

  const items = useMemo(
    () => buildItems(rootPath, protectedPaths, typeMap),
    [protectedPaths, rootPath, typeMap]
  );
  const workspaceItems = items.filter((item) => item.scope === "workspace").length;
  const externalItems = items.length - workspaceItems;
  const rootName = rootPath.split(/[/\\]/).pop() || rootPath;
  const visibleResults = searchQuery.trim() ? searchResults : [];

  async function handleBatchProtect() {
    const extensions = normalizeExtensions(extensionInput);
    if (extensions.length === 0) {
      setBatchFeedback("Enter one or more extensions such as .env or .json.");
      return;
    }

    setLoadingBatch(true);
    setBatchFeedback("");
    try {
      const matches = await window.electronAPI.workspaceSearch(rootPath, {
        extensions,
        includeDirectories: false,
        limit: 500,
      });
      const candidates = matches
        .map((entry) => entry.path)
        .filter((filePath) => !isPathCovered(filePath, protectedPaths));
      const protectedCount = await onProtectMany(candidates);

      if (protectedCount === 0) {
        setBatchFeedback("No new matching files were found.");
      } else {
        setBatchFeedback(`Protected ${protectedCount} matching file${protectedCount === 1 ? "" : "s"}.`);
      }
    } catch {
      setBatchFeedback("Batch protection failed.");
    } finally {
      setLoadingBatch(false);
    }
  }

  async function handleProtectEntry(entry: WorkspaceSearchResult) {
    await onProtect(entry.path);
    setSearchQuery("");
    setSearchResults([]);
    setSearchFeedback(`Protected ${entry.relativePath}.`);
  }

  return (
    <div className="protection-center">
      <div className="protection-center-scroll">
        <section className="protection-hero">
          <div className="protection-hero-title-row">
            <div className="protection-hero-accent"></div>
            <div className="protection-hero-copy">
              <h2>Tactical Protection Center</h2>
              <p>
                Manage active file shielding for <strong>{rootName}</strong>.
                FortShell stores policy in app data and enforces access at the OS layer.
              </p>
            </div>
          </div>
        </section>

        <section className="protection-summary-grid">
          <article className="protection-summary-card">
            <span className="protection-summary-label">Protected Paths</span>
            <strong>{items.length}</strong>
          </article>
          <article className="protection-summary-card">
            <span className="protection-summary-label">Workspace Paths</span>
            <strong>{workspaceItems}</strong>
          </article>
          <article className="protection-summary-card">
            <span className="protection-summary-label">External Paths</span>
            <strong>{externalItems}</strong>
          </article>
          <article className="protection-summary-card">
            <span className="protection-summary-label">Kernel Status</span>
            <strong>{items.length > 0 ? "Locked" : "Ready"}</strong>
          </article>
        </section>

        <section className="protection-action-grid">
          <article className="protection-action-card protection-action-card-batch">
            <div className="protection-card-heading">
              <h3>Batch Protection by Extension</h3>
              <span>Automated shielding policy</span>
            </div>
            <div className="protection-form-block">
              <label className="protection-form-label" htmlFor="protection-extension-input">
                Target Extensions
              </label>
              <input
                id="protection-extension-input"
                className="protection-input"
                value={extensionInput}
                onChange={(event) => setExtensionInput(event.target.value)}
                placeholder=".env, .config, .json"
              />
            </div>
            <button
              className="protection-primary-action"
              onClick={handleBatchProtect}
              disabled={loadingBatch}
            >
              {loadingBatch ? "Scanning..." : "Protect All"}
            </button>
            <p className="protection-feedback">{batchFeedback || "Apply protection to matching files inside this workspace."}</p>
          </article>

          <article className="protection-action-card protection-action-card-manual">
            <div className="protection-card-heading">
              <h3>Add New Protection</h3>
              <span>Manual resource locking</span>
            </div>
            <div className="protection-form-block">
              <label className="protection-form-label" htmlFor="protection-search-input">
                Search Resources
              </label>
              <input
                id="protection-search-input"
                className="protection-input"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search files or folders to protect"
              />
            </div>
            <div className="protection-search-results">
              {visibleResults.length === 0 ? (
                <div className="protection-empty-search">
                  {loadingSearch
                    ? "Searching workspace..."
                    : searchQuery.trim()
                      ? "No matching paths found."
                      : "Search for a file or folder to protect."}
                </div>
              ) : (
                visibleResults.map((entry) => (
                  <button
                    key={entry.path}
                    className="protection-search-item"
                    onClick={() => handleProtectEntry(entry)}
                  >
                    <span className="protection-search-item-icon">
                      {entry.isDirectory ? "DIR" : "FILE"}
                    </span>
                    <span className="protection-search-item-copy">
                      <span>{entry.relativePath}</span>
                      <span>{entry.path}</span>
                    </span>
                    <span className="protection-search-item-action">ADD</span>
                  </button>
                ))
              )}
            </div>
            <p className="protection-feedback">
              {searchFeedback ||
                "Search across the workspace and add files or folders directly from here."}
            </p>
          </article>
        </section>

        <section className="protection-table-shell">
          <div className="protection-table-header">
            <div>
              <h3>Active Protection List</h3>
              <p>{items.length} live shield{items.length === 1 ? "" : "s"}</p>
            </div>
          </div>

          {items.length === 0 ? (
            <div className="protection-table-empty">
              <strong>No protected paths yet</strong>
              <span>Use Explorer to protect a file or folder. It will appear here immediately.</span>
            </div>
          ) : (
            <div className="protection-table-wrap">
              <table className="protection-table">
                <thead>
                  <tr>
                    <th>Resource Path</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th className="protection-table-actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.filePath}>
                      <td>
                        <div className="protection-path-cell">
                          <span className="protection-path-lock">LOCK</span>
                          <div className="protection-path-copy">
                            <span className="protection-path-primary">{item.relativePath}</span>
                            <span className="protection-path-secondary" title={item.filePath}>
                              {item.filePath}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className="protection-scope-pill">
                          {item.isDirectory ? "Folder" : "File"}
                        </span>
                      </td>
                      <td>
                        <span className="protection-status-pill">Shielded</span>
                      </td>
                      <td className="protection-table-actions">
                        <button
                          className="protection-table-remove"
                          onClick={() => onUnprotect(item.filePath)}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
