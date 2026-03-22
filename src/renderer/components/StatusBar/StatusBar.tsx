import type { TerminalLayoutMode } from "../../lib/terminalLayout";

type StatusBarProps = {
  terminalCount: number;
  layoutMode: TerminalLayoutMode;
  workspacePath: string | null;
  protectedCount: number;
  platform: string;
};

export function StatusBar({
  terminalCount,
  layoutMode,
  workspacePath,
  protectedCount,
  platform,
}: StatusBarProps) {
  return (
    <div className="status-bar">
      <div className="status-bar-left">
        {workspacePath ? (
          <span title={workspacePath}>
            {workspacePath.split(/[/\\]/).pop()}
          </span>
        ) : (
          <span>No workspace</span>
        )}
        {protectedCount > 0 && (
          <span className="status-protected">
            &#128274; {protectedCount} protected
          </span>
        )}
      </div>
      <div className="status-bar-right">
        <span>
          {terminalCount} terminal{terminalCount !== 1 ? "s" : ""}
        </span>
        <span>Layout: {layoutMode}</span>
        <span>{platform}</span>
      </div>
    </div>
  );
}
