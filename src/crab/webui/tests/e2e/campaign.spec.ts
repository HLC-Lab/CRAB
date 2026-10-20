// Campaign editor flow on the sbatchman branch, against a real
// `crab web` on throwaway data dirs (playwright.config.ts). No cluster needed:
// validation, the YAML preview, save, and the delete warning are all local.
import { expect, test } from "@playwright/test";
import yaml from "js-yaml";

test("validate, fix, save, and warn before deleting the open campaign", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/sbatchman/);

  const preview = page.locator("aside.yamlpane");
  const writeBar = page.getByRole("region", { name: "Write to cluster" });
  const write = writeBar.getByRole("button", { name: "Write files" });

  // A fresh campaign is not writable, and says why: the count sits next to the
  // button, the list is in the preview.
  await expect(preview.getByText(/to fix before writing/)).toBeVisible();
  await expect(write).toBeDisabled();
  await expect(writeBar.getByRole("button", { name: /\d+ problems?/ })).toBeVisible();

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
  await expect(writeBar.getByRole("button", { name: /problems?/ })).toHaveCount(0);
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

test("variables card edits both scopes, and Campaign settings lives in the rail", async ({
  page,
}) => {
  await page.goto("/sbatchman");
  const preview = page.locator("aside.yamlpane");
  const rail = page.locator("aside.rail");
  const campaignEntry = rail.locator("li", { hasText: "Campaign settings" });
  const groupEntry = rail.locator("li", { hasText: "untitled group" });

  // Three numbered cards for the selected group, in order.
  const cards = page.locator("section.card");
  await expect(cards).toHaveCount(3);
  await expect(cards.nth(0)).toContainText("Group");
  await expect(cards.nth(1)).toContainText("Variables");
  await expect(cards.nth(2)).toContainText("Experiment");
  await expect(groupEntry).toHaveClass(/on/);
  await expect(campaignEntry).not.toHaveClass(/on/);

  // Both variable scopes sit in the Variables card; each reaches its own place in the YAML.
  const groupVars = page.getByRole("group", { name: "This group variables" });
  const campaignVars = page.getByRole("group", { name: "Campaign-wide variables" });
  await groupVars.getByRole("button", { name: "+ Add variable" }).click();
  await groupVars.getByPlaceholder("name").fill("size");
  await groupVars.getByPlaceholder("values, comma separated").fill("1, 2");
  await groupVars.getByPlaceholder("values, comma separated").blur();
  await campaignVars.getByRole("button", { name: "+ Add variable" }).click();
  await campaignVars.getByPlaceholder("name").fill("iters");
  await campaignVars.getByPlaceholder("values, comma separated").fill("10, 20, 30");
  await campaignVars.getByPlaceholder("values, comma separated").blur();
  let doc = yaml.load(await preview.locator("pre").innerText()) as {
    variables?: Record<string, unknown[]>;
    jobs: Array<{ variables?: Record<string, unknown[]> }>;
  };
  expect(doc.variables).toEqual({ iters: [10, 20, 30] });
  expect(doc.jobs[0]!.variables).toEqual({ size: [1, 2] });

  // Removing a campaign-wide variable takes it out of the top level.
  await campaignVars.getByTitle("Remove variable").click();
  doc = yaml.load(await preview.locator("pre").innerText()) as typeof doc;
  expect(doc.variables).toBeUndefined();

  // Campaign settings: selected from the rail, highlighted, and the cards give way to it.
  await campaignEntry.getByRole("button").click();
  await expect(campaignEntry).toHaveClass(/on/);
  await expect(groupEntry).not.toHaveClass(/on/);
  await expect(
    page.getByText(/Jobs take their environment from the SbatchMan preset/),
  ).toBeVisible();
  await expect(page.getByPlaceholder("e.g. /home/user/CRAB")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "Campaign-wide variables" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Campaign settings", exact: true })).toHaveCount(0);
  await groupEntry.click();
  await expect(groupEntry).toHaveClass(/on/);
  await expect(cards).toHaveCount(3);
});
