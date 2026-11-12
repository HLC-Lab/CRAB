/**
 * Accessibility basics on the shared, live views (Remotes and Wrappers), end to
 * end against a real `crab web` with no cluster: a failed connect is announced
 * as an alert, connection state is available as text, every input and button
 * has an accessible name, and toggle buttons expose their state.
 *
 * Two remotes are added through the UI (127.0.0.1 port 1 never answers, so a
 * connect fails fast) and removed again at the end: the server is shared with
 * the other specs.
 */
import { expect, test, type Page } from "@playwright/test";

const AGENT = "a11y-agent";
const SECRET = "a11y-secret";

/** Adds an ssh remote that cannot be reached, through the form. */
async function addRemote(page: Page, name: string, auth: "agent" | "password"): Promise<void> {
  await page.getByRole("button", { name: "+ Add cluster" }).click();
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Host", { exact: true }).fill("127.0.0.1");
  await page.getByLabel("User", { exact: true }).fill("nobody");
  await page.getByLabel("Port", { exact: true }).fill("1");
  await page.locator('label:has-text("Auth") select').selectOption(auth);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".list li", { hasText: name })).toBeVisible();
}

/** Deletes both remotes; a name that does not exist is fine. */
async function removeRemotes(page: Page): Promise<void> {
  // The API wants the session token that the server puts into the page shell.
  await page.goto("/");
  const token = (await page.locator('meta[name="crab-token"]').getAttribute("content")) ?? "";
  for (const name of [AGENT, SECRET]) {
    await page.request.delete(`/api/remotes/${name}`, { headers: { "x-crab-token": token } });
  }
}

/** The inputs that have no accessible name, as short descriptions. */
async function unnamedInputs(page: Page): Promise<string[]> {
  return page
    .locator("input:not([type=hidden])")
    .evaluateAll((els) =>
      (els as HTMLInputElement[])
        .filter(
          (e) =>
            !(
              (e.labels?.length ?? 0) > 0 ||
              e.getAttribute("aria-label") ||
              e.getAttribute("aria-labelledby")
            ),
        )
        .map((e) => `<input type=${e.type} placeholder="${e.placeholder}">`),
    );
}

/** The buttons that have no accessible name, as short descriptions. */
async function unnamedButtons(page: Page): Promise<string[]> {
  return page
    .locator("button")
    .evaluateAll((els) =>
      els
        .filter(
          (e) =>
            !(
              (e.textContent ?? "").trim() ||
              e.getAttribute("aria-label") ||
              e.getAttribute("aria-labelledby")
            ),
        )
        .map((e) => `<button class="${e.className}" title="${e.getAttribute("title") ?? ""}">`),
    );
}

test.describe("accessibility basics", () => {
  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await removeRemotes(page);
    await page.goto("/remotes");
    await addRemote(page, AGENT, "agent");
    await addRemote(page, SECRET, "password");
    await page.close();
  });

  test.afterAll(async ({ browser }) => {
    const page = await browser.newPage();
    await removeRemotes(page);
    await page.close();
  });

  test("a failed connect is announced and the state is text", async ({ page }) => {
    await page.goto("/remotes");
    const row = page.locator(".list li", { hasText: AGENT });
    // Colour is not the only carrier of the connection state.
    await expect(row).toContainText("Not connected");
    await expect(page.getByRole("alert")).toHaveCount(0);

    await row.getByRole("button", { name: "Connect" }).click();
    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(/No SSH key is loaded|Could not reach/);
  });

  test("a failed connect on the Wrappers page is announced too", async ({ page }) => {
    await page.goto("/wrappers");
    const row = page.getByRole("row", { name: new RegExp(AGENT) });
    await row.getByRole("button", { name: "Connect" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("alert")).not.toBeEmpty();
  });

  test("every input has an accessible name", async ({ page }) => {
    await page.goto("/remotes");
    await page.getByRole("button", { name: "+ Add cluster" }).click(); // the form's inputs too
    expect(await unnamedInputs(page), "remotes, form open").toEqual([]);
    await page.getByRole("button", { name: "Cancel" }).click();
    // The password remote shows its own password field.
    await expect(page.locator(".list li", { hasText: SECRET }).locator("input")).toBeVisible();
    expect(await unnamedInputs(page), "remotes, list").toEqual([]);

    await page.goto("/wrappers");
    await expect(page.getByRole("heading", { name: "Wrappers", level: 1 })).toBeVisible();
    expect(await unnamedInputs(page), "wrappers, status").toEqual([]);
    await page.getByRole("tab", { name: "Library", exact: true }).click();
    await expect(page.getByRole("button", { name: "path_app.py" })).toBeVisible();
    expect(await unnamedInputs(page), "wrappers, library").toEqual([]);
  });

  test("every button has an accessible name", async ({ page }) => {
    await page.goto("/remotes");
    await expect(page.locator(".list li", { hasText: AGENT })).toBeVisible();
    await page.getByRole("button", { name: "+ Add cluster" }).click();
    expect(await unnamedButtons(page), "remotes").toEqual([]);

    // A symbol alone is not a name: these buttons are named in words.
    await expect(page.getByRole("button", { name: "Close", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Dark mode" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Re-check backend" })).toBeVisible();
    const row = page.locator(".list li", { hasText: AGENT });
    await expect(row.getByRole("button", { name: `Edit ${AGENT}` })).toBeVisible();
    await expect(row.getByRole("button", { name: `Remove ${AGENT}` })).toBeVisible();

    await page.goto("/wrappers");
    await expect(page.getByRole("heading", { name: "Wrappers", level: 1 })).toBeVisible();
    expect(await unnamedButtons(page), "wrappers, status").toEqual([]);
    await page.getByRole("tab", { name: "Library", exact: true }).click();
    await expect(page.getByRole("button", { name: "path_app.py" })).toBeVisible();
    expect(await unnamedButtons(page), "wrappers, library").toEqual([]);
  });

  test("toggle buttons expose their state", async ({ page }) => {
    await page.goto("/wrappers?tab=library");
    await expect(page.getByRole("button", { name: "path_app.py" })).toBeVisible();

    // The theme toggle keeps one name and flips its state.
    const theme = page.getByRole("button", { name: "Dark mode" });
    const before = await theme.getAttribute("aria-pressed");
    expect(["true", "false"]).toContain(before);
    await theme.click();
    await expect(theme).toHaveAttribute("aria-pressed", before === "true" ? "false" : "true");
    await theme.click();
    await expect(theme).toHaveAttribute("aria-pressed", before as string);

    // Disclosure buttons (New wrapper, tree folders): aria-expanded flips on click.
    const disclosures = page.locator("button[aria-expanded]");
    expect(await disclosures.count()).toBeGreaterThan(0);
    for (let i = 0; i < (await disclosures.count()); i++) {
      const button = disclosures.nth(i);
      const was = await button.getAttribute("aria-expanded");
      expect(["true", "false"]).toContain(was);
      await button.click();
      await expect(button).toHaveAttribute("aria-expanded", was === "true" ? "false" : "true");
    }
  });
});
