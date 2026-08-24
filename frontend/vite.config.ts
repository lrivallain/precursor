import { defineConfig, loadEnv } from "vite";
import type { Plugin, ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Where the host runtime module is served from. Plugin bundles never reference
// this path directly — they import bare specifiers, which the injected import
// map resolves here. It must stay unhashed so the map can be static, and stable
// across releases because installed plugin bundles depend on it.
const HOST_RUNTIME_ENTRY = "host-runtime";
const HOST_RUNTIME_URL = `/assets/${HOST_RUNTIME_ENTRY}.js`;
const HOST_RUNTIME_SRC = "/src/host/runtime.ts";

/**
 * Publish the host runtime to plugin bundles.
 *
 * A plugin's frontend is built separately and loaded at runtime from its Python
 * package, so it can't be bundled against the app's React. Instead it leaves
 * `react`, `react-dom`, `react/jsx-runtime` and `@precursor/host` external, and
 * this import map points every one of them at the host's own module — which is
 * what keeps a single React instance on the page.
 *
 * The map must appear before any module that relies on it, hence
 * `head-prepend`. In dev it targets the TypeScript source, which Vite serves
 * directly; in a build it targets the unhashed chunk configured below.
 */
function pluginRuntime(): Plugin {
  return {
    name: "precursor-plugin-runtime",
    transformIndexHtml(_html, ctx) {
      const target = ctx.server ? HOST_RUNTIME_SRC : HOST_RUNTIME_URL;
      const imports = {
        react: target,
        "react-dom": target,
        "react/jsx-runtime": target,
        "react/jsx-dev-runtime": target,
        "@precursor/host": target,
      };
      return [
        {
          tag: "script",
          attrs: { type: "importmap" },
          children: JSON.stringify({ imports }, null, 2),
          injectTo: "head-prepend" as const,
        },
      ];
    },
  };
}

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
    // The self-hosted draw.io editor is served by the backend from the data
    // dir; without this the SPA's catch-all would answer the editor iframe
    // with index.html.
    "/drawio": target,
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
    plugins: [react(), tailwindcss(), pluginRuntime()],
    server: {
      port: 5173,
      proxy,
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      rollupOptions: {
        // Without this Rollup strips the runtime chunk's exports — an HTML entry
        // needs none, so the default is to drop them, which would leave plugin
        // bundles importing an empty module.
        preserveEntrySignatures: "strict",
        // Relative to Vite's `root`, so the config needs no Node typings.
        input: {
          index: "index.html",
          // Second entry so the runtime is emitted as its own chunk. React ends
          // up in a chunk both entries import, which is the point: the app and
          // any plugin resolve to the same instance.
          [HOST_RUNTIME_ENTRY]: "src/host/runtime.ts",
        },
        output: {
          entryFileNames: (chunk) =>
            chunk.name === HOST_RUNTIME_ENTRY
              ? `assets/${HOST_RUNTIME_ENTRY}.js`
              : "assets/[name]-[hash].js",
        },
      },
    },
  };
});
