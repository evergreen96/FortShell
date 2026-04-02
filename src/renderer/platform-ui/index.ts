import { applyDarwinStyles, darwinOverrides } from "./darwin/vibrancy";

const WINDOW_RESIZE_START_EVENT = "fortshell:window-resize-start";
const WINDOW_RESIZE_END_EVENT = "fortshell:window-resize-end";

function installResizeStabilityMode(): void {
  let resizeTimeout: ReturnType<typeof setTimeout> | null = null;
  let isResizing = false;

  const clearResizeState = () => {
    document.body.classList.remove("is-window-resizing");
    if (isResizing) {
      window.dispatchEvent(new Event(WINDOW_RESIZE_END_EVENT));
      isResizing = false;
    }
    resizeTimeout = null;
  };

  window.addEventListener("resize", () => {
    if (!isResizing) {
      window.dispatchEvent(new Event(WINDOW_RESIZE_START_EVENT));
      isResizing = true;
    }
    document.body.classList.add("is-window-resizing");
    if (resizeTimeout) clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(clearResizeState, 140);
  });
}

export function applyPlatformStyles(): void {
  const platform = window.electronAPI?.platform;
  installResizeStabilityMode();

  if (platform === "darwin") {
    applyDarwinStyles();
    const style = document.createElement("style");
    style.textContent = darwinOverrides;
    document.head.appendChild(style);
  } else {
    document.body.classList.add("platform-linux");
  }
}
