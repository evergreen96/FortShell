export type TerminalLayoutMode = "horizontal" | "vertical" | "grid";

export type TerminalGridSpan = {
  cols: number;
  rows: number;
};

export type TerminalWorkspaceLayout = {
  mode: TerminalLayoutMode;
  stackWeights: Record<string, number>;
  gridSpans: Record<string, TerminalGridSpan>;
};

const DEFAULT_STACK_WEIGHT = 1;
const MIN_STACK_WEIGHT = 0.2;
const MIN_GRID_COLUMNS = 1;
const MAX_GRID_COLUMNS = 5;
const MIN_GRID_ROWS = 1;
const MAX_GRID_ROWS = 4;

export function createTerminalWorkspaceLayout(): TerminalWorkspaceLayout {
  return {
    mode: "vertical",
    stackWeights: {},
    gridSpans: {},
  };
}

export function syncTerminalWorkspaceLayout(
  layout: TerminalWorkspaceLayout,
  terminalIds: string[],
): TerminalWorkspaceLayout {
  const stackWeights: Record<string, number> = {};
  const gridSpans: Record<string, TerminalGridSpan> = {};

  for (const terminalId of terminalIds) {
    const nextWeight = layout.stackWeights[terminalId];
    stackWeights[terminalId] = Number.isFinite(nextWeight) && nextWeight > 0 ? nextWeight : DEFAULT_STACK_WEIGHT;

    const currentSpan = layout.gridSpans[terminalId];
    gridSpans[terminalId] = {
      cols: clamp(currentSpan?.cols ?? 1, MIN_GRID_COLUMNS, MAX_GRID_COLUMNS),
      rows: clamp(currentSpan?.rows ?? 1, MIN_GRID_ROWS, MAX_GRID_ROWS),
    };
  }

  return {
    mode: layout.mode,
    stackWeights,
    gridSpans,
  };
}

export function resizeStackDivider(
  layout: TerminalWorkspaceLayout,
  terminalIds: string[],
  dividerIndex: number,
  deltaRatio: number,
): TerminalWorkspaceLayout {
  if (dividerIndex < 0 || dividerIndex >= terminalIds.length - 1) {
    return layout;
  }

  const leftId = terminalIds[dividerIndex];
  const rightId = terminalIds[dividerIndex + 1];
  const leftWeight = layout.stackWeights[leftId] ?? DEFAULT_STACK_WEIGHT;
  const rightWeight = layout.stackWeights[rightId] ?? DEFAULT_STACK_WEIGHT;
  const totalWeight = leftWeight + rightWeight;
  const minWeight = Math.max(MIN_STACK_WEIGHT, totalWeight * 0.1);
  const nextLeftWeight = clamp(leftWeight + deltaRatio * totalWeight, minWeight, totalWeight - minWeight);

  return {
    ...layout,
    stackWeights: {
      ...layout.stackWeights,
      [leftId]: nextLeftWeight,
      [rightId]: totalWeight - nextLeftWeight,
    },
  };
}

export function resizeGridSpan(
  layout: TerminalWorkspaceLayout,
  terminalId: string,
  deltaCols: number,
  deltaRows: number,
): TerminalWorkspaceLayout {
  const current = layout.gridSpans[terminalId] ?? { cols: 1, rows: 1 };

  return {
    ...layout,
    gridSpans: {
      ...layout.gridSpans,
      [terminalId]: {
        cols: clamp(current.cols + deltaCols, MIN_GRID_COLUMNS, MAX_GRID_COLUMNS),
        rows: clamp(current.rows + deltaRows, MIN_GRID_ROWS, MAX_GRID_ROWS),
      },
    },
  };
}

export function stackWeight(layout: TerminalWorkspaceLayout, terminalId: string): number {
  return layout.stackWeights[terminalId] ?? DEFAULT_STACK_WEIGHT;
}

export function gridSpan(layout: TerminalWorkspaceLayout, terminalId: string): TerminalGridSpan {
  return layout.gridSpans[terminalId] ?? { cols: 1, rows: 1 };
}

function clamp(value: number, min: number, max: number): number {
  if (value < min) {
    return min;
  }
  if (value > max) {
    return max;
  }
  return value;
}
