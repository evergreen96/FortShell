type ProtectionRefreshGuardInput = {
  requestedWorkspacePath: string | null;
  currentWorkspacePath: string | null;
  requestId: number;
  latestRequestId: number;
};

export function shouldApplyProtectionRefreshResult(
  input: ProtectionRefreshGuardInput
): boolean {
  return (
    input.requestId === input.latestRequestId &&
    input.requestedWorkspacePath === input.currentWorkspacePath
  );
}
