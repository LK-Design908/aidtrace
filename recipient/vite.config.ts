import { defineConfig, Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

/** Emits sw.js with the full list of built files so the service worker can precache everything. */
function serviceWorker(): Plugin {
  return {
    name: "aidtrace-sw",
    apply: "build",
    generateBundle(_opts, bundle) {
      const files = ["./", "index.html", "manifest.webmanifest", "icon.svg", ...Object.keys(bundle).filter((f) => f !== "sw.js")];
      const version = createHash("sha256").update(files.join("|") + Date.now()).digest("hex").slice(0, 12);
      const src = readFileSync(new URL("./sw-template.js", import.meta.url), "utf8")
        .replace("__VERSION__", version)
        .replace("__PRECACHE__", JSON.stringify([...new Set(files)]));
      this.emitFile({ type: "asset", fileName: "sw.js", source: src });
    },
  };
}

export default defineConfig({
  base: "/recipient/",
  plugins: [react(), serviceWorker()],
  server: { port: 5174, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
  test: { environment: "node" },
} as any);
