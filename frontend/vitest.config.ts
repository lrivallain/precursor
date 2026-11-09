import { defineConfig } from "vitest/config";

// Unit tests for pure frontend modules: `src/**/*.test.ts`, next to the code.
// Kept apart from vite.config.ts so the app's dev-server plugins don't load.
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
