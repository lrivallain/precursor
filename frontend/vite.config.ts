import { defineConfig, loadEnv } from "vite";
import type { ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  // Point the dev proxy at the real backend. `precursor --dev` injects
  // PRECURSOR_PORT / PRECURSOR_HOST into the environment so the proxy follows
  // `--port`; loadEnv also reads the repo-root .env (one level up) for a bare
  // `npm run dev`, with inline env vars winning over the file. Falls back to
  // 127.0.0.1:8000.
  const env = loadEnv(mode, "..", "PRECURSOR_");
  const host = env.PRECURSOR_HOST || "127.0.0.1";
  const connectHost = host === "0.0.0.0" || host === "::" ? "127.0.0.1" : host;
  const target = `http://${connectHost}:${env.PRECURSOR_PORT || "8000"}`;

  const proxy: Record<string, string | ProxyOptions> = {
    "/api": target,
    "/raw": target,
  };
  // When `precursor --dev` runs the live VitePress docs server it injects
  // PRECURSOR_DOCS_PORT; proxy /docs to it (ws:true for HMR) so the docs are
  // reachable in-app under the same origin, mirroring production's /docs mount.
  const docsPort = env.PRECURSOR_DOCS_PORT;
  if (docsPort) {
    proxy["/docs"] = {
      target: `http://${connectHost}:${docsPort}`,
      changeOrigin: true,
      ws: true,
    };
  }

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy,
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
