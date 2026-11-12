/**
 * Routing basics: a path the dashboard does not have ends on /sbatchman, and
 * each page names the browser tab.
 */
import { expect, test } from "@playwright/test";

for (const path of ["/jobs", "/author", "/results", "/nonsense"]) {
  test(`${path} redirects to /sbatchman`, async ({ page }) => {
    await page.goto(path);
    await expect(page).toHaveURL(/\/sbatchman$/);
  });
}

test("the tab title carries the page name", async ({ page }) => {
  await page.goto("/remotes");
  await expect(page).toHaveTitle(/Remotes - CRAB/);
  await page.goto("/wrappers");
  await expect(page).toHaveTitle(/Wrappers - CRAB/);
});
