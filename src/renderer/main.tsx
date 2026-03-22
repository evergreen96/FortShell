import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyPlatformStyles } from "./platform-ui/index";
import "./styles/global.css";

applyPlatformStyles();

const root = createRoot(document.getElementById("root")!);
root.render(<App />);
