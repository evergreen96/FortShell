import { useState, useCallback } from "react";
import { destroyTerminalCache } from "../components/Terminal/TerminalPane";
import type { ShellProfile } from "../lib/types";

export type TerminalInfo = {
  id: string;
  name: string;
  status: "active" | "exited";
};

export function useTerminalState() {
  const [terminals, setTerminals] = useState<TerminalInfo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ShellProfile[]>([]);

  const loadProfiles = useCallback(async () => {
    const result = await window.electronAPI.terminalProfiles();
    setProfiles(result);
  }, []);

  const createTerminal = useCallback(
    async (opts?: { profileId?: string; cwd?: string }) => {
      const profile = opts?.profileId
        ? profiles.find((p) => p.id === opts.profileId)
        : profiles.find((p) => p.isDefault) || profiles[0];

      const result = await window.electronAPI.terminalCreate({
        shell: profile?.command,
        cwd: opts?.cwd,
      });

      setTerminals((prev) => [
        ...prev,
        { id: result.id, name: result.name, status: "active" },
      ]);
      setActiveId(result.id);
      return result;
    },
    [profiles]
  );

  const destroyTerminal = useCallback(
    async (id: string) => {
      await window.electronAPI.terminalDestroy(id);
      destroyTerminalCache(id);
      setTerminals((prev) => {
        const next = prev.filter((t) => t.id !== id);
        if (activeId === id) {
          setActiveId(next.length > 0 ? next[next.length - 1].id : null);
        }
        return next;
      });
    },
    [activeId]
  );

  const markExited = useCallback((id: string) => {
    setTerminals((prev) =>
      prev.map((t) =>
        t.id === id ? { ...t, status: "exited" as const } : t
      )
    );
  }, []);

  return {
    terminals,
    activeId,
    setActiveId,
    profiles,
    loadProfiles,
    createTerminal,
    destroyTerminal,
    markExited,
  };
}
