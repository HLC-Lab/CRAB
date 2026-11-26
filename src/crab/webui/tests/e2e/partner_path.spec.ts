/**
 * The partner path, step by step as the partner docs (connect.md "Add the cluster in crab web",
 * campaigns.md "In the dashboard") describe it: add a cluster on Remotes, connect it, browse
 * Wrappers, then on SbatchMan build a campaign and press "Write files". The labels below are the
 * ones the docs name, so renaming a button or a field breaks this test on purpose.
 *
 * Two stand-ins, because an e2e run has no ssh server and no umbrella cluster:
 *  - The cluster uses the `local` transport instead of `ssh`. The form shows Host, User, Port,
 *    Auth, Host key, Install dir and Crab command only for ssh, so the test checks that those
 *    labels exist under ssh, then switches to local for the rest.
 *  - The backend never probes a local cluster as an umbrella (probe_umbrella answers "plain"),
 *    so `page.route` answers GET /api/sbatchman/target with an umbrella laid out in a temp dir,
 *    exactly as campaign.spec.ts does. The write itself then takes the plain path (a file in the
 *    local cluster's staging directory); the umbrella write is covered by
 *    tests/test_web_sbatchman.py.
 */
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import yaml from "js-yaml";

const CLUSTER = "partner-e2e";
const TAG = "partner_{nodes}";
const CONFIG = "crab_run_2";

let umbrella = "";

test.beforeAll(() => {
  // An umbrella laid out as the docs describe: benchmarks/<folder>/ and the cluster's configs file.
  umbrella = mkdtempSync(join(tmpdir(), "crab-e2e-partner-"));
  mkdirSync(join(umbrella, "benchmarks", "blink"), { recursive: true });
  mkdirSync(join(umbrella, ".local"));
  writeFileSync(
    join(umbrella, ".local", "configs.leonardo.yaml"),
    `leonardo:\n  configs:\n    - name: ${CONFIG}\n    - name: crab_run_4\n`,
  );
});

test.afterAll(async ({ browser }) => {
  if (umbrella) rmSync(umbrella, { recursive: true, force: true });
  // One server and profile store serve every spec: drop the remote even when the test failed.
  const baseURL = test.info().project.use.baseURL;
  const context = await browser.newContext({ baseURL });
  try {
    const page = await context.newPage();
    await page.goto("/");
    const token = (await page.locator('meta[name="crab-token"]').getAttribute("content")) ?? "";
    await page.request.delete(`/api/remotes/${CLUSTER}`, { headers: { "x-crab-token": token } });
  } finally {
    await context.close();
  }
});

test("add a cluster, browse wrappers, write a campaign file", async ({ page }) => {
  const nav = page.getByRole("navigation");
  // --- connect.md: Remotes, + Add cluster, fields, Save, Connect --------------------------
  await page.goto("/");
  await nav.getByRole("link", { name: "Remotes", exact: true }).click();
  await page.getByRole("button", { name: "+ Add cluster" }).click();

  await page.getByLabel("Name", { exact: true }).fill(CLUSTER);
  // The docs' ssh fields exist under Transport ssh (the default); local replaces them.
  const transport = page.getByLabel("Transport");
  await transport.selectOption("ssh");
  for (const label of ["Host", "User", "Port", "Auth", "Host key", "Install dir"]) {
    // A select's accessible name carries its option texts, so match the label as a prefix.
    await expect(page.getByLabel(new RegExp(`^${label}`)).first()).toBeVisible();
  }
  await expect(page.getByLabel(/^Crab command/)).toBeVisible();
  await transport.selectOption("local");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  const row = page.locator(".list li", { hasText: CLUSTER });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(row.locator(".dot.on")).toBeVisible({ timeout: 30_000 });
  await expect(row.getByRole("button", { name: "Disconnect" })).toBeVisible();

  // --- Wrappers: the page lists wrappers -------------------------------------------------
  await nav.getByRole("link", { name: "Wrappers", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Wrappers", level: 1 })).toBeVisible();
  await page.getByRole("tab", { name: "Library", exact: true }).click();
  await expect(page.getByRole("button", { name: "path_app.py" })).toBeVisible();

  // --- campaigns.md "In the dashboard" ---------------------------------------------------
  await page.route("**/api/sbatchman/target?*", (route) =>
    route.fulfill({
      json: {
        kind: "umbrella",
        root: umbrella,
        configs_file: join(umbrella, ".local", "configs.leonardo.yaml"),
        configs_names: [CONFIG, "crab_run_4"],
        benchmarks: ["blink"],
      },
    }),
  );
  // 1. Connected as above, then open SbatchMan.
  await nav.getByRole("link", { name: "SbatchMan", exact: true }).click();
  await expect(page).toHaveURL(/\/sbatchman/);

  // 2. The campaign name sits next to Save.
  await page.getByPlaceholder("Campaign name").fill("Partner Scaling");

  // 3. Write to cluster: pick the cluster, then the benchmark folder.
  const writeBar = page.getByRole("region", { name: "Write to cluster" });
  await writeBar.getByLabel("Cluster").selectOption(CLUSTER);
  await writeBar.getByLabel("Benchmark folder").fill("blink");

  // 4. Group: Tag, SbatchMan config, Nodes, Processes per node.
  const group = page.locator("section.card").first();
  await group.getByLabel(/^Tag/).fill(TAG);
  await group.getByLabel(/^SbatchMan config/).fill(CONFIG);
  await group.getByLabel(/^Nodes/).fill("{nodes}");
  await group.getByLabel(/^Processes per node/).fill("4");

  // 5. Variables card: + Add variable under This group.
  const groupVars = page.getByRole("group", { name: "This group variables" });
  await groupVars.getByRole("button", { name: "+ Add variable" }).click();
  await groupVars.getByPlaceholder("name").fill("nodes");
  await groupVars.getByPlaceholder("values, comma separated").fill("2, 4");
  await groupVars.getByPlaceholder("values, comma separated").blur();

  // 6. Experiment: + Add app, Choose wrapper…, pick a wrapper, args.
  await page.getByRole("button", { name: "+ Add app" }).click();
  await page.getByRole("button", { name: "Choose wrapper…" }).click();
  await page.locator(".wrap-row", { hasText: "path_app.py" }).click();
  await page.getByPlaceholder("args", { exact: true }).fill("--size 8");

  // 7. Write files; the page shows the path it wrote.
  await expect(writeBar.getByRole("button", { name: /\d+ problems?/ })).toHaveCount(0);
  await writeBar.getByRole("button", { name: "Write files" }).click();
  const written = await writeBar.locator("p.out b").innerText();
  expect(written).toMatch(/partner-scaling\.yaml$/);

  // --- The file on disk -----------------------------------------------------------------
  const text = readFileSync(written, "utf8");
  const doc = yaml.load(text) as {
    configs: string;
    jobs: Array<Record<string, unknown>>;
  };
  // campaigns.md: the file starts with the configs key, derived from the cluster's own file.
  expect(Object.keys(doc)[0]).toBe("configs");
  expect(doc.configs).toBe("../../.local/configs.leonardo.yaml");
  expect(doc.jobs).toHaveLength(1);
  expect(doc.jobs[0]).toMatchObject({ tag: TAG, config: CONFIG, variables: { nodes: [2, 4] } });
  // The group fields and the app land in the CRAB JSON of the quoted <<'JSON' heredoc.
  const preprocess = String(doc.jobs[0]!.preprocess);
  const json = /<<'JSON'\n([\s\S]*?)\nJSON\n/.exec(preprocess)?.[1];
  expect(json).toBeTruthy();
  const config = JSON.parse(json!) as {
    global_options: Record<string, string>;
    experiments: Record<string, { apps: Record<string, Record<string, unknown>> }>;
  };
  expect(config.global_options).toMatchObject({ numnodes: "{nodes}", ppn: "4" });
  expect(Object.values(config.experiments)[0]!.apps["0"]).toMatchObject({
    path: "fixture/path_app.py",
    args: "--size 8",
  });
});
