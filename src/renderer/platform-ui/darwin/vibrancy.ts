/**
 * macOS vibrancy/transparency effects.
 * Applied via Electron's BrowserWindow vibrancy option.
 * This module provides CSS class names for macOS-specific styling.
 */

export function applyDarwinStyles(): void {
  document.body.classList.add("platform-darwin");
}

export const darwinOverrides = `
  .platform-darwin .app-header {
    background: rgba(10, 14, 20, 0.96);
  }

  .platform-darwin .nav-rail,
  .platform-darwin .explorer-pane,
  .platform-darwin .content-area,
  .platform-darwin .terminal-area {
    background: rgba(24, 28, 34, 0.96);
  }
`;
