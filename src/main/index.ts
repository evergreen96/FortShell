import { app, BrowserWindow, Menu } from "electron";
import path from "path";
import { PtyManager } from "./core/terminal/pty-manager";
import { PolicyEngine } from "./core/policy/policy-engine";
import { FileWatcher } from "./core/workspace/file-watcher";
import { registerIpcHandlers } from "./core/ipc/handlers";
import { createPolicyEnforcer } from "./platform/index";

const ptyManager = new PtyManager();
const policyEngine = new PolicyEngine();
const fileWatcher = new FileWatcher();
const enforcer = createPolicyEnforcer();
policyEngine.setEnforcer(enforcer);
ptyManager.setEnforcer(enforcer);

let mainWindow: BrowserWindow | null = null;

function createWindow(): void {
  const windowOpts: Electron.BrowserWindowConstructorOptions = {
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    backgroundColor: "#10141a",
    title: app.name,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  };

  mainWindow = new BrowserWindow(windowOpts);

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(
      path.join(__dirname, "../dist-renderer/index.html")
    );
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

registerIpcHandlers(ptyManager, policyEngine, fileWatcher);

function buildMenu(): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        {
          label: "Settings...",
          accelerator: "Cmd+,",
          click: () => {
            const win = BrowserWindow.getFocusedWindow();
            if (win) win.webContents.send("app:toggle-settings");
          },
        },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Window",
      submenu: [
        { role: "minimize" },
        { role: "zoom" },
        { role: "close" },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  buildMenu();
  createWindow();
});

app.on("window-all-closed", () => {
  ptyManager.destroyAll();
  fileWatcher.close();
  policyEngine.cleanup().finally(() => {
    app.quit();
  });
});

app.on("before-quit", () => {
  fileWatcher.close();
  policyEngine.cleanup().catch(() => {});
});

app.on("activate", () => {
  if (app.isReady() && BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
