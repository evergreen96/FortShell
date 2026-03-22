import { useState, useEffect } from "react";
import type { ShellProfile } from "../../lib/types";

type SettingsProps = {
  onClose: () => void;
  profiles: ShellProfile[];
  onConfigChange: (config: AppConfig) => void;
  onProfilesChanged: () => void;
};

type CustomProfile = {
  label: string;
  command: string;
};

export type AppConfig = {
  defaultShell: string | null;
  fontSize: number;
  theme: string;
  sidebarWidth: number;
  defaultLayout: string;
  customProfiles: CustomProfile[];
};

export function Settings({ onClose, profiles, onConfigChange, onProfilesChanged }: SettingsProps) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [newCommand, setNewCommand] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);

  useEffect(() => {
    window.electronAPI.configGet().then((c) => setConfig(c as AppConfig));
  }, []);

  async function updateConfig(partial: Partial<AppConfig>) {
    const updated = await window.electronAPI.configSet(partial);
    setConfig(updated as AppConfig);
    onConfigChange(updated as AppConfig);
  }

  async function addProfile() {
    if (!newLabel.trim() || !newCommand.trim() || !config) return;
    const updated = [
      ...(config.customProfiles || []),
      { label: newLabel.trim(), command: newCommand.trim() },
    ];
    await updateConfig({ customProfiles: updated });
    setNewLabel("");
    setNewCommand("");
    setShowAddForm(false);
    onProfilesChanged();
  }

  async function removeProfile(index: number) {
    if (!config) return;
    const updated = (config.customProfiles || []).filter((_, i) => i !== index);
    await updateConfig({ customProfiles: updated });
    onProfilesChanged();
  }

  if (!config) return null;

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="settings-header">
          <h2>Settings</h2>
          <button className="settings-close" onClick={onClose}>&times;</button>
        </div>

        <div className="settings-body">
          <div className="settings-group">
            <label>Font Size</label>
            <input
              type="number"
              min={10}
              max={24}
              value={config.fontSize}
              onChange={(e) => updateConfig({ fontSize: Number(e.target.value) })}
            />
          </div>

          <div className="settings-group">
            <label>Default Shell</label>
            <select
              value={config.defaultShell || ""}
              onChange={(e) =>
                updateConfig({ defaultShell: e.target.value || null })
              }
            >
              <option value="">Auto-detect</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.command}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          <div className="settings-group">
            <label>Default Layout</label>
            <select
              value={config.defaultLayout}
              onChange={(e) => updateConfig({ defaultLayout: e.target.value })}
            >
              <option value="horizontal">Horizontal</option>
              <option value="vertical">Vertical</option>
              <option value="grid">Grid</option>
            </select>
          </div>

          <div className="settings-group">
            <label>Sidebar Width</label>
            <input
              type="number"
              min={150}
              max={600}
              step={10}
              value={config.sidebarWidth}
              onChange={(e) => updateConfig({ sidebarWidth: Number(e.target.value) })}
            />
          </div>

          <div className="settings-section">
            <div className="settings-section-header">
              <h3>Custom Shell Profiles</h3>
              <button className="settings-add-btn" onClick={() => setShowAddForm((prev) => !prev)}>
                {showAddForm ? "Cancel" : "+ Add"}
              </button>
            </div>
            <p className="settings-description">
              Add custom shells or CLI tools. Use a command name in your PATH (e.g. <code>node</code>, <code>python3</code>) or an absolute path (e.g. <code>/opt/homebrew/bin/fish</code>).
            </p>
            {showAddForm && (
              <div className="settings-profile-add">
                <div className="settings-profile-field">
                  <label>Display Name</label>
                  <input
                    type="text"
                    placeholder="e.g. Node REPL, Python, Claude Code"
                    value={newLabel}
                    onChange={(e) => setNewLabel(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addProfile()}
                    autoFocus
                  />
                </div>
                <div className="settings-profile-field">
                  <label>Command</label>
                  <input
                    type="text"
                    placeholder="e.g. node, python3, /usr/local/bin/fish"
                    value={newCommand}
                    onChange={(e) => setNewCommand(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addProfile()}
                  />
                </div>
                <button className="settings-profile-save" onClick={addProfile}>Save Profile</button>
              </div>
            )}
            {(config.customProfiles || []).length > 0 && (
              <div className="settings-profiles-list">
                {config.customProfiles.map((cp, i) => (
                  <div key={i} className="settings-profile-item">
                    <div className="settings-profile-info">
                      <span className="settings-profile-label">{cp.label}</span>
                      <span className="settings-profile-cmd">{cp.command}</span>
                    </div>
                    <button
                      className="settings-profile-remove"
                      onClick={() => removeProfile(i)}
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="settings-footer">
          <span className="settings-hint">Cmd+, to toggle settings</span>
        </div>
      </div>
    </div>
  );
}
