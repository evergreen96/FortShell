import { describe, it, expect } from "vitest";
import { parseImportedWorkspacePolicy } from "../../src/main/core/config/policy-store";

describe("policy-store import validation", () => {
  it("rejects non-v3 import payloads even when they contain a rules array", () => {
    const parsed = parseImportedWorkspacePolicy(
      JSON.stringify({
        version: 2,
        workspaceRoot: "/tmp/workspace",
        rules: [
          {
            id: "legacy-rule",
            kind: "path",
            source: "import",
            targetPath: ".env",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "/tmp/import-policy.json"
    );

    expect(parsed).toBeNull();
  });

  it("rejects malformed rule entries instead of leaking parser internals", () => {
    const parsed = parseImportedWorkspacePolicy(
      JSON.stringify({
        version: 3,
        workspaceRoot: "/tmp/workspace",
        rules: [
          {
            id: "broken-rule",
            kind: "path",
            source: "import",
          },
        ],
        updatedAt: "2026-04-03T00:00:00.000Z",
      }),
      "/tmp/import-policy.json"
    );

    expect(parsed).toBeNull();
  });
});
