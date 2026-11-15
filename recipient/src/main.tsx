import { createRoot } from "react-dom/client";
import "./styles.css";
import App from "./App";
import { ready } from "./crypto";

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`, { scope: import.meta.env.BASE_URL }).catch(() => {});
}

ready.then(() => createRoot(document.getElementById("root")!).render(<App />));
