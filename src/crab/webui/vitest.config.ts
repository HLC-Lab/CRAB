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
    // sbatchman branch only: these cover the Jobs/Results/report code that this branch
    // keeps in the tree but never routes to (ADR-026). They still run on the product line.
    exclude: [
      "tests/unit/ansi.spec.ts",
      "tests/unit/groupExperimentsBySubmission.spec.ts",
      "tests/unit/jobDetail.store.spec.ts",
      "tests/unit/jobKey.spec.ts",
      "tests/unit/jobs.store.spec.ts",
      "tests/unit/jobStatus.spec.ts",
      "tests/unit/report.store.spec.ts",
      "tests/unit/resultsChart.spec.ts",
      "tests/unit/resultsCompareWorkbench.spec.ts",
      "tests/unit/resultsIndex.spec.ts",
      "tests/unit/resultsPlot.spec.ts",
      "tests/unit/results.store.spec.ts",
      "tests/unit/resultsTable.spec.ts",
    ],
  },
});
