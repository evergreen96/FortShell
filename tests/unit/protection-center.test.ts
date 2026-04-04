import { describe, expect, it } from "vitest";
import {
  getBlockedSearchSummary,
  getProtectionAction,
  getRemovalImpactMessage,
} from "../../src/renderer/components/Protection/ProtectionCenter";
import {
  getProtectionRuleRemovalToastMessage,
  shouldApplyProtectionRefreshResult,
  shouldRefreshForPolicyChange,
} from "../../src/renderer/lib/protection-refresh";

describe("getProtectionAction", () => {
  it("returns view-source for rule-generated entries", () => {
    expect(
      getProtectionAction({
        name: ".env",
        path: "/repo/.env",
        relativePath: ".env",
        isDirectory: false,
        type: "file",
        status: "shielded",
        sourceLabel: "Env Files Preset",
        sourceRuleId: "preset-1",
        sourceKind: "preset",
        canRemoveDirectly: false,
      })
    ).toBe("view-source");
  });
});

describe("getRemovalImpactMessage", () => {
  it("describes the compiled-path impact count before removal", () => {
    expect(getRemovalImpactMessage(3)).toBe("Remove this rule? It will unshield 3 concrete paths.");
  });
});

describe("getBlockedSearchSummary", () => {
  it("explains blocked paths even when some visible results remain", () => {
    expect(
      getBlockedSearchSummary([
        {
          relativePath: ".env",
          sourceLabel: "Env Files Preset",
          reason: "duplicate",
        },
        {
          relativePath: "secrets/db.txt",
          sourceLabel: "Directory Folder",
          reason: "contained",
        },
      ])
    ).toBe(
      "Blocked: .env is already protected by Env Files Preset. secrets/db.txt is already covered by Directory Folder."
    );
  });
});

describe("shouldApplyProtectionRefreshResult", () => {
  it("rejects stale refreshes from older requests or prior workspaces", () => {
    expect(
      shouldApplyProtectionRefreshResult({
        requestedWorkspacePath: "/repo-a",
        currentWorkspacePath: "/repo-b",
        requestId: 2,
        latestRequestId: 3,
      })
    ).toBe(false);
  });
});

describe("shouldRefreshForPolicyChange", () => {
  it("ignores policy-change events for a different workspace during a switch", () => {
    expect(
      shouldRefreshForPolicyChange({
        eventWorkspacePath: "/repo-b",
        currentWorkspacePath: "/repo-a",
      })
    ).toBe(false);
  });
});

describe("getProtectionRuleRemovalToastMessage", () => {
  it("preserves backend errors instead of collapsing them into not-found", () => {
    expect(
      getProtectionRuleRemovalToastMessage({
        error: new Error("remove failed"),
      })
    ).toBe("remove failed");
  });
});
