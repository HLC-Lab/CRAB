/**
 * Submit and monitor a real local job through the dashboard: a `local`
 * remote, a config with one tiny wrapper, Submit with the `local` preset, then
 * watch the Jobs view until the job completes and read its log.
 *
 * Isolation: playwright.config.ts points XDG_STATE_HOME at a scratch dir (the
 * local scheduler's job records), the run's data path is a temp dir, and the
 * test fails if the repo's own `data/` changed.
 */
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const repoRoot = resolve(fileURLToPath(new URL(".", import.meta.url)), "../../../../..");
const repoData = join(repoRoot, "data");

const WRAPPER = `from crab.wrappers.base import base


class app(base):
    metadata = [{"name": "v", "unit": "x", "conv": True}]

    def run_app(self):
        return "sh -c 'sleep 1; echo e2e ran'"
`;

// Every path under dir with its size and mtime, so any created, removed or
// touched file shows up as a difference.
function snapshot(dir: string): string[] {
  const out: string[] = [];
  const walk = (d: string) => {
    for (const name of readdirSync(d).sort()) {
      const full = join(d, name);
      const st = statSync(full);
      out.push(`${full} ${st.isDirectory() ? "dir" : st.size} ${st.mtimeMs}`);
      if (st.isDirectory()) walk(full);
    }
  };
  walk(dir);
  return out;
}

// Pids of processes whose command line mentions the marker (the wrapper path
// is unique to this test's job).
function leftovers(marker: string): string[] {
  try {
    return execFileSync("pgrep", ["-f", marker], { encoding: "utf8" }).trim().split("\n");
  } catch {
    return []; // pgrep exits 1 when nothing matches
  }
}

let work = "";
let dataBefore: string[] = [];

test.beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "crab-e2e-job-"));
  mkdirSync(join(work, "out"));
  writeFileSync(join(work, "sleep_wrapper.py"), WRAPPER);
  dataBefore = snapshot(repoData);
});

test.afterAll(() => {
  if (work) rmSync(work, { recursive: true, force: true });
});

test("submit a local job and watch it complete", async ({ page }) => {
  test.setTimeout(150_000);
  const wrapper = join(work, "sleep_wrapper.py");

  // --- Remotes: a local cluster, connected -----------------------------------
  await page.goto("/");
  await page.getByRole("link", { name: "Remotes" }).click();
  await page.getByRole("button", { name: "+ Add cluster" }).click();
  await page.getByPlaceholder("my-cluster").fill("laptop");
  await page.locator('label:has-text("Transport") select').selectOption("local");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const row = page.locator(".list li", { hasText: "laptop" });
  await row.getByRole("button", { name: "Connect" }).click();
  await expect(row.locator(".dot.on")).toBeVisible({ timeout: 30_000 });
  await expect(row.locator(".info")).toContainText("CRAB");

  // --- Author: one experiment, one app, no metric collection ------------------
  await page.getByRole("link", { name: "Author" }).click();
  await page.locator(".job-grid label.wide input").fill("e2e_local");
  await page.locator('label:has-text("Total nodes to allocate") input').fill("1");

  await page.locator(".rail li", { hasText: "Run settings" }).click();
  await page.locator('label:has-text("Data path") input').fill(join(work, "out"));
  await page.locator('label:has-text("Min runs") input').fill("1");
  await page.locator('label:has-text("Max runs") input').fill("1");

  await page.locator(".zone .add").click(); // + experiment
  await page.locator('.exp-edit label:has-text("Experiment name") input').fill("exp1");
  await page.getByRole("button", { name: "+ Add app" }).click();
  await page.getByRole("button", { name: "Choose wrapper…" }).click();
  await page.getByPlaceholder("Search wrappers by name, path, or group…").fill(wrapper);
  await page.locator(".wm-add").click();
  await expect(page.locator(".chip-text", { hasText: wrapper })).toBeVisible();
  await page.getByRole("switch", { name: "collect metrics" }).click();
  await expect(page.getByRole("switch", { name: "collect metrics" })).toHaveAttribute(
    "aria-checked",
    "false",
  );

  // --- Submit to the local remote with the local preset -----------------------
  await page.getByRole("button", { name: "Submit…" }).click();
  const modal = page.locator(".modal");
  await modal.locator('label:has-text("Remote") select').selectOption("laptop");
  await modal.locator('label:has-text("Preset") select').selectOption("local");
  await modal.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(modal).toBeHidden({ timeout: 30_000 });

  // --- Jobs: the job appears and completes ------------------------------------
  await page.getByRole("link", { name: "Jobs" }).click();
  const card = page.locator(".list li.card", { hasText: "e2e_local" });
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.locator(".state")).toHaveText("COMPLETED", { timeout: 60_000 });

  // Its job log shows output from the worker.
  await card.getByRole("button", { name: "Logs", exact: true }).click();
  await expect(card.locator(".logs pre")).toContainText(
    /Execution settings from execution\.json|All experiments finished/,
    { timeout: 15_000 },
  );

  // The card opens the job detail. Its experiment list comes from `crab history`,
  // which only reads <crab_root>/data, so a run writing to a temp data path
  // shows none there.
  await card.locator(".use-case").click();
  await expect(page.locator(".detail h1")).toHaveText("e2e_local");
  await expect(page.locator(".detail .sub")).toContainText("laptop / local");

  // --- Nothing leaked: repo data/ untouched, no job process left --------------
  expect(snapshot(repoData)).toEqual(dataBefore);
  expect(leftovers("sleep_wrapper")).toEqual([]);
});
