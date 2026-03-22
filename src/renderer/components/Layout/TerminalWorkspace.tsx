import { TerminalPane } from "../Terminal/TerminalPane";
import type { TerminalLayoutMode } from "../../lib/terminalLayout";

type TerminalInfo = {
  id: string;
  name: string;
  status: string;
};

type TerminalWorkspaceProps = {
  terminals: TerminalInfo[];
  activeId: string | null;
  layoutMode: TerminalLayoutMode;
  onSelectTerminal: (id: string) => void;
  fontSize?: number;
};

export function TerminalWorkspace({
  terminals,
  activeId,
  layoutMode,
  onSelectTerminal,
  fontSize,
}: TerminalWorkspaceProps) {
  if (terminals.length === 0) {
    return (
      <div className="terminal-workspace-empty">
        <p>No terminals open</p>
      </div>
    );
  }

  // Tabs mode: show only active terminal
  if (layoutMode === "horizontal" && terminals.length === 1) {
    const t = terminals[0];
    return (
      <div className="terminal-workspace terminal-workspace-single">
        <TerminalPane terminalId={t.id} isActive={true} fontSize={fontSize} />
      </div>
    );
  }

  const layoutClass =
    layoutMode === "vertical"
      ? "terminal-workspace-vertical"
      : layoutMode === "horizontal"
        ? "terminal-workspace-horizontal"
        : "terminal-workspace-grid";

  return (
    <div className={`terminal-workspace ${layoutClass}`}>
      {terminals.map((t) => (
        <div
          key={t.id}
          className={`terminal-workspace-cell ${t.id === activeId ? "terminal-workspace-cell-active" : ""}`}
          onClick={() => onSelectTerminal(t.id)}
        >
          <TerminalPane terminalId={t.id} isActive={t.id === activeId} fontSize={fontSize} />
        </div>
      ))}
    </div>
  );
}
