// Keyboard use of the campaign editor on the sbatchman line, against a real
// `crab web` on throwaway data dirs (playwright.config.ts). No cluster needed:
// the group rail, the Browse dialog and the page landmarks are all local.
import { expect, test, type Page } from "@playwright/test";

const TAG_FIELD = "e.g. run_{nodes}";

/** True when the focused element sits inside the dialog. */
async function focusInside(page: Page): Promise<boolean> {
  return page
    .getByRole("dialog")
    .evaluate(
      (dialog) => dialog === document.activeElement || dialog.contains(document.activeElement),
    );
}

test("the group rail is operated with the keyboard", async ({ page }) => {
  await page.goto("/sbatchman");
  const rail = page.locator("aside.rail");
  const tag = page.getByPlaceholder(TAG_FIELD);

  // Two groups, named so they can be told apart; adding the second selects it.
  await tag.fill("alpha");
  await page.getByRole("button", { name: "Add group" }).click();
  await tag.fill("beta");

  // Group rows are real buttons, and the selected one says so.
  const alpha = rail.getByRole("button", { name: /^alpha/ });
  const beta = rail.getByRole("button", { name: /^beta/ });
  await expect(alpha).toBeVisible();
  await expect(beta).toHaveAttribute("aria-current", "true");
  await expect(alpha).not.toHaveAttribute("aria-current", "true");

  // Keyboard only: from the first rail button, Tab past "Add group" to alpha, Enter.
  await rail.getByRole("button", { name: /^Campaign settings/ }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Add group" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(alpha).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(alpha).toHaveAttribute("aria-current", "true");
  await expect(beta).not.toHaveAttribute("aria-current", "true");
  await expect(tag).toHaveValue("alpha");

  // Tab past alpha's remove button to beta, Enter: the editor switches to it.
  await page.keyboard.press("Tab");
  await expect(rail.getByRole("button", { name: "Remove group" }).first()).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(beta).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(beta).toHaveAttribute("aria-current", "true");
  await expect(alpha).not.toHaveAttribute("aria-current", "true");
  await expect(tag).toHaveValue("beta");
});

test("the Browse dialog and its entries work with the keyboard", async ({ page }) => {
  await page.goto("/sbatchman");
  const name = page.getByPlaceholder("Campaign name");
  const browse = page.getByRole("button", { name: "Browse…" });

  // A saved campaign to open, then a fresh editor (not dirty, so Browse opens at once).
  await name.fill("kbd campaign");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator("p.banner.info")).toContainText('Saved "kbd campaign"');
  await page.getByRole("button", { name: "+ New" }).click();
  await expect(name).toHaveValue("campaign");

  // The dialog has a name, takes the focus, keeps it, and gives it back on Escape.
  await browse.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Open a saved campaign" });
  await expect(dialog).toBeVisible();
  expect(await focusInside(page)).toBe(true);
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Tab");
    expect(await focusInside(page)).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(browse).toBeFocused();

  // Open the entry by keyboard: search, Tab to its button, Enter.
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  await page.getByPlaceholder(/Search saved campaigns/).fill("kbd campaign");
  await page.keyboard.press("Tab");
  const open = dialog.getByRole("button", { name: /^kbd campaign/ });
  await expect(open).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCount(0);
  await expect(name).toHaveValue("kbd campaign");

  // Leave the shared library as it was: delete the entry again.
  await browse.click();
  await page.getByPlaceholder(/Search saved campaigns/).fill("kbd campaign");
  await page.getByTitle("Delete campaign").click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(name).toHaveValue("campaign");
});

test("the page has exactly one main landmark", async ({ page }) => {
  await page.goto("/sbatchman");
  await expect(page.locator("aside.rail")).toBeVisible();
  await expect(page.getByRole("main")).toHaveCount(1);
});
