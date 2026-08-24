import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import pkg from "./package.json";

// Vite config files aren't type-checked against Node's typings here (they are
// bundled and run by Vite itself), so declare the one global we need rather than
// pulling @types/node in for a single lookup.
declare const process: { env: Record<string, string | undefined> };

/**
 * Build the frontend of an **in-repo** plugin.
 *
 * A plugin's UI is loaded at runtime from its Python wheel, so it is a separate
 * bundle from the app's. It still builds with the *host's* toolchain, driven by
 * this config: that avoids a second npm project (and lockfile) per plugin and,
 * more importantly, guarantees the plugin is compiled against exactly the React
 * the host ships — which is the instance it will share at runtime.
 *
 * Convention, keyed off the distribution name in `PRECURSOR_PLUGIN`:
 *
 *   plugins/<dist>/web/src/index.tsx        -> entry (calls registerSection)
 *   plugins/<dist>/src/<module>/web/index.js -> output, shipped in the wheel
 *
 * where `<module>` is `<dist>` with dashes turned into underscores, matching the
 * import package. Precursor serves that directory at
 * `/api/plugins/<id>/assets/…`.
 *
 * A third-party plugin brings its own toolchain; all it has to reproduce is the
 * externals below and the output filename.
 */
const dist = process.env.PRECURSOR_PLUGIN;
if (!dist) {
  throw new Error(
    "PRECURSOR_PLUGIN must name the plugin distribution to build, e.g. precursor-kanban",
  );
}
const module = dist.replace(/-/g, "_");
const root = new URL(`../plugins/${dist}/web/`, import.meta.url);
const outDir = new URL(`../plugins/${dist}/src/${module}/web/`, import.meta.url);

// A plugin's sources sit outside `frontend/`, so Node-style resolution walking
// up from them never reaches the host's `node_modules`. Alias every runtime
// dependency the host already ships, which is exactly the set an in-repo plugin
// is entitled to use — and means it can't quietly pull in a *different* copy of
// one of them.
const hostDeps = Object.fromEntries(
  Object.keys(pkg.dependencies).map((name) => [
    name,
    new URL(`node_modules/${name}`, import.meta.url).pathname,
  ]),
);

export default defineConfig({
  root: root.pathname,
  plugins: [react()],
  resolve: { alias: hostDeps },
  build: {
    outDir: outDir.pathname,
    emptyOutDir: true,
    lib: {
      entry: "src/index.tsx",
      formats: ["es"],
      // Fixed name: the backend advertises `<package>/web/index.js` as the
      // entry and the host imports exactly that URL.
      fileName: () => "index.js",
    },
    rollupOptions: {
      // The whole trick. These are *not* bundled; the import map the host
      // injects points each at its own `host-runtime.js`, so there is one React
      // on the page (a second copy would break every hook the plugin calls) and
      // the plugin gets the Precursor SDK without vendoring it.
      external: [
        "react",
        "react-dom",
        "react/jsx-runtime",
        "react/jsx-dev-runtime",
        "@precursor/host",
      ],
    },
  },
});
