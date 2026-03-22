import { describe, it, expect } from "vitest";
import { detectProfiles, getDefaultProfile } from "../../src/main/core/terminal/profiles";

describe("Terminal Profiles", () => {
  it("should detect at least one profile", () => {
    const profiles = detectProfiles();
    expect(profiles.length).toBeGreaterThan(0);
  });

  it("should have at least one default profile", () => {
    const profiles = detectProfiles();
    const defaultProfile = getDefaultProfile(profiles);
    expect(defaultProfile).toBeDefined();
  });

  it("should have valid profile structure", () => {
    const profiles = detectProfiles();
    for (const p of profiles) {
      expect(p.id).toBeTruthy();
      expect(p.label).toBeTruthy();
      expect(p.command).toBeTruthy();
      expect(Array.isArray(p.args)).toBe(true);
      expect(typeof p.isDefault).toBe("boolean");
    }
  });
});
