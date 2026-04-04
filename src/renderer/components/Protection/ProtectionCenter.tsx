import { useEffect, useMemo, useRef, useState } from "react";
import type {
  CompiledProtectionEntry,
  ProtectionMutationResult,
  ProtectionPreset,
  ProtectionPresetId,
  ProtectionRule,
  WorkspaceSearchResult,
} from "../../lib/types";

type ProtectionCenterProps = {
  rootPath: string;
  presets: ProtectionPreset[];
  rules: ProtectionRule[];
  compiledEntries: CompiledProtectionEntry[];
  focusedSourceRuleId: string | null;
  onApplyPreset: (presetId: ProtectionPresetId) => Promise<ProtectionMutationResult>;
  onAddExtensionRule: (extensions: string[]) => Promise<ProtectionMutationResult>;
  onAddManualPath: (targetPath: string) => Promise<boolean>;
  onRemoveRule: (ruleId: string) => Promise<boolean>;
  onFocusSource: (ruleId: string) => void;
  onClearFocusedSource: () => void;
};

export function getProtectionAction(
  entry: CompiledProtectionEntry
): "remove" | "view-source" {
  return entry.canRemoveDirectly ? "remove" : "view-source";
}

type BlockedSearchEntry = {
  relativePath: string;
  sourceLabel: string;
  reason: "duplicate" | "contained";
};

export function getRemovalImpactMessage(affectedPathCount: number): string {
  return `Remove this rule? It will unshield ${affectedPathCount} concrete path${affectedPathCount === 1 ? "" : "s"}.`;
}

export function getBlockedSearchSummary(
  blockedEntries: readonly BlockedSearchEntry[]
): string {
  if (blockedEntries.length === 0) {
    return "";
  }

  const details = blockedEntries.map((entry) => {
    const reasonText =
      entry.reason === "contained" ? "is already covered by" : "is already protected by";
    return `${entry.relativePath} ${reasonText} ${entry.sourceLabel}.`;
  });

  return `Blocked: ${details.join(" ")}`;
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
  ).sort();
}

function normalizePath(targetPath: string): string {
  return targetPath.replace(/\\/g, "/").replace(/\/+$/, "");
}

function isCoveredByDirectory(parentPath: string, childPath: string): boolean {
  const normalizedParent = normalizePath(parentPath);
  const normalizedChild = normalizePath(childPath);
  if (normalizedParent === normalizedChild) {
    return true;
  }
  return normalizedChild.startsWith(`${normalizedParent}/`);
}

function getCoveringEntry(
  targetPath: string,
  compiledEntries: readonly CompiledProtectionEntry[]
): CompiledProtectionEntry | null {
  const normalizedTarget = normalizePath(targetPath);

  for (const entry of compiledEntries) {
    const normalizedEntryPath = normalizePath(entry.path);
    if (normalizedEntryPath === normalizedTarget) {
      return entry;
    }
  }

  for (const entry of compiledEntries) {
    if (entry.type !== "folder") {
      continue;
    }
    if (isCoveredByDirectory(entry.path, normalizedTarget)) {
      return entry;
    }
  }

  return null;
}

function getBlockedReason(
  targetPath: string,
  coveringEntry: CompiledProtectionEntry
): "duplicate" | "contained" {
  return normalizePath(targetPath) === normalizePath(coveringEntry.path)
    ? "duplicate"
    : "contained";
}

function getPresetFeedback(
  label: string,
  result: ProtectionMutationResult
): string {
  if (result.changed) {
    return `${label} preset applied to this workspace.`;
  }

  if (result.reason === "already-exists") {
    return `${label} is already applied.`;
  }

  return `Could not apply ${label}.`;
}

function getExtensionRuleFeedback(
  extensions: string[],
  result: ProtectionMutationResult
): string {
  const label = extensions.join(", ");
  if (result.changed) {
    return `${label} rule added. Matching files will stay shielded as the workspace changes.`;
  }

  if (result.reason === "already-exists") {
    return `${label} rule is already active.`;
  }

  if (result.reason === "invalid-extension") {
    return "Enter one or more extensions such as .env, .pem, or .key.";
  }

  return `Could not add ${label} rule.`;
}

function getRuleBadgeLabel(rule: ProtectionRule): string {
  if (rule.kind === "preset") {
    return "Preset";
  }

  if (rule.kind === "extension") {
    return "Batch Rule";
  }

  return rule.kind === "directory" ? "Folder Rule" : "Path Rule";
}

function getRuleDetail(rule: ProtectionRule, presets: readonly ProtectionPreset[]): string {
  if (rule.kind === "preset") {
    return (
      presets.find((preset) => preset.id === rule.presetId)?.description ?? "Built-in preset"
    );
  }

  if (rule.kind === "extension") {
    return rule.extensions.join(", ");
  }

  if (rule.targetPath === ".") {
    return "Workspace root";
  }

  return rule.targetPath;
}

export function ProtectionCenter({
  rootPath,
  presets,
  rules,
  compiledEntries,
  focusedSourceRuleId,
  onApplyPreset,
  onAddExtensionRule,
  onAddManualPath,
  onRemoveRule,
  onFocusSource,
  onClearFocusedSource,
}: ProtectionCenterProps) {
  const [extensionInput, setExtensionInput] = useState(".env, .pem");
  const [presetFeedback, setPresetFeedback] = useState("");
  const [batchFeedback, setBatchFeedback] = useState("");
  const [manualFeedback, setManualFeedback] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<WorkspaceSearchResult[]>([]);
  const [blockedSearchEntries, setBlockedSearchEntries] = useState<BlockedSearchEntry[]>([]);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [pendingPresetId, setPendingPresetId] = useState<ProtectionPresetId | null>(null);
  const [pendingRuleId, setPendingRuleId] = useState<string | null>(null);
  const [pendingManualPath, setPendingManualPath] = useState<string | null>(null);
  const [pendingBatchAdd, setPendingBatchAdd] = useState(false);
  const [confirmingRuleId, setConfirmingRuleId] = useState<string | null>(null);
  const sourceRuleRefs = useRef(new Map<string, HTMLElement>());

  const presetRules = useMemo(
    () =>
      new Map(
        rules
          .filter((rule): rule is Extract<ProtectionRule, { kind: "preset" }> => rule.kind === "preset")
          .map((rule) => [rule.presetId, rule])
      ),
    [rules]
  );
  const extensionRules = useMemo(
    () =>
      rules.filter(
        (rule): rule is Extract<ProtectionRule, { kind: "extension" }> => rule.kind === "extension"
      ),
    [rules]
  );
  const compiledCountByRuleId = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of compiledEntries) {
      counts.set(entry.sourceRuleId, (counts.get(entry.sourceRuleId) ?? 0) + 1);
    }
    return counts;
  }, [compiledEntries]);
  const workspaceName = rootPath.split(/[/\\]/).pop() || rootPath;

  useEffect(() => {
    if (!focusedSourceRuleId) {
      return;
    }

    const target = sourceRuleRefs.current.get(focusedSourceRuleId);
    if (!target) {
      return;
    }

    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
  }, [focusedSourceRuleId, rules]);

  useEffect(() => {
    let disposed = false;
    const trimmed = searchQuery.trim();

    if (!trimmed) {
      setSearchResults([]);
      setBlockedSearchEntries([]);
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
          if (disposed) {
            return;
          }

          const visibleResults: WorkspaceSearchResult[] = [];
          const blockedEntries: BlockedSearchEntry[] = [];

          for (const entry of results) {
            const coveringEntry = getCoveringEntry(entry.path, compiledEntries);
            if (coveringEntry) {
              blockedEntries.push({
                relativePath: entry.relativePath,
                sourceLabel: coveringEntry.sourceLabel,
                reason: getBlockedReason(entry.path, coveringEntry),
              });
              continue;
            }
            visibleResults.push(entry);
          }

          setSearchResults(visibleResults);
          setBlockedSearchEntries(blockedEntries);
          setLoadingSearch(false);
        })
        .catch(() => {
          if (disposed) {
            return;
          }
          setSearchResults([]);
          setBlockedSearchEntries([]);
          setLoadingSearch(false);
        });
    }, 120);

    return () => {
      disposed = true;
      window.clearTimeout(timeoutId);
    };
  }, [compiledEntries, rootPath, searchQuery]);

  const setSourceRuleRef =
    (ruleId: string) =>
    (node: HTMLElement | null): void => {
      if (node) {
        sourceRuleRefs.current.set(ruleId, node);
        return;
      }
      sourceRuleRefs.current.delete(ruleId);
    };

  async function handleApplyPreset(presetId: ProtectionPresetId, label: string) {
    setPendingPresetId(presetId);
    setPresetFeedback("");
    try {
      const result = await onApplyPreset(presetId);
      setPresetFeedback(getPresetFeedback(label, result));

      const existingRule = presetRules.get(presetId);
      if (result.changed) {
        onClearFocusedSource();
      } else if (existingRule) {
        onFocusSource(existingRule.id);
      }
    } finally {
      setPendingPresetId(null);
    }
  }

  async function handleAddBatchRule() {
    const extensions = normalizeExtensions(extensionInput);
    if (extensions.length === 0) {
      setBatchFeedback("Enter one or more extensions such as .env, .pem, or .key.");
      return;
    }

    setPendingBatchAdd(true);
    setBatchFeedback("");
    try {
      const result = await onAddExtensionRule(extensions);
      setBatchFeedback(getExtensionRuleFeedback(extensions, result));

      if (!result.changed) {
        const existingRule = extensionRules.find(
          (rule) => normalizeExtensions(rule.extensions.join(",")).join(",") === extensions.join(",")
        );
        if (existingRule) {
          onFocusSource(existingRule.id);
        }
      } else {
        onClearFocusedSource();
      }
    } finally {
      setPendingBatchAdd(false);
    }
  }

  async function handleAddManualEntry(entry: WorkspaceSearchResult) {
    setPendingManualPath(entry.path);
    setManualFeedback("");
    try {
      const changed = await onAddManualPath(entry.path);
      if (changed) {
        setSearchQuery("");
        setSearchResults([]);
        setBlockedSearchEntries([]);
        setManualFeedback(`Protected ${entry.relativePath}.`);
        onClearFocusedSource();
        return;
      }

      const coveringEntry = getCoveringEntry(entry.path, compiledEntries);
      if (coveringEntry) {
        setManualFeedback(
          `${entry.relativePath} is already protected by ${coveringEntry.sourceLabel}.`
        );
        onFocusSource(coveringEntry.sourceRuleId);
        return;
      }

      setManualFeedback(`${entry.relativePath} is already protected.`);
    } finally {
      setPendingManualPath(null);
    }
  }

  async function handleRemoveRule(ruleId: string, feedbackSetter: (value: string) => void) {
    setConfirmingRuleId(null);
    setPendingRuleId(ruleId);
    try {
      const removed = await onRemoveRule(ruleId);
      feedbackSetter(removed ? "Rule removed." : "Rule could not be removed.");
      if (removed && focusedSourceRuleId === ruleId) {
        onClearFocusedSource();
      }
    } finally {
      setPendingRuleId(null);
    }
  }

  function renderRemoveAction(
    ruleId: string,
    affectedPathCount: number,
    feedbackSetter: (value: string) => void
  ) {
    if (confirmingRuleId === ruleId) {
      return (
        <div className="protection-remove-confirm">
          <span className="protection-remove-confirm-copy">
            {getRemovalImpactMessage(affectedPathCount)}
          </span>
          <div className="protection-remove-confirm-actions">
            <button
              className="protection-table-remove"
              disabled={pendingRuleId === ruleId}
              onClick={() => handleRemoveRule(ruleId, feedbackSetter)}
            >
              {pendingRuleId === ruleId ? "Removing..." : "Confirm"}
            </button>
            <button
              className="protection-secondary-action"
              disabled={pendingRuleId === ruleId}
              onClick={() => setConfirmingRuleId(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      );
    }

    return (
      <button
        className="protection-secondary-action protection-secondary-action-danger"
        disabled={pendingRuleId === ruleId}
        onClick={() => setConfirmingRuleId(ruleId)}
      >
        Remove
      </button>
    );
  }

  return (
    <div className="protection-center">
      <div className="protection-center-scroll">
        <section className="protection-hero">
          <div className="protection-hero-title-row">
            <div className="protection-hero-accent"></div>
            <div className="protection-hero-copy">
              <h2>Protection Management Console</h2>
              <p>
                Rule-first protection for <strong>{workspaceName}</strong>. Presets and rules define
                policy. The active list shows the concrete paths currently shielded by those rules.
              </p>
            </div>
          </div>
        </section>

        <section className="protection-summary-grid">
          <article className="protection-summary-card">
            <span className="protection-summary-label">Rules</span>
            <strong>{rules.length}</strong>
          </article>
          <article className="protection-summary-card">
            <span className="protection-summary-label">Concrete Paths</span>
            <strong>{compiledEntries.length}</strong>
          </article>
          <article className="protection-summary-card">
            <span className="protection-summary-label">Preset Sources</span>
            <strong>{presetRules.size}</strong>
          </article>
          <article className="protection-summary-card">
            <span className="protection-summary-label">Kernel Status</span>
            <strong>{compiledEntries.length > 0 ? "Shielded" : "Ready"}</strong>
          </article>
        </section>

        <section className="protection-section-shell">
          <div className="protection-section-heading">
            <div>
              <span className="protection-section-eyebrow">Coverage</span>
              <h3>Coverage Rules</h3>
            </div>
            <p>
              Add extension rules first for direct coverage, then use built-in presets when you want
              fast default protection for common cases.
            </p>
          </div>
          <div className="protection-coverage-shell">
            <div className="protection-rule-shell protection-coverage-rule-block">
              <div className="protection-form-block">
                <label className="protection-form-label" htmlFor="protection-extension-input">
                  Extension List
                </label>
                <div className="protection-inline-form">
                  <input
                    id="protection-extension-input"
                    className="protection-input"
                    value={extensionInput}
                    onChange={(event) => setExtensionInput(event.target.value)}
                    placeholder=".env, .pem, .key"
                  />
                  <button
                    className="protection-primary-action protection-primary-action-inline protection-primary-action-compact"
                    disabled={pendingBatchAdd}
                    onClick={handleAddBatchRule}
                  >
                    {pendingBatchAdd ? "Adding..." : "Add Rule"}
                  </button>
                </div>
              </div>
              <div className="protection-source-list">
                {extensionRules.length === 0 ? (
                  <div className="protection-source-empty">
                    No extension rules yet. Add a rule to keep matching files protected
                    automatically.
                  </div>
                ) : (
                  extensionRules.map((rule) => (
                    <article
                      key={rule.id}
                      className={`protection-source-row ${focusedSourceRuleId === rule.id ? "protection-source-focused" : ""}`}
                      ref={setSourceRuleRef(rule.id)}
                      tabIndex={-1}
                    >
                      <div className="protection-source-copy">
                        <div className="protection-source-meta">
                          <span className="protection-rule-badge">{getRuleBadgeLabel(rule)}</span>
                          <span>{compiledCountByRuleId.get(rule.id) ?? 0} matches</span>
                        </div>
                        <strong>{getRuleDetail(rule, presets)}</strong>
                      </div>
                      {renderRemoveAction(
                        rule.id,
                        compiledCountByRuleId.get(rule.id) ?? 0,
                        setBatchFeedback
                      )}
                    </article>
                  ))
                )}
              </div>
            </div>
            <p className="protection-feedback">
              {batchFeedback ||
                "Batch rules are source-level policy. Concrete file matches appear below in the active list."}
            </p>
            <div className="protection-source-list protection-coverage-preset-list">
              {presets.map((preset) => {
                const sourceRule = presetRules.get(preset.id);
                const matchCount = sourceRule ? compiledCountByRuleId.get(sourceRule.id) ?? 0 : 0;
                const isFocused = sourceRule?.id === focusedSourceRuleId;
                return (
                  <article
                    key={preset.id}
                    className={`protection-source-row protection-coverage-row ${isFocused ? "protection-source-focused" : ""}`}
                    ref={sourceRule ? setSourceRuleRef(sourceRule.id) : undefined}
                    tabIndex={sourceRule ? -1 : undefined}
                  >
                    <div className="protection-preset-simple-label">
                      <strong>{preset.label}</strong>
                    </div>
                    <div className="protection-card-actions">
                      {sourceRule ? (
                        renderRemoveAction(sourceRule.id, matchCount, setPresetFeedback)
                      ) : (
                        <button
                          className="protection-primary-action protection-primary-action-compact"
                          disabled={pendingPresetId === preset.id}
                          onClick={() => handleApplyPreset(preset.id, preset.label)}
                        >
                          {pendingPresetId === preset.id ? "Applying..." : "Apply"}
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
            <p className="protection-feedback">
              {presetFeedback || "Use built-in presets when you want fast default coverage."}
            </p>
          </div>
        </section>

        <section className="protection-section-shell">
          <div className="protection-section-heading">
            <div>
              <span className="protection-section-eyebrow">Manual Add</span>
              <h3>Direct workspace targets</h3>
            </div>
            <p>Search for a file or folder inside this workspace, then add it immediately as a direct rule.</p>
          </div>
          <div className="protection-manual-shell">
            <div className="protection-manual-search">
              <div className="protection-form-block">
                <label className="protection-form-label" htmlFor="protection-search-input">
                  Search Files Or Folders
                </label>
                <input
                  id="protection-search-input"
                  className="protection-input"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search workspace paths"
                />
              </div>
              <div className="protection-search-results">
                {searchResults.length === 0 ? (
                  <div className="protection-empty-search">
                    {loadingSearch
                      ? "Searching workspace..."
                      : searchQuery.trim()
                        ? getBlockedSearchSummary(blockedSearchEntries) ||
                          "No matching unprotected paths found."
                        : "Search for a file or folder to add a direct rule."}
                  </div>
                ) : (
                  searchResults.map((entry) => (
                    <button
                      key={entry.path}
                      className="protection-search-item"
                      disabled={pendingManualPath === entry.path}
                      onClick={() => handleAddManualEntry(entry)}
                    >
                      <span className="protection-search-item-icon">
                        {entry.isDirectory ? "DIR" : "FILE"}
                      </span>
                      <span className="protection-search-item-copy">
                        <span>{entry.relativePath}</span>
                        <span>{entry.path}</span>
                      </span>
                      <span className="protection-search-item-action">
                        {pendingManualPath === entry.path ? "ADDING" : "ADD"}
                      </span>
                    </button>
                  ))
                )}
              </div>
              {blockedSearchEntries.length > 0 && searchResults.length > 0 ? (
                <p className="protection-inline-note">
                  {getBlockedSearchSummary(blockedSearchEntries)}
                </p>
              ) : null}
            </div>
          </div>
          <p className="protection-feedback">
            {manualFeedback ||
              "Manual add is input-only. Manage direct rules from the active protection list."}
          </p>
        </section>

        <section className="protection-table-shell">
          <div className="protection-table-header">
            <div>
              <span className="protection-section-eyebrow">Active Protection List</span>
              <h3>Compiled concrete paths</h3>
              <p>{compiledEntries.length} live shield{compiledEntries.length === 1 ? "" : "s"}</p>
            </div>
          </div>

          {compiledEntries.length === 0 ? (
            <div className="protection-table-empty">
              <strong>No protected paths yet</strong>
              <span>Apply a preset, add an extension rule, or add a direct path to populate the active list.</span>
            </div>
          ) : (
            <div className="protection-table-wrap">
              <table className="protection-table">
                <thead>
                  <tr>
                    <th>Path</th>
                    <th>Type</th>
                    <th>Source</th>
                    <th>Status</th>
                    <th className="protection-table-actions">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {compiledEntries.map((entry) => {
                    const action = getProtectionAction(entry);
                    return (
                      <tr key={entry.path}>
                        <td>
                          <div className="protection-path-cell">
                            <span className="protection-path-lock">LOCK</span>
                            <div className="protection-path-copy">
                              <span className="protection-path-primary">{entry.relativePath}</span>
                              <span className="protection-path-secondary" title={entry.path}>
                                {entry.path}
                              </span>
                            </div>
                          </div>
                        </td>
                        <td>
                          <span className="protection-scope-pill">
                            {entry.type === "folder" ? "Folder" : "File"}
                          </span>
                        </td>
                        <td>
                          {entry.canRemoveDirectly ? (
                            <span className="protection-source-label">{entry.sourceLabel}</span>
                          ) : (
                            <button
                              className="protection-source-link"
                              onClick={() => onFocusSource(entry.sourceRuleId)}
                            >
                              {entry.sourceLabel}
                            </button>
                          )}
                        </td>
                        <td>
                          <span className="protection-status-pill">Shielded</span>
                        </td>
                        <td className="protection-table-actions">
                          {action === "remove" ? (
                            renderRemoveAction(
                              entry.sourceRuleId,
                              compiledCountByRuleId.get(entry.sourceRuleId) ?? 0,
                              setManualFeedback
                            )
                          ) : (
                            <button
                              className="protection-table-view-source"
                              onClick={() => onFocusSource(entry.sourceRuleId)}
                            >
                              View Source
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
