export type SessionLaunchMode =
  | "sandboxed"
  | "plain-shell-fallback"
  | "launch-failed";

export type TerminalTrustState =
  | "protected"
  | "unprotected"
  | "stale-policy"
  | "fallback"
  | "launch-failed"
  | "exited";

export type TerminalSessionRuntime = {
  terminalId: string;
  displayName: string;
  shell: string;
  launchMode: SessionLaunchMode;
  trustState: TerminalTrustState;
  policyRevision: number;
  startedAt: string;
  layoutSlotKey?: string;
  staleReason?: "policy-changed";
  launchFailureReason?: string;
  launchFailureDetail?: string;
};

export function createSessionRuntime(input: {
  terminalId: string;
  displayName: string;
  shell: string;
  policyRevision: number;
  launchMode: SessionLaunchMode;
  layoutSlotKey?: string;
}): TerminalSessionRuntime {
  return {
    terminalId: input.terminalId,
    displayName: input.displayName,
    shell: input.shell,
    launchMode: input.launchMode,
    trustState: input.launchMode === "sandboxed" ? "protected" : "unprotected",
    policyRevision: input.policyRevision,
    startedAt: new Date().toISOString(),
    layoutSlotKey: input.layoutSlotKey,
  };
}

export function markPolicyRevisionChanged(
  runtime: TerminalSessionRuntime,
  nextPolicyRevision: number
): TerminalSessionRuntime {
  if (runtime.launchMode !== "sandboxed" || runtime.policyRevision === nextPolicyRevision) {
    return runtime;
  }

  return {
    ...runtime,
    trustState: "stale-policy",
    staleReason: "policy-changed",
  };
}

export function markLaunchFallback(
  runtime: TerminalSessionRuntime,
  reason: string,
  detail?: string
): TerminalSessionRuntime {
  return {
    ...runtime,
    launchMode: "plain-shell-fallback",
    trustState: "fallback",
    launchFailureReason: reason,
    launchFailureDetail: detail,
  };
}

export function markLaunchFailed(input: {
  terminalId: string;
  displayName: string;
  shell: string;
  policyRevision: number;
  launchFailureReason: string;
  launchFailureDetail?: string;
}): TerminalSessionRuntime {
  return {
    terminalId: input.terminalId,
    displayName: input.displayName,
    shell: input.shell,
    launchMode: "launch-failed",
    trustState: "launch-failed",
    policyRevision: input.policyRevision,
    startedAt: new Date().toISOString(),
    launchFailureReason: input.launchFailureReason,
    launchFailureDetail: input.launchFailureDetail,
  };
}
