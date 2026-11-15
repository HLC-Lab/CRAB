/**
 * A remote name that exists is refused at the Name field: the hint shows under the field, Save is
 * disabled, and the list keeps one remote. Forcing the same POST through the API answers 409 and
 * never creates a `<name>2`. A refusal that only the backend knows (the reserved name `local`)
 * shows inside the open form, not in a banner above it.
 *
 * With CRAB_SHOTS_DIR set, it saves a screenshot of the hint there (render-verify).
 */
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const NAME = "dup-one";
const SHOTS = process.env.CRAB_SHOTS_DIR;

test.use({ viewport: { width: 1280, height: 900 } });

async function token(page: Page): Promise<string> {
  await page.goto("/");
  return (await page.locator('meta[name="crab-token"]').getAttribute("content")) ?? "";
}

async function cleanup(page: Page): Promise<void> {
  const headers = { "x-crab-token": await token(page) };
  for (const name of [NAME, `${NAME}2`]) {
    await page.request.delete(`/api/remotes/${name}`, { headers });
  }
}

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await cleanup(page);
  await page.close();
});

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage();
  await cleanup(page);
  await page.close();
});

async function fillForm(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "+ Add cluster" }).click();
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Host", { exact: true }).fill("127.0.0.1");
  await page.getByLabel("User", { exact: true }).fill("nobody");
}

test("a duplicate name is refused at the Name field and never becomes name2", async ({ page }) => {
  await page.goto("/remotes");
  await fillForm(page, NAME);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".list li", { hasText: NAME })).toHaveCount(1);

  // The same name again: the hint is under the Name field and Save is disabled.
  await fillForm(page, NAME);
  const nameField = page.getByPlaceholder("my-cluster");
  await expect(nameField).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("label", { hasText: "Name" }).first()).toContainText(
    `A remote named '${NAME}' already exists. Choose another name.`,
  );
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  if (SHOTS) await page.screenshot({ path: join(SHOTS, "143-duplicate.png") });

  // A free name enables Save again.
  await nameField.fill(`${NAME}-other`);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
  await expect(nameField).toHaveAttribute("aria-invalid", "false");
  await nameField.fill(NAME);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator(".list li", { hasText: NAME })).toHaveCount(1);

  // Forcing the POST past the form: 409, the backend's message, and no name2 anywhere.
  const headers = { "x-crab-token": await token(page) };
  const profile = { name: NAME, transport: "ssh", host: "127.0.0.1", user: "nobody" };
  const forced = await page.request.post("/api/remotes", { headers, data: profile });
  expect(forced.status()).toBe(409);
  expect((await forced.json()).message).toBe(
    `A remote named '${NAME}' already exists. Choose another name or edit that one.`,
  );
  const listed = (await (await page.request.get("/api/remotes", { headers })).json()) as {
    name: string;
  }[];
  expect(listed.filter((r) => r.name.startsWith(NAME)).map((r) => r.name)).toEqual([NAME]);
});

test("a refusal only the backend knows shows inside the open form", async ({ page }) => {
  await page.goto("/remotes");
  await fillForm(page, "local"); // reserved for the laptop
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const form = page.locator("form.form");
  await expect(form.getByRole("alert")).toContainText("reserved");
  // One surface: no second copy of the message in a banner above the form.
  await expect(page.getByRole("alert")).toHaveCount(1);
  await page.getByRole("button", { name: "Cancel" }).click();
});
