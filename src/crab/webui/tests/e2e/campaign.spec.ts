// Campaign editor flow on the sbatchman branch, against a real
// `crab web` on throwaway data dirs (playwright.config.ts). No cluster needed:
// validation, the YAML preview, save, and the delete warning are all local.
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
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

  // Fill the group: tag, SbatchMan config, nodes, one app with a wrapper path.
  await page.getByPlaceholder("e.g. run_{nodes}").fill("e2e_{nodes}");
  await page.getByLabel("SbatchMan config").fill("cpu");
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
  // The header names the file the campaign gets on the cluster: the backend's slug of the
  // campaign name (lower-case, runs of non-alphanumerics become "-"), plus ".yaml".
  const campaignName = await page.getByPlaceholder("Campaign name").inputValue();
  const slug = campaignName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  await expect(preview.getByText(`${slug || "campaign"}.yaml · 2 jobs`)).toBeVisible();

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
  await page.getByLabel("SbatchMan config").fill("gpu");
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
    page.getByText(/Jobs take their environment from the SbatchMan config/),
  ).toBeVisible();
  await expect(page.getByPlaceholder("e.g. /home/user/CRAB")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "Campaign-wide variables" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Campaign settings", exact: true })).toHaveCount(0);
  await groupEntry.click();
  await expect(groupEntry).toHaveClass(/on/);
  await expect(cards).toHaveCount(3);
});

// ---- Umbrella-ready files ------------------------------------------------------------------
// A local cluster stands in for the target: the write itself is the plain path (a local
// transport is never an umbrella), so the file is read back from disk. The umbrella half of the
// page (folder field, config suggestions, derived configs: value) runs against a stubbed
// /target answer; the real umbrella write is covered by tests/test_web_sbatchman.py.
const LOCAL = "e2e-local";

async function token(page: Page): Promise<string> {
  return (await page.locator('meta[name="crab-token"]').getAttribute("content")) ?? "";
}

/** A connected local cluster, removed again by the returned function (other specs expect none). */
async function connectLocal(page: Page): Promise<() => Promise<void>> {
  await page.goto("/");
  const headers = { "x-crab-token": await token(page) };
  await page.request.post("/api/remotes", { headers, data: { name: LOCAL, transport: "local" } });
  const connected = await page.request.post(`/api/remotes/${LOCAL}/connect`, { headers });
  expect(connected.ok()).toBe(true);
  return async () => {
    await page.request.delete(`/api/remotes/${LOCAL}`, { headers });
  };
}

/** A QE-like group: a config, one app, and the wrapper attributes that need $CRAB_ROOT. */
async function fillQeGroup(page: Page): Promise<void> {
  await page.getByPlaceholder("e.g. run_{nodes}").fill("qe-alone");
  await page.getByLabel("SbatchMan config").fill("crab_qe_2");
  await page.getByPlaceholder("e.g. {nodes}", { exact: true }).fill("2");
  await page.getByRole("button", { name: "+ Add app" }).click();
  await page.getByTitle("Choose or change the wrapper").click();
  await page.getByPlaceholder(/Search wrappers/).fill("fixture/path_app.py");
  await page.getByRole("button", { name: /\+ Add "fixture\/path_app.py"/ }).click();
  for (const [key, value] of [
    ["input_file", "$CRAB_ROOT/examples/sbatchman/qe/si-scf.in"],
    ["pseudo_dir", "$CRAB_ROOT/examples/sbatchman/qe"],
  ]) {
    await page.getByRole("button", { name: "+ Add attribute" }).click();
    await page.getByPlaceholder("name").last().fill(key);
    await page.getByPlaceholder("value").last().fill(value);
  }
}

test("a QE-like campaign is written with the configs key and an unquoted heredoc", async ({
  page,
}) => {
  const disconnect = await connectLocal(page);
  try {
    await page.goto("/sbatchman");
    await fillQeGroup(page);
    await page.locator("aside.rail li", { hasText: "Campaign settings" }).click();
    await page.getByLabel("Configs file").fill("../../.local/configs.x.yaml");

    const preview = page.locator("aside.yamlpane pre");
    await expect(preview).toContainText("configs: ../../.local/configs.x.yaml");
    await expect(preview).toContainText("<<JSON");
    await expect(preview).not.toContainText("<<'JSON'");
    await expect(preview).toContainText(
      '"input_file": "$CRAB_ROOT/examples/sbatchman/qe/si-scf.in"',
    );
    // A plain install has no benchmark folder to ask for.
    await expect(page.getByLabel("Benchmark folder")).toHaveCount(0);

    const writeBar = page.getByRole("region", { name: "Write to cluster" });
    await writeBar.getByRole("button", { name: "Write files" }).click();
    const written = await writeBar.locator("p.out b").innerText();
    const doc = yaml.load(readFileSync(written, "utf8")) as {
      configs: string;
      jobs: Array<{ config: string; preprocess: string }>;
    };
    expect(Object.keys(doc)[0]).toBe("configs");
    expect(doc.configs).toBe("../../.local/configs.x.yaml");
    expect(doc.jobs[0].config).toBe("crab_qe_2");
    expect(doc.jobs[0].preprocess).toContain('config.json" <<JSON\n');
  } finally {
    await disconnect();
  }
});

test("on an umbrella cluster the page suggests configs and folders and derives configs:", async ({
  page,
}) => {
  const disconnect = await connectLocal(page);
  try {
    await page.route("**/api/sbatchman/target?*", (route) =>
      route.fulfill({
        json: {
          kind: "umbrella",
          root: "/srv/umbrella",
          configs_file: "/srv/umbrella/.local/configs.x.yaml",
          configs_names: ["crab_run_{nodes}", "crab_qe_{nodes}"],
          benchmarks: ["blink", "qe"],
        },
      }),
    );
    const firstPreview = page.waitForRequest(/staging-path.*benchmark=blink/);
    await page.goto("/sbatchman");
    await firstPreview;

    // The folder defaults to the first existing one and offers the others.
    const folder = page.getByLabel("Benchmark folder");
    await expect(folder).toHaveAttribute("placeholder", "blink");
    await expect(page.locator("#benchmark-folders option")).toHaveCount(2);
    await expect(page.locator("#sbatchman-config-names option")).toHaveCount(2);
    const typed = page.waitForRequest(/staging-path.*benchmark=fresh/);
    await folder.fill("fresh");
    await typed;

    // configs: is derived from the umbrella's own file, and the field says so.
    await page.locator("aside.rail li", { hasText: "Campaign settings" }).click();
    await expect(page.getByLabel("Configs file")).toHaveAttribute(
      "placeholder",
      "../../.local/configs.x.yaml",
    );
    await expect(page.locator("aside.yamlpane pre")).toContainText(
      "configs: ../../.local/configs.x.yaml",
    );

    // A folder name that is not one safe name blocks Write files and says why.
    await folder.fill("a/b");
    await expect(page.getByRole("alert").filter({ hasText: "benchmark folder" })).toBeVisible();
    await expect(
      page
        .getByRole("region", { name: "Write to cluster" })
        .getByRole("button", { name: "Write files" }),
    ).toBeDisabled();
  } finally {
    await disconnect();
  }
});
