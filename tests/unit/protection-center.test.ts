import { describe, expect, it } from "vitest";
import { getProtectionAction } from "../../src/renderer/components/Protection/ProtectionCenter";

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
