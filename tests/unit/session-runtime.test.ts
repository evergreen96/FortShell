import { describe, expect, it } from "vitest";
import {
  createSessionRuntime,
  createUnprotectedSessionRuntime,
  markPolicyRevisionChanged,
  markLaunchFallback,
  markLaunchFailed,
} from "../../src/main/core/terminal/session-runtime";

describe("session runtime trust model", () => {
  it("marks active protected sessions stale when policy revision changes", () => {
    const runtime = createSessionRuntime({
      terminalId: "term-1",
      displayName: "zsh (term-1)",
      shell: "zsh",
      policyRevision: 2,
      launchMode: "sandboxed",
    });

    const stale = markPolicyRevisionChanged(runtime, 3);

    expect(stale.trustState).toBe("stale-policy");
    expect(stale.policyRevision).toBe(2);
    expect(stale.staleReason).toBe("policy-changed");
  });

  it("does not mark exited sessions stale when policy revision changes", () => {
    const runtime = {
      ...createSessionRuntime({
        terminalId: "term-1b",
        displayName: "zsh (term-1b)",
        shell: "zsh",
        policyRevision: 2,
        launchMode: "sandboxed",
      }),
      trustState: "exited" as const,
    };

    const updated = markPolicyRevisionChanged(runtime, 3);

    expect(updated).toBe(runtime);
    expect(updated.trustState).toBe("exited");
    expect(updated.staleReason).toBeUndefined();
  });

  it("keeps fallback sessions out of stale bulk restart targeting", () => {
    const runtime = markLaunchFallback(
      createSessionRuntime({
        terminalId: "term-2",
        displayName: "zsh (term-2)",
        shell: "zsh",
        policyRevision: 4,
        launchMode: "sandboxed",
      }),
      "wrapper missing",
      "sandbox-wrapper binary not found"
    );

    expect(runtime.trustState).toBe("fallback");
    expect(runtime.launchMode).toBe("plain-shell-fallback");
  });

  it("tracks plain shell sessions without active protections as unprotected", () => {
    const runtime = createUnprotectedSessionRuntime({
      terminalId: "term-plain",
      displayName: "zsh (term-plain)",
      shell: "zsh",
      policyRevision: 0,
    });

    expect(runtime.trustState).toBe("unprotected");
    expect(runtime.launchMode).toBe("plain-shell");
  });

  it("marks unprotected sessions stale when policy revision changes", () => {
    const runtime = createUnprotectedSessionRuntime({
      terminalId: "term-plain-2",
      displayName: "zsh (term-plain-2)",
      shell: "zsh",
      policyRevision: 0,
    });

    const stale = markPolicyRevisionChanged(runtime, 1);

    expect(stale.trustState).toBe("stale-policy");
    expect(stale.staleReason).toBe("policy-changed");
  });

  it("records launch-failed sessions with retryable metadata", () => {
    const runtime = markLaunchFailed({
      terminalId: "term-3",
      displayName: "zsh (term-3)",
      shell: "zsh",
      policyRevision: 4,
      launchFailureReason: "spawn failed",
      launchFailureDetail: "ENOENT",
    });

    expect(runtime.trustState).toBe("launch-failed");
    expect(runtime.launchMode).toBe("launch-failed");
    expect(runtime.launchFailureReason).toBe("spawn failed");
    expect(runtime.launchRetryable).toBe(true);
  });
});
