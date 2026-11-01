// The basic user journey, end to end against the fixture bundle: the globe starts,
// close approaches load and can be selected, filters and colour modes work, the
// risk dashboard and simulator run, and an API failure is shown, not hidden.
import { expect, test, type Page } from "@playwright/test";

const errors: string[] = [];

test.beforeEach(async ({ page }) => {
  errors.length = 0;
  page.on("pageerror", (e) => errors.push(e.message));
});

test.afterEach(() => {
  expect(errors, `uncaught page errors: ${errors.join("; ")}`).toEqual([]);
});

async function globeReady(page: Page) {
  await expect(page.locator(".globe-canvas canvas")).toBeVisible();
  // The status line ("Loading orbit data…") disappears once the worker has loaded every record.
  await expect(page.locator(".globe-status")).toHaveCount(0);
}

test("API health answers through the web app", async ({ request }) => {
  const r = await request.get("/v1/health");
  expect(r.status()).toBe(200);
  expect((await r.json()).status).toBe("ok");
});

test("globe starts and close approaches load", async ({ page }) => {
  await page.goto("/");
  await globeReady(page);
  const rows = page.locator(".table tbody tr");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(5);
  await expect(page.locator(".footer")).toContainText("not for operational use");
  // The fixture has no update history: estimates must say they are provisional.
  await expect(page.locator(".banner").first()).toContainText("Provisional risk estimates");
  await expect(page.locator(".tag.provisional").first()).toBeVisible();
});

test("selecting an approach jumps the clock and shows its detail", async ({ page }) => {
  await page.goto("/#/conjunctions");
  await globeReady(page);
  const first = page.locator(".table tbody tr").first();
  const tca = (await first.locator("td").nth(1).locator(".small").innerText()).trim();
  await first.locator(".row-button").click();
  await expect(page.locator(".timebar .time")).toHaveText(tca);
  const detail = page.locator(".detail");
  await expect(detail).toContainText("Predicted miss");
  await expect(detail).toContainText("Not a collision probability");
  await expect(detail.locator(".note")).toContainText("Provisional");
  await expect(page.locator(".table tbody tr.active")).toHaveCount(1);
});

test("filters and colour modes", async ({ page }) => {
  await page.goto("/");
  await globeReady(page);
  const payloads = page.getByLabel("Payloads");
  await payloads.uncheck();
  await expect(payloads).not.toBeChecked();
  await page.getByLabel("Colour by").selectOption("risk");
  await expect(page.locator(".legend")).toContainText("Not a collision probability");
  await page.getByLabel("Colour by").selectOption("altitude");
  await expect(page.locator(".legend .ramp")).toBeVisible();
  await page.getByRole("button", { name: "Pause" }).click();
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
});

test("object search opens its detail", async ({ page }) => {
  await page.goto("/#/object");
  await page.getByLabel("Find by name or NORAD id").fill("25544");
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("button", { name: /ISS/ }).first().click();
  await expect(page.locator(".detail h3")).toContainText("ISS");
  await expect(page.locator(".detail")).toContainText("Orbit data");
});

test("risk dashboard draws the index and its table", async ({ page }) => {
  await page.goto("/#/risk");
  await expect(page.locator("svg.chart path.series")).toHaveCount(2);
  await expect(page.locator("svg.chart")).toContainText("not a probability");
  await page.getByRole("button", { name: "Show table" }).click();
  expect(await page.locator("table.compact tbody tr").count()).toBeGreaterThan(10);
  await expect(page.locator("ol li").first()).toContainText("km");
});

test("simulator runs and keeps its label on screen", async ({ page }) => {
  await page.goto("/#/simulator");
  await globeReady(page);
  await expect(page.locator(".limits")).toContainText("No air drag");
  await page.getByRole("button", { name: "Simulate collision" }).click();
  await expect(page.locator(".detail h3")).toHaveText(/breakup/);
  await expect(page.locator(".sim-label")).toHaveText("Illustrative, simplified; not a prediction.");
  await page.getByLabel("Time after the collision").fill("3");
  await expect(page.locator(".detail")).toContainText("of the way around its orbits");
  await page.getByRole("button", { name: "Clear simulation" }).click();
  await expect(page.locator(".sim-label")).toHaveCount(0);
});

test("an API failure is shown, not hidden", async ({ page }) => {
  await page.route("**/v1/meta", (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "boom", request_id: "x" } }) }),
  );
  await page.goto("/");
  await expect(page.locator(".banner.warn")).toContainText("The API is not answering", { timeout: 60_000 });
});

test("method page states the limits", async ({ page }) => {
  await page.goto("/#/method");
  await expect(page.locator(".prose")).toContainText("not a collision probability");
  await expect(page.locator(".prose")).toContainText("provisional");
});
