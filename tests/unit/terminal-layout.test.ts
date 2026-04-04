import { describe, it, expect } from "vitest";
import {
  createTerminalWorkspaceLayout,
  getStaleSessions,
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

  it("selects only stale-policy sessions for bulk restart", () => {
    const staleSessions = getStaleSessions([
      {
        terminalId: "term-1",
        displayName: "Protected",
        shell: "zsh",
        trustState: "protected",
        launchMode: "sandboxed",
        policyRevision: 4,
        startedAt: "2026-04-03T00:00:00.000Z",
      },
      {
        terminalId: "term-2",
        displayName: "Stale",
        shell: "zsh",
        trustState: "stale-policy",
        launchMode: "sandboxed",
        policyRevision: 3,
        startedAt: "2026-04-03T00:01:00.000Z",
        staleReason: "policy-changed",
      },
      {
        terminalId: "term-3",
        displayName: "Fallback",
        shell: "zsh",
        trustState: "fallback",
        launchMode: "plain-shell-fallback",
        policyRevision: 4,
        startedAt: "2026-04-03T00:02:00.000Z",
      },
      {
        terminalId: "term-4",
        displayName: "Launch failed",
        shell: "zsh",
        trustState: "launch-failed",
        launchMode: "launch-failed",
        policyRevision: 4,
        startedAt: "2026-04-03T00:03:00.000Z",
      },
      {
        terminalId: "term-5",
        displayName: "Exited",
        shell: "zsh",
        trustState: "exited",
        launchMode: "sandboxed",
        policyRevision: 4,
        startedAt: "2026-04-03T00:04:00.000Z",
      },
    ]);

    expect(staleSessions.map((session) => session.terminalId)).toEqual(["term-2"]);
  });
});
