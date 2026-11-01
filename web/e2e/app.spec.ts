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

// The fixture snapshot is 2026-10-31 14:49 UTC; this "now" is inside its 7-day window.
const NOW = "2026-11-01T12:00:00Z";
const at = (path: string) => `/?now=${NOW}${path.startsWith("/#") ? path.slice(1) : path === "/" ? "" : path}`;

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
  await page.goto(at("/"));
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
  await page.goto(at("/#/conjunctions"));
  await globeReady(page);
  const first = page.locator(".table tbody tr").first();
  const tca = (await first.locator("td").nth(1).locator(".small").innerText()).trim();
  await expect(page.locator(".mode-badge")).toContainText("LIVE");
  await first.locator(".row-button").click();
  // Forecast mode, paused at the closest approach.
  await expect(page.getByTestId("sim-time")).toHaveText(tca);
  await expect(page.locator(".mode-badge")).toContainText("FORECAST");
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
  const detail = page.locator(".detail");
  await expect(detail).toContainText("Predicted miss");
  await expect(detail).toContainText("Not a collision probability");
  await expect(detail.locator(".note")).toContainText("Provisional");
  await expect(page.locator(".table tbody tr.active")).toHaveCount(1);
});

test("the clock starts live and returns to live", async ({ page }) => {
  await page.goto(at("/"));
  await globeReady(page);
  await expect(page.locator(".mode-badge")).toContainText("LIVE");
  await expect(page.getByTestId("sim-time")).toHaveText("2026-11-01 12:00 UTC");
  await page.getByLabel("Speed").selectOption("600");
  await expect(page.locator(".mode-badge")).toContainText("FORECAST");
  await page.getByRole("button", { name: "Back to live" }).click();
  await expect(page.locator(".mode-badge")).toContainText("LIVE");
  await expect(page.getByLabel("Speed")).toHaveValue("1");
});

test("data too old to show the present is said so", async ({ page }) => {
  await page.goto("/?now=2026-11-20T00:00:00Z");
  await globeReady(page);
  await expect(page.locator(".mode-badge")).toHaveText("DATA TOO OLD");
  await expect(page.getByTestId("sim-time")).toHaveText("2026-11-07 14:49 UTC"); // parked at the window's end
  await expect(page.getByRole("button", { name: "Back to live" })).toHaveCount(0);
});

test("motion is smooth: every frame moves points, each update is cheap", async ({ page }) => {
  await page.goto(at("/"));
  await globeReady(page);
  await page.getByLabel("Speed").selectOption("600");
  await page.waitForFunction(() => (window as unknown as { __perigee?: { frames: number } }).__perigee!.frames > 90, null, { timeout: 30_000 });
  const perf = await page.evaluate(() => {
    const p = (window as unknown as { __perigee: { updateMs: number[]; samples: number } }).__perigee;
    const u = [...p.updateMs].sort((a, b) => a - b);
    return { median: u[Math.floor(u.length / 2)], samples: p.samples };
  });
  // Placing every point for one frame must leave most of a 16.7 ms frame for drawing.
  expect(perf.median).toBeLessThan(8);
  expect(perf.samples).toBeGreaterThan(3); // the worker keeps sampling ahead of the clock
});

test("quality setting applies and is remembered", async ({ page }) => {
  await page.goto(at("/"));
  await globeReady(page);
  await page.getByLabel("Quality").selectOption("battery");
  await page.reload();
  await globeReady(page);
  await expect(page.getByLabel("Quality")).toHaveValue("battery");
  // NASA imagery needs its credit on screen.
  await expect(page.locator(".footer")).toContainText("NASA Blue Marble");
  await expect(page.locator(".cesium-widget-credits")).toContainText("NASA");
});

test("filters and colour modes", async ({ page }) => {
  await page.goto(at("/"));
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
  await page.goto(at("/#/object"));
  await page.getByLabel("Find by name or NORAD id").fill("25544");
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("button", { name: /ISS/ }).first().click();
  await expect(page.locator(".detail h3")).toContainText("ISS");
  await expect(page.locator(".detail")).toContainText("Orbit data");
});

test("risk dashboard draws the index and its table", async ({ page }) => {
  await page.goto(at("/#/risk"));
  await expect(page.locator("svg.chart path.series")).toHaveCount(2);
  await expect(page.locator("svg.chart")).toContainText("not a probability");
  await page.getByRole("button", { name: "Show table" }).click();
  expect(await page.locator("table.compact tbody tr").count()).toBeGreaterThan(10);
  await expect(page.locator("ol li").first()).toContainText("km");
});

test("simulator runs and keeps its label on screen", async ({ page }) => {
  await page.goto(at("/#/simulator"));
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
  await page.goto(at("/"));
  await expect(page.locator(".banner.warn")).toContainText("The API is not answering", { timeout: 60_000 });
});

test("method page states the limits", async ({ page }) => {
  await page.goto(at("/#/method"));
  await expect(page.locator(".prose")).toContainText("not a collision probability");
  await expect(page.locator(".prose")).toContainText("provisional");
});
