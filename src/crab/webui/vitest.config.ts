import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests only (pure logic, above all the config round-trip suite).
// Browser flows live in tests/e2e (Playwright), not here.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["tests/unit/**/*.spec.ts"],
  },
});
