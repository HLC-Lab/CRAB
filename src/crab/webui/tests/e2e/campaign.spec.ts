// Campaign editor flow on the sbatchman branch (plan 090 S11i), against a real
// `crab web` on throwaway data dirs (playwright.config.ts). No cluster needed:
// validation, the YAML preview, save, and the delete warning are all local.
import { expect, test } from "@playwright/test";
import yaml from "js-yaml";

test("validate, fix, save, and warn before deleting the open campaign", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/sbatchman/);

  const preview = page.locator("aside.yamlpane");
  const write = preview.getByRole("button", { name: "Write files" });

  // A fresh campaign is not writable, and says why.
  await expect(preview.getByText(/to fix before writing/)).toBeVisible();
  await expect(write).toBeDisabled();

  // Fill the group: tag, SbatchMan preset, nodes, one app with a wrapper path.
  await page.getByPlaceholder("e.g. g500_baseline_{scale}_{nodes}").fill("e2e_{nodes}");
  await page.getByPlaceholder("e.g. {nodes}_nodes").fill("cpu");
  await page.getByPlaceholder("e.g. {nodes}", { exact: true }).fill("{nodes}");
  await page.getByRole("button", { name: "+ Add variable" }).first().click();
  const varRow = page.locator(".var-row").last();
  await varRow.getByPlaceholder("name").fill("nodes");
  await varRow.getByPlaceholder("values, comma separated").fill("2, 4");
  await varRow.getByPlaceholder("values, comma separated").blur();
  await page.getByRole("button", { name: "+ Add app" }).click();
  await page.getByTitle("Choose or change the wrapper").click();
  await page.getByPlaceholder(/Search wrappers/).fill("blink/a2a_b.py");
  await page.getByRole("button", { name: /\+ Add "blink\/a2a_b.py"/ }).click();

  await expect(preview.getByText(/to fix before writing/)).toHaveCount(0);
  await expect(preview.getByText("jobs.yaml · 2 jobs")).toBeVisible();

  // A blank variable name is flagged, and the preview is still valid YAML.
  await page.getByRole("button", { name: "+ Add variable" }).first().click();
  await expect(preview.getByText(/a variable has no name/)).toBeVisible();
  const doc = yaml.load(await preview.locator("pre").innerText()) as {
    jobs: Array<{ tag: string; config: string }>;
  };
  expect(doc.jobs[0]).toMatchObject({ tag: "e2e_{nodes}", config: "cpu" });

  // Save: the notice shows at the top of the page, not only inside the preview.
  await page.getByPlaceholder("Campaign name").fill("e2e campaign");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.locator("p.banner.info")).toContainText('Saved "e2e campaign"');

  // Edit, then delete the open campaign from Browse: the confirmation names the
  // unsaved loss, and confirming resets the editor.
  await page.getByPlaceholder("e.g. {nodes}_nodes").fill("gpu");
  await page.getByRole("button", { name: "Browse…" }).click();
  await page.getByRole("button", { name: "Discard" }).click(); // opens the overlay only
  await page.getByTitle("Delete campaign").click();
  await expect(page.getByText(/unsaved changes, which will be lost too/)).toBeVisible();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByPlaceholder("Campaign name")).toHaveValue("campaign");
});
