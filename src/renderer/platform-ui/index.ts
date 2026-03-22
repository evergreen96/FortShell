import { applyDarwinStyles, darwinOverrides } from "./darwin/vibrancy";

export function applyPlatformStyles(): void {
  const platform = window.electronAPI?.platform;

  if (platform === "darwin") {
    applyDarwinStyles();
    const style = document.createElement("style");
    style.textContent = darwinOverrides;
    document.head.appendChild(style);
  } else {
    document.body.classList.add("platform-linux");
  }
}
