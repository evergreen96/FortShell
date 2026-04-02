import { Fragment, useState, useRef } from "react";
import { TerminalPane } from "../Terminal/TerminalPane";
import type { TerminalLayoutMode } from "../../lib/terminalLayout";

const HANDLE_SIZE = 5;
const MIN_PANEL_FRACTION = 0.12;
const MIN_PANEL_WIDTH = 220;
const MIN_PANEL_HEIGHT = 140;

type TerminalInfo = {
  id: string;
  name: string;
  status: string;
  stalePolicy?: boolean;
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
  const dragRef = useRef<{ index: number; startPos: number; startSizes: number[]; totalPaneSize: number } | null>(null);
  const gridDragRef = useRef<{ axis: "col" | "row"; index: number; startPos: number; startSizes: number[]; totalPaneSize: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);

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
        <div
          className="terminal-workspace-cell terminal-workspace-cell-active"
          onClick={() => onSelectTerminal(t.id)}
        >
          <div className="terminal-cell-label">
            <div className="terminal-cell-meta">
              <span className="terminal-cell-title">{t.name}</span>
              {t.stalePolicy && (
                <span className="terminal-cell-badge">restart required</span>
              )}
              {t.status === "exited" && (
                <span className="terminal-cell-badge terminal-cell-badge-muted">exited</span>
              )}
            </div>
          </div>
          <TerminalPane terminalId={t.id} isActive={true} fontSize={fontSize} />
        </div>
      </div>
    );
  }

  const isVertical = layoutMode === "vertical";
  const isGrid = layoutMode === "grid";

  const gridRows = Math.ceil(terminals.length / 2);
  const currentRowSizes = gridRowSizes.length === gridRows
    ? gridRowSizes
    : Array(gridRows).fill(1 / gridRows);

  function minFractionForSize(totalPaneSize: number, minPanelSize: number, pairTotal: number = 1): number {
    const pixelFraction = totalPaneSize > 0 ? minPanelSize / totalPaneSize : MIN_PANEL_FRACTION;
    const desiredMin = Math.max(MIN_PANEL_FRACTION, pixelFraction);
    const maxAllowed = Math.max(0.01, pairTotal / 2 - 0.001);
    return Math.max(0.01, Math.min(desiredMin, maxAllowed));
  }

  function cleanupDragListeners() {
    if (cleanupRef.current) {
      cleanupRef.current();
      cleanupRef.current = null;
    }
    document.body.classList.remove("is-resizing");
    document.body.style.removeProperty("cursor");
  }

  function handleDragStart(e: React.MouseEvent, index: number) {
    e.preventDefault();
    if (!containerRef.current) return;

    const panes = Array.from(
      containerRef.current.querySelectorAll<HTMLElement>(".terminal-workspace-cell")
    );
    const paneSizes = panes.map((pane) =>
      isVertical ? pane.getBoundingClientRect().height : pane.getBoundingClientRect().width
    );
    const totalPaneSize = paneSizes.reduce((sum, size) => sum + size, 0);
    if (totalPaneSize <= 0) return;

    document.body.classList.add("is-resizing");
    document.body.style.cursor = isVertical ? "row-resize" : "col-resize";
    const startPos = isVertical ? e.clientY : e.clientX;
    dragRef.current = {
      index,
      startPos,
      startSizes: paneSizes.map((size) => size / totalPaneSize),
      totalPaneSize,
    };

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const pos = isVertical ? ev.clientY : ev.clientX;
      const delta = (pos - dragRef.current.startPos) / dragRef.current.totalPaneSize;
      const { index: idx, startSizes } = dragRef.current;

      const newSizes = [...startSizes];
      const pairTotal = startSizes[idx] + startSizes[idx + 1];
      const minFraction = minFractionForSize(
        dragRef.current.totalPaneSize,
        isVertical ? MIN_PANEL_HEIGHT : MIN_PANEL_WIDTH,
        pairTotal
      );
      let a = startSizes[idx] + delta;
      let b = startSizes[idx + 1] - delta;

      if (a < minFraction) { a = minFraction; b = pairTotal - minFraction; }
      if (b < minFraction) { b = minFraction; a = pairTotal - minFraction; }

      newSizes[idx] = a;
      newSizes[idx + 1] = b;
      setSizes(newSizes);
    };

    const onUp = () => {
      dragRef.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      cleanupRef.current = null;
      document.body.classList.remove("is-resizing");
      document.body.style.removeProperty("cursor");
    };

    cleanupDragListeners();
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    cleanupRef.current = onUp;
  }

  function handleGridDragStart(e: React.MouseEvent, axis: "col" | "row", index: number = 0) {
    e.preventDefault();
    if (!containerRef.current) return;
    const startPos = axis === "col" ? e.clientX : e.clientY;
    document.body.classList.add("is-resizing");
    document.body.style.cursor = axis === "col" ? "col-resize" : "row-resize";

    if (axis === "col") {
      const leftPane = containerRef.current.querySelector<HTMLElement>(
        '.terminal-workspace-cell[data-grid-col="1"]'
      );
      const rightPane = containerRef.current.querySelector<HTMLElement>(
        '.terminal-workspace-cell[data-grid-col="3"]'
      );
      const leftSize = leftPane?.getBoundingClientRect().width ?? 0;
      const rightSize = rightPane?.getBoundingClientRect().width ?? 0;
      const totalPaneSize = leftSize + rightSize;
      if (totalPaneSize <= 0) return;
      gridDragRef.current = {
        axis,
        index: 0,
        startPos,
        startSizes: [leftSize / totalPaneSize],
        totalPaneSize,
      };
    } else {
      const rowSizes = Array.from({ length: gridRows }, (_, rowIndex) => {
        const pane = containerRef.current?.querySelector<HTMLElement>(
          `.terminal-workspace-cell[data-grid-row="${rowIndex * 2 + 1}"]`
        );
        return pane?.getBoundingClientRect().height ?? 0;
      });
      const totalPaneSize = rowSizes.reduce((sum, size) => sum + size, 0);
      if (totalPaneSize <= 0) return;
      gridDragRef.current = {
        axis,
        index,
        startPos,
        startSizes: rowSizes.map((size) => size / totalPaneSize),
        totalPaneSize,
      };
    }

    const onMove = (ev: MouseEvent) => {
      if (!gridDragRef.current) return;
      const { axis: a, index: idx, startPos: sp, startSizes: ss } = gridDragRef.current;
      const pos = a === "col" ? ev.clientX : ev.clientY;
      const delta = (pos - sp) / gridDragRef.current.totalPaneSize;

      if (a === "col") {
        const minRatio = minFractionForSize(
          gridDragRef.current.totalPaneSize,
          MIN_PANEL_WIDTH
        );
        const maxRatio = 1 - minRatio;
        setGridColRatio(Math.max(minRatio, Math.min(maxRatio, ss[0] + delta)));
      } else {
        const newSizes = [...ss];
        const pairTotal = ss[idx] + ss[idx + 1];
        const minFraction = minFractionForSize(
          gridDragRef.current.totalPaneSize,
          MIN_PANEL_HEIGHT,
          pairTotal
        );
        let sA = ss[idx] + delta;
        let sB = ss[idx + 1] - delta;
        if (sA < minFraction) { sA = minFraction; sB = pairTotal - minFraction; }
        if (sB < minFraction) { sB = minFraction; sA = pairTotal - minFraction; }
        newSizes[idx] = sA;
        newSizes[idx + 1] = sB;
        setGridRowSizes(newSizes);
      }
    };

    const onUp = () => {
      gridDragRef.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      cleanupRef.current = null;
      document.body.classList.remove("is-resizing");
      document.body.style.removeProperty("cursor");
    };

    cleanupDragListeners();
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    cleanupRef.current = onUp;
  }

  if (isGrid) {
    const cols = 2;
    const rows = gridRows;
    const colTemplate = `${gridColRatio}fr ${HANDLE_SIZE}px ${1 - gridColRatio}fr`;
    const rowParts: string[] = [];
    for (let r = 0; r < rows; r++) {
      if (r > 0) rowParts.push(`${HANDLE_SIZE}px`);
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
            data-grid-col={gridCol}
            data-grid-row={gridRow}
            style={{ gridRow, gridColumn: gridCol }}
            onClick={() => onSelectTerminal(t.id)}
          >
            <div className="terminal-cell-label">
              <div className="terminal-cell-meta">
                <span className="terminal-cell-title">{t.name}</span>
                {t.stalePolicy && (
                  <span className="terminal-cell-badge">restart required</span>
                )}
                {t.status === "exited" && (
                  <span className="terminal-cell-badge terminal-cell-badge-muted">exited</span>
                )}
              </div>
            </div>
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
        <Fragment key={t.id}>
          <div
            className={`terminal-workspace-cell ${t.id === activeId ? "terminal-workspace-cell-active" : ""}`}
            style={{ flex: `${currentSizes[i]} 1 0%` }}
            onClick={() => onSelectTerminal(t.id)}
          >
            <div className="terminal-cell-label">
              <div className="terminal-cell-meta">
                <span className="terminal-cell-title">{t.name}</span>
                {t.stalePolicy && (
                  <span className="terminal-cell-badge">restart required</span>
                )}
                {t.status === "exited" && (
                  <span className="terminal-cell-badge terminal-cell-badge-muted">exited</span>
                )}
              </div>
            </div>
            <TerminalPane terminalId={t.id} isActive={t.id === activeId} fontSize={fontSize} />
          </div>
          {i < terminals.length - 1 && (
            <div
              className={`terminal-resize-handle ${isVertical ? "terminal-resize-handle-horizontal" : "terminal-resize-handle-vertical"}`}
              onMouseDown={(e) => handleDragStart(e, i)}
            />
          )}
        </Fragment>
      ))}
    </div>
  );
}
