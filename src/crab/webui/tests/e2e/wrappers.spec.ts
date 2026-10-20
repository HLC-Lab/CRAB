/**
 * The Wrappers section offline: no cluster is connected, so only the built-in
 * This laptop target exists. Status lists it, the Library lists its wrappers,
 * and picking one shows its summary.
 *
 * Determinism: playwright.config.ts points CRAB_PATH_WRAPPERS at
 * tests/fixtures/wrappers, so the list holds the fixture wrappers and Status
 * reports "not a git clone" without asking GitHub. The assertions name only
 * fixture wrappers, so a developer's own local/wrappers cannot change them.
 */
import { expect, test } from "@playwright/test";

test("browse This laptop's wrappers offline", async ({ page }) => {
  // --- Status: This laptop is the only target -------------------------------
  await page.goto("/wrappers");
  await expect(page.getByRole("heading", { name: "Wrappers", level: 1 })).toBeVisible();
  // The tab's name carries the attention count after its label.
  await expect(page.getByRole("tab", { name: /^Status/ })).toHaveAttribute("aria-selected", "true");

  const laptop = page.getByRole("row", { name: /This laptop/ });
  await expect(laptop).toBeVisible();
  await expect(laptop).toContainText("not a git clone");
  await expect(page.getByRole("link", { name: "Connect a cluster" })).toBeVisible();

  // --- Library: the tree lists the fixture wrappers --------------------------
  await page.getByRole("tab", { name: "Library", exact: true }).click();
  await expect(page).toHaveURL(/tab=library/);
  const leaf = page.getByRole("button", { name: "path_app.py" });
  await expect(leaf).toBeVisible();
  await expect(page.getByRole("button", { name: "receipt_app.py" })).toBeVisible();

  // --- Pick one: its summary shows what it measures --------------------------
  await leaf.click();
  // vue-router leaves the slash of a query value unencoded.
  await expect(page).toHaveURL(/tab=library&target=local&wrapper=fixture(%2F|\/)path_app\.py/);
  await expect(leaf).toHaveAttribute("aria-current", "true");
  await expect(page.getByRole("heading", { name: "path_app", level: 2 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What it measures" })).toBeVisible();
  const metric = page.locator("table.metrics tbody tr");
  await expect(metric).toHaveCount(1);
  await expect(metric).toContainText("t");
  await expect(metric).toContainText("convergence target");
  await expect(page.getByRole("heading", { name: "How it runs" })).toBeVisible();
});
