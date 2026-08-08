import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/theme.css";

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);

// Register the service worker for PWA installability/offline app-shell
// loading (see public/sw.js). Guarded by a feature check since older
// browsers don't support service workers at all — this simply does
// nothing on those instead of throwing an error.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((error) => {
      console.warn("Service worker registration failed:", error);
    });
  });
}
