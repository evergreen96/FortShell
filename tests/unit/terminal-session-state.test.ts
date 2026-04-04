import { describe, expect, it } from "vitest";
import {
  applyTerminalReplacement,
  applyTerminalReplacements,
  reconcileActiveTerminalId,
  type TerminalTabState,
} from "../../src/renderer/lib/terminalSessionState";
import type { TerminalSessionReplacement } from "../../src/renderer/lib/types";

describe("terminal session state reconciliation", () => {
  it("appends a replacement when the old local tab is already gone", () => {
    const terminals: TerminalTabState[] = [
      {
        id: "term-2",
        name: "Other",
        status: "active",
        slotKey: "slot-2",
      },
    ];

    const replacement: TerminalSessionReplacement = {
      oldTerminalId: "term-1",
      newTerminalId: "term-9",
      displayName: "Restarted",
      layoutSlotKey: "slot-1",
    };

    expect(applyTerminalReplacement(terminals, replacement)).toEqual([
      {
        id: "term-2",
        name: "Other",
        status: "active",
        slotKey: "slot-2",
      },
      {
        id: "term-9",
        name: "Restarted",
        status: "active",
        slotKey: "slot-1",
      },
    ]);
  });

  it("reconciles batches without losing replacements whose old ids are missing", () => {
    const terminals: TerminalTabState[] = [
      {
        id: "term-1",
        name: "One",
        status: "active",
        slotKey: "slot-1",
      },
      {
        id: "term-2",
        name: "Two",
        status: "active",
        slotKey: "slot-2",
      },
    ];

    const replacements: TerminalSessionReplacement[] = [
      {
        oldTerminalId: "term-1",
        newTerminalId: "term-3",
        displayName: "Three",
        layoutSlotKey: "slot-1",
      },
      {
        oldTerminalId: "term-missing",
        newTerminalId: "term-4",
        displayName: "Four",
        layoutSlotKey: "slot-4",
      },
    ];

    expect(applyTerminalReplacements(terminals, replacements)).toEqual([
      {
        id: "term-3",
        name: "Three",
        status: "active",
        slotKey: "slot-1",
      },
      {
        id: "term-2",
        name: "Two",
        status: "active",
        slotKey: "slot-2",
      },
      {
        id: "term-4",
        name: "Four",
        status: "active",
        slotKey: "slot-4",
      },
    ]);
  });

  it("moves the active id to the replacement id when needed", () => {
    expect(
      reconcileActiveTerminalId("term-1", [
        {
          oldTerminalId: "term-1",
          newTerminalId: "term-7",
          displayName: "Seven",
          layoutSlotKey: "slot-1",
        },
      ]),
    ).toBe("term-7");
  });
});
