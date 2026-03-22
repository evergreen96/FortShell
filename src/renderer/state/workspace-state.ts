import { useState, useCallback } from "react";

export function useWorkspaceState() {
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [protectedPaths, setProtectedPaths] = useState<Set<string>>(
    new Set()
  );

  const openFolder = useCallback(async () => {
    const path = await window.electronAPI.openFolder();
    if (path) setWorkspacePath(path);
    return path;
  }, []);

  const selectRecent = useCallback((path: string) => {
    setWorkspacePath(path);
  }, []);

  const loadPolicy = useCallback(async () => {
    const paths = await window.electronAPI.policyList();
    setProtectedPaths(new Set(paths));
  }, []);

  const protect = useCallback(async (filePath: string) => {
    await window.electronAPI.policySet(filePath);
    setProtectedPaths((prev) => new Set([...prev, filePath]));
  }, []);

  const unprotect = useCallback(async (filePath: string) => {
    await window.electronAPI.policyRemove(filePath);
    setProtectedPaths((prev) => {
      const next = new Set(prev);
      next.delete(filePath);
      return next;
    });
  }, []);

  return {
    workspacePath,
    setWorkspacePath,
    protectedPaths,
    openFolder,
    selectRecent,
    loadPolicy,
    protect,
    unprotect,
  };
}
