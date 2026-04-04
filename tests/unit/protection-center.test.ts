import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  ProtectionCenter,
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

describe("ProtectionCenter coverage rules layout", () => {
  it("renders extension controls first and keeps preset rows minimal", () => {
    const markup = renderToStaticMarkup(
      createElement(ProtectionCenter, {
        rootPath: "/repo",
        presets: [
          {
            id: "env-files",
            label: "Env Files",
            description: "Protect common env files.",
            rules: [{ kind: "path", value: ".env" }],
          },
        ],
        rules: [
          {
            id: "rule-extension-1",
            kind: "extension",
            source: "extension",
            extensions: [".env"],
          },
        ],
        compiledEntries: [],
        focusedSourceRuleId: null,
        onApplyPreset: async () => ({ changed: true }),
        onAddExtensionRule: async () => ({ changed: true }),
        onAddManualPath: async () => true,
        onRemoveRule: async () => true,
        onFocusSource: () => {},
        onClearFocusedSource: () => {},
      })
    );

    expect(markup).toContain("Coverage Rules");
    expect(markup).not.toContain("Built-in policy packs");
    expect(markup).not.toContain("Extension-driven coverage");
    expect(markup.indexOf("Extension List")).toBeLessThan(markup.indexOf("Env Files"));
    expect(markup).toContain("Env Files");
    expect(markup).not.toContain("selectors");
    expect(markup).not.toContain("Protect common env files.");
    expect(markup).toContain("Add Rule");
    expect(markup).toContain("protection-source-row");
    expect(markup).not.toContain("protection-preset-card");
  });

  it("keeps manual add as input-only and removes the separate direct rules list", () => {
    const markup = renderToStaticMarkup(
      createElement(ProtectionCenter, {
        rootPath: "/repo",
        presets: [],
        rules: [
          {
            id: "rule-path-1",
            kind: "path",
            source: "manual",
            targetPath: ".env",
          },
        ],
        compiledEntries: [
          {
            name: ".env",
            path: "/repo/.env",
            relativePath: ".env",
            isDirectory: false,
            type: "file",
            status: "shielded",
            canRemoveDirectly: true,
            sourceRuleId: "rule-path-1",
            sourceKind: "path",
            sourceLabel: "Manual Path",
          },
        ],
        focusedSourceRuleId: null,
        onApplyPreset: async () => ({ changed: true }),
        onAddExtensionRule: async () => ({ changed: true }),
        onAddManualPath: async () => true,
        onRemoveRule: async () => true,
        onFocusSource: () => {},
        onClearFocusedSource: () => {},
      })
    );

    expect(markup).not.toContain("Direct Rules");
    expect(markup).not.toContain("No direct rules yet.");
    expect(markup).toContain("Active Protection List");
    expect(markup).toContain("Manual Path");
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
