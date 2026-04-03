import type { TerminalSessionReplacement } from "./types";

export type TerminalTabState = {
  id: string;
  name: string;
  status: "active" | "exited";
  slotKey: string;
};

export function applyTerminalReplacement(
  terminals: TerminalTabState[],
  replacement: TerminalSessionReplacement,
): TerminalTabState[] {
  const existingIndex = terminals.findIndex(
    (terminal) => terminal.id === replacement.oldTerminalId,
  );

  if (existingIndex >= 0) {
    return terminals.map((terminal, index) =>
      index === existingIndex
        ? {
            ...terminal,
            id: replacement.newTerminalId,
            name: replacement.displayName,
            status: "active",
            slotKey: replacement.layoutSlotKey ?? terminal.slotKey,
          }
        : terminal,
    );
  }

  if (terminals.some((terminal) => terminal.id === replacement.newTerminalId)) {
    return terminals;
  }

  return [
    ...terminals,
    {
      id: replacement.newTerminalId,
      name: replacement.displayName,
      status: "active",
      slotKey: replacement.layoutSlotKey ?? replacement.oldTerminalId,
    },
  ];
}

export function applyTerminalReplacements(
  terminals: TerminalTabState[],
  replacements: TerminalSessionReplacement[],
): TerminalTabState[] {
  return replacements.reduce(
    (currentTerminals, replacement) =>
      applyTerminalReplacement(currentTerminals, replacement),
    terminals,
  );
}

export function reconcileActiveTerminalId(
  activeTerminalId: string | null,
  replacements: TerminalSessionReplacement[],
): string | null {
  if (!activeTerminalId) {
    return activeTerminalId;
  }

  const replacement = replacements.find(
    (candidate) => candidate.oldTerminalId === activeTerminalId,
  );

  return replacement ? replacement.newTerminalId : activeTerminalId;
}
