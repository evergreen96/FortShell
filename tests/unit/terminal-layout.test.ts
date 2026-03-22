import { describe, it, expect } from "vitest";
import {
  createTerminalWorkspaceLayout,
  syncTerminalWorkspaceLayout,
  resizeStackDivider,
  stackWeight,
} from "../../src/renderer/lib/terminalLayout";

describe("Terminal Layout", () => {
  it("should create default layout", () => {
    const layout = createTerminalWorkspaceLayout();
    expect(layout.mode).toBe("vertical");
    expect(Object.keys(layout.stackWeights)).toHaveLength(0);
  });

  it("should sync layout with terminal ids", () => {
    const layout = createTerminalWorkspaceLayout();
    const synced = syncTerminalWorkspaceLayout(layout, ["t1", "t2", "t3"]);
    expect(Object.keys(synced.stackWeights)).toHaveLength(3);
    expect(stackWeight(synced, "t1")).toBe(1);
    expect(stackWeight(synced, "t2")).toBe(1);
  });

  it("should resize stack divider", () => {
    const layout = createTerminalWorkspaceLayout();
    const synced = syncTerminalWorkspaceLayout(layout, ["t1", "t2"]);
    const resized = resizeStackDivider(synced, ["t1", "t2"], 0, 0.2);
    expect(stackWeight(resized, "t1")).toBeGreaterThan(stackWeight(synced, "t1"));
    expect(stackWeight(resized, "t2")).toBeLessThan(stackWeight(synced, "t2"));
  });

  it("should not resize with invalid divider index", () => {
    const layout = createTerminalWorkspaceLayout();
    const synced = syncTerminalWorkspaceLayout(layout, ["t1", "t2"]);
    const result = resizeStackDivider(synced, ["t1", "t2"], 5, 0.2);
    expect(result).toBe(synced);
  });
});
