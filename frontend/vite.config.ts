import { defineConfig, loadEnv } from "vite";
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

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        "/api": target,
        "/raw": target,
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
