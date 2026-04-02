type ProtectionPanelProps = {
  rootPath: string;
  protectedPaths: Set<string>;
  onOpenExplorer: () => void;
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

export function ProtectionPanel({
  rootPath,
  protectedPaths,
  onOpenExplorer,
}: ProtectionPanelProps) {
  const items = Array.from(protectedPaths)
    .map((filePath) => ({
      filePath,
      relativePath: toRelativePath(rootPath, filePath),
    }))
    .sort((a, b) => a.relativePath.localeCompare(b.relativePath));

  return (
    <div className="protection-panel">
      <div className="protection-panel-header">
        <span className="protection-panel-eyebrow">Protection Mode</span>
        <div className="protection-panel-row">
          <span className="protection-panel-title">Shielded Workspace</span>
          <span className="protection-panel-count">{items.length}</span>
        </div>
        <p className="protection-panel-copy">
          Review the full protection dashboard in the main panel. Use Explorer to
          add or adjust protected files and folders.
        </p>
        <button className="protection-panel-explorer-btn" onClick={onOpenExplorer}>
          Open Explorer
        </button>
      </div>

      <div className="protection-panel-list">
        {items.length === 0 ? (
          <div className="protection-panel-empty">
            <span className="protection-panel-empty-title">No protected paths</span>
            <span className="protection-panel-empty-copy">
              Switch to Explorer and protect a file or folder from the context menu.
            </span>
          </div>
        ) : (
          items.map((item) => (
            <div key={item.filePath} className="protection-item">
              <div className="protection-item-copy">
                <span className="protection-item-label">{item.relativePath}</span>
                <span className="protection-item-meta" title={item.filePath}>
                  {item.filePath}
                </span>
              </div>
              <span className="protection-item-state">Live</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
