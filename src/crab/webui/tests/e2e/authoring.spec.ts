/**
 * The critical authoring flow, end to end against a real `crab web`:
 * author a use case (basics, divided allocation, experiment with two apps
 * attached to slices), save it, reload the app, reopen it from Browse, and
 * verify everything survived — including that a per-experiment placement
 * override forks from the global allocation instead of starting blank.
 */
import { expect, test } from "@playwright/test";

// Author is unhooked from nav/routing on this branch (plan 085) -- SbatchMan
// owns run/monitor/results and this dashboard only authors campaigns now.
// See docs/dev/dashboard/deferred.md for the deferred replacement (a
// campaign-editor e2e); this spec is kept, not deleted, as a reference.
test.skip("author, save, reopen, override", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Author" }).click();

  // --- Basics ---------------------------------------------------------------
  await page.locator(".job-grid label.wide input").fill("e2e_use_case");
  await page.locator('label:has-text("Total nodes to allocate") input').fill("8");

  // --- Divide the allocation into two named slices ---------------------------
  await page.locator(".rail li", { hasText: "Node allocation" }).click();
  await page.getByRole("button", { name: "+ divide the nodes" }).click();
  const sliceNames = page.locator(".alloc .slice input.nm");
  await expect(sliceNames).toHaveCount(2);
  await sliceNames.nth(0).fill("victim");
  await sliceNames.nth(1).fill("aggressor");

  // --- Experiment with two apps attached to the slices -----------------------
  await page.locator(".zone .add").click(); // + experiment
  await page.locator('.exp-edit label:has-text("Experiment name") input').fill("exp1");

  for (const [i, path] of ["blink/a2a.py", "others/g500.py"].entries()) {
    await page.getByRole("button", { name: "+ Add app" }).click();
    // Wrapper chip opens the picker; with no cluster connected the free-text
    // "+ Add" path is how a path gets in.
    await page.getByRole("button", { name: "Choose wrapper…" }).click();
    await page.getByPlaceholder("Search wrappers by name, path, or group…").fill(path);
    await page.locator(".wm-add").click();
    await expect(page.locator(".chip-text", { hasText: path })).toBeVisible();
    // Attach to a slice.
    await page
      .locator(".slicepick select")
      .nth(i)
      .selectOption(i === 0 ? "victim" : "aggressor");
  }

  // --- Save (name already set, so no prompt) and reload the whole app --------
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
  await page.reload();
  await page.getByRole("link", { name: "Author" }).click();

  // --- Reopen from Browse and verify everything survived ---------------------
  await page.getByRole("button", { name: "Browse…" }).click();
  await page.locator(".open-list li", { hasText: "e2e_use_case" }).click();

  await page.locator(".rail li", { hasText: "Basics" }).click();
  await expect(page.locator(".job-grid label.wide input")).toHaveValue("e2e_use_case");
  await expect(page.locator('label:has-text("Total nodes to allocate") input')).toHaveValue("8");

  await page.locator(".rail li", { hasText: "Node allocation" }).click();
  await expect(page.locator(".alloc .slice input.nm")).toHaveCount(2);
  await expect(page.locator(".alloc .slice input.nm").nth(0)).toHaveValue("victim");
  await expect(page.locator(".alloc .slice input.nm").nth(1)).toHaveValue("aggressor");

  await page.locator(".rail .zone li", { hasText: "exp1" }).click();
  await expect(page.locator(".chip-text", { hasText: "blink/a2a.py" })).toBeVisible();
  await expect(page.locator(".chip-text", { hasText: "others/g500.py" })).toBeVisible();
  await expect(page.locator(".slicepick select").nth(0)).toHaveValue("victim");
  await expect(page.locator(".slicepick select").nth(1)).toHaveValue("aggressor");

  // --- Placement override forks from the global allocation -------------------
  await expect(page.locator(".place .inh")).toHaveText("inherited");
  await page.getByRole("button", { name: "Override for this experiment" }).click();
  await expect(page.locator(".place .inh")).toHaveText("override");
  // The inline editor opens pre-filled with the global slices, not blank.
  const ovSlices = page.locator(".place-section .alloc .slice input.nm");
  await expect(ovSlices).toHaveCount(2);
  await expect(ovSlices.nth(0)).toHaveValue("victim");
  await expect(ovSlices.nth(1)).toHaveValue("aggressor");
});
