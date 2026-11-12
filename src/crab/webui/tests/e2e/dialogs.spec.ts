/**
 * Dialog keyboard behaviour, end to end against a real `crab web`: both the
 * wrapper picker and a ConfirmModal must announce themselves as dialogs, take
 * focus on open, keep Tab inside, close on Escape and give focus back to the
 * button that opened them.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

/** True when the element holding focus is inside `dialog`. */
async function focusIsInside(dialog: Locator): Promise<boolean> {
  return dialog.evaluate((el) => el.contains(document.activeElement));
}

/**
 * The scoped styles of the host component must still reach the shared wrapper's
 * container: the backdrop covers the page and the dialog keeps its width and surface.
 */
async function expectOverlayStyled(dialog: Locator, widthPx: number): Promise<void> {
  const styles = await dialog.evaluate((el) => {
    const box = getComputedStyle(el);
    const bg = getComputedStyle(el.parentElement as HTMLElement);
    return {
      bgPosition: bg.position,
      width: el.getBoundingClientRect().width,
      borderTopWidth: box.borderTopWidth,
      background: box.backgroundColor,
    };
  });
  expect(styles.bgPosition).toBe("fixed");
  expect(Math.round(styles.width)).toBe(widthPx);
  expect(styles.borderTopWidth).toBe("1px");
  expect(styles.background).not.toBe("rgba(0, 0, 0, 0)");
}

/** Presses Tab more often than there are focusable elements; focus must never leave. */
async function expectTabStaysInside(page: Page, dialog: Locator): Promise<void> {
  const focusable = await dialog.evaluate(
    (el) =>
      el.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ).length,
  );
  for (let i = 0; i < focusable + 3; i++) {
    await page.keyboard.press("Tab");
    expect(await focusIsInside(dialog)).toBe(true);
  }
  for (let i = 0; i < focusable + 3; i++) {
    await page.keyboard.press("Shift+Tab");
    expect(await focusIsInside(dialog)).toBe(true);
  }
}

test("wrapper picker: role, focus, Tab trap, Escape, focus return", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Author" }).click();
  await page.locator(".zone .add").click(); // + experiment
  await page.getByRole("button", { name: "+ Add app" }).click();

  const opener = page.getByRole("button", { name: "Choose wrapper…" });
  await opener.click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect(dialog).toHaveAccessibleName(/\S/);
  await expectOverlayStyled(dialog, 704); // min(44rem, 94vw) at 16px

  const search = page.getByPlaceholder("Search wrappers by name, path, or group…");
  await expect(search).toBeFocused();

  // A path that is not in the catalog adds the "+ Add" button, so the dialog
  // has at least two focusable elements: the search box first, "+ Add" last.
  await search.fill("zz_not_a_wrapper.py");
  const add = page.locator(".wm-add");
  await expect(add).toBeVisible();

  await expectTabStaysInside(page, dialog);

  // Shift+Tab from the first focusable goes to the last, Tab from the last to the first.
  await search.focus();
  await page.keyboard.press("Shift+Tab");
  await expect(add).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(search).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test("confirm modal: role, focus, Tab trap, Escape cancels, focus return", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Author" }).click();
  // A dirty draft makes "+ New" ask before discarding it.
  await page.locator(".job-grid label.wide input").fill("dialogs_use_case");

  const opener = page.getByRole("button", { name: "+ New" });
  await opener.click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect(dialog).toHaveAccessibleName("Discard unsaved changes?");
  await expectOverlayStyled(dialog, 448); // min(28rem, 92vw) at 16px
  // Focus lands on the safe choice, Cancel, not on the destructive button.
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();

  await expectTabStaysInside(page, dialog);

  // Shift+Tab from the first button wraps to the last, Tab wraps back.
  await dialog.getByRole("button", { name: "Cancel" }).focus();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Discard" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  // Escape cancelled: the draft is still there.
  await expect(page.locator(".job-grid label.wide input")).toHaveValue("dialogs_use_case");
});
