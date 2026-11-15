/**
 * Page layout basics on every live route, end to end against a real `crab web`:
 * one `<main>` landmark per page, and no scroll container that shows a horizontal
 * scrollbar at a 1280x800 window (Author also with the JSON panel open).
 *
 * Both lines share this file. A route the running build does not have redirects (Author, Jobs
 * and Results on the SbatchMan line, SbatchMan on the product line), and its cases skip.
 */
import { expect, test, type Page } from "@playwright/test";

const ROUTES = ["/remotes", "/sbatchman", "/author", "/wrappers", "/jobs", "/results"];

/** Lets the route render before it is measured; skips the test when the build has no such route. */
async function open(page: Page, route: string): Promise<void> {
  await page.goto(route);
  await expect(page.locator("main.content")).toBeVisible();
  test.skip(new URL(page.url()).pathname !== route, `${route} is not a route of this build`);
  // Author and SbatchMan have no heading: their rail and pane are the signal that the view rendered.
  if (route === "/author" || route === "/sbatchman")
    await expect(page.locator(".layout .pane")).toBeVisible();
  else await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.waitForLoadState("networkidle");
}

/**
 * Every element whose computed overflow-x is auto or scroll and whose content is wider than
 * its box, plus the document, as short descriptions with both widths.
 */
async function horizontalOverflows(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const describe = (el: Element): string => {
      const cls = typeof el.className === "string" ? el.className.trim().replace(/\s+/g, ".") : "";
      return `<${el.tagName.toLowerCase()}${cls ? "." + cls : ""}>`;
    };
    const doc = document.documentElement;
    if (doc.scrollWidth > doc.clientWidth) {
      out.push(`${describe(doc)} scrollWidth=${doc.scrollWidth} clientWidth=${doc.clientWidth}`);
    }
    for (const el of Array.from(document.querySelectorAll("*"))) {
      const overflowX = getComputedStyle(el).overflowX;
      if (overflowX !== "auto" && overflowX !== "scroll") continue;
      if (el.scrollWidth > el.clientWidth) {
        out.push(`${describe(el)} scrollWidth=${el.scrollWidth} clientWidth=${el.clientWidth}`);
      }
    }
    return out;
  });
}

test.describe("one main landmark", () => {
  for (const route of ROUTES) {
    test(`${route} has exactly one main`, async ({ page }) => {
      await open(page, route);
      await expect(page.locator("main")).toHaveCount(1);
    });
  }
});

test.describe("no horizontal scrollbar at 1280 px", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  for (const route of ROUTES) {
    test(`${route} fits`, async ({ page }) => {
      await open(page, route);
      expect(await horizontalOverflows(page)).toEqual([]);
    });
  }

  test("/author fits with the JSON panel open", async ({ page }) => {
    await open(page, "/author");
    await page.getByRole("button", { name: "{ } JSON" }).click();
    await expect(page.locator(".jsonpane")).toBeVisible();
    expect(await horizontalOverflows(page)).toEqual([]);

    // The panel has a row of its own: no empty column and gap leave a strip beside the pane.
    const pane = await page.locator(".layout .pane").boundingBox();
    const panel = await page.locator(".jsonpane").boundingBox();
    expect(pane && panel).toBeTruthy();
    const paneRight = pane!.x + pane!.width;
    const panelRight = panel!.x + panel!.width;
    expect(
      Math.abs(paneRight - panelRight),
      `pane right ${paneRight}, panel right ${panelRight}`,
    ).toBeLessThanOrEqual(2);
  });
});
