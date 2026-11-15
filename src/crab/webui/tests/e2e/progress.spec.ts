/**
 * A slow connect shows its stage: a Node listener accepts the TCP connection and never speaks,
 * as a login node that is slow to answer would. The Remotes card must name the stage, what it
 * waits for, a counter that moves, and when the connect gives up (30 s). The test does not wait
 * for that timeout: it drops the listener's sockets, so the connect fails at once, and then checks
 * that the failure is shown and no stage line is left behind.
 *
 * With CRAB_SHOTS_DIR set, it also saves two screenshots there (render-verify).
 */
import { createServer, type Server, type Socket } from "node:net";
import type { AddressInfo } from "node:net";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const NAME = "progress-slow";
const SHOTS = process.env.CRAB_SHOTS_DIR;

test.use({ viewport: { width: 1280, height: 900 } });

let server: Server;
const sockets: Socket[] = [];
let port = 0;

async function token(page: Page): Promise<string> {
  await page.goto("/");
  return (await page.locator('meta[name="crab-token"]').getAttribute("content")) ?? "";
}

test.beforeAll(async () => {
  server = createServer((socket) => {
    sockets.push(socket); // accept, never answer
    socket.on("error", () => undefined);
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  port = (server.address() as AddressInfo).port;
});

test.afterAll(async ({ browser }) => {
  for (const s of sockets) s.destroy();
  await new Promise<void>((done) => server.close(() => done()));
  const page = await browser.newPage();
  await page.request.delete(`/api/remotes/${NAME}`, {
    headers: { "x-crab-token": await token(page) },
  });
  await page.close();
});

test("a slow connect shows the stage, the wait and the give-up limit, and leaves no stage behind", async ({
  page,
}) => {
  await page.goto("/remotes");
  await page.getByRole("button", { name: "+ Add cluster" }).click();
  await page.getByLabel("Name", { exact: true }).fill(NAME);
  await page.getByLabel("Host", { exact: true }).fill("127.0.0.1");
  await page.getByLabel("User", { exact: true }).fill("nobody");
  await page.getByLabel("Port", { exact: true }).fill(String(port));
  await page.locator('label:has-text("Auth") select').selectOption("password");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const row = page.locator(".list li", { hasText: NAME });
  await expect(row).toBeVisible();

  await page.getByLabel(`Password for ${NAME}`).fill("not-a-real-password");
  await row.getByRole("button", { name: "Connect" }).click();

  const line = row.getByTestId("progress-line");
  await expect(row.getByRole("button", { name: "Connecting…" })).toBeVisible();
  await expect(line).toContainText("Connecting to the cluster");
  await expect(line).toContainText(`127.0.0.1:${port}`);
  const meta = row.getByTestId("progress-meta");
  await expect(meta).toContainText("gives up after 30 s");
  // The counter moves: it reaches 2 s while the connect is still waiting.
  await expect(meta).toHaveText(/^([2-9]|\d\d) s waited/, { timeout: 10_000 });
  await expect(line).not.toContainText("not-a-real-password");

  if (SHOTS) {
    await expect(meta).toHaveText(/^([5-9]|\d\d) s waited/, { timeout: 10_000 });
    await page.screenshot({ path: join(SHOTS, "143-connecting.png") });
  }

  // End the wait: the connect fails now, with its message and without a stage line.
  for (const s of sockets) s.destroy();
  await expect(row.getByRole("alert")).toBeVisible({ timeout: 15_000 });
  await expect(row.getByRole("button", { name: "Connect" })).toBeEnabled();
  await expect(line).toBeEmpty();
  await expect(row).not.toContainText("waiting for");
  if (SHOTS) await page.screenshot({ path: join(SHOTS, "143-failed.png") });
});
