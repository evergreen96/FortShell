type ProtectionRefreshGuardInput = {
  requestedWorkspacePath: string | null;
  currentWorkspacePath: string | null;
  requestId: number;
  latestRequestId: number;
};

type PolicyChangeRefreshInput = {
  eventWorkspacePath: string | null;
  currentWorkspacePath: string | null;
};

type ProtectionRuleRemovalToastInput =
  | { removed: boolean }
  | { error: unknown };

export function shouldApplyProtectionRefreshResult(
  input: ProtectionRefreshGuardInput
): boolean {
  return (
    input.requestId === input.latestRequestId &&
    input.requestedWorkspacePath === input.currentWorkspacePath
  );
}

export function shouldRefreshForPolicyChange(
  input: PolicyChangeRefreshInput
): boolean {
  return (
    input.currentWorkspacePath !== null &&
    input.eventWorkspacePath === input.currentWorkspacePath
  );
}

export function getProtectionRuleRemovalToastMessage(
  input: ProtectionRuleRemovalToastInput
): string {
  if ("error" in input) {
    return input.error instanceof Error ? input.error.message : String(input.error);
  }

  return input.removed ? "Rule removed" : "Rule not found";
}
