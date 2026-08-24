import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ConfirmProvider } from "./components/ConfirmDialog";
import "./index.css";
import { applyInitialTheme } from "./lib/theme";
// Side-effect import: registers every bundled plugin's frontend half before
// the app mounts, so a section is available the moment its backend
// descriptor arrives.
import "./plugins";

applyInitialTheme();

// Register the PWA service worker in production builds only. It enables
// "install to home screen" / standalone-window launch; it does no caching, so
// Vite dev (where import.meta.env.PROD is false) is left untouched to avoid
// serving a stale shell during development.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Installability is a progressive enhancement; ignore registration
      // failures so the app still works when service workers are unavailable.
    });
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfirmProvider>
      <App />
    </ConfirmProvider>
  </React.StrictMode>,
);
