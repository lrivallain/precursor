// monaco-yaml's worker, re-exported from a source file so Vite pre-bundles it:
// loaded straight from node_modules, its CommonJS dependencies break in dev.
// This is monaco-yaml's documented workaround for Vite.
import "monaco-yaml/yaml.worker.js";
