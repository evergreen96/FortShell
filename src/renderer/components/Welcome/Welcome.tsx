import { useEffect, useState } from "react";

type WelcomeProps = {
  onOpenFolder: () => void;
  onSelectRecent: (path: string) => void;
};

export function Welcome({ onOpenFolder, onSelectRecent }: WelcomeProps) {
  const [recentPaths, setRecentPaths] = useState<string[]>([]);

  useEffect(() => {
    window.electronAPI.workspaceRecent().then(setRecentPaths);
  }, []);

  return (
    <div className="welcome">
      <div className="welcome-content">
        <h1 className="welcome-title">{window.electronAPI.appName}</h1>
        <p className="welcome-subtitle">Terminal-first multi AI CLI management IDE</p>

        <button className="welcome-open-btn" onClick={onOpenFolder}>
          Open Folder
        </button>

        <div className="welcome-guide">
          <div className="welcome-guide-item">
            <span className="welcome-guide-key">Open Folder</span>
            <span>to start a workspace with file tree + terminals</span>
          </div>
          <div className="welcome-guide-item">
            <span className="welcome-guide-key">Right-click file</span>
            <span>to protect — blocks AI CLI access at OS kernel level</span>
          </div>
          <div className="welcome-guide-item">
            <span className="welcome-guide-key">Cmd+B</span>
            <span>toggle sidebar</span>
          </div>
          <div className="welcome-guide-item">
            <span className="welcome-guide-key">Cmd+,</span>
            <span>settings</span>
          </div>
        </div>

        {recentPaths.length > 0 && (
          <div className="welcome-recent">
            <h3>Recent</h3>
            <ul>
              {recentPaths.map((p) => (
                <li key={p}>
                  <button
                    className="welcome-recent-item"
                    onClick={() => onSelectRecent(p)}
                    title={p}
                  >
                    {p.split(/[/\\]/).pop()}
                    <span className="welcome-recent-path">{p}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
