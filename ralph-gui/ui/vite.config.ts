import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // Tauri dev server: prevent Vite from clearing the terminal on startup
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // Tauri recommends using polling for some OS/FS combinations
      ignored: ["**/src-tauri/**"],
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    // When the system has NODE_ENV=production set globally, React loads its
    // production build which does not support act(). The globalSetup file
    // sets NODE_ENV=development before workers are spawned so workers inherit it.
    globalSetup: ["./src/global-test-setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html"],
      exclude: [
        // Types-only files have no executable code — excluding avoids false 0% coverage.
        "src/types/**",
        // Entry point — not independently testable.
        "src/main.tsx",
        // Test infrastructure — not product code.
        "src/test-setup.ts",
        "src/global-test-setup.ts",
        // Vite config file — not product code.
        "vite.config.ts",
      ],
      thresholds: {
        statements: 80,
        branches: 80,
        functions: 80,
        lines: 80,
      },
    },
  },
});
