import type { PolicyEnforcer } from "./types";

export function createPolicyEnforcer(): PolicyEnforcer {
  switch (process.platform) {
    case "darwin": {
      const { DarwinSeatbeltEnforcer } = require("./darwin/seatbelt");
      const enforcer = new DarwinSeatbeltEnforcer();
      if (enforcer.isAvailable()) {
        return enforcer;
      }
      console.warn("[policy] sandbox-exec not available.");
      return createNoopEnforcer();
    }
    case "linux": {
      const { LinuxLandlockEnforcer } = require("./linux/landlock");
      const enforcer = new LinuxLandlockEnforcer();
      if (enforcer.isAvailable()) {
        return enforcer;
      }
      console.warn("[policy] Landlock not available (kernel 5.13+).");
      return createNoopEnforcer();
    }
    default:
      console.info("[policy] No OS-level file enforcement on this platform.");
      return createNoopEnforcer();
  }
}

export function getSandboxedSpawnArgs(
  shell: string,
  enforcer: PolicyEnforcer
): { command: string; args: string[] } | null {
  if ("getSandboxedSpawnArgs" in enforcer) {
    return (enforcer as any).getSandboxedSpawnArgs(shell);
  }
  return null;
}

function createNoopEnforcer(): PolicyEnforcer {
  return {
    isAvailable: () => false,
    applyProtection: async () => {},
    removeProtection: async () => {},
    cleanup: async () => {},
  };
}
