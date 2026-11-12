/**
 * The Remotes form on the SbatchMan line has no CRAB-preset field or list (ADR-045): jobs
 * run with a SbatchMan config, so a "Preset" input would be a setting that does nothing.
 */
import { expect, test } from "@playwright/test";

const NAME = "remotes-copy-agent";

test("the add-cluster form has no Preset field and the list has no presets line", async ({
  page,
}) => {
  await page.goto("/remotes");
  await page.getByRole("button", { name: "+ Add cluster" }).click();
  await expect(page.getByLabel("Name", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Preset")).toHaveCount(0);
  await expect(page.locator("form")).not.toContainText("Preset");

  await page.getByLabel("Name", { exact: true }).fill(NAME);
  await page.getByLabel("Host", { exact: true }).fill("127.0.0.1");
  await page.getByLabel("User", { exact: true }).fill("nobody");
  await page.getByLabel("Port", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const row = page.locator(".list li", { hasText: NAME });
  await expect(row).toBeVisible();
  // An unreachable remote has no CRAB info, so this only guards the markup of the list.
  await expect(row).not.toContainText("presets:");

  await page.goto("/");
  const token = (await page.locator('meta[name="crab-token"]').getAttribute("content")) ?? "";
  await page.request.delete(`/api/remotes/${NAME}`, { headers: { "x-crab-token": token } });
});
