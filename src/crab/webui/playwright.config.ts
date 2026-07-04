import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

// E2E against a real `crab web` started on throwaway data dirs, so the
// developer's actual library/profiles are never touched. The committed static
// build is what gets served — run `npm run build` first (make verify-full does).
const repoRoot = resolve(fileURLToPath(new URL(".", import.meta.url)), "../../..");
const scratch = mkdtempSync(join(tmpdir(), "crab-e2e-"));
const PORT = 8878;

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: `${repoRoot}/.venv/bin/crab web --no-browser --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    env: {
      CRAB_WEB_CONFIG_DIR: join(scratch, "config"),
      CRAB_WEB_DATA_DIR: join(scratch, "data"),
    },
  },
});
