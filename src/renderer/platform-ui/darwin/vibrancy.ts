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
    background: rgba(24, 24, 37, 0.8);
    -webkit-backdrop-filter: blur(20px);
    backdrop-filter: blur(20px);
  }

  .platform-darwin .sidebar {
    background: rgba(24, 24, 37, 0.7);
    -webkit-backdrop-filter: blur(20px);
    backdrop-filter: blur(20px);
  }

  .platform-darwin .status-bar {
    background: rgba(24, 24, 37, 0.8);
    -webkit-backdrop-filter: blur(20px);
    backdrop-filter: blur(20px);
  }
`;
