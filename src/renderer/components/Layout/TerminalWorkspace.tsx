import { useState, useRef } from "react";
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
  const [sizes, setSizes] = useState<number[]>([]);
  const [gridColRatio, setGridColRatio] = useState(0.5);
  const [gridRowSizes, setGridRowSizes] = useState<number[]>([]);
  const dragRef = useRef<{ index: number; startPos: number; startSizes: number[] } | null>(null);
  const gridDragRef = useRef<{ axis: "col" | "row"; index: number; startPos: number; startSizes: number[] } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const expectedLen = terminals.length;
  const currentSizes = sizes.length === expectedLen ? sizes : Array(expectedLen).fill(1 / expectedLen);

  if (terminals.length === 0) {
    return (
      <div className="terminal-workspace-empty">
        <p>No terminals open</p>
      </div>
    );
  }

  if (layoutMode === "horizontal" && terminals.length === 1) {
    const t = terminals[0];
    return (
      <div className="terminal-workspace terminal-workspace-single">
        <TerminalPane terminalId={t.id} isActive={true} fontSize={fontSize} />
      </div>
    );
  }

  const isVertical = layoutMode === "vertical";
  const isGrid = layoutMode === "grid";

  function handleDragStart(e: React.MouseEvent, index: number) {
    e.preventDefault();
    const startPos = isVertical ? e.clientY : e.clientX;
    dragRef.current = { index, startPos, startSizes: [...currentSizes] };

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current || !containerRef.current) return;
      const container = containerRef.current;
      const totalSize = isVertical ? container.offsetHeight : container.offsetWidth;
      const pos = isVertical ? ev.clientY : ev.clientX;
      const delta = (pos - dragRef.current.startPos) / totalSize;
      const { index: idx, startSizes } = dragRef.current;

      const newSizes = [...startSizes];
      const minFraction = 0.1;
      let a = startSizes[idx] + delta;
      let b = startSizes[idx + 1] - delta;

      if (a < minFraction) { a = minFraction; b = startSizes[idx] + startSizes[idx + 1] - minFraction; }
      if (b < minFraction) { b = minFraction; a = startSizes[idx] + startSizes[idx + 1] - minFraction; }

      newSizes[idx] = a;
      newSizes[idx + 1] = b;
      setSizes(newSizes);
    };

    const onUp = () => {
      dragRef.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  function handleGridDragStart(e: React.MouseEvent, axis: "col" | "row", index: number = 0) {
    e.preventDefault();
    const startPos = axis === "col" ? e.clientX : e.clientY;

    if (axis === "col") {
      gridDragRef.current = { axis, index: 0, startPos, startSizes: [gridColRatio] };
    } else {
      gridDragRef.current = { axis, index, startPos, startSizes: [...currentRowSizes] };
    }

    const onMove = (ev: MouseEvent) => {
      if (!gridDragRef.current || !containerRef.current) return;
      const container = containerRef.current;
      const { axis: a, index: idx, startPos: sp, startSizes: ss } = gridDragRef.current;
      const totalSize = a === "col" ? container.offsetWidth : container.offsetHeight;
      const pos = a === "col" ? ev.clientX : ev.clientY;
      const delta = (pos - sp) / totalSize;

      if (a === "col") {
        setGridColRatio(Math.max(0.15, Math.min(0.85, ss[0] + delta)));
      } else {
        const newSizes = [...ss];
        const minFraction = 0.1;
        let sA = ss[idx] + delta;
        let sB = ss[idx + 1] - delta;
        if (sA < minFraction) { sA = minFraction; sB = ss[idx] + ss[idx + 1] - minFraction; }
        if (sB < minFraction) { sB = minFraction; sA = ss[idx] + ss[idx + 1] - minFraction; }
        newSizes[idx] = sA;
        newSizes[idx + 1] = sB;
        setGridRowSizes(newSizes);
      }
    };

    const onUp = () => {
      gridDragRef.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  const gridRows = Math.ceil(terminals.length / 2);
  const currentRowSizes = gridRowSizes.length === gridRows
    ? gridRowSizes
    : Array(gridRows).fill(1 / gridRows);

  if (isGrid) {
    const cols = 2;
    const rows = gridRows;
    const colTemplate = `${gridColRatio}fr 4px ${1 - gridColRatio}fr`;
    const rowParts: string[] = [];
    for (let r = 0; r < rows; r++) {
      if (r > 0) rowParts.push("4px");
      rowParts.push(`${currentRowSizes[r]}fr`);
    }

    const gridItems: React.ReactNode[] = [];
    for (let r = 0; r < rows; r++) {
      const gridRow = r * 2 + 1; // 1-based, accounting for handle rows
      for (let c = 0; c < cols; c++) {
        const idx = r * cols + c;
        if (idx >= terminals.length) break;
        const t = terminals[idx];
        const gridCol = c * 2 + 1;
        gridItems.push(
          <div
            key={t.id}
            className={`terminal-workspace-cell ${t.id === activeId ? "terminal-workspace-cell-active" : ""}`}
            style={{ gridRow, gridColumn: gridCol }}
            onClick={() => onSelectTerminal(t.id)}
          >
            <div className="terminal-cell-label">{t.name}</div>
            <TerminalPane terminalId={t.id} isActive={t.id === activeId} fontSize={fontSize} />
          </div>
        );
      }

      // Horizontal handle between rows
      if (r < rows - 1) {
        gridItems.push(
          <div
            key={`row-handle-${r}`}
            className="terminal-resize-handle-horizontal"
            style={{ gridRow: gridRow + 1, gridColumn: "1 / -1" }}
            onMouseDown={(e) => handleGridDragStart(e, "row", r)}
          />
        );
      }
    }

    // Vertical handle between columns
    if (terminals.length > 1) {
      gridItems.push(
        <div
          key="col-handle"
          className="terminal-resize-handle-vertical"
          style={{ gridRow: "1 / -1", gridColumn: 2 }}
          onMouseDown={(e) => handleGridDragStart(e, "col")}
        />
      );
    }

    return (
      <div
        ref={containerRef}
        className="terminal-workspace terminal-workspace-grid"
        style={{
          display: "grid",
          gridTemplateColumns: colTemplate,
          gridTemplateRows: rowParts.join(" "),
        }}
      >
        {gridItems}
      </div>
    );
  }

  const layoutClass = isVertical
    ? "terminal-workspace-vertical"
    : "terminal-workspace-horizontal";

  return (
    <div ref={containerRef} className={`terminal-workspace ${layoutClass}`} style={{ display: "flex", flexDirection: isVertical ? "column" : "row" }}>
      {terminals.map((t, i) => (
        <div key={t.id} style={{ display: "contents" }}>
          <div
            className={`terminal-workspace-cell ${t.id === activeId ? "terminal-workspace-cell-active" : ""}`}
            style={{ flex: `${currentSizes[i]} 1 0%` }}
            onClick={() => onSelectTerminal(t.id)}
          >
            <div className="terminal-cell-label">{t.name}</div>
            <TerminalPane terminalId={t.id} isActive={t.id === activeId} fontSize={fontSize} />
          </div>
          {i < terminals.length - 1 && (
            <div
              className={`terminal-resize-handle ${isVertical ? "terminal-resize-handle-horizontal" : "terminal-resize-handle-vertical"}`}
              onMouseDown={(e) => handleDragStart(e, i)}
            />
          )}
        </div>
      ))}
    </div>
  );
}
