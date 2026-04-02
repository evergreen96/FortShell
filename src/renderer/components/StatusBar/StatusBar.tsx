import type { TerminalLayoutMode } from "../../lib/terminalLayout";

type StatusBarProps = {
  terminalCount: number;
  layoutMode: TerminalLayoutMode;
  protectedCount: number;
};

export function StatusBar({
  terminalCount,
  layoutMode,
  protectedCount,
}: StatusBarProps) {
  return (
    <div className="status-bar">
      <div className="status-bar-group">
        {protectedCount > 0 && (
          <span className="status-bar-chip status-protected">
            {protectedCount} protected
          </span>
        )}
        <span className="status-bar-chip">
          {terminalCount} terminal{terminalCount !== 1 ? "s" : ""}
        </span>
        {terminalCount > 1 && (
          <span className="status-bar-chip">layout {layoutMode}</span>
        )}
      </div>
    </div>
  );
}
